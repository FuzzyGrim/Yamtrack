"""A parallel test runner that cannot be stranded by a lost worker result.

Django's ``ParallelTestSuite`` hands each subsuite to a
``multiprocessing.Pool`` and drains the results with ``imap_unordered``. If a
result is produced but never delivered, that iterator never raises
``StopIteration``, and the parent waits for it forever.

This is not hypothetical here. Instrumenting the runner locally
(``docs/architecture/test-suite-cost.md``) showed:

    PARENT_DISPATCH total_subsuites=659 processes=4
    WORKER_START:      659
    WORKER_DONE:       659      <- every subsuite finished
    PARENT_RECEIVED:   565      <- 94 results never arrived

and a watchdog dump from a real CI run caught the parent at
``pool.py:861 in next`` / ``django/test/runner.py:541 in run`` -- the result
loop -- with ``_handle_workers`` and ``_handle_tasks`` alive and no
``_handle_results`` thread at all. ``multiprocessing`` confirms it from the
other side: tearing such a pool down raises ``AssertionError: Cannot have cache
with result_handler not alive``, which is precisely "results outstanding, and
the thread that would deliver them is gone".

It survives ``spawn``, the results pickle fine (188 KB for a whole run), and it
is intermittent and load-dependent. Nobody has root-caused it. What makes it
expensive was that the CI job had no ``timeout-minutes``, so a hung run could
hold a runner for GitHub's six-hour default and report nothing. The current
application job also has a 120-minute timeout as an outer bound.

So this subclass does not try to fix the race. It makes the *collection* side
unable to hang on it:

1. **Completion** is detected by counting results, not by waiting for
   ``StopIteration``.
2. **Stalling** is bounded: if nothing arrives for ``stall_timeout`` seconds,
   stop waiting and re-dispatch whatever never came back to a *fresh pool*.
   A fresh pool is a faithful worker environment; the parent is not, so
   running the work here is kept as a last resort after several attempts.
3. **Teardown** avoids ``multiprocessing``'s own API entirely. Neither
   ``pool.join()`` nor ``pool.terminate()`` is safe on a pool in this state --
   both can block forever, and two earlier versions of this file relocated the
   hang into each of them in turn. Workers are given a deadline and then
   killed directly.

Nothing is skipped, disabled or quarantined: a recovered run executes every
test, it is just slower than it should have been. It says so loudly, because
needing the fallback means the underlying race is still there.
"""

from __future__ import annotations

import ctypes
import json
import logging
import multiprocessing
import multiprocessing.pool as mp_pool
import os
import time
import unittest
from collections import defaultdict
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.models.signals import pre_migrate
from django.test.runner import DiscoverRunner, ParallelTestSuite
from django.test.utils import iter_test_cases

from config import test_db_template, test_sharding

logger = logging.getLogger(__name__)

# How long to wait with no result at all before concluding that the rest are
# never coming. One subsuite is one TestCase class, so this is far longer than
# any healthy gap -- the slowest whole *module* in this suite is under a minute.
DEFAULT_STALL_TIMEOUT = 300.0

# How long to let workers wind themselves up after the pool is closed, before
# killing them. Healthy workers exit in milliseconds; the budget only matters
# when one is wedged, and letting them exit on their own keeps their coverage
# data intact.
POOL_TEARDOWN_TIMEOUT = 120.0

# How many times to re-dispatch undelivered work to a fresh pool. Only a pool
# is a faithful environment for these tests, so recovery means retrying pools
# rather than running the work in the parent -- that was tried, and the classes
# came back with tearDownClass errors caused by the harness, not the code. A
# retry that works is quick, because it only re-dispatches what is missing.
DEFAULT_MAX_ATTEMPTS = 5


def _max_attempts() -> int:
    """How many pools to try before giving up on the workers entirely."""
    raw = os.environ.get("FLOPPY_TEST_MAX_DISPATCH_ATTEMPTS")
    try:
        return max(1, int(raw)) if raw else DEFAULT_MAX_ATTEMPTS
    except ValueError:
        return DEFAULT_MAX_ATTEMPTS


