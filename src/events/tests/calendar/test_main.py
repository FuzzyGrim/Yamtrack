from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from app.models import Item, Sources
from events.calendar.main import cleanup_invalid_events, fetch_releases, save_events
from events.models import Event
from events.tests.calendar.utils import CalendarFixturesMixin


class CalendarMainTests(CalendarFixturesMixin, TestCase):
    """Test the calendar orchestration entrypoint."""

    @patch("events.calendar.selectors.tmdb.movie_changes")
    @patch("events.calendar.selectors.tmdb.tv_changes")
    @patch("events.calendar.main.process_comic")
    @patch("events.calendar.main.process_tv")
    @patch("events.calendar.main.process_other")
    @patch("events.calendar.main.process_anime_bulk")
    def test_fetch_releases_all_types(
        self,
        mock_process_anime_bulk,
        mock_process_other,
        mock_process_tv,
        mock_process_comic,
        mock_movie_changes,
        mock_tv_changes,
    ):
        """Test fetch_releases with all media types."""
        mock_tv_changes.return_value = set()
        mock_movie_changes.return_value = set()

        mock_process_tv.side_effect = lambda _, events_bulk: events_bulk.append(
            Event(
                item=self.season_item,
                content_number=1,
                datetime=timezone.now(),
            ),
        )
        mock_process_other.side_effect = lambda item, events_bulk: events_bulk.append(
            Event(
                item=item,
                content_number=1,
                datetime=timezone.now(),
            ),
        )
        mock_process_comic.side_effect = lambda item, events_bulk: events_bulk.append(
            Event(
                item=item,
                content_number=1,
                datetime=timezone.now(),
            ),
        )
        mock_process_anime_bulk.side_effect = lambda items, events_bulk: [
            events_bulk.append(
                Event(
                    item=item,
                    content_number=1,
                    datetime=timezone.now(),
                ),
            )
            for item in items
        ]

        result = fetch_releases(self.user.id)

        mock_process_anime_bulk.assert_called_once()
        anime_items = mock_process_anime_bulk.call_args[0][0]
        self.assertEqual(len(anime_items), 1)
        self.assertEqual(anime_items[0].id, self.anime_item.id)

        self.assertTrue(Event.objects.filter(item=self.season_item).exists())
        self.assertEqual(mock_process_other.call_count, 3)

        self.assertTrue(Event.objects.filter(item=self.anime_item).exists())
        self.assertTrue(Event.objects.filter(item=self.movie_item).exists())
        self.assertTrue(Event.objects.filter(item=self.manga_item).exists())
        self.assertTrue(Event.objects.filter(item=self.book_item).exists())
        self.assertTrue(Event.objects.filter(item=self.comic_item).exists())

        self.assertIn("Perfect Blue", result)
        self.assertIn("The Godfather", result)
        self.assertIn("Breaking Bad", result)
        self.assertIn("Berserk", result)
        self.assertIn("1984", result)

    @patch("events.calendar.main.process_other")
    def test_fetch_releases_specific_items(self, mock_process_other):
        """Test fetch_releases with specific items to process."""
        mock_process_other.side_effect = lambda item, events_bulk: events_bulk.append(
            Event(
                item=item,
                content_number=1,
                datetime=timezone.now(),
            ),
        )

        items_to_process = [self.movie_item, self.book_item]
        result = fetch_releases(self.user.id, items_to_process)

        self.assertEqual(mock_process_other.call_count, 2)

        self.assertFalse(Event.objects.filter(item=self.anime_item).exists())
        self.assertTrue(Event.objects.filter(item=self.movie_item).exists())
        self.assertFalse(Event.objects.filter(item=self.season_item).exists())
        self.assertFalse(Event.objects.filter(item=self.manga_item).exists())
        self.assertTrue(Event.objects.filter(item=self.book_item).exists())

        self.assertIn("The Godfather", result)
        self.assertIn("1984", result)
        self.assertNotIn("Perfect Blue", result)
        self.assertNotIn("Breaking Bad", result)
        self.assertNotIn("Berserk", result)

    def test_fetch_releases_returns_for_manual_items(self):
        """Manual items should be rejected before any processing."""
        manual_item = Item(source=Sources.MANUAL.value)

        result = fetch_releases(items_to_process=[manual_item])

        self.assertEqual(result, "Manual sources are not processed")

    @patch("events.calendar.main.get_items_to_process")
    def test_fetch_releases_returns_when_no_items_to_process(
        self,
        mock_get_items_to_process,
    ):
        """The task should return early when nothing is eligible."""
        mock_get_items_to_process.return_value = []

        result = fetch_releases(self.user.id)

        self.assertEqual(result, "No items to process")

    def test_save_events_updates_existing_unnumbered_event(self):
        """Existing unnumbered events should be updated in place."""
        original_datetime = timezone.now() - timezone.timedelta(days=2)
        updated_datetime = timezone.now() + timezone.timedelta(days=2)
        Event.objects.create(
            item=self.movie_item,
            content_number=None,
            datetime=original_datetime,
        )

        save_events(
            [
                Event(
                    item=self.movie_item,
                    content_number=None,
                    datetime=updated_datetime,
                ),
            ],
        )

        self.assertEqual(
            Event.objects.filter(item=self.movie_item).count(),
            1,
        )
        self.assertEqual(
            Event.objects.get(item=self.movie_item).datetime,
            updated_datetime,
        )

    def test_cleanup_invalid_events_removes_missing_numbered_events(self):
        """Stale numbered events should be removed after a refresh."""
        kept_datetime = timezone.now()
        removed_datetime = timezone.now() + timezone.timedelta(days=1)
        Event.objects.create(
            item=self.season_item,
            content_number=1,
            datetime=kept_datetime,
        )
        Event.objects.create(
            item=self.season_item,
            content_number=2,
            datetime=removed_datetime,
        )

        cleanup_invalid_events(
            [
                Event(
                    item=self.season_item,
                    content_number=1,
                    datetime=kept_datetime,
                ),
            ],
        )

        self.assertTrue(
            Event.objects.filter(item=self.season_item, content_number=1).exists(),
        )
        self.assertFalse(
            Event.objects.filter(item=self.season_item, content_number=2).exists(),
        )

    @patch("events.calendar.main.process_other")
    def test_fetch_releases_returns_no_updates_message(self, mock_process_other):
        """The final message should mention when nothing was changed."""
        result = fetch_releases(items_to_process=[self.movie_item])

        mock_process_other.assert_called_once()
        self.assertIn("No releases have been updated.", result)
