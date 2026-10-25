from types import SimpleNamespace

from django.template import Context, Template
from django.test import TestCase
from django.utils import translation
from django.utils.dates import MONTHS, WEEKDAYS
from django.utils.text import capfirst

from users.models import DateFormatChoices


class TemplateFiltersTagsTests(TestCase):
    """Tests for custom template filters and tags."""

    def test_get_item_filter(self):
        """Test the get_item filter."""
        # Create a template that uses the get_item filter
        template_str = """
            {% load events_tags %}
            {{ my_dict|get_item:"key1" }}
            {{ my_dict|get_item:"key2" }}
            {{ my_dict|get_item:"nonexistent_key" }}
        """
        template = Template(template_str)

        # Create a context with a dictionary
        context = Context(
            {
                "my_dict": {
                    "key1": "value1",
                    "key2": ["item1", "item2"],
                },
            },
        )

        # Render the template
        rendered = template.render(context)

        # Check the output
        self.assertIn("value1", rendered)
        self.assertIn("item1", rendered)
        self.assertIn("item2", rendered)
        self.assertIn("[]", rendered)  # Default empty list for nonexistent key

    def test_get_item_filter_with_empty_dict(self):
        """Test the get_item filter with an empty dictionary."""
        template_str = """
            {% load events_tags %}
            {{ empty_dict|get_item:"any_key" }}
        """
        template = Template(template_str)
        context = Context({"empty_dict": {}})
        rendered = template.render(context)
        self.assertIn("[]", rendered)

    def _render_day_label(self, day, month, year, date_format):
        template = Template(
            "{% load events_tags %}{% calendar_day_label day month year user %}"
        )
        context = Context(
            {
                "day": day,
                "month": month,
                "year": year,
                "user": SimpleNamespace(date_format=date_format),
            },
        )
        return template.render(context)

    def test_calendar_day_label_follows_date_format_order(self):
        """Month-first date formats put the month before the day."""
        cases = [
            (DateFormatChoices.ISO, "Sunday, January 1"),
            (DateFormatChoices.US, "Sunday, January 1"),
            (DateFormatChoices.LONG, "Sunday, January 1"),
            (DateFormatChoices.EUROPEAN, "Sunday 1 January"),
            (DateFormatChoices.LONG_EU, "Sunday 1 January"),
        ]
        for date_format, expected in cases:
            with self.subTest(date_format=date_format):
                self.assertEqual(
                    self._render_day_label(1, 1, 2023, date_format),
                    expected,
                )

    def test_calendar_day_label_accepts_strings(self):
        """Day, month and year can come from the template as strings."""
        self.assertEqual(
            self._render_day_label("31", "10", "2023", DateFormatChoices.US),
            "Tuesday, October 31",
        )

    def test_calendar_day_label_leap_year(self):
        """February 29 is a valid day in a leap year."""
        self.assertEqual(
            self._render_day_label(29, 2, 2020, DateFormatChoices.US),
            "Saturday, February 29",
        )

    def test_calendar_day_label_is_translated(self):
        """Weekday and month names follow the active language."""
        with translation.override("it"):
            label = self._render_day_label(1, 1, 2023, DateFormatChoices.EUROPEAN)
            expected = f"{capfirst(WEEKDAYS[6])} 1 {MONTHS[1]}"
        self.assertEqual(label, expected)
        self.assertNotEqual(label, "Sunday 1 January")

    def test_calendar_day_label_invalid_input(self):
        """Invalid dates raise a ValueError."""
        with self.assertRaises(ValueError):
            self._render_day_label(30, 2, 2023, DateFormatChoices.US)

        with self.assertRaises(ValueError):
            self._render_day_label("day", "month", "year", DateFormatChoices.US)
