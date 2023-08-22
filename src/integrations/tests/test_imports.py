import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.models import TV, Anime, Episode, Manga, Movie, Season, User
from django.test import TestCase

from integrations.exceptions import ImportSourceError
from integrations.utils import imports

mock_path = os.path.join(os.path.dirname(__file__), "mock_data")


class ImportMAL(TestCase):
    """Test importing media from MyAnimeList."""

    def setUp(self) -> None:
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)

    @patch("requests.get")
    @patch("app.utils.helpers.images_downloader", return_value=None)
    def test_import_animelist(self, mock_asyncio, mock_request) -> None:
        """Basic test importing anime and manga from MyAnimeList."""

        with open(mock_path + "/import_mal_anime.json") as file:
            anime_response = json.load(file)
        with open(mock_path + "/import_mal_manga.json") as file:
            manga_response = json.load(file)

        anime_mock = MagicMock()
        anime_mock.json.return_value = anime_response
        manga_mock = MagicMock()
        manga_mock.json.return_value = manga_response
        mock_request.side_effect = [anime_mock, manga_mock]

        imports.mal_data("bloodthirstiness", self.user)
        self.assertEqual(Anime.objects.filter(user=self.user).count(), 4)
        self.assertEqual(Manga.objects.filter(user=self.user).count(), 2)
        self.assertEqual(
            Anime.objects.get(user=self.user, title="Ama Gli Animali").image
            == "none.svg",
            True,
        )
        self.assertEqual(
            Anime.objects.get(user=self.user, title="FLCL").status == "Paused",
            True,
        )
        self.assertEqual(
            Manga.objects.get(user=self.user, title="Fire Punch").score
            == 7,  # noqa: PLR2004
            True,
        )

    def test_user_not_found(self) -> None:
        """Test that an error is raised if the user is not found."""

        self.assertRaises(ImportSourceError, imports.mal_data, "fhdsufdsu", self.user)


class ImportTMDB(TestCase):
    """Test importing media from TMDB."""

    def setUp(self) -> None:
        """Create user for the tests."""

        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)

    @patch("app.utils.helpers.images_downloader", return_value=None)
    def test_tmdb_import_ratings(self, mock_asyncio) -> None:
        """Test importing ratings from TMDB."""

        with open(mock_path + "/import_tmdb_ratings.csv", "rb") as file:
            imports.tmdb_data(file, self.user, "Completed")
        self.assertEqual(Movie.objects.filter(user=self.user).count(), 2)
        self.assertEqual(TV.objects.filter(user=self.user).count(), 1)

    @patch("app.utils.helpers.images_downloader", return_value=None)
    def test_tmdb_import_watchlist(self, mock_asyncio) -> None:
        """Test importing watchlist from TMDB."""

        with open(mock_path + "/import_tmdb_watchlist.csv", "rb") as file:
            imports.tmdb_data(file, self.user, "Planning")

        self.assertEqual(TV.objects.filter(user=self.user).count(), 0)
        self.assertEqual(Season.objects.filter(user=self.user).count(), 2)


class ImportAniList(TestCase):
    """Test importing media from AniList."""

    def setUp(self) -> None:
        """Create user for the tests."""

        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)

    @patch("requests.post")
    @patch("app.utils.helpers.images_downloader", return_value=None)
    def test_import_anilist(self, mock_asyncio, mock_request) -> None:
        """Basic test importing anime and manga from AniList."""

        with open(mock_path + "/import_anilist.json") as file:
            anilist_response = json.load(file)
        mock_request.return_value.json.return_value = anilist_response

        imports.anilist_data("bloodthirstiness", self.user)
        self.assertEqual(Anime.objects.filter(user=self.user).count(), 4)
        self.assertEqual(Manga.objects.filter(user=self.user).count(), 2)
        self.assertEqual(
            Anime.objects.get(user=self.user, title="FLCL").status == "Paused",
            True,
        )
        self.assertEqual(
            Manga.objects.get(user=self.user, title="One Punch-Man").score == 9, # noqa: PLR2004
            True,
        )

    def test_user_not_found(self) -> None:
        """Test that an error is raised if the user is not found."""

        self.assertRaises(
            ImportSourceError, imports.anilist_data, "fhdsufdsu", self.user
        )


class ImportYamtrack(TestCase):
    """Test importing media from Yamtrack CSV."""

    def setUp(self) -> None:
        """Create user for the tests."""

        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)

    @patch("app.utils.helpers.images_downloader", return_value=None)
    def test_import_yamtrack(self, mock_asyncio) -> None:
        """Basic test importing media from Yamtrack."""

        with open(mock_path + "/import_yamtrack.csv", "rb") as file:
            imports.yamtrack_data(file, self.user)

        self.assertEqual(Anime.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Manga.objects.filter(user=self.user).count(), 1)
        self.assertEqual(TV.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Movie.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Season.objects.filter(user=self.user).count(), 2)
        self.assertEqual(
            Episode.objects.filter(related_season__user=self.user).count(), 24,
        )
