from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings

from app.models import MediaTypes, Sources
from app.providers import (
    anilist,
    hardcover,
    igdb,
    mal,
    mangaupdates,
    openlibrary,
    tmdb,
)

mock_path = Path(__file__).resolve().parent.parent / "mock_data"

BREAKING_BAD_TMDB_ID = 1396
THE_MATRIX_TMDB_ID = 603


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

    @patch("app.providers.mangaupdates.services.api_request")
    def test_mangaupdates_nsfw_setting(self, mock_api_request):
        """Test MangaUpdates adult genres follow MU_NSFW, not MAL_NSFW."""
        mock_api_request.return_value = {"results": [], "total_hits": 0}

        with self.settings(MU_NSFW=False, MAL_NSFW=True):
            mangaupdates.search("mu nsfw disabled", 1)
        params = mock_api_request.call_args.kwargs["params"]
        self.assertIn("Hentai", params["exclude_genre"])

        with self.settings(MU_NSFW=True, MAL_NSFW=False):
            mangaupdates.search("mu nsfw enabled", 1)
        params = mock_api_request.call_args.kwargs["params"]
        self.assertNotIn("exclude_genre", params)

    def test_anilist_anime(self):
        """Test the search method for anime from AniList.

        Assert that all required keys are present in each entry.
        """
        response = anilist.search(MediaTypes.ANIME.value, "Cowboy Bebop", 1)
        required_keys = {"media_id", "media_type", "title", "image"}

        self.assertTrue(response["results"])
        for anime in response["results"]:
            self.assertTrue(all(key in anime for key in required_keys))
            self.assertEqual(anime["source"], Sources.ANILIST.value)

    def test_anilist_manga(self):
        """Test the search method for manga from AniList.

        Assert that all required keys are present in each entry.
        """
        response = anilist.search(MediaTypes.MANGA.value, "Berserk", 1)
        required_keys = {"media_id", "media_type", "title", "image"}

        self.assertTrue(response["results"])
        for manga in response["results"]:
            self.assertTrue(all(key in manga for key in required_keys))
            self.assertEqual(manga["media_type"], MediaTypes.MANGA.value)

    @patch("app.providers.anilist.services.api_request")
    def test_anilist_search_variables_and_pages(self, mock_api_request):
        """Test the AniList search excludes adult media and counts pages."""
        mock_api_request.return_value = {
            "data": {
                "Page": {
                    "pageInfo": {"hasNextPage": True},
                    "media": [
                        {
                            "id": 1,
                            "type": "ANIME",
                            "title": {"userPreferred": "Cowboy Bebop"},
                            "coverImage": {"large": "bebop.jpg"},
                        },
                    ],
                },
            },
        }

        response = anilist.search(MediaTypes.ANIME.value, "anilist pages", 2)

        variables = mock_api_request.call_args.kwargs["params"]["variables"]
        self.assertEqual(variables["type"], "ANIME")
        self.assertEqual(variables["page"], 2)
        self.assertFalse(variables["isAdult"])
        self.assertEqual(
            response["total_results"],
            settings.PER_PAGE + 1 + settings.PER_PAGE,
        )
        self.assertEqual(response["results"][0]["media_id"], 1)

    @override_settings(ANILIST_NSFW=True)
    @patch("app.providers.anilist.services.api_request")
    def test_anilist_search_nsfw(self, mock_api_request):
        """Test the AniList search leaves out the adult filter when allowed."""
        mock_api_request.return_value = {
            "data": {"Page": {"pageInfo": {"hasNextPage": False}, "media": []}},
        }

        anilist.search(MediaTypes.MANGA.value, "anilist nsfw", 1)

        variables = mock_api_request.call_args.kwargs["params"]["variables"]
        self.assertNotIn("isAdult", variables)

    def test_tv(self):
        """Test the search method for TV shows.

        Assert that all required keys are present in each entry.
        """
        response = tmdb.search(MediaTypes.TV.value, "Breaking Bad", 1)
        required_keys = {"media_id", "media_type", "title", "image"}

        for tv in response["results"]:
            self.assertTrue(all(key in tv for key in required_keys))

        breaking_bad = next(
            tv for tv in response["results"] if tv["media_id"] == BREAKING_BAD_TMDB_ID
        )
        self.assertEqual(breaking_bad["release_year"], 2008)

    def test_movie(self):
        """Test the search method for movies.

        Assert that all required keys are present in each entry.
        """
        response = tmdb.search(MediaTypes.MOVIE.value, "The Matrix", 1)
        required_keys = {"media_id", "media_type", "title", "image"}

        for movie in response["results"]:
            self.assertTrue(all(key in movie for key in required_keys))

        the_matrix = next(
            movie
            for movie in response["results"]
            if movie["media_id"] == THE_MATRIX_TMDB_ID
        )
        self.assertEqual(the_matrix["release_year"], 1999)

    def test_tmdb_release_year(self):
        """Test the release year is read from movie and TV date fields."""
        self.assertEqual(tmdb.get_release_year({"release_date": "1999-03-31"}), 1999)
        self.assertEqual(
            tmdb.get_release_year({"first_air_date": "2008-01-20"}),
            2008,
        )

    def test_tmdb_release_year_unknown(self):
        """Test the release year is None when the date is empty or missing."""
        self.assertIsNone(tmdb.get_release_year({"release_date": ""}))
        self.assertIsNone(tmdb.get_release_year({"first_air_date": ""}))
        self.assertIsNone(tmdb.get_release_year({}))

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


