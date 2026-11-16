"""Cover the high-water attribution layer.

Every filesystem read the module does goes through ``_read_text``, so these
tests inject a fake ``/proc`` and cgroup instead of building one on disk. That
is deliberate: the suite runs on hosts with cgroup v1, with cgroup v2, and --
on GitHub's runners -- with no memory controller at all, and an instrumentation
layer that raised on any of them would take a production request down with it.
"""

from unittest.mock import patch

from django.db import connection
from django.http import HttpResponse
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import ResolverMatch

from app import memory_envelope, request_timing

_STATM = "1000 2048 300 40 0 500 0\n"
_STATUS = "Name:\tpython\nVmPeak:\t  900000 kB\nVmHWM:\t   16384 kB\nVmRSS:\t    8192 kB\n"
_MEMORY_STAT = (
    "anon 104857600\nfile 209715200\nkernel_stack 131072\n"
    "kernel 8388608\nslab 4194304\npgfault 12345\n"
)

_PAGE = memory_envelope._PAGE_SIZE


def fake_proc(files):
    """Return a _read_text stand-in serving `files`, None for anything else."""

    def read(path):
        return files.get(path)

    return read


def linux_host(**overrides):
    """Return a full, healthy cgroup-v2 + /proc fake, with optional overrides."""
    files = {
        "/proc/self/statm": _STATM,
        "/proc/self/status": _STATUS,
        "/sys/fs/cgroup/memory.current": "1073741824\n",
        "/sys/fs/cgroup/memory.stat": _MEMORY_STAT,
        "/sys/fs/cgroup/memory.peak": "2147483648\n",
    }
    files.update(overrides)
    return fake_proc(files)


class SampleMemoryTests(SimpleTestCase):
    """The reader turns proc/cgroup text into a sample, or into unknowns."""

    def test_reads_every_field_on_a_cgroup_v2_host(self):
        """A healthy host fills both envelopes from five cheap reads."""
        with patch.object(memory_envelope, "_read_text", linux_host()):
            sample = memory_envelope.sample_memory()

        self.assertEqual(sample.rss_bytes, 2048 * _PAGE)
        # resident 2048 pages minus 300 shared (file and shmem) pages.
        self.assertEqual(sample.rss_anon_bytes, (2048 - 300) * _PAGE)
        self.assertEqual(sample.hwm_bytes, 16384 * 1024)
        self.assertEqual(sample.cgroup_current_bytes, 1073741824)
        self.assertEqual(sample.cgroup_anon_bytes, 104857600)
        self.assertEqual(sample.cgroup_file_bytes, 209715200)
        self.assertEqual(sample.cgroup_kernel_bytes, 8388608)
        self.assertEqual(sample.cgroup_peak_bytes, 2147483648)

    def test_absent_cgroup_leaves_process_fields_intact(self):
        """A cgroup v1 host, or a bare CI runner, still reports RSS and VmHWM."""
        reader = fake_proc({"/proc/self/statm": _STATM, "/proc/self/status": _STATUS})
        with patch.object(memory_envelope, "_read_text", reader):
            sample = memory_envelope.sample_memory()

        self.assertEqual(sample.rss_bytes, 2048 * _PAGE)
        self.assertEqual(sample.hwm_bytes, 16384 * 1024)
        self.assertIsNone(sample.cgroup_current_bytes)
        self.assertIsNone(sample.cgroup_anon_bytes)
        self.assertIsNone(sample.cgroup_file_bytes)

    def test_nothing_readable_is_all_unknown_rather_than_an_error(self):
        """No /proc at all must degrade, not raise: this runs inside requests."""
        with patch.object(memory_envelope, "_read_text", fake_proc({})):
            sample = memory_envelope.sample_memory()

        self.assertIsNone(sample.rss_bytes)
        self.assertIsNone(sample.hwm_bytes)
        self.assertIsNone(sample.cgroup_current_bytes)

    def test_absent_kernel_key_is_unknown_not_zero(self):
        """An older kernel omits memory.stat's `kernel` roll-up."""
        reader = linux_host(
            **{"/sys/fs/cgroup/memory.stat": "anon 100\nfile 200\nslab 300\n"},
        )
        with patch.object(memory_envelope, "_read_text", reader):
            sample = memory_envelope.sample_memory()

        self.assertEqual(sample.cgroup_anon_bytes, 100)
        self.assertIsNone(sample.cgroup_kernel_bytes)

    def test_unparsable_values_are_unknown_not_zero(self):
        """A garbled file must not be reported as a real measurement of zero."""
        reader = linux_host(
            **{
                "/proc/self/statm": "not a number\n",
                "/proc/self/status": "VmHWM:\tnonsense kB\n",
                "/sys/fs/cgroup/memory.current": "max\n",
            },
        )
        with patch.object(memory_envelope, "_read_text", reader):
            sample = memory_envelope.sample_memory()

        self.assertIsNone(sample.rss_bytes)
        self.assertIsNone(sample.hwm_bytes)
        self.assertIsNone(sample.cgroup_current_bytes)

    def test_read_text_swallows_os_errors(self):
        """The real reader must return None for a missing path, not raise."""
        self.assertIsNone(memory_envelope._read_text("/proc/floppy/does-not-exist"))

    def test_sampling_does_not_read_smaps(self):
        """PSS collection is a VMA walk and stays out of the hot path."""
        seen = []

        def record(path):
            seen.append(path)

        with patch.object(memory_envelope, "_read_text", record):
            memory_envelope.sample_memory()

        self.assertTrue(seen)
        self.assertFalse([path for path in seen if "smaps" in path])


