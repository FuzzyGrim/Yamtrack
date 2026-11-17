"""Recurring maintenance tasks must be bounded and leave a trail."""

from datetime import timedelta
from unittest import mock, skipUnless

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from app.models import (
    Item,
    MediaTypes,
    MetadataBackfillField,
    MetadataBackfillState,
    Sources,
)
from app.tasks_interactive import reconcile_statistics_sync_task
from integrations.tasks._state_sync import sweep_watched_state_deliveries


class PeriodicTaskBoundsTests(TestCase):
    """A wedged run has to be killed, and a stalled one has to be visible."""

    def test_time_limits_are_set_and_ordered(self):
        for task in (reconcile_statistics_sync_task, sweep_watched_state_deliveries):
            self.assertTrue(0 < task.soft_time_limit < task.time_limit <= 300)

    def test_reconcile_logs_start_and_finish(self):
        with (
            mock.patch("app.statistics_sync.reconcile", return_value=2),
            self.assertLogs("app.tasks_interactive", "INFO") as logs,
        ):
            reconcile_statistics_sync_task()
        text = "\n".join(logs.output)
        self.assertIn("reconcile_statistics_sync start", text)
        self.assertIn("reconcile_statistics_sync finish queued=2", text)

    def test_sweep_logs_start_and_finish(self):
        with self.assertLogs("integrations.tasks._state_sync", "INFO") as logs:
            sweep_watched_state_deliveries()
        text = "\n".join(logs.output)
        self.assertIn("sweep_watched_state start", text)
        self.assertIn("sweep_watched_state finish swept=0", text)


