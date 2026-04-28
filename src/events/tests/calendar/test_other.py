import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from django.test import TestCase

from app.models import Item, MediaTypes, Sources
from app.providers import services
from events.calendar.helpers import date_parser
from events.calendar.other import process_other
from events.tests.calendar.utils import CalendarFixturesMixin


class CalendarOtherTests(CalendarFixturesMixin, TestCase):
    """Test generic calendar processing."""

    @patch("events.calendar.other.services.get_media_metadata")
    def test_process_other_movie(self, mock_get_media_metadata):
        """Test process_other for a movie."""
        mock_get_media_metadata.return_value = {
            "max_progress": 1,
            "details": {
                "release_date": "1999-10-15",
            },
        }

        events_bulk = []
        process_other(self.movie_item, events_bulk)

        self.assertEqual(len(events_bulk), 1)
        self.assertEqual(events_bulk[0].item, self.movie_item)
        self.assertIsNone(events_bulk[0].content_number)
        self.assertEqual(events_bulk[0].datetime, date_parser("1999-10-15"))

    @patch("events.calendar.other.services.get_media_metadata")
    def test_process_other_book(self, mock_get_media_metadata):
        """Test process_other for a book."""
        mock_get_media_metadata.return_value = {
            "max_progress": 328,
            "details": {
                "publish_date": "1949-06-08",
            },
        }

        events_bulk = []
        process_other(self.book_item, events_bulk)

        self.assertEqual(len(events_bulk), 1)
        self.assertEqual(events_bulk[0].item, self.book_item)
        self.assertEqual(events_bulk[0].content_number, 328)
        self.assertEqual(events_bulk[0].datetime, date_parser("1949-06-08"))

    @patch("events.calendar.other.services.get_media_metadata")
    def test_process_other_manga(self, mock_get_media_metadata):
        """Test process_other for manga."""
        mock_get_media_metadata.return_value = {
            "details": {
                "end_date": "2023-12-22",
            },
            "max_progress": 375,
        }

        events_bulk = []
        process_other(self.manga_item, events_bulk)

        self.assertEqual(len(events_bulk), 1)
        self.assertEqual(events_bulk[0].item, self.manga_item)
        self.assertEqual(events_bulk[0].content_number, 375)
        self.assertEqual(events_bulk[0].datetime, date_parser("2023-12-22"))

    @patch("events.calendar.other.services.get_media_metadata")
    def test_process_other_mangaupdates(self, mock_get_media_metadata):
        """Test process_other for MangaUpdates manga."""
        mangaupdates_item = Item.objects.create(
            media_id="123",
            source=Sources.MANGAUPDATES.value,
            media_type=MediaTypes.MANGA.value,
            title="Some Manga",
            image="http://example.com/manga.jpg",
        )

        mock_get_media_metadata.return_value = {
            "max_progress": 100,
            "details": {},
        }

        events_bulk = []
        process_other(mangaupdates_item, events_bulk)

        self.assertEqual(len(events_bulk), 1)
        self.assertEqual(events_bulk[0].item, mangaupdates_item)
        self.assertEqual(events_bulk[0].content_number, 100)
        expected_date = datetime.datetime.min.replace(tzinfo=ZoneInfo("UTC"))
        self.assertEqual(events_bulk[0].datetime, expected_date)

    @patch("events.calendar.other.services.get_media_metadata")
    def test_process_other_uses_placeholder_when_date_is_unknown(
        self,
        mock_get_media_metadata,
    ):
        """A known max progress with an empty date should use a placeholder."""
        mock_get_media_metadata.return_value = {
            "max_progress": 328,
            "details": {
                "publish_date": "",
            },
        }

        events_bulk = []
        process_other(self.book_item, events_bulk)

        self.assertEqual(len(events_bulk), 1)
        self.assertEqual(
            events_bulk[0].datetime,
            datetime.datetime.min.replace(tzinfo=ZoneInfo("UTC")),
        )

    @patch("events.calendar.other.services.get_media_metadata")
    def test_process_other_game(self, mock_get_media_metadata):
        """Test process_other for a game."""
        game_item = Item.objects.create(
            media_id="52189",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Grand Theft Auto VI",
            image="http://example.com/gta6.jpg",
        )

        mock_get_media_metadata.return_value = {
            "max_progress": None,
            "details": {
                "release_date": "2025-10-15",
            },
        }

        events_bulk = []
        process_other(game_item, events_bulk)

        self.assertEqual(len(events_bulk), 1)
        self.assertEqual(events_bulk[0].item, game_item)
        self.assertIsNone(events_bulk[0].content_number)
        self.assertEqual(events_bulk[0].datetime, date_parser("2025-10-15"))

    @patch("events.calendar.other.services.get_media_metadata")
    def test_process_other_invalid_date(self, mock_get_media_metadata):
        """Test process_other with invalid date."""
        mock_get_media_metadata.return_value = {
            "max_progress": None,
            "details": {
                "release_date": "invalid-date",
            },
        }

        events_bulk = []
        process_other(self.movie_item, events_bulk)

        self.assertEqual(len(events_bulk), 0)

    @patch("events.calendar.other.services.get_media_metadata")
    def test_process_other_no_date(self, mock_get_media_metadata):
        """Test process_other with no date."""
        mock_get_media_metadata.return_value = {
            "max_progress": None,
            "details": {},
        }

        events_bulk = []
        process_other(self.movie_item, events_bulk)

        self.assertEqual(len(events_bulk), 0)

    @patch("events.calendar.other.services.get_media_metadata")
    def test_process_other_handles_provider_error(self, mock_get_media_metadata):
        """Provider failures should not raise from process_other."""
        response_mock = MagicMock()
        response_mock.status_code = 500
        response_mock.text = "boom"
        mock_get_media_metadata.side_effect = services.ProviderAPIError(
            provider=Sources.TMDB.value,
            error=response_mock,
            details="boom",
        )

        events_bulk = []
        process_other(self.movie_item, events_bulk)

        self.assertEqual(len(events_bulk), 0)

    @patch("app.providers.tmdb.movie")
    def test_http_error_handling(self, mock_tmdb_movie):
        """Test handling of ProviderAPIError in process_other."""
        response_mock = MagicMock()
        response_mock.status_code = 404
        response_mock.text = "Not found"

        mock_tmdb_movie.side_effect = services.ProviderAPIError(
            provider=Sources.TMDB.value,
            error=response_mock,
            details="Movie not found",
        )

        events_bulk = []
        process_other(self.movie_item, events_bulk)

        self.assertEqual(len(events_bulk), 0)
