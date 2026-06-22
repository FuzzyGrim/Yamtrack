import os
import unittest
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
from app.providers.search_rank import normalize_search_text, rank_results

mock_path = Path(__file__).resolve().parent.parent / "mock_data"
RUN_PROVIDER_TESTS = os.environ.get("RUN_PROVIDER_TESTS") == "1"
requires_provider_network = unittest.skipUnless(
    RUN_PROVIDER_TESTS,
    "Set RUN_PROVIDER_TESTS=1 to run live provider API tests.",
)


class Search(TestCase):
    """Test the external API calls for media search."""

    @requires_provider_network
    def test_anime(self):
        """Test the search method for anime.

        Assert that all required keys are present in each entry.
        """
        response = mal.search(MediaTypes.ANIME.value, "Cowboy Bebop", 1)

        required_keys = {"media_id", "media_type", "title", "image"}

        for anime in response["results"]:
            self.assertTrue(all(key in anime for key in required_keys))

    @requires_provider_network
    def test_anime_not_found(self):
        """Test the search method for anime with no results."""
        response = mal.search(MediaTypes.ANIME.value, "q", 1)

        self.assertEqual(response["results"], [])

    @requires_provider_network
    def test_mangaupdates(self):
        """Test the search method for manga.

        Assert that all required keys are present in each entry.
        """
        response = mangaupdates.search("One Piece", 1)
        required_keys = {"media_id", "media_type", "title", "image"}

        for manga in response["results"]:
            self.assertTrue(all(key in manga for key in required_keys))

    @requires_provider_network
    def test_manga_not_found(self):
        """Test the search method for manga with no results."""
        response = mangaupdates.search("", 1)

        self.assertEqual(response["results"], [])

    @requires_provider_network
    def test_tv(self):
        """Test the search method for TV shows.

        Assert that all required keys are present in each entry.
        """
        response = tmdb.search(MediaTypes.TV.value, "Breaking Bad", 1)
        required_keys = {"media_id", "media_type", "title", "image"}

        for tv in response["results"]:
            self.assertTrue(all(key in tv for key in required_keys))

    @requires_provider_network
    def test_games(self):
        """Test the search method for games.

        Assert that all required keys are present in each entry.
        """
        response = igdb.search("Persona 5", 1)
        required_keys = {"media_id", "media_type", "title", "image"}

        for game in response["results"]:
            self.assertTrue(all(key in game for key in required_keys))

    @requires_provider_network
    def test_books(self):
        """Test the search method for books.

        Assert that all required keys are present in each entry.
        """
        response = openlibrary.search("The Name of the Wind", 1)
        required_keys = {"media_id", "media_type", "title", "image"}

        for book in response["results"]:
            self.assertTrue(all(key in book for key in required_keys))

    @requires_provider_network
    def test_comics(self):
        """Test the search method for comics.

        Assert that all required keys are present in each entry.
        """
        response = igdb.search("Batman", 1)
        required_keys = {"media_id", "media_type", "title", "image"}

        for comic in response["results"]:
            self.assertTrue(all(key in comic for key in required_keys))

    @requires_provider_network
    def test_hardcover(self):
        """Test the search method for books from Hardcover.

        Assert that all required keys are present in each entry.
        """
        response = hardcover.search("1984 George Orwell", 1)
        required_keys = {"media_id", "media_type", "title", "image"}

        self.assertTrue(len(response["results"]) > 0)

        for book in response["results"]:
            self.assertTrue(all(key in book for key in required_keys))

    @requires_provider_network
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

    def test_search_text_normalization_is_forgiving(self):
        """Test search matching ignores accents, symbols, and case."""
        self.assertEqual(normalize_search_text("Pokémon: Blue!"), "pokemon blue")
        self.assertEqual(normalize_search_text("  HARRY--Potter  "), "harry potter")

    def test_book_ranking_prefers_real_metadata_over_bare_exact_title(self):
        """Test useful book records outrank low-information exact-title shells."""
        results = [
            {"title": "Harry Potter", "media_type": MediaTypes.BOOK.value},
            {
                "title": "Harry Potter and the Philosopher's Stone",
                "media_type": MediaTypes.BOOK.value,
                "image": "https://example.com/hp1.jpg",
                "first_publish_year": 1997,
                "ratings_count": 100000,
                "author_name": ["J. K. Rowling"],
            },
        ]

        ranked = rank_results("harry potter", results, MediaTypes.BOOK.value)

        self.assertEqual(ranked[0]["title"], "Harry Potter and the Philosopher's Stone")

    def test_game_ranking_balances_popularity_and_relevance(self):
        """Test official popular games outrank low-signal spin-offs."""
        results = [
            {
                "title": "Pokemon Random Side Story",
                "media_type": MediaTypes.GAME.value,
                "total_rating_count": 2,
                "game_type": 6,
            },
            {
                "title": "Pokémon Red Version",
                "media_type": MediaTypes.GAME.value,
                "image": "https://example.com/red.jpg",
                "total_rating_count": 5000,
                "game_type": 0,
            },
        ]

        ranked = rank_results("pokemon", results, MediaTypes.GAME.value)

        self.assertEqual(ranked[0]["title"], "Pokémon Red Version")

    def test_exact_relevance_still_beats_unrelated_popularity(self):
        """Test popularity cannot bury an exact obscure title under unrelated hits."""
        results = [
            {
                "title": "The Popular Unrelated Game",
                "media_type": MediaTypes.GAME.value,
                "total_rating_count": 1000000,
                "game_type": 0,
            },
            {
                "title": "Obscure Quest",
                "media_type": MediaTypes.GAME.value,
                "image": "https://example.com/obscure.jpg",
                "game_type": 0,
            },
        ]

        ranked = rank_results("obscure quest", results, MediaTypes.GAME.value)

        self.assertEqual(ranked[0]["title"], "Obscure Quest")

    @patch("app.providers.igdb.cache")
    @patch("app.providers.igdb.get_access_token")
    @patch("app.providers.igdb.services.api_request")
    def test_igdb_search_uses_full_text_search(
        self,
        mock_api_request,
        mock_get_access_token,
        mock_cache,
    ):
        """Test game search does not use exact title substring matching."""
        mock_cache.get.return_value = None
        mock_get_access_token.return_value = "token"
        mock_api_request.return_value = [
            {
                "name": "SearchResults",
                "result": [
                    {
                        "id": 1,
                        "name": "Pokémon",
                    },
                ],
            },
            {
                "name": "TotalCount",
                "count": 1,
            },
        ]

        response = igdb.search('pokemon "blue"', 1)

        self.assertEqual(response["results"][0]["title"], "Pokémon")
        multiquery = mock_api_request.call_args.kwargs["data"]
        self.assertIn('search "pokemon \\"blue\\"";', multiquery)
        self.assertIn("total_rating_count", multiquery)
        self.assertIn("game_type", multiquery)
        self.assertNotIn("name ~", multiquery)

    @patch("app.providers.openlibrary.cache")
    @patch("app.providers.openlibrary.services.api_request")
    def test_openlibrary_search_requests_ranking_fields(
        self,
        mock_api_request,
        mock_cache,
    ):
        """Test OpenLibrary search asks for fields used by ranking."""
        mock_cache.get.return_value = None
        mock_api_request.return_value = {
            "numFound": 1,
            "docs": [
                {
                    "title": "Harry Potter and the Philosopher's Stone",
                    "editions": {
                        "docs": [
                            {
                                "key": "/books/OL82563M",
                                "title": "Harry Potter and the Philosopher's Stone",
                                "cover_i": 123,
                            },
                        ],
                    },
                    "ratings_count": 1000,
                    "ratings_average": 4.5,
                    "edition_count": 30,
                    "first_publish_year": 1997,
                    "author_name": ["J. K. Rowling"],
                },
            ],
        }

        response = openlibrary.search("harry potter", 1)

        fields = mock_api_request.call_args.kwargs["params"]["fields"]
        self.assertIn("ratings_count", fields)
        self.assertIn("edition_count", fields)
        self.assertNotIn("ratings_count", response["results"][0])
