from django.test import TestCase

from unittest.mock import patch, MagicMock
import json
import os

from app.utils.imports import (
    import_anilist,
    import_mal,
    import_tmdb_ratings,
    import_tmdb_watchlist,
    import_csv,
)
from app.exceptions import ImportSourceError
from app.models import User, Movie, Anime, Manga, TV, Season, Episode


mock_path = os.path.join(os.path.dirname(__file__), "mock_data")


class ImportMAL(TestCase):
    def setUp(self):
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)

    @patch("requests.get")
    @patch("app.utils.helpers.images_downloader", return_value=None)
    def test_import_animelist(self, mock_asyncio, mock_request):
        with open(mock_path + "/import_mal_anime.json", "r") as file:
            anime_response = json.load(file)
        with open(mock_path + "/import_mal_manga.json", "r") as file:
            manga_response = json.load(file)

        anime_mock = MagicMock()
        anime_mock.json.return_value = anime_response
        manga_mock = MagicMock()
        manga_mock.json.return_value = manga_response
        mock_request.side_effect = [anime_mock, manga_mock]

        import_mal("bloodthirstiness", self.user)
        self.assertEqual(Anime.objects.filter(user=self.user).count(), 4)
        self.assertEqual(Manga.objects.filter(user=self.user).count(), 2)
        self.assertEqual(
            Anime.objects.get(user=self.user, title="Ama Gli Animali").image
            == "none.svg",
            True,
        )
        self.assertEqual(
            Anime.objects.get(user=self.user, title="FLCL").status == "Paused", True
        )
        self.assertEqual(
            Manga.objects.get(user=self.user, title="Fire Punch").score == 7, True
        )

    def test_user_not_found(self):
        self.assertRaises(ImportSourceError, import_mal, "fhdsufdsu", self.user)


class ImportTMDB(TestCase):
    def setUp(self):
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)

    @patch("app.utils.helpers.images_downloader", return_value=None)
    def test_tmdb_import_ratings(self, mock_asyncio):
        with open(mock_path + "/import_tmdb_ratings.csv", "rb") as file:
            import_tmdb_ratings(file, self.user)
        print(Movie.objects.filter(user=self.user))
        self.assertEqual(Movie.objects.filter(user=self.user).count(), 2)
        self.assertEqual(TV.objects.filter(user=self.user).count(), 1)

    @patch("app.utils.helpers.images_downloader", return_value=None)
    def test_tmdb_import_watchlist(self, mock_asyncio):
        with open(mock_path + "/import_tmdb_watchlist.csv", "rb") as file:
            import_tmdb_watchlist(file, self.user)

        self.assertEqual(TV.objects.filter(user=self.user).count(), 0)
        self.assertEqual(Season.objects.filter(user=self.user).count(), 2)


class ImportAniList(TestCase):
    def setUp(self):
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)

    @patch("requests.post")
    @patch("app.utils.helpers.images_downloader", return_value=None)
    def test_import_anilist(self, mock_asyncio, mock_request):
        with open(mock_path + "/import_anilist.json", "r") as file:
            anilist_response = json.load(file)
        mock_request.return_value.json.return_value = anilist_response

        import_anilist("bloodthirstiness", self.user)
        self.assertEqual(Anime.objects.filter(user=self.user).count(), 4)
        self.assertEqual(Manga.objects.filter(user=self.user).count(), 2)
        self.assertEqual(
            Anime.objects.get(user=self.user, title="FLCL").status == "Paused", True
        )
        self.assertEqual(
            Manga.objects.get(user=self.user, title="One Punch-Man").score == 9, True
        )

    def test_user_not_found(self):
        self.assertRaises(ImportSourceError, import_anilist, "fhdsufdsu", self.user)


class ImportCSV(TestCase):
    def setUp(self):
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)

    @patch("app.utils.helpers.images_downloader", return_value=None)
    def test_import_csv(self, mock_asyncio):

        with open(mock_path + "/import_yamtrack.csv", "rb") as file:
            import_csv(file, self.user)

        self.assertEqual(Anime.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Manga.objects.filter(user=self.user).count(), 1)
        self.assertEqual(TV.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Movie.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Season.objects.filter(user=self.user).count(), 2)
        self.assertEqual(
            Episode.objects.filter(related_season__user=self.user).count(), 24
        )
