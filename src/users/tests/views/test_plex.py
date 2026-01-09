
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse


class PlexUsernamesUpdateTests(TestCase):
    """Tests for Plex integration functionality."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_update_plex_usernames_success(self):
        """Test successful update of Plex usernames."""
        response = self.client.post(
            reverse("update_plex_usernames"),
            {"plex_usernames": "user1, user2, user3"},
        )

        self.assertRedirects(response, reverse("integrations"))
        self.user.refresh_from_db()

        self.assertEqual(self.user.plex_usernames, "user1, user2, user3")

        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("updated successfully", str(messages[0]))

    def test_update_plex_usernames_deduplication(self):
        """Test duplicate usernames are removed."""
        self.client.post(
            reverse("update_plex_usernames"),
            {"plex_usernames": "user1, user2, user1, user3, user2"},
        )

        self.user.refresh_from_db()
        self.assertEqual(self.user.plex_usernames, "user1, user2, user3")

    def test_update_plex_usernames_whitespace_handling(self):
        """Test whitespace in usernames is handled correctly."""
        self.client.post(
            reverse("update_plex_usernames"),
            {"plex_usernames": "  user1  , user2  ,  user3  "},
        )

        self.user.refresh_from_db()
        self.assertEqual(self.user.plex_usernames, "user1, user2, user3")

    def test_update_plex_usernames_empty(self):
        """Test empty username list."""
        self.user.plex_usernames = "user1, user2"
        self.user.save()

        response = self.client.post(
            reverse("update_plex_usernames"),
            {"plex_usernames": ""},
        )

        self.user.refresh_from_db()
        self.assertEqual(self.user.plex_usernames, "")

        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("updated successfully", str(messages[0]))

    def test_update_plex_usernames_no_change(self):
        """Test no update when usernames haven't changed."""
        self.user.plex_usernames = "user1, user2"
        self.user.save()

        response = self.client.post(
            reverse("update_plex_usernames"),
            {"plex_usernames": "user1, user2"},
        )

        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 0)
