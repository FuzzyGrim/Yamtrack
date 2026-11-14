"""The background Statistics sync (#1272).

The page never builds or waits: changes mark days (or the aggregate), one
per-user sync rebuilds them and republishes the ranges, and a reconciler finds
any work a lost message left behind. These tests pin those promises.
"""

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import OperationalError
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from app import statistics_cache, statistics_day_builder, statistics_sync
from app.models import (
    Anime,
    Item,
    MediaTypes,
    Movie,
    Sources,
    StatisticsDirtyDay,
    StatisticsSnapshot,
    StatisticsSyncState,
    Status,
)
from app.statistics_day_cache import _day_cache_key
from integrations.models import ImportRun

NON_EAGER = override_settings(CELERY_TASK_ALWAYS_EAGER=False, TESTING=False)
SYNC_TASK = "app.tasks_interactive.statistics_sync_task.apply_async"


class StatisticsSyncTestCase(TestCase):
    offsets = (0, 3, 40, 400)

    def setUp(self):
        cache.clear()
        # Non-eager tests must not inspect the developer's live broker queues.
        self.enterContext(
            patch("app.statistics_sync.higher_priority_task_waiting", return_value=False)
        )
        self.user = get_user_model().objects.create_user(
            username=f"sync-{self.id()[-40:]}", password="secret123"
        )
        self.movies = []
        for index, offset in enumerate(self.offsets):
            item = Item.objects.create(
                media_id=f"sync-{index}",
                source=Sources.MANUAL.value,
                media_type=MediaTypes.MOVIE.value,
                title=f"Sync movie {index}",
                runtime_minutes=100,
            )
            self.movies.append(
                Movie.objects.create(
                    user=self.user,
                    item=item,
                    status=Status.COMPLETED.value,
                    end_date=timezone.now() - timedelta(days=offset),
                    score=7,
                )
            )
        self.today = timezone.localdate()
        # Creating the fixture marked its days; start each test from clean.
        StatisticsDirtyDay.objects.filter(user_id=self.user.id).delete()

    def tearDown(self):
        cache.clear()

    def day(self, offset):
        return self.today - timedelta(days=offset)

    def full_sync(self):
        result = statistics_sync.run_sync(self.user.id)
        self.assertEqual(result["status"], "done")
        return result

    def mark(self, days):
        with self.captureOnCommitCallbacks(execute=False):
            statistics_sync.mark_days(self.user.id, days, reason="test")

    def state(self):
        return StatisticsSyncState.objects.get(user_id=self.user.id)

    def snapshot_generation(self, range_name):
        return StatisticsSnapshot.objects.get(
            user_id=self.user.id, range_name=range_name
        ).generation


class SchemaUpgradeTests(StatisticsSyncTestCase):
    def test_a_schema_bump_rebuilds_every_range_of_a_synced_user(self):
        """Heavy ranges such as All Time must not wait for the next full sync."""
        self.full_sync()
        StatisticsSnapshot.objects.filter(user_id=self.user.id).update(
            schema_version=statistics_sync.SNAPSHOT_SCHEMA_VERSION - 1
        )

        result = self.full_sync()

        self.assertIn("All Time", result["published"])
        self.assertEqual(
            set(
                StatisticsSnapshot.objects.filter(user_id=self.user.id).values_list(
                    "schema_version", flat=True
                )
            ),
            {statistics_sync.SNAPSHOT_SCHEMA_VERSION},
        )


class MarkingTests(StatisticsSyncTestCase):
    @patch("app.statistics_sync._aggregate_range", return_value={})
    @patch("app.statistics_sync._build_days", return_value=0)
    def test_inline_range_preserves_pending_full_sweep(self, *_mocks):
        with self.captureOnCommitCallbacks(execute=False):
            statistics_sync.mark_aggregate(self.user.id, full_sweep=True)
        statistics_sync.refresh_range_inline(self.user.id, "Today")
        self.assertIsNotNone(self.state().full_sweep_requested_at)

    @NON_EAGER
    @patch(SYNC_TASK)
    @patch("app.statistics_sync._aggregate_range", return_value={})
    def test_inline_refresh_defers_to_active_sync(self, aggregate, enqueue):
        lease = timezone.now() + timedelta(minutes=4)
        StatisticsSyncState.objects.filter(user=self.user).update(
            lease_expires_at=lease
        )
        before = self.state().generation
        with self.captureOnCommitCallbacks(execute=True):
            result = statistics_sync.refresh_range_inline(self.user.id, "Today")
        self.assertIsNone(result)
        aggregate.assert_not_called()
        self.assertEqual(self.state().lease_expires_at, lease)
        self.assertGreater(self.state().generation, before)
        self.assertIsNotNone(self.state().full_sweep_requested_at)
        enqueue.assert_called_once()

    @NON_EAGER
    @patch(SYNC_TASK)
    def test_inline_refresh_that_hits_a_lock_leaves_durable_work(self, enqueue):
        before = self.state().generation
        with (
            patch.object(
                statistics_sync,
                "_claim_lease",
                side_effect=OperationalError("database is locked"),
            ),
            self.captureOnCommitCallbacks(execute=True),
        ):
            result = statistics_sync.refresh_range_inline(self.user.id, "Today")
        self.assertIsNone(result)
        self.assertGreater(self.state().generation, before)
        self.assertIsNotNone(self.state().full_sweep_requested_at)
        enqueue.assert_called_once()

    def test_repeated_marks_keep_one_row_per_day_and_rotate_its_token(self):
        self.mark([self.day(3)])
        first = StatisticsDirtyDay.objects.get(user_id=self.user.id, day=self.day(3))
        self.mark([self.day(3), self.day(40)])

        rows = dict(
            StatisticsDirtyDay.objects.filter(user_id=self.user.id).values_list(
                "day", "token"
            )
        )
        self.assertEqual(set(rows), {self.day(3), self.day(40)})
        self.assertNotEqual(rows[self.day(3)], first.token)

    def test_marks_survive_a_cache_flush(self):
        self.mark([self.day(3)])
        generation = self.state().generation
        cache.clear()

        self.assertTrue(
            StatisticsDirtyDay.objects.filter(user_id=self.user.id).exists()
        )
        self.assertEqual(self.state().generation, generation)

    def test_marking_drops_the_day_payload(self):
        self.full_sync()
        key = _day_cache_key(self.user.id, self.day(40))
        self.assertIsNotNone(cache.get(key))

        self.mark([self.day(40)])

        self.assertIsNone(cache.get(key))


