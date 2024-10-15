import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import TV, Anime, Episode, Item, Manga, Movie, Season
from integrations.imports import anilist, kitsu, mal, simkl, tmdb, trakt, yamtrack

mock_path = Path(__file__).resolve().parent / "mock_data"
app_mock_path = (
    Path(__file__).resolve().parent.parent.parent / "app" / "tests" / "mock_data"
)


class ImportMAL(TestCase):
    """Test importing media from MyAnimeList."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

    @patch("requests.Session.get")
    def test_import_animelist(self, mock_request):
        """Basic test importing anime and manga from MyAnimeList."""
        with Path(mock_path / "import_mal_anime.json").open() as file:
            anime_response = json.load(file)
        with Path(mock_path / "import_mal_manga.json").open() as file:
            manga_response = json.load(file)

        anime_mock = MagicMock()
        anime_mock.json.return_value = anime_response
        manga_mock = MagicMock()
        manga_mock.json.return_value = manga_response
        mock_request.side_effect = [anime_mock, manga_mock]

        mal.importer("bloodthirstiness", self.user)
        self.assertEqual(Anime.objects.filter(user=self.user).count(), 4)
        self.assertEqual(Manga.objects.filter(user=self.user).count(), 2)
        self.assertEqual(
            Anime.objects.get(
                user=self.user,
                item__title="Ama Gli Animali",
            ).item.image,
            settings.IMG_NONE,
        )
        self.assertEqual(
            Anime.objects.get(user=self.user, item__title="FLCL").status,
            "Paused",
        )
        self.assertEqual(
            Manga.objects.get(user=self.user, item__title="Fire Punch").score,
            7,
        )

    def test_user_not_found(self):
        """Test that an error is raised if the user is not found."""
        self.assertRaises(
            requests.exceptions.HTTPError,
            mal.importer,
            "fhdsufdsu",
            self.user,
        )


class ImportTMDB(TestCase):
    """Test importing media from TMDB."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

    def test_tmdb_import_ratings(self):
        """Test importing ratings from TMDB."""
        with Path(mock_path / "import_tmdb_ratings.csv").open("rb") as file:
            tmdb.importer(file, self.user, "Completed")
        self.assertEqual(Movie.objects.filter(user=self.user).count(), 2)
        self.assertEqual(TV.objects.filter(user=self.user).count(), 1)

    def test_tmdb_import_watchlist(self):
        """Test importing watchlist from TMDB."""
        with Path(mock_path / "import_tmdb_watchlist.csv").open("rb") as file:
            tmdb.importer(file, self.user, "Planning")

        self.assertEqual(TV.objects.filter(user=self.user).count(), 2)


class ImportAniList(TestCase):
    """Test importing media from AniList."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

    @patch("requests.Session.post")
    def test_import_anilist(self, mock_request):
        """Basic test importing anime and manga from AniList."""
        with Path(mock_path / "import_anilist.json").open() as file:
            anilist_response = json.load(file)
        mock_request.return_value.json.return_value = anilist_response

        anilist.importer("bloodthirstiness", self.user)
        self.assertEqual(Anime.objects.filter(user=self.user).count(), 4)
        self.assertEqual(Manga.objects.filter(user=self.user).count(), 2)
        self.assertEqual(
            Anime.objects.get(user=self.user, item__title="FLCL").status,
            "Paused",
        )
        self.assertEqual(
            Manga.objects.get(user=self.user, item__title="One Punch-Man").score,
            9,
        )

    def test_user_not_found(self):
        """Test that an error is raised if the user is not found."""
        self.assertRaises(
            requests.exceptions.HTTPError,
            anilist.importer,
            "fhdsufdsu",
            self.user,
        )


class ImportYamtrack(TestCase):
    """Test importing media from Yamtrack CSV."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

    def test_import_yamtrack(self):
        """Basic test importing media from Yamtrack."""
        with Path(mock_path / "import_yamtrack.csv").open("rb") as file:
            yamtrack.importer(file, self.user)

        self.assertEqual(Anime.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Manga.objects.filter(user=self.user).count(), 1)
        self.assertEqual(TV.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Movie.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Season.objects.filter(user=self.user).count(), 1)
        self.assertEqual(
            Episode.objects.filter(related_season__user=self.user).count(),
            24,
        )


