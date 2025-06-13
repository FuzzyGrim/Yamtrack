import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from app.models import (
    TV,
    Anime,
    Book,
    Comic,
    Item,
    Manga,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Status,
)
from app.providers import services
from events.calendar import (
    anilist_date_parser,
    date_parser,
    fetch_releases,
    get_anime_schedule_bulk,
    get_items_to_process,
    get_tvmaze_episode_map,
    process_anime_bulk,
    process_comic,
    process_other,
    process_tv,
)
from events.models import Event


class ReloadCalendarTaskTests(TestCase):
    """Test the fetch_releases task."""

    def setUp(self):
        """Set up the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        # Create anime item
        self.anime_item = Item.objects.create(
            media_id="437",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Perfect Blue",
            image="http://example.com/image.jpg",
        )
        Anime.objects.create(
            item=self.anime_item,
            user=self.user,
            status=Status.PLANNING.value,
        )

        # Create movie item
        self.movie_item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="The Godfather",
            image="http://example.com/thegodfather.jpg",
        )
        Movie.objects.create(
            item=self.movie_item,
            user=self.user,
            status=Status.PLANNING.value,
        )

        self.tv_item = Item.objects.create(
            media_id="1396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Breaking Bad",
            image="http://example.com/breakingbad.jpg",
        )
        tv_object = TV.objects.create(
            item=self.tv_item,
            user=self.user,
            status=Status.PLANNING.value,
        )

        # Create season item
        self.season_item = Item.objects.create(
            media_id="1396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Breaking Bad",
            image="http://example.com/breakingbad.jpg",
            season_number=1,
        )
        Season.objects.create(
            item=self.season_item,
            related_tv=tv_object,
            user=self.user,
            status=Status.PLANNING.value,
        )

        # Create manga item
        self.manga_item = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.MANGA.value,
            title="Berserk",
            image="http://example.com/berserk.jpg",
        )
        Manga.objects.create(
            item=self.manga_item,
            user=self.user,
            status=Status.PLANNING.value,
        )

        # Create book item
        self.book_item = Item.objects.create(
            media_id="OL21733390M",
            source=Sources.OPENLIBRARY.value,
            media_type=MediaTypes.BOOK.value,
            title="1984",
            image="http://example.com/1984.jpg",
        )
        Book.objects.create(
            item=self.book_item,
            user=self.user,
            status=Status.PLANNING.value,
        )

        self.comic_item = Item.objects.create(
            media_id="60760",
            source=Sources.COMICVINE.value,
            media_type=MediaTypes.COMIC.value,
            title="Batman",
            image="http://example.com/batman.jpg",
        )
        Comic.objects.create(
            item=self.comic_item,
            user=self.user,
            status=Status.PLANNING.value,
        )

    @patch("events.calendar.process_tv")
    @patch("events.calendar.process_other")
    @patch("events.calendar.process_anime_bulk")
    def test_fetch_releases_all_types(
        self,
        mock_process_anime_bulk,
        mock_process_other,
        mock_process_tv,
    ):
        """Test fetch_releases with all media types."""
        # Setup mocks
        mock_process_tv.side_effect = lambda _, events_bulk, __: events_bulk.append(
            Event(
                item=self.season_item,
                content_number=1,
                datetime=timezone.now(),
            ),
        )
        mock_process_other.side_effect = (
            lambda item, events_bulk, __: events_bulk.append(
                Event(
                    item=item,
                    content_number=1,
                    datetime=timezone.now(),
                ),
            )
        )
        # Setup mock for process_anime_bulk to create events for anime items
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

        # Call the task
        result = fetch_releases(self.user.id)

        # Verify process_anime_bulk was called for anime items
        mock_process_anime_bulk.assert_called_once()
        anime_items = mock_process_anime_bulk.call_args[0][0]
        self.assertEqual(len(anime_items), 1)
        self.assertEqual(anime_items[0].id, self.anime_item.id)

        # Verify process_tv was called for season items
        self.assertTrue(Event.objects.filter(item=self.season_item).exists())

        # Verify process_other was called for non-anime items
        #  items: movie, manga, book
        self.assertEqual(mock_process_other.call_count, 3)

        # Verify events were created
        self.assertTrue(Event.objects.filter(item=self.anime_item).exists())
        self.assertTrue(Event.objects.filter(item=self.movie_item).exists())
        self.assertTrue(Event.objects.filter(item=self.manga_item).exists())
        self.assertTrue(Event.objects.filter(item=self.book_item).exists())

        # Verify result message
        self.assertIn("Perfect Blue", result)
        self.assertIn("The Godfather", result)
        self.assertIn("Breaking Bad", result)
        self.assertIn("Berserk", result)
        self.assertIn("1984", result)

    @patch("events.calendar.process_other")
    def test_fetch_releases_specific_items(self, mock_process_other):
        """Test fetch_releases with specific items to process."""
        # Setup mock
        mock_process_other.side_effect = (
            lambda item, events_bulk, _: events_bulk.append(
                Event(
                    item=item,
                    content_number=1,
                    datetime=timezone.now(),
                ),
            )
        )

        # Call the task with specific items
        items_to_process = [self.movie_item, self.book_item]
        result = fetch_releases(self.user.id, items_to_process)

        # Verify process_other was called only for specified items
        self.assertEqual(mock_process_other.call_count, 2)

        # Verify events were created only for specified items
        self.assertFalse(Event.objects.filter(item=self.anime_item).exists())
        self.assertTrue(Event.objects.filter(item=self.movie_item).exists())
        self.assertFalse(Event.objects.filter(item=self.season_item).exists())
        self.assertFalse(Event.objects.filter(item=self.manga_item).exists())
        self.assertTrue(Event.objects.filter(item=self.book_item).exists())

        # Verify result message
        self.assertIn("The Godfather", result)
        self.assertIn("1984", result)
        self.assertNotIn("Perfect Blue", result)
        self.assertNotIn("Breaking Bad", result)
        self.assertNotIn("Berserk", result)

    def test_get_items_to_process(self):
        """Test the get_items_to_process function."""
        # Create a second user to verify user filtering
        credentials = {"username": "test2", "password": "12345"}
        user2 = get_user_model().objects.create_user(**credentials)

        # Create items with future events
        future_date = timezone.now() + timezone.timedelta(days=7)

        # Create an event for anime item (future)
        Event.objects.create(
            item=self.anime_item,
            content_number=1,
            datetime=future_date,
        )

        # Create an event for season item (future)
        Event.objects.create(
            item=self.season_item,
            content_number=1,
            datetime=future_date,
        )

        # Create an event for manga item (past)
        past_date = timezone.now() - timezone.timedelta(days=30)
        Event.objects.create(
            item=self.manga_item,
            content_number=1,
            datetime=past_date,
        )

        # Create an event for book item (past)
        old_past_date = timezone.now() - timezone.timedelta(
            days=400,
        )  # More than a year ago
        Event.objects.create(
            item=self.book_item,
            content_number=1,
            datetime=old_past_date,
        )

        # Create a recent event for comic item (within a year)
        comic_recent_date = timezone.now() - timezone.timedelta(days=180)
        Event.objects.create(
            item=self.comic_item,
            content_number=1,
            datetime=comic_recent_date,
        )

        # Create an item for user2 (should not appear when filtering for user1)
        user2_item = Item.objects.create(
            media_id="888",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="User2 Anime",
            image="http://example.com/user2.jpg",
        )
        Anime.objects.create(
            item=user2_item,
            user=user2,
            status=Status.IN_PROGRESS.value,
        )

        # Test with specific user
        items = get_items_to_process(self.user)

        # Verify items with future events are included
        self.assertIn(self.anime_item, items)
        self.assertIn(self.tv_item, items)

        # Verify comic with recent events is included
        self.assertIn(self.comic_item, items)

        # Verify item with no events is included (movie_item has no events)
        self.assertIn(self.movie_item, items)

        # Verify items from other users are excluded
        self.assertNotIn(user2_item, items)

        # Verify manga with old events is not included
        self.assertNotIn(self.manga_item, items)

        # Verify book with very old events is not included
        self.assertNotIn(self.book_item, items)

        # Test without user parameter (should include all users)
        all_items = get_items_to_process()

        # Should include items from both users
        self.assertIn(self.anime_item, all_items)
        self.assertIn(user2_item, all_items)

    @patch("events.calendar.tmdb.tv")
    @patch("events.calendar.tmdb.tv_with_seasons")
    @patch("events.calendar.get_tvmaze_episode_map")
    def test_process_tv_season(
        self,
        mock_get_tvmaze_episode_map,
        mock_tv_with_seasons,
        mock_tv,
    ):
        """Test processing for a TV season."""
        # Setup mocks
        mock_tv.return_value = {
            "related": {
                "seasons": [
                    {"season_number": 1, "episodes": [1, 2, 3]},
                    {"season_number": 2, "episodes": [1, 2]},
                    {"season_number": 3, "episodes": [1]},
                ],
            },
            "next_episode_season": 2,
        }

        mock_tv_with_seasons.return_value = {
            "season/1": {
                "image": "http://example.com/season1.jpg",
                "season_number": 1,
                "episodes": [
                    {"episode_number": 1, "air_date": "2008-01-20"},
                    {"episode_number": 2, "air_date": "2008-01-27"},
                    {"episode_number": 3, "air_date": "2008-02-03"},
                ],
                "tvdb_id": "81189",
            },
            "season/2": {
                "image": "http://example.com/season2.jpg",
                "season_number": 2,
                "episodes": [
                    {"episode_number": 1, "air_date": "2009-01-20"},
                    {"episode_number": 2, "air_date": "2009-01-27"},
                ],
                "tvdb_id": "81189",
            },
            "season/3": {
                "image": "http://example.com/season3.jpg",
                "season_number": 3,
                "episodes": [
                    {"episode_number": 1, "air_date": "2010-01-20"},
                ],
                "tvdb_id": "81189",
            },
        }

        # TVMaze data with more precise timestamps
        mock_get_tvmaze_episode_map.return_value = {
            "1_1": "2008-01-20T22:00:00+00:00",
            "1_2": "2008-01-27T22:00:00+00:00",
            "1_3": "2008-02-03T22:00:00+00:00",
        }

        # Process the item
        events_bulk = []
        skipped_items = []
        process_tv(self.tv_item, events_bulk, skipped_items)

        # Verify events were added
        self.assertEqual(len(events_bulk), 6)

        # Verify the first episode
        self.assertEqual(events_bulk[0].item, self.season_item)
        self.assertEqual(events_bulk[0].content_number, 1)

        # Verify TVMaze data was used (more precise timestamp)
        expected_date = datetime.datetime.fromisoformat("2008-01-20T22:00:00+00:00")
        self.assertEqual(events_bulk[0].datetime, expected_date)

    @patch("events.calendar.services.get_media_metadata")
    def test_process_other_movie(self, mock_get_media_metadata):
        """Test process_other for a movie."""
        # Setup mock
        mock_get_media_metadata.return_value = {
            "max_progress": 1,
            "details": {
                "release_date": "1999-10-15",
            },
        }

        # Process the item
        events_bulk = []
        skipped_items = []
        process_other(self.movie_item, events_bulk, skipped_items)

        # Verify event was added
        self.assertEqual(len(events_bulk), 1)
        self.assertEqual(events_bulk[0].item, self.movie_item)
        self.assertEqual(
            events_bulk[0].content_number,
            None,
        )

        # Verify the date was parsed correctly
        expected_date = date_parser("1999-10-15")
        self.assertEqual(events_bulk[0].datetime, expected_date)

    @patch("events.calendar.services.get_media_metadata")
    def test_process_other_book(self, mock_get_media_metadata):
        """Test process_other for a book."""
        # Setup mock
        mock_get_media_metadata.return_value = {
            "max_progress": 328,
            "details": {
                "publish_date": "1949-06-08",
            },
        }

        # Process the item
        events_bulk = []
        skipped_items = []
        process_other(self.book_item, events_bulk, skipped_items)

        # Verify event was added
        self.assertEqual(len(events_bulk), 1)
        self.assertEqual(events_bulk[0].item, self.book_item)
        self.assertEqual(events_bulk[0].content_number, 328)  # Books use page count

        # Verify the date was parsed correctly
        expected_date = date_parser("1949-06-08")
        self.assertEqual(events_bulk[0].datetime, expected_date)

    @patch("events.calendar.services.get_media_metadata")
    def test_process_other_manga(self, mock_get_media_metadata):
        """Test process_other for manga."""
        # Setup mock
        mock_get_media_metadata.return_value = {
            "details": {
                "end_date": "2023-12-22",
            },
            "max_progress": 375,  # Total chapters
        }

        # Process the item
        events_bulk = []
        skipped_items = []
        process_other(self.manga_item, events_bulk, skipped_items)

        # Verify event was added
        self.assertEqual(len(events_bulk), 1)

        # Second event should be for the final chapter
        self.assertEqual(events_bulk[0].item, self.manga_item)
        self.assertEqual(events_bulk[0].content_number, 375)
        expected_end_date = date_parser("2023-12-22")
        self.assertEqual(events_bulk[0].datetime, expected_end_date)

    @patch("events.calendar.services.api_request")
    def test_get_anime_schedule_bulk(self, mock_api_request):
        """Test get_anime_schedule_bulk function."""
        # Setup mock
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
                                    {"episode": 1, "airingAt": 870739200},  # 1997-08-05
                                ],
                            },
                        },
                    ],
                },
            },
        }

        # Call the function
        result = get_anime_schedule_bulk(["437"])

        # Verify result
        self.assertIn("437", result)
        self.assertEqual(len(result["437"]), 1)
        self.assertEqual(result["437"][0]["episode"], 1)
        self.assertEqual(result["437"][0]["airingAt"], 870739200)

    @patch("events.calendar.services.api_request")
    def test_get_anime_schedule_bulk_no_airing_schedule(self, mock_api_request):
        """Test get_anime_schedule_bulk with no airing schedule."""
        # Setup mock
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

        # Call the function
        result = get_anime_schedule_bulk(["437"])

        # Verify result - should create a basic schedule from dates
        self.assertIn("437", result)
        self.assertEqual(len(result["437"]), 1)  # end date
        self.assertEqual(result["437"][0]["episode"], 2)

        # Convert timestamp to datetime for easier verification
        start_date = datetime.datetime.fromtimestamp(
            result["437"][0]["airingAt"],
            tz=ZoneInfo("UTC"),
        )
        self.assertEqual(start_date.year, 1997)
        self.assertEqual(start_date.month, 8)
        self.assertEqual(start_date.day, 12)

    @patch("events.calendar.services.api_request")
    def test_get_anime_schedule_bulk_filter_episodes(self, mock_api_request):
        """Test get_anime_schedule_bulk filtering episodes beyond total count."""
        # Setup mock with more episodes in schedule than total episodes
        mock_api_request.return_value = {
            "data": {
                "Page": {
                    "pageInfo": {"hasNextPage": False},
                    "media": [
                        {
                            "idMal": 437,
                            "endDate": {"year": 1997, "month": 8, "day": 5},
                            "episodes": 1,  # Only 1 episode total
                            "airingSchedule": {
                                "nodes": [
                                    {"episode": 1, "airingAt": 870739200},  # Valid
                                    {
                                        "episode": 2,
                                        "airingAt": 870825600,
                                    },  # Should be filtered
                                ],
                            },
                        },
                    ],
                },
            },
        }

        # Call the function
        result = get_anime_schedule_bulk(["437"])

        # Verify result - should filter out episode 2
        self.assertIn("437", result)
        self.assertEqual(len(result["437"]), 1)  # Only episode 1
        self.assertEqual(result["437"][0]["episode"], 1)

    @patch("events.calendar.services.api_request")
    def test_get_tvmaze_episode_map(self, mock_api_request):
        """Test get_tvmaze_episode_map function."""
        # Clear cache first
        cache.clear()

        # Setup mocks for the two API calls
        mock_api_request.side_effect = [
            # First call - lookup show
            {"id": 12345},
            # Second call - get episodes
            {
                "_embedded": {
                    "episodes": [
                        {
                            "season": 1,
                            "number": 1,
                            "airstamp": "2008-01-20T22:00:00+00:00",
                            "airtime": "22:00",
                        },
                        {
                            "season": 1,
                            "number": 2,
                            "airstamp": "2008-01-27T22:00:00+00:00",
                            "airtime": "22:00",
                        },
                    ],
                },
            },
        ]

        # Call the function
        result = get_tvmaze_episode_map("81189")

        # Verify result
        self.assertEqual(len(result), 2)
        self.assertIn("1_1", result)
        self.assertIn("1_2", result)
        self.assertEqual(result["1_1"], "2008-01-20T22:00:00+00:00")
        self.assertEqual(result["1_2"], "2008-01-27T22:00:00+00:00")

        # Verify cache was set
        cached_result = cache.get("tvmaze_map_81189")
        self.assertEqual(cached_result, result)

        # Reset mock and call again - should use cache
        mock_api_request.reset_mock()
        cached_result = get_tvmaze_episode_map("81189")
        mock_api_request.assert_not_called()  # Should not call API again

    @patch("events.calendar.services.api_request")
    def test_get_tvmaze_episode_map_lookup_failure(self, mock_api_request):
        """Test get_tvmaze_episode_map when lookup fails."""
        # Clear cache first
        cache.clear()

        # Setup mock to return empty response for lookup
        mock_api_request.return_value = None

        # Call the function
        result = get_tvmaze_episode_map("invalid_id")

        # Verify result is empty
        self.assertEqual(result, {})

        # Should only have called the API once (for lookup)
        mock_api_request.assert_called_once()

    def test_anilist_date_parser(self):
        """Test anilist_date_parser function."""
        # Test with complete date
        complete_date = {"year": 2024, "month": 3, "day": 28}
        result = anilist_date_parser(complete_date)

        # Convert timestamp to datetime for verification
        dt = datetime.datetime.fromtimestamp(result, tz=ZoneInfo("UTC"))
        self.assertEqual(dt.year, 2024)
        self.assertEqual(dt.month, 3)
        self.assertEqual(dt.day, 28)

        # Test with partial date (missing day)
        partial_date = {"year": 2024, "month": 3, "day": None}
        result = anilist_date_parser(partial_date)

        # Convert timestamp to datetime for verification
        dt = datetime.datetime.fromtimestamp(result, tz=ZoneInfo("UTC"))
        self.assertEqual(dt.year, 2024)
        self.assertEqual(dt.month, 3)
        self.assertEqual(dt.day, 1)  # Default to 1

        # Test with partial date (missing month and day)
        year_only_date = {"year": 2024, "month": None, "day": None}
        result = anilist_date_parser(year_only_date)

        # Convert timestamp to datetime for verification
        dt = datetime.datetime.fromtimestamp(result, tz=ZoneInfo("UTC"))
        self.assertEqual(dt.year, 2024)
        self.assertEqual(dt.month, 1)  # Default to 1
        self.assertEqual(dt.day, 1)  # Default to 1

        # Test with missing year
        missing_year = {"year": None, "month": 3, "day": 28}
        result = anilist_date_parser(missing_year)
        self.assertIsNone(result)

    @patch("events.calendar.services.get_media_metadata")
    def test_process_other_invalid_date(self, mock_get_media_metadata):
        """Test process_other with invalid date."""
        # Setup mock with invalid date
        mock_get_media_metadata.return_value = {
            "details": {
                "release_date": "invalid-date",
            },
        }

        # Process the item
        events_bulk = []
        skipped_items = []
        process_other(self.movie_item, events_bulk, skipped_items)

        # Verify no events were added
        self.assertEqual(len(events_bulk), 0)

    @patch("events.calendar.services.get_media_metadata")
    def test_process_other_no_date(self, mock_get_media_metadata):
        """Test process_other with no date."""
        # Setup mock with no date
        mock_get_media_metadata.return_value = {
            "details": {},
        }

        # Process the item
        events_bulk = []
        skipped_items = []
        process_other(self.movie_item, events_bulk, skipped_items)

        # Verify no events were added
        self.assertEqual(len(events_bulk), 0)

    @patch("events.calendar.services.api_request")
    def test_process_anime_bulk(self, mock_api_request):
        """Test process_anime_bulk function."""
        # Setup mock
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
                                    {"episode": 1, "airingAt": 870739200},  # 1997-08-05
                                ],
                            },
                        },
                    ],
                },
            },
        }

        # Process anime items
        events_bulk = []
        process_anime_bulk([self.anime_item], events_bulk)

        # Verify events were added
        self.assertEqual(len(events_bulk), 1)
        self.assertEqual(events_bulk[0].item, self.anime_item)
        self.assertEqual(events_bulk[0].content_number, 1)

        # Convert timestamp to datetime for verification
        expected_date = datetime.datetime.fromtimestamp(870739200, tz=ZoneInfo("UTC"))
        self.assertEqual(events_bulk[0].datetime, expected_date)

    @patch("events.calendar.services.api_request")
    def test_process_anime_bulk_no_matching_anime(self, mock_api_request):
        """Test process_anime_bulk with no matching anime."""
        # Setup mock with empty media list
        mock_api_request.return_value = {
            "data": {
                "Page": {
                    "pageInfo": {"hasNextPage": False},
                    "media": [],  # No matching anime
                },
            },
        }

        # Process anime items
        events_bulk = []
        process_anime_bulk([self.anime_item], events_bulk)

        # Verify no events were added
        self.assertEqual(len(events_bulk), 0)

    @patch("app.providers.tmdb.movie")
    def test_http_error_handling(self, mock_tmdb_movie):
        """Test handling of ProviderAPIError in process_other."""
        # Create a mock response for the error
        response_mock = MagicMock()
        response_mock.status_code = 404
        response_mock.text = "Not found"

        # Create and raise the ProviderAPIError
        mock_tmdb_movie.side_effect = services.ProviderAPIError(
            provider=Sources.TMDB.value,
            error=response_mock,
            details="Movie not found",
        )

        # Process the item - should not raise exception
        events_bulk = []
        skipped_items = []
        process_other(self.movie_item, events_bulk, skipped_items)

        # Verify no events were added
        self.assertEqual(len(events_bulk), 0)

    @patch("events.calendar.services.get_media_metadata")
    @patch("events.calendar.comicvine.issue")
    def test_process_comic_with_store_date(self, mock_issue, mock_get_media_metadata):
        """Test process_comic with store date available."""
        # Create comic item
        comic_item = Item.objects.create(
            media_id="4050-18166",
            source=Sources.COMICVINE.value,
            media_type=MediaTypes.COMIC.value,
            title="Batman",
            image="http://example.com/batman.jpg",
        )

        # Setup mocks
        mock_get_media_metadata.return_value = {
            "max_progress": 10,
            "last_issue_id": "4000-123456",
            "last_issue": {"issue_number": "10"},
        }

        mock_issue.return_value = {
            "store_date": "2023-04-15",
            "cover_date": "2023-05-01",  # Should use store_date instead
        }

        # Process the item
        events_bulk = []
        skipped_items = []
        process_comic(comic_item, events_bulk, skipped_items)

        # Verify event was added
        self.assertEqual(len(events_bulk), 1)
        self.assertEqual(events_bulk[0].item, comic_item)
        self.assertEqual(events_bulk[0].content_number, 10)

        # Verify the date was parsed correctly (should use store_date)
        expected_date = date_parser("2023-04-15")
        self.assertEqual(events_bulk[0].datetime, expected_date)

        # Verify the correct issue was fetched
        mock_issue.assert_called_once_with("4000-123456")

    @patch("events.calendar.services.get_media_metadata")
    @patch("events.calendar.comicvine.issue")
    def test_process_comic_with_cover_date_only(
        self,
        mock_issue,
        mock_get_media_metadata,
    ):
        """Test process_comic with only cover date available."""
        # Create comic item
        comic_item = Item.objects.create(
            media_id="4050-18167",
            source=Sources.COMICVINE.value,
            media_type=MediaTypes.COMIC.value,
            title="Superman",
            image="http://example.com/superman.jpg",
        )

        # Setup mocks
        mock_get_media_metadata.return_value = {
            "max_progress": 5,
            "last_issue_id": "4000-123457",
            "last_issue": {"issue_number": "5"},
        }

        mock_issue.return_value = {
            "store_date": None,  # No store date
            "cover_date": "2023-05-01",  # Should use cover_date
        }

        # Process the item
        events_bulk = []
        skipped_items = []
        process_comic(comic_item, events_bulk, skipped_items)

        # Verify event was added
        self.assertEqual(len(events_bulk), 1)
        self.assertEqual(events_bulk[0].item, comic_item)
        self.assertEqual(events_bulk[0].content_number, 5)

        # Verify the date was parsed correctly (should use cover_date)
        expected_date = date_parser("2023-05-01")
        self.assertEqual(events_bulk[0].datetime, expected_date)

    @patch("events.calendar.services.get_media_metadata")
    @patch("events.calendar.comicvine.issue")
    def test_process_comic_no_dates(self, mock_issue, mock_get_media_metadata):
        """Test process_comic with no dates available."""
        # Create comic item
        comic_item = Item.objects.create(
            media_id="4050-18168",
            source=Sources.COMICVINE.value,
            media_type=MediaTypes.COMIC.value,
            title="Wonder Woman",
            image="http://example.com/wonderwoman.jpg",
        )

        # Setup mocks
        mock_get_media_metadata.return_value = {
            "max_progress": 3,
            "last_issue_id": "4000-123458",
            "last_issue": {"issue_number": "3"},
        }

        mock_issue.return_value = {
            "store_date": None,
            "cover_date": None,
        }

        # Process the item
        events_bulk = []
        skipped_items = []
        process_comic(comic_item, events_bulk, skipped_items)

        # Verify no event was added
        self.assertEqual(len(events_bulk), 0)