class BulkImportStatisticsTests(StatisticsSyncTestCase):
    @NON_EAGER
    @patch(SYNC_TASK)
    @patch("app.statistics_sync.interactive_request_active", return_value=True)
    def test_reconciler_does_not_publish_work_that_browser_guard_will_defer(self, _active, enqueue):
        self.mark([self.day(3)])
        self.assertEqual(statistics_sync.reconcile(), 0)
        enqueue.assert_not_called()
        self.assertTrue(StatisticsDirtyDay.objects.filter(user=self.user).exists())
        self.assertTrue(statistics_sync.ensure_sync(self.user.id, urgent=True))
        enqueue.assert_called_once()

    def test_verified_noop_import_restores_only_its_own_mark(self):
        self.full_sync()
        before = self.state().generation
        with statistics_sync.coalesce_import_changes(self.user.id) as changes:
            changes["unchanged"] = True
        self.assertEqual(self.state().generation, before)
        self.assertIsNone(self.state().full_sweep_requested_at)

    def test_scoped_import_spends_the_sweep_it_requested_up_front(self):
        self.full_sync()
        with statistics_sync.coalesce_import_changes(self.user.id) as changes:
            self.mark([self.day(3)])
            changes["scoped"] = True
        self.assertIsNone(self.state().full_sweep_requested_at)
        self.assertTrue(StatisticsDirtyDay.objects.filter(user=self.user).exists())

    def test_scoped_import_leaves_a_sweep_that_was_already_pending(self):
        statistics_sync.mark_aggregate(self.user.id, full_sweep=True)
        pending = self.state().full_sweep_requested_at
        with statistics_sync.coalesce_import_changes(self.user.id) as changes:
            changes["scoped"] = True
        self.assertEqual(self.state().full_sweep_requested_at, pending)

    def test_noop_import_cannot_erase_concurrent_tracking(self):
        self.full_sync()
        before = self.state().generation
        with statistics_sync.coalesce_import_changes(self.user.id) as changes:
            # A different process/context does not inherit import coalescing.
            token = statistics_sync._import_changes.set({})
            try:
                self.mark([self.day(3)])
            finally:
                statistics_sync._import_changes.reset(token)
            changes["unchanged"] = True
        self.assertEqual(self.state().generation, before + 2)
        self.assertIsNotNone(self.state().full_sweep_requested_at)
        self.assertTrue(StatisticsDirtyDay.objects.filter(user=self.user).exists())

    def test_import_coalesces_generation_but_retains_dirty_tokens(self):
        before = self.state().generation
        with statistics_sync.coalesce_import_changes(self.user.id):
            self.mark([self.day(3)])
            first = StatisticsDirtyDay.objects.get(user=self.user, day=self.day(3))
            self.mark([self.day(3), self.day(40)])
            statistics_sync.mark_aggregate(self.user.id, reason="item_metadata")
            self.assertEqual(self.state().generation, before + 1)
            self.assertNotEqual(
                StatisticsDirtyDay.objects.get(user=self.user, day=self.day(3)).token,
                first.token,
            )
        self.mark([self.day(3)])
        self.assertEqual(self.state().generation, before + 2)

    def test_failed_import_restores_ordinary_marking_and_keeps_full_sweep(self):
        before = self.state().generation
        with self.assertRaisesRegex(ValueError, "import failed"):
            with statistics_sync.coalesce_import_changes(self.user.id):
                raise ValueError("import failed")
        self.assertIsNotNone(self.state().full_sweep_requested_at)
        statistics_sync.mark_aggregate(self.user.id)
        self.assertEqual(self.state().generation, before + 2)

    @NON_EAGER
    @patch(SYNC_TASK)
    def test_running_import_defers_sync_and_reconcile_until_completion(self, enqueue):
        run = ImportRun.objects.create(user=self.user, source="trakt")
        self.mark([self.day(3)])
        self.assertFalse(statistics_sync.ensure_sync(self.user.id))
        self.assertEqual(statistics_sync.reconcile(), 0)
        result = statistics_sync.run_sync(self.user.id, budget_seconds=10)
        self.assertEqual(result["status"], "deferred")
        self.assertIsNone(self.state().lease_expires_at)
        enqueue.assert_not_called()
        run.status = ImportRun.Status.COMPLETED
        run.save(update_fields=["status"])
        self.assertEqual(statistics_sync.reconcile(), 1)
        enqueue.assert_called_once()

    @override_settings(CELERY_TASK_TIME_LIMIT=60)
    def test_abandoned_import_and_self_rescheduling_source_do_not_defer(self):
        run = ImportRun.objects.create(user=self.user, source="trakt")
        ImportRun.objects.filter(pk=run.pk).update(
            started_at=timezone.now() - timedelta(hours=1)
        )
        ImportRun.objects.create(user=self.user, source="lastfm")
        self.assertFalse(statistics_sync._bulk_import_active(self.user.id))

    @override_settings(CELERY_TASK_TIME_LIMIT=60)
    def test_resumed_import_fresh_lease_defers_despite_original_start_timestamp(self):
        """Restart retains provenance while proving its current writer is alive."""
        run = ImportRun.objects.create(user=self.user, source="trakt", phase="persist")
        ImportRun.objects.filter(pk=run.pk).update(
            started_at=timezone.now() - timedelta(days=1), lease_expires_at=timezone.now() + timedelta(minutes=10),
        )
        self.assertTrue(statistics_sync._bulk_import_active(self.user.id))

    def test_sqlite_defers_other_users_but_postgres_does_not(self):
        other = get_user_model().objects.create_user(username="other-import")
        ImportRun.objects.create(user=other, source="mdblist")
        with patch.object(statistics_sync.connection, "vendor", "sqlite"):
            self.assertTrue(statistics_sync._bulk_import_active(self.user.id))
        with patch.object(statistics_sync.connection, "vendor", "postgresql"):
            self.assertFalse(statistics_sync._bulk_import_active(self.user.id))

    def test_full_sweep_repairs_cached_day_after_signal_free_write(self):
        self.full_sync()
        old = statistics_sync.refresh_range_inline(self.user.id, "Today")
        Movie.objects.filter(pk=self.movies[0].pk).update(score=9)
        with statistics_sync.coalesce_import_changes(self.user.id):
            pass
        rebuilt = statistics_sync.run_sync(self.user.id, only_ranges=["Today"])
        self.assertEqual(rebuilt["status"], "done")
        self.assertEqual(old["score_distribution"]["average_score"], 7)
        self.assertEqual(rebuilt["published"]["Today"]["score_distribution"]["average_score"], 9)

    def test_cleared_rating_is_removed_from_cached_statistics(self):
        item = Item.objects.create(
            media_id="unrated-anime", source=Sources.MANUAL.value,
            media_type=MediaTypes.ANIME.value, title="Anime",
        )
        anime = Anime.objects.create(
            user=self.user, item=item, status=Status.COMPLETED.value,
            end_date=timezone.now(), score=8,
        )
        self.full_sync()
        with self.captureOnCommitCallbacks(execute=True):
            anime.score = None
            anime.save(update_fields=["score"])
        rebuilt = statistics_sync.run_sync(self.user.id, only_ranges=["Today"])
        distribution = rebuilt["published"]["Today"]["score_distribution"]
        self.assertEqual(distribution["total_scored"], 1)
        self.assertEqual(distribution["average_score"], 7)

    def test_budget_yields_between_days_without_rebuilding_completed_day(self):
        self.full_sync()
        self.mark([self.day(3), self.day(40)])
        clock = 0.0
        real_build = statistics_day_builder.build_stats_for_day
        built = []

        def slow_day(*args, **kwargs):
            nonlocal clock
            data = real_build(*args, **kwargs)
            built.append(args[1])
            clock += 2.0
            return data

        with (
            patch("app.statistics_sync.time.monotonic", side_effect=lambda: clock),
            patch("app.statistics_day_builder.build_stats_for_day", side_effect=slow_day),
        ):
            result = statistics_sync.run_sync(self.user.id, budget_seconds=1)
        self.assertEqual(result["status"], "continued")
        self.assertEqual(len(built), 1)
        self.assertTrue(StatisticsDirtyDay.objects.filter(user=self.user).exists())
        result = self.full_sync()
        self.assertFalse(StatisticsDirtyDay.objects.filter(user=self.user).exists())

    def test_full_sweep_continuations_retain_completed_days(self):
        self.full_sync()
        with statistics_sync.coalesce_import_changes(self.user.id):
            pass
        real_build = statistics_day_builder.build_stats_for_day
        clock = 0.0
        built = []

        def slow_day(*args, **kwargs):
            nonlocal clock
            data = real_build(*args, **kwargs)
            built.append(args[1])
            clock += 2.0
            return data

        with (
            patch("app.statistics_sync.time.monotonic", side_effect=lambda: clock),
            patch("app.statistics_day_builder.build_stats_for_day", side_effect=slow_day),
        ):
            for _ in range(len(self.offsets)):
                result = statistics_sync.run_sync(self.user.id, budget_seconds=1)
                self.assertEqual(result["status"], "continued")
        self.assertEqual(len(built), len(self.offsets))
        self.assertEqual(len(set(built)), len(built))
        self.assertFalse(StatisticsDirtyDay.objects.filter(user=self.user).exists())
        self.full_sync()


