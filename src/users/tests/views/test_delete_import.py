
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from django_celery_beat.models import CrontabSchedule, PeriodicTask


class DeleteImportScheduleTests(TestCase):
    """Tests for the delete_import_schedule view."""

    def setUp(self):
        """Create user and test data for the tests."""
        self.credentials = {"username": "testuser", "password": "testpass123"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        self.crontab = CrontabSchedule.objects.create(
            minute="0",
            hour="0",
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
        )

        self.task = PeriodicTask.objects.create(
            name="Import from Trakt for testuser at daily",
            task="Import from Trakt",
            kwargs=f'{{"user_id": {self.user.id}, "username": "testuser"}}',
            crontab=self.crontab,
            enabled=True,
        )

        self.other_credentials = {"username": "otheruser", "password": "testpass123"}
        self.other_user = get_user_model().objects.create_user(**self.other_credentials)

        self.other_task = PeriodicTask.objects.create(
            name="Import from Trakt for otheruser at daily",
            task="Import from Trakt",
            kwargs=f'{{"user_id": {self.other_user.id}, "username": "otheruser"}}',
            crontab=self.crontab,
            enabled=True,
        )

    def test_delete_import_schedule_success(self):
        """Test successful deletion of an import schedule."""
        response = self.client.post(
            reverse("delete_import_schedule"),
            {
                "task_name": self.task.name,
            },
        )
        self.assertRedirects(response, reverse("import_data"))

        with self.assertRaises(PeriodicTask.DoesNotExist):
            PeriodicTask.objects.get(id=self.task.id)

        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("Import schedule deleted", str(messages[0]))

        self.assertTrue(PeriodicTask.objects.filter(id=self.other_task.id).exists())

    def test_delete_import_schedule_not_found(self):
        """Test deletion of a non-existent import schedule."""
        response = self.client.post(
            reverse("delete_import_schedule"),
            {
                "task_name": "Non-existent Task",
            },
        )
        self.assertRedirects(response, reverse("import_data"))

        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("Import schedule not found", str(messages[0]))

        self.assertTrue(PeriodicTask.objects.filter(id=self.task.id).exists())

    def test_delete_import_schedule_other_user(self):
        """Test deletion of another user's import schedule."""
        response = self.client.post(
            reverse("delete_import_schedule"),
            {
                "task_name": self.other_task.name,
            },
        )
        self.assertRedirects(response, reverse("import_data"))

        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("Import schedule not found", str(messages[0]))

        self.assertTrue(PeriodicTask.objects.filter(id=self.other_task.id).exists())


