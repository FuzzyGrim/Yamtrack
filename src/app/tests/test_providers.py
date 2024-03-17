import json
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase

from app.providers import mal, tmdb

mock_path = Path(__file__).resolve().parent / "mock_data"


class Search(TestCase):
    """Test the external API calls for media search."""

    def test_anime(self):
        """Test the search method for anime.

        Assert that all required keys are present in each entry.
        """
        response = mal.search("anime", "Cowboy Bebop")

        required_keys = {"media_id", "media_type", "format", "title", "image"}

        for anime in response:
            self.assertTrue(all(key in anime for key in required_keys))

    def test_anime_not_found(self):
        """Test the search method for anime with no results."""
        response = mal.search("anime", "q")

        self.assertEqual(response, [])

    def test_tv(self):
        """Test the search method for TV shows.

        Assert that all required keys are present in each entry.
        """
        response = tmdb.search("tv", "Breaking Bad")
        required_keys = {"media_id", "media_type", "format", "title", "image"}

        for tv in response:
            self.assertTrue(all(key in tv for key in required_keys))


class Metadata(TestCase):
    """Test the external API calls for media details."""

    def test_anime(self):
        """Test the metadata method for anime."""
        response = mal.anime("1")
        self.assertEqual(response["title"], "Cowboy Bebop")
        self.assertEqual(response["details"]["start_date"], "1998-04-03")
        self.assertEqual(response["details"]["status"], "Finished")
        self.assertEqual(response["details"]["number_of_episodes"], 26)

    @patch("requests.get")
    def test_anime_unknown(self, mock_data: "patch"):
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
        self.assertEqual(response["details"]["number_of_episodes"], "Unknown")
        self.assertEqual(response["details"]["runtime"], "Unknown")

    def test_manga(self):
        """Test the metadata method for manga."""
        response = mal.manga("1")
        self.assertEqual(response["title"], "Monster")
        self.assertEqual(response["details"]["start_date"], "1994-12-05")
        self.assertEqual(response["details"]["status"], "Finished")
        self.assertEqual(response["details"]["number_of_episodes"], 162)

    def test_tv(self):
        """Test the metadata method for TV shows."""
        response = tmdb.tv("1396")
        self.assertEqual(response["title"], "Breaking Bad")
        self.assertEqual(response["details"]["start_date"], "2008-01-20")
        self.assertEqual(response["details"]["status"], "Ended")
        self.assertEqual(response["details"]["number_of_episodes"], 62)

    def test_movie(self):
        """Test the metadata method for movies."""
        response = tmdb.movie("10494")
        self.assertEqual(response["title"], "Perfect Blue")
        self.assertEqual(response["details"]["start_date"], "1998-02-28")
        self.assertEqual(response["details"]["status"], "Released")
        self.assertEqual(response["details"]["number_of_episodes"], 1)

    @patch("requests.get")
    def test_movie_unknown(self, mock_data: "patch"):
        """Test the metadata method for movies with mostly unknown data."""
        with Path(mock_path / "metadata_movie_unknown.json").open() as file:
            movie_response = json.load(file)
        mock_data.return_value.json.return_value = movie_response
        mock_data.return_value.status_code = 200

        response = tmdb.movie("0")
        self.assertEqual(response["title"], "Unknown Movie")
        self.assertEqual(response["image"], settings.IMG_NONE)
        self.assertEqual(response["synopsis"], "No synopsis available.")
        self.assertEqual(response["details"]["start_date"], "Unknown")
        self.assertEqual(response["details"]["runtime"], "Unknown")
        self.assertEqual(response["details"]["genres"], "Unknown")
        self.assertEqual(response["details"]["studios"], "Unknown")
        self.assertEqual(response["details"]["country"], "Unknown")
        self.assertEqual(response["details"]["languages"], "Unknown")