class SyncTests(StatisticsSyncTestCase):
    @NON_EAGER
    @patch("app.statistics_sync.interactive_request_active", return_value=True)
    def test_background_sync_defers_before_claiming_lease(self, _active):
        self.mark([self.day(3)])
        result = statistics_sync.run_sync(self.user.id, budget_seconds=10)
        self.assertEqual(result["status"], "deferred")
        self.assertIsNone(self.state().lease_expires_at)
        self.assertTrue(StatisticsDirtyDay.objects.filter(user=self.user).exists())

        with patch("app.statistics_sync.interactive_request_active", return_value=False):
            resumed = statistics_sync.run_sync(self.user.id, budget_seconds=60)
        self.assertEqual(resumed["status"], "done")
        self.assertFalse(StatisticsDirtyDay.objects.filter(user=self.user).exists())

    @NON_EAGER
    @patch(SYNC_TASK)
    def test_background_sync_yields_mid_slice_without_losing_dirty_days(self, _enqueue):
        self.mark([self.day(3)])
        active = False
        real_build = statistics_day_builder.build_stats_for_day

        def build_then_mark_active(*args, **kwargs):
            nonlocal active
            result = real_build(*args, **kwargs)
            active = True
            return result

        with (
            patch(
                "app.statistics_sync.interactive_request_active",
                side_effect=lambda: active,
            ),
            patch(
                "app.statistics_day_builder.build_stats_for_day",
                side_effect=build_then_mark_active,
            ),
        ):
            result = statistics_sync.run_sync(self.user.id, budget_seconds=60)

        self.assertEqual(result["status"], "continued")
        self.assertTrue(
            StatisticsDirtyDay.objects.filter(user=self.user, day=self.day(3)).exists()
        )
        with patch("app.statistics_sync.interactive_request_active", return_value=False):
            resumed = statistics_sync.run_sync(self.user.id, budget_seconds=60)
        self.assertEqual(resumed["status"], "done")
        self.assertFalse(StatisticsDirtyDay.objects.filter(user=self.user).exists())

    def test_a_full_sync_publishes_every_range_and_clears_dirty_days(self):
        self.mark([self.day(3)])
        result = self.full_sync()

        self.assertEqual(
            set(result["published"]), set(statistics_cache.PREDEFINED_RANGES)
        )
        self.assertFalse(StatisticsDirtyDay.objects.filter(user_id=self.user.id).exists())
        state = self.state()
        self.assertEqual(state.hot_synced_generation, state.generation)
        self.assertEqual(state.heavy_synced_generation, state.generation)
        self.assertEqual(state.synced_day, self.today)
        self.assertIsNone(state.full_sweep_requested_at)
        self.assertIsNone(state.lease_expires_at)

    def test_a_day_marked_again_during_its_rebuild_stays_dirty(self):
        self.full_sync()
        self.mark([self.day(40)])
        real_build = statistics_sync._build_days

        def build_then_remark(user, days, deadline, tokens, **kwargs):
            hints = real_build(user, days, deadline, tokens, **kwargs)
            # Would have landed while the rebuild was running.
            self.mark([self.day(40)])
            return hints

        with patch.object(statistics_sync, "_build_days", build_then_remark):
            statistics_sync.run_sync(self.user.id)

        self.assertTrue(
            StatisticsDirtyDay.objects.filter(
                user_id=self.user.id, day=self.day(40)
            ).exists()
        )

    def test_a_lock_error_ends_the_sync_quietly_and_leaves_the_work_queued(self):
        self.mark([self.day(3)])

        with (
            patch.object(
                statistics_sync,
                "_build_days",
                side_effect=OperationalError("database is locked"),
            ),
            patch(SYNC_TASK) as enqueue,
            self.assertLogs("app.statistics_sync", level="WARNING") as logs,
        ):
            result = statistics_sync.run_sync(self.user.id)

        self.assertEqual(result["status"], "locked")
        self.assertIn("stats_sync_locked", logs.output[0])
        enqueue.assert_not_called()
        self.assertIsNone(self.state().lease_expires_at)
        self.assertEqual(self.state().last_error, "")
        self.assertTrue(
            StatisticsDirtyDay.objects.filter(user=self.user, day=self.day(3)).exists()
        )
        self.assertIn(self.user.id, statistics_sync.users_needing_sync())

    def test_a_lock_while_claiming_the_lease_ends_the_sync_quietly(self):
        self.mark([self.day(3)])

        with (
            patch.object(
                statistics_sync,
                "_claim_lease",
                side_effect=OperationalError("database is locked"),
            ),
            self.assertLogs("app.statistics_sync", level="WARNING") as logs,
        ):
            result = statistics_sync.run_sync(self.user.id)

        self.assertEqual(result["status"], "locked")
        self.assertIn("stats_sync_locked", logs.output[0])
        self.assertIsNone(self.state().lease_expires_at)
        self.assertTrue(
            StatisticsDirtyDay.objects.filter(user=self.user, day=self.day(3)).exists()
        )

    def test_a_lock_while_releasing_the_lease_does_not_fail_the_sync(self):
        self.mark([self.day(3)])

        with (
            patch.object(
                statistics_sync,
                "_release_lease",
                side_effect=OperationalError("database is locked"),
            ),
            self.assertLogs("app.statistics_sync", level="WARNING") as logs,
        ):
            result = statistics_sync.run_sync(self.user.id)

        self.assertEqual(result["status"], "done")
        self.assertTrue(any("stats_sync_release_locked" in line for line in logs.output))

    def test_other_database_errors_still_fail_the_sync(self):
        self.mark([self.day(3)])

        with (
            patch.object(
                statistics_sync,
                "_build_days",
                side_effect=OperationalError("no such table"),
            ),
            self.assertRaises(OperationalError),
        ):
            statistics_sync.run_sync(self.user.id)

        self.assertIn("no such table", self.state().last_error)

    def test_an_aggregate_only_change_rebuilds_no_old_day(self):
        self.full_sync()
        with self.captureOnCommitCallbacks(execute=False):
            statistics_sync.mark_aggregate(self.user.id, reason="score")

        with patch(
            "app.statistics_day_builder.build_stats_for_day",
            wraps=statistics_cache.build_stats_for_day,
        ) as build:
            statistics_sync.run_sync(self.user.id)

        built = {call.args[1] for call in build.call_args_list}
        self.assertNotIn(self.day(40), built)
        self.assertNotIn(self.day(400), built)
        self.assertEqual(
            self.snapshot_generation("Today"), self.state().generation
        )

    @NON_EAGER
    @patch(SYNC_TASK)
    def test_a_change_during_the_sync_publishes_and_queues_another_pass(self, enqueue):
        self.full_sync()
        self.mark([self.day(0)])
        generation = self.state().generation
        real_aggregate = statistics_sync._aggregate_range
        marked = []

        def aggregate_then_change(user, range_name, hints, deadline=None, **kwargs):
            if not marked:
                marked.append(range_name)
                self.mark([self.day(3)])
            return real_aggregate(user, range_name, hints, deadline, **kwargs)

        with patch.object(statistics_sync, "_aggregate_range", aggregate_then_change):
            result = statistics_sync.run_sync(self.user.id)

        # Never aborted: every hot range was published at the generation it read.
        self.assertEqual(result["status"], "done")
        self.assertEqual(self.snapshot_generation("Today"), generation)
        enqueue.assert_called_once()

        # The follow-up converges.
        statistics_sync.run_sync(self.user.id)
        self.assertEqual(self.snapshot_generation("Today"), self.state().generation)
        self.assertFalse(StatisticsDirtyDay.objects.filter(user_id=self.user.id).exists())

    def test_heavy_ranges_wait_for_changes_to_settle(self):
        self.full_sync()
        self.mark([self.day(0)])
        generation = self.state().generation

        statistics_sync.run_sync(self.user.id)

        self.assertEqual(self.snapshot_generation("Last 7 Days"), generation)
        self.assertLess(self.snapshot_generation("All Time"), generation)
        # A heavy default range settles like the others.
        self.assertIn(self.user.statistics_default_range, statistics_sync.HEAVY_RANGES)
        self.assertLess(
            self.snapshot_generation(self.user.statistics_default_range), generation
        )

        later = timezone.now() + timedelta(
            seconds=statistics_sync._heavy_settle_seconds() + 1
        )
        with patch("django.utils.timezone.now", return_value=later):
            statistics_sync.run_sync(self.user.id)
        self.assertEqual(self.snapshot_generation("All Time"), generation)

    def test_a_continued_full_sweep_still_finishes_the_heavy_ranges(self):
        """A manual refresh that runs out of time must not strand All Time."""
        self.full_sync()
        with self.captureOnCommitCallbacks(execute=False):
            statistics_sync.request_manual_refresh(self.user, "Today")
        generation = self.state().generation
        real_check = statistics_sync._check_deadline
        calls = []

        def run_out_after_the_hot_ranges(deadline):
            calls.append(deadline)
            # Days slice + six hot ranges fit; the first heavy range does not.
            if len(calls) > 7:
                raise statistics_sync._OutOfTimeError
            real_check(None)

        with patch.object(
            statistics_sync, "_check_deadline", run_out_after_the_hot_ranges
        ):
            first = statistics_sync.run_sync(self.user.id, budget_seconds=60)
        self.assertEqual(first["status"], "continued")
        self.assertLess(self.snapshot_generation("All Time"), generation)

        statistics_sync.run_sync(self.user.id)

        self.assertEqual(self.snapshot_generation("All Time"), generation)
        self.assertIsNone(self.state().full_sweep_requested_at)

    def test_a_held_lease_turns_a_second_sync_away(self):
        StatisticsSyncState.objects.update_or_create(
            user_id=self.user.id,
            defaults={"lease_expires_at": timezone.now() + timedelta(minutes=5)},
        )

        self.assertEqual(statistics_sync.run_sync(self.user.id)["status"], "busy")

    @NON_EAGER
    @patch(SYNC_TASK)
    def test_running_out_of_time_queues_a_continuation(self, enqueue):
        with patch.object(statistics_sync, "_check_deadline") as check:
            check.side_effect = [None, statistics_sync._OutOfTimeError]
            result = statistics_sync.run_sync(self.user.id, budget_seconds=1)

        self.assertEqual(result["status"], "continued")
        self.assertIsNone(self.state().lease_expires_at)
        enqueue.assert_called_once()


