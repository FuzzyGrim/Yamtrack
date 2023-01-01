from django.test import TestCase, LiveServerTestCase
from django.urls import reverse
from django.contrib import auth
from django.test import override_settings
from django.conf import settings

import shutil
import os
import csv

from app.models import User, Media
from app.utils import api


class RegisterLoginUser(TestCase):
    def test_create_user(self):
        response = self.client.post(
            reverse("register"),
            {
                "username": "test",
                "default_api": "tmdb",
                "password1": "SMk5noPnqs",
                "password2": "SMk5noPnqs",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("login"))
        self.assertEqual(User.objects.count(), 1)

    def test_login_user(self):
        self.client.post(
            reverse("register"),
            {
                "username": "test",
                "default_api": "tmdb",
                "password1": "SMk5noPnqs",
                "password2": "SMk5noPnqs",
            },
        )

        response = self.client.post(
            reverse("login"),
            {
                "username": "test",
                "password": "SMk5noPnqs",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("home"))
        self.assertEqual(auth.get_user(self.client).username, "test")


class CreateMedia(TestCase):
    def setUp(self):
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)
        self.client.login(**self.credentials)


    @override_settings(MEDIA_ROOT=("test_CreateMedia/media"))
    def test_create_tmdb(self):
        self.assertEqual(
            Media.objects.filter(media_id=5895, user=self.user).exists(), False
        )

        response = self.client.post(
            reverse("search", args=["tmdb", "flcl"]),
            {
                "media_id": 5895,
                "title": "FLCL",
                "image": "/FkgA8CcmiLJGVCRYRQ2g2UfVtF.jpg",
                "api_origin": "tmdb",
                "status": "Completed",
                "score": 4,
                "season": 1,
                "media_type": "tv",
                "num_seasons": 3,
            },
        )

        response = self.client.get(reverse("home"))
        self.assertContains(response, "FLCL")
        self.assertEqual(
            Media.objects.filter(media_id=5895, user=self.user).exists(), True
        )
        self.assertEqual(
            os.path.exists(settings.MEDIA_ROOT + "/images/tmdb-FkgA8CcmiLJGVCRYRQ2g2UfVtF.jpg"),
            True,
        )

    @override_settings(MEDIA_ROOT=("test_CreateMedia/media"))
    def test_create_tmdb_middle_season(self):
        self.assertEqual(
            Media.objects.filter(media_id=1668, user=self.user).exists(), False
        )

        response = self.client.post(
            reverse("search", args=["tmdb", "flcl"]),
            {
                "media_id": 1668,
                "title": "Friends",
                "image": "/f496cm9enuEsZkSPzCwnTESEK5s.jpg",
                "api_origin": "tmdb",
                "status": "Completed",
                "score": 8,
                "season": 1,
                "media_type": "tv",
                "num_seasons": 10,
            },
        )

        response = self.client.get(reverse("home"))
        self.assertContains(response, "Friends")
        self.assertEqual(
            Media.objects.filter(media_id=1668, user=self.user).exists(), True
        )
        self.assertEqual(
            Media.objects.filter(media_id=1668, user=self.user, status="Completed").exists(), False
        )
        self.assertEqual(
            Media.objects.filter(media_id=1668, user=self.user, status="Watching").exists(), True
        )
        self.assertEqual(
            os.path.exists(settings.MEDIA_ROOT + "/images/tmdb-f496cm9enuEsZkSPzCwnTESEK5s.jpg"),
            True,
        )


    def tearDownClass():
        try:
            shutil.rmtree("test_CreateMedia")
        except OSError:
            pass



class EditMedia(TestCase):
    @override_settings(MEDIA_ROOT=("test_EditMedia/media"))
    def setUp(self):
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)
        self.client.login(**self.credentials)
        self.client.post(
            reverse("search", args=["tmdb", "flcl"]),
            {
                "media_id": 1668,
                "title": "Friends",
                "image": "/f496cm9enuEsZkSPzCwnTESEK5s.jpg",
                "api_origin": "tmdb",
                "status": "Completed",
                "score": 9,
                "season": 10,
                "media_type": "tv",
                "num_seasons": 10,
            },
        )

    def test_edit_tmdb_score(self):
        self.assertEqual(
            Media.objects.filter(media_id=1668, user=self.user, score=9).exists(), True
        )
        self.assertEqual(
            Media.objects.filter(media_id=1668, user=self.user, score=9.5).exists(), False
        )

        response = self.client.post(
            reverse("home"),
            {
                "media_id": 1668,
                "api_origin": "tmdb",
                "status": "Completed",
                "score": 9.5,
                "season": 10,
                "media_type": "tv",
                "num_seasons": 10,
            },
        )

        response = self.client.get(reverse("home"))
        self.assertContains(response, "9.5")
        self.assertEqual(
            Media.objects.filter(media_id=1668, user=self.user, score=9).exists(), False
        )
        self.assertEqual(
            Media.objects.filter(media_id=1668, user=self.user, score=9.5).exists(), True
        )

    def test_edit_tmdb_status(self):
        self.assertEqual(
            Media.objects.filter(
                media_id=1668, user=self.user, status="Completed"
            ).exists(),
            True,
        )
        self.assertEqual(
            Media.objects.filter(
                media_id=1668, user=self.user, status="Watching"
            ).exists(),
            False,
        )
        response = self.client.post(
            reverse("home"),
            {
                "media_id": 1668,
                "api_origin": "tmdb",
                "status": "Watching",
                "score": 9,
                "season": 10,
                "media_type": "tv",
                "num_seasons": 10,
            },
        )

        response = self.client.get(reverse("home"))
        self.assertContains(response, "Watching")
        self.assertEqual(
            Media.objects.filter(
                media_id=1668, user=self.user, status="Completed"
            ).exists(),
            False,
        )
        self.assertEqual(
            Media.objects.filter(
                media_id=1668, user=self.user, status="Watching"
            ).exists(),
            True,
        )

    def test_edit_tmdb_all(self):
        self.assertEqual(
            Media.objects.filter(
                media_id=1668, user=self.user, status="Completed"
            ).exists(),
            True,
        )
        self.assertEqual(
            Media.objects.filter(
                media_id=1668, user=self.user, status="Watching"
            ).exists(),
            False,
        )
        self.assertEqual(
            Media.objects.filter(media_id=1668, user=self.user, score=9).exists(), True
        )
        self.assertEqual(
            Media.objects.filter(media_id=1668, user=self.user, score=9.5).exists(), False
        )

        response = self.client.post(
            reverse("home"),
            {
                "media_id": 1668,
                "api_origin": "tmdb",
                "status": "Watching",
                "score": 9.5,
                "season": "all",
                "media_type": "tv",
                "num_seasons": 10,
            },
        )

        response = self.client.get(reverse("home"))
        self.assertContains(response, "Watching")
        self.assertContains(response, "9.5")
        self.assertEqual(
            Media.objects.filter(
                media_id=1668, user=self.user, status="Completed"
            ).exists(),
            False,
        )
        self.assertEqual(
            Media.objects.filter(
                media_id=1668, user=self.user, status="Watching"
            ).exists(),
            True,
        )
        self.assertEqual(
            Media.objects.filter(media_id=1668, user=self.user, score=9).exists(), False
        )
        self.assertEqual(
            Media.objects.filter(media_id=1668, user=self.user, score=9.5).exists(), True
        )

    def tearDownClass():
        try:
            shutil.rmtree("test_EditMedia")
        except OSError:
            pass


