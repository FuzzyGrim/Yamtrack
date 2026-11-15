# What the test suite costs


## November 12, 2026: publish-gate evidence

[PR #1917](https://github.com/dannyvfilms/Floppy/pull/1917) moved
traced coverage to a separate `Coverage` workflow and added an **opt-in**
path for reusing a green pull request's tests when the tree tested on
`refs/pull/N/merge` is identical to the tree published on `latest`.

**Observed after merge, with reuse OFF:** The `Reuse PR test result?`
job in 14 `Docker Image` push runs reported `Decision: run (shadow
mode). Every condition for reuse held`, and recorded matching
`pushed tree` and `tested tree` values. Examples:
[#1917's merge](https://github.com/dannyvfilms/Floppy/actions/runs/43112262627),
[#1933's merge](https://github.com/dannyvfilms/Floppy/actions/runs/43222624986),
and [#1938's merge](https://github.com/dannyvfilms/Floppy/actions/runs/43325180912).
All three test shards still executed and passed. The first four
post-merge Docker Image workflows failed at the *distributed image
verification* step despite passing the quality gate and build; later
[Docker Image runs](https://github.com/dannyvfilms/Floppy/actions/runs/43325180912)
passed end to end. The independent
[Coverage workflow](https://github.com/dannyvfilms/Floppy/actions/runs/43325180826)
also completed successfully.

**Live direct-push negative case:** A documentation-only direct push
([Docker Image run 43352328375](https://github.com/dannyvfilms/Floppy/actions/runs/43352328375))
correctly reported `Decision: run` with the reason
`no pull request is associated with this commit`, so the full gate
was selected. The run's eventual completion is tracked separately.

**Switched on 2026-11-13.** `REUSE_PR_TEST_RESULTS` has been `true`
since then. The cases that were "not proven" above have all happened on
`latest` itself, in the 32 `Docker Image` push runs from then to
2026-11-15:

| Case | Example | Decision |
| --- | --- | --- |
| Up-to-date merge commit | [#1996](https://github.com/dannyvfilms/Floppy/actions/runs/43733184735) and 18 more | Reused, trees equal |
| Squash merge | [#1974](https://github.com/dannyvfilms/Floppy/actions/runs/43680572742), [#1562](https://github.com/dannyvfilms/Floppy/actions/runs/43505694059) | Reused, trees equal |
| Squash merged while behind | [#1998](https://github.com/dannyvfilms/Floppy/actions/runs/43670473917) | Full gate: tested tree differs |
| Merged while the PR run was still going | [#1987](https://github.com/dannyvfilms/Floppy/actions/runs/43652629220), [#1988](https://github.com/dannyvfilms/Floppy/actions/runs/43655717286) | Full gate: run in progress or queued |
| Direct push, including merge commits pushed by hand | [eed87a907](https://github.com/dannyvfilms/Floppy/actions/runs/43671959036) and 7 more | Full gate: no pull request |
| Reused, then an always-run job failed | [#1989](https://github.com/dannyvfilms/Floppy/actions/runs/43664812425) | Upgrade (PostgreSQL) failed, no image published |

On every reused run the shards show as skipped, `test (3.12)` passed,
and migration checks, OAuth, both upgrade replays, release lint and the
image smoke still ran.

Push to built image, from the same runs:

| Gate | Runs | Median | Range |
| --- | --- | --- | --- |
| Reused | 20 | 15.1 min | 10.7 to 16.6 min |
| Full | 6 | 25.0 min | 23.3 to 27.8 min |

The full-gate row includes three shadow-mode runs from 2026-11-12; only
six full-gate runs published an image, because most direct pushes in
this window failed a shard.

These are different commits on shared runners, not a controlled A/B, but
the gap is the shard time and is consistent across every run.

The one thing tree equality cannot prove is that the publish gate runs
no test the pull request skipped, because the same workflow file picks
tests by event. `scripts/tests/test_reuse_pr_workflow.py` holds that:
it runs the shard's test step for both events and fails if the gate
would select a label, tag or test the pull request did not. It also
checks that only the decision can skip the shards and that the
roll-up and the image build still need every always-run job. If that
test breaks because test selection was rewritten, prove the property
again for the new wiring; do not delete it.

If GitHub stops reporting the tested commit for runs, reuse stops on
every merge (safe) and the decision job raises a warning on the run.

Remaining follow-ups are in
[#1918](https://github.com/dannyvfilms/Floppy/issues/1918).


The suite got slow enough that waiting on it became the bottleneck for both
people and agents. This records what was measured, what the causes actually
are, and what was changed.

All numbers below are from one cloud container: 4 CPUs, 16 GiB RAM, SQLite,
`--parallel` (so 4 workers), `--exclude-tag slow --exclude-tag network`.
6,115 tests across 420 test files.

## October 21 CI investigation

These newer hosted measurements are separate from the older local figures
below. The referenced run discovered 9,480 offline tests (9,478 executed).

| Successful run | Test step | Test execution | Notes |
| --- | --- | --- | --- |
| [39542220106](https://github.com/dannyvfilms/Floppy/actions/runs/39542220106) | 75m40s | 4,144.417s | Release, ctrace coverage with per-test contexts |
| [39538589973](https://github.com/dannyvfilms/Floppy/actions/runs/39538589973) | about 38m | 2,081.421s | Nearby PR, no coverage |
| [39692521277](https://github.com/dannyvfilms/Floppy/actions/runs/39692521277) | 47m43s | 2,588.239s | Referenced release; tests ultimately passed |

These runs use different commits, so this is evidence of the cost difference,
not a controlled coverage benchmark. In the successful release, Docker smoke
took 5m53s and publication itself took 2m56s. In
[39692521277](https://github.com/dannyvfilms/Floppy/actions/runs/39692521277),
tests, smoke and both upgrade replays passed; publication was blocked by
five virtualenv advisories in lint. Updating only the locked package from 21.7.13 to
21.14.12 passes the complete Python audit with the existing OAuth exception.

CI now runs two isolated jobs: `app`, and
`users integrations lists events api config`. Each remains serial with real
migrations; this does not re-enable the multiprocessing path described below.
The existing `test (3.12)` check requires both jobs to succeed. MCP/script
tests and migration graph checks run once. Both shards retain slow/browser
regressions and exclude network-tagged tests. Coverage files are merged before
generating Codecov output and the affected-test map, retaining all test contexts.

The expected wall-time cost is the slower shard plus coverage merging, rather
than the sum of both groups. The tradeoff is one additional migration replay
and dependency/browser installation, plus another hosted runner. Hosted
improved timings still need measurement after this workflow runs; the old
20-minute local figure is not a valid current hosted baseline. `--timing` now
reports setup, teardown and total time, and `--durations 30` reports individual
tests plus the twenty most expensive modules. Module totals include method
setup/teardown but exclude class fixtures and database creation.

Reproduce the two CI shards in separate terminals (Chromium must be installed):

```bash
SECRET=test-only FLOPPY_TEST_FAST_DB=0 FLOPPY_TEST_PARALLEL=1 scripts/test.sh app --timing --durations 30
SECRET=test-only FLOPPY_TEST_FAST_DB=0 FLOPPY_TEST_PARALLEL=1 scripts/test.sh users integrations lists events api config --timing --durations 30
```

Local full offline validation on the Mac, with real migrations and no coverage:

| Shard | Reported tests | Skipped | Database setup | Test execution | Total |
| --- | --- | --- | --- | --- | --- |
| app | 4,191 | 5 | 197.515s | 1,331.019s | 1,530.410s |
| other | 5,295 | 19 | 167.418s | 1,247.553s | 1,416.793s |

Both passed, including benchmarks and browser regressions. The jobs overlapped
on a machine with other work running; their totals are not a controlled
before/after comparison with the hosted coverage runs. Concurrent UI work
added seven browser tests after these runs; fresh discovery verified that the
two app-label sets still preserve the full multiset without overlap
(4,200 + 5,295 = 9,495 discovered tests). Those later UI tests are outside this
validation run. The final guard's socket/SSRF checks passed separately (68
tests), and two covered regression processes merged all 21 recorded contexts.

The module summaries identified these expensive groups in the full runs:
preflight (165.450s), statistics views (129.555s), Trakt allocation scaling
(100.384s), Home card projection (72.657s), API tracking (71.002s), and core
media API tests (68.373s). First-start preflight includes another real database
creation; the profiling and allocation regressions remain enabled.

### API fixture side effects

`FloppyApiTestCase` seeds a library in every test. Each seeded media row ran
the tracked-media signal chain against caches that were still empty: under
`cProfile`, `api.tests.test_media_core` spent 161s of its 209s inside
`_handle_media_cache_change`, and `_invalidate_history_for_media_change` alone
was 120s. Fixture creation now runs inside
`suppress_media_change_side_effects()`; a test that exercises invalidation
still writes through the API, where the signals run. The same module fell from
86s to 18.7s on one machine (106 tests).

Ordinary test settings now guard CPython socket audit events as well as
requests. External DNS, TCP and UDP fail before I/O, including aiohttp and
standard-library HTTP clients. Loopback and Unix sockets remain available.
Blocked DNS raises `socket.gaierror`, preserving the resolver contract used
by URL validation before it reaches a mocked HTTP transport.
IP-literal parsing remains available locally, so metadata-address checks
still validate the actual address before a mocked transport is reached.
This matters even when HTTP is mocked: `validate_self_hosted_url()` resolves
the hostname first. A controlled local Mac check of that function measured:

| Existing fixture URL | System DNS, HTTP-only guard baseline | Socket/DNS guard |
| --- | --- | --- |
| `https://abs.example.com` | 0.022505s | 0.000841s |
| `https://kavita.local:5000` | 5.008777s | 0.000027s |

Both paths report an unresolvable fixture host and perform no HTTP request.
The baseline deliberately used the real resolver; the guarded path performed
no DNS I/O. These are single-call local measurements, not projected hosted
savings: resolver behavior and negative caching vary by environment.

`scripts/test.sh --network` still opts in before settings load. This is a
Python-process boundary: child executables such as Chromium do not inherit
Python audit hooks, and intentionally mocked socket functions perform no I/O.
Keep browser fixtures local and provider calls mocked; the guard does not
replace those contracts.

## Headline

| | before | after |
| --- | --- | --- |
| test-database setup, every run | 141 s | 141 s (2.8 s with `FLOPPY_TEST_FAST_DB=1`) |
| `app.tests.views.test_history` (35 tests), execution only | 53.6 s | 25.6 s |
| whole suite, one app at a time | — | 398 s with `FLOPPY_TEST_FAST_DB=1` |
| whole suite, single invocation | 21 min+, sometimes never finishes | **~20 min, deterministic** (runs serially) |

## Cause 1: 141 seconds of migrations before any test runs

`setup_databases()` takes 141 s, and 140.1 s of that is applying migrations —
422 of them. It is the same 141 s whether the run is `--parallel 1` or
`--parallel 4` (the migrate is serial; the workers then clone the result), and
the same 141 s for one targeted test as for the whole suite. That is the floor
under every single test command anyone runs.

Where it goes:

| app | migrations | time |
| --- | --- | --- |
| users | 121 | 88.8 s |
| app | 153 | 33.2 s |
| integrations | 50 | 10.7 s |
| everything else | 98 | 7.4 s |

`users` is two thirds of it for a third of the migrations. The reason is a
repeating pattern — seventeen migrations named
`users.NNNN_remove_user_tv_sort_valid_and_more`, each ~3.5 s:

```
RemoveConstraint x10   (tv_sort_valid, season_sort_valid, movie_sort_valid, ...)
AlterField       x10   (the matching *_sort CharField choices)
AddConstraint    x10
```

Every time a sort option was added to a choices list, `makemigrations`
regenerated the whole check-constraint set. On SQLite each of those ~30
operations rebuilds the entire `users_user` table, and `users_user` is wide.
The tables are empty — this is pure DDL overhead, thirty table rebuilds per
migration.

### What was done

`FLOPPY_TEST_FAST_DB=1` sets `MIGRATION_MODULES` to `None` for the first-party
apps, so the schema is created straight from the models: **141 s → 2.8 s**.

It is **opt-in, not the default**, for two honest reasons:

* ordinary database setup no longer exercises the migration graph. The hygiene
  tests explicitly restore migration modules to inspect the real graph, but
  passing them does not replay the migrations;
* it skips `RunPython` data migrations, so anything depending on rows a
  migration creates would silently differ.

Use it while iterating. Do not use it as the final gate on a migration change,
and CI should not use it.

### The squash: attempted, measured, and deliberately not shipped

Squashing `users` is the obvious real fix, so it was tried rather than assumed.
What came back changes the recommendation.

`squashmigrations users 0044_merge_20251115_1520 0132_user_appearance` succeeds,
and the operation count is a genuine win:

| | operations |
| --- | --- |
| originals, 0044-0132 | 1,097 |
| squashed | 410 |

63% fewer operations means roughly 63% fewer `users_user` table rebuilds, so
most of the 88.8 s is recoverable. Two things stop it being a drive-by:

1. **Django's optimizer is defeated by the fork's own migration operations.**
   `AddFieldIfNotExists`, `AddConstraintIfNotExists` and
   `RemoveConstraintIfExists` are redefined *inline inside 21 separate
   migration files* rather than imported from one module. The optimizer sees
   21 unrelated classes, none of which implement `reduce()`, so it cannot
   collapse a `RemoveConstraintIfExists` against the matching
   `AddConstraintIfNotExists`. That is why 410 operations survive instead of
   something closer to the ~100 the final model state actually needs. Eleven
   `RunPython` operations act as further optimization barriers.

2. **31 RunPython functions need hand-porting** into the squashed file, which
   Django cannot do automatically. On a fresh install those data fixes run
   against empty tables and are no-ops, so most are probably `elidable`, but
   "probably" is not good enough for code that runs against real upgrade
   paths. Squashing across the 0038-0043 merge points is a further wrinkle:
   those numbers are duplicated by merge migrations, and `0038` contains a
   `lambda` that blocks serialization outright
   (`ValueError: Cannot serialize function: lambda`).

So: a squash is worth doing, it is worth roughly 50-55 s of every test run, and
it needs its own change with the maintainer reviewing the ported functions and
the upgrade matrix (`scripts/replay_upgrade_matrix.sh`) replayed. It is not
something to bolt onto an unrelated PR.

**The prerequisite, now done (November 2026).** The idempotent operations
live in one shared module, `src/app/migration_operations.py`, and
`check_migration_hygiene --strict` rejects any new migration that defines one
inline. By then the inline copies had grown from 21 files to 102 (75
`AddFieldIfNotExists`, 39 `AddConstraintIfNotExists`, 27
`RemoveConstraintIfExists`, 13 `AddIndexIfNotExists`, 5
`CreateModelIfNotExists`, 1 `RemoveFieldIfExists`). Those 102 files are
frozen on an allowlist and were not touched.

Measuring it corrected point 1 above. The optimizer is *not* blind to the
inline copies: Django's `reduce()` matches by `isinstance`, so an inline
`AddConstraintIfNotExists` followed by an inline `RemoveConstraintIfExists`
of the same name already cancels (2 operations to 0 in a scratch squash).
What actually blocks a squash is the operations that survive: the writer
serializes an inline class as `users.migrations.0038_x.AddFieldIfNotExists(...)`,
which is a `SyntaxError` because the module name starts with a digit, and it
flags every such migration as needing hand-copying. The shared classes
serialize as `app.migration_operations.AddFieldIfNotExists(...)` and import
cleanly. They also keep their class when the optimizer merges (Django's own
`AddField` + `AlterField` merge returns a plain `AddField`, silently dropping
idempotence), and `CreateModelIfNotExists` refuses to absorb later field
changes, which would be lost when the table already exists.

The 410 surviving operations are mostly ordering, not class identity: Django
will not move a constraint operation across another operation on the same
model, so add, alter-field, remove sequences stay as they are. That is the
same for Django's own classes.

**What a `users` squash still needs:** hand-porting and maintainer review of
the 31 `RunPython` functions; replacing the `lambda` in `0038` that blocks
serialization; rewriting the surviving inline operations in the squashed file
to the shared imports; and a full `scripts/replay_upgrade_matrix.sh` replay on
SQLite and PostgreSQL with drift scenarios.

## Cause 2: password hashing, 42% of an auth-heavy module

Django's default `PBKDF2PasswordHasher` costs **296 ms per hash** on this
hardware. The suite has 631 `create_user`/`create_superuser` call sites, 210
`client.login(...)` calls (each of which verifies, another 296 ms), and 586
`setUp` methods against only 17 `setUpTestData` — so most of that cost is paid
per test method, not once per class.

Measured on `app.tests.views.test_history`: 72 encodes (21.8 s) + 35 verifies
(10.6 s) = **32.4 s of the 53.6 s** the module spent executing tests.

### What was done

`config/test_settings.py` now sets:

```python
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
```

`app.tests.views.test_history` went from 53.6 s to 31.0 s of execution, a 42%
cut, with no test changes. Tests assert on authorization, never on hash
strength, and this is test settings only — production is untouched.

## Cause 2b: fixtures rebuilt for every test method

With hashing fixed, `setUp` was still **37% of what `app.tests.views.test_history`
spent executing tests** — 11.7 s of 31.7 s, rebuilding the same rows for every
test method. The suite has 586 `setUp` methods against 17 `setUpTestData`.

`setUpTestData` builds the fixture once per class inside a class-level atomic
block, and Django rolls each test method back to that state, so for a fixture
that is only *read* the two are equivalent. Django also wraps the attributes so
a test that mutates them does not leak into the next one.

Three classes in that module were converted:

| | setUp | module setUp share | module tests |
| --- | --- | --- | --- |
| before | 11.7 s | 37% | 53.6 s (31.0 s after MD5) |
| after | 2.7 s | 11% | **25.6 s** |

`HistoryMonthViewTests` alone went from 22.0 s to 15.0 s.

### The recipe

Not every `setUp` can move, and the split matters:

* **Moves to `setUpTestData`:** creating users, items and tracked media — rows
  the tests only read.
* **Stays in `setUp`:** `self.client.login(...)` (the test client is per-test),
  `cache.clear()`, and anything with a side effect outside the database, such as
  `history_cache.invalidate_history_cache(...)`.
* **`mock.patch` needs care.** `setUpTestData` is a classmethod with no
  `addCleanup`, so a fixture that must not hit providers starts the patches and
  stops them itself. `_begin_model_metadata_patches()` in
  `app/tests/views/test_history.py` is the shape to copy;
  `_start_model_metadata_patches(self)` stays in `setUp` for the test bodies.
* **`captureOnCommitCallbacks` is a classmethod**, so `cls.captureOnCommitCallbacks(...)`
  works in a class fixture — but on-commit callbacks behave differently inside
  the class-level atomic block, so check any fixture that depends on them.

Classes whose `setUp` mutates fixture objects or touches the cache were
deliberately left alone here. Audit before converting: the cheap filter is
whether the body does anything other than create rows.

The remaining 583 `setUp` methods are the same opportunity, module by module.
Convert the slowest first (`--durations` names them) and verify each module
against real migrations, not `FLOPPY_TEST_FAST_DB`.

## Cause 3: the parallel runner can lose results and hang forever

This is the one that actually strands a session, and it is not slowness — it
is a hang.

Reproduced with `app` and `integrations` in a single `--parallel` invocation
(each passes on its own: 159 s and 38 s). Instrumenting
`ParallelTestSuite.run_subsuite` and the parent's result loop:

```
PARENT_DISPATCH total_subsuites=659 processes=4
WORKER_START:      659
WORKER_DONE:       659        <- every subsuite finished
PARENT_RECEIVED:   565        <- 94 results never arrived
```

Every test ran to completion. All four workers were then idle in
`multiprocessing.queues.get()`, and the parent sat in `IMapIterator.next()`
waiting for results that never came. A `py-spy` dump of the parent showed
`_handle_workers` and `_handle_tasks` but **no `_handle_results` thread** — the
pool's result-handler thread was gone, which is why delivery stopped dead.

Ruled out:

* **not unpicklable results.** Pickling every result in the worker succeeded;
  the whole run's results total 188 KB, the largest 5.2 KB.
* **not a stuck test.** All 659 subsuites reported done.
* **not the `FLOPPY_TEST_FAST_DB` flag.** The first full-suite run in this
  session, with real migrations and no flag, also ran 21 minutes without
  producing output.
* **not the fork start method.** See below — it hangs under `spawn` too.

It is **intermittent** — a later identical run of `app integrations` passed
(659/659 received). That matches the reported experience of the GitHub run
taking "about 30 minutes, sometimes 40".

This is a race in the interaction between Django's parallel runner and
`multiprocessing.Pool`, not a Floppy bug in the ordinary sense, and fixing it
properly is its own investigation. What is not acceptable is that it hangs
silently.

### What was done

`scripts/test.sh` now wraps the runner in `timeout --kill-after=30s`, default
2700 s, tunable with `FLOPPY_TEST_TIMEOUT` (`0` disables). A lost-result hang
now fails loudly with a non-zero exit instead of never returning. CI already
has `timeout-minutes` on its jobs.

### `spawn` does not fix it

The leading theory was fork-related: workers forked from a parent that already
has pool threads can inherit a half-held queue lock, and corrupted bytes on the
result queue would kill the unpickling thread. `spawn` re-imports instead of
forking and cannot inherit a lock, so it was the obvious test.

`FLOPPY_TEST_START_METHOD=spawn`, `app integrations`, three attempts:

| attempt | result |
| --- | --- |
| 1 | passed, 194 s |
| 2 | **hung** (killed at 900 s) |
| 3 | passed, 166 s |

So the hang survives `spawn`, and the fork theory is wrong. Worth noting that
spawn is not slower here, despite re-importing per worker.

It is also load-dependent: four consecutive `app integrations` runs on an
otherwise idle box all passed, while the hangs in this session all happened
when something else was competing for CPU. That is consistent with a timing
race and is why it resists a small reproduction.

### What is armed for next time

`config/test_settings.py` now calls `faulthandler.enable()` unconditionally,
and `FLOPPY_TEST_WATCHDOG=<seconds>` arms `faulthandler.dump_traceback_later`
so every thread's stack is dumped if a run outlives its budget.
`scripts/test.sh` sets that automatically to 120 s before
`FLOPPY_TEST_TIMEOUT`, so a hang now dumps its stacks *before* the timeout
kills it, with no one needing to remember a flag.

CI accepts workflow changes through the normal PR checks. Hosted run
`38993407779` took almost 69 minutes, so the test job now allows 120 minutes,
with `FLOPPY_TEST_TIMEOUT=6600` and `FLOPPY_TEST_WATCHDOG=6300`. PR, publish
and coverage runs have the same bounded test budget, with time left for setup and
reporting. The watchdog leaves thread stacks before the runner times out:

```yaml
      - name: Run Tests
        env:
          FLOPPY_TEST_TIMEOUT: "6600"
          FLOPPY_TEST_WATCHDOG: "6300"
```

### Fixed: the suite runs serially, and survives parallel if you ask for it

There turned out to be **two** failures wearing the same costume, and only one
of them is a delivery race:

* On a *subset* of the suite (`app integrations`), results go missing at random
  and re-dispatching to a fresh pool recovers them — five runs out of five,
  clean.
* On the *whole* suite, the same subsuites go missing every time. Measured:
  **131 of 923 lost, and three fresh pools recovered only 2 of them.** Work
  that is lost deterministically is not a delivery race — the workers carrying
  it are dying, which is consistent with the SIGSEGV seen on CI. No amount of
  re-dispatching fixes that, and the run ends red after half an hour.

So `config/test_runner.py` (wired in via `TEST_RUNNER`) does two things:

**It runs serially by default.** With `parallel=1` Django builds a plain suite
and never touches `multiprocessing` at all — `DiscoverRunner.build_suite`
guards the entire parallel path on `parallel > 1`. No pool, no worker to lose,
nothing to hang on. Measured on the full suite with real migrations:

```
Ran 6127 tests in 1185.348s   (~20 min, no hang)
```

That is inside the 30–40 minutes this suite historically took, and the hashing
and fixture work above is part of why.

**It makes parallel survivable for anyone who opts in** with
`FLOPPY_TEST_PARALLEL=<n>`: completion is counted rather than inferred from
`StopIteration`, a stall re-dispatches the missing work to a fresh pool up to
`FLOPPY_TEST_MAX_DISPATCH_ATTEMPTS` times, and if that fails the run **fails
loudly and ends** rather than hanging or pretending the missing tests passed.

#### Three things that had to be learned the hard way

Each was a version of this runner that moved the hang instead of removing it,
and each is now pinned by a test in `config/tests/test_resilient_test_runner.py`:

1. **`pool.join()` on the healthy path.** Joining waits on the pool's handler
   threads; when one has died, that wait *is* the hang.
2. **`pool.terminate()` anywhere.** `_terminate_pool` calls
   `_help_stuff_finish`, which takes the task queue's `_rlock` — still held by
   the wedged task handler. Caught live with `py-spy`:
   `_help_stuff_finish (pool.py:675)` ← `_terminate_pool (pool.py:695)` ←
   `terminate (pool.py:657)`.
3. **The pool's atexit finalizer.** `Pool` registers `_terminate_pool` through
   `util.Finalize`, so multiprocessing's exit handler runs it on the way out —
   back into the same deadlock *after* the suite printed its results. A run
   reported "Ran 4302 tests" and then never exited.

#### Why recovery re-dispatches instead of running in the parent

The first working version ran the undelivered subsuites in the parent. It
completed the suite but produced `tearDownClass` errors —
`TransactionManagementError: The rollback flag doesn't work outside of an
'atomic' block` — because the parent spends the parallel phase orchestrating
and its connections never went through `_init_worker`. Adopting a worker's
connections with `setup_worker_connection` did not fix it either. The parent
is not a faithful environment, so that path was removed.

#### What serial execution exposed

Running everything in one process surfaced a latent isolation bug that
parallelism had been hiding: `app.tests.test_celery_broker` registers stand-in
Celery tasks under real route names, but `app.finalize()` replays every
`@shared_task` onto the new app, so the real `import_radarr_recurring` won the
name and failed its signature check. It only bites when `app` and
`integrations` tests share a process. Fixed by registering the stand-ins after
`finalize()`.

### Superseded: the earlier recovery-only design

`config/test_runner.py` (wired in via `TEST_RUNNER` in test settings) replaces
Django's collection loop. It does not fix the race — nobody has root-caused it
— it makes the race survivable:

* **Completion is counted**, not inferred from `StopIteration`.
* **Stalling is bounded.** If nothing arrives for `FLOPPY_TEST_STALL_TIMEOUT`
  seconds (default 300), the pool is abandoned and whatever never came back is
  **re-dispatched to a fresh pool**, up to `FLOPPY_TEST_MAX_DISPATCH_ATTEMPTS`
  times (default 5). Only the missing subsuites are retried, so a successful
  retry is quick.
* **Teardown touches neither `pool.join()` nor `pool.terminate()`** — both can
  block forever on a pool in this state. Workers are killed directly and the
  pool's atexit finalizer is cancelled.
* **If every attempt fails**, the run *fails loudly and ends*. It never hangs,
  and it never reports missing tests as passing.

Measured on the `app integrations` combination that reproduces the race, five
consecutive runs:

| run | result | stalls | errors | duration |
| --- | --- | --- | --- | --- |
| 1 | pass | yes, recovered on attempt 4 | 0 | 462 s |
| 2-5 | pass | none | 0 | ~141 s |

Before this, the same combination hung outright.

#### Three things that had to be learned the hard way

Each of these was a version of the runner that moved the hang rather than
removing it, and each is now pinned by a test:

1. **`pool.join()` on the healthy path.** Joining waits on the pool's handler
   threads; when one has died, that wait *is* the hang.
2. **`pool.terminate()` anywhere.** `_terminate_pool` calls
   `_help_stuff_finish`, which takes the task queue's `_rlock` — still held by
   the wedged task handler. Observed blocking forever:
   `_help_stuff_finish (pool.py:675)` ← `_terminate_pool (pool.py:695)` ←
   `terminate (pool.py:657)`.
3. **The pool's atexit finalizer.** `Pool` registers `_terminate_pool` through
   `util.Finalize`, so multiprocessing's exit handler runs it on the way out —
   back into the same deadlock *after* the suite has printed its results. A
   recovered run reported "Ran 4302 tests" and then never exited. Cancelling
   the finalizer is what lets the process die.

#### Why recovery re-dispatches instead of running in the parent

The first working version ran the undelivered subsuites in the parent process.
It completed the suite, but produced `tearDownClass` errors —
`TransactionManagementError: The rollback flag doesn't work outside of an
'atomic' block` — because the parent spends the parallel phase orchestrating
and its connections never went through `_init_worker`. Adopting a worker's
connections via `setup_worker_connection` did not fix it either. A fresh pool
is a faithful environment and the parent is not, so recovery retries pools and
the in-process path was removed entirely.

### CPython confirms the dead result handler

An attempt to mitigate this (a `TEST_RUNNER` that stops waiting after a stall
and re-runs the missing subsuites in-process) was built, tested and then
**reverted** — but it produced the best evidence so far. On detecting the
stall it called `pool.terminate()`, which raised:

```
AssertionError: Cannot have cache with result_handler not alive
```

That is `multiprocessing.pool._terminate_pool` asserting that a pool with
outstanding results in its cache must still have a live `_result_handler`
thread. It does not. So the py-spy reading is now confirmed by CPython's own
invariant: **results are outstanding and the thread that would deliver them is
dead.** That is the bug, stated precisely.

That mitigation was briefly reverted when a second reproduction appeared to
show the stall branch never firing. `py-spy` on the live hang showed the
opposite: the stall *was* detected, and the process was then stuck inside
`pool.terminate()`. That is finding 2 above, and the runner described earlier
is the result of fixing it.

### What should be done next

* Root-cause what kills `_handle_results`. The runner survives the symptom;
  it does not explain it. A `parallel_runner_stalled` line in a CI log is the
  signal that it is still happening.
* A segfault (`selectors.py`, exit 139) was seen once, right after a watchdog
  dump. `selectors` is what `multiprocessing.connection.wait()` uses, which is
  where `_handle_results` sits — suggestive, but seen once and not confirmed.
* Try a smaller `--parallel` worker count and see whether the rate changes;
  that would confirm the timing-race reading.
* Until then, treat a suite run that produces no output well past its usual
  wall time as this bug, not as a slow test.

## Coverage runs beside the publish gate, not in front of it

Until November 2026 a push to `latest` ran the three shards under
`coverage run` with one context per test, inside the Docker publish gate. The
tracer is needed for the affected-test map (`ctrace`, because `sysmon` on
3.12 keeps only the first test per line), but nothing it produces decides
whether an image is safe, and it slowed every shard on the critical path.

Now:

- The publish gate (`docker-image.yml` calling `app-tests.yml` with
  `release_quality_gates`) runs the same tests through `scripts/test.sh`,
  with no tracer. The exclusions are unchanged: `network` and `playwright`.
  The timeout and watchdog come from `scripts/test.sh`.
- `coverage.yml` runs on every push to `latest`: the same three shards under
  coverage, then the combine step, the `affected-map` artifact and the
  Codecov upload. Its job summary records the number of measured files and
  test contexts. A newer push cancels a run still in flight, so only the
  newest commit's map is built.
- A red coverage run does not block or unpublish an image. It shows as a red
  run on `latest`.

The two workflows list the shards separately. Change them together.

## Knowing when latest itself is broken

From 12 to 15 November 2026, 30 of 100 pull request runs of App Tests failed.
27 of those fell in four windows where unrelated branches failed the same
tests, and each window began with a push to `latest` and ended when a fix
reached it (one began when the date entered the Discover holiday window).
Three of the four were non-browser tests: the publish gate went red on
`latest` within about 20 minutes, but a red run on `latest` tells nobody. The
fourth was four Playwright tests, which the publish gate leaves out on
purpose, so nothing on `latest` ran them at all.

`latest-health.yml` closes both gaps without gating anything:

- On every push to `latest` it runs the browser tests pull requests must
  pass: `scripts/test.sh app integrations --tag playwright --exclude-tag
  flaky_alphabet_rail`, with real migrations. A newer push cancels the
  older run.
- When the publish gate finishes for a push to `latest`, it reads the
  gate's test shards.
- `scripts/latest_health.py` keeps one issue, **Tests are failing on
  latest**: opened on the first failure with the failing tests, the first
  bad commit and the range since the last good one; edited while anything
  still fails; commented on and closed only when both sources have passed
  cleanly. The closed issue keeps each source's last result, so a result for
  an older commit than the one recorded is ignored even when nothing is open. A run that could not tell
  (Chromium did not install or start, a job was killed, no test was named)
  is "unknown" and never opens or closes the issue.
- When a pull request's shards fail, the **Compare failures with latest**
  job in `app-tests.yml` writes one line in the run summary: whether the
  same tests are failing on `latest`. It says green only when both sources
  passed `latest`'s newest commit; otherwise, or if it cannot read the
  state, it says "unknown". It is not required and never fails.

If the browser command in `app-tests.yml` changes (for example a separate
browser shard), change the one in `latest-health.yml` with it.

Rollback: delete `latest-health.yml` and the `baseline_note` job.

## Reusing a pull request's test result on the publish gate

A push to `latest` runs the Docker publish gate, which runs the three test
shards. When that push is a pull request merge and the branch was up to date,
the pull request's own `App Tests` run already executed on a commit with the
same Git tree. The first job of the gate, `Reuse PR test result?`
(`scripts/reuse_pr_tests.py`), decides whether that is so, and the shards are
skipped only when it is.

It is off unless the repository variable `REUSE_PR_TEST_RESULTS` is `true`.
Without it the job still evaluates every push and writes what it would have
decided to its job summary (shadow mode), and the full gate runs. To turn the
feature off, delete the variable.

What is compared is the tree, not the commit. A merge commit is never the
pull request head, but a tree hash names every file in the checkout: source,
tests, `uv.lock`, the workflows and the test tooling. Equal trees give a test
run identical input files.

The tested tree is read from GitHub's record of the run, not inferred from
the pull request head. A `pull_request` run executes on GitHub's test merge
commit (`refs/pull/N/merge`). The run's `head_sha` is the pull request head,
whose tree differs whenever the branch was behind. The test merge commit is
reported in the run's `referenced_workflows` (as `sha`, with
`ref: refs/pull/N/merge`), which is present because `App Tests` references
the local lint workflow. If that reference is ever removed, runs stop
reporting a tested commit and nothing is reused.

Reuse needs all of these. Anything else, including any API error, runs the
full gate:

1. The event is a push to `refs/heads/latest`. Tags, `release` and manual
   runs are never reused.
2. The pushed commit is associated with exactly one pull request, merged into
   `latest` in this repository, with the pushed commit as its merge commit.
3. The newest `App Tests` `pull_request` run for that pull request's head is
   completed and successful, and ran on that pull request's merge ref.
4. The commit that run executed merges the pull request head, and its tree
   equals the pushed tree.
5. That run's `test (3.12)` job succeeded on its latest attempt.
6. The run started no more than 72 hours ago, so a result does not outlive
   the runner image, Python patch release and browser build it ran on.

What this means in practice:

| Push to `latest` | Result |
| --- | --- |
| Up-to-date green pull request, merge commit or squash | Reused |
| Pull request merged while behind `latest` (bypass) | Full gate: trees differ |
| Pull request whose run failed, merged by bypass | Full gate |
| Docs-only pull request (no `App Tests` run) | Full gate |
| Direct push | Full gate: no pull request |
| Tag, `release`, manual run | Full gate: not applicable |

Still run on the pushed commit every time: migration hygiene and
`makemigrations --check` (the `Migration checks` job, because hygiene reads
the upstream baseline at run time), OAuth on PostgreSQL, both upgrade
replays, release lint and the image smoke. MCP and script tests run in the
`app` shard and are reused with it.

The `test (3.12)` job passes when the shards succeeded, or when the decision
job succeeded, said `reuse`, and the shards were skipped. A pull request run
can never take the second path: the decision output is forced to `run` for
every event except the publish-gate push to `latest`. That also means a
reused result can never become the evidence for another reuse, because
evidence is only ever a `pull_request` run.

Coverage is unaffected: `coverage.yml` runs its own shards on every push to
`latest`, whether or not the publish gate reused a result.

## Reusing the migrated test database

The migration floor is paid once per set of migration inputs, not once per
command or per CI job. `scripts/test.sh` sets `FLOPPY_TEST_DB_TEMPLATE=1` for
local runs, and `app-tests.yml` sets it for the test shards and the browser
job. The
first run replays every migration as before and then saves the result to
`.floppy/testdb-<key>-<contents>.sqlite3`. Later runs copy that file into the
in-memory test database and let Django run `migrate` on it, which finds nothing
to apply. The runner prints one `[test-db]` line saying which happened.

Measured on a Mac, `scripts/test.sh app.tests.test_query_counts`:

| | database setup | whole command |
| --- | --- | --- |
| replay (first run, or any input changed) | 124 s | 141 s |
| restore | 0.7 s | 16 s |

This is not the models-only schema of `FLOPPY_TEST_FAST_DB=1`. The saved file is
the output of a real replay, so `RunPython` rows are in it and tests that are
skipped under the fast schema still run.

### Why a restored database is the replayed one

It was measured before anything was built on it:

* Two independent replays produce the same schema (`sqlite_master`, in creation
  order), the same `PRAGMA` values and the same rows in every table that has
  rows (`django_migrations`, `django_content_type`, `auth_permission`,
  `sqlite_sequence`). The only difference is the 593 `applied` timestamps in
  `django_migrations`, which differ between any two test runs anyway.
* A replayed database saved to disk, restored, run through `migrate` and saved
  again is the same file byte for byte. SQLite's backup API copies pages, so
  the layout, the autoincrement counters and therefore the ids tests receive
  are unchanged.
* Each test still gets the isolation it had: the restore happens once, before
  Django's setup, and everything after it (per-test transactions, flushes,
  worker clones) is Django's unchanged code working on that database.

### What stops a stale database being used

A stale template would mean tests passing against an old schema, so reuse does
not rest on the key alone (`src/config/test_db_template.py`):

1. **The key** hashes every file under `src/*/migrations/`, `uv.lock`, the
   settings and runner modules, the SQLite backend, every first-party module
   that registers a `pre_migrate` or `post_migrate` receiver, and the Python
   and SQLite versions. Any change is a miss, and a miss is a real replay.
2. **`migrate` still runs** on a restored database. If it plans to apply
   anything, the key was wrong: the file is deleted and the run fails before a
   single test.
3. **The contents are checked.** The file name carries a hash of the schema and
   every row. A truncated or edited file is deleted and replaced by a replay.
   If the database no longer matches that hash after `migrate`, the run fails.

`FLOPPY_TEST_DB_TEMPLATE=0` turns reuse off. It never applies to PostgreSQL,
`--keepdb`, `FLOPPY_TEST_FAST_DB=1` or `--affected-record`.

### In CI

`app-tests.yml` has one job, "Migrated test database", in front of the shards:

1. It computes the runner's own key (`python src/config/test_db_template.py`)
   and asks `actions/cache` for a file saved under it.
2. It runs `config.tests.test_test_db_template`, which holds one
   database-backed test. On a cache miss that replays every migration and
   saves the result. On a hit it restores the file, and `migrate` must find
   nothing to apply.
3. It uploads the file as a run artifact. The shards and the browser job
   download it into `.floppy/` and restore it under the same three guards.

The job never decides whether the gate passes. If it fails, or the artifact is
missing or does not match a shard's own key, that shard prints a warning and
replays migrations as before. It also runs when the shards reuse a pull
request's result, so the cache on `latest` stays warm for the next pull
request. A pull request that touches a migration gets a new key and a real
replay in that run.

The file holds an empty schema, permission and content-type rows and migration
bookkeeping. No account, secret or user data exists when it is saved.

Rollback: set the `TEST_DB_TEMPLATE` repository variable to `false`. Every job
then replays, with no commit needed. If a run ever reports a saved database that
"does not match this checkout", delete that cache entry; the key design has a
gap and it should be reported.

### Where migrations are still really replayed

| boundary | replay |
| --- | --- |
| any key input changes (a migration added, edited or renamed; the lockfile; settings; the runner) | full replay in that run |
| "Migrated test database" job | once per run when the key is new; otherwise a restore plus a `migrate` that must apply nothing |
| CI test shards and browser job | only when the saved database is missing, damaged or keyed differently |
| `Migration checks` (hygiene `--strict`, missing-migration check) | every run |
| `OAuth (PostgreSQL)` | every run, on PostgreSQL |
| `coverage.yml` shards | every run (reuse is not set there) |
| `Upgrade (sqlite)` and `Upgrade (postgres)` | every run, with retained user data |
| image smoke | every run, cold boot of a real database |
| `migration-order.yml` | graph load on every migration PR |
| `scripts/test_postgres.sh` | every run |

## How to run tests quickly now

```bash
# Iterating on non-migration code — the fast path.
FLOPPY_TEST_FAST_DB=1 SECRET=test-only scripts/test.sh app.tests.test_statistics_sync

# The real gate: real migrations, real graph. Replays once, then restores the
# saved result until a migration input changes.
SECRET=test-only scripts/test.sh app.tests.test_statistics_sync

# Force a replay.
FLOPPY_TEST_DB_TEMPLATE=0 SECRET=test-only scripts/test.sh app.tests.test_statistics_sync

# Whole fast suite.
SECRET=test-only scripts/test.sh

# Tests the current diff can reach. Pays the migration floor only when a
# migration input changed. Uses the coverage map when present.
SECRET=test-only scripts/test.sh --affected

# Record the map. Same suite CI runs on latest: slow included, network
# excluded. Do not set FLOPPY_TEST_FAST_DB. Line numbers have to match the
# tree the map was recorded on, which is origin/latest for a branch diff.
SECRET=test-only scripts/test.sh --affected-record
```

## Tests for the diff in front of you

`scripts/test.sh --affected` is the local iteration path when you do not
want to type a label. The change set is every commit since the merge-base
with `origin/latest`, plus staged, unstaged, and untracked files.

When `.floppy/affected.coverage` is present (or `FLOPPY_AFFECTED_MAP`
points at one), selection is the test modules whose coverage context
executed the old side of the diff. The map is built with
`dynamic_context = test_function` and `core = ctrace`. The ctrace core is
required: on Python 3.12 the sysmon core records only the first test to
hit a line. A changed test module always runs. A pure insertion selects
tests that executed the anchor line. A new file, or a file coverage does
not measure (`migrations`, `__init__.py`, `admin.py`), falls through to
the import walk below. Changed lines without a recorded test also fall back;
an untested branch must not turn a code change into a no-test success.

`scripts/test.sh --affected-record` runs the CI suite (slow included,
network excluded) and writes that file for local iteration. Application PRs
run the full offline app list rather than relying on historical line selection.
Publication calls the same reusable suite and uploads a fresh map.

Without a map, a changed test module runs. A changed source module runs
the test modules that import it, including a name re-exported from a
package `__init__.py`. The walk is not transitive: `from app.models import
Movie` selects tests when `media.py` changes, and does not select them
when some other model file changes. If nothing imports the file and it
sits under one app, that app runs. Modules loaded by string from
`AppConfig.ready()` (`app.signals`, `app.signals_watch_state`,
`app.signals_music`, `integrations.signals_state`, `users.signals`) run
the app when the map has no line for them, because a static importer list
is the wrong set. Music hook files under `MUSIC_HOOKS_DIR` run `app`.

`src/templates/`, `src/static/`, `uv.lock`, `pyproject.toml`, settings, and
the runner (`scripts/test.sh`, `src/config/test_runner.py`,
`src/config/affected_tests.py`, and the other paths in
`ESCAPE_HATCH`) run the fast suite. Coverage never sees a template.
Docs and other non-code run nothing. `mcp_server/` and `scripts/tests/`
are named and do not escalate: the Django suite does not collect them.

A selected label excludes `@tag("network")` and does not exclude
`@tag("slow")`, so a slow test you changed still runs. The fast-suite
fallback excludes both, unless the caller passes `--include-slow`.
`scripts/test.sh` with no arguments is still the fast suite.

Running apps one at a time (`app`, then `users`, …) both avoids the
lost-result hang seen so far and gives usable per-app timings:

| app | tests | time (fast DB) |
| --- | --- | --- |
| app | 2,880 | 153 s |
| api | 715 | 142 s |
| integrations | 1,422 | 33 s |
| lists | 268 | 11 s |
| events | 174 | 10 s |
| config | 245 | 10 s |
| users | 411 | 6 s |

## Pre-existing failure worth knowing about

`config.tests.test_container_memory_sample.ReconciliationTests.test_complete_sample_reports_totals_as_reconciled`
fails in this container with and without any change here: it reads real
`/proc` PSS and finds one process without `smaps_rollup`
(`rss_only_processes` is 1, not 0). It is environment-dependent, not a
regression.