class SlowHostContinuationTests(StatisticsSyncTestCase):
    """Days found by the stale-score scan must not be rebuilt from scratch forever."""

    def test_scan_found_days_make_progress_across_continuations(self):
        self.full_sync()
        StatisticsSyncState.objects.filter(user_id=self.user.id).update(
            synced_day=self.day(5)
        )
        stale = {self.day(offset) for offset in (3, 4, 5, 6, 7, 8)}
        built = []
        from app.statistics_day_builder import build_stats_for_day as real_build

        clock = {"now": 0.0}

        def slow_build(user_id, day, **kwargs):
            built.append(day)
            clock["now"] += 4  # each day costs 4 s against a 10 s budget
            return real_build(user_id, day, **kwargs)

        with (
            patch(
                "app.statistics_cache._collect_stale_reading_score_days",
                # Rebuilding does not always clear the flag (a score the day
                # payload cannot show), so the scan keeps naming the same days.
                return_value=stale,
            ),
            patch(
                "app.statistics_day_builder.build_stats_for_day", side_effect=slow_build
            ),
            patch(
                "app.statistics_sync.time.monotonic",
                side_effect=lambda: clock["now"],
            ),
            patch("app.statistics_sync.ensure_sync"),
        ):
            statuses = [
                statistics_sync.run_sync(self.user.id, budget_seconds=10)["status"]
                for _ in range(6)
            ]

        self.assertEqual(statuses[0], "continued")
        self.assertEqual(statuses[-1], "done")
        stale_built = [day for day in built if day in stale]
        self.assertEqual(len(stale_built), len(set(stale_built)))