@override_settings(
    MEMORY_HIGH_WATER_ENABLED=True,
    MEMORY_HIGH_WATER_DURATION_MS=10_000,
    MEMORY_HIGH_WATER_RSS_DELTA_BYTES=32 * 1024 * 1024,
    MEMORY_HIGH_WATER_HWM_DELTA_BYTES=32 * 1024 * 1024,
    MEMORY_HIGH_WATER_CGROUP_DELTA_BYTES=128 * 1024 * 1024,
    MEMORY_HIGH_WATER_CGROUP_FILE_DELTA_BYTES=128 * 1024 * 1024,
    MEMORY_HIGH_WATER_CEILING_RATIO=0.85,
    GUNICORN_MAX_WORKER_MEMORY_BYTES=400 * 1024 * 1024,
)
class ReportBoundaryTests(SimpleTestCase):
    """Only excursions are logged, and each one says which kind it was."""

    def setUp(self):
        """Pin the role so a ceiling check does not depend on the runner."""
        patcher = patch.object(memory_envelope, "process_role", return_value="web")
        patcher.start()
        self.addCleanup(patcher.stop)

    def report(self, before, after, duration_ms=10.0):
        """Run one boundary and return (reasons, log records)."""
        with self.assertLogs("app.memory_envelope", level="INFO") as captured:
            # A no-op log keeps assertLogs from failing when nothing fires,
            # so a "logged nothing" case is assertable rather than an error.
            memory_envelope.logger.info("probe")
            reasons = memory_envelope.report_boundary(
                kind="request",
                name="/medialist/movie",
                before=before,
                after=after,
                duration_ms=duration_ms,
            )
        events = [line for line in captured.output if "memory_high_water" in line]
        return reasons, events

    def test_ordinary_boundary_logs_nothing(self):
        """The whole point of leaving this on is that it is usually silent."""
        sample = memory_envelope.MemorySample(
            rss_bytes=100 * 1024 * 1024,
            hwm_bytes=100 * 1024 * 1024,
            cgroup_current_bytes=500 * 1024 * 1024,
            cgroup_anon_bytes=1,
            cgroup_file_bytes=1,
        )
        reasons, events = self.report(sample, sample, duration_ms=12.0)

        self.assertEqual(reasons, ())
        self.assertEqual(events, [])

    def test_slow_boundary_reports_duration(self):
        """A two-minute request is an excursion even if it freed what it built."""
        sample = memory_envelope.MemorySample(rss_bytes=1, hwm_bytes=1)
        reasons, events = self.report(sample, sample, duration_ms=120_000)

        self.assertEqual(reasons, ("duration",))
        self.assertIn("kind=request", events[0])
        self.assertIn("name=/medialist/movie", events[0])
        self.assertIn("duration_ms=120000", events[0])

    def test_resident_growth_reports_rss(self):
        """Process-anonymous growth is named separately from page cache."""
        before = memory_envelope.MemorySample(rss_bytes=100 * 1024 * 1024)
        after = memory_envelope.MemorySample(rss_bytes=200 * 1024 * 1024)
        reasons, events = self.report(before, after)

        self.assertIn("rss_growth", reasons)
        self.assertIn(f"rss_delta={100 * 1024 * 1024}", events[0])

    def test_freed_excursion_is_still_caught_by_peak_rss(self):
        """RSS returns to where it started; VmHWM keeps the mark.

        This is the case a before/after RSS delta alone would miss entirely.
        """
        before = memory_envelope.MemorySample(
            rss_bytes=120 * 1024 * 1024,
            hwm_bytes=150 * 1024 * 1024,
        )
        after = memory_envelope.MemorySample(
            rss_bytes=121 * 1024 * 1024,
            hwm_bytes=1500 * 1024 * 1024,
        )
        reasons, events = self.report(before, after)

        self.assertEqual(reasons, ("peak_rss",))
        self.assertIn(f"hwm_after={1500 * 1024 * 1024}", events[0])

    def test_page_cache_growth_is_distinguished_from_anonymous_growth(self):
        """A page-cache excursion must not read as a Python leak.

        A 2 GiB snapshot written through the page cache grows ``file`` and
        leaves ``anon`` where it was; the reason names which one it is.
        """
        before = memory_envelope.MemorySample(
            rss_bytes=100 * 1024 * 1024,
            cgroup_current_bytes=800 * 1024 * 1024,
            cgroup_anon_bytes=400 * 1024 * 1024,
            cgroup_file_bytes=300 * 1024 * 1024,
        )
        after = memory_envelope.MemorySample(
            rss_bytes=101 * 1024 * 1024,
            cgroup_current_bytes=2800 * 1024 * 1024,
            cgroup_anon_bytes=401 * 1024 * 1024,
            cgroup_file_bytes=2300 * 1024 * 1024,
        )
        reasons, events = self.report(before, after)

        self.assertIn("page_cache_growth", reasons)
        self.assertIn("cgroup_growth", reasons)
        self.assertNotIn("rss_growth", reasons)
        self.assertIn(f"file_after={2300 * 1024 * 1024}", events[0])
        self.assertIn(f"anon_after={401 * 1024 * 1024}", events[0])

    def test_unknown_values_never_trip_a_threshold(self):
        """A host that cannot report a number must not manufacture events."""
        unknown = memory_envelope.MemorySample()
        reasons, events = self.report(unknown, unknown, duration_ms=10.0)

        self.assertEqual(reasons, ())
        self.assertEqual(events, [])

    def test_unknown_values_are_logged_as_unknown(self):
        """When one probe fails, the event says so rather than printing 0."""
        before = memory_envelope.MemorySample(rss_bytes=1)
        after = memory_envelope.MemorySample(rss_bytes=1)
        _reasons, events = self.report(before, after, duration_ms=60_000)

        self.assertIn("cgroup_after=unknown", events[0])
        self.assertIn("rss_delta=0", events[0])

    def test_ending_near_the_recycle_ceiling_is_reported(self):
        """The boundary that will get this worker retired is worth naming."""
        sample = memory_envelope.MemorySample(rss_bytes=390 * 1024 * 1024)
        reasons, events = self.report(sample, sample, duration_ms=1.0)

        self.assertEqual(reasons, ("near_recycle_ceiling",))
        self.assertIn(f"ceiling={400 * 1024 * 1024}", events[0])

    def test_mapped_database_pages_are_not_near_the_ceiling(self):
        """The ceiling is compared on what a recycle returns, as the recyclers do.

        A worker that has read a large table holds the memory-mapped database
        in its RSS. That is page cache; retiring the worker frees none of it.
        """
        sample = memory_envelope.MemorySample(
            rss_bytes=390 * 1024 * 1024,
            rss_anon_bytes=150 * 1024 * 1024,
        )
        reasons, _events = self.report(sample, sample, duration_ms=1.0)

        self.assertEqual(reasons, ())

    def test_anonymous_growth_near_the_ceiling_is_reported(self):
        """Python's own memory near the ceiling still names the boundary."""
        sample = memory_envelope.MemorySample(
            rss_bytes=395 * 1024 * 1024,
            rss_anon_bytes=380 * 1024 * 1024,
        )
        reasons, events = self.report(sample, sample, duration_ms=1.0)

        self.assertEqual(reasons, ("near_recycle_ceiling",))
        self.assertIn(f"rss_anon_after={380 * 1024 * 1024}", events[0])

    @override_settings(GUNICORN_MAX_WORKER_MEMORY_BYTES=0)
    def test_disabled_ceiling_never_reports_near_ceiling(self):
        """Retirement off means there is no ceiling to be near."""
        sample = memory_envelope.MemorySample(rss_bytes=10 * 1024 * 1024 * 1024)
        reasons, _events = self.report(sample, sample, duration_ms=1.0)

        self.assertEqual(reasons, ())

    @override_settings(MEMORY_HIGH_WATER_DURATION_MS=0)
    def test_zero_threshold_disables_that_signal(self):
        """Zero means off, not "fire on every boundary"."""
        sample = memory_envelope.MemorySample(rss_bytes=1)
        reasons, _events = self.report(sample, sample, duration_ms=999_999)

        self.assertEqual(reasons, ())


