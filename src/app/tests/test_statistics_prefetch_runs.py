"""A sync slice prefetches each run of nearby days, not the whole span between them.

A save on a day years back is built in the same slice as today and yesterday.
One prefetch over that slice read every row between the old day and today.
"""

import json
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from app import statistics_day_builder, statistics_sync
from app.models import Item, MediaTypes, Movie, Sources, Status
from app.models.statistics import StatisticsDirtyDay
from app.signals import suppress_media_cache_change_signals
from app.statistics_day_builder import (
    SpanPrefetches,
    _prefetch_runs,
    build_stats_for_day,
)
from app.statistics_day_cache import _day_cache_key


def _comparable(payload):
    payload = dict(payload or {})
    for volatile in ("computed_at", "history_version", "backfill"):
        payload.pop(volatile, None)
    return json.loads(json.dumps(payload, sort_keys=True, default=str))


class PrefetchRunsTests(TestCase):
    def test_days_split_where_the_gap_exceeds_a_month(self):
        d = date(2026, 1, 1)
        days = [
            d,
            d + timedelta(days=31),
            d + timedelta(days=63),
            d + timedelta(days=64),
        ]
        self.assertEqual(
            _prefetch_runs(list(reversed(days))),
            [[days[0], days[1]], [days[2], days[3]]],
        )
        self.assertEqual(_prefetch_runs([]), [])

    def test_runs_are_built_once_and_only_when_used(self):
        user = get_user_model().objects.create_user(username="runs")
        today = timezone.localdate()
        far = today - timedelta(days=1000)
        with patch.object(
            statistics_day_builder, "_build_prefetch_for_range", return_value={}
        ) as build:
            prefetches = SpanPrefetches(user, [far, today, today - timedelta(days=1)])
            self.assertEqual(build.call_count, 0)
            prefetches.for_day(today)
            prefetches.for_day(today - timedelta(days=1))
            self.assertEqual(
                [call.args[1] for call in build.call_args_list],
                [[today - timedelta(days=1), today]],
            )
            prefetches.for_day(far)
            self.assertEqual(build.call_args_list[-1].args[1], [far])
            self.assertIsNone(prefetches.for_day(far - timedelta(days=1)))


class SyncSlicePrefetchTests(TestCase):
    def setUp(self):
        cache.clear()
        self.enterContext(
            patch(
                "app.statistics_sync.higher_priority_task_waiting", return_value=False
            )
        )
        self.user = get_user_model().objects.create_user(username="slice")
        now = timezone.now()
        self.today = timezone.localdate()
        self.old_day = self.today - timedelta(days=1460)
        with suppress_media_cache_change_signals():
            for offset in range(0, 1461, 3):
                Movie.objects.create(
                    user=self.user,
                    item=Item.objects.create(
                        media_id=f"slice-{offset}",
                        source=Sources.MANUAL.value,
                        media_type=MediaTypes.MOVIE.value,
                        title=f"Slice {offset}",
                        runtime_minutes=90,
                    ),
                    status=Status.COMPLETED.value,
                    end_date=now - timedelta(days=offset),
                )
            # A play spanning the old day, so bucketing across a run edge
            # is exercised as well.
            Movie.objects.create(
                user=self.user,
                item=Item.objects.create(
                    media_id="slice-span",
                    source=Sources.MANUAL.value,
                    media_type=MediaTypes.MOVIE.value,
                    title="Span",
                    runtime_minutes=90,
                ),
                status=Status.COMPLETED.value,
                start_date=now - timedelta(days=1462),
                end_date=now - timedelta(days=1458),
            )
        statistics_sync.run_sync(self.user.id)
        StatisticsDirtyDay.objects.filter(user_id=self.user.id).delete()

    def tearDown(self):
        cache.clear()

    def test_old_dirty_day_does_not_read_the_years_between(self):
        with self.captureOnCommitCallbacks(execute=False):
            statistics_sync.mark_days(self.user.id, [self.old_day], reason="test")
        real = statistics_day_builder._build_prefetch_for_range
        spans = []

        def spy(user, day_list):
            spans.append(sorted(day_list))
            return real(user, day_list)

        with (
            patch.object(statistics_day_builder, "_build_prefetch_for_range", spy),
            CaptureQueriesContext(connection) as queries,
        ):
            result = statistics_sync.run_sync(self.user.id)
        self.assertEqual(result["status"], "done")

        self.assertIn([self.old_day], spans)
        self.assertIn([self.today - timedelta(days=1), self.today], spans)
        self.assertTrue(all((s[-1] - s[0]).days <= 31 for s in spans))
        self.assertTrue(queries)
        self.assertFalse(
            StatisticsDirtyDay.objects.filter(user_id=self.user.id).exists()
        )

        for day in (self.old_day, self.today, self.today - timedelta(days=1)):
            cached = cache.get(_day_cache_key(self.user.id, day))
            direct = build_stats_for_day(
                self.user.id, day, user=self.user, defer_cache_write=True
            )
            self.assertEqual(_comparable(cached), _comparable(direct), day)

    def test_split_prefetch_matches_one_span_prefetch(self):
        days = [
            self.old_day,
            self.old_day + timedelta(days=2),
            self.today - timedelta(days=1),
            self.today,
        ]
        one_span = statistics_day_builder._build_prefetch_for_range(self.user, days)
        split = SpanPrefetches(self.user, days)
        for day in days:
            self.assertEqual(
                _comparable(
                    build_stats_for_day(
                        self.user.id,
                        day,
                        user=self.user,
                        prefetch=one_span,
                        defer_cache_write=True,
                    )
                ),
                _comparable(
                    build_stats_for_day(
                        self.user.id,
                        day,
                        user=self.user,
                        prefetch=split.for_day(day),
                        defer_cache_write=True,
                    )
                ),
                day,
            )