class SliceBoundTests(StatisticsSyncTestCase):
    """A sync gives way to webhooks and stops starting ranges it cannot finish."""

    def cost_all_ranges(self, seconds):
        for range_name in (*statistics_sync.HOT_RANGES, *statistics_sync.HEAVY_RANGES):
            cache.set(statistics_sync._range_cost_key(self.user.id, range_name), seconds)

    @patch("app.statistics_sync.higher_priority_task_waiting", return_value=True)
    def test_sync_yields_to_a_queued_webhook(self, waiting):
        self.mark([self.day(3)])
        result = statistics_sync.run_sync(self.user.id, budget_seconds=60)

        self.assertEqual(result["status"], "continued")
        self.assertEqual(result["published"], {})
        waiting.assert_called_with("interactive", 0)
        self.assertTrue(StatisticsDirtyDay.objects.filter(user=self.user).exists())

        with patch(
            "app.statistics_sync.higher_priority_task_waiting", return_value=False
        ):
            resumed = statistics_sync.run_sync(self.user.id, budget_seconds=60)
        self.assertEqual(resumed["status"], "done")

    def test_a_range_too_dear_for_what_is_left_waits_for_the_next_task(self):
        self.cost_all_ranges(1000)

        first = statistics_sync.run_sync(self.user.id, budget_seconds=60)

        # The first range always runs; the rest do not fit in what is left.
        self.assertEqual(first["status"], "continued")
        self.assertEqual(list(first["published"]), ["Today"])

        rounds = 0
        status = first["status"]
        while status == "continued" and rounds < 20:
            result = statistics_sync.run_sync(self.user.id, budget_seconds=60)
            status = result["status"]
            rounds += 1
            self.assertLessEqual(len(result["published"]), 1)
        self.assertEqual(status, "done")

    def test_every_range_records_what_it_cost(self):
        self.full_sync()

        for range_name in statistics_sync.HOT_RANGES:
            self.assertIsNotNone(
                cache.get(statistics_sync._range_cost_key(self.user.id, range_name))
            )

    def test_a_range_summary_says_how_much_was_day_building(self):
        with self.assertLogs("app.statistics_sync", level="INFO") as logs:
            self.full_sync()

        line = next(row for row in logs.output if "stats_range_summary" in row)
        self.assertIn("missing_days=", line)
        self.assertIn("days_ms=", line)

    def test_a_sync_far_past_its_budget_names_its_slowest_range(self):
        with (
            patch.object(statistics_sync, "OVERRUN_WARNING_SECONDS", -1000),
            self.assertLogs("app.statistics_sync", level="WARNING") as logs,
        ):
            statistics_sync.run_sync(self.user.id, budget_seconds=60)

        line = next(row for row in logs.output if "stats_sync_overrun" in row)
        self.assertIn("slowest_range=", line)
        self.assertNotIn("slowest_range=-", line)