class RoleCeilingTests(SimpleTestCase):
    """Each role reports the ceiling that is actually enforced against it."""

    @override_settings(GUNICORN_MAX_WORKER_MEMORY_BYTES=321 * 1024 * 1024)
    def test_web_role_reads_the_gunicorn_ceiling(self):
        """One definition, shared with config/gunicorn.py."""
        self.assertEqual(
            memory_envelope.role_ceiling_bytes("web"),
            321 * 1024 * 1024,
        )

    @override_settings(CELERY_WORKER_MAX_MEMORY_PER_CHILD=140 * 1024)
    def test_celery_roles_read_the_child_ceiling_in_bytes(self):
        """Celery configures KiB; the event reports bytes like everything else."""
        self.assertEqual(
            memory_envelope.role_ceiling_bytes("interactive"),
            140 * 1024 * 1024,
        )

    @override_settings(CELERY_WORKER_MAX_MEMORY_PER_CHILD=None)
    def test_absent_celery_ceiling_is_none(self):
        """No ceiling configured is None, not zero."""
        self.assertIsNone(memory_envelope.role_ceiling_bytes("background"))


class RedactedRouteTests(SimpleTestCase):
    """Request names must identify the page without carrying a secret."""

    def name_for(self, route, kwargs):
        """Return the logged name for a request matched to `route`."""

        class FakeRequest:
            resolver_match = ResolverMatch(
                func=lambda r: None,
                args=(),
                kwargs=kwargs,
                url_name="x",
                route=route,
            )

        return memory_envelope.redacted_route(FakeRequest())

    def test_keeps_the_part_that_identifies_the_page(self):
        """"/medialist/<str:media_type>" alone would not say *movie*."""
        self.assertEqual(
            self.name_for("medialist/<str:media_type>", {"media_type": "movie"}),
            "/medialist/movie",
        )

    def test_redacts_credential_bearing_path_parameters(self):
        """A password-reset key travels in the path, not the query string."""
        name = self.name_for(
            "accounts/password/reset/key/<str:uidb36>-<str:key>/",
            {"uidb36": "abc123", "key": "super-secret-value"},
        )

        self.assertNotIn("super-secret-value", name)
        self.assertNotIn("abc123", name)
        self.assertEqual(name.count("<redacted>"), 2)

    def test_unresolved_request_has_no_path_in_its_name(self):
        """A 404 never resolves; the name must not fall back to the raw URL."""

        class FakeRequest:
            resolver_match = None

        self.assertEqual(memory_envelope.redacted_route(FakeRequest()), "<unresolved>")

    def test_uncaptured_placeholder_is_left_alone(self):
        """A parameter the resolver did not name is not guessed at."""
        self.assertEqual(
            self.name_for("thing/<int:pk>/", {}),
            "/thing/<int:pk>/",
        )


