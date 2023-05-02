from django.test import TestCase
from django.test import override_settings

from unittest.mock import patch
import shutil
import os

from app.utils.imports import anilist, mal, tmdb
from app.models import User, Media


class ImportsMAL(TestCase):
    def setUp(self):
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)
        os.makedirs("MAL")

    @override_settings(MEDIA_ROOT=("MAL"))
    def test_import_animelist(self):
        mal.import_myanimelist("bloodthirstiness", self.user)
        self.assertEqual(Media.objects.filter(user=self.user).count(), 6)
        self.assertEqual(
            Media.objects.filter(user=self.user, media_type="anime").count(), 4
        )
        self.assertEqual(
            Media.objects.filter(user=self.user, media_type="manga").count(), 2
        )
        self.assertEqual(
            Media.objects.get(user=self.user, title="Ama Gli Animali").image
            == "none.svg",
            True,
        )
        self.assertEqual(
            Media.objects.get(user=self.user, title="FLCL").status == "Paused", True
        )
        self.assertEqual(
            Media.objects.get(user=self.user, title="Fire Punch").score == 7, True
        )

    def tearDown(self):
        shutil.rmtree("MAL")


class ImportsTMDB(TestCase):
    def setUp(self):
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)
        os.makedirs("TMDB")

    @override_settings(MEDIA_ROOT=("TMDB"))
    @patch("requests.get")
    def test_import_tmdb(self, mock_data):
        mock_data.return_value.json.return_value = {
            "total_pages": 1,
            "results": [
                {
                    "id": 361743,
                    "title": "Top Gun: Maverick",
                    "rating": 7,
                    "poster_path": "/62HCnUTziyWcpDaBO2i1DX17ljH.jpg",
                },
                {
                    "id": 634649,
                    "title": "Spider-Man: No Way Home",
                    "rating": 7,
                    "poster_path": "/uJYYizSuA9Y3DCs0qS4qWvHfZg4.jpg",
                },
            ],
        }
        fake_url = "https://api.themoviedb.org/3/account/1/rated/movies?api_key=12345&session_id=12345"
        images, bulk_add_media = tmdb.process_media_list(
            fake_url, "movie", "Completed", self.user, bulk_add_media=[]
        )
        Media.objects.bulk_create(bulk_add_media)

        self.assertEqual(Media.objects.filter(user=self.user).count(), 2)
        self.assertEqual(
            Media.objects.filter(user=self.user, media_type="movie").count(), 2
        )
        self.assertEqual(
            Media.objects.get(user=self.user, media_id=634649).score == 7, True
        )
        self.assertEqual(
            Media.objects.get(user=self.user, media_id=361743).score == 7, True
        )

    def tearDown(self):
        shutil.rmtree("TMDB")


class ImportsAniList(TestCase):
    def setUp(self):
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)
        os.makedirs("AniList")

    @override_settings(MEDIA_ROOT=("AniList"))
    def test_import_anilist(self):
        anilist.import_anilist("bloodthirstiness", self.user)
        self.assertEqual(Media.objects.filter(user=self.user).count(), 6)
        self.assertEqual(
            Media.objects.filter(user=self.user, media_type="anime").count(), 4
        )
        self.assertEqual(
            Media.objects.filter(user=self.user, media_type="manga").count(), 2
        )
        self.assertEqual(
            Media.objects.get(user=self.user, title="FLCL").status == "Paused", True
        )
        self.assertEqual(
            Media.objects.get(user=self.user, title="One Punch-Man").score == 9, True
        )

    def tearDown(self):
        shutil.rmtree("AniList")
