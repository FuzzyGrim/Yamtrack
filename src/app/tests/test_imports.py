from django.test import TestCase
from django.test import override_settings

from unittest.mock import patch, MagicMock
import json
import shutil
import os

from app.utils.imports import (
    import_anilist,
    import_mal,
    process_media_list,
    import_csv,
)

from app.models import User, Movie, Anime, Manga, TV, Season, Episode


mock_path = os.path.join(os.path.dirname(__file__), "mock_data")


class ImportMAL(TestCase):
    def setUp(self):
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)
        os.makedirs("MAL")

    @override_settings(MEDIA_ROOT=("MAL"))
    @patch("requests.get")
    def test_import_animelist(self, mock_get):
        with open(mock_path + "/user_mal_anime.json", "r") as file:
            anime_response = json.load(file)
        with open(mock_path + "/user_mal_manga.json", "r") as file:
            manga_response = json.load(file)

        anime_mock = MagicMock()
        anime_mock.json.return_value = anime_response
        manga_mock = MagicMock()
        manga_mock.json.return_value = manga_response
        mock_get.side_effect = [anime_mock, manga_mock]

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
        self.assertEqual(import_mal("fhdsufdsu", self.user), False)

    def tearDown(self):
        shutil.rmtree("MAL")


class ImportTMDB(TestCase):
    def setUp(self):
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)
        os.makedirs("TMDB")

    @override_settings(MEDIA_ROOT=("TMDB"))
    @patch("requests.get")
    def test_import_rated_movies(self, mock_data):
        with open(mock_path + "/user_rated_movies_tmdb.json", "r") as file:
            tmdb_response = json.load(file)
        mock_data.return_value.json.return_value = tmdb_response
        fake_url = "https://api.themoviedb.org/3/account/1/rated/movies?api_key=12345&session_id=12345"

        images, bulk_movies = process_media_list(
            fake_url, "movie", "Completed", self.user
        )

        Movie.objects.bulk_create(bulk_movies)
        self.assertEqual(Movie.objects.filter(user=self.user).count(), 2)
        self.assertEqual(
            Movie.objects.get(user=self.user, media_id=634649).score == 7, True
        )
        self.assertEqual(
            Movie.objects.get(user=self.user, media_id=361743).score == 7, True
        )

    def tearDown(self):
        shutil.rmtree("TMDB")


class ImportAniList(TestCase):
    def setUp(self):
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)
        os.makedirs("AniList")

    @override_settings(MEDIA_ROOT=("AniList"))
    @patch("requests.post")
    def test_import_anilist(self, mock_data):
        with open(mock_path + "/user_anilist.json", "r") as file:
            anilist_response = json.load(file)
        mock_data.return_value.json.return_value = anilist_response

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
        self.assertEqual(import_anilist("fhdsufdsu", self.user), "User not found")

    def tearDown(self):
        shutil.rmtree("AniList")


class ImportCSV(TestCase):
    def setUp(self):
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)

    @patch("app.utils.helpers.images_downloader", return_value=None)
    def test_import_csv(self, mock_asyncio):

        with open(mock_path + "/yamtrack.csv", "rb") as file:
            import_csv(file, self.user)

        self.assertEqual(Anime.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Manga.objects.filter(user=self.user).count(), 1)
        self.assertEqual(TV.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Movie.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Season.objects.filter(user=self.user).count(), 2)
        self.assertEqual(
            Episode.objects.filter(related_season__user=self.user).count(), 24
        )