@override_settings(
    MEMORY_HIGH_WATER_ENABLED=True,
    MEMORY_HIGH_WATER_DURATION_MS=1,
    GUNICORN_MAX_WORKER_MEMORY_BYTES=0,
)
class MiddlewareTests(SimpleTestCase):
    """The middleware brackets a response and never breaks one."""

    def test_reports_the_boundary_and_returns_the_response(self):
        """Normal operation: response passes through, one event is emitted."""
        seen = {}

        def capture(**kwargs):
            seen.update(kwargs)
            return ("duration",)

        middleware = memory_envelope.MemoryHighWaterMiddleware(
            lambda request: HttpResponse("ok"),
        )
        class FakeRequest:
            method = "GET"
            resolver_match = None

        with patch.object(memory_envelope, "report_boundary", capture):
            response = middleware(FakeRequest())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(seen["kind"], "request")
        self.assertEqual(seen["extra"], {"method": "GET", "status": 200})

    def test_a_failing_probe_never_fails_the_request(self):
        """Instrumentation is not allowed to turn a 200 into a 500."""

        def explode(**_kwargs):
            message = "probe blew up"
            raise RuntimeError(message)

        middleware = memory_envelope.MemoryHighWaterMiddleware(
            lambda request: HttpResponse("ok"),
        )

        class FakeRequest:
            method = "GET"
            resolver_match = None

        with (
            patch.object(memory_envelope, "report_boundary", explode),
            self.assertLogs("app.memory_envelope", level="ERROR"),
        ):
            response = middleware(FakeRequest())

        self.assertEqual(response.status_code, 200)

    @override_settings(MEMORY_HIGH_WATER_ENABLED=False)
    def test_disabled_middleware_takes_no_samples(self):
        """Switched off means no /proc reads at all, not just no logging."""
        middleware = memory_envelope.MemoryHighWaterMiddleware(
            lambda request: HttpResponse("ok"),
        )
        with patch.object(memory_envelope, "sample_memory") as sampler:
            response = middleware(object())

        self.assertEqual(response.status_code, 200)
        sampler.assert_not_called()


