from unittest.mock import patch

from django.utils import timezone

from .base import YamtrackApiTestCase
from .helpers import check_calendar_event_structure, check_pagination_structure


class CalendarTests(YamtrackApiTestCase):
    """Validate calendar endpoint contracts."""

    def setUp(self):
        """Set up."""
        super().setUp()
        self._calendar_patcher = patch(
            "app.models.Item.fetch_releases",
            return_value=None,
        )
        self._calendar_patcher.start()
        self.addCleanup(self._calendar_patcher.stop)

    def test_calendar_get(self):
        """Calendar GET should return paginated response data."""
        today = timezone.localdate()
        month_start = today.replace(day=1)
        if today.month == 12:  # noqa: PLR2004
            next_month_start = today.replace(year=today.year + 1, month=1, day=1)
        else:
            next_month_start = today.replace(month=today.month + 1, day=1)
        month_end = next_month_start - timezone.timedelta(days=1)
        expected_total = sum(
            1
            for event in self.calendar_events
            if month_start <= timezone.localtime(event.datetime).date() <= month_end
        )

        response = self.call_api("get", "api_calendar", headers=self.auth_headers)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("pagination", payload)
        check_pagination_structure(
            self,
            payload["pagination"],
            total=expected_total,
            limit=20,
            offset=0,
        )
        self.assertIn("results", payload)

        returned_media_ids = {
            result["item"]["media_id"] for result in payload["results"]
        }
        expected_media_ids = {item.media_id for item in self.items_by_type["movie"]}
        self.assertTrue(expected_media_ids.issubset(returned_media_ids))

        for item in payload["results"]:
            check_calendar_event_structure(self, item)

    def test_calendar_get_date_filtering(self):
        """Calendar GET should filter by date query params."""
        earlier_event = self.calendar_events[3]
        future_event = self.calendar_events[4]
        earlier_event_date = timezone.localtime(earlier_event.datetime).date()
        future_event_date = timezone.localtime(future_event.datetime).date()

        response = self.call_api(
            "get",
            "api_calendar",
            headers=self.auth_headers,
            params={
                "end_date": (
                    earlier_event_date + timezone.timedelta(days=1)
                ).isoformat()
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        returned_ids = {item["id"] for item in payload["results"]}
        self.assertIn(earlier_event.id, returned_ids)
        self.assertNotIn(future_event.id, returned_ids)

        response = self.call_api(
            "get",
            "api_calendar",
            headers=self.auth_headers,
            params={
                "start_date": (
                    future_event_date - timezone.timedelta(days=5)
                ).isoformat(),
                "end_date": (
                    future_event_date + timezone.timedelta(days=5)
                ).isoformat(),
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        returned_ids = {item["id"] for item in payload["results"]}
        self.assertIn(future_event.id, returned_ids)
        self.assertNotIn(earlier_event.id, returned_ids)

        response = self.call_api(
            "get",
            "api_calendar",
            headers=self.auth_headers,
            params={"start_date": future_event_date.isoformat()},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        returned_ids = {item["id"] for item in payload["results"]}
        self.assertIn(future_event.id, returned_ids)
        self.assertNotIn(earlier_event.id, returned_ids)

    def test_calendar_get_invalid_date_params(self):
        """Calendar GET should return 400 on invalid date params."""
        invalid_queries = [
            {"start_date": "not-a-date"},
            {"end_date": "2026-13-40"},
            {"month": "13", "year": "2026"},
            {"month": "1", "year": "not-a-year"},
        ]

        for params in invalid_queries:
            response = self.call_api(
                "get",
                "api_calendar",
                headers=self.auth_headers,
                params=params,
            )
            self.assertEqual(response.status_code, 400)
            payload = response.json()
            self.assertIn("detail", payload)

    @patch("events.tasks.reload_calendar.delay")
    def test_calendar_update_post(self, mock_delay):
        """Calendar update should queue async task and return 202."""
        response = self.call_api(
            "post",
            "api_update_calendar",
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 202)
        mock_delay.assert_called_once()
