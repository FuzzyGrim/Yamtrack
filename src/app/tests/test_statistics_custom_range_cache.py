"""A custom Statistics range is remembered until the next change.

Only the predefined ranges have published snapshots. A custom range used to be
assembled from every day payload on every load (6 to 12 s of web CPU for a
full history at 10k items), and then read every day a second time to find any
missing one. These tests pin the remembered result to the same freshness rules
the snapshots follow: any mark, the local day, preferences that change the
numbers, a missing day and a running import.
"""

import json
from datetime import date, datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from app import cache_utils, statistics_aggregator, statistics_cache, statistics_sync
from app.models import Item, MediaTypes, Movie, Sources, StatisticsSyncState, Status

RANGE_START = date(2024, 3, 1)
RANGE_END = date(2024, 3, 31)

# A remembered custom range reads the generation and the album check, then
# one cached value. Measured 2; a rebuilt range is far above this.
REPEAT_CUSTOM_RANGE_QUERY_BUDGET = 4


def _aware(day, end=False):
    moment = datetime.max.time() if end else datetime.min.time()
    return timezone.make_aware(datetime.combine(day, moment))


def _canonical(data):
    return json.dumps(
        statistics_sync.dehydrate_payload(data), sort_keys=True, default=str
    )


class CustomRangeCacheTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="custom-range", password="x"
        )
        self.start = _aware(RANGE_START)
        self.end = _aware(RANGE_END, end=True)
        self._movie("m1", day=date(2024, 3, 10), score=8)
        # A user's first mark requests a full sweep; in production the sync
        # clears it, and nothing is remembered until it has.
        StatisticsSyncState.objects.filter(user=self.user).update(
            full_sweep_requested_at=None
        )

    def tearDown(self):
        cache.clear()

    def _movie(self, media_id, *, day, score=None):
        item = Item.objects.create(
            media_id=media_id,
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MOVIE.value,
            title=f"Movie {media_id}",
            runtime_minutes=100,
        )
        played = timezone.make_aware(datetime.combine(day, datetime.min.time()))
        played += timedelta(hours=20)
        return Movie.objects.create(
            user=self.user,
            item=item,
            status=Status.COMPLETED.value,
            score=score,
            start_date=played,
            end_date=played,
        )

    def _read(self):
        return statistics_cache.get_statistics_data(self.user, self.start, self.end)

    def _minutes(self):
        return statistics_cache.get_statistics_minutes_by_type(
            self.user, self.start, self.end
        )

    def _counting_aggregate(self):
        return patch.object(
            statistics_cache,
            "_aggregate_statistics_from_days",
            wraps=statistics_aggregator._aggregate_statistics_from_days,
        )

    def test_repeat_load_reuses_the_result_with_bounded_work(self):
        with self._counting_aggregate() as aggregate:
            first = self._read()
            with CaptureQueriesContext(connection) as queries:
                second = self._read()

        self.assertEqual(aggregate.call_count, 1)
        self.assertLessEqual(len(queries), REPEAT_CUSTOM_RANGE_QUERY_BUDGET)
        self.assertEqual(_canonical(first), _canonical(second))
        self.assertEqual(second["media_count"]["movie"], 1)

    def test_remembered_payload_matches_a_fresh_build(self):
        self._read()
        remembered = self._read()
        with patch.object(
            statistics_cache,
            "_custom_range_result",
            side_effect=lambda _kind, _user, _start, _end, build: build([]),
        ):
            fresh = self._read()
        self.assertEqual(_canonical(remembered), _canonical(fresh))

    def test_a_play_logged_afterwards_shows_on_the_next_load(self):
        self.assertEqual(self._read()["media_count"]["movie"], 1)
        self.assertEqual(self._minutes().get("movie"), 100)

        self._movie("m2", day=date(2024, 3, 20))

        self.assertEqual(self._read()["media_count"]["movie"], 2)
        self.assertEqual(self._minutes().get("movie"), 200)

    def test_a_score_change_with_no_day_reaches_the_next_load(self):
        self._read()
        movie = Movie.objects.get(user=self.user)
        movie.score = 3
        movie.save()

        data = self._read()

        self.assertEqual(data["top_rated"][0].score, 3)

    def test_a_new_local_day_rebuilds(self):
        with self._counting_aggregate() as aggregate:
            self._read()
            tomorrow = timezone.localdate() + timedelta(days=1)
            with patch("django.utils.timezone.localdate", return_value=tomorrow):
                self._read()
        self.assertEqual(aggregate.call_count, 2)

    def test_a_generation_that_comes_round_again_does_not_match(self):
        # A restored database can give the counter a value it had before.
        with self._counting_aggregate() as aggregate:
            self._read()
            StatisticsSyncState.objects.filter(user=self.user).update(
                last_marked_at=timezone.now() + timedelta(seconds=1)
            )
            self._read()
        self.assertEqual(aggregate.call_count, 2)

    def test_a_preference_that_changes_the_numbers_rebuilds(self):
        with self._counting_aggregate() as aggregate:
            self._read()
            self.user.rating_scale = "5"
            self.user.save(update_fields=["rating_scale"])
            self._read()
        self.assertEqual(aggregate.call_count, 2)

    def test_a_different_range_is_built_and_replaces_the_last_one(self):
        self._read()
        first_key = cache.get(statistics_cache._custom_range_slot_key("range", self.user.id))
        self.end = _aware(RANGE_END - timedelta(days=1), end=True)
        self._read()
        second_key = cache.get(statistics_cache._custom_range_slot_key("range", self.user.id))

        self.assertNotEqual(first_key, second_key)
        self.assertIsNone(cache.get(first_key))
        self.assertIsNotNone(cache.get(second_key))

    def test_a_range_read_with_a_missing_day_is_not_kept(self):
        self._read()  # builds every day payload (eager mode)
        cache.clear()
        # Only one day payload is present: the rest are missing, as after a
        # cache flush, and the background task has not built them yet.
        statistics_sync._build_days(self.user, [date(2024, 3, 10)], None, {})

        with (
            patch.object(statistics_cache, "_eager_statistics_mode", return_value=False),
            patch("app.tasks.build_statistics_days_task.apply_async") as schedule,
            self._counting_aggregate() as aggregate,
        ):
            self._read()
            self._read()

        self.assertEqual(aggregate.call_count, 2)
        schedule.assert_called_once()

    def test_complete_days_are_kept_and_schedule_nothing(self):
        self._read()  # eager: builds every day of the range
        cache.delete(statistics_cache._custom_range_slot_key("range", self.user.id))
        self.start = _aware(RANGE_START + timedelta(days=1))
        with (
            patch.object(statistics_cache, "_eager_statistics_mode", return_value=False),
            patch("app.tasks.build_statistics_days_task.apply_async") as schedule,
            self._counting_aggregate() as aggregate,
        ):
            self._read()
            self._read()

        self.assertEqual(aggregate.call_count, 1)
        schedule.assert_not_called()

    def test_nothing_is_kept_while_an_import_runs(self):
        with (
            patch.object(statistics_sync, "_bulk_import_active", return_value=True),
            self._counting_aggregate() as aggregate,
        ):
            self._read()
            self._read()
        self.assertEqual(aggregate.call_count, 2)

    def test_nothing_is_kept_while_a_full_sweep_is_pending(self):
        # An import requests a sweep that rebuilds day payloads without a mark.
        self._read()
        StatisticsSyncState.objects.filter(user=self.user).update(
            full_sweep_requested_at=timezone.now()
        )
        cache.delete(statistics_cache._custom_range_slot_key("range", self.user.id))
        self.end = _aware(RANGE_END - timedelta(days=2), end=True)
        with self._counting_aggregate() as aggregate:
            self._read()
            self._read()
        self.assertEqual(aggregate.call_count, 2)

    def test_comparison_minutes_are_remembered(self):
        with patch.object(
            statistics_cache,
            "_aggregate_minutes_per_media_type_from_days",
            wraps=statistics_aggregator._aggregate_minutes_per_media_type_from_days,
        ) as minutes:
            self.assertEqual(self._minutes().get("movie"), 100)
            self.assertEqual(self._minutes().get("movie"), 100)
        self.assertEqual(minutes.call_count, 1)

    def test_a_second_request_waits_for_the_first_build(self):
        token = statistics_cache._custom_range_token(self.user, self.start, self.end)
        key = statistics_cache._custom_range_key("range", self.user.id, token)
        built = statistics_aggregator._aggregate_statistics_from_days(
            self.user,
            statistics_cache._resolve_day_list(self.user, self.start, self.end),
            self.start,
            self.end,
            build_missing=True,
        )
        self.assertTrue(cache_utils.acquire_build_lock(key, 60))

        def first_request_finishes(cache_key, is_ready, wait):
            self.assertEqual(cache_key, key)
            self.assertFalse(is_ready())
            cache.set(key, built)
            cache_utils.release_build_lock(key)
            return is_ready()

        with (
            patch.object(cache_utils, "wait_for_build", side_effect=first_request_finishes),
            self._counting_aggregate() as aggregate,
        ):
            data = self._read()

        aggregate.assert_not_called()
        self.assertEqual(data["media_count"]["movie"], 1)

    def test_a_request_that_waited_in_vain_builds_it_itself(self):
        token = statistics_cache._custom_range_token(self.user, self.start, self.end)
        key = statistics_cache._custom_range_key("range", self.user.id, token)
        self.assertTrue(cache_utils.acquire_build_lock(key, 60))

        with (
            patch.object(cache_utils, "wait_for_build", return_value=False),
            self._counting_aggregate() as aggregate,
        ):
            data = self._read()

        self.assertEqual(aggregate.call_count, 1)
        self.assertEqual(data["media_count"]["movie"], 1)

    def test_predefined_ranges_are_not_stored_as_custom(self):
        statistics_cache.get_statistics_data(
            self.user, None, None, range_name="All Time"
        )
        self.assertIsNone(
            cache.get(statistics_cache._custom_range_slot_key("range", self.user.id))
        )