class ImportKitsu(TestCase):
    """Test importing media from Kitsu."""

    def setUp(self):
        """Create user for the tests."""
        credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**credentials)

        with Path(mock_path / "import_kitsu_anime.json").open() as file:
            self.sample_anime_response = json.load(file)

        with Path(mock_path / "import_kitsu_manga.json").open() as file:
            self.sample_manga_response = json.load(file)

    @patch("app.providers.services.api_request")
    def test_get_kitsu_id(self, mock_api_request):
        """Test getting Kitsu ID from username."""
        mock_api_request.return_value = {
            "data": [{"id": "12345"}],
        }
        kitsu_id = kitsu.get_kitsu_id("testuser")
        self.assertEqual(kitsu_id, "12345")

    @patch("app.providers.services.api_request")
    def test_get_media_response(self, mock_api_request):
        """Test getting media response from Kitsu."""
        mock_api_request.side_effect = [
            self.sample_anime_response,
            self.sample_manga_response,
        ]

        num_anime_imported, num_manga_imported, warning_message = (
            kitsu.import_by_user_id("123", self.user)
        )

        self.assertEqual(num_anime_imported, 5)
        self.assertEqual(num_manga_imported, 5)
        self.assertEqual(warning_message, "")

        # Check if the media was imported
        self.assertEqual(Anime.objects.count(), 5)

    def test_get_rating(self):
        """Test getting rating from Kitsu."""
        self.assertEqual(kitsu.get_rating(20), 10)
        self.assertEqual(kitsu.get_rating(10), 5)
        self.assertEqual(kitsu.get_rating(1), 0.5)
        self.assertIsNone(kitsu.get_rating(None))

    def test_get_date(self):
        """Test getting date from Kitsu."""
        self.assertEqual(kitsu.get_date("2023-01-01T00:00:00.000Z"), date(2023, 1, 1))
        self.assertIsNone(kitsu.get_date(None))

    def test_get_status(self):
        """Test getting status from Kitsu."""
        self.assertEqual(kitsu.get_status("completed"), "Completed")
        self.assertEqual(kitsu.get_status("current"), "In progress")
        self.assertEqual(kitsu.get_status("planned"), "Planning")
        self.assertEqual(kitsu.get_status("on_hold"), "Paused")

    def test_process_entry(self):
        """Test processing an entry from Kitsu."""
        entry = self.sample_anime_response["data"][0]
        media_lookup = {
            item["id"]: item
            for item in self.sample_anime_response["included"]
            if item["type"] == "anime"
        }
        mapping_lookup = {
            item["id"]: item
            for item in self.sample_anime_response["included"]
            if item["type"] == "mappings"
        }

        instance = kitsu.process_entry(
            entry,
            "anime",
            media_lookup,
            mapping_lookup,
            None,
            self.user,
        )

        self.assertEqual(instance.item.media_id, "1")
        self.assertIsInstance(instance, Anime)
        self.assertEqual(instance.score, 9)
        self.assertEqual(instance.progress, 26)
        self.assertEqual(instance.status, "Completed")
        self.assertEqual(instance.repeats, 1)
        self.assertEqual(instance.start_date, date(2023, 8, 1))
        self.assertEqual(instance.end_date, date(2023, 9, 1))
        self.assertEqual(instance.notes, "Great series!")


class ImportTrakt(TestCase):
    """Test importing media from Trakt."""

    def setUp(self):
        """Create user for the tests."""
        credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**credentials)

    @patch("integrations.imports.trakt.get_mal_mappings")
    @patch("integrations.imports.trakt.get_response")
    def test_importer_anime(self, mock_api_request, mock_get_mal_mappings):
        """Test importing media from Trakt."""
        # Mock the MAL mappings
        mock_get_mal_mappings.side_effect = [
            {(30857, 1): 1},  # shows mapping
            {(554,): 1},  # movies mapping
        ]

        # Mock API responses
        mock_api_request.side_effect = [
            [
                {
                    "show": {"title": "Example", "ids": {"trakt": 30857}},
                    "seasons": [
                        {
                            "number": 1,
                            "episodes": [
                                {
                                    "number": 1,
                                    "last_watched_at": "2023-01-01T00:00:00.000Z",
                                    "plays": 1,
                                },
                                {
                                    "number": 2,
                                    "last_watched_at": "2023-01-02T00:00:00.000Z",
                                    "plays": 2,
                                },
                            ],
                        },
                    ],
                },
            ],
            [],  # empty movie history
            [],  # empty watchlist
            [],  # empty ratings
        ]

        trakt.importer("testuser", self.user)

        self.assertEqual(Item.objects.count(), 1)

    @patch("integrations.imports.trakt.get_mal_mappings")
    @patch("integrations.imports.trakt.get_response")
    def test_importer_movie(self, mock_api_request, mock_get_mal_mappings):
        """Test importing media from Trakt."""
        # Mock the MAL mappings
        mock_get_mal_mappings.side_effect = [
            {(30857, 1): 1},  # shows mapping
            {(554,): 1},  # movies mapping
        ]

        # Mock API responses
        mock_api_request.side_effect = [
            [],  # empty show history
            [
                {
                    "movie": {"title": "Example", "ids": {"trakt": 554, "tmdb": 680}},
                    "last_watched_at": "2023-01-01T00:00:00.000Z",
                    "plays": 2,
                },
            ],
            [],  # empty watchlist
            [],  # empty ratings
        ]

        trakt.importer("testuser", self.user)

        self.assertEqual(Item.objects.count(), 1)

    def test_process_watched_shows(self):
        """Test processing watched shows from Trakt."""
        with Path(mock_path / "import_trakt_watched.json").open() as file:
            watched = json.load(file)

        trakt.process_watched_shows(watched, {}, self.user)

        self.assertEqual(Item.objects.count(), 15)
        self.assertEqual(Episode.objects.count(), 13)

    def test_process_ratings(self):
        """Test processing watched shows from Trakt."""
        with Path(mock_path / "import_trakt_ratings.json").open() as file:
            ratings = json.load(file)

        trakt.process_list(ratings, {}, {}, self.user, "ratings")

        self.assertEqual(Item.objects.count(), 1)
        movie = Movie.objects.first()
        self.assertEqual(movie.score, 8)

    def test_get_date(self):
        """Test getting date from Trakt."""
        self.assertEqual(trakt.get_date("2023-01-01T00:00:00.000Z"), date(2023, 1, 1))
        self.assertIsNone(trakt.get_date(None))


