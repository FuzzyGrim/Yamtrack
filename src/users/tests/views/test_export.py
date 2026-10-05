"""Tests for data export settings."""

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse


class ExportSettingsTests(TestCase):
    """Test the automatic export settings."""

    def setUp(self):
        """Create and authenticate a user."""
        self.credentials = {
            "username": "export-user",
            "password": "test-password",
        }
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_export_settings_default_to_disabled_and_seven_days(self):
        """New users do not receive export reminders until they opt in."""
        self.assertFalse(self.user.auto_export_enabled)
        self.assertEqual(self.user.auto_export_interval_value, 7)
        self.assertEqual(self.user.auto_export_interval_unit, "days")

    def test_export_settings_can_be_updated(self):
        """Users can enable the reminder and choose its interval."""
        response = self.client.post(
            reverse("export_data"),
            {
                "auto_export_enabled": "on",
                "auto_export_interval_value": "48",
                "auto_export_interval_unit": "hours",
            },
        )

        self.assertRedirects(response, reverse("export_data"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.auto_export_enabled)
        self.assertEqual(self.user.auto_export_interval_value, 48)
        self.assertEqual(self.user.auto_export_interval_unit, "hours")
        message = next(iter(get_messages(response.wsgi_request)))
        self.assertIn("Automatic export settings updated", str(message))

    def test_export_settings_reject_invalid_interval(self):
        """The interval must be a practical positive number of hours."""
        response = self.client.post(
            reverse("export_data"),
            {
                "auto_export_enabled": "on",
                "auto_export_interval_value": "0",
                "auto_export_interval_unit": "seconds",
            },
        )

        self.assertRedirects(response, reverse("export_data"))
        self.user.refresh_from_db()
        self.assertFalse(self.user.auto_export_enabled)
        self.assertEqual(self.user.auto_export_interval_value, 7)
        self.assertEqual(self.user.auto_export_interval_unit, "days")
        message = next(iter(get_messages(response.wsgi_request)))
        self.assertIn("positive number with a valid unit", str(message))

    def test_export_settings_accepts_a_five_second_interval(self):
        """Users can configure a short reminder interval for testing."""
        response = self.client.post(
            reverse("export_data"),
            {
                "auto_export_enabled": "on",
                "auto_export_interval_value": "5",
                "auto_export_interval_unit": "seconds",
            },
        )

        self.assertRedirects(response, reverse("export_data"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.auto_export_interval_value, 5)
        self.assertEqual(self.user.auto_export_interval_unit, "seconds")

    def test_export_settings_demo_user_cannot_update(self):
        """Demo accounts cannot change automatic export settings."""
        self.user.is_demo = True
        self.user.save()

        response = self.client.post(
            reverse("export_data"),
            {
                "auto_export_enabled": "on",
                "auto_export_interval_value": "48",
                "auto_export_interval_unit": "hours",
            },
        )

        self.assertRedirects(response, reverse("export_data"))
        self.user.refresh_from_db()
        self.assertFalse(self.user.auto_export_enabled)
        self.assertEqual(self.user.auto_export_interval_value, 7)
        self.assertEqual(self.user.auto_export_interval_unit, "days")
        message = next(iter(get_messages(response.wsgi_request)))
        self.assertIn("view-only for demo accounts", str(message))

    def test_export_settings_reject_non_numeric_interval(self):
        """A non-numeric interval is treated as invalid."""
        response = self.client.post(
            reverse("export_data"),
            {
                "auto_export_enabled": "on",
                "auto_export_interval_value": "not-a-number",
                "auto_export_interval_unit": "hours",
            },
        )

        self.assertRedirects(response, reverse("export_data"))
        self.user.refresh_from_db()
        self.assertFalse(self.user.auto_export_enabled)
        self.assertEqual(self.user.auto_export_interval_value, 7)
        self.assertEqual(self.user.auto_export_interval_unit, "days")
        message = next(iter(get_messages(response.wsgi_request)))
        self.assertIn("positive number with a valid unit", str(message))
