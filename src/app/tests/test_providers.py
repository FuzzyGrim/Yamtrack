import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests
from django.conf import settings
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

mock_path = Path(__file__).resolve().parent / "mock_data"


class Search(TestCase):
    """Test the external API calls for media search."""

    def test_anime(self):
        """Test the search method for anime.

        Assert that all required keys are present in each entry.
        """
        response = mal.search(MediaTypes.ANIME.value, "Cowboy Bebop", 1)

        required_keys = {"media_id", "media_type", "title", "image"}

        for anime in response["results"]:
            self.assertTrue(all(key in anime for key in required_keys))

    def test_anime_not_found(self):
        """Test the search method for anime with no results."""
        response = mal.search(MediaTypes.ANIME.value, "q", 1)

        self.assertEqual(response["results"], [])

    def test_mangaupdates(self):
        """Test the search method for manga.

        Assert that all required keys are present in each entry.
        """
        response = mangaupdates.search("One Piece", 1)
        required_keys = {"media_id", "media_type", "title", "image"}

        for manga in response["results"]:
            self.assertTrue(all(key in manga for key in required_keys))

    def test_manga_not_found(self):
        """Test the search method for manga with no results."""
        response = mangaupdates.search("", 1)

        self.assertEqual(response["results"], [])

    def test_tv(self):
        """Test the search method for TV shows.

        Assert that all required keys are present in each entry.
        """
        response = tmdb.search(MediaTypes.TV.value, "Breaking Bad", 1)
        required_keys = {"media_id", "media_type", "title", "image"}

        for tv in response["results"]:
            self.assertTrue(all(key in tv for key in required_keys))

    def test_games(self):
        """Test the search method for games.

        Assert that all required keys are present in each entry.
        """
        response = igdb.search("Persona 5", 1)
        required_keys = {"media_id", "media_type", "title", "image"}

        for game in response["results"]:
            self.assertTrue(all(key in game for key in required_keys))

    def test_books(self):
        """Test the search method for books.

        Assert that all required keys are present in each entry.
        """
        response = openlibrary.search("The Name of the Wind", 1)
        required_keys = {"media_id", "media_type", "title", "image"}

        for book in response["results"]:
            self.assertTrue(all(key in book for key in required_keys))

    def test_comics(self):
        """Test the search method for comics.

        Assert that all required keys are present in each entry.
        """
        response = igdb.search("Batman", 1)
        required_keys = {"media_id", "media_type", "title", "image"}

        for comic in response["results"]:
            self.assertTrue(all(key in comic for key in required_keys))

    def test_hardcover(self):
        """Test the search method for books from Hardcover.

        Assert that all required keys are present in each entry.
        """
        response = hardcover.search("1984 George Orwell", 1)
        required_keys = {"media_id", "media_type", "title", "image"}

        # Ensure we got results
        self.assertTrue(len(response["results"]) > 0)

        for book in response["results"]:
            self.assertTrue(all(key in book for key in required_keys))

    def test_hardcover_not_found(self):
        """Test the search method for books from Hardcover with no results."""
        # Using a very specific query that shouldn't match any books
        response = hardcover.search("xjkqzptmvnsieurytowahdbfglc", 1)
        self.assertEqual(response["results"], [])