class ImportSimkl(TestCase):
    """Test importing media from SIMKL."""

    def setUp(self):
        """Create user for the tests."""
        credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**credentials)

    @patch("integrations.imports.simkl.get_user_list")
    def test_importer(
        self,
        user_list,
    ):
        """Test importing media from SIMKL."""
        # Mock API response
        user_list.return_value = {
            "shows": [
                {
                    "show": {"title": "Breaking Bad", "ids": {"tmdb": 1396}},
                    "status": "watching",
                    "user_rating": 8,
                    "seasons": [
                        {
                            "number": 1,
                            "episodes": [
                                {"number": 1, "watched_at": "2023-01-01T00:00:00Z"},
                                {"number": 2, "watched_at": "2023-01-02T00:00:00Z"},
                            ],
                        },
                    ],
                },
            ],
            "movies": [
                {
                    "movie": {"title": "Perfect Blue", "ids": {"tmdb": 10494}},
                    "status": "completed",
                    "user_rating": 9,
                    "last_watched_at": "2023-02-01T00:00:00Z",
                },
            ],
            "anime": [
                {
                    "show": {"title": "Example Anime", "ids": {"mal": 1}},
                    "status": "plantowatch",
                    "user_rating": 7,
                    "watched_episodes_count": 0,
                    "last_watched_at": None,
                },
            ],
        }

        tv_count, movie_count, anime_count, warnings = simkl.importer(
            "token",
            self.user,
        )

        # Check the results
        self.assertEqual(tv_count, 1)
        self.assertEqual(movie_count, 1)
        self.assertEqual(anime_count, 1)
        self.assertEqual(warnings, "")

        # Check TV show
        tv_item = Item.objects.get(media_type="tv")
        self.assertEqual(tv_item.title, "Breaking Bad")
        tv_obj = TV.objects.get(item=tv_item)
        self.assertEqual(tv_obj.status, "In progress")
        self.assertEqual(tv_obj.score, 8)

        # Check Movie
        movie_item = Item.objects.get(media_type="movie")
        self.assertEqual(movie_item.title, "Perfect Blue")
        movie_obj = Movie.objects.get(item=movie_item)
        self.assertEqual(movie_obj.status, "Completed")
        self.assertEqual(movie_obj.score, 9)

        # Check Anime
        anime_item = Item.objects.get(media_type="anime")
        self.assertEqual(anime_item.title, "Cowboy Bebop")
        anime_obj = Anime.objects.get(item=anime_item)
        self.assertEqual(anime_obj.status, "Planning")
        self.assertEqual(anime_obj.score, 7)

    def test_get_status(self):
        """Test mapping SIMKL status to internal status."""
        self.assertEqual(simkl.get_status("completed"), "Completed")
        self.assertEqual(simkl.get_status("watching"), "In progress")
        self.assertEqual(simkl.get_status("plantowatch"), "Planning")
        self.assertEqual(simkl.get_status("hold"), "Paused")
        self.assertEqual(simkl.get_status("dropped"), "Dropped")
        self.assertEqual(simkl.get_status("unknown"), "In progress")  # Default case

    def test_get_date(self):
        """Test getting date from SIMKL."""
        self.assertEqual(simkl.get_date("2023-01-01T00:00:00Z"), date(2023, 1, 1))
        self.assertIsNone(simkl.get_date(None))