@override_settings(
    MEMORY_HIGH_WATER_ENABLED=True,
    MEMORY_HIGH_WATER_DURATION_MS=0,
    MEMORY_HIGH_WATER_RSS_DELTA_BYTES=32 * 1024 * 1024,
    GUNICORN_MAX_WORKER_MEMORY_BYTES=0,
    CELERY_WORKER_MAX_MEMORY_PER_CHILD=None,
)
class TaskBoundaryTests(SimpleTestCase):
    """Task boundaries report the task, its id, and nothing about its arguments."""

    def setUp(self):
        """Start from an empty tracking map whatever ran before."""
        memory_envelope._task_samples.clear()
        self.addCleanup(memory_envelope._task_samples.clear)

    def test_reports_task_name_and_id(self):
        """The id is the only way to tie this to the task's own log lines.

        The samples are injected rather than measured: a task that starts and
        ends in the same microsecond would otherwise cross no threshold, and
        the assertion would be about the clock instead of the report.
        """
        samples = [
            memory_envelope.MemorySample(rss_bytes=100 * 1024 * 1024),
            memory_envelope.MemorySample(rss_bytes=900 * 1024 * 1024),
        ]
        with patch.object(memory_envelope, "sample_memory", side_effect=samples):
            memory_envelope.task_started("abc-123")
            with self.assertLogs("app.memory_envelope", level="INFO") as captured:
                memory_envelope.task_finished(
                    "abc-123",
                    "Write database snapshot",
                    "SUCCESS",
                )

        event = captured.output[0]
        self.assertIn("kind=task", event)
        self.assertIn("name=Write database snapshot", event)
        self.assertIn("task_id=abc-123", event)
        self.assertIn("state=SUCCESS", event)

    def test_finishing_an_untracked_task_is_a_no_op(self):
        """A postrun without a prerun (eager edge, restarted child) is silent."""
        with patch.object(memory_envelope, "report_boundary") as report:
            memory_envelope.task_finished("never-started", "Some task")

        report.assert_not_called()

    def test_tracking_map_cannot_grow_without_bound(self):
        """A task whose postrun never fires must not leak the memory we measure."""
        for index in range(memory_envelope._MAX_TRACKED_TASKS * 3):
            memory_envelope.task_started(f"task-{index}")

        self.assertLessEqual(
            len(memory_envelope._task_samples),
            memory_envelope._MAX_TRACKED_TASKS,
        )