class Metadata(TestCase):
    """Test the external API calls for media details."""

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

    def test_manga(self):
        """Test the metadata method for manga."""
        response = mal.manga("1")
        self.assertEqual(response["title"], "Monster")
        self.assertEqual(response["details"]["start_date"], "1994-12-05")
        self.assertEqual(response["details"]["status"], "Finished")
        self.assertEqual(response["details"]["number_of_chapters"], 162)

    def test_mangaupdates(self):
        """Test the metadata method for manga from mangaupdates."""
        response = mangaupdates.manga("72274276213")
        self.assertEqual(response["title"], "Monster")
        self.assertEqual(response["details"]["year"], "1994")
        self.assertEqual(response["details"]["format"], "Manga")

    def test_tv(self):
        """Test the metadata method for TV shows."""
        response = tmdb.tv("1396")
        self.assertEqual(response["title"], "Breaking Bad")
        self.assertEqual(response["details"]["first_air_date"], "2008-01-20")
        self.assertEqual(response["details"]["status"], "Ended")
        self.assertEqual(response["details"]["episodes"], 62)

    def test_tmdb_process_episodes(self):
        """Test the process_episodes function for TMDB episodes."""
        # Create test data
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

        # Create episodes
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

        # Create a sample season metadata structure
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
                },
                {
                    "episode_number": 2,
                    "air_date": "2008-01-27",
                    "still_path": "/path/to/still2.jpg",
                    "name": "Cat's in the Bag...",
                    "overview": "overview of the episode",
                },
                {
                    "episode_number": 3,
                    "air_date": "2008-02-10",
                    "still_path": "/path/to/still3.jpg",
                    "name": "...And the Bag's in the River",
                    "overview": "overview of the episode",
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

        # Verify results
        self.assertEqual(len(result), 3)

        # Check first episode
        self.assertEqual(result[0]["episode_number"], 1)
        self.assertEqual(result[0]["title"], "Pilot")
        self.assertEqual(result[0]["air_date"], "2008-01-20")
        self.assertTrue(result[0]["history"], [episode_1])

        # Check second episode
        self.assertEqual(result[1]["episode_number"], 2)
        self.assertEqual(result[1]["title"], "Cat's in the Bag...")
        self.assertEqual(result[1]["air_date"], "2008-01-27")
        self.assertTrue(result[1]["history"], [episode_2])

        # Check third episode (not watched)
        self.assertEqual(result[2]["episode_number"], 3)
        self.assertEqual(result[2]["title"], "...And the Bag's in the River")
        self.assertEqual(result[2]["air_date"], "2008-02-10")
        self.assertFalse(result[2]["history"], [])

    @patch("app.providers.tmdb.tv_with_seasons")
    def test_tmdb_episode(self, mock_tv_with_seasons):
        """Test the episode method for TMDB episodes."""
        # Create a mock response for tv_with_seasons
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

        # Test getting an existing episode
        result = tmdb.episode("1396", "1", "1")

        # Verify the result
        self.assertEqual(result["title"], "Breaking Bad")
        self.assertEqual(result["season_title"], "Season 1")
        self.assertEqual(result["episode_title"], "Pilot")
        self.assertEqual(result["image"], tmdb.get_image_url("/path/to/still1.jpg"))

        # Test getting a non-existent episode
        result = tmdb.episode("1396", "1", "3")
        self.assertIsNone(result)

        # Verify tv_with_seasons was called with correct parameters
        mock_tv_with_seasons.assert_called_with("1396", ["1"])

    def test_tmdb_find_next_episode(self):
        """Test the find_next_episode function."""
        # Create sample episodes metadata
        episodes_metadata = [
            {"episode_number": 1, "title": "Episode 1"},
            {"episode_number": 2, "title": "Episode 2"},
            {"episode_number": 3, "title": "Episode 3"},
        ]

        # Test finding next episode in the middle
        next_episode = tmdb.find_next_episode(1, episodes_metadata)
        self.assertEqual(next_episode, 2)

        # Test finding next episode at the end
        next_episode = tmdb.find_next_episode(3, episodes_metadata)
        self.assertIsNone(next_episode)

        # Test finding next episode for non-existent episode
        next_episode = tmdb.find_next_episode(5, episodes_metadata)
        self.assertIsNone(next_episode)

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

    def test_book(self):
        """Test the metadata method for books."""
        response = openlibrary.book("OL21733390M")
        self.assertEqual(response["title"], "Nineteen Eighty-Four")
        self.assertEqual(response["details"]["author"], ["George Orwell"])

    def test_comic(self):
        """Test the metadata method for comics."""
        response = comicvine.comic("155969")
        self.assertEqual(response["title"], "Ultimate Spider-Man")

    def test_hardcover_book(self):
        """Test the metadata method for books from Hardcover."""
        response = hardcover.book("377193")
        self.assertEqual(response["title"], "The Great Gatsby")
        self.assertEqual(response["details"]["author"], "F. Scott Fitzgerald")
        self.assertEqual(response["details"]["publisher"], "imusti")
        self.assertEqual(response["details"]["publish_date"], "1920-06-01")
        self.assertEqual(response["details"]["number_of_pages"], 180)
        self.assertEqual(response["details"]["format"], "Paperback")
        # Testing that we have some of the expected genres
        self.assertIn("Fiction", response["genres"])
        self.assertIn("Young Adult", response["genres"])
        self.assertIn("Classics", response["genres"])
        # Rating is approximately 4.21 * 2 = 8.42
        self.assertAlmostEqual(response["score"], 7.4, delta=0.1)

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
        self.assertIsNone(response["score"])

    def test_manual_tv(self):
        """Test the metadata method for manually created TV shows."""
        # Create test data
        Item.objects.create(
            media_id="1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.TV.value,
            title="Manual TV Show",
            image="http://example.com/manual.jpg",
        )

        # Create a season
        Item.objects.create(
            media_id="1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.SEASON.value,
            title="Manual TV Show",
            image="http://example.com/manual_s1.jpg",
            season_number=1,
        )

        # Create episodes
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

        # Test metadata
        response = manual.metadata("1", MediaTypes.TV.value)

        # Check basic fields
        self.assertEqual(response["title"], "Manual TV Show")
        self.assertEqual(response["media_id"], "1")
        self.assertEqual(response["source"], Sources.MANUAL.value)
        self.assertEqual(response["media_type"], MediaTypes.TV.value)
        self.assertEqual(response["synopsis"], "No synopsis available.")

        # Check season and episode data
        self.assertEqual(response["details"]["seasons"], 1)
        self.assertEqual(response["details"]["episodes"], 3)
        self.assertEqual(response["max_progress"], 3)
        self.assertEqual(len(response["related"]["seasons"]), 1)

        # Check season data
        season_data = response["season/1"]
        self.assertEqual(season_data["season_number"], 1)
        self.assertEqual(season_data["max_progress"], 3)
        self.assertEqual(len(season_data["episodes"]), 3)

    def test_manual_movie(self):
        """Test the metadata method for manually created movies."""
        # Create test data
        Item.objects.create(
            media_id="2",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MOVIE.value,
            title="Manual Movie",
            image="http://example.com/manual_movie.jpg",
        )

        # Test metadata
        response = manual.metadata("2", MediaTypes.MOVIE.value)

        # Check basic fields
        self.assertEqual(response["title"], "Manual Movie")
        self.assertEqual(response["media_id"], "2")
        self.assertEqual(response["source"], Sources.MANUAL.value)
        self.assertEqual(response["media_type"], MediaTypes.MOVIE.value)
        self.assertEqual(response["synopsis"], "No synopsis available.")
        self.assertEqual(response["max_progress"], 1)

    def test_manual_season(self):
        """Test the season method for manually created seasons."""
        # Create test data
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

        # Create episodes
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

        # Test season metadata
        response = manual.season("3", 1)

        # Check season data
        self.assertEqual(response["season_number"], 1)
        self.assertEqual(response["title"], "Another TV Show")
        self.assertEqual(response["season_title"], "Season 1")
        self.assertEqual(response["max_progress"], 2)
        self.assertEqual(len(response["episodes"]), 2)

    def test_manual_episode(self):
        """Test the episode method for manually created episodes."""
        # Create test data
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

        # Test episode metadata
        response = manual.episode("4", 1, 1)

        # Check episode data
        self.assertEqual(response["media_type"], MediaTypes.EPISODE.value)
        self.assertEqual(response["title"], "Third TV Show")
        self.assertEqual(response["season_title"], "Season 1")
        self.assertEqual(response["episode_title"], "Special Episode")

    def test_manual_process_episodes(self):
        """Test the process_episodes function for manual episodes."""
        # Create test data
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

        # Create episodes
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

        # Create season metadata structure
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

        # Verify results
        self.assertEqual(len(result), 3)

        # Check first episode (watched)
        self.assertEqual(result[0]["episode_number"], 1)
        self.assertEqual(result[0]["title"], "Process Episode 1")
        self.assertEqual(result[0]["air_date"], "2025-01-01")
        self.assertTrue(result[0]["history"], [episode_1])

        # Check second episode (watched with repeats)
        self.assertEqual(result[1]["episode_number"], 2)
        self.assertEqual(result[1]["title"], "Process Episode 2")
        self.assertEqual(result[1]["air_date"], "2025-01-08")
        self.assertTrue(result[0]["history"], [episode_2])

        # Check third episode (not watched)
        self.assertEqual(result[2]["episode_number"], 3)
        self.assertEqual(result[2]["title"], "Process Episode 3")
        self.assertEqual(result[2]["air_date"], "2025-01-15")
        self.assertFalse(result[2]["history"], [])

    def test_hardcover_get_tags(self):
        """Test the get_tags function from Hardcover provider."""
        tags_data = [{"tag": "Science Fiction"}, {"tag": "Fantasy"}]
        result = hardcover.get_tags(tags_data)
        self.assertEqual(result, ["Science Fiction", "Fantasy"])

        # Test with None
        self.assertIsNone(hardcover.get_tags(None))

    def test_hardcover_get_ratings(self):
        """Test the get_ratings function from Hardcover provider."""
        # Test with 4.5 rating (scaled to 10)
        self.assertEqual(hardcover.get_ratings(4.5), 9.0)

        # Test with None
        self.assertIsNone(hardcover.get_ratings(None))

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

        # Test with None
        self.assertEqual(hardcover.get_edition_details(None), {})

        # Test with missing publisher
        no_publisher = {
            "edition_format": "Paperback",
            "isbn_13": "9781234567890",
        }
        result = hardcover.get_edition_details(no_publisher)
        self.assertEqual(result["publisher"], None)

    def test_hardcover_get_recommendations(self):
        """Test the get_recommendations function from Hardcover provider."""
        recs_data = [
            {
                "item_book": {
                    "id": 123,
                    "title": "Book 1",
                    "cached_image": "https://example.com/book1.jpg",
                },
            },
            {
                "item_book": {
                    "id": 456,
                    "title": "Book 2",
                    "cached_image": None,
                },
            },
        ]

        result = hardcover.get_recommendations(recs_data)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["media_id"], 123)
        self.assertEqual(result[0]["title"], "Book 1")
        self.assertEqual(result[0]["image"], "https://example.com/book1.jpg")
        self.assertEqual(result[1]["image"], settings.IMG_NONE)

        # Test with None
        self.assertEqual(hardcover.get_recommendations(None), [])

    def test_handle_error_hardcover_unauthorized(self):
        """Test the handle_error function with Hardcover unauthorized error."""
        # Setup mock response for unauthorized error
        mock_response = MagicMock()
        mock_response.status_code = 401  # Unauthorized
        mock_response.json.return_value = {"error": "Invalid API key"}

        # Create HTTP error with this response
        error = requests.exceptions.HTTPError("401 Unauthorized")
        error.response = mock_response

        # Call the function and expect it to raise ProviderAPIError
        with self.assertRaises(services.ProviderAPIError) as cm:
            hardcover.handle_error(error)

        # Verify the exception contains the correct source and details
        self.assertEqual(cm.exception.provider, Sources.HARDCOVER.value)

    def test_handle_error_hardcover_other(self):
        """Test the handle_error function with Hardcover other error."""
        # Setup mock response for other error
        mock_response = MagicMock()
        mock_response.status_code = 500  # Server error
        mock_response.json.return_value = {"error": "Server error"}

        # Create HTTP error with this response
        error = requests.exceptions.HTTPError("500 Server Error")
        error.response = mock_response

        # Call the function and expect it to raise ProviderAPIError
        with self.assertRaises(services.ProviderAPIError) as cm:
            hardcover.handle_error(error)

        # Verify the exception contains the correct source
        self.assertEqual(cm.exception.provider, Sources.HARDCOVER.value)

    def test_handle_error_hardcover_json_error(self):
        """Test the handle_error function with JSON decode error."""
        # Setup mock response that returns invalid JSON
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.side_effect = requests.exceptions.JSONDecodeError(
            "Invalid JSON",
            "",
            0,
        )

        # Create HTTP error with this response
        error = requests.exceptions.HTTPError("500 Server Error")
        error.response = mock_response

        # Call the function and expect it to raise ProviderAPIError
        with self.assertRaises(services.ProviderAPIError) as cm:
            hardcover.handle_error(error)

        # Verify the exception contains the correct source
        self.assertEqual(cm.exception.provider, Sources.HARDCOVER.value)


