from datetime import UTC, date, datetime
from unittest.mock import patch

from api.helpers import MEDIA_STATUS_MAP
from app.models import Status

from .base import YamtrackApiTestCase
from .helpers import check_statistics_structure


class StatisticsTests(YamtrackApiTestCase):
    """Validate statistics endpoint contracts."""

    def test_statistics_unauthenticated_returns_unauthorized(self):
        """Statistics endpoint should require authentication."""
        response = self.call_api(
            "get",
            "api_statistics",
        )

        self.assertEqual(response.status_code, 401)

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

    @patch("api.views.statistics.localdate", return_value=date(2028, 2, 29))
    def test_statistics_default_range_on_leap_day(self, _mock_localdate):
        """Statistics default range shouldn't fail on February 29th."""
        response = self.call_api(
            "get",
            "api_statistics",
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        check_statistics_structure(self, payload)
        self.assertEqual(payload["start_date"], "2027-02-28")

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

    def test_statistics_timeline_items_use_api_values(self):
        """Timeline items should return the score and status like the media."""
        movie_media = self.movie_medias[0]
        movie_media.score = 8.5
        movie_media.status = Status.COMPLETED.value
        movie_media.start_date = datetime(2026, 3, 10, 10, tzinfo=UTC)
        movie_media.end_date = datetime(2026, 3, 12, 10, tzinfo=UTC)
        movie_media.save()

        response = self.call_api(
            "get",
            "api_statistics",
            headers=self.auth_headers,
            params={"start_date": "2026-01-01", "end_date": "2026-12-31"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["start_date"], "2026-01-01")
        self.assertEqual(payload["end_date"], "2026-12-31")
        timeline_item = next(
            item
            for item in payload["timeline"]["March 2026"]
            if item["id"] == movie_media.id
        )
        self.assertEqual(timeline_item["score"], 8.5)
        self.assertEqual(timeline_item["status"], MEDIA_STATUS_MAP["Completed"])
        self.assertEqual(timeline_item["media_type"], movie_media.item.media_type)
        self.assertNotIn("item", timeline_item)
