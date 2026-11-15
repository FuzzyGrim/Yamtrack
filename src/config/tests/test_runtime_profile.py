"""Tests for host resource detection and tier derivation (issue #521)."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest import mock

from django.test import SimpleTestCase

from config import runtime_profile
from config.runtime_profile import (
    GIB,
    TIER_CONSTRAINED,
    TIER_MINIMAL,
    TIER_STANDARD,
    ResourceProfile,
    by_tier,
    celery_queue_plan,
    detect_profile,
    gunicorn_threads,
    web_concurrency,
)


def write_cgroup_v2(tmp_path, memory_max=None, cpu_max=None):
    """Return patch targets for a synthetic cgroup v2 hierarchy."""
    patches = {}
    if memory_max is not None:
        path = tmp_path / "memory.max"
        path.write_text(str(memory_max))
        patches["_CGROUP_V2_MEMORY_MAX"] = path
    if cpu_max is not None:
        path = tmp_path / "cpu.max"
        path.write_text(cpu_max)
        patches["_CGROUP_V2_CPU_MAX"] = path
    return patches


def write_meminfo(tmp_path, *, mem_total_kb, swap_total_kb, mem_available_kb=None):
    """Write a minimal /proc/meminfo and return its path."""
    available = mem_available_kb if mem_available_kb is not None else mem_total_kb // 2
    path = tmp_path / "meminfo"
    path.write_text(
        f"MemTotal:       {mem_total_kb} kB\n"
        f"MemFree:        {available} kB\n"
        f"MemAvailable:   {available} kB\n"
        f"SwapTotal:      {swap_total_kb} kB\n",
    )
    return path


class MemoryDetectionTests(SimpleTestCase):
    """Reading the memory ceiling from cgroups and /proc/meminfo."""

    def test_takes_the_smaller_of_cgroup_limit_and_memtotal(self):
        """A small container limit on a big host must win, and vice versa."""
        with self.subTest("cgroup binds"):
            patches = write_cgroup_v2(self.tmp_v2, memory_max=1 * GIB)
            meminfo = write_meminfo(
                self.tmp_meminfo,
                mem_total_kb=16 * 1024 * 1024,
                swap_total_kb=0,
            )
            with mock.patch.multiple(
                runtime_profile,
                _PROC_MEMINFO=meminfo,
                **patches,
            ):
                self.assertEqual(runtime_profile.detect_memory_bytes(), 1 * GIB)

        with self.subTest("memtotal binds"):
            patches = write_cgroup_v2(self.tmp_v2b, memory_max=32 * GIB)
            meminfo = write_meminfo(
                self.tmp_meminfo2,
                mem_total_kb=2 * 1024 * 1024,
                swap_total_kb=0,
            )
            with mock.patch.multiple(
                runtime_profile,
                _PROC_MEMINFO=meminfo,
                **patches,
            ):
                self.assertEqual(
                    runtime_profile.detect_memory_bytes(),
                    2 * 1024 * 1024 * 1024,
                )

    def test_cgroup_v2_max_keyword_is_not_a_limit(self):
        """Cgroup v2 spells "no limit" as the literal string "max"."""
        path = self.tmp_v2 / "memory.max"
        path.write_text("max")
        meminfo = write_meminfo(
            self.tmp_meminfo,
            mem_total_kb=8 * 1024 * 1024,
            swap_total_kb=0,
        )
        with mock.patch.multiple(
            runtime_profile,
            _CGROUP_V2_MEMORY_MAX=path,
            _CGROUP_V1_MEMORY_MAX=self.tmp_v2 / "absent-v1-memory.limit_in_bytes",
            _PROC_MEMINFO=meminfo,
        ):
            self.assertEqual(runtime_profile.detect_memory_bytes(), 8 * GIB)

    def test_cgroup_v1_unlimited_sentinel_is_not_a_limit(self):
        """Cgroup v1 reports a near-2**63 page-aligned value for "unlimited"."""
        path = self.tmp_v2 / "memory.limit_in_bytes"
        path.write_text("9223372036854771712")
        meminfo = write_meminfo(
            self.tmp_meminfo,
            mem_total_kb=8 * 1024 * 1024,
            swap_total_kb=0,
        )
        with mock.patch.multiple(
            runtime_profile,
            _CGROUP_V2_MEMORY_MAX=self.tmp_v2 / "absent-v2-memory.max",
            _CGROUP_V1_MEMORY_MAX=path,
            _PROC_MEMINFO=meminfo,
        ):
            self.assertEqual(runtime_profile.detect_memory_bytes(), 8 * GIB)

    def test_unreadable_probes_yield_none_not_an_exception(self):
        """Every probe must degrade rather than stop the container booting."""
        missing = self.tmp_v2 / "definitely-absent"
        with mock.patch.multiple(
            runtime_profile,
            _CGROUP_V2_MEMORY_MAX=missing,
            _CGROUP_V1_MEMORY_MAX=missing,
            _PROC_MEMINFO=missing,
        ):
            self.assertIsNone(runtime_profile.detect_memory_bytes())
            self.assertIsNone(runtime_profile.detect_swap_bytes())

    def setUp(self):
        """Create per-test scratch directories for synthetic sysfs trees."""
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = runtime_profile.Path(self._tmp.name)
        self.tmp_v2 = base / "v2"
        self.tmp_v2b = base / "v2b"
        self.tmp_meminfo = base / "mi"
        self.tmp_meminfo2 = base / "mi2"
        for path in (self.tmp_v2, self.tmp_v2b, self.tmp_meminfo, self.tmp_meminfo2):
            path.mkdir(parents=True)


class CpuDetectionTests(SimpleTestCase):
    """Reading the effective CPU count, honouring cgroup quotas."""

    def setUp(self):
        """Create a scratch directory for synthetic cgroup files."""
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = runtime_profile.Path(self._tmp.name)

    def test_cgroup_v2_quota_is_converted_to_cores(self):
        """ "200000 100000" is a two-core quota."""
        path = self.base / "cpu.max"
        path.write_text("200000 100000")
        with mock.patch.object(runtime_profile, "_CGROUP_V2_CPU_MAX", path):
            self.assertEqual(runtime_profile.detect_cpus(), 2)

    def test_cgroup_v1_quota_is_converted_to_cores(self):
        """cpu.cfs_quota_us / cpu.cfs_period_us is the v1 spelling."""
        quota = self.base / "cpu.cfs_quota_us"
        period = self.base / "cpu.cfs_period_us"
        quota.write_text("400000")
        period.write_text("100000")
        missing = self.base / "absent"
        with mock.patch.multiple(
            runtime_profile,
            _CGROUP_V2_CPU_MAX=missing,
            _CGROUP_V1_CPU_QUOTA=quota,
            _CGROUP_V1_CPU_PERIOD=period,
        ):
            self.assertEqual(runtime_profile.detect_cpus(), 4)

    def test_no_quota_falls_back_to_the_scheduler_affinity(self):
        """With no cgroup quota, use the CPUs this process may actually run on."""
        missing = self.base / "absent"
        with mock.patch.multiple(
            runtime_profile,
            _CGROUP_V2_CPU_MAX=missing,
            _CGROUP_V1_CPU_QUOTA=missing,
            _CGROUP_V1_CPU_PERIOD=missing,
        ):
            self.assertGreaterEqual(runtime_profile.detect_cpus(), 1)


class TierDerivationTests(SimpleTestCase):
    """Turning raw numbers into a tier."""

    def _profile(self, *, memory_gib, swap_gib, cpus, env=None):
        with (
            mock.patch.object(
                runtime_profile,
                "detect_memory_bytes",
                return_value=None if memory_gib is None else int(memory_gib * GIB),
            ),
            mock.patch.object(
                runtime_profile,
                "detect_swap_bytes",
                return_value=None if swap_gib is None else int(swap_gib * GIB),
            ),
            mock.patch.object(
                runtime_profile,
                "detect_available_memory_bytes",
                return_value=None,
            ),
            mock.patch.object(runtime_profile, "detect_cpus", return_value=cpus),
            mock.patch.dict(runtime_profile.os.environ, env or {}, clear=False),
        ):
            return detect_profile()

    def test_generous_host_is_standard(self):
        """8 GB with swap and four cores needs no degradation."""
        profile = self._profile(memory_gib=8, swap_gib=4, cpus=4)
        self.assertEqual(profile.tier, TIER_STANDARD)
        self.assertFalse(profile.is_constrained)

    def test_two_gib_with_swap_is_constrained(self):
        """Under 3 GiB is constrained even when swap can absorb spikes."""
        profile = self._profile(memory_gib=2, swap_gib=4, cpus=4)
        self.assertEqual(profile.tier, TIER_CONSTRAINED)

    def test_two_gib_without_swap_is_promoted_to_minimal(self):
        """The issue #521 configuration: swapless hosts get one tier stricter."""
        profile = self._profile(memory_gib=2, swap_gib=0, cpus=2)
        self.assertEqual(profile.tier, TIER_MINIMAL)
        self.assertIn("no_swap", profile.notes)

    def test_low_cpu_promotes_a_small_host(self):
        """Two cores can't feed the standard process set on a smallish host."""
        profile = self._profile(memory_gib=3.5, swap_gib=4, cpus=2)
        self.assertEqual(profile.tier, TIER_CONSTRAINED)
        self.assertIn("low_cpu", profile.notes)

    def test_undetectable_memory_defaults_to_standard(self):
        """Never degrade on ignorance - a big host must not be halved silently."""
        profile = self._profile(memory_gib=None, swap_gib=None, cpus=4)
        self.assertEqual(profile.tier, TIER_STANDARD)
        self.assertIn("memory_undetected", profile.notes)

    def test_swapless_host_with_undetectable_memory_is_not_promoted(self):
        """Promotion needs a real memory reading to be meaningful."""
        profile = self._profile(memory_gib=None, swap_gib=0, cpus=4)
        self.assertEqual(profile.tier, TIER_STANDARD)
        self.assertNotIn("no_swap", profile.notes)

    def test_a_large_swapless_host_is_not_de_rated(self):
        """Running Docker without swap is common; 16 GB doesn't need de-rating."""
        profile = self._profile(memory_gib=16, swap_gib=0, cpus=8)
        self.assertEqual(profile.tier, TIER_STANDARD)
        self.assertNotIn("no_swap", profile.notes)

    def test_a_midsized_swapless_host_is_de_rated(self):
        """Below the ceiling, no swap still means less headroom than it looks."""
        profile = self._profile(memory_gib=4, swap_gib=0, cpus=8)
        self.assertEqual(profile.tier, TIER_CONSTRAINED)
        self.assertIn("no_swap", profile.notes)

    def test_explicit_override_wins_over_detection(self):
        """An operator's FLOPPY_RESOURCE_TIER is final."""
        profile = self._profile(
            memory_gib=64,
            swap_gib=8,
            cpus=32,
            env={"FLOPPY_RESOURCE_TIER": "minimal"},
        )
        self.assertEqual(profile.tier, TIER_MINIMAL)
        self.assertTrue(profile.overridden)

    def test_legacy_yamtrack_prefix_is_honoured(self):
        """Pre-rename installs keep working."""
        profile = self._profile(
            memory_gib=64,
            swap_gib=8,
            cpus=32,
            env={"YAMTRACK_RESOURCE_TIER": "constrained"},
        )
        self.assertEqual(profile.tier, TIER_CONSTRAINED)

    def test_floppy_prefix_beats_the_legacy_one(self):
        """When both are set, the current name wins."""
        profile = self._profile(
            memory_gib=64,
            swap_gib=8,
            cpus=32,
            env={
                "FLOPPY_RESOURCE_TIER": "minimal",
                "YAMTRACK_RESOURCE_TIER": "constrained",
            },
        )
        self.assertEqual(profile.tier, TIER_MINIMAL)

    def test_nonsense_override_is_ignored_and_noted(self):
        """A typo must not silently pick a tier."""
        profile = self._profile(
            memory_gib=8,
            swap_gib=8,
            cpus=8,
            env={"FLOPPY_RESOURCE_TIER": "tiny"},
        )
        self.assertEqual(profile.tier, TIER_STANDARD)
        self.assertFalse(profile.overridden)
        self.assertTrue(any("tiny" in note for note in profile.notes))