def _configured_parallel(requested: int) -> int:
    """Honour FLOPPY_TEST_PARALLEL, else run serially. See the runner below."""
    raw = os.environ.get("FLOPPY_TEST_PARALLEL")
    if not raw:
        return 1
    if raw == "auto":
        return requested
    try:
        return max(1, int(raw))
    except ValueError:
        return 1


def _stall_timeout() -> float:
    """Seconds of no results before recovering. 0 disables recovery."""
    raw = os.environ.get("FLOPPY_TEST_STALL_TIMEOUT")
    if not raw:
        return DEFAULT_STALL_TIMEOUT
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_STALL_TIMEOUT
    # 0 restores Django's wait-forever behaviour, which is what someone
    # deliberately reproducing the race wants: the process left alive to
    # inspect rather than rescued.
    return max(0.0, value)


def _kill_pool(pool) -> None:
    """Stop a pool's workers without calling ``Pool.terminate()``.

    ``Pool.terminate()`` must not be used on a pool in this state, for two
    independently fatal reasons:

    * it asserts that a pool with outstanding results still has a live
      ``_result_handler`` thread, which is exactly what has gone missing;
    * worse, ``_terminate_pool`` calls ``_help_stuff_finish``, which takes the
      task queue's ``_rlock`` -- and the task handler thread is still holding
      it. That is a deadlock, and it was observed: a run stalled, detected the
      stall correctly, and then blocked *forever* inside ``terminate()``::

          _help_stuff_finish (multiprocessing/pool.py:675)
          _terminate_pool (multiprocessing/pool.py:695)
          terminate (multiprocessing/pool.py:657)

    So the pool's own teardown is left alone entirely. Killing the worker
    processes is enough: they are the only OS resources that matter, and the
    pool's three handler threads are daemons, so they cannot keep the
    interpreter alive. The handler states are set to TERMINATE first, or
    ``_handle_workers`` would helpfully respawn the workers being killed.
    """
    terminate_state = getattr(mp_pool, "TERMINATE", 2)
    for attr in ("_worker_handler", "_task_handler", "_result_handler"):
        handler = getattr(pool, attr, None)
        if handler is not None:
            try:
                handler._state = terminate_state
            except Exception:  # pragma: no cover - best-effort teardown
                logger.debug("could not stop pool handler %s", attr, exc_info=True)
    try:
        pool._state = terminate_state
    except Exception:  # pragma: no cover - best-effort teardown
        logger.debug("could not mark the pool terminated", exc_info=True)

    for worker in list(getattr(pool, "_pool", []) or []):
        try:
            if worker.is_alive():
                worker.terminate()
        except Exception:  # pragma: no cover - best-effort teardown
            logger.debug("could not terminate a pool worker", exc_info=True)

    # A Pool registers ``_terminate_pool`` as a ``util.Finalize`` callback, so
    # multiprocessing's atexit handler runs it on the way out -- straight back
    # into the same deadlock, after the suite has already printed its results.
    # That was observed: a recovered run reported "Ran 4302 tests" and then
    # never exited. Cancelling the finalizer is what lets the process die.
    finalizer = getattr(pool, "_terminate", None)
    if finalizer is not None:
        try:
            finalizer.cancel()
        except Exception:  # pragma: no cover - best-effort teardown
            logger.debug("could not cancel the pool finalizer", exc_info=True)


def _drain_pool(pool) -> None:
    """Close the pool and wait for its workers, with a deadline.

    Deliberately not ``pool.join()``: joining waits on the pool's handler
    threads, and when one of those has died that wait is the very hang this
    class exists to avoid.
    """
    try:
        pool.close()
    except Exception:  # pragma: no cover - best-effort teardown
        logger.debug("pool.close() failed", exc_info=True)

    deadline = time.monotonic() + POOL_TEARDOWN_TIMEOUT
    for worker in list(getattr(pool, "_pool", []) or []):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            worker.join(remaining)
        except Exception:  # pragma: no cover - best-effort teardown
            logger.debug("could not join a pool worker", exc_info=True)

    _kill_pool(pool)


