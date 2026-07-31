import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django_celery_beat.models import PeriodicTask

from integrations.forms import CalibreWebNextGenImportDataForm


def form_data(**overrides):
    """Build a valid import-form payload, overriding individual fields."""
    data = {
        "url": "http://localhost:8083",
        "username": "admin",
        "password": "secret",
        "frequency": "once",
        "mode": "new",
    }
    data.update(overrides)
    return data


class CalibreWebNextGenFormTests(TestCase):
    """Test Calibre-Web-NextGen import form validation."""

    def test_once_needs_no_time(self):
        """Test 'once' is valid without a time."""
        self.assertTrue(CalibreWebNextGenImportDataForm(form_data()).is_valid())

    def test_recurring_requires_time(self):
        """Test a recurring frequency without a time is rejected."""
        form = CalibreWebNextGenImportDataForm(form_data(frequency="daily"))
        self.assertFalse(form.is_valid())
        self.assertIn("time", form.errors)


class CalibreWebNextGenViewTests(TestCase):
    """Test the Calibre-Web-NextGen import view."""

    def setUp(self):
        """Create and log in a user."""
        credentials = {"username": "testuser", "password": "testpass123"}
        get_user_model().objects.create_user(**credentials)
        self.client.login(**credentials)

    def _post(self, **overrides):
        return self.client.post(
            reverse("import_calibre_web_nextgen"), form_data(**overrides)
        )

    @patch("integrations.views.tasks.import_calibre_web_nextgen.delay")
    def test_once_queues_task_with_encrypted_password(self, mock_delay):
        """Test 'once' queues the task and never forwards the raw password."""
        self.assertRedirects(self._post(), reverse("import_data"))
        mock_delay.assert_called_once()
        kwargs = mock_delay.call_args.kwargs
        self.assertEqual(kwargs["mode"], "new")
        self.assertNotEqual(kwargs["encrypted_password"], "secret")

    @patch("integrations.views.tasks.import_calibre_web_nextgen.delay")
    def test_recurring_schedules_periodic_task(self, mock_delay):
        """Test a recurring frequency schedules a task instead of queuing one."""
        self.assertRedirects(
            self._post(frequency="daily", time="14:30"), reverse("import_data")
        )
        mock_delay.assert_not_called()
        task = PeriodicTask.objects.get(task="Import from Calibre-Web-NextGen")
        stored = json.loads(task.kwargs)
        self.assertEqual(stored["url"], "http://localhost:8083")
        self.assertNotEqual(stored["encrypted_password"], "secret")

    @patch("integrations.views.tasks.import_calibre_web_nextgen.delay")
    def test_invalid_form_does_nothing(self, mock_delay):
        """Test an invalid payload queues no task and schedules nothing."""
        response = self.client.post(
            reverse("import_calibre_web_nextgen"), {"frequency": "once", "mode": "new"}
        )
        self.assertRedirects(response, reverse("import_data"))
        mock_delay.assert_not_called()
        self.assertFalse(PeriodicTask.objects.exists())