@override_settings(PERF_LOG_ENABLED=True, PERF_LOG_SLOW_TASK_MS=0)
class TaskPerformanceTests(TestCase):
    """Task diagnostics share request spans without leaking nested contexts."""

    def tearDown(self):
        for sample in reversed(memory_envelope._task_timings.get()):
            memory_envelope.task_timing_finished(sample.task_id, "cleanup")

    def test_task_reports_db_cache_provider_and_failure_state(self):
        memory_envelope.task_timing_started("timed-task")
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        with request_timing.boundary("cache"):
            pass

        @request_timing.timed_provider_call
        def provider():
            return None

        provider()
        with self.assertLogs("app.memory_envelope", level="INFO") as captured:
            memory_envelope.task_timing_finished("timed-task", "Import Trakt", "FAILURE")
        line = captured.output[0]
        for field in (
            "slow_task", "state=FAILURE", "task_id=timed-task", "queries=1",
            "cache_calls=1", "provider_calls=1", "db_ms=", "cpu_ms=",
            "unclassified_ms=", "rss_delta_bytes=", "process_hwm_bytes=",
        ):
            self.assertIn(field, line)
        self.assertEqual(memory_envelope._task_timings.get(), ())
        self.assertIsNone(request_timing._tally.get())

    def test_nested_eager_tasks_restore_outer_tally(self):
        outer, token = request_timing.begin()
        try:
            memory_envelope.task_timing_started("outer")
            outer_task = request_timing._tally.get()
            with request_timing.boundary("cache"):
                pass
            memory_envelope.task_timing_started("inner")
            inner_task = request_timing._tally.get()
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            memory_envelope.task_timing_finished("inner", "inner")
            self.assertIs(request_timing._tally.get(), outer_task)
            self.assertEqual(inner_task["boundary_calls"]["db"], 1)
            self.assertNotIn("db", outer_task["boundary_calls"])
            memory_envelope.task_timing_finished("outer", "outer")
            self.assertIs(request_timing._tally.get(), outer)
        finally:
            request_timing.end(token)

    @override_settings(PERF_LOG_SLOW_TASK_MS=1000)
    def test_fast_task_is_silent(self):
        with patch.object(memory_envelope.logger, "info") as log:
            memory_envelope.task_timing_started("fast")
            memory_envelope.task_timing_finished("fast", "fast")
        log.assert_not_called()

    def test_cache_wait_is_exclusive_and_cpu_is_not_subtracted(self):
        with (
            patch.object(memory_envelope.time, "perf_counter", side_effect=[0, 0.01, 0.04, 0.2]),
            patch.object(memory_envelope.time, "thread_time", side_effect=[0, 0.001]),
        ):
            memory_envelope.task_timing_started("cache-wait")
            with request_timing.boundary("cache"):
                pass
            with self.assertLogs("app.memory_envelope", level="INFO") as captured:
                memory_envelope.task_timing_finished("cache-wait", "import")
        self.assertIn("cache_ms=30.0", captured.output[0])
        self.assertIn("unclassified_ms=170.0", captured.output[0])
        self.assertIn("cpu_ms=1.0", captured.output[0])

    @override_settings(PERF_LOG_ENABLED=False)
    def test_disabled_task_timing_leaves_no_context(self):
        memory_envelope.task_timing_started("disabled")
        self.assertEqual(memory_envelope._task_timings.get(), ())

    def test_reporting_failure_still_resets_context_and_wrappers(self):
        original_wrappers = len(connection.execute_wrappers)
        memory_envelope.task_timing_started("fail-report")
        with patch.object(memory_envelope.logger, "info", side_effect=RuntimeError), self.assertRaises(RuntimeError):
            memory_envelope.task_timing_finished("fail-report", "task")
        self.assertEqual(len(connection.execute_wrappers), original_wrappers)
        self.assertIsNone(request_timing._tally.get())
        self.assertEqual(memory_envelope._task_timings.get(), ())

    def test_task_signal_runs_when_memory_instrumentation_is_disabled(self):
        from config.celery import app

        @app.task(name="performance-diagnostic-test")
        def failing_task():
            with request_timing.boundary("cache"):
                pass
            raise ValueError("intentional test failure")

        with override_settings(MEMORY_HIGH_WATER_ENABLED=False):
            memory_envelope.connect_celery_signals()
            with self.assertLogs("app.memory_envelope", level="INFO") as captured:
                with self.assertRaises(ValueError):
                    failing_task.apply(throw=False).get()
        self.assertTrue(any("slow_task" in line and "state=FAILURE" in line for line in captured.output))
        self.assertEqual(memory_envelope._task_timings.get(), ())
        self.assertIsNone(request_timing._tally.get())