class HighlightHistoryDayTests(StatisticsSyncTestCase):
    def test_a_day_built_for_a_highlight_is_kept_for_the_next_range(self):
        from app import history_cache_day_builder
        from app.statistics_highlights import _get_history_day_payload

        day = self.day(3)
        cache.clear()
        with patch.object(
            history_cache_day_builder,
            "build_history_day",
            wraps=history_cache_day_builder.build_history_day,
        ) as build:
            first = _get_history_day_payload(self.user, day)
            second = _get_history_day_payload(self.user, day)

        self.assertEqual(build.call_count, 1)
        self.assertEqual(len(first["entries"]), len(second["entries"]))
        self.assertGreater(len(second["entries"]), 0)


class ReconcilerTests(StatisticsSyncTestCase):
    @NON_EAGER
    # The recovered sync must finish in one task; the default 10s budget
    # leaves it "continued" on a slower machine.
    @override_settings(STATISTICS_SYNC_TASK_BUDGET_SECONDS=600)
    def test_a_dropped_sync_message_is_recovered(self):
        """#1272: the message that should rebuild the page never arrives."""
        with patch(SYNC_TASK) as dropped, self.captureOnCommitCallbacks(execute=True):
            statistics_sync.mark_days(self.user.id, [self.day(0)], reason="scrobble")
        dropped.assert_called_once()  # published, then lost
        # A message waits for the task to start; only after the pending window
        # expires does the reconciler treat it as lost and publish a retry.
        cache.delete(statistics_sync._pending_key(self.user.id))

        with patch(SYNC_TASK) as enqueue:
            queued = statistics_sync.reconcile()
        self.assertEqual(queued, 1)
        self.assertEqual(enqueue.call_args.kwargs["args"], [self.user.id])

        statistics_sync.sync_task_body(self.user.id)
        self.assertEqual(statistics_sync.users_needing_sync(), [])
        self.assertIn(
            "hours_per_media_type",
            statistics_cache.get_statistics_data(self.user, None, None, "Today"),
        )

    @NON_EAGER
    @patch(SYNC_TASK)
    def test_reconcile_queues_once_while_a_message_waits_behind_the_worker(self, enqueue):
        self.mark([self.day(3)])

        self.assertEqual(statistics_sync.reconcile(), 1)
        self.assertEqual(statistics_sync.reconcile(), 0)
        self.assertEqual(statistics_sync.reconcile(), 0)
        self.assertEqual(enqueue.call_count, 1)
        self.assertIsNotNone(cache.get(statistics_sync._pending_key(self.user.id)))

    @NON_EAGER
    @patch(SYNC_TASK)
    def test_starting_the_task_clears_the_pending_marker(self, _enqueue):
        self.mark([self.day(3)])
        statistics_sync.reconcile()

        with patch.object(
            statistics_sync, "run_sync", return_value={"status": "done", "published": {}}
        ):
            statistics_sync.sync_task_body(self.user.id)

        self.assertIsNone(cache.get(statistics_sync._pending_key(self.user.id)))
        # Durable work is still unfinished, so the next tick can queue again.
        self.assertEqual(statistics_sync.reconcile(), 1)

    @NON_EAGER
    @patch(SYNC_TASK)
    def test_reconcile_retries_after_the_pending_window_expires(self, enqueue):
        self.mark([self.day(3)])
        self.assertEqual(statistics_sync.reconcile(), 1)

        cache.delete(statistics_sync._pending_key(self.user.id))

        self.assertEqual(statistics_sync.reconcile(), 1)
        self.assertEqual(enqueue.call_count, 2)

    def test_a_live_lease_is_left_alone_and_an_expired_one_is_retried(self):
        self.mark([self.day(3)])
        state = self.state()
        state.lease_expires_at = timezone.now() + timedelta(minutes=1)
        state.save()
        self.assertEqual(statistics_sync.users_needing_sync(), [])

        state.lease_expires_at = timezone.now() - timedelta(seconds=1)
        state.save()
        self.assertEqual(statistics_sync.users_needing_sync(), [self.user.id])

    def test_midnight_rollover_is_found_without_any_change(self):
        self.full_sync()
        self.assertEqual(statistics_sync.users_needing_sync(), [])
        generation = self.state().generation

        tomorrow = timezone.now() + timedelta(days=1)
        with patch("django.utils.timezone.now", return_value=tomorrow):
            self.assertEqual(statistics_sync.users_needing_sync(), [self.user.id])
            result = self.full_sync()
            self.assertEqual(self.state().generation, generation)
            self.assertEqual(
                set(result["published"]),
                set(statistics_sync.HOT_RANGES + statistics_sync.HEAVY_RANGES),
            )
            # Even All Time contains today's history card and calendar label.
            # An unchanged generation alone cannot make yesterday's payload fresh.
            for payload in result["published"].values():
                self.assertEqual(
                    payload["history_highlights"]["today_day"],
                    timezone.localdate().day,
                )
                self.assertEqual(
                    payload["history_highlights"]["today_month"],
                    timezone.localdate().month,
                )

    def test_users_without_state_are_bootstrapped_for_warming(self):
        StatisticsSyncState.objects.filter(user_id=self.user.id).delete()

        statistics_sync._bootstrap_states()

        self.assertIn(self.user.id, statistics_sync.users_needing_sync())

    def test_reconcile_is_scheduled_every_minute_on_the_default_lane(self):
        from django.conf import settings

        entry = settings.CELERY_BEAT_SCHEDULE["reconcile_statistics_sync"]
        self.assertEqual(entry["task"], "Reconcile statistics sync")
        self.assertLessEqual(entry["schedule"], 60)
        self.assertEqual(entry["options"]["expire_seconds"], entry["schedule"])
        route = settings.CELERY_TASK_ROUTES["Reconcile statistics sync"]
        self.assertNotIn("queue", route)



