"""A web worker must be bounded by what it holds, not only by what it served.

``max_requests`` counts requests. Floppy's expensive pages cost hundreds of
times an ordinary one, so a worker can grow for hours without reaching the
count: production showed a worker at 567 MiB of private memory after three
hours, never recycled, because real traffic had not served 500 requests yet.
"""

import importlib
import os
import sys
import time
from pathlib import Path
from unittest import TestResult, skipUnless
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase

import config

sys.path.insert(0, str(Path(settings.BASE_DIR)))


# config.gunicorn reads its tier at import, so each case has to import it
# again under a different environment. Both modules are put back afterwards:
# config.runtime_profile computes a module-level PROFILE from the environment
# at import, and leaving a tier-patched copy in sys.modules makes whichever
# test runs next read this file's environment instead of its own.
RELOADED_MODULES = ("config.runtime_profile", "config.gunicorn")


class ModuleIsolationTests(SimpleTestCase):
    def test_tier_tests_restore_package_attributes_too(self):
        before = dict(vars(config))
        result = TestResult()
        WorkerMemoryCeilingTests("test_every_tier_bounds_worker_memory").run(result)
        self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
        for name in ("runtime_profile", "gunicorn"):
            if name in before:
                self.assertIs(getattr(config, name), before[name])
            else:
                self.assertFalse(hasattr(config, name))


def load_config(tier="standard", **environment):
    """Import config.gunicorn fresh under a given tier and environment."""
    values = {"FLOPPY_RESOURCE_TIER": tier, **environment}
    with patch.dict(os.environ, values, clear=False):
        for module in RELOADED_MODULES:
            sys.modules.pop(module, None)
        return importlib.import_module("config.gunicorn")


class Worker:
    """What the hook reads: the retire flag, request count and start time."""

    def __init__(self, nr=1000, age_seconds=3600.0):
        """Start alive and, by default, well past both retirement floors."""
        self.alive = True
        self.nr = nr
        self.floppy_started_at = time.monotonic() - age_seconds


class WorkerMemoryCeilingTests(SimpleTestCase):
    """The ceiling exists, scales with the host, and only fires when crossed."""

    def setUp(self):
        """Remember the real modules so they can be put back exactly."""
        self.saved_modules = {name: sys.modules.get(name) for name in RELOADED_MODULES}
        self.saved_package_attributes = {
            name: vars(config)[name]
            for name in ("runtime_profile", "gunicorn")
            if name in vars(config)
        }

    def tearDown(self):
        """Restore the real modules, not merely drop the patched ones.

        Dropping alone is not enough: the next importer would rebuild the
        module under whatever environment happens to be current, which is not
        necessarily the one the process started with.
        """
        for name, module in self.saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        # Parent-package imports and direct module imports must resolve to
        # the same objects after these tests finish.
        for name in ("runtime_profile", "gunicorn"):
            if name in self.saved_package_attributes:
                setattr(config, name, self.saved_package_attributes[name])
            else:
                vars(config).pop(name, None)

    def test_every_tier_bounds_worker_memory(self):
        """No tier may leave a web worker free to grow without limit."""
        for tier in ("minimal", "constrained", "standard"):
            with self.subTest(tier=tier):
                self.assertGreater(load_config(tier).max_worker_memory_bytes, 0)

    def test_smaller_hosts_recycle_sooner(self):
        """The ceiling must rise with the tier, never fall."""
        minimal = load_config("minimal").max_worker_memory_bytes
        constrained = load_config("constrained").max_worker_memory_bytes
        standard = load_config("standard").max_worker_memory_bytes

        self.assertLess(minimal, constrained)
        self.assertLess(constrained, standard)

    def test_the_ceiling_clears_a_preloaded_worker(self):
        """A ceiling near a fresh worker's size retires it as fast as it starts.

        Measured, not assumed: with preload_app a fresh worker starts at
        109-136 MiB RSS, because RSS counts the shared application image it
        was forked from. A 120 MiB ceiling produced 30 workers in six minutes,
        none older than 16 seconds, so every tier must clear the top of that
        range with room for a request to do real work.
        """
        largest_observed_fresh_worker_bytes = 136 * 1024 * 1024
        for tier in ("minimal", "constrained", "standard"):
            with self.subTest(tier=tier):
                self.assertGreater(
                    load_config(tier).max_worker_memory_bytes,
                    largest_observed_fresh_worker_bytes * 1.5,
                )

    def test_a_young_worker_is_never_retired_for_size(self):
        """A ceiling below the starting size must not become a restart loop.

        This is the guard that keeps a misconfiguration survivable: without
        it every worker crosses the ceiling on its first request and is
        replaced by one that does the same.
        """
        module = load_config("standard")
        worker = Worker(nr=module.MINIMUM_REQUESTS_BEFORE_RETIREMENT - 1)
        with patch.object(module, "_worker_rss_bytes", return_value=1 << 40):
            module.post_request(worker, None, None, None)

        self.assertTrue(worker.alive)

    def test_a_worker_past_the_minimum_is_retired_for_size(self):
        """Once it has served enough and lived long enough, the ceiling applies."""
        module = load_config("standard")
        worker = Worker(nr=module.MINIMUM_REQUESTS_BEFORE_RETIREMENT)
        with patch.object(module, "_worker_rss_bytes", return_value=1 << 40):
            module.post_request(worker, None, None, None)

        self.assertFalse(worker.alive)

    def test_a_request_count_alone_does_not_bound_the_respawn_rate(self):
        """Under load a worker reaches the request floor in seconds.

        Measured: at four concurrent clients a 120 MiB ceiling produced 42
        workers in thirteen minutes even with the count floor in place, so a
        lifetime floor is what actually bounds how often a misconfigured
        ceiling can respawn.
        """
        module = load_config("standard")
        worker = Worker(nr=10_000, age_seconds=1.0)
        with patch.object(module, "_worker_rss_bytes", return_value=1 << 40):
            module.post_request(worker, None, None, None)

        self.assertTrue(worker.alive)

    def test_post_worker_init_stamps_the_start_time(self):
        """The lifetime floor is inert unless the worker is stamped."""
        module = load_config("standard")
        worker = Worker()
        del worker.floppy_started_at
        module.post_worker_init(worker)

        self.assertAlmostEqual(
            worker.floppy_started_at,
            time.monotonic(),
            delta=1.0,
        )

    def test_a_worker_under_the_ceiling_keeps_serving(self):
        """The common case must not touch the worker."""
        module = load_config("standard")
        worker = Worker()
        with patch.object(module, "_worker_rss_bytes", return_value=1024):
            module.post_request(worker, None, None, None)

        self.assertTrue(worker.alive)

    def test_a_worker_over_the_ceiling_is_retired(self):
        """Crossing the ceiling marks the worker for a graceful exit."""
        module = load_config("standard")
        worker = Worker()
        over = module.max_worker_memory_bytes + 1
        with patch.object(module, "_worker_rss_bytes", return_value=over):
            module.post_request(worker, None, None, None)

        self.assertFalse(worker.alive)

    def test_an_unreadable_rss_never_retires_a_worker(self):
        """Where /proc is absent the ceiling must not fire on a guess."""
        module = load_config("standard")
        worker = Worker()
        with patch.object(module, "_worker_rss_bytes", return_value=None):
            module.post_request(worker, None, None, None)

        self.assertTrue(worker.alive)

    def test_the_ceiling_can_be_turned_off(self):
        """An operator must be able to opt out without editing the image."""
        module = load_config(
            "standard",
            FLOPPY_GUNICORN_MAX_WORKER_MEMORY_BYTES="0",
        )
        worker = Worker()
        with patch.object(module, "_worker_rss_bytes", return_value=1 << 40):
            module.post_request(worker, None, None, None)

        self.assertEqual(module.max_worker_memory_bytes, 0)
        self.assertTrue(worker.alive)

    @skipUnless(sys.platform == "linux", "reads /proc/self/statm")
    def test_the_reported_rss_is_this_process(self):
        """The hook must read a real resident size, not a constant."""
        module = load_config("standard")
        resident = module._worker_rss_bytes()

        self.assertIsNotNone(resident)
        self.assertGreater(resident, 1024 * 1024)