class SearchShowAdultTitles(TestCase):
    """Test the adult titles preference when the server allows adult content."""

    def assert_separate_cache_keys(self, mock_cache):
        """Check each preference value reads its own cached results."""
        first_key, second_key = (call.args[0] for call in mock_cache.get.call_args_list)
        self.assertNotEqual(first_key, second_key)

    @override_settings(TMDB_NSFW=True)
    @patch("app.providers.tmdb.cache")
    @patch("app.providers.tmdb.services.api_request")
    def test_tmdb(self, mock_api_request, mock_cache):
        """Test TMDB only asks for adult titles when the user allows them."""
        mock_cache.get.return_value = None
        mock_api_request.return_value = {"results": [], "total_results": 0}

        tmdb.search(MediaTypes.MOVIE.value, "test", 1, show_adult_titles=True)
        params = mock_api_request.call_args.kwargs["params"]
        self.assertEqual(params["include_adult"], "true")

        tmdb.search(MediaTypes.MOVIE.value, "test", 1, show_adult_titles=False)
        params = mock_api_request.call_args.kwargs["params"]
        self.assertNotIn("include_adult", params)

        self.assert_separate_cache_keys(mock_cache)

    @override_settings(MAL_NSFW=True)
    @patch("app.providers.mal.cache")
    @patch("app.providers.mal.services.api_request")
    def test_mal(self, mock_api_request, mock_cache):
        """Test MyAnimeList only asks for adult titles when the user allows them."""
        mock_cache.get.return_value = None
        mock_api_request.return_value = {"data": []}

        mal.search(MediaTypes.ANIME.value, "test", 1, show_adult_titles=True)
        params = mock_api_request.call_args.kwargs["params"]
        self.assertEqual(params["nsfw"], "true")

        mal.search(MediaTypes.ANIME.value, "test", 1, show_adult_titles=False)
        params = mock_api_request.call_args.kwargs["params"]
        self.assertNotIn("nsfw", params)

        self.assert_separate_cache_keys(mock_cache)

    @override_settings(IGDB_NSFW=True)
    @patch("app.providers.igdb.cache")
    @patch("app.providers.igdb.get_access_token", return_value="token")
    @patch("app.providers.igdb.services.api_request")
    def test_igdb(self, mock_api_request, _mock_token, mock_cache):
        """Test IGDB only excludes the erotic theme when the user hides adult titles."""
        mock_cache.get.return_value = None
        mock_api_request.return_value = []

        igdb.search("test", 1, show_adult_titles=True)
        self.assertNotIn("themes != (42)", mock_api_request.call_args.kwargs["data"])

        igdb.search("test", 1, show_adult_titles=False)
        self.assertIn("themes != (42)", mock_api_request.call_args.kwargs["data"])

        self.assert_separate_cache_keys(mock_cache)

    @override_settings(MU_NSFW=True)
    @patch("app.providers.mangaupdates.cache")
    @patch("app.providers.mangaupdates.services.api_request")
    def test_mangaupdates(self, mock_api_request, mock_cache):
        """Test MangaUpdates only excludes adult genres when the user hides them."""
        mock_cache.get.return_value = None
        mock_api_request.return_value = {"results": [], "total_hits": 0}

        mangaupdates.search("test", 1, show_adult_titles=True)
        params = mock_api_request.call_args.kwargs["params"]
        self.assertNotIn("exclude_genre", params)

        mangaupdates.search("test", 1, show_adult_titles=False)
        params = mock_api_request.call_args.kwargs["params"]
        self.assertIn("Hentai", params["exclude_genre"])

        self.assert_separate_cache_keys(mock_cache)

    @override_settings(ANILIST_NSFW=True)
    @patch("app.providers.anilist.cache")
    @patch("app.providers.anilist.services.api_request")
    def test_anilist(self, mock_api_request, mock_cache):
        """Test AniList only filters adult titles when the user hides them."""
        mock_cache.get.return_value = None
        mock_api_request.return_value = {
            "data": {"Page": {"pageInfo": {"hasNextPage": False}, "media": []}},
        }

        anilist.search(MediaTypes.MANGA.value, "test", 1, show_adult_titles=True)
        variables = mock_api_request.call_args.kwargs["params"]["variables"]
        self.assertNotIn("isAdult", variables)

        anilist.search(MediaTypes.MANGA.value, "test", 1, show_adult_titles=False)
        variables = mock_api_request.call_args.kwargs["params"]["variables"]
        self.assertFalse(variables["isAdult"])

        self.assert_separate_cache_keys(mock_cache)
