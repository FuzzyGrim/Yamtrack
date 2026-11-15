"""Every tier must bound how large a Celery child can become.

Recycling a child is the only thing that returns fragmented or leaked memory
to the OS. Standard hosts used to have no RSS ceiling, so a child that grew
during one import stayed that size until ``max_tasks_per_child`` recycled it --
on a warm-idle install, potentially days.
"""

import json
import os
import subprocess
import sys

from django.conf import settings
from django.test import SimpleTestCase

# Starting Django in a subprocess takes a second or two; this is a wedge
# detector, not a performance bound.
_SUBPROCESS_TIMEOUT_SECONDS = 120

TIERS = ("minimal", "constrained", "standard")


class WorkerRecyclingTests(SimpleTestCase):
    """Read the settings back per tier, as a worker process would."""

    def _limits(self, tier, role="background"):
        """Return the recycling limits settings resolve to on this tier."""
        script = """
import json

from django.conf import settings

print(json.dumps({
    "max_memory_per_child": settings.CELERY_WORKER_MAX_MEMORY_PER_CHILD,
    "max_tasks_per_child": settings.CELERY_WORKER_MAX_TASKS_PER_CHILD,
    "concurrency": settings.CELERY_WORKER_CONCURRENCY,
}))
"""
        environment = os.environ.copy()
        environment["DJANGO_SETTINGS_MODULE"] = "config.test_settings"
        environment["PYTHONPATH"] = str(settings.BASE_DIR)
        environment["FLOPPY_RESOURCE_TIER"] = tier
        environment["FLOPPY_PROCESS_ROLE"] = role
        result = subprocess.run(  # noqa: S603
            [sys.executable, "-c", script],
            check=True,
            # Bounded on purpose: a wedged interpreter must fail this test,
            # not hang the whole run until the CI job's limit expires.
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
            capture_output=True,
            text=True,
            env=environment,
        )
        return json.loads(result.stdout.splitlines()[-1])

    def test_every_tier_bounds_child_memory(self):
        """No tier may leave a child free to grow without limit."""
        for tier in TIERS:
            with self.subTest(tier=tier):
                limits = self._limits(tier)

                self.assertIsNotNone(limits["max_memory_per_child"])
                self.assertGreater(limits["max_memory_per_child"], 0)

    def test_the_ceiling_leaves_room_for_a_started_child(self):
        """A ceiling near the import cost would recycle a child continuously.

        A freshly started worker child carries roughly 100 MiB of imports, so
        anything close to that would retire it after almost every task and pay
        the import cost again each time.
        """
        started_child_kib = 100 * 1024
        for tier in TIERS:
            with self.subTest(tier=tier):
                self.assertGreater(
                    self._limits(tier)["max_memory_per_child"],
                    started_child_kib * 1.5,
                )

    def test_the_interactive_lane_is_bounded_well_below_the_background_one(self):
        """The background ceiling has to clear an import; this one must not.

        A child that only runs webhooks and short cache refreshes never
        approaches a ceiling sized for a large import, so sharing that number
        means it is never retired and its creep is never returned. Production
        showed an interactive child at 215 MiB after three hours, still under
        the background ceiling and still climbing.
        """
        for tier in TIERS:
            with self.subTest(tier=tier):
                interactive = self._limits(tier, role="interactive")
                background = self._limits(tier, role="background")

                self.assertLess(
                    interactive["max_memory_per_child"],
                    background["max_memory_per_child"],
                )

    def test_the_interactive_ceiling_still_clears_a_started_child(self):
        """A ceiling near the import cost would retire the child continuously.

        The interactive role loads a third of the background worker's
        CELERY_IMPORTS, so its started child is roughly 50 MiB.
        """
        started_interactive_child_kib = 50 * 1024
        for tier in TIERS:
            with self.subTest(tier=tier):
                self.assertGreater(
                    self._limits(tier, role="interactive")["max_memory_per_child"],
                    started_interactive_child_kib * 2,
                )

    def test_smaller_hosts_recycle_sooner(self):
        """The ceiling must rise with the tier, never fall."""
        ceilings = [self._limits(tier)["max_memory_per_child"] for tier in TIERS]

        self.assertEqual(ceilings, sorted(ceilings))


class ChildMemoryMeasureTests(SimpleTestCase):
    """CELERY_WORKER_MAX_MEMORY_PER_CHILD compares the child's anonymous size.

    billiard's own reading is ru_maxrss, a lifetime peak that includes the
    memory-mapped database. Once one task read a wide table, every later task
    ended with the child "over" its ceiling and recycled for page cache.
    """

    def test_pool_children_measure_anonymous_resident_size(self):
        import billiard.pool

        from config import celery as celery_config

        original = billiard.pool.mem_rss
        self.addCleanup(setattr, billiard.pool, "mem_rss", original)
        celery_config._measure_child_memory_without_page_cache()

        self.assertIs(billiard.pool.mem_rss, celery_config._child_anon_rss_kib)

    def test_the_reading_is_in_kib(self):
        from unittest.mock import patch

        from config import celery as celery_config

        with patch.object(
            celery_config, "current_anon_rss_bytes", return_value=150 * 1024 * 1024
        ):
            self.assertEqual(celery_config._child_anon_rss_kib(), 150 * 1024)

    def test_an_unreadable_proc_falls_back_to_billiards_figure(self):
        from unittest.mock import patch

        from billiard import compat

        from config import celery as celery_config

        with (
            patch.object(celery_config, "current_anon_rss_bytes", return_value=None),
            patch.object(compat, "mem_rss", return_value=4321),
        ):
            self.assertEqual(celery_config._child_anon_rss_kib(), 4321)