class SizingTests(SimpleTestCase):
    """Turning a tier into process counts and queue assignments."""

    def _profile(self, tier):
        return ResourceProfile(
            tier=tier,
            memory_bytes=2 * GIB,
            available_bytes=None,
            swap_bytes=0,
            cpus=2,
            overridden=True,
        )

    def test_by_tier_selects_per_tier(self):
        """by_tier reads the tier of whichever profile it is given."""
        for tier, expected in (
            (TIER_MINIMAL, "a"),
            (TIER_CONSTRAINED, "b"),
            (TIER_STANDARD, "c"),
        ):
            with self.subTest(tier=tier):
                self.assertEqual(by_tier("a", "b", "c", self._profile(tier)), expected)

    def test_minimal_host_gets_one_worker_and_two_threads(self):
        """Each gunicorn worker holds a resident Django import."""
        profile = self._profile(TIER_MINIMAL)
        with mock.patch.dict(runtime_profile.os.environ, {}, clear=True):
            self.assertEqual(web_concurrency(profile), 1)
            self.assertEqual(gunicorn_threads(profile), 2)

    def test_standard_host_uses_one_threaded_web_worker(self):
        """Threads retain I/O concurrency without another Django process."""
        profile = ResourceProfile(
            tier=TIER_STANDARD,
            memory_bytes=16 * GIB,
            available_bytes=None,
            swap_bytes=4 * GIB,
            cpus=8,
            overridden=True,
        )
        with mock.patch.dict(runtime_profile.os.environ, {}, clear=True):
            self.assertEqual(web_concurrency(profile), 1)
            self.assertEqual(gunicorn_threads(profile), 4)

    def test_explicit_env_overrides_tier_sizing(self):
        """WEB_CONCURRENCY and GUNICORN_THREADS keep working as documented."""
        profile = self._profile(TIER_MINIMAL)
        env = {"WEB_CONCURRENCY": "4", "GUNICORN_THREADS": "8"}
        with mock.patch.dict(runtime_profile.os.environ, env, clear=True):
            self.assertEqual(web_concurrency(profile), 4)
            self.assertEqual(gunicorn_threads(profile), 8)

    def test_unparseable_env_falls_back_to_the_tier_default(self):
        """A malformed override must not crash gunicorn's config import."""
        profile = self._profile(TIER_MINIMAL)
        with mock.patch.dict(
            runtime_profile.os.environ,
            {"WEB_CONCURRENCY": "lots"},
            clear=True,
        ):
            self.assertEqual(web_concurrency(profile), 1)

    def test_queue_plan_collapses_gradually(self):
        """Interactive isolation is the last thing given up, not the first."""
        standard = celery_queue_plan(self._profile(TIER_STANDARD))
        self.assertEqual(standard["queues"], "celery,discover")
        self.assertEqual(standard["start_interactive"], "true")
        self.assertEqual(standard["start_discover"], "false")
        self.assertEqual(standard["role"], "background")

        constrained = celery_queue_plan(self._profile(TIER_CONSTRAINED))
        self.assertEqual(constrained["queues"], "celery,discover")
        self.assertEqual(constrained["start_interactive"], "true")
        self.assertEqual(constrained["start_discover"], "false")
        self.assertEqual(constrained["role"], "background")

        minimal = celery_queue_plan(self._profile(TIER_MINIMAL))
        self.assertEqual(minimal["queues"], "celery,interactive,discover")
        self.assertEqual(minimal["start_interactive"], "false")
        self.assertEqual(minimal["start_discover"], "false")
        self.assertEqual(minimal["role"], "combined")

    def test_no_tier_starts_a_separate_discover_worker(self):
        """Discover rides the background worker; its own worker is never started."""
        for tier in (TIER_MINIMAL, TIER_CONSTRAINED, TIER_STANDARD, "unrecognised"):
            with self.subTest(tier=tier):
                plan = celery_queue_plan(self._profile(tier))
                self.assertEqual(plan["start_discover"], "false")
                self.assertIn("discover", plan["queues"].split(","))

    def test_every_queue_is_served_on_every_tier(self):
        """No tier may leave a queue with no consumer, or tasks vanish."""
        for tier in (TIER_MINIMAL, TIER_CONSTRAINED, TIER_STANDARD):
            with self.subTest(tier=tier):
                plan = celery_queue_plan(self._profile(tier))
                served = set(plan["queues"].split(","))
                if plan["start_interactive"] == "true":
                    served.add("interactive")
                if plan["start_discover"] == "true":
                    served.add("discover")
                self.assertEqual(served, {"celery", "interactive", "discover"})

    def test_emit_env_logs_the_full_worker_topology(self):
        """Startup output must expose the isolated interactive worker."""
        profile = self._profile(TIER_STANDARD)
        stdout = StringIO()
        stderr = StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            runtime_profile.emit_env(profile)

        self.assertIn("background=on(celery,discover)", stderr.getvalue())
        self.assertIn("interactive=on(interactive)", stderr.getvalue())
        self.assertIn("discover=off(merged)", stderr.getvalue())
        self.assertIn(
            "export FLOPPY_START_INTERACTIVE_WORKER='true'", stdout.getvalue()
        )


