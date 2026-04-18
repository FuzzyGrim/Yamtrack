import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase

from events.calendar.anime import (
    anilist_date_parser,
    get_anime_schedule_bulk,
    process_anime_bulk,
)
from events.tests.calendar.utils import CalendarFixturesMixin


class CalendarAnimeTests(CalendarFixturesMixin, TestCase):
    """Test anime calendar processing."""

    @patch("events.calendar.anime.services.api_request")
    def test_get_anime_schedule_bulk(self, mock_api_request):
        """Test get_anime_schedule_bulk function."""
        mock_api_request.return_value = {
            "data": {
                "Page": {
                    "pageInfo": {"hasNextPage": False},
                    "media": [
                        {
                            "idMal": 437,
                            "startDate": {"year": 1997, "month": 8, "day": 5},
                            "endDate": {"year": 1997, "month": 8, "day": 5},
                            "episodes": 1,
                            "airingSchedule": {
                                "nodes": [
                                    {"episode": 1, "airingAt": 870739200},
                                ],
                            },
                        },
                    ],
                },
            },
        }

        result = get_anime_schedule_bulk(["437"])

        self.assertIn("437", result)
        self.assertEqual(len(result["437"]), 1)
        self.assertEqual(result["437"][0]["episode"], 1)
        self.assertEqual(result["437"][0]["airingAt"], 870739200)

    @patch("events.calendar.anime.services.get_media_metadata")
    @patch("events.calendar.anime.services.api_request")
    def test_get_anime_schedule_bulk_no_airing_schedule(
        self,
        mock_api_request,
        mock_get_media_metadata,
    ):
        """Test get_anime_schedule_bulk with no airing schedule."""
        mock_api_request.return_value = {
            "data": {
                "Page": {
                    "pageInfo": {"hasNextPage": False},
                    "media": [
                        {
                            "idMal": 437,
                            "endDate": {"year": 1997, "month": 8, "day": 12},
                            "episodes": 2,
                            "airingSchedule": {"nodes": []},
                        },
                    ],
                },
            },
        }

        mock_get_media_metadata.return_value = {
            "max_progress": 2,
            "details": {"end_date": "1997-08-12"},
        }

        result = get_anime_schedule_bulk(["437"])

        self.assertIn("437", result)
        self.assertEqual(len(result["437"]), 1)
        self.assertEqual(result["437"][0]["episode"], 2)

        start_date = datetime.datetime.fromtimestamp(
            result["437"][0]["airingAt"],
            tz=ZoneInfo("UTC"),
        )
        self.assertEqual(start_date.year, 1997)
        self.assertEqual(start_date.month, 8)
        self.assertEqual(start_date.day, 12)

    @patch("events.calendar.anime.services.api_request")
    def test_get_anime_schedule_bulk_filter_episodes(self, mock_api_request):
        """Test get_anime_schedule_bulk filtering episodes beyond total count."""
        mock_api_request.return_value = {
            "data": {
                "Page": {
                    "pageInfo": {"hasNextPage": False},
                    "media": [
                        {
                            "idMal": 437,
                            "endDate": {"year": 1997, "month": 8, "day": 5},
                            "episodes": 1,
                            "airingSchedule": {
                                "nodes": [
                                    {"episode": 1, "airingAt": 870739200},
                                    {"episode": 2, "airingAt": 870825600},
                                ],
                            },
                        },
                    ],
                },
            },
        }

        result = get_anime_schedule_bulk(["437"])

        self.assertIn("437", result)
        self.assertEqual(len(result["437"]), 1)
        self.assertEqual(result["437"][0]["episode"], 1)

    def test_anilist_date_parser(self):
        """Test anilist_date_parser function."""
        complete_date = {"year": 2024, "month": 3, "day": 28}
        result = anilist_date_parser(complete_date)

        dt = datetime.datetime.fromtimestamp(result, tz=ZoneInfo("UTC"))
        self.assertEqual(dt.year, 2024)
        self.assertEqual(dt.month, 3)
        self.assertEqual(dt.day, 28)

        partial_date = {"year": 2024, "month": 3, "day": None}
        result = anilist_date_parser(partial_date)

        dt = datetime.datetime.fromtimestamp(result, tz=ZoneInfo("UTC"))
        self.assertEqual(dt.year, 2024)
        self.assertEqual(dt.month, 3)
        self.assertEqual(dt.day, 1)

        year_only_date = {"year": 2024, "month": None, "day": None}
        result = anilist_date_parser(year_only_date)

        dt = datetime.datetime.fromtimestamp(result, tz=ZoneInfo("UTC"))
        self.assertEqual(dt.year, 2024)
        self.assertEqual(dt.month, 1)
        self.assertEqual(dt.day, 1)

        missing_year = {"year": None, "month": 3, "day": 28}
        result = anilist_date_parser(missing_year)
        self.assertIsNone(result)

    @patch("events.calendar.anime.services.api_request")
    def test_process_anime_bulk(self, mock_api_request):
        """Test process_anime_bulk function."""
        mock_api_request.return_value = {
            "data": {
                "Page": {
                    "pageInfo": {"hasNextPage": False},
                    "media": [
                        {
                            "idMal": 437,
                            "endDate": {"year": 1997, "month": 8, "day": 5},
                            "episodes": 1,
                            "airingSchedule": {
                                "nodes": [
                                    {"episode": 1, "airingAt": 870739200},
                                ],
                            },
                        },
                    ],
                },
            },
        }

        events_bulk = []
        process_anime_bulk([self.anime_item], events_bulk)

        self.assertEqual(len(events_bulk), 1)
        self.assertEqual(events_bulk[0].item, self.anime_item)
        self.assertEqual(events_bulk[0].content_number, 1)

        expected_date = datetime.datetime.fromtimestamp(870739200, tz=ZoneInfo("UTC"))
        self.assertEqual(events_bulk[0].datetime, expected_date)

    @patch("events.calendar.anime.services.get_media_metadata")
    @patch("events.calendar.anime.services.api_request")
    def test_process_anime_bulk_no_matching_anime_anilist(
        self,
        mock_api_request,
        mock_get_media_metadata,
    ):
        """Test process_anime_bulk with no matching anime in AniList."""
        mock_api_request.return_value = {
            "data": {
                "Page": {
                    "pageInfo": {"hasNextPage": False},
                    "media": [],
                },
            },
        }
        mock_get_media_metadata.return_value = {
            "max_progress": 1,
            "details": {"end_date": "1997-08-05"},
        }

        events_bulk = []
        process_anime_bulk([self.anime_item], events_bulk)

        self.assertEqual(len(events_bulk), 1)
