from unittest.mock import patch

from django.test import TestCase

from app.models import Item, MediaTypes, Sources
from app.providers import services
from events.calendar.comic import process_comic
from events.calendar.helpers import date_parser
from events.models import Event
from events.tests.calendar.utils import CalendarFixturesMixin


class CalendarComicTests(CalendarFixturesMixin, TestCase):
    """Test comic calendar processing."""

    @patch("events.calendar.comic.services.get_media_metadata")
    @patch("events.calendar.comic.comicvine.issue")
    def test_process_comic_with_store_date(self, mock_issue, mock_get_media_metadata):
        """Test process_comic with store date available."""
        comic_item = Item.objects.create(
            media_id="4050-18166",
            source=Sources.COMICVINE.value,
            media_type=MediaTypes.COMIC.value,
            title="Batman",
            image="http://example.com/batman.jpg",
        )

        mock_get_media_metadata.return_value = {
            "max_issue_number": 10,
            "last_issue_id": "4000-123456",
            "last_issue": {"issue_number": "10"},
        }

        mock_issue.return_value = {
            "store_date": "2023-04-15",
            "cover_date": "2023-05-01",
        }

        events_bulk = []
        process_comic(comic_item, events_bulk)

        self.assertEqual(len(events_bulk), 1)
        self.assertEqual(events_bulk[0].item, comic_item)
        self.assertEqual(events_bulk[0].content_number, 10)
        self.assertEqual(events_bulk[0].datetime, date_parser("2023-04-15"))
        mock_issue.assert_called_once_with("4000-123456")

    @patch("events.calendar.comic.services.get_media_metadata")
    @patch("events.calendar.comic.comicvine.issue")
    def test_process_comic_with_cover_date_only(
        self,
        mock_issue,
        mock_get_media_metadata,
    ):
        """Test process_comic with only cover date available."""
        comic_item = Item.objects.create(
            media_id="4050-18167",
            source=Sources.COMICVINE.value,
            media_type=MediaTypes.COMIC.value,
            title="Superman",
            image="http://example.com/superman.jpg",
        )

        mock_get_media_metadata.return_value = {
            "max_issue_number": 5,
            "last_issue_id": "4000-123457",
            "last_issue": {"issue_number": "5"},
        }

        mock_issue.return_value = {
            "store_date": None,
            "cover_date": "2023-05-01",
        }

        events_bulk = []
        process_comic(comic_item, events_bulk)

        self.assertEqual(len(events_bulk), 1)
        self.assertEqual(events_bulk[0].item, comic_item)
        self.assertEqual(events_bulk[0].content_number, 5)
        self.assertEqual(events_bulk[0].datetime, date_parser("2023-05-01"))

    @patch("events.calendar.comic.services.get_media_metadata")
    @patch("events.calendar.comic.comicvine.issue")
    def test_process_comic_no_dates(self, mock_issue, mock_get_media_metadata):
        """Test process_comic with no dates available."""
        comic_item = Item.objects.create(
            media_id="4050-18168",
            source=Sources.COMICVINE.value,
            media_type=MediaTypes.COMIC.value,
            title="Wonder Woman",
            image="http://example.com/wonderwoman.jpg",
        )

        mock_get_media_metadata.return_value = {
            "max_issue_number": 3,
            "last_issue_id": "4000-123458",
            "last_issue": {"issue_number": "3"},
        }

        mock_issue.return_value = {
            "store_date": None,
            "cover_date": None,
        }

        events_bulk = []
        process_comic(comic_item, events_bulk)

        self.assertEqual(len(events_bulk), 0)

    @patch("events.calendar.comic.services.get_media_metadata")
    def test_process_comic_returns_when_issue_is_already_saved(
        self,
        mock_get_media_metadata,
    ):
        """No new event should be added if the latest issue is already stored."""
        Event.objects.create(
            item=self.comic_item,
            content_number=10,
            datetime=date_parser("2023-04-15"),
        )
        mock_get_media_metadata.return_value = {
            "max_issue_number": 10,
            "last_issue_id": "4000-123456",
            "last_issue": {"issue_number": "10"},
        }

        events_bulk = []
        process_comic(self.comic_item, events_bulk)

        self.assertEqual(events_bulk, [])

    @patch("events.calendar.comic.services.get_media_metadata")
    @patch("events.calendar.comic.comicvine.issue")
    def test_process_comic_handles_issue_provider_error(
        self,
        mock_issue,
        mock_get_media_metadata,
    ):
        """Issue lookup failures should stop comic processing quietly."""
        response = type("Response", (), {"status_code": 500, "text": "boom"})()
        mock_get_media_metadata.return_value = {
            "max_issue_number": 10,
            "last_issue_id": "4000-123456",
            "last_issue": {"issue_number": "10"},
        }
        mock_issue.side_effect = services.ProviderAPIError(
            provider=Sources.COMICVINE.value,
            error=type("Error", (), {"response": response})(),
            details="boom",
        )

        events_bulk = []
        process_comic(self.comic_item, events_bulk)

        self.assertEqual(events_bulk, [])
