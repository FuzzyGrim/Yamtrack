from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.db import models
from django.test import TestCase, override_settings
from django.utils import formats, timezone

from app.models import TV, Anime, Item, Manga, MediaTypes, Season, Sources, Status
from events.models import Event
from events.notifications import (
    format_notification,
    get_all_user_tracking_data,
    get_tv_tracking_data,
    get_user_releases,
    is_user_tracking_item,
    send_daily_digest,
    send_notifications,
    send_releases,
)


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class NotificationTests(TestCase):
    """Tests for the notification system."""

    def setUp(self):
        """Set up test data."""
        # Create users
        self.credentials = {
            "username": "user1",
            "password": "12345",
            "notification_urls": "https://example.com/notify1",
        }
        self.user1 = get_user_model().objects.create_user(**self.credentials)

        self.credentials = {
            "username": "user2",
            "password": "12345",
            "notification_urls": "https://example.com/notify2",
        }
        self.user2 = get_user_model().objects.create_user(**self.credentials)

        self.credentials = {"username": "user3", "password": "12345"}
        self.user3 = get_user_model().objects.create_user(**self.credentials)

        # Create items
        self.anime_item = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Test Anime",
            image="http://example.com/anime.jpg",
        )

        self.manga_item = Item.objects.create(
            media_id="2",
            source=Sources.MAL.value,
            media_type=MediaTypes.MANGA.value,
            title="Test Manga",
            image="http://example.com/manga.jpg",
        )

        # Create TV show and season items
        self.tv_show_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test TV Show",
            image="http://example.com/tv.jpg",
        )

        self.season1_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test TV Show - Season 1",
            season_number=1,
            image="http://example.com/tv.jpg",
        )

        self.season2_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test TV Show - Season 2",
            season_number=2,
            image="http://example.com/tv.jpg",
        )

        self.season3_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test TV Show - Season 3",
            season_number=3,
            image="http://example.com/tv.jpg",
        )

        # Create media tracking
        Anime.objects.create(
            item=self.anime_item,
            user=self.user1,
            status=Status.IN_PROGRESS.value,
        )

        Anime.objects.create(
            item=self.anime_item,
            user=self.user2,
            status=Status.IN_PROGRESS.value,
        )

        Anime.objects.create(
            item=self.anime_item,
            user=self.user3,
            status=Status.IN_PROGRESS.value,
        )

        Manga.objects.create(
            item=self.manga_item,
            user=self.user1,
            status=Status.IN_PROGRESS.value,
        )

        Manga.objects.create(
            item=self.manga_item,
            user=self.user2,
            status=Status.PAUSED.value,
        )

        TV.objects.create(
            item=self.tv_show_item,
            user=self.user1,
            status=Status.IN_PROGRESS.value,
        )

        user2_tv = TV.objects.create(
            item=self.tv_show_item,
            user=self.user2,
            status=Status.IN_PROGRESS.value,
        )

        # Create season tracking - user2 drops season 2
        Season.objects.bulk_create(
            [
                Season(
                    item=self.season2_item,
                    related_tv=user2_tv,
                    user=self.user2,
                    status=Status.DROPPED.value,
                ),
            ],
        )

        # Create events
        now = timezone.now()
        ten_mins_ago = now - timedelta(minutes=10)

        self.anime_event = Event.objects.create(
            item=self.anime_item,
            content_number=5,
            datetime=ten_mins_ago,
            notification_sent=False,
        )

        self.manga_event = Event.objects.create(
            item=self.manga_item,
            content_number=10,
            datetime=ten_mins_ago,
            notification_sent=False,
        )

        self.season1_event = Event.objects.create(
            item=self.season1_item,
            content_number=5,
            datetime=ten_mins_ago,
            notification_sent=False,
        )

        self.season2_event = Event.objects.create(
            item=self.season2_item,
            content_number=3,
            datetime=ten_mins_ago,
            notification_sent=False,
        )

        self.season3_event = Event.objects.create(
            item=self.season3_item,
            content_number=1,
            datetime=ten_mins_ago,
            notification_sent=False,
        )

        # User1 excludes manga_item
        self.user1.notification_excluded_items.add(self.manga_item)

    @patch("events.notifications.send_notifications")
    def test_end_to_end_notification(self, mock_send_notifications):
        """Test the entire notification flow."""
        # Setup mock
        mock_send_notifications.return_value = {
            "event_count": 5,
            "event_ids": [
                self.anime_event.id,
                self.manga_event.id,
                self.season1_event.id,
                self.season2_event.id,
                self.season3_event.id,
            ],
        }

        # Run the task
        send_releases()

        # Verify events were marked as notified
        self.anime_event.refresh_from_db()
        self.manga_event.refresh_from_db()
        self.season1_event.refresh_from_db()
        self.season2_event.refresh_from_db()
        self.season3_event.refresh_from_db()

        self.assertTrue(self.anime_event.notification_sent)
        self.assertTrue(self.manga_event.notification_sent)
        self.assertTrue(self.season1_event.notification_sent)
        self.assertTrue(self.season2_event.notification_sent)
        self.assertTrue(self.season3_event.notification_sent)

        # Verify send_notifications was called
        mock_send_notifications.assert_called_once()

    @patch("events.notifications.send_notifications")
    def test_exclude_then_notify(self, mock_send_notifications):
        """Test excluding an item then verifying it's not in notifications."""
        # Create a second anime item
        item2 = Item.objects.create(
            media_id="100",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Another Anime",
            image="http://example.com/anime2.jpg",
        )

        # Track the second item
        Anime.objects.create(
            item=item2,
            user=self.user1,
            status=Status.IN_PROGRESS.value,
        )

        # Create event for the second item
        now = timezone.now()
        ten_mins_ago = now - timedelta(minutes=10)

        event2 = Event.objects.create(
            item=item2,
            content_number=3,
            datetime=ten_mins_ago,
            notification_sent=False,
        )

        # Setup mock
        mock_send_notifications.return_value = {
            "event_count": 6,
            "event_ids": [
                self.anime_event.id,
                self.manga_event.id,
                self.season1_event.id,
                self.season2_event.id,
                self.season3_event.id,
                event2.id,
            ],
        }

        # Exclude the first anime item
        self.user1.notification_excluded_items.add(self.anime_item)

        # Run the task
        send_releases()

        # Verify all events were marked as notified
        self.anime_event.refresh_from_db()
        event2.refresh_from_db()
        self.manga_event.refresh_from_db()
        self.assertTrue(self.anime_event.notification_sent)
        self.assertTrue(event2.notification_sent)
        self.assertTrue(self.manga_event.notification_sent)

    @patch("events.notifications.send_notifications")
    def test_no_users_with_notifications(self, mock_send_notifications):
        """Test behavior when no users have notification URLs configured."""
        # Setup mock
        mock_send_notifications.return_value = {
            "event_count": 5,
            "event_ids": [
                self.anime_event.id,
                self.manga_event.id,
                self.season1_event.id,
                self.season2_event.id,
                self.season3_event.id,
            ],
        }

        # Remove notification URLs from all users
        get_user_model().objects.all().update(notification_urls="")

        # Run the task
        send_releases()

        # Verify send_notifications was not called
        mock_send_notifications.assert_not_called()

    @patch("events.notifications.send_notifications")
    def test_multiple_media_types(self, mock_send_notifications):
        """Test notifications with multiple media types."""
        # Setup mock
        mock_send_notifications.return_value = {
            "event_count": 5,
            "event_ids": [
                self.anime_event.id,
                self.manga_event.id,
                self.season1_event.id,
                self.season2_event.id,
                self.season3_event.id,
            ],
        }

        # Remove user1's exclusion of manga_item
        self.user1.notification_excluded_items.remove(self.manga_item)

        # Run the task
        send_releases()

        # Verify all events were marked as notified
        self.anime_event.refresh_from_db()
        self.manga_event.refresh_from_db()
        self.season1_event.refresh_from_db()
        self.season2_event.refresh_from_db()
        self.season3_event.refresh_from_db()

        self.assertTrue(self.anime_event.notification_sent)
        self.assertTrue(self.manga_event.notification_sent)
        self.assertTrue(self.season1_event.notification_sent)
        self.assertTrue(self.season2_event.notification_sent)
        self.assertTrue(self.season3_event.notification_sent)

    @patch("events.notifications.send_notifications")
    def test_send_releases(self, mock_send_notifications):
        """Test the send_releases task."""
        # Setup mock
        mock_send_notifications.return_value = {
            "event_count": 5,
            "event_ids": [
                self.anime_event.id,
                self.manga_event.id,
                self.season1_event.id,
                self.season2_event.id,
                self.season3_event.id,
            ],
        }

        # Run the task
        send_releases()

        # Check that events were marked as notified
        self.anime_event.refresh_from_db()
        self.manga_event.refresh_from_db()
        self.season1_event.refresh_from_db()
        self.season2_event.refresh_from_db()
        self.season3_event.refresh_from_db()

        self.assertTrue(self.anime_event.notification_sent)
        self.assertTrue(self.manga_event.notification_sent)
        self.assertTrue(self.season1_event.notification_sent)
        self.assertTrue(self.season2_event.notification_sent)
        self.assertTrue(self.season3_event.notification_sent)

    def test_get_all_user_tracking_data(self):
        """Test the get_all_user_tracking_data function."""
        # Get users with notifications
        users_with_notifications = (
            get_user_model()
            .objects.filter(
                ~models.Q(notification_urls=""),
            )
            .prefetch_related("notification_excluded_items")
        )

        # Create target events dict
        target_events = {
            (
                self.anime_event.item.id,
                self.anime_event.content_number,
            ): self.anime_event,
            (
                self.manga_event.item.id,
                self.manga_event.content_number,
            ): self.manga_event,
            (
                self.season1_event.item.id,
                self.season1_event.content_number,
            ): self.season1_event,
            (
                self.season2_event.item.id,
                self.season2_event.content_number,
            ): self.season2_event,
        }

        # Create user exclusions dict
        user_exclusions = {}
        for user in users_with_notifications:
            user_exclusions[user.id] = set(
                user.notification_excluded_items.values_list("id", flat=True),
            )

        # Get tracking data
        tracking_data = get_all_user_tracking_data(
            users_with_notifications,
            target_events,
            user_exclusions,
        )

        # Verify results
        self.assertIsInstance(tracking_data, dict)

        # Check that user1's anime tracking is included
        anime_key = (self.user1.id, self.anime_item.id)
        self.assertIn(anime_key, tracking_data)

        # Check that user1's manga tracking is included
        manga_key = (self.user1.id, self.manga_item.id)
        self.assertIn(manga_key, tracking_data)

        # Check that season tracking data is included
        season1_key = (self.user1.id, self.season1_item.id)
        self.assertIn(season1_key, tracking_data)

    def test_get_tv_tracking_data(self):
        """Test the get_tv_tracking_data function."""
        users = [self.user1, self.user2]
        season_items = [self.season1_item, self.season2_item, self.season3_item]

        # Create user exclusions dict
        user_exclusions = {
            self.user1.id: set(),
            self.user2.id: set(),
        }

        # Get TV tracking data
        tracking_data = get_tv_tracking_data(users, season_items, user_exclusions)

        # Verify results
        self.assertIsInstance(tracking_data, dict)

        # User1 should be tracking all seasons (no dropped seasons)
        user1_season1_key = (self.user1.id, self.season1_item.id)
        user1_season2_key = (self.user1.id, self.season2_item.id)
        user1_season3_key = (self.user1.id, self.season3_item.id)

        self.assertTrue(tracking_data[user1_season1_key])
        self.assertTrue(tracking_data[user1_season2_key])
        self.assertTrue(tracking_data[user1_season3_key])

        # User2 should be tracking season 1 but not seasons 2 and 3 (dropped season 2)
        user2_season1_key = (self.user2.id, self.season1_item.id)
        user2_season2_key = (self.user2.id, self.season2_item.id)
        user2_season3_key = (self.user2.id, self.season3_item.id)

        self.assertTrue(tracking_data[user2_season1_key])
        self.assertFalse(tracking_data[user2_season2_key])
        self.assertFalse(tracking_data[user2_season3_key])

    def test_get_tv_tracking_data_with_inactive_tv_show(self):
        """Test get_tv_tracking_data when TV show is inactive."""
        # Create a user with dropped TV show
        credentials = {
            "username": "user4",
            "password": "12345",
        }
        user4 = get_user_model().objects.create_user(**credentials)

        # Create TV show item and mark as dropped
        tv_item = Item.objects.create(
            media_id="1396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Dropped TV Show",
            image="http://example.com/tv2.jpg",
        )

        season_item = Item.objects.create(
            media_id="1396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Dropped TV Show - Season 1",
            season_number=1,
            image="http://example.com/tv2.jpg",
        )

        TV.objects.create(
            item=tv_item,
            user=user4,
            status=Status.DROPPED.value,
        )

        users = [user4]
        season_items = [season_item]
        user_exclusions = {user4.id: set()}

        # Get TV tracking data
        tracking_data = get_tv_tracking_data(users, season_items, user_exclusions)

        # User should not be tracking the season since TV show is dropped
        season_key = (user4.id, season_item.id)
        self.assertNotIn(season_key, tracking_data)

    def test_get_tv_tracking_data_with_excluded_items(self):
        """Test get_tv_tracking_data with excluded items."""
        users = [self.user1]
        season_items = [self.season1_item]

        # Exclude the TV show item
        user_exclusions = {
            self.user1.id: {self.tv_show_item.id},
        }

        # Get TV tracking data
        tracking_data = get_tv_tracking_data(users, season_items, user_exclusions)

        # User should not be tracking the season since TV show is excluded
        season_key = (self.user1.id, self.season1_item.id)
        self.assertNotIn(season_key, tracking_data)

    def test_get_tv_tracking_data_empty_season_items(self):
        """Test get_tv_tracking_data with empty season items."""
        users = [self.user1, self.user2]
        season_items = []
        user_exclusions = {
            self.user1.id: set(),
            self.user2.id: set(),
        }

        # Get TV tracking data
        tracking_data = get_tv_tracking_data(users, season_items, user_exclusions)

        # Should return empty dict
        self.assertEqual(tracking_data, {})

    def test_get_user_releases(self):
        """Test the get_user_releases function."""
        # Get users with notifications
        users_with_notifications = (
            get_user_model()
            .objects.filter(
                ~models.Q(notification_urls=""),
            )
            .prefetch_related("notification_excluded_items")
        )

        # Create target events dict
        target_events = {
            (
                self.anime_event.item.id,
                self.anime_event.content_number,
            ): self.anime_event,
            (
                self.manga_event.item.id,
                self.manga_event.content_number,
            ): self.manga_event,
            (
                self.season1_event.item.id,
                self.season1_event.content_number,
            ): self.season1_event,
            (
                self.season2_event.item.id,
                self.season2_event.content_number,
            ): self.season2_event,
        }

        # Get user releases
        user_releases = get_user_releases(users_with_notifications, target_events)

        # Verify results
        self.assertIn(self.user1.id, user_releases)
        self.assertIn(self.user2.id, user_releases)

        # User1 should have anime and season events (manga is excluded)
        user1_events = user_releases[self.user1.id]
        anime_event_found = any(
            event.id == self.anime_event.id for event in user1_events
        )
        manga_event_found = any(
            event.id == self.manga_event.id for event in user1_events
        )
        season1_event_found = any(
            event.id == self.season1_event.id for event in user1_events
        )

        self.assertTrue(anime_event_found)
        self.assertFalse(manga_event_found)
        self.assertTrue(season1_event_found)

        # User2 should have anime and season1 events
        # manga is paused, season2 is dropped
        user2_events = user_releases[self.user2.id]
        anime_event_found = any(
            event.id == self.anime_event.id for event in user2_events
        )
        manga_event_found = any(
            event.id == self.manga_event.id for event in user2_events
        )
        season1_event_found = any(
            event.id == self.season1_event.id for event in user2_events
        )
        season2_event_found = any(
            event.id == self.season2_event.id for event in user2_events
        )

        self.assertTrue(anime_event_found)
        self.assertFalse(manga_event_found)
        self.assertTrue(season1_event_found)
        self.assertFalse(season2_event_found)

    def test_is_user_tracking_item(self):
        """Test the is_user_tracking_item function."""
        # Create tracking data
        users = [self.user1, self.user2]
        target_events = {
            (
                self.anime_event.item.id,
                self.anime_event.content_number,
            ): self.anime_event,
            (
                self.manga_event.item.id,
                self.manga_event.content_number,
            ): self.manga_event,
            (
                self.season1_event.item.id,
                self.season1_event.content_number,
            ): self.season1_event,
        }

        user_exclusions = {
            self.user1.id: set(),
            self.user2.id: set(),
        }

        tracking_data = get_all_user_tracking_data(
            users,
            target_events,
            user_exclusions,
        )

        # Test user1 tracking anime (should be True)
        result = is_user_tracking_item(self.user1, self.anime_item, tracking_data)
        self.assertTrue(result)

        # Test user1 tracking manga (should be True - status is IN_PROGRESS)
        result = is_user_tracking_item(self.user1, self.manga_item, tracking_data)
        self.assertTrue(result)

        # Test user2 tracking anime (should be True)
        result = is_user_tracking_item(self.user2, self.anime_item, tracking_data)
        self.assertTrue(result)

        # Test user2 tracking manga (should be False - status is PAUSED)
        result = is_user_tracking_item(self.user2, self.manga_item, tracking_data)
        self.assertFalse(result)

        # Test user1 tracking season1 (should be True)
        result = is_user_tracking_item(self.user1, self.season1_item, tracking_data)
        self.assertTrue(result)

    @patch("apprise.Apprise")
    def test_send_notifications(self, mock_apprise):
        """Test the send_notifications function."""
        # Setup mock
        mock_instance = MagicMock()
        mock_apprise.return_value = mock_instance
        mock_instance.notify.return_value = True

        # Get users with notifications
        users_with_notifications = (
            get_user_model()
            .objects.filter(
                ~models.Q(notification_urls=""),
            )
            .prefetch_related("notification_excluded_items")
        )

        # Get recent events
        recent_events = Event.objects.filter(
            notification_sent=False,
        ).select_related("item")

        # Call function
        result = send_notifications(
            recent_events,
            users_with_notifications,
            "Test Title",
        )

        # Verify result
        self.assertIn("event_count", result)
        self.assertIn("event_ids", result)
        self.assertEqual(result["event_count"], 5)
        self.assertEqual(len(result["event_ids"]), 5)

    def test_format_notification(self):
        """Test the format_notification function."""
        # Test with multiple media types
        releases = [self.anime_event, self.manga_event, self.season1_event]
        notification_text = format_notification(releases)

        # Verify text contains expected content
        self.assertIn("ANIME", notification_text)
        self.assertIn("MANGA", notification_text)
        self.assertIn("TV Shows", notification_text)
        self.assertIn("Test Anime", notification_text)
        self.assertIn("Test Manga", notification_text)
        self.assertIn("Test TV Show", notification_text)
        self.assertIn("E5", notification_text)
        self.assertIn("#10", notification_text)

        # Test with single media type
        releases = [self.anime_event]
        notification_text = format_notification(releases)

        # Verify text contains expected content
        self.assertIn("ANIME", notification_text)
        self.assertIn("Test Anime", notification_text)
        self.assertIn("E5", notification_text)
        self.assertNotIn("MANGA", notification_text)
        self.assertNotIn("Test Manga", notification_text)

    @patch("events.notifications.send_notifications")
    def test_no_recent_events(self, mock_send_notifications):
        """Test behavior when no recent events are found."""
        # Mark all events as notified
        Event.objects.all().update(notification_sent=True)

        # Run the task
        result = send_releases()

        # Verify send_notifications was not called
        mock_send_notifications.assert_not_called()

        # Verify the result message
        self.assertEqual(result, "No recent releases found")

    def test_user_exclusion(self):
        """Test that user exclusions are respected."""
        # Get users with notifications
        users_with_notifications = (
            get_user_model()
            .objects.filter(
                ~models.Q(notification_urls=""),
            )
            .prefetch_related("notification_excluded_items")
        )

        # Create target events dict
        target_events = {
            (
                self.anime_event.item.id,
                self.anime_event.content_number,
            ): self.anime_event,
            (
                self.manga_event.item.id,
                self.manga_event.content_number,
            ): self.manga_event,
        }

        # Get user releases
        user_releases = get_user_releases(users_with_notifications, target_events)

        # Verify user1 doesn't get manga notifications (excluded)
        user1_events = user_releases[self.user1.id]
        manga_event_found = any(
            event.id == self.manga_event.id for event in user1_events
        )
        self.assertFalse(manga_event_found)

    def test_future_events_not_included(self):
        """Test that future events are not included in notifications."""
        # Create a future event
        now = timezone.now()
        one_hour_ahead = now + timedelta(hours=1)

        future_event = Event.objects.create(
            item=self.anime_item,
            content_number=6,
            datetime=one_hour_ahead,
            notification_sent=False,
        )

        # Run the task
        send_releases()

        # Future event should not be marked as notified
        future_event.refresh_from_db()
        self.assertFalse(future_event.notification_sent)

    @patch("apprise.Apprise")
    def test_exception_during_notification(self, mock_apprise):
        """Test handling of exceptions during notification."""
        # Setup mock to raise exception
        mock_instance = MagicMock()
        mock_apprise.return_value = mock_instance
        mock_instance.notify.side_effect = Exception("Test exception")

        # Get users with notifications
        users_with_notifications = (
            get_user_model()
            .objects.filter(
                ~models.Q(notification_urls=""),
            )
            .prefetch_related("notification_excluded_items")
        )

        # Get recent events
        recent_events = Event.objects.filter(
            notification_sent=False,
        ).select_related("item")

        # Call function - should not propagate exception
        result = send_notifications(
            recent_events,
            users_with_notifications,
            "Test Title",
        )

        # Verify result still contains expected data
        self.assertIn("event_count", result)
        self.assertIn("event_ids", result)

    @patch("events.notifications.send_notifications")
    def test_release_notifications_disabled(self, mock_send_notifications):
        """Test that users with disabled release_notifications_enabled."""
        # Setup mock
        mock_send_notifications.return_value = {
            "event_count": 5,
            "event_ids": [
                self.anime_event.id,
                self.manga_event.id,
                self.season1_event.id,
                self.season2_event.id,
                self.season3_event.id,
            ],
        }

        # Disable release notifications for user1
        self.user1.release_notifications_enabled = False
        self.user1.save()

        # Run the task
        send_releases()

        # Verify send_notifications was called
        mock_send_notifications.assert_called_once()

        # Get the arguments passed to send_notifications
        users = mock_send_notifications.call_args[1]["users"]

        # Verify user1 is not in users
        user_ids = [user.id for user in users]
        self.assertNotIn(self.user1.id, user_ids)

        # Verify user2 is still in users
        self.assertIn(self.user2.id, user_ids)

    @patch("events.notifications.send_notifications")
    def test_all_users_notifications_disabled(self, mock_send_notifications):
        """Test behavior when all users have notifications disabled."""
        # Setup mock
        mock_send_notifications.return_value = {}

        # Disable release notifications for all users
        get_user_model().objects.all().update(release_notifications_enabled=False)

        # Run the task
        send_releases()

        # Verify send_notifications was not called
        mock_send_notifications.assert_not_called()

    @patch("events.notifications.send_notifications")
    def test_send_daily_digest(self, mock_send_notifications):
        """Test the send_daily_digest task."""
        # Setup mock
        mock_send_notifications.return_value = {
            "event_count": 5,
            "event_ids": [
                self.anime_event.id,
                self.manga_event.id,
                self.season1_event.id,
                self.season2_event.id,
                self.season3_event.id,
            ],
        }

        # Set events to today
        now = timezone.localtime()
        today = now.replace(hour=12, minute=0, second=0, microsecond=0)

        self.anime_event.datetime = today
        self.anime_event.save()

        self.manga_event.datetime = today
        self.manga_event.save()

        self.season1_event.datetime = today
        self.season1_event.save()

        self.season2_event.datetime = today
        self.season2_event.save()

        self.season3_event.datetime = today
        self.season3_event.save()

        # Enable daily digest for users
        self.user1.daily_digest_enabled = True
        self.user1.save()

        self.user2.daily_digest_enabled = True
        self.user2.save()

        # Run the task
        result = send_daily_digest()

        # Verify send_notifications was called
        mock_send_notifications.assert_called_once()

        # Check the title contains today's date
        title = mock_send_notifications.call_args[1]["title"]
        today_str = formats.date_format(
            today.date(),
            "DATE_FORMAT",
        )
        self.assertIn(today_str, title)

        # Verify the result message
        self.assertEqual(result, "Daily digest sent for 5 releases")

    @patch("events.notifications.send_notifications")
    def test_daily_digest_no_releases(self, mock_send_notifications):
        """Test daily digest when no releases are scheduled for today."""
        # Set events to tomorrow
        now = timezone.now()
        tomorrow = now + timedelta(days=1)

        self.anime_event.datetime = tomorrow
        self.anime_event.save()

        self.manga_event.datetime = tomorrow
        self.manga_event.save()

        self.season1_event.datetime = tomorrow
        self.season1_event.save()

        self.season2_event.datetime = tomorrow
        self.season2_event.save()

        self.season3_event.datetime = tomorrow
        self.season3_event.save()

        # Enable daily digest for users
        self.user1.daily_digest_enabled = True
        self.user1.save()

        # Run the task
        result = send_daily_digest()

        # Verify send_notifications was not called
        mock_send_notifications.assert_not_called()

        # Verify the result message
        self.assertEqual(result, "No releases scheduled for today")

    @patch("events.notifications.send_notifications")
    def test_daily_digest_no_users(self, mock_send_notifications):
        """Test daily digest when no users have it enabled."""
        # Set events to today
        now = timezone.now()
        today = now.replace(hour=12, minute=0, second=0, microsecond=0)

        self.anime_event.datetime = today
        self.anime_event.save()

        # Make sure daily digest is disabled for all users
        get_user_model().objects.all().update(daily_digest_enabled=False)

        # Run the task
        result = send_daily_digest()

        # Verify send_notifications was not called
        mock_send_notifications.assert_not_called()

        # Verify the result message
        self.assertEqual(result, "No users with daily digest enabled")

    @patch("events.notifications.send_notifications")
    def test_daily_digest_excluded_items(self, mock_send_notifications):
        """Test daily digest respects excluded items."""
        # Setup mock
        mock_send_notifications.return_value = {
            "event_count": 4,
            "event_ids": [
                self.anime_event.id,
                self.season1_event.id,
                self.season2_event.id,
                self.season3_event.id,
            ],
        }

        # Set events to today
        now = timezone.localtime()
        today = now.replace(hour=12, minute=0, second=0, microsecond=0)

        self.anime_event.datetime = today
        self.anime_event.save()

        self.manga_event.datetime = today
        self.manga_event.save()

        self.season1_event.datetime = today
        self.season1_event.save()

        self.season2_event.datetime = today
        self.season2_event.save()

        self.season3_event.datetime = today
        self.season3_event.save()

        # Enable daily digest for user1
        self.user1.daily_digest_enabled = True
        self.user1.save()

        # User1 already excludes manga_item from setUp

        # Run the task
        result = send_daily_digest()

        # Verify send_notifications was called
        mock_send_notifications.assert_called_once()

        # Verify the result message
        self.assertEqual(result, "Daily digest sent for 4 releases")

    @patch("events.notifications.send_notifications")
    def test_daily_digest_timezone_handling(self, mock_send_notifications):
        """Test daily digest handles timezones correctly."""
        # Setup mock
        mock_send_notifications.return_value = {
            "event_count": 5,
            "event_ids": [
                self.anime_event.id,
                self.manga_event.id,
                self.season1_event.id,
                self.season2_event.id,
                self.season3_event.id,
            ],
        }

        # Get current date in the timezone defined in settings
        now_in_current_tz = timezone.localtime()

        # Create a time that's today in the current timezone
        today_in_current_tz = now_in_current_tz.replace(
            hour=12,
            minute=0,
            second=0,
            microsecond=0,
        )

        # Set events to today in the current timezone
        self.anime_event.datetime = today_in_current_tz
        self.anime_event.save()

        self.manga_event.datetime = today_in_current_tz
        self.manga_event.save()

        self.season1_event.datetime = today_in_current_tz
        self.season1_event.save()

        self.season2_event.datetime = today_in_current_tz
        self.season2_event.save()

        self.season3_event.datetime = today_in_current_tz
        self.season3_event.save()

        # Enable daily digest for users
        self.user1.daily_digest_enabled = True
        self.user1.save()

        # Run the task
        result = send_daily_digest()

        # Verify send_notifications was called
        mock_send_notifications.assert_called_once()

        # Verify the result message
        self.assertEqual(result, "Daily digest sent for 5 releases")

    @patch("events.notifications.send_notifications")
    def test_daily_digest_with_notification_urls(self, mock_send_notifications):
        """Test daily digest only sends to users with notification URLs."""
        # Setup mock
        mock_send_notifications.return_value = {
            "event_count": 5,
            "event_ids": [
                self.anime_event.id,
                self.manga_event.id,
                self.season1_event.id,
                self.season2_event.id,
                self.season3_event.id,
            ],
        }

        # Set events to today
        now = timezone.localtime()
        today = now.replace(hour=12, minute=0, second=0, microsecond=0)

        self.anime_event.datetime = today
        self.anime_event.save()

        self.manga_event.datetime = today
        self.manga_event.save()

        self.season1_event.datetime = today
        self.season1_event.save()

        self.season2_event.datetime = today
        self.season2_event.save()

        self.season3_event.datetime = today
        self.season3_event.save()

        # Enable daily digest for all users
        get_user_model().objects.all().update(daily_digest_enabled=True)

        # Remove notification URL from user2
        self.user2.notification_urls = ""
        self.user2.save()

        # Run the task
        result = send_daily_digest()

        # Verify send_notifications was called
        mock_send_notifications.assert_called_once()

        # Check that only user1 is included
        users = mock_send_notifications.call_args[1]["users"]
        user_ids = [user.id for user in users]
        self.assertIn(self.user1.id, user_ids)
        self.assertNotIn(self.user2.id, user_ids)

        # Verify the result message
        self.assertEqual(result, "Daily digest sent for 5 releases")