class ReconcilerSchedulePersistenceTests(TransactionTestCase):
    """The real scheduler may close connections; exercise it in autocommit."""

    def test_beat_updates_existing_reconciler_with_a_message_lifetime(self):
        from celery import Celery
        from django.conf import settings
        from django_celery_beat.models import IntervalSchedule, PeriodicTask
        from django_celery_beat.schedulers import DatabaseScheduler

        self.assertEqual(
            settings.CELERY_BEAT_SCHEDULER,
            "django_celery_beat.schedulers:DatabaseScheduler",
        )
        interval, _ = IntervalSchedule.objects.get_or_create(
            every=60, period=IntervalSchedule.SECONDS
        )
        periodic = PeriodicTask.objects.create(
            name="reconcile_statistics_sync",
            task="Reconcile statistics sync",
            interval=interval,
        )
        tomorrow = timezone.now() + timedelta(days=1)
        for absolute_expiry, enabled in ((None, True), (tomorrow, True), (tomorrow, False)):
            with self.subTest(absolute_expiry=absolute_expiry, enabled=enabled):
                periodic.expires = absolute_expiry
                periodic.expire_seconds = None
                periodic.enabled = enabled
                periodic.save()
                with Celery("reconciler_schedule_test", set_as_current=False) as app:
                    app.conf.update(
                        broker_url="memory://",
                        result_expires=None,
                        beat_schedule={
                            periodic.name: settings.CELERY_BEAT_SCHEDULE[periodic.name],
                        },
                    )
                    scheduler = DatabaseScheduler(app=app)
                    try:
                        periodic.refresh_from_db()
                        self.assertIsNone(periodic.expires)
                        self.assertEqual(periodic.expire_seconds, 60)
                        self.assertEqual(periodic.enabled, enabled)
                        self.assertEqual(
                            PeriodicTask.objects.get(name=periodic.name).pk, periodic.pk
                        )
                        if enabled:
                            self.assertEqual(
                                scheduler.schedule[periodic.name].options["expires"], 60
                            )
                        else:
                            self.assertNotIn(periodic.name, scheduler.schedule)
                    finally:
                        scheduler.close()


class ReadPathTests(StatisticsSyncTestCase):
    @patch("app.statistics_cache.range_needs_top_talent_upgrade", return_value=True)
    @patch("app.statistics_cache.refresh_statistics_cache", return_value=None)
    @patch("app.statistics_cache.get_top_talent_data", return_value={})
    @patch(
        "app.statistics_views.stats_cast_crew.get_featured_repeat_player_with_strip",
        return_value=(None, []),
    )
    @patch("app.statistics_views.stats_cast_crew.get_studio_footprint", return_value={})
    def test_deferred_upgrade_returns_fragments_instead_of_reloading(self, *_mocks):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("update_top_talent_sort"),
            {"sort_by": "plays", "range_name": "Today", "total_library_titles": "4"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["requires_reload"])
        self.assertIn("role_leaders_html", response.json())

    @NON_EAGER
    @patch(SYNC_TASK)
    def test_polling_never_interrupts_a_running_sync(self, enqueue):
        self.full_sync()
        StatisticsSyncState.objects.filter(user_id=self.user.id).update(
            lease_expires_at=timezone.now() + timedelta(minutes=5)
        )
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("cache_status"),
            {"cache_type": "statistics", "range_name": "Last 30 Days"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["any_range_refreshing"])
        self.assertIsNotNone(self.state().lease_expires_at)

    def test_manual_refresh_marks_only_the_ranges_days(self):
        self.full_sync()
        old_key = _day_cache_key(self.user.id, self.day(400))
        self.assertIsNotNone(cache.get(old_key))

        with self.captureOnCommitCallbacks(execute=False):
            statistics_sync.request_manual_refresh(self.user, "Last 7 Days")

        dirty = set(
            StatisticsDirtyDay.objects.filter(user_id=self.user.id).values_list(
                "day", flat=True
            )
        )
        self.assertEqual(dirty, {self.day(offset) for offset in range(7)})
        self.assertIsNotNone(cache.get(old_key))
        self.assertIsNotNone(self.state().full_sweep_requested_at)

    def test_manual_refresh_view_uses_the_sync(self):
        self.client.force_login(self.user)
        with self.captureOnCommitCallbacks(execute=False):
            response = self.client.post(
                reverse("refresh_statistics"), {"range_name": "This Week"}
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            StatisticsDirtyDay.objects.filter(
                user_id=self.user.id, day=self.today
            ).exists()
        )


