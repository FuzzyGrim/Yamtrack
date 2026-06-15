from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse


class JellyfinWebhookEventSettingsTests(TestCase):
    """Tests for Jellyfin integration settings."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_update_jellyfin_webhook_events(self):
        """Test updating optional Jellyfin webhook event settings."""
        response = self.client.post(
            reverse("update_jellyfin_webhook_events"),
            {
                "jellyfin_mark_played_enabled": "on",
                "jellyfin_mark_unplayed_enabled": "on",
            },
        )
        self.assertRedirects(response, reverse("integrations"))

        self.user.refresh_from_db()
        self.assertTrue(self.user.jellyfin_mark_played_enabled)
        self.assertTrue(self.user.jellyfin_mark_unplayed_enabled)

        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("Jellyfin webhook settings updated", str(messages[0]))

    def test_update_jellyfin_webhook_events_unchecked(self):
        """Test unchecked Jellyfin webhook event settings are disabled."""
        self.user.jellyfin_mark_played_enabled = True
        self.user.jellyfin_mark_unplayed_enabled = True
        self.user.save(
            update_fields=[
                "jellyfin_mark_played_enabled",
                "jellyfin_mark_unplayed_enabled",
            ],
        )

        response = self.client.post(reverse("update_jellyfin_webhook_events"), {})
        self.assertRedirects(response, reverse("integrations"))

        self.user.refresh_from_db()
        self.assertFalse(self.user.jellyfin_mark_played_enabled)
        self.assertFalse(self.user.jellyfin_mark_unplayed_enabled)