class _UnrecoveredSubsuites:
    """Stands in for the tests that never reported, so the run fails loudly."""

    def __init__(self, indexes):
        self.indexes = list(indexes)

    def id(self):
        return "config.test_runner.unrecovered_subsuites"

    def __str__(self):
        return f"{len(self.indexes)} subsuites whose results were never delivered"


class ResilientParallelTestSuite(ParallelTestSuite):
    """``ParallelTestSuite`` that recovers from undelivered worker results."""

    def run(self, result):
        """Dispatch, and re-dispatch whatever the pool fails to deliver."""
        self.initialize_suite()

        pending = list(range(len(self.subsuites)))
        max_attempts = _max_attempts()
        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                self._announce_retry(pending, attempt, max_attempts)
            received = self._dispatch(pending, result)
            pending = [index for index in pending if index not in received]
            if not pending or result.shouldStop:
                break

        if pending and not result.shouldStop:
            # Every attempt lost the same work. Say so and fail: running it in
            # this process was tried and does not work -- the parent is not a
            # faithful worker environment, and the classes come back with
            # tearDownClass errors that are artefacts of the harness rather
            # than real defects. A loud failure that ends is recoverable by a
            # re-run; wrong results are not.
            message = (
                f"parallel_runner_unrecovered: {len(pending)} of "
                f"{len(self.subsuites)} subsuites never reported a result "
                f"after {max_attempts} dispatch attempts. Their tests did NOT "
                f"run. This is the lost-result race -- see "
                f"docs/architecture/test-suite-cost.md."
            )
            logger.error(message)
            print(f"\n{message}\n", flush=True)  # noqa: T201
            result.errors.append((_UnrecoveredSubsuites(pending), message))

        return result

    def _announce_retry(self, pending, attempt, max_attempts) -> None:
        message = (
            f"parallel_runner_stalled: {len(pending)} of {len(self.subsuites)} "
            f"subsuite results were never delivered by the worker pool "
            f"({_stall_timeout():.0f}s with no progress). Re-dispatching them "
            f"to a fresh pool (attempt {attempt} of {max_attempts}). This is "
            f"the known lost-result race, not a test failure -- see "
            f"docs/architecture/test-suite-cost.md."
        )
        logger.error(message)
        # The suite's own stream is what a developer actually reads.
        print(f"\n{message}\n", flush=True)  # noqa: T201

    def _dispatch(self, indexes, result) -> set[int]:
        """Run `indexes` on a fresh pool; return the ones that reported back."""
        counter = multiprocessing.Value(ctypes.c_int, 0)
        pool = multiprocessing.Pool(
            processes=min(self.processes, len(indexes)) or 1,
            initializer=self.init_worker.__func__,
            initargs=[
                counter,
                self.initial_settings,
                self.serialized_contents,
                self.process_setup.__func__,
                self.process_setup_args,
                self.debug_mode,
                self.used_aliases,
            ],
        )
        args = [
            (
                self.runner_class,
                index,
                self.subsuites[index],
                self.failfast,
                self.buffer,
            )
            for index in indexes
        ]
        test_results = pool.imap_unordered(self.run_subsuite.__func__, args)

        received: set[int] = set()
        stall_timeout = _stall_timeout()
        last_progress = time.monotonic()

        while True:
            if result.shouldStop:
                _kill_pool(pool)
                break

            try:
                subsuite_index, events = test_results.next(timeout=0.1)
            except multiprocessing.TimeoutError:
                if stall_timeout and time.monotonic() - last_progress > stall_timeout:
                    _kill_pool(pool)
                    break
                continue
            except StopIteration:
                _drain_pool(pool)
                break

            received.add(subsuite_index)
            last_progress = time.monotonic()

            tests = list(self.subsuites[subsuite_index])
            for event in events:
                self.handle_event(result, tests, event)

            if len(received) == len(indexes):
                # Everything is in; don't wait for StopIteration to say so.
                _drain_pool(pool)
                break

        return received


SELECTED_FILE = "selected.txt"
REPORTED_FILE = "reported.txt"
MODULE_SECONDS_FILE = "module_seconds.json"


