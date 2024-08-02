import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests
from app.models import TV, Anime, Episode, Manga, Movie, Season
from django.conf import settings
from django.test import TestCase
from users.models import User

from integrations.imports import anilist, mal, tmdb, yamtrack

mock_path = Path(__file__).resolve().parent / "mock_data"


class ImportMAL(TestCase):
    """Test importing media from MyAnimeList."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)

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
        self.user = User.objects.create_user(**self.credentials)

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
        self.user = User.objects.create_user(**self.credentials)

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
        self.user = User.objects.create_user(**self.credentials)

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
