from datetime import timedelta
from unittest.mock import patch

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from app.models import TV, Episode, Item, MediaTypes, Season, Sources, Status
from app.signals import suppress_media_change_side_effects

from .base import FloppyApiTestCase
from .helpers import check_statistics_structure


class StatisticsTests(FloppyApiTestCase):
    """Validate statistics endpoint contracts."""

    def test_statistics_unauthenticated_returns_forbidden(self):
        """Statistics endpoint should require authentication."""
        response = self.call_api(
            "get",
            "api_statistics",
        )

        self.assertEqual(response.status_code, 403)

    def test_statistics_invalid_dates_return_bad_request(self):
        """Statistics endpoint should reject invalid date formats."""
        invalid_queries = [
            {
                "start_date": "not-a-date",
                "end_date": "2026-01-01",
            },
            {
                "start_date": "2026-01-01",
                "end_date": "still-not-a-date",
            },
            {
                "start_date": "2026-99-99",
                "end_date": "2026-01-01",
            },
        ]

        for params in invalid_queries:
            with self.subTest(params=params):
                response = self.call_api(
                    "get",
                    "api_statistics",
                    headers=self.auth_headers,
                    params=params,
                )

                self.assertEqual(response.status_code, 400)
                payload = response.json()
                self.assertIn("detail", payload)

    def test_statistics_default_range_returns_payload(self):
        """Statistics endpoint should return aggregated payload with defaults."""
        response = self.call_api(
            "get",
            "api_statistics",
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        check_statistics_structure(self, payload)

    def test_statistics_all_time_range_returns_payload(self):
        """Statistics endpoint should support all-time range with null dates."""
        response = self.call_api(
            "get",
            "api_statistics",
            headers=self.auth_headers,
            params={"start_date": "all", "end_date": "all"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        check_statistics_structure(self, payload)
        self.assertIsNone(payload.get("start_date"))
        self.assertIsNone(payload.get("end_date"))

    def test_statistics_valid_custom_range_returns_payload(self):
        """Statistics endpoint should accept explicit valid date range."""
        response = self.call_api(
            "get",
            "api_statistics",
            headers=self.auth_headers,
            params={"start_date": "2025-01-01", "end_date": "2026-12-31"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        check_statistics_structure(self, payload)


class StatisticsQueryScalingTests(FloppyApiTestCase):
    """The legacy statistics endpoint must not query once per show or episode."""

    def _add_watched_shows(self, count, offset):
        now = timezone.now()
        with suppress_media_change_side_effects():
            for index in range(offset, offset + count):
                media_id = f"stats-scale-{index}"
                tv = TV.objects.create(
                    item=Item.objects.create(
                        media_id=media_id,
                        source=Sources.TMDB.value,
                        media_type=MediaTypes.TV.value,
                        title=f"Scale show {index}",
                    ),
                    user=self.user1,
                    status=Status.COMPLETED.value,
                    score=7,
                )
                season = Season.objects.create(
                    item=Item.objects.create(
                        media_id=media_id,
                        source=Sources.TMDB.value,
                        media_type=MediaTypes.SEASON.value,
                        title=f"Scale show {index}",
                        season_number=1,
                    ),
                    user=self.user1,
                    related_tv=tv,
                    status=Status.COMPLETED.value,
                    score=8,
                )
                Episode.objects.bulk_create(
                    [
                        Episode(
                            item=Item.objects.create(
                                media_id=media_id,
                                source=Sources.TMDB.value,
                                media_type=MediaTypes.EPISODE.value,
                                title=f"Scale episode {number}",
                                season_number=1,
                                episode_number=number,
                            ),
                            related_season=season,
                            status=Status.COMPLETED.value,
                            end_date=now - timedelta(days=number),
                        )
                        for number in range(1, 4)
                    ],
                )

    def _query_count(self, params):
        with CaptureQueriesContext(connection) as queries:
            response = self.call_api(
                "get",
                "api_statistics",
                headers=self.auth_headers,
                params=params,
            )
        self.assertEqual(response.status_code, 200)
        return len(queries)

    # Top rated is capped (50 in production) and each entry it serializes
    # looks up its next episode, so hold it at 2 entries to see only the cost
    # that used to grow with the library: season progress and activity dates.
    @patch("app.stats_score.STATISTICS_TOP_RATED_OVERALL", 2)
    def test_query_count_does_not_grow_with_shows(self):
        """More watched, scored shows and episodes cost no extra queries."""
        for params in ({}, {"start_date": "all", "end_date": "all"}):
            with self.subTest(params=params):
                self._add_watched_shows(2, offset=0 if not params else 100)
                self._query_count(params)  # warm per-process lookups
                baseline = self._query_count(params)
                self._add_watched_shows(5, offset=10 if not params else 110)
                self.assertEqual(self._query_count(params), baseline)