class ServicesTests(TestCase):
    """Test the services module functions."""

    @patch("app.providers.services.session.get")
    def test_api_request_get(self, mock_get):
        """Test the api_request function with GET method."""
        # Setup mock response
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": "test"}
        mock_get.return_value = mock_response

        # Call the function
        result = services.api_request(
            "TEST",
            "GET",
            "https://example.com/api",
            params={"param": "value"},
        )

        # Verify the result
        self.assertEqual(result, {"data": "test"})

        # Verify the request was made correctly
        mock_get.assert_called_once()
        args, kwargs = mock_get.call_args
        self.assertEqual(kwargs["url"], "https://example.com/api")
        self.assertEqual(kwargs["params"], {"param": "value"})
        self.assertIn("timeout", kwargs)

    @patch("app.providers.services.session.post")
    def test_api_request_post(self, mock_post):
        """Test the api_request function with POST method."""
        # Setup mock response
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": "test"}
        mock_post.return_value = mock_response

        # Call the function
        result = services.api_request(
            "TEST",
            "POST",
            "https://example.com/api",
            params={"json_param": "value"},
            data={"form_data": "value"},
        )

        # Verify the result
        self.assertEqual(result, {"data": "test"})

        # Verify the request was made correctly
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(kwargs["url"], "https://example.com/api")
        self.assertEqual(kwargs["json"], {"json_param": "value"})
        self.assertEqual(kwargs["data"], {"form_data": "value"})
        self.assertIn("timeout", kwargs)

    @patch("app.providers.services.api_request")
    def test_request_error_handling_rate_limit(self, mock_api_request):
        """Test the request_error_handling function with rate limiting."""
        # Setup mock response for rate limit error
        mock_response = MagicMock()
        mock_response.status_code = 429  # Too many requests
        mock_response.headers = {"Retry-After": "5"}

        # Create HTTP error with this response
        error = requests.exceptions.HTTPError("429 Too Many Requests")
        error.response = mock_response

        # Setup mock for recursive api_request call
        mock_api_request.return_value = {"data": "retry_success"}

        # Call the function
        result = services.api_request(
            error,
            "TEST",
            "GET",
            "https://example.com/api",
            {"param": "value"},
            None,
            None,
        )

        # Verify api_request was called again
        mock_api_request.assert_called_once()

        # Verify the result
        self.assertEqual(result, {"data": "retry_success"})

    @patch("app.providers.igdb.cache.delete")
    def test_handle_error_igdb_unauthorized(
        self,
        mock_cache_delete,
    ):
        """Test the handle_error function with IGDB unauthorized error."""
        # Setup mock response for unauthorized error
        mock_response = MagicMock()
        mock_response.status_code = 401  # Unauthorized

        # Create HTTP error with this response
        error = requests.exceptions.HTTPError("401 Unauthorized")
        error.response = mock_response

        # Call the function
        result = igdb.handle_error(error)

        # Verify cache delete was called with correct key
        mock_cache_delete.assert_called_once_with("igdb_access_token")

        # Verify the result indicates retry should be attempted
        self.assertEqual(result, {"retry": True})

    def test_handle_error_igdb_bad_request(self):
        """Test the handle_error function with IGDB bad request error."""
        # Setup mock response for bad request error
        mock_response = MagicMock()
        mock_response.status_code = 400  # Bad Request
        mock_response.json.return_value = {"message": "Invalid query"}

        # Create HTTP error with this response
        error = requests.exceptions.HTTPError("400 Bad Request")
        error.response = mock_response

        # Call the function and expect it to raise ProviderAPIError
        with self.assertRaises(services.ProviderAPIError) as cm:
            igdb.handle_error(error)

        # Verify the exception contains the correct source
        self.assertEqual(cm.exception.provider, Sources.IGDB.value)

    def test_handle_error_tmdb_unauthorized(self):
        """Test the handle_error function with TMDB unauthorized error."""
        # Setup mock response for unauthorized error
        mock_response = MagicMock()
        mock_response.status_code = 401  # Unauthorized
        mock_response.json.return_value = {"status_message": "Invalid API key"}

        # Create HTTP error with this response
        error = requests.exceptions.HTTPError("401 Unauthorized")
        error.response = mock_response

        # Call the function and expect it to raise ProviderAPIError
        with self.assertRaises(services.ProviderAPIError) as cm:
            tmdb.handle_error(error)

        # Verify the exception contains the correct source
        self.assertEqual(cm.exception.provider, Sources.TMDB.value)

    def test_handle_error_mal_forbidden(self):
        """Test the handle_error function with MAL forbidden error."""
        # Setup mock response for forbidden error
        mock_response = MagicMock()
        mock_response.status_code = 403  # Forbidden
        mock_response.json.return_value = {"message": "Forbidden"}

        # Create HTTP error with this response
        error = requests.exceptions.HTTPError("403 Forbidden")
        error.response = mock_response

        # Call the function and expect it to raise ProviderAPIError
        with self.assertRaises(services.ProviderAPIError) as cm:
            mal.handle_error(error)

        # Verify the exception contains the correct source
        self.assertEqual(cm.exception.provider, Sources.MAL.value)

    @patch("app.providers.mal.anime")
    def test_get_media_metadata_anime(self, mock_anime):
        """Test the get_media_metadata function for anime."""
        # Setup mock
        mock_anime.return_value = {"title": "Test Anime"}

        # Call the function
        result = services.get_media_metadata(
            MediaTypes.ANIME.value,
            "1",
            Sources.MAL.value,
        )

        # Verify the result
        self.assertEqual(result, {"title": "Test Anime"})

        # Verify the correct function was called
        mock_anime.assert_called_once_with("1")

    @patch("app.providers.mangaupdates.manga")
    def test_get_media_metadata_manga_mangaupdates(self, mock_manga):
        """Test the get_media_metadata function for manga from MangaUpdates."""
        # Setup mock
        mock_manga.return_value = {"title": "Test Manga"}

        # Call the function
        result = services.get_media_metadata(
            MediaTypes.MANGA.value,
            "1",
            Sources.MANGAUPDATES.value,
        )

        # Verify the result
        self.assertEqual(result, {"title": "Test Manga"})

        # Verify the correct function was called
        mock_manga.assert_called_once_with("1")

    @patch("app.providers.mal.manga")
    def test_get_media_metadata_manga_mal(self, mock_manga):
        """Test the get_media_metadata function for manga from MAL."""
        # Setup mock
        mock_manga.return_value = {"title": "Test Manga"}

        # Call the function
        result = services.get_media_metadata(
            MediaTypes.MANGA.value,
            "1",
            Sources.MAL.value,
        )

        # Verify the result
        self.assertEqual(result, {"title": "Test Manga"})

        # Verify the correct function was called
        mock_manga.assert_called_once_with("1")

    @patch("app.providers.tmdb.tv")
    def test_get_media_metadata_tv(self, mock_tv):
        """Test the get_media_metadata function for TV shows."""
        # Setup mock
        mock_tv.return_value = {"title": "Test TV"}

        # Call the function
        result = services.get_media_metadata(
            MediaTypes.TV.value,
            "1",
            Sources.TMDB.value,
        )

        # Verify the result
        self.assertEqual(result, {"title": "Test TV"})

        # Verify the correct function was called
        mock_tv.assert_called_once_with("1")

    @patch("app.providers.tmdb.tv_with_seasons")
    def test_get_media_metadata_tv_with_seasons(self, mock_tv_with_seasons):
        """Test the get_media_metadata function for TV shows with seasons."""
        # Setup mock
        mock_tv_with_seasons.return_value = {"title": "Test TV with Seasons"}

        # Call the function
        result = services.get_media_metadata(
            "tv_with_seasons",
            "1",
            Sources.TMDB.value,
            season_numbers=[1, 2],
        )

        # Verify the result
        self.assertEqual(result, {"title": "Test TV with Seasons"})

        # Verify the correct function was called
        mock_tv_with_seasons.assert_called_once_with("1", [1, 2])

    @patch("app.providers.tmdb.tv_with_seasons")
    def test_get_media_metadata_season(self, mock_tv_with_seasons):
        """Test the get_media_metadata function for TV seasons."""
        # Setup mock
        mock_tv_with_seasons.return_value = {
            "season/1": {"title": "Test Season"},
        }

        # Call the function
        result = services.get_media_metadata(
            MediaTypes.SEASON.value,
            "1",
            Sources.TMDB.value,
            season_numbers=[1],
        )

        # Verify the result
        self.assertEqual(result, {"title": "Test Season"})

        # Verify the correct function was called
        mock_tv_with_seasons.assert_called_once_with("1", [1])

    @patch("app.providers.tmdb.episode")
    def test_get_media_metadata_episode(self, mock_episode):
        """Test the get_media_metadata function for TV episodes."""
        # Setup mock
        mock_episode.return_value = {"title": "Test Episode"}

        # Call the function
        result = services.get_media_metadata(
            MediaTypes.EPISODE.value,
            "1",
            Sources.TMDB.value,
            season_numbers=[1],
            episode_number="2",
        )

        # Verify the result
        self.assertEqual(result, {"title": "Test Episode"})

        # Verify the correct function was called
        mock_episode.assert_called_once_with("1", 1, "2")

    @patch("app.providers.tmdb.movie")
    def test_get_media_metadata_movie(self, mock_movie):
        """Test the get_media_metadata function for movies."""
        # Setup mock
        mock_movie.return_value = {"title": "Test Movie"}

        # Call the function
        result = services.get_media_metadata(
            MediaTypes.MOVIE.value,
            "1",
            Sources.TMDB.value,
        )

        # Verify the result
        self.assertEqual(result, {"title": "Test Movie"})

        # Verify the correct function was called
        mock_movie.assert_called_once_with("1")

    @patch("app.providers.igdb.game")
    def test_get_media_metadata_game(self, mock_game):
        """Test the get_media_metadata function for games."""
        # Setup mock
        mock_game.return_value = {"title": "Test Game"}

        # Call the function
        result = services.get_media_metadata(
            MediaTypes.GAME.value,
            "1",
            Sources.IGDB.value,
        )

        # Verify the result
        self.assertEqual(result, {"title": "Test Game"})

        # Verify the correct function was called
        mock_game.assert_called_once_with("1")

    @patch("app.providers.comicvine.comic")
    def test_get_media_metadata_comic(self, mock_comic):
        """Test the get_media_metadata function for comics."""
        # Setup mock
        mock_comic.return_value = {"title": "Test Comic"}

        # Call the function
        result = services.get_media_metadata(
            MediaTypes.COMIC.value,
            "1",
            Sources.COMICVINE.value,
        )

        # Verify the result
        self.assertEqual(result, {"title": "Test Comic"})

        # Verify the correct function was called
        mock_comic.assert_called_once_with("1")

    @patch("app.providers.openlibrary.book")
    def test_get_media_metadata_book(self, mock_book):
        """Test the get_media_metadata function for books."""
        # Setup mock
        mock_book.return_value = {"title": "Test Book"}

        # Call the function
        result = services.get_media_metadata(
            MediaTypes.BOOK.value,
            "1",
            Sources.OPENLIBRARY.value,
        )

        # Verify the result
        self.assertEqual(result, {"title": "Test Book"})

        # Verify the correct function was called
        mock_book.assert_called_once_with("1")

    @patch("app.providers.manual.metadata")
    def test_get_media_metadata_manual(self, mock_metadata):
        """Test the get_media_metadata function for manual media."""
        # Setup mock
        mock_metadata.return_value = {"title": "Test Manual"}

        # Call the function
        result = services.get_media_metadata(
            MediaTypes.MOVIE.value,
            "1",
            Sources.MANUAL.value,
        )

        # Verify the result
        self.assertEqual(result, {"title": "Test Manual"})

        # Verify the correct function was called
        mock_metadata.assert_called_once_with("1", MediaTypes.MOVIE.value)

    @patch("app.providers.manual.season")
    def test_get_media_metadata_manual_season(self, mock_season):
        """Test the get_media_metadata function for manual seasons."""
        # Setup mock
        mock_season.return_value = {"title": "Test Manual Season"}

        # Call the function
        result = services.get_media_metadata(
            MediaTypes.SEASON.value,
            "1",
            Sources.MANUAL.value,
            season_numbers=[1],
        )

        # Verify the result
        self.assertEqual(result, {"title": "Test Manual Season"})

        # Verify the correct function was called
        mock_season.assert_called_once_with("1", 1)

    @patch("app.providers.manual.episode")
    def test_get_media_metadata_manual_episode(self, mock_episode):
        """Test the get_media_metadata function for manual episodes."""
        # Setup mock
        mock_episode.return_value = {"title": "Test Manual Episode"}

        # Call the function
        result = services.get_media_metadata(
            MediaTypes.EPISODE.value,
            "1",
            Sources.MANUAL.value,
            season_numbers=[1],
            episode_number="2",
        )

        # Verify the result
        self.assertEqual(result, {"title": "Test Manual Episode"})

        # Verify the correct function was called
        mock_episode.assert_called_once_with("1", 1, "2")

    @patch("app.providers.hardcover.book")
    def test_get_media_metadata_hardcover_book(self, mock_book):
        """Test the get_media_metadata function for books from Hardcover."""
        # Setup mock
        mock_book.return_value = {"title": "Test Hardcover Book"}

        # Call the function
        result = services.get_media_metadata(
            MediaTypes.BOOK.value,
            "1",
            Sources.HARDCOVER.value,
        )

        # Verify the result
        self.assertEqual(result, {"title": "Test Hardcover Book"})

        # Verify the correct function was called
        mock_book.assert_called_once_with("1")

    @patch("app.providers.mal.search")
    def test_search_anime(self, mock_search):
        """Test the search function for anime."""
        # Setup mock
        mock_search.return_value = [{"title": "Test Anime"}]

        # Call the function
        result = services.search(MediaTypes.ANIME.value, "test", 1)

        # Verify the result
        self.assertEqual(result, [{"title": "Test Anime"}])

        # Verify the correct function was called
        mock_search.assert_called_once_with(MediaTypes.ANIME.value, "test", 1)

    @patch("app.providers.mangaupdates.search")
    def test_search_manga_mangaupdates(self, mock_search):
        """Test the search function for manga from MangaUpdates."""
        # Setup mock
        mock_search.return_value = [{"title": "Test Manga"}]

        # Call the function
        result = services.search(
            MediaTypes.MANGA.value,
            "test",
            1,
            source=Sources.MANGAUPDATES.value,
        )

        # Verify the result
        self.assertEqual(result, [{"title": "Test Manga"}])

        # Verify the correct function was called
        mock_search.assert_called_once_with("test", 1)

    @patch("app.providers.mal.search")
    def test_search_manga_mal(self, mock_search):
        """Test the search function for manga from MAL."""
        # Setup mock
        mock_search.return_value = [{"title": "Test Manga"}]

        # Call the function
        result = services.search(MediaTypes.MANGA.value, "test", 1)

        # Verify the result
        self.assertEqual(result, [{"title": "Test Manga"}])

        # Verify the correct function was called
        mock_search.assert_called_once_with(MediaTypes.MANGA.value, "test", 1)

    @patch("app.providers.tmdb.search")
    def test_search_tv(self, mock_search):
        """Test the search function for TV shows."""
        # Setup mock
        mock_search.return_value = [{"title": "Test TV"}]

        # Call the function
        result = services.search(MediaTypes.TV.value, "test", 1)

        # Verify the result
        self.assertEqual(result, [{"title": "Test TV"}])

        # Verify the correct function was called
        mock_search.assert_called_once_with(MediaTypes.TV.value, "test", 1)

    @patch("app.providers.tmdb.search")
    def test_search_movie(self, mock_search):
        """Test the search function for movies."""
        # Setup mock
        mock_search.return_value = [{"title": "Test Movie"}]

        # Call the function
        result = services.search(MediaTypes.MOVIE.value, "test", 1)

        # Verify the result
        self.assertEqual(result, [{"title": "Test Movie"}])

        # Verify the correct function was called
        mock_search.assert_called_once_with(MediaTypes.MOVIE.value, "test", 1)

    @patch("app.providers.igdb.search")
    def test_search_game(self, mock_search):
        """Test the search function for games."""
        # Setup mock
        mock_search.return_value = [{"title": "Test Game"}]

        # Call the function
        result = services.search(MediaTypes.GAME.value, "test", 1)

        # Verify the result
        self.assertEqual(result, [{"title": "Test Game"}])

        # Verify the correct function was called
        mock_search.assert_called_once_with("test", 1)

    @patch("app.providers.hardcover.search")
    def test_search_hardcover_book(self, mock_search):
        """Test the search function for books from Hardcover."""
        # Setup mock
        mock_search.return_value = [{"title": "Test Hardcover Book"}]

        # Call the function
        result = services.search(
            MediaTypes.BOOK.value,
            "test",
            1,
            source=Sources.HARDCOVER.value,
        )

        # Verify the result
        self.assertEqual(result, [{"title": "Test Hardcover Book"}])

        # Verify the correct function was called
        mock_search.assert_called_once_with("test", 1)

    @patch("app.providers.openlibrary.search")
    def test_search_openlibrary_book(self, mock_search):
        """Test the search function for books."""
        # Setup mock
        mock_search.return_value = [{"title": "Test Book"}]

        # Call the function
        result = services.search(
            MediaTypes.BOOK.value,
            "test",
            1,
            source=Sources.OPENLIBRARY.value,
        )

        # Verify the result
        self.assertEqual(result, [{"title": "Test Book"}])

        # Verify the correct function was called
        mock_search.assert_called_once_with("test", 1)

    @patch("app.providers.comicvine.search")
    def test_search_comic(self, mock_search):
        """Test the search function for comics."""
        # Setup mock
        mock_search.return_value = [{"title": "Test Comic"}]

        # Call the function
        result = services.search(MediaTypes.COMIC.value, "test", 1)

        # Verify the result
        self.assertEqual(result, [{"title": "Test Comic"}])

        # Verify the correct function was called
        mock_search.assert_called_once_with("test", 1)