class IdleBackfillTickTests(TestCase):
    """A recurring backfill with nothing to do costs a bounded, small amount.

    Production (46 hours, 2026-11-15) had six recurring tasks spend seconds per
    tick finding little or nothing. Each test here pins the shape that fixed one
    of them: a bounded question first, never a walk over the whole library.
    """

    def _movie(self, n, **fields):
        return Item.objects.create(
            media_id=f"58{n}",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title=f"Movie {n}",
            **fields,
        )

    def _due_state(self, item, field, age):
        MetadataBackfillState.objects.create(
            item=item,
            field=field,
            fail_count=3,
            next_retry_at=timezone.now() - age,
        )

    def test_watch_provider_retries_read_a_window_and_retire_satisfied_rows(self):
        from app import tasks_providers

        # Providers arrived through another path while the state still says
        # "retry due": the old query re-read every one of these on every tick.
        satisfied = [
            self._movie(n, watch_providers={"US": [{"name": "S"}]}) for n in range(30)
        ]
        for n, item in enumerate(satisfied):
            self._due_state(
                item, MetadataBackfillField.WATCH_PROVIDERS, timedelta(days=60 - n)
            )
        missing = self._movie(99, watch_providers={})
        self._due_state(missing, MetadataBackfillField.WATCH_PROVIDERS, timedelta(hours=1))

        queued = []
        with (
            mock.patch.object(
                tasks_providers,
                "enqueue_provider_backfill_items",
                side_effect=lambda ids, countdown: queued.append(ids) or len(ids),
            ),
            CaptureQueriesContext(connection) as ctx,
        ):
            ticks = 0
            while not queued and ticks < 10:
                tasks_providers.enqueue_due_provider_backfill_retries(batch_size=5)
                ticks += 1

        self.assertEqual(queued, [[missing.id]])
        # 30 satisfied rows, a window of 20: two ticks retire them, the third
        # queues the item that is really missing.
        self.assertEqual(ticks, 2)
        for query in ctx.captured_queries:
            sql = query["sql"]
            self.assertFalse(
                "app_metadatabackfillstate" in sql and 'JOIN "app_item"' in sql,
                "the due-retry question must not read an item row per state row",
            )
        self.assertFalse(
            MetadataBackfillState.objects.filter(
                field=MetadataBackfillField.WATCH_PROVIDERS,
                item__in=satisfied,
                next_retry_at__lte=timezone.now(),
            ).exists()
        )

    def test_external_id_retries_retire_rows_whose_item_has_the_id(self):
        from app import tasks_external_ids

        has_id = self._movie(1, provider_external_ids={"imdb_id": "tt1"})
        self._due_state(has_id, MetadataBackfillField.EXTERNAL_IDS, timedelta(days=9))
        lacks_id = self._movie(2, provider_external_ids={})
        self._due_state(lacks_id, MetadataBackfillField.EXTERNAL_IDS, timedelta(days=1))

        with mock.patch.object(
            tasks_external_ids, "enqueue_external_ids_backfill_items", return_value=1
        ) as enqueue:
            tasks_external_ids.enqueue_due_external_ids_backfill_retries(batch_size=5)

        enqueue.assert_called_once_with([lacks_id.id], countdown=10)
        state = MetadataBackfillState.objects.get(
            item=has_id, field=MetadataBackfillField.EXTERNAL_IDS
        )
        self.assertGreater(state.next_retry_at, timezone.now() + timedelta(days=13))

    def test_metadata_backfill_standing_back_does_not_count_the_library(self):
        from app import tasks

        self._movie(1)
        with (
            mock.patch.object(tasks, "interactive_request_active", return_value=True),
            CaptureQueriesContext(connection) as ctx,
        ):
            result = tasks.backfill_item_metadata_task(batch_size=10)

        self.assertTrue(result["skipped"])
        # The language-repair pick runs first; nothing else may.
        self.assertLessEqual(len(ctx.captured_queries), 2)

    @skipUnless(connection.vendor == "sqlite", "counts SQLite VM steps")
    def test_metadata_backfill_idle_tick_does_not_walk_unrelated_items(self):
        """Fetched, finished items outside every bucket cost the idle tick nothing.

        The release, Discover and status picks ordered by fetch time, so SQLite
        walked the fetch-time index over every fetched item looking for ten
        matches. A long-lived library is mostly episodes and tracks that no
        bucket wants.
        """
        from app import tasks

        long_ago = timezone.now() - timedelta(days=400)

        def finished(media_type, n, **fields):
            return Item(
                media_id=str(7000 + n),
                source=Sources.MANUAL.value,
                media_type=media_type,
                title=f"{media_type} {n}",
                metadata_fetched_at=long_ago + timedelta(minutes=n),
                release_datetime=long_ago - timedelta(hours=n),
                **fields,
            )

        # A spread of finished items, so planner statistics see several types.
        Item.objects.bulk_create(
            finished(media_type, n)
            for media_type in (
                MediaTypes.MUSIC.value,
                MediaTypes.BOOK.value,
                MediaTypes.COMIC.value,
                MediaTypes.BOARDGAME.value,
                MediaTypes.PODCAST.value,
            )
            for n in range(40)
        )

        def add_episodes(start, count):
            Item.objects.bulk_create(
                finished(
                    MediaTypes.EPISODE.value,
                    n,
                    season_number=n // 10 % 4 + 1,
                    episode_number=n % 10 + 1,
                )
                for n in range(start, start + count)
            )
            # Production keeps planner statistics (the daily optimize task);
            # without them SQLite never picks the fetch-time walk.
            with connection.cursor() as cursor:
                cursor.execute("ANALYZE")

        def idle_tick_steps():
            steps = [0]

            def tick():
                steps[0] += 1
                return 0

            connection.ensure_connection()
            connection.connection.set_progress_handler(tick, 100)
            try:
                result = tasks.backfill_item_metadata_task(batch_size=10)
            finally:
                connection.connection.set_progress_handler(None, 0)
            self.assertEqual(result["success_count"], 0)
            return steps[0]

        add_episodes(0, 200)
        small = idle_tick_steps()
        add_episodes(200, 3000)
        large = idle_tick_steps()
        # Fifteen times the items; the tick may not grow with them.
        self.assertLess(large, small * 2 + 50, (small, large))

    def test_credits_drain_takes_one_item_while_someone_is_using_the_app(self):
        from app import backfill_queue, tasks_credits
        from app.task_cooperation import DEFERRED_RETRY_SECONDS

        items = [self._movie(n) for n in range(10)]
        MetadataBackfillState.objects.filter(
            field=MetadataBackfillField.CREDITS
        ).delete()
        backfill_queue.clear(
            tasks_credits.CREDITS_BACKFILL_ITEMS_QUEUE_KEY,
            tasks_credits.CREDITS_BACKFILL_ITEMS_SCHEDULED_KEY,
        )
        drain = tasks_credits.populate_credits_backfill_queue
        with mock.patch.object(drain, "apply_async"):
            tasks_credits.enqueue_credits_backfill_items([item.id for item in items])
        with (
            mock.patch.object(
                tasks_credits, "interactive_request_active", return_value=True
            ),
            mock.patch(
                "app.task_cooperation.interactive_request_active", return_value=True
            ),
            mock.patch.object(tasks_credits, "_clear_items_metadata_cache") as clear,
            mock.patch.object(
                tasks_credits,
                "_fetch_item_metadata",
                return_value={"cast": [], "crew": [], "studios_full": []},
            ),
            mock.patch("app.credits.sync_item_credits_from_metadata"),
            mock.patch.object(drain, "apply_async") as next_drain,
        ):
            result = drain()

        self.assertEqual(result["updated"], 1)
        # One item loaded and its cache cleared, not all ten.
        self.assertEqual(len(clear.call_args.args[0]), 1)
        self.assertEqual(
            backfill_queue.depth(tasks_credits.CREDITS_BACKFILL_ITEMS_QUEUE_KEY), 9
        )
        self.assertEqual(next_drain.call_args.kwargs["countdown"], DEFERRED_RETRY_SECONDS)
