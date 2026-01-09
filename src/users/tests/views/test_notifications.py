from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from app.models import Item, MediaTypes, Sources


class NotificationTests(TestCase):
    """Tests for notification functionality."""

    def setUp(self):
        """Set up test data."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

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

        self.user.refresh_from_db()
        self.assertEqual(
            self.user.notification_urls,
            "discord://webhook_id/webhook_token",
        )

        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("updated successfully", str(messages[0]))

    @patch("apprise.Apprise.add")
    def test_notifications_post_invalid(self, mock_add):
        """Test POST request with invalid data."""
        mock_add.return_value = False

        response = self.client.post(
            reverse("notifications"),
            {
                "notification_urls": "invalid://url",
            },
        )
        self.assertRedirects(response, reverse("notifications"))

        self.user.refresh_from_db()
        self.assertEqual(self.user.notification_urls, "")

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

        self.assertTrue(
            self.user.notification_excluded_items.filter(id=self.item1.id).exists(),
        )

    def test_include_item(self):
        """Test removing an item from exclusions."""
        self.user.notification_excluded_items.add(self.item1)

        response = self.client.post(
            reverse("include_notification_item"),
            {"item_id": self.item1.id},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "users/components/excluded_items.html")

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

        self.assertContains(response, "Test Anime")
        self.assertContains(response, "Test Manga")

        self.user.notification_excluded_items.add(self.item1)

        response = self.client.get(
            reverse("search_notification_items"),
            {"q": "Test"},
            HTTP_HX_REQUEST="true",
        )

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

        self.assertNotContains(response, "Test Anime")
        self.assertNotContains(response, "Test Manga")

    @patch("apprise.Apprise")
    def test_test_notification(self, mock_apprise):
        """Test the test notification endpoint."""
        self.user.notification_urls = "https://example.com/notify"
        self.user.save()

        mock_instance = MagicMock()
        mock_apprise.return_value = mock_instance
        mock_instance.notify.return_value = True

        response = self.client.get(reverse("test_notification"))

        self.assertRedirects(response, reverse("notifications"))

        mock_instance.notify.assert_called_once()

        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("successfully", str(messages[0]))

    def test_test_notification_no_urls(self):
        """Test the test notification endpoint with no URLs configured."""
        self.user.notification_urls = ""
        self.user.save()

        response = self.client.get(reverse("test_notification"))

        self.assertRedirects(response, reverse("notifications"))

        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("No notification URLs configured", str(messages[0]))

    @patch("apprise.Apprise")
    def test_test_notification_failure(self, mock_apprise):
        """Test the test notification endpoint when notification fails."""
        self.user.notification_urls = "https://example.com/notify"
        self.user.save()

        mock_instance = MagicMock()
        mock_apprise.return_value = mock_instance
        mock_instance.notify.return_value = False

        response = self.client.get(reverse("test_notification"))

        self.assertRedirects(response, reverse("notifications"))

        mock_instance.notify.assert_called_once()

        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("Failed", str(messages[0]))


