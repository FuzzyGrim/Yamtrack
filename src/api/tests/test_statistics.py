from datetime import date
from unittest.mock import patch

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

    @patch("api.views.localdate", return_value=date(2028, 2, 29))
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
        self.assertTrue(payload["start_date"].startswith("2027-02-28"))

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
