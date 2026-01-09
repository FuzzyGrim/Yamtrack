from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse


class RegenerateTokenTests(TestCase):
    """Tests for the regenerate_token view."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "testuser", "password": "testpass123"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        self.user.token = "initial_token"  # noqa: S105
        self.user.save()

    def test_regenerate_token(self):
        """Test token regeneration."""
        response = self.client.post(reverse("regenerate_token"))
        self.assertRedirects(response, reverse("integrations"))

        self.user.refresh_from_db()
        self.assertNotEqual(self.user.token, "initial_token")
        self.assertIsNotNone(self.user.token)

        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("Token regenerated successfully", str(messages[0]))

    @patch("django.db.models.Model.save")
    def test_regenerate_token_integrity_error(self, mock_save):
        """Test token regeneration with an IntegrityError on first attempt."""
        mock_save.side_effect = [
            IntegrityError("Duplicate token"),
            None,
        ]

        response = self.client.post(reverse("regenerate_token"))
        self.assertRedirects(response, reverse("integrations"))

        self.assertEqual(mock_save.call_count, 2)

        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("Token regenerated successfully", str(messages[0]))


