from pathlib import Path
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase

from app.models import MediaTypes, Sources
from app.providers import (
    hardcover,
    igdb,
    mal,
    mangaupdates,
    openlibrary,
    tmdb,
)

mock_path = Path(__file__).resolve().parent.parent / "mock_data"


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

        self.assertTrue(len(response["results"]) > 0)

        for book in response["results"]:
            self.assertTrue(all(key in book for key in required_keys))

    def test_hardcover_not_found(self):
        """Test the search method for books from Hardcover with no results."""
        response = hardcover.search("xjkqzptmvnsieurytowahdbfglc", 1)
        self.assertEqual(response["results"], [])

    @patch("app.providers.hardcover.services.api_request")
    def test_hardcover_title_query_is_capped(self, mock_api_request):
        """Test the long title is capped before search."""
        query = (
            "The Short Story of Architecture: A Pocket Guide to Key Styles, "
            "Buildings, Elements & Materials (Architectural History Introduction, "
            "A Guide to Architecture)"
        )
        capped_query = "The Short Story of Architecture: A Pocket Guide to"
        cache.delete(
            f"search_{Sources.HARDCOVER.value}_{MediaTypes.BOOK.value}_"
            f"{capped_query}_1",
        )
        mock_api_request.return_value = {
            "data": {
                "search": {
                    "results": {
                        "hits": [
                            {
                                "document": {
                                    "id": "123",
                                    "title": "The Short Story of Architecture",
                                    "image": {"url": "https://example.com/cover.jpg"},
                                },
                            },
                        ],
                        "found": 1,
                    },
                },
            },
        }

        response = hardcover.search(query, 1)
        required_keys = {"media_id", "media_type", "title", "image"}

        self.assertEqual(len(query), 156)
        self.assertEqual(hardcover.cap_search_query(query), capped_query)
        _, kwargs = mock_api_request.call_args
        self.assertEqual(kwargs["params"]["variables"]["query"], capped_query)
        self.assertTrue(len(response["results"]) > 0)

        for book in response["results"]:
            self.assertTrue(all(key in book for key in required_keys))

    def test_hardcover_title_query_cap_stops_at_word_boundary(self):
        """Test the long title cap does not split words."""
        query = "one two three four five six seven eight nine ten eleven twelve"

        self.assertEqual(
            hardcover.cap_search_query(query),
            "one two three four five six seven eight nine ten",
        )