# Read once by the runner that owns the process, then removed from the
# environment. A test that builds its own runner, or starts a subprocess, must
# not inherit them: one did, in this suite's own tests, and rewrote the shard's
# selected.txt with a list of its own (caught by the accounting check).
_OWNED_VARIABLES = (
    "FLOPPY_TEST_SHARD",
    "FLOPPY_TEST_ACCOUNTING_DIR",
    "FLOPPY_TEST_LIST_ONLY",
)


def _claim_environment() -> dict[str, str]:
    """Take this run's sharding and accounting settings out of ``os.environ``.

    ``FLOPPY_TEST_ACCOUNTING_DIR`` receives three files:

    * ``selected.txt``: the sorted IDs this process kept after tags and
      ``FLOPPY_TEST_SHARD`` were applied, written before any test runs;
    * ``reported.txt``: one ID per line for every outcome reported (pass,
      fail, error, skip, expected or unexpected failure), appended and flushed
      as it happens, so a process that dies mid-run leaves what it finished;
    * ``module_seconds.json``: wall seconds per test module, including class
      setup, for refreshing ``test_shard_weights.json``.

    ``scripts/check_test_accounting.py`` compares them across shards.
    ``FLOPPY_TEST_LIST_ONLY=1`` writes ``selected.txt`` and runs nothing.
    """
    return {
        name: os.environ.pop(name) for name in _OWNED_VARIABLES if name in os.environ
    }


class _AccountingSink:
    """Appends reported test IDs and totals wall time per module."""

    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.directory = directory
        self._reported = (directory / REPORTED_FILE).open("a", encoding="utf-8")
        self.module_seconds: dict[str, float] = defaultdict(float)

    def reported(self, test) -> None:
        self._reported.write(f"{test.id()}\n")
        self._reported.flush()

    def timed(self, test, seconds: float) -> None:
        self.module_seconds[test_sharding.module_of(test)] += seconds

    def close(self) -> None:
        self._reported.close()
        payload = {
            name: round(seconds, 3)
            for name, seconds in sorted(self.module_seconds.items())
        }
        (self.directory / MODULE_SECONDS_FILE).write_text(
            json.dumps(payload, indent=1, sort_keys=True) + "\n"
        )


def _recording_result_class(base, sink: _AccountingSink):
    """Subclass ``base`` so every outcome and every test's wall time is kept.

    Time is measured stop-to-stop, so the gap before a test (its class's
    ``setUpClass`` and the previous class's ``tearDownClass``) counts towards
    its module. That is what a shard actually pays for the module.
    """

    class RecordingTestResult(base):
        _last_stop: float | None = None

        def startTest(self, test):  # noqa: N802  # unittest's API
            if self._last_stop is None:
                self._last_stop = time.monotonic()
            super().startTest(test)

        def stopTest(self, test):  # noqa: N802
            super().stopTest(test)
            now = time.monotonic()
            sink.timed(test, now - (self._last_stop or now))
            self._last_stop = now

        def addSuccess(self, test):  # noqa: N802
            super().addSuccess(test)
            sink.reported(test)

        def addFailure(self, test, err):  # noqa: N802
            super().addFailure(test, err)
            sink.reported(test)

        def addError(self, test, err):  # noqa: N802
            super().addError(test, err)
            sink.reported(test)

        def addSkip(self, test, reason):  # noqa: N802
            super().addSkip(test, reason)
            sink.reported(test)

        def addExpectedFailure(self, test, err):  # noqa: N802
            super().addExpectedFailure(test, err)
            sink.reported(test)

        def addUnexpectedSuccess(self, test):  # noqa: N802
            super().addUnexpectedSuccess(test)
            sink.reported(test)

        def addSubTest(self, test, subtest, err):  # noqa: N802
            # A failing subtest replaces the test's own addSuccess.
            super().addSubTest(test, subtest, err)
            sink.reported(test)

    return RecordingTestResult


