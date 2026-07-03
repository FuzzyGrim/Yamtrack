import json
import os
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests
from django.conf import settings
from django.core.cache import cache
from django.test import TestCase

from app.models import Episode, Item, MediaTypes, Sources
from app.providers import (
    comicvine,
    hardcover,
    igdb,
    mal,
    mangaupdates,
    manual,
    openlibrary,
    services,
    tmdb,
)

mock_path = Path(__file__).resolve().parent.parent / "mock_data"
RUN_PROVIDER_TESTS = os.environ.get("RUN_PROVIDER_TESTS") == "1"
requires_provider_network = unittest.skipUnless(
    RUN_PROVIDER_TESTS,
    "Set RUN_PROVIDER_TESTS=1 to run live provider API tests.",
)


class Metadata(TestCase):
    """Test the external API calls for media details."""

    @requires_provider_network
    def test_anime(self):
        """Test the metadata method for anime."""
        response = mal.anime("1")
        self.assertEqual(response["title"], "Cowboy Bebop")
        self.assertEqual(response["details"]["start_date"], "1998-04-03")
        self.assertEqual(response["details"]["status"], "Finished")
        self.assertEqual(response["details"]["episodes"], 26)

    @patch("requests.Session.get")
    def test_anime_unknown(self, mock_data):
        """Test the metadata method for anime with mostly unknown data."""
        with Path(mock_path / "metadata_anime_unknown.json").open() as file:
            anime_response = json.load(file)
        mock_data.return_value.json.return_value = anime_response
        mock_data.return_value.status_code = 200

        # anime without picture, synopsis, duration, or number of episodes
        response = mal.anime("0")
        self.assertEqual(response["title"], "Unknown Example")
        self.assertEqual(response["image"], settings.IMG_NONE)
        self.assertEqual(response["synopsis"], "No synopsis available.")
        self.assertEqual(response["details"]["episodes"], None)
        self.assertEqual(response["details"]["runtime"], None)

    @requires_provider_network
    def test_manga(self):
        """Test the metadata method for manga."""
        response = mal.manga("1")
        self.assertEqual(response["title"], "Monster")
        self.assertEqual(response["details"]["start_date"], "1994-12-05")
        self.assertEqual(response["details"]["status"], "Finished")
        self.assertEqual(response["details"]["number_of_chapters"], 162)

    @requires_provider_network
    def test_mangaupdates(self):
        """Test the metadata method for manga from mangaupdates."""
        response = mangaupdates.manga("72274276213")
        self.assertEqual(response["title"], "Monster")
        self.assertEqual(response["details"]["year"], "1994")
        self.assertEqual(response["details"]["format"], "Manga")

    @requires_provider_network
    def test_tv(self):
        """Test the metadata method for TV shows."""
        response = tmdb.tv("1396")
        self.assertEqual(response["title"], "Breaking Bad")
        self.assertEqual(response["details"]["first_air_date"], "2008-01-20")
        self.assertEqual(response["details"]["status"], "Ended")
        self.assertEqual(response["details"]["episodes"], 62)

    def test_tmdb_crew_keeps_profile_image(self):
        """Test grouped TMDB crew credits keep person images."""
        credits = {
            "crew": [
                {"id": 1, "name": "Denis Villeneuve", "job": "Screenplay", "profile_path": "/denis.jpg"},
                {"id": 1, "name": "Denis Villeneuve", "job": "Director", "profile_path": "/denis.jpg"},
            ]
        }

        crew = tmdb.get_crew(credits)

        self.assertEqual(crew[0]["image"], "https://image.tmdb.org/t/p/w500/denis.jpg")

    @patch("app.providers.tmdb.timezone.localdate")
    @patch("app.providers.tmdb.services.api_request")
    def test_tv_changes(self, mock_api_request, mock_localdate):
        """Test fetching changed TV ids from TMDB."""
        mock_localdate.return_value = date(2026, 4, 5)
        mock_api_request.return_value = {
            "results": [{"id": 1}, {"id": 2}],
            "total_pages": 1,
        }

        result = tmdb.tv_changes()

        self.assertEqual(result, {"1", "2"})
        _, kwargs = mock_api_request.call_args
        self.assertEqual(kwargs["params"]["start_date"], "2026-04-02")
        self.assertEqual(kwargs["params"]["end_date"], "2026-04-05")
        self.assertEqual(kwargs["params"]["page"], 1)

    @patch("app.providers.tmdb.timezone.localdate")
    @patch("app.providers.tmdb.services.api_request")
    def test_tv_changes_across_pages(self, mock_api_request, mock_localdate):
        """Test TMDB TV changes pagination and deduplication."""
        mock_localdate.return_value = date(2026, 4, 5)
        mock_api_request.side_effect = [
            {
                "results": [{"id": 1}, {"id": 2}],
                "total_pages": 2,
            },
            {
                "results": [{"id": 2}, {"id": 3}],
                "total_pages": 2,
            },
        ]

        result = tmdb.tv_changes()

        self.assertEqual(result, {"1", "2", "3"})
        self.assertEqual(mock_api_request.call_count, 2)

    @patch("app.providers.tmdb.services.api_request")
    def test_title_logo_prefers_language_then_votes(self, mock_api_request):
        """Test TMDB title logo selection."""
        cache.clear()
        mock_api_request.return_value = {
            "logos": [
                {
                    "file_path": "/fallback.png",
                    "width": 2000,
                    "height": 500,
                    "aspect_ratio": 4,
                    "vote_average": 10,
                    "vote_count": 20,
                    "iso_639_1": None,
                },
                {
                    "file_path": "/low-votes.png",
                    "width": 1000,
                    "height": 400,
                    "aspect_ratio": 2.5,
                    "vote_average": 7,
                    "vote_count": 1,
                    "iso_639_1": settings.TMDB_LANG,
                },
                {
                    "file_path": "/best.png",
                    "width": 1493,
                    "height": 482,
                    "aspect_ratio": 3.1,
                    "vote_average": 7,
                    "vote_count": 5,
                    "iso_639_1": settings.TMDB_LANG,
                },
            ],
        }

        logo = tmdb.get_title_logo("550", MediaTypes.MOVIE.value)

        self.assertEqual(
            logo,
            {
                "url": "https://image.tmdb.org/t/p/w500/best.png",
                "width": 1493,
                "height": 482,
                "aspect_ratio": 3.1,
            },
        )
        self.assertEqual(mock_api_request.call_count, 1)

    @patch("app.providers.tmdb.timezone.localdate")
    @patch("app.providers.tmdb.services.api_request")
    def test_movie_changes(self, mock_api_request, mock_localdate):
        """Test fetching changed movie ids from TMDB."""
        mock_localdate.return_value = date(2026, 4, 5)
        mock_api_request.return_value = {
            "results": [{"id": 10}, {"id": 20}],
            "total_pages": 1,
        }

        result = tmdb.movie_changes()

        self.assertEqual(result, {"10", "20"})
        _, kwargs = mock_api_request.call_args
        self.assertEqual(kwargs["params"]["start_date"], "2026-04-02")
        self.assertEqual(kwargs["params"]["end_date"], "2026-04-05")
        self.assertEqual(kwargs["params"]["page"], 1)

    @patch("app.providers.tmdb.timezone.localdate")
    @patch("app.providers.tmdb.services.api_request")
    def test_movie_changes_across_pages(self, mock_api_request, mock_localdate):
        """Test TMDB movie changes pagination and deduplication."""
        mock_localdate.return_value = date(2026, 4, 5)
        mock_api_request.side_effect = [
            {
                "results": [{"id": 10}, {"id": 20}],
                "total_pages": 2,
            },
            {
                "results": [{"id": 20}, {"id": 30}],
                "total_pages": 2,
            },
        ]

        result = tmdb.movie_changes()

        self.assertEqual(result, {"10", "20", "30"})
        self.assertEqual(mock_api_request.call_count, 2)

    def test_tmdb_process_episodes(self):
        """Test the process_episodes function for TMDB episodes."""
        Item.objects.create(
            media_id="5",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Process Episodes Test",
            image="http://example.com/process.jpg",
        )

        Item.objects.create(
            media_id="5",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Process Episodes Test",
            image="http://example.com/process_s1.jpg",
            season_number=1,
        )

        for i in range(1, 4):
            Item.objects.create(
                media_id="5",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title=f"Process Episode {i}",
                image=f"http://example.com/process_s1e{i}.jpg",
                season_number=1,
                episode_number=i,
            )

        season_metadata = {
            "media_id": "1396",  # Breaking Bad
            "season_number": 1,
            "episodes": [
                {
                    "episode_number": 1,
                    "air_date": "2008-01-20",
                    "still_path": "/path/to/still1.jpg",
                    "name": "Pilot",
                    "overview": "overview of the episode",
                    "runtime": 90,
                },
                {
                    "episode_number": 2,
                    "air_date": "2008-01-27",
                    "still_path": "/path/to/still2.jpg",
                    "name": "Cat's in the Bag...",
                    "overview": "overview of the episode",
                    "runtime": 23,
                },
                {
                    "episode_number": 3,
                    "air_date": "2008-02-10",
                    "still_path": "/path/to/still3.jpg",
                    "name": "...And the Bag's in the River",
                    "overview": "overview of the episode",
                    "runtime": 23,
                },
            ],
        }
        episode_item_1 = Item.objects.get(
            media_id="5",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=1,
        )
        episode_1 = Episode(item=episode_item_1)

        episode_item_2 = Item.objects.get(
            media_id="5",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=2,
        )
        episode_2 = Episode(item=episode_item_2)

        episodes_in_db = [episode_1, episode_2]

        # Call process_episodes
        result = tmdb.process_episodes(season_metadata, episodes_in_db)

        self.assertEqual(len(result), 3)

        self.assertEqual(result[0]["episode_number"], 1)
        self.assertEqual(result[0]["title"], "Pilot")
        self.assertEqual(result[0]["air_date"], "2008-01-20")
        self.assertEqual(result[0]["runtime"], "1h 30m")
        self.assertTrue(result[0]["history"], [episode_1])

        self.assertEqual(result[1]["episode_number"], 2)
        self.assertEqual(result[1]["title"], "Cat's in the Bag...")
        self.assertEqual(result[1]["air_date"], "2008-01-27")
        self.assertEqual(result[1]["runtime"], "23m")
        self.assertTrue(result[1]["history"], [episode_2])

        self.assertEqual(result[2]["episode_number"], 3)
        self.assertEqual(result[2]["title"], "...And the Bag's in the River")
        self.assertEqual(result[2]["air_date"], "2008-02-10")
        self.assertFalse(result[2]["history"], [])

    @patch("app.providers.tmdb.tv_with_seasons")
    def test_tmdb_episode(self, mock_tv_with_seasons):
        """Test the episode method for TMDB episodes."""
        mock_tv_with_seasons.return_value = {
            "title": "Breaking Bad",
            "season/1": {
                "title": "Breaking Bad",
                "season_title": "Season 1",
                "episodes": [
                    {
                        "episode_number": 1,
                        "name": "Pilot",
                        "still_path": "/path/to/still1.jpg",
                    },
                    {
                        "episode_number": 2,
                        "name": "Cat's in the Bag...",
                        "still_path": "/path/to/still2.jpg",
                    },
                ],
            },
        }

        result = tmdb.episode("1396", "1", "1")

        self.assertEqual(result["title"], "Breaking Bad")
        self.assertEqual(result["season_title"], "Season 1")
        self.assertEqual(result["episode_title"], "Pilot")
        self.assertEqual(result["image"], tmdb.get_image_url("/path/to/still1.jpg"))

        with self.assertRaises(services.ProviderAPIError) as cm:
            tmdb.episode("1396", "1", "3")

        self.assertIn("Episode 3 not found in season 1", str(cm.exception))
        self.assertIn("The Movie Database with ID 1396", str(cm.exception))

        mock_tv_with_seasons.assert_called_with("1396", ["1"])

    def test_tmdb_find_next_episode(self):
        """Test the find_next_episode function."""
        episodes_metadata = [
            {"episode_number": 1, "title": "Episode 1"},
            {"episode_number": 2, "title": "Episode 2"},
            {"episode_number": 3, "title": "Episode 3"},
        ]

        next_episode = tmdb.find_next_episode(1, episodes_metadata)
        self.assertEqual(next_episode, 2)

        next_episode = tmdb.find_next_episode(3, episodes_metadata)
        self.assertIsNone(next_episode)

        next_episode = tmdb.find_next_episode(5, episodes_metadata)
        self.assertIsNone(next_episode)

    @requires_provider_network
    def test_movie(self):
        """Test the metadata method for movies."""
        response = tmdb.movie("10494")
        self.assertEqual(response["title"], "Perfect Blue")
        self.assertEqual(response["details"]["release_date"], "1998-02-28")
        self.assertEqual(response["details"]["status"], "Released")

    @patch("requests.Session.get")
    def test_movie_unknown(self, mock_data):
        """Test the metadata method for movies with mostly unknown data."""
        with Path(mock_path / "metadata_movie_unknown.json").open() as file:
            movie_response = json.load(file)
        mock_data.return_value.json.return_value = movie_response
        mock_data.return_value.status_code = 200

        response = tmdb.movie("0")
        self.assertEqual(response["title"], "Unknown Movie")
        self.assertEqual(response["image"], settings.IMG_NONE)
        self.assertEqual(response["synopsis"], "No synopsis available.")
        self.assertEqual(response["details"]["release_date"], None)
        self.assertEqual(response["details"]["runtime"], None)
        self.assertEqual(response["genres"], None)
        self.assertEqual(response["details"]["studios"], None)
        self.assertEqual(response["details"]["country"], None)
        self.assertEqual(response["details"]["languages"], None)

    @requires_provider_network
    def test_games(self):
        """Test the metadata method for games."""
        response = igdb.game("1942")
        self.assertEqual(response["title"], "The Witcher 3: Wild Hunt")
        self.assertEqual(response["details"]["format"], "Main game")
        self.assertEqual(response["details"]["release_date"], "2015-05-19")
        self.assertEqual(
            response["details"]["themes"],
            ["Action", "Fantasy", "Open world"],
        )
        self.assertIsNotNone(response["time_to_beat"])
        self.assertIn("normally", response["time_to_beat"])
        self.assertEqual(
            list(response["time_to_beat"].keys()),
            ["hastily", "normally", "completely"],
        )

    @requires_provider_network
    def test_external_game_steam(self):
        """Test the external_game method for Steam games."""
        igdb_game_id = igdb.external_game("292030", igdb.ExternalGameSource.STEAM)

        self.assertEqual(igdb_game_id, 1942)

    @requires_provider_network
    def test_external_game_not_found(self):
        """Test the external_game method with non-existent Steam ID."""
        igdb_game_id = igdb.external_game("999999999", igdb.ExternalGameSource.STEAM)

        self.assertIsNone(igdb_game_id)

    @requires_provider_network
    def test_book(self):
        """Test the metadata method for books."""
        response = openlibrary.book("OL21733390M")
        self.assertEqual(response["title"], "Nineteen Eighty-Four")
        self.assertEqual(response["details"]["author"], "George Orwell")

    def test_openlibrary_publish_date_with_abbreviated_month(self):
        """Test Open Library publish dates with abbreviated month names."""
        response = openlibrary.get_publish_date({"publish_date": "Oct 01, 2017"})
        self.assertEqual(response, "2017-10-01")

    @requires_provider_network
    def test_comic(self):
        """Test the metadata method for comics."""
        response = comicvine.comic("155969")
        self.assertEqual(response["title"], "Ultimate Spider-Man")

    @requires_provider_network
    def test_hardcover_book(self):
        """Test the metadata method for books from Hardcover."""
        response = hardcover.book("377193")
        self.assertEqual(response["title"], "The Great Gatsby")
        self.assertEqual(response["details"]["author"], "F. Scott Fitzgerald")
        self.assertIn("Fiction", response["genres"])
        self.assertIn("Young Adult", response["genres"])
        self.assertIn("Classics", response["genres"])
        self.assertAlmostEqual(response["score"], 3.7, delta=0.1)

    def test_hardcover_featured_series_uses_nested_series(self):
        series = hardcover.get_featured_series(
            {
                "id": 1108,
                "series": {
                    "id": 981,
                    "name": "A Song of Ice and Fire",
                },
                "position": 2.0,
            }
        )

        self.assertEqual(series, {"id": 981, "name": "A Song of Ice and Fire"})

    @patch("app.providers.hardcover.services.api_request")
    def test_hardcover_series_books_dedupe_uses_nested_book_read_count(self, api_request_mock):
        api_request_mock.return_value = {
            "data": {
                "series_by_pk": {
                    "book_series": [
                        {
                            "position": 1,
                            "book": {
                                "id": 1,
                                "title": "Low Read Edition",
                                "users_read_count": 1,
                                "cached_image": None,
                            },
                        },
                        {
                            "position": 1,
                            "book": {
                                "id": 2,
                                "title": "Preferred Edition",
                                "users_read_count": 10,
                                "cached_image": "https://example.com/book.jpg",
                            },
                        },
                    ],
                },
            },
        }

        series_books = hardcover.get_series_books(981)

        self.assertEqual(len(series_books), 1)
        self.assertEqual(series_books[0]["media_id"], 2)
        self.assertEqual(series_books[0]["title"], "Preferred Edition")

    @requires_provider_network
    def test_hardcover_book_unknown(self):
        """Test the metadata method for books from Hardcover with minimal data."""
        response = hardcover.book("1265528")
        self.assertEqual(response["title"], "MiNRS")
        self.assertEqual(response["details"]["author"], "Kevin Sylvester")
        self.assertEqual(response["details"]["publish_date"], "2015-09-22")
        # These fields should be None or default values
        self.assertEqual(response["synopsis"], "No synopsis available.")
        self.assertEqual(response["details"]["format"], "Unknown")
        self.assertIsNone(response["genres"])

    def test_manual_tv(self):
        """Test the metadata method for manually created TV shows."""
        Item.objects.create(
            media_id="1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.TV.value,
            title="Manual TV Show",
            image="http://example.com/manual.jpg",
        )

        Item.objects.create(
            media_id="1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.SEASON.value,
            title="Manual TV Show",
            image="http://example.com/manual_s1.jpg",
            season_number=1,
        )

        for i in range(1, 4):
            Item.objects.create(
                media_id="1",
                source=Sources.MANUAL.value,
                media_type=MediaTypes.EPISODE.value,
                title=f"Episode {i}",
                image=f"http://example.com/manual_s1e{i}.jpg",
                season_number=1,
                episode_number=i,
            )

        response = manual.metadata("1", MediaTypes.TV.value)

        self.assertEqual(response["title"], "Manual TV Show")
        self.assertEqual(response["media_id"], "1")
        self.assertEqual(response["source"], Sources.MANUAL.value)
        self.assertEqual(response["media_type"], MediaTypes.TV.value)
        self.assertEqual(response["synopsis"], "No synopsis available.")

        self.assertEqual(response["details"]["seasons"], 1)
        self.assertEqual(response["details"]["episodes"], 3)
        self.assertEqual(response["max_progress"], 3)
        self.assertEqual(len(response["related"]["seasons"]), 1)

        season_data = response["season/1"]
        self.assertEqual(season_data["season_number"], 1)
        self.assertEqual(season_data["max_progress"], 3)
        self.assertEqual(len(season_data["episodes"]), 3)

    def test_manual_movie(self):
        """Test the metadata method for manually created movies."""
        Item.objects.create(
            media_id="2",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MOVIE.value,
            title="Manual Movie",
            image="http://example.com/manual_movie.jpg",
        )

        response = manual.metadata("2", MediaTypes.MOVIE.value)

        self.assertEqual(response["title"], "Manual Movie")
        self.assertEqual(response["media_id"], "2")
        self.assertEqual(response["source"], Sources.MANUAL.value)
        self.assertEqual(response["media_type"], MediaTypes.MOVIE.value)
        self.assertEqual(response["synopsis"], "No synopsis available.")
        self.assertEqual(response["max_progress"], 1)

    def test_manual_season(self):
        """Test the season method for manually created seasons."""
        Item.objects.create(
            media_id="3",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.TV.value,
            title="Another TV Show",
            image="http://example.com/another.jpg",
        )

        Item.objects.create(
            media_id="3",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.SEASON.value,
            title="Another TV Show",
            image="http://example.com/another_s1.jpg",
            season_number=1,
        )

        for i in range(1, 3):
            Item.objects.create(
                media_id="3",
                source=Sources.MANUAL.value,
                media_type=MediaTypes.EPISODE.value,
                title=f"Episode {i}",
                image=f"http://example.com/another_s1e{i}.jpg",
                season_number=1,
                episode_number=i,
            )

        response = manual.season("3", 1)

        self.assertEqual(response["season_number"], 1)
        self.assertEqual(response["title"], "Another TV Show")
        self.assertEqual(response["season_title"], "Season 1")
        self.assertEqual(response["max_progress"], 2)
        self.assertEqual(len(response["episodes"]), 2)

    def test_manual_episode(self):
        """Test the episode method for manually created episodes."""
        Item.objects.create(
            media_id="4",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.TV.value,
            title="Third TV Show",
            image="http://example.com/third.jpg",
        )

        Item.objects.create(
            media_id="4",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.SEASON.value,
            title="Third TV Show",
            image="http://example.com/third_s1.jpg",
            season_number=1,
        )

        Item.objects.create(
            media_id="4",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.EPISODE.value,
            title="Special Episode",
            image="http://example.com/third_s1e1.jpg",
            season_number=1,
            episode_number=1,
        )

        response = manual.episode("4", 1, 1)

        self.assertEqual(response["media_type"], MediaTypes.EPISODE.value)
        self.assertEqual(response["title"], "Third TV Show")
        self.assertEqual(response["season_title"], "Season 1")
        self.assertEqual(response["episode_title"], "Special Episode")

        result = manual.episode("4", 1, 2)
        self.assertIsNone(result)

    def test_manual_process_episodes(self):
        """Test the process_episodes function for manual episodes."""
        Item.objects.create(
            media_id="5",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.TV.value,
            title="Process Episodes Test",
            image="http://example.com/process.jpg",
        )

        Item.objects.create(
            media_id="5",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.SEASON.value,
            title="Process Episodes Test",
            image="http://example.com/process_s1.jpg",
            season_number=1,
        )

        for i in range(1, 4):
            Item.objects.create(
                media_id="5",
                source=Sources.MANUAL.value,
                media_type=MediaTypes.EPISODE.value,
                title=f"Process Episode {i}",
                image=f"http://example.com/process_s1e{i}.jpg",
                season_number=1,
                episode_number=i,
            )

        season_metadata = {
            "season_number": 1,
            "episodes": [
                {
                    "media_id": "5",
                    "episode_number": 1,
                    "air_date": "2025-01-01",
                    "image": "http://example.com/process_s1e1.jpg",
                    "title": "Process Episode 1",
                },
                {
                    "media_id": "5",
                    "episode_number": 2,
                    "air_date": "2025-01-08",
                    "image": "http://example.com/process_s1e2.jpg",
                    "title": "Process Episode 2",
                },
                {
                    "media_id": "5",
                    "episode_number": 3,
                    "air_date": "2025-01-15",
                    "image": "http://example.com/process_s1e3.jpg",
                    "title": "Process Episode 3",
                },
            ],
        }

        ep_item1 = Item.objects.get(
            media_id="5",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=1,
        )
        ep_item2 = Item.objects.get(
            media_id="5",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=2,
        )

        episode_1 = Episode(item=ep_item1)
        episode_2 = Episode(item=ep_item2)

        episodes_in_db = [episode_1, episode_2]

        # Call process_episodes
        result = manual.process_episodes(season_metadata, episodes_in_db)

        self.assertEqual(len(result), 3)

        self.assertEqual(result[0]["episode_number"], 1)
        self.assertEqual(result[0]["title"], "Process Episode 1")
        self.assertEqual(result[0]["air_date"], "2025-01-01")
        self.assertTrue(result[0]["history"], [episode_1])

        self.assertEqual(result[1]["episode_number"], 2)
        self.assertEqual(result[1]["title"], "Process Episode 2")
        self.assertEqual(result[1]["air_date"], "2025-01-08")
        self.assertTrue(result[0]["history"], [episode_2])

        self.assertEqual(result[2]["episode_number"], 3)
        self.assertEqual(result[2]["title"], "Process Episode 3")
        self.assertEqual(result[2]["air_date"], "2025-01-15")
        self.assertFalse(result[2]["history"], [])

    def test_hardcover_get_tags(self):
        """Test the get_tags function from Hardcover provider."""
        tags_data = [{"tag": "Science Fiction"}, {"tag": "Fantasy"}]
        result = hardcover.get_tags(tags_data)
        self.assertEqual(result, ["Science Fiction", "Fantasy"])

        self.assertIsNone(hardcover.get_tags(None))

    def test_hardcover_get_ratings(self):
        """Test the get_ratings function from Hardcover provider."""
        self.assertEqual(hardcover.get_ratings(4.5), 4.5)

        self.assertIsNone(hardcover.get_ratings(None))

    def test_igdb_get_score(self):
        """Test the get_score function from IGDB provider."""
        self.assertEqual(igdb.get_score({"total_rating": 92.70730625238252}), 92.7)
        self.assertIsNone(igdb.get_score({}))

    def test_hardcover_get_edition_details(self):
        """Test the get_edition_details function from Hardcover provider."""
        edition_data = {
            "edition_format": "Paperback",
            "isbn_13": "9781234567890",
            "isbn_10": "1234567890",
            "publisher": {"name": "Test Publisher"},
        }

        result = hardcover.get_edition_details(edition_data)
        self.assertEqual(result["format"], "Paperback")
        self.assertEqual(result["publisher"], "Test Publisher")
        self.assertEqual(result["isbn"], ["1234567890", "9781234567890"])

        self.assertEqual(hardcover.get_edition_details(None), {})

        no_publisher = {
            "edition_format": "Paperback",
            "isbn_13": "9781234567890",
        }
        result = hardcover.get_edition_details(no_publisher)
        self.assertEqual(result["publisher"], None)

    def test_handle_error_hardcover_unauthorized(self):
        """Test the handle_error function with Hardcover unauthorized error."""
        mock_response = MagicMock()
        mock_response.status_code = 401  # Unauthorized
        mock_response.json.return_value = {"error": "Invalid API key"}

        error = requests.exceptions.HTTPError("401 Unauthorized")
        error.response = mock_response

        with self.assertRaises(services.ProviderAPIError) as cm:
            hardcover.handle_error(error)

        self.assertEqual(cm.exception.provider, Sources.HARDCOVER.value)

    def test_handle_error_hardcover_other(self):
        """Test the handle_error function with Hardcover other error."""
        mock_response = MagicMock()
        mock_response.status_code = 500  # Server error
        mock_response.json.return_value = {"error": "Server error"}

        error = requests.exceptions.HTTPError("500 Server Error")
        error.response = mock_response

        with self.assertRaises(services.ProviderAPIError) as cm:
            hardcover.handle_error(error)

        self.assertEqual(cm.exception.provider, Sources.HARDCOVER.value)

    def test_handle_error_hardcover_json_error(self):
        """Test the handle_error function with JSON decode error."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.side_effect = requests.exceptions.JSONDecodeError(
            "Invalid JSON",
            "",
            0,
        )

        error = requests.exceptions.HTTPError("500 Server Error")
        error.response = mock_response

        with self.assertRaises(services.ProviderAPIError) as cm:
            hardcover.handle_error(error)

        self.assertEqual(cm.exception.provider, Sources.HARDCOVER.value)
