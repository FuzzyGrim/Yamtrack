from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from app.models import MediaTypes
from users.models import WeekStartDayChoices


class SidebarViewTests(TestCase):
    """Tests for the sidebar view."""

    def setUp(self):
        """Create user for the tests."""
        self.watch_regions_patcher = patch(
            "users.views.tmdb.watch_provider_regions",
            return_value=[("UNSET", "Disabled"), ("US", "United States")],
        )
        self.watch_regions_patcher.start()
        self.addCleanup(self.watch_regions_patcher.stop)

        self.credentials = {"username": "testuser", "password": "testpass123"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_preferences_get(self):
        """Test GET request to preferences view."""
        response = self.client.get(reverse("preferences"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "users/preferences.html")

        self.assertIn("media_types", response.context)
        self.assertIn(MediaTypes.TV.value, response.context["media_types"])
        self.assertIn(MediaTypes.MOVIE.value, response.context["media_types"])
        self.assertNotIn(MediaTypes.EPISODE.value, response.context["media_types"])

    def test_sidebar_post_update_preferences(self):
        """Test POST request to update preferences."""
        self.user.tv_enabled = True
        self.user.movie_enabled = True
        self.user.anime_enabled = True
        self.user.save()

        response = self.client.post(
            reverse("preferences"),
            {
                "media_types_checkboxes": [MediaTypes.TV.value, MediaTypes.ANIME.value],
            },
        )
        self.assertRedirects(response, reverse("preferences"))

        self.user.refresh_from_db()
        self.assertTrue(self.user.tv_enabled)
        self.assertFalse(self.user.movie_enabled)
        self.assertTrue(self.user.anime_enabled)

        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("Settings updated", str(messages[0]))

    def test_sidebar_post_demo_user(self):
        """Test POST request from a demo user to preferences."""
        self.user.is_demo = True
        self.user.tv_enabled = True
        self.user.movie_enabled = False
        self.user.save()

        response = self.client.post(
            reverse("preferences"),
            {
                "media_types_checkboxes": [MediaTypes.TV.value, MediaTypes.MOVIE.value],
            },
        )
        self.assertRedirects(response, reverse("preferences"))

        self.user.refresh_from_db()
        self.assertTrue(self.user.tv_enabled)
        self.assertFalse(self.user.movie_enabled)

        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("view-only for demo accounts", str(messages[0]))

    def test_obfuscate_unseen_episodes_post_enable(self):
        """Test enabling obfuscate_unseen_episodes via preferences."""
        self.user.obfuscate_unseen_episodes = False
        self.user.save()

        response = self.client.post(
            reverse("preferences"),
            {
                "obfuscate_unseen_episodes": "on",
                "media_types_checkboxes": [MediaTypes.TV.value],
            },
        )
        self.assertRedirects(response, reverse("preferences"))

        self.user.refresh_from_db()
        self.assertTrue(self.user.obfuscate_unseen_episodes)

        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("Settings updated", str(messages[0]))

    def test_obfuscate_unseen_episodes_post_disable(self):
        """Test disabling obfuscate_unseen_episodes via preferences."""
        self.user.obfuscate_unseen_episodes = True
        self.user.save()

        response = self.client.post(
            reverse("preferences"),
            {
                "media_types_checkboxes": [MediaTypes.TV.value],
            },
        )
        self.assertRedirects(response, reverse("preferences"))

        self.user.refresh_from_db()
        self.assertFalse(self.user.obfuscate_unseen_episodes)

        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("Settings updated", str(messages[0]))

    def test_clickable_media_cards_and_obfuscate_unseen_episodes(self):
        """Test updating both clickable_media_cards and obfuscate_unseen_episodes."""
        self.user.clickable_media_cards = False
        self.user.obfuscate_unseen_episodes = False
        self.user.save()

        response = self.client.post(
            reverse("preferences"),
            {
                "clickable_media_cards": "on",
                "obfuscate_unseen_episodes": "on",
                "media_types_checkboxes": [MediaTypes.TV.value],
            },
        )
        self.assertRedirects(response, reverse("preferences"))

        self.user.refresh_from_db()
        self.assertTrue(self.user.clickable_media_cards)
        self.assertTrue(self.user.obfuscate_unseen_episodes)

    def test_obfuscate_unseen_episodes_post_demo_user(self):
        """Test that demo users cannot update obfuscate_unseen_episodes."""
        self.user.is_demo = True
        self.user.obfuscate_unseen_episodes = False
        self.user.save()

        response = self.client.post(
            reverse("preferences"),
            {
                "obfuscate_unseen_episodes": "on",
                "media_types_checkboxes": [MediaTypes.TV.value],
            },
        )
        self.assertRedirects(response, reverse("preferences"))

        self.user.refresh_from_db()
        self.assertFalse(self.user.obfuscate_unseen_episodes)

        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("view-only for demo accounts", str(messages[0]))

    def test_post_updates_week_start_day_valid(self):
        """Posting a valid week_start_day updates the user preference."""
        self.assertEqual(self.user.week_start_day, WeekStartDayChoices.MONDAY)
        response = self.client.post(
            reverse("preferences"),
            {
                "media_types_checkboxes": [MediaTypes.TV.value],
                "week_start_day": WeekStartDayChoices.SUNDAY,
            },
        )
        self.assertRedirects(response, reverse("preferences"))

        self.user.refresh_from_db()
        self.assertEqual(self.user.week_start_day, WeekStartDayChoices.SUNDAY)

    def test_post_ignores_invalid_week_start_day(self):
        """Posting an invalid week_start_day leaves the value unchanged."""
        self.user.week_start_day = WeekStartDayChoices.SUNDAY
        self.user.save()

        response = self.client.post(
            reverse("preferences"),
            {
                "media_types_checkboxes": [MediaTypes.TV.value],
                "week_start_day": "saturday",
            },
        )
        self.assertRedirects(response, reverse("preferences"))

        self.user.refresh_from_db()
        self.assertEqual(self.user.week_start_day, WeekStartDayChoices.SUNDAY)