class ResilientDiscoverRunner(DiscoverRunner):
    """``DiscoverRunner`` that defaults to serial, and survives parallel.

    Two different failures hide behind "the suite hangs", and they need
    different answers:

    * On a *subset* of the suite, results go missing at random and a fresh pool
      delivers them: ``ResilientParallelTestSuite`` recovers that cleanly, five
      runs out of five.
    * On the *whole* suite, the same subsuites go missing every time --
      measured at 131 of 923, with three fresh pools recovering only 2 of them.
      Work that is lost deterministically is not a delivery race; the workers
      carrying it are dying, which also fits the SIGSEGV seen on CI. No amount
      of re-dispatching fixes that.

    So parallelism is off by default. With ``parallel=1`` Django builds a plain
    suite and never touches ``multiprocessing`` (see ``DiscoverRunner.build_suite``,
    which guards the whole parallel path on ``parallel > 1``), so there is no
    pool, no worker to lose, and nothing to hang on. The suite runs slower and
    finishes.

    ``FLOPPY_TEST_PARALLEL=<n>`` opts back in for anyone who wants the speed
    locally; the resilient suite above is what makes that survivable.
    """

    parallel_test_suite = ResilientParallelTestSuite

    def __init__(self, *args, **kwargs):
        """Override whatever ``--parallel`` asked for with the configured value."""
        super().__init__(*args, **kwargs)
        self.parallel = _configured_parallel(self.parallel)
        self._sink: _AccountingSink | None = None
        owned = _claim_environment()
        self._shard_spec = owned.get("FLOPPY_TEST_SHARD")
        raw_dir = owned.get("FLOPPY_TEST_ACCOUNTING_DIR")
        self._accounting_dir = Path(raw_dir) if raw_dir else None
        self._list_only = owned.get("FLOPPY_TEST_LIST_ONLY") == "1"

    def build_suite(self, test_labels=None, **kwargs):
        """Apply ``FLOPPY_TEST_SHARD`` after tags, then write ``selected.txt``."""
        shard = test_sharding.parse_shard(self._shard_spec)
        if shard is not None and self.parallel > 1:
            message = (
                "FLOPPY_TEST_SHARD splits the run into separate serial "
                "processes; it cannot be combined with FLOPPY_TEST_PARALLEL > 1."
            )
            raise ImproperlyConfigured(message)
        suite = super().build_suite(test_labels, **kwargs)
        if shard is not None:
            suite = self._keep_shard(suite, *shard)
        directory = self._accounting_dir
        if directory is not None:
            directory.mkdir(parents=True, exist_ok=True)
            ids = sorted(test.id() for test in iter_test_cases(suite))
            (directory / SELECTED_FILE).write_text(
                "".join(f"{test_id}\n" for test_id in ids)
            )
        return suite

    def _keep_shard(self, suite, index: int, count: int):
        tests = list(iter_test_cases(suite))
        modules = {test_sharding.module_of(test) for test in tests}
        assignment = test_sharding.assign(modules, test_sharding.load_weights(), count)
        kept = [
            test for test in tests if assignment[test_sharding.module_of(test)] == index
        ]
        kept_modules = {test_sharding.module_of(test) for test in kept}
        self.log(
            f"[test-shard] {index}/{count}: kept {len(kept)} of {len(tests)} "
            f"tests, {len(kept_modules)} of {len(modules)} modules."
        )
        return self.test_suite(kept)

    def run_tests(self, test_labels, **kwargs):
        """With ``FLOPPY_TEST_LIST_ONLY=1``, only discover: no database, no tests."""
        if not self._list_only:
            return super().run_tests(test_labels, **kwargs)
        if self._accounting_dir is None:
            message = "FLOPPY_TEST_LIST_ONLY=1 needs FLOPPY_TEST_ACCOUNTING_DIR."
            raise ImproperlyConfigured(message)
        self.setup_test_environment()
        try:
            suite = self.build_suite(test_labels)
            # A module that fails to import is a _FailedTest here. Listing it
            # would hide the problem behind a passing discover job.
            broken = [
                test.id()
                for test in iter_test_cases(suite)
                if isinstance(test, unittest.loader._FailedTest)
            ]
        finally:
            self.teardown_test_environment()
        if broken:
            self.log("[test-list] these test modules could not be loaded:")
            for test_id in broken:
                self.log(f"  {test_id}")
            return len(broken)
        self.log(f"[test-list] wrote {SELECTED_FILE} to {self._accounting_dir}.")
        return 0

    def get_resultclass(self):
        """Record every outcome when ``FLOPPY_TEST_ACCOUNTING_DIR`` is set."""
        base = super().get_resultclass()
        directory = self._accounting_dir
        if directory is None:
            return base
        if self._sink is None:
            self._sink = _AccountingSink(directory)
        return _recording_result_class(base or unittest.TextTestResult, self._sink)

    def _template_applies(self, aliases) -> bool:
        """Whether this run builds the one in-memory SQLite test database."""
        if not test_db_template.requested() or self.keepdb:
            return False
        if aliases is not None and DEFAULT_DB_ALIAS not in aliases:
            return False
        if list(connections) != [DEFAULT_DB_ALIAS]:
            return False
        connection = connections[DEFAULT_DB_ALIAS]
        return connection.vendor == "sqlite" and connection.creation.is_in_memory_db(
            connection.creation._get_test_db_name()
        )

    def setup_databases(self, **kwargs):
        """Restore a saved migrated database when its inputs are unchanged.

        See ``config.test_db_template``. Django's own setup runs either way, so
        ``migrate`` still executes against a restored database; there it must
        find nothing to apply.
        """
        if not self._template_applies(kwargs.get("aliases")):
            return super().setup_databases(**kwargs)

        connection = connections[DEFAULT_DB_ALIAS]
        key = test_db_template.current_key()
        template = test_db_template.find(key)
        keeper = None
        if template is not None:
            keeper = test_db_template.restore(
                template, connection.creation._get_test_db_name()
            )
            if keeper is None:
                self.log(
                    f"[test-db] saved database {template.name} was damaged and "
                    "has been deleted; replaying migrations."
                )

        planned = []

        def record_plan(sender, plan=None, **_kwargs):
            planned.extend(str(migration) for migration, _backwards in plan or [])

        pre_migrate.connect(record_plan, weak=False)
        try:
            old_config = super().setup_databases(**kwargs)
        finally:
            pre_migrate.disconnect(record_plan)

        connection.ensure_connection()
        raw = connection.connection
        if keeper is None:
            saved = test_db_template.save(raw, key)
            self.log(f"[test-db] replayed migrations; saved {saved.name} for reuse.")
            return old_config

        keeper.close()
        problem = None
        if planned:
            problem = (
                f"migrate applied {len(set(planned))} migration(s) to it, "
                f"starting with {planned[0]}"
            )
        elif test_db_template.fingerprint(raw) != test_db_template.expected_fingerprint(
            template
        ):
            problem = "its contents changed when migrate ran"
        if problem:
            template.unlink(missing_ok=True)
            message = (
                f"[test-db] the saved database {template.name} does not match "
                f"this checkout: {problem}. It has been deleted and no test "
                "ran. Run again to rebuild it from real migrations, or set "
                "FLOPPY_TEST_DB_TEMPLATE=0."
            )
            raise test_db_template.TemplateMismatchError(message)
        self.log(f"[test-db] restored {template.name}; migrate found nothing to apply.")
        return old_config

    def run_suite(self, suite, **kwargs):
        """Keep Django's individual durations and also summarize module costs."""
        test_modules = (
            {str(test): test.__class__.__module__ for test in iter_test_cases(suite)}
            if self.durations is not None
            else {}
        )
        try:
            result = super().run_suite(suite, **kwargs)
        finally:
            if self._sink is not None:
                self._sink.close()
                self._sink = None
        if self.durations is not None:
            modules = defaultdict(float)
            for test, elapsed in result.collectedDurations:
                modules[test_modules.get(test, "<unattributed>")] += elapsed
            self.log(
                "Slowest test modules (test method time, including setUp/tearDown):"
            )
            for module, elapsed in sorted(
                modules.items(), key=lambda item: item[1], reverse=True
            )[:20]:
                self.log(f"{elapsed:9.3f}s {module}")
        return result
