import json

from django.contrib import auth
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django_celery_results.models import TaskResult


class Profile(TestCase):
    """Test profile page."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_change_username(self):
        """Test changing username."""
        self.assertEqual(auth.get_user(self.client).username, "test")
        self.client.post(
            reverse("profile"),
            {
                "username": "new_test",
            },
        )
        self.assertEqual(auth.get_user(self.client).username, "new_test")

    def test_change_password(self):
        """Test changing password."""
        self.assertEqual(auth.get_user(self.client).check_password("12345"), True)
        self.client.post(
            reverse("profile"),
            {
                "old_password": "12345",
                "new_password1": "*FNoZN64",
                "new_password2": "*FNoZN64",
            },
        )
        self.assertEqual(auth.get_user(self.client).check_password("*FNoZN64"), True)


class TaskViewTests(TestCase):
    """Test user task views."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_task_status_handling(self):
        """Test if view correctly modifies the task results based on their status."""
        TaskResult.objects.create(
            task_args=json.dumps(["<SimpleLazyObject: <User: test>>"]),
            result=json.dumps({"exc_message": ["Error occurred"]}),
            task_id="1",
            status="FAILURE",
        )
        TaskResult.objects.create(
            task_args=json.dumps(["<SimpleLazyObject: <User: test>>"]),
            result=json.dumps({}),
            task_id="2",
            status="STARTED",
        )
        TaskResult.objects.create(
            task_args=json.dumps(["<SimpleLazyObject: <User: test>>"]),
            result=None,
            task_id="3",
            status="PENDING",
        )

        response = self.client.get(reverse("tasks"))
        tasks = response.context["tasks"]

        self.assertEqual(tasks[2].result, "Error occurred")
        self.assertEqual(tasks[1].result, "Task in progress")
        self.assertEqual(tasks[0].result, "Waiting for task to start")