class SizingSourceTests(SimpleTestCase):
    """Telling an operator's choice apart from the profile's own."""

    def _profile(self, tier=TIER_STANDARD):
        return ResourceProfile(
            tier=tier,
            memory_bytes=16 * GIB,
            available_bytes=None,
            swap_bytes=8 * GIB,
            cpus=8,
            overridden=False,
        )

    def test_defaults_ignore_the_environment(self):
        """The tier's own answer must stay readable behind an override."""
        profile = self._profile()
        with mock.patch.dict(
            runtime_profile.os.environ,
            {"WEB_CONCURRENCY": "4", "GUNICORN_THREADS": "16"},
            clear=True,
        ):
            self.assertEqual(runtime_profile.web_concurrency_default(profile), 1)
            self.assertEqual(runtime_profile.gunicorn_threads_default(profile), 4)
            self.assertEqual(web_concurrency(profile), 4)
            self.assertEqual(gunicorn_threads(profile), 16)

    def test_unset_and_empty_are_both_automatic(self):
        """An empty string is how compose passes through an unset variable."""
        for environment in ({}, {"WEB_CONCURRENCY": ""}):
            with (
                self.subTest(environment=environment),
                mock.patch.dict(runtime_profile.os.environ, environment, clear=True),
            ):
                self.assertEqual(
                    runtime_profile.sizing_source("WEB_CONCURRENCY"), "auto"
                )

    def test_a_number_is_an_override_and_a_word_is_invalid(self):
        """A value that cannot be parsed is ignored, so it must be reported."""
        cases = {"2": "override", "two": "invalid"}
        for value, expected in cases.items():
            with (
                self.subTest(value=value),
                mock.patch.dict(
                    runtime_profile.os.environ,
                    {"WEB_CONCURRENCY": value},
                    clear=True,
                ),
            ):
                self.assertEqual(
                    runtime_profile.sizing_source("WEB_CONCURRENCY"),
                    expected,
                )

    def test_a_recorded_source_wins_over_an_inherited_value(self):
        """Supervisord's children inherit the value emit_env wrote itself.

        Without the recorded marker every supervised process would report its
        own inherited WEB_CONCURRENCY as an operator override.
        """
        with mock.patch.dict(
            runtime_profile.os.environ,
            {"WEB_CONCURRENCY": "1", "FLOPPY_WEB_CONCURRENCY_SOURCE": "auto"},
            clear=True,
        ):
            self.assertEqual(runtime_profile.sizing_source("WEB_CONCURRENCY"), "auto")

    def test_report_flags_an_override_above_the_profile(self):
        """Only an override that costs a process is worth warning about."""
        profile = self._profile()
        with mock.patch.dict(
            runtime_profile.os.environ,
            {"WEB_CONCURRENCY": "2"},
            clear=True,
        ):
            report = runtime_profile.sizing_report(profile)
            warning = runtime_profile.web_concurrency_warning(profile)

        self.assertTrue(report["web_concurrency_over_profile"])
        self.assertEqual(report["web_concurrency_source"], "override")
        self.assertIn("WEB_CONCURRENCY=2", warning)

    def test_report_is_quiet_when_nothing_is_overridden(self):
        """A default deployment must produce no warning at all."""
        profile = self._profile()
        with mock.patch.dict(runtime_profile.os.environ, {}, clear=True):
            report = runtime_profile.sizing_report(profile)
            warning = runtime_profile.web_concurrency_warning(profile)

        self.assertFalse(report["web_concurrency_over_profile"])
        self.assertEqual(warning, "")

    def test_expected_programs_follow_the_queue_plan(self):
        """A worker that never starts must not be listed as resident."""
        profile = self._profile()
        with mock.patch.dict(runtime_profile.os.environ, {}, clear=True):
            report = runtime_profile.sizing_report(profile)

        self.assertIn("gunicorn", report["expected_programs"])
        self.assertEqual(
            "celery-discover" in report["expected_programs"],
            report["start_discover_worker"],
        )

    def test_emit_env_exports_and_warns_about_an_override(self):
        """The boot log is the only surface that reaches a running install."""
        profile = self._profile()
        stdout = StringIO()
        stderr = StringIO()
        with (
            mock.patch.dict(
                runtime_profile.os.environ,
                {"WEB_CONCURRENCY": "2"},
                clear=True,
            ),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            runtime_profile.emit_env(profile)

        self.assertIn(
            "export FLOPPY_WEB_CONCURRENCY_SOURCE='override'", stdout.getvalue()
        )
        self.assertIn("export FLOPPY_GUNICORN_THREADS_SOURCE='auto'", stdout.getvalue())
        self.assertIn("WEB_CONCURRENCY=2 is set explicitly", stderr.getvalue())
