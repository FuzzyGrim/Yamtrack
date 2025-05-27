from unittest.mock import MagicMock, patch

from django.contrib import auth
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from django_celery_beat.models import CrontabSchedule, PeriodicTask

from app.models import Item, MediaTypes, Sources


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
            reverse("account"),
            {
                "username": "new_test",
            },
        )
        self.assertEqual(auth.get_user(self.client).username, "new_test")

    def test_change_password(self):
        """Test changing password."""
        self.assertEqual(auth.get_user(self.client).check_password("12345"), True)
        self.client.post(
            reverse("account"),
            {
                "old_password": "12345",
                "new_password1": "*FNoZN64",
                "new_password2": "*FNoZN64",
            },
        )
        self.assertEqual(auth.get_user(self.client).check_password("*FNoZN64"), True)

    def test_invalid_password_change(self):
        """Test password change with incorrect old password."""
        response = self.client.post(
            reverse("account"),
            {
                "old_password": "wrongpass",
                "new_password1": "newpass123",
                "new_password2": "newpass123",
            },
        )
        self.assertTrue(auth.get_user(self.client).check_password("12345"))
        self.assertContains(response, "Your old password was entered incorrectly")


class DemoProfileTests(TestCase):
    """Extended profile tests."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "testuser", "password": "testpass123"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_demo_user_cannot_change_username(self):
        """Test that demo users cannot change their username."""
        self.user.is_demo = True
        self.user.save()

        response = self.client.post(
            reverse("account"),
            {
                "username": "new_username",
            },
        )
        self.assertEqual(auth.get_user(self.client).username, "testuser")
        self.assertContains(response, "not allowed for the demo account")

    def test_demo_user_cannot_change_password(self):
        """Test that demo users cannot change their password."""
        self.user.is_demo = True
        self.user.save()

        response = self.client.post(
            reverse("account"),
            {
                "old_password": "testpass123",
                "new_password1": "newpass123",
                "new_password2": "newpass123",
            },
        )
        self.assertTrue(auth.get_user(self.client).check_password("testpass123"))
        self.assertContains(response, "not allowed for the demo account")


class NotificationTests(TestCase):
    """Tests for notification functionality."""

    def setUp(self):
        """Set up test data."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        # Create items
        self.item1 = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Test Anime",
            image="http://example.com/anime.jpg",
        )

        self.item2 = Item.objects.create(
            media_id="2",
            source=Sources.MAL.value,
            media_type=MediaTypes.MANGA.value,
            title="Test Manga",
            image="http://example.com/manga.jpg",
        )

    def test_notifications_get(self):
        """Test GET request to notifications view."""
        response = self.client.get(reverse("notifications"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "users/notifications.html")
        self.assertIn("form", response.context)

    def test_notifications_post_valid(self):
        """Test POST request with valid data."""
        response = self.client.post(
            reverse("notifications"),
            {
                "notification_urls": "discord://webhook_id/webhook_token",
            },
        )
        self.assertRedirects(response, reverse("notifications"))

        # Check that the user's notification_urls were updated
        self.user.refresh_from_db()
        self.assertEqual(
            self.user.notification_urls,
            "discord://webhook_id/webhook_token",
        )

        # Check for success message
        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("updated successfully", str(messages[0]))

    @patch("apprise.Apprise.add")
    def test_notifications_post_invalid(self, mock_add):
        """Test POST request with invalid data."""
        # Configure mock to return False for invalid URL
        mock_add.return_value = False

        response = self.client.post(
            reverse("notifications"),
            {
                "notification_urls": "invalid://url",
            },
        )
        self.assertRedirects(response, reverse("notifications"))

        # Check that the user's notification_urls were not updated
        self.user.refresh_from_db()
        self.assertEqual(self.user.notification_urls, "")

        # Check for error message
        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("not a valid Apprise URL", str(messages[0]))

    def test_exclude_item(self):
        """Test excluding an item from notifications."""
        response = self.client.post(
            reverse("exclude_notification_item"),
            {"item_id": self.item1.id},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "users/components/excluded_items.html")

        # Verify item was added to exclusions
        self.assertTrue(
            self.user.notification_excluded_items.filter(id=self.item1.id).exists(),
        )

    def test_include_item(self):
        """Test removing an item from exclusions."""
        # First add the item to exclusions
        self.user.notification_excluded_items.add(self.item1)

        response = self.client.post(
            reverse("include_notification_item"),
            {"item_id": self.item1.id},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "users/components/excluded_items.html")

        # Verify item was removed from exclusions
        self.assertFalse(
            self.user.notification_excluded_items.filter(id=self.item1.id).exists(),
        )

    def test_search_items(self):
        """Test searching for items to exclude."""
        response = self.client.get(
            reverse("search_notification_items"),
            {"q": "Test"},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "users/components/search_results.html")

        # Both items should be in results
        self.assertContains(response, "Test Anime")
        self.assertContains(response, "Test Manga")

        # Add item1 to exclusions
        self.user.notification_excluded_items.add(self.item1)

        response = self.client.get(
            reverse("search_notification_items"),
            {"q": "Test"},
            HTTP_HX_REQUEST="true",
        )

        # Only item2 should be in results now
        self.assertNotContains(response, "Test Anime")
        self.assertContains(response, "Test Manga")

    def test_search_items_short_query(self):
        """Test searching with a query that's too short."""
        response = self.client.get(
            reverse("search_notification_items"),
            {"q": "T"},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "users/components/search_results.html")

        # No items should be returned for a 1-character query
        self.assertNotContains(response, "Test Anime")
        self.assertNotContains(response, "Test Manga")

    def test_search_items_empty_query(self):
        """Test searching with an empty query."""
        response = self.client.get(
            reverse("search_notification_items"),
            {"q": ""},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "users/components/search_results.html")

        # No items should be returned for an empty query
        self.assertNotContains(response, "Test Anime")
        self.assertNotContains(response, "Test Manga")

    @patch("apprise.Apprise")
    def test_test_notification(self, mock_apprise):
        """Test the test notification endpoint."""
        # User with notification URLs
        self.user.notification_urls = "https://example.com/notify"
        self.user.save()

        mock_instance = MagicMock()
        mock_apprise.return_value = mock_instance
        mock_instance.notify.return_value = True

        response = self.client.get(reverse("test_notification"))

        # Should redirect back to notifications page
        self.assertRedirects(response, reverse("notifications"))

        # Should have called notify
        mock_instance.notify.assert_called_once()

        # Check for success message
        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("successfully", str(messages[0]))

    def test_test_notification_no_urls(self):
        """Test the test notification endpoint with no URLs configured."""
        # User without notification URLs
        self.user.notification_urls = ""
        self.user.save()

        response = self.client.get(reverse("test_notification"))

        # Should redirect back to notifications page
        self.assertRedirects(response, reverse("notifications"))

        # Check for error message
        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("No notification URLs configured", str(messages[0]))

    @patch("apprise.Apprise")
    def test_test_notification_failure(self, mock_apprise):
        """Test the test notification endpoint when notification fails."""
        # User with notification URLs
        self.user.notification_urls = "https://example.com/notify"
        self.user.save()

        mock_instance = MagicMock()
        mock_apprise.return_value = mock_instance
        mock_instance.notify.return_value = False

        response = self.client.get(reverse("test_notification"))

        # Should redirect back to notifications page
        self.assertRedirects(response, reverse("notifications"))

        # Should have called notify
        mock_instance.notify.assert_called_once()

        # Check for error message
        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("Failed", str(messages[0]))


class SidebarViewTests(TestCase):
    """Tests for the sidebar view."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "testuser", "password": "testpass123"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_sidebar_get(self):
        """Test GET request to sidebar view."""
        response = self.client.get(reverse("sidebar"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "users/sidebar.html")

        # Check that media_types are in context
        self.assertIn("media_types", response.context)
        self.assertIn(MediaTypes.TV.value, response.context["media_types"])
        self.assertIn(MediaTypes.MOVIE.value, response.context["media_types"])
        self.assertNotIn(MediaTypes.EPISODE.value, response.context["media_types"])

    def test_sidebar_post_update_preferences(self):
        """Test POST request to update sidebar preferences."""
        # Initial state
        self.user.tv_enabled = True
        self.user.movie_enabled = True
        self.user.anime_enabled = True
        self.user.hide_from_search = False
        self.user.save()

        # Update preferences
        response = self.client.post(
            reverse("sidebar"),
            {
                "media_types_checkboxes": [MediaTypes.TV.value, MediaTypes.ANIME.value],
                "hide_disabled": "on",
            },
        )
        self.assertRedirects(response, reverse("sidebar"))

        # Check that preferences were updated
        self.user.refresh_from_db()
        self.assertTrue(self.user.tv_enabled)
        self.assertFalse(self.user.movie_enabled)
        self.assertTrue(self.user.anime_enabled)
        self.assertTrue(self.user.hide_from_search)

        # Check for success message
        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("Settings updated", str(messages[0]))

    def test_sidebar_post_demo_user(self):
        """Test POST request from a demo user."""
        # Set user as demo
        self.user.is_demo = True
        self.user.tv_enabled = True
        self.user.movie_enabled = False
        self.user.save()

        # Try to update preferences
        response = self.client.post(
            reverse("sidebar"),
            {
                "media_types_checkboxes": [MediaTypes.TV.value, MediaTypes.MOVIE.value],
            },
        )
        self.assertRedirects(response, reverse("sidebar"))

        # Check that preferences were not updated
        self.user.refresh_from_db()
        self.assertTrue(self.user.tv_enabled)
        self.assertFalse(self.user.movie_enabled)

        # Check for error message
        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("view-only for demo accounts", str(messages[0]))


class DeleteImportScheduleTests(TestCase):
    """Tests for the delete_import_schedule view."""

    def setUp(self):
        """Create user and test data for the tests."""
        self.credentials = {"username": "testuser", "password": "testpass123"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        # Create a crontab schedule
        self.crontab = CrontabSchedule.objects.create(
            minute="0",
            hour="0",
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
        )

        # Create a periodic task for the user
        self.task = PeriodicTask.objects.create(
            name="Import from Trakt for testuser at daily",
            task="Import from Trakt",
            kwargs=f'{{"user_id": {self.user.id}, "username": "testuser"}}',
            crontab=self.crontab,
            enabled=True,
        )

        # Create a periodic task for another user
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

        # Check that the task was deleted
        with self.assertRaises(PeriodicTask.DoesNotExist):
            PeriodicTask.objects.get(id=self.task.id)

        # Check for success message
        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("Import schedule deleted", str(messages[0]))

        # Other user's task should still exist
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

        # Check for error message
        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("Import schedule not found", str(messages[0]))

        # Original task should still exist
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

        # Check for error message
        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("Import schedule not found", str(messages[0]))

        # Other user's task should still exist
        self.assertTrue(PeriodicTask.objects.filter(id=self.other_task.id).exists())


class RegenerateTokenTests(TestCase):
    """Tests for the regenerate_token view."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "testuser", "password": "testpass123"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        # Set initial token
        self.user.token = "initial_token"  # noqa: S105
        self.user.save()

    def test_regenerate_token(self):
        """Test token regeneration."""
        response = self.client.post(reverse("regenerate_token"))
        self.assertRedirects(response, reverse("integrations"))

        # Check that the token was changed
        self.user.refresh_from_db()
        self.assertNotEqual(self.user.token, "initial_token")
        self.assertIsNotNone(self.user.token)

        # Check for success message
        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("Token regenerated successfully", str(messages[0]))

    @patch("django.db.models.Model.save")
    def test_regenerate_token_integrity_error(self, mock_save):
        """Test token regeneration with an IntegrityError on first attempt."""
        # Configure mock to raise IntegrityError on first call, then succeed
        mock_save.side_effect = [
            IntegrityError("Duplicate token"),
            None,
        ]

        response = self.client.post(reverse("regenerate_token"))
        self.assertRedirects(response, reverse("integrations"))

        # Check that save was called twice (retry after IntegrityError)
        self.assertEqual(mock_save.call_count, 2)

        # Check for success message
        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("Token regenerated successfully", str(messages[0]))


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

        # Check usernames were updated and formatted correctly
        self.assertEqual(self.user.plex_usernames, "user1, user2, user3")

        # Check success message
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
        # Set initial usernames
        self.user.plex_usernames = "user1, user2"
        self.user.save()

        response = self.client.post(
            reverse("update_plex_usernames"),
            {"plex_usernames": ""},
        )

        self.user.refresh_from_db()
        self.assertEqual(self.user.plex_usernames, "")

        # Check success message
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

        # No message should be added
        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 0)