class InvalidationGapTests(StatisticsSyncTestCase):
    def test_moving_an_entry_marks_the_day_it_left(self):
        self.full_sync()
        movie = self.movies[2]  # 40 days ago
        movie.end_date = timezone.now() - timedelta(days=41)
        with self.captureOnCommitCallbacks(execute=False):
            movie.save()

        dirty = set(
            StatisticsDirtyDay.objects.filter(user_id=self.user.id).values_list(
                "day", flat=True
            )
        )
        self.assertIn(self.day(40), dirty)
        self.assertIn(self.day(41), dirty)

    def test_an_undated_change_keeps_every_day_payload(self):
        self.full_sync()
        keys = [_day_cache_key(self.user.id, self.day(o)) for o in self.offsets]
        generation = self.state().generation
        item = Item.objects.create(
            media_id="planning",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MOVIE.value,
            title="Planned",
        )
        with self.captureOnCommitCallbacks(execute=False):
            Movie.objects.create(user=self.user, item=item, status=Status.PLANNING.value)

        self.assertEqual(len(cache.get_many(keys)), len(keys))
        self.assertGreater(self.state().generation, generation)

    def test_an_import_requests_a_full_rebuild(self):
        self.full_sync()
        with self.captureOnCommitCallbacks(execute=False):
            statistics_cache.invalidate_all_statistics_days(
                self.user.id, reason="media_import"
            )

        self.assertIsNotNone(self.state().full_sweep_requested_at)
        self.assertIsNone(cache.get(_day_cache_key(self.user.id, self.day(40))))
        self.full_sync()
        self.assertIsNotNone(cache.get(_day_cache_key(self.user.id, self.day(40))))

    def test_bulk_written_rows_mark_their_days(self):
        rows = [
            SimpleNamespace(end_date=timezone.now() - timedelta(days=12)),
            SimpleNamespace(end_date=None, start_date=None),
        ]
        with self.captureOnCommitCallbacks(execute=False):
            statistics_sync.mark_rows(self.user.id, rows, reason="bulk")

        self.assertEqual(
            list(
                StatisticsDirtyDay.objects.filter(user_id=self.user.id).values_list(
                    "day", flat=True
                )
            ),
            [self.day(12)],
        )


class SnapshotSerializationTests(StatisticsSyncTestCase):
    def test_payload_round_trips_with_model_rows_and_python_types(self):
        movie = self.movies[0]
        movie.aggregated_score = 9.5
        payload = {
            "top_rated": [movie],
            "reading": {"top_items": [{"media": movie, "units": 3}]},
            "when": timezone.now(),
            "day": date(2026, 1, 2),
            "amount": Decimal("1.5"),
            "pairs": {("movie", 1): 2},
            "tags": ("a", "b"),
        }

        stored = statistics_sync.dehydrate_payload(payload)
        restored = statistics_sync.hydrate_payload(stored)

        self.assertEqual(restored["top_rated"][0].pk, movie.pk)
        self.assertEqual(restored["top_rated"][0].aggregated_score, 9.5)
        self.assertEqual(restored["top_rated"][0].item.title, movie.item.title)
        self.assertEqual(restored["reading"]["top_items"][0]["media"].pk, movie.pk)
        self.assertEqual(restored["when"], payload["when"])
        self.assertEqual(restored["day"], payload["day"])
        self.assertEqual(restored["amount"], payload["amount"])
        self.assertEqual(restored["pairs"], payload["pairs"])
        self.assertEqual(restored["tags"], payload["tags"])

    def test_a_card_for_a_deleted_row_is_dropped(self):
        movie = self.movies[0]
        stored = statistics_sync.dehydrate_payload(
            {"top_items": [{"media": movie, "units": 1}], "top_rated": [movie]}
        )
        movie.delete()

        restored = statistics_sync.hydrate_payload(stored)

        self.assertEqual(restored, {"top_items": [], "top_rated": []})

    def test_a_published_range_rehydrates_from_the_database(self):
        self.full_sync()
        cache.clear()

        entry = statistics_sync.load_snapshot(self.user.id, "All Time")

        self.assertIsNotNone(entry)
        self.assertFalse(statistics_sync.entry_is_stale(entry, user_id=self.user.id))
        self.assertTrue(
            all(
                isinstance(movie, Movie)
                for movie in entry["data"].get("top_rated", [])
            )
        )


class CreditsStatisticsCoalescingTests(StatisticsSyncTestCase):
    """A credits backfill must not rebuild Statistics back to back (#1706)."""

    def setUp(self):
        super().setUp()
        statistics_sync._ensure_state(self.user.id)
        self.before = self.state().generation

    def request(self):
        return statistics_sync.request_credits_statistics_refresh(self.user.id)

    def test_the_first_credits_batch_marks_the_generation(self):
        self.assertTrue(self.request())
        self.assertEqual(self.state().generation, self.before + 1)

    @patch("app.tasks_interactive.mark_credits_statistics_refresh.apply_async")
    def test_later_batches_are_coalesced_and_schedule_one_trailing_mark(self, enqueue):
        self.assertTrue(self.request())
        marked = self.state().generation

        self.assertFalse(self.request())
        self.assertFalse(self.request())

        self.assertEqual(self.state().generation, marked)
        enqueue.assert_called_once_with(args=[self.user.id], countdown=600)

    def test_the_trailing_mark_publishes_the_last_coalesced_credits(self):
        self.request()
        marked = self.state().generation
        cache.delete(statistics_sync._credits_mark_key(self.user.id))

        statistics_sync.mark_credits_statistics_refresh(self.user.id)

        self.assertEqual(self.state().generation, marked + 1)

    def test_the_trailing_mark_does_not_duplicate_a_fresh_mark(self):
        self.request()
        marked = self.state().generation

        statistics_sync.mark_credits_statistics_refresh(self.user.id)

        self.assertEqual(self.state().generation, marked)

    @patch("app.tasks_interactive.mark_credits_statistics_refresh.apply_async")
    def test_credits_batches_coalesce_through_the_backfill_scheduler(self, enqueue):
        from app.models import MetadataBackfillField
        from app.tasks_backfill_state import _schedule_metadata_statistics_refresh

        items = [movie.item for movie in self.movies]
        _schedule_metadata_statistics_refresh(
            items, MetadataBackfillField.CREDITS, "credits_backfill"
        )
        marked = self.state().generation
        _schedule_metadata_statistics_refresh(
            items, MetadataBackfillField.CREDITS, "credits_backfill"
        )

        self.assertEqual(self.state().generation, marked)
        enqueue.assert_called_once()