class PageCacheIsNotWorkerMemoryTests(SimpleTestCase):
    """The ceiling measures what a recycle returns, not the mapped database.

    SQLite memory-maps the database file (SQLITE_MMAP_SIZE_MIB), and mapped
    pages count as resident. One cold Home row put ~120 MiB of the file into a
    worker, where it stays; a ceiling on plain RSS spent itself on page cache
    that the replacement worker maps straight back in.
    """

    def test_shared_pages_are_subtracted_from_resident(self):
        from config import runtime_profile

        page = runtime_profile._page_size()
        statm = "90000 60000 45000 700 0 15000 0\n"

        self.assertEqual(
            runtime_profile.anon_resident_bytes_from_statm(statm),
            15000 * page,
        )

    def test_an_unreadable_statm_is_unknown(self):
        from config import runtime_profile

        for contents in (None, "", "12 34\n", "a b c\n"):
            with self.subTest(contents=contents):
                self.assertIsNone(
                    runtime_profile.anon_resident_bytes_from_statm(contents),
                )

    def test_the_hook_reads_the_anonymous_size(self):
        module = load_config("standard")
        with patch.object(module, "current_anon_rss_bytes", return_value=1234):
            self.assertEqual(module._worker_rss_bytes(), 1234)

    @skipUnless(sys.platform == "linux", "reads /proc/self/statm")
    def test_a_mapped_file_does_not_grow_the_measured_size(self):
        """Reading a memory-mapped file the way SQLite does stays out of it."""
        import mmap
        import tempfile

        from config import runtime_profile

        size = 64 * 1024 * 1024
        with tempfile.TemporaryFile() as handle:
            handle.truncate(size)
            mapped = mmap.mmap(handle.fileno(), size, access=mmap.ACCESS_READ)
            try:
                anon_before = runtime_profile.current_anon_rss_bytes()
                rss_before = runtime_profile.current_rss_bytes()
                step = mmap.PAGESIZE
                touched = sum(mapped[offset] for offset in range(0, size, step))
                anon_after = runtime_profile.current_anon_rss_bytes()
                rss_after = runtime_profile.current_rss_bytes()
            finally:
                mapped.close()

        self.assertEqual(touched, 0)
        self.assertGreater(rss_after - rss_before, size // 2)
        self.assertLess(anon_after - anon_before, 8 * 1024 * 1024)
