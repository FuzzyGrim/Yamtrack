from django.test import TestCase

from events.calendar.helpers import date_parser
from events.models import SentinelDatetime


class CalendarHelperTests(TestCase):
    """Test shared calendar helper functions."""

    def test_date_parser_supports_year_only_dates(self):
        """Year-only dates should default to January 1st."""
        parsed = date_parser("2025")

        self.assertEqual(parsed.year, 2025)
        self.assertEqual(parsed.month, 1)
        self.assertEqual(parsed.day, 1)
        self.assertEqual(parsed.hour, SentinelDatetime.HOUR)

    def test_date_parser_supports_year_month_dates(self):
        """Year-month dates should default to the first day of the month."""
        parsed = date_parser("2025-03")

        self.assertEqual(parsed.year, 2025)
        self.assertEqual(parsed.month, 3)
        self.assertEqual(parsed.day, 1)
        self.assertEqual(parsed.minute, SentinelDatetime.MINUTE)
