from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.models import UserMessage, UserMessageLevel


class PersistentUserMessageViewTests(TestCase):
    """Test persistent user message rendering and acknowledgement."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_unshown_messages_are_rendered(self):
        """Persistent messages should be available in the base toast UI."""
        persistent_message = UserMessage.objects.create(
            user=self.user,
            level=UserMessageLevel.WARNING,
            message="Persistent warning",
        )

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Persistent warning")
        self.assertContains(response, reverse("mark_user_messages_shown"))
        self.assertContains(
            response,
            f'name="message_ids" value="{persistent_message.id}"',
        )

    def test_mark_user_messages_shown(self):
        """Posting to the mark-shown endpoint should timestamp only rendered rows."""
        first_message = UserMessage.objects.create(
            user=self.user,
            level=UserMessageLevel.INFO,
            message="First message",
        )
        second_message = UserMessage.objects.create(
            user=self.user,
            level=UserMessageLevel.SUCCESS,
            message="Second message",
        )
        third_message = UserMessage.objects.create(
            user=self.user,
            level=UserMessageLevel.WARNING,
            message="Third message",
        )

        response = self.client.post(
            reverse("mark_user_messages_shown"),
            {"message_ids": [first_message.id, second_message.id]},
        )

        self.assertEqual(response.status_code, 204)

        first_message.refresh_from_db()
        second_message.refresh_from_db()
        third_message.refresh_from_db()

        self.assertIsNotNone(first_message.shown_at)
        self.assertIsNotNone(second_message.shown_at)
        self.assertIsNone(third_message.shown_at)