class Imports(LiveServerTestCase):
    def setUp(self):
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)


    @override_settings(MEDIA_ROOT=("test_Imports/media"))
    def test_import_animelist(self):
        api.import_myanimelist("bloodthirstiness", self.user)
        self.assertEqual(Media.objects.filter(user=self.user).count(), 6)
        self.assertEqual(Media.objects.filter(user=self.user, media_type="anime").count(), 4)
        self.assertEqual(Media.objects.filter(user=self.user, media_type="manga").count(), 2)
        self.assertEqual(Media.objects.get(user=self.user, title="Ama Gli Animali").image=="images/none.svg", True)
        self.assertEqual(Media.objects.get(user=self.user, title="FLCL").status=="Paused", True)
        self.assertEqual(Media.objects.get(user=self.user, title="Fire Punch").score==7, True)


    @override_settings(MEDIA_ROOT=("test_Imports/media"))
    def test_import_anilist(self):
        api.import_anilist("bloodthirstiness", self.user)
        self.assertEqual(Media.objects.filter(user=self.user).count(), 6)
        self.assertEqual(Media.objects.filter(user=self.user, media_type="anime").count(), 4)
        self.assertEqual(Media.objects.filter(user=self.user, media_type="manga").count(), 2)
        self.assertEqual(Media.objects.get(user=self.user, title="FLCL").status=="Paused", True)
        self.assertEqual(Media.objects.get(user=self.user, title="One Punch-Man").score==9, True)


    @override_settings(MEDIA_ROOT=("test_Imports/media"))
    def test_import_tmdb(self):
        file_path = os.path.join("test_Imports/ratings.csv")
        fields = ["TMDb ID", "IMDb ID", "Type", "Name", "Release Date", "Season Number", "Episode Number", "Rating", "Your Rating", "Date Rated"]
        data = [
            ["634649", "tt10872600", "movie", "Spider-Man: No Way Home", "2021-12-15T00:00:00Z", "", "", "8.022", "7", "2022-12-17T15:50:35Z"],
            ["1668", "tt0108778", "tv", "Friends", "1994-09-22T00:00:00Z", "", "", "8.463", "10", "2022-12-17T16:23:01Z"],
        ]
        with open(file_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(fields)
            writer.writerows(data)

        with open(file_path, "rb") as file:
            api.import_tmdb(file, self.user)
            self.assertEqual(Media.objects.filter(user=self.user).count(), 2)
            self.assertEqual(Media.objects.filter(user=self.user, media_type="movie").count(), 1)
            self.assertEqual(Media.objects.filter(user=self.user, media_type="tv").count(), 1)
            self.assertEqual(Media.objects.get(user=self.user, media_id=634649).score==7, True)
            self.assertEqual(Media.objects.get(user=self.user, media_id=1668).score==10, True)


    def tearDownClass():
        try:
            shutil.rmtree("test_Imports")
        except OSError:
            pass