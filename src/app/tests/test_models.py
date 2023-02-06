from django.test import TestCase
from django.urls import reverse
from django.contrib import auth
from django.test import override_settings
from django.conf import settings

import shutil
import os

from app.models import User, Media, Season


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

        session = self.client.session
        session["metadata"] = {'id': 5895, 'title': 'FLCL', 'image': '/FkgA8CcmiLJGVCRYRQ2g2UfVtF.jpg', 
                               'api': 'tmdb', 'media_type': 'tv', 'number_of_seasons': 4,
                               'seasons':[{"episode_count":6, "season_number": 1},
                                          {"episode_count":6, "season_number": 2},
                                          {"episode_count":6, "season_number": 3},
                                          {"episode_count":0, "season_number": 4}]}
        session.save()
        response = self.client.post(
            reverse("search") + "?api=tmdb&q=flcl",
            {
                "status": "Completed",
                "score": 4,
                "progress": "",
                "season": 1,
                "start": "2023-01-01",
                "end": "2023-01-02",
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
        media = Media(
            media_id=1668,
            title="Friends",
            image="/f496cm9enuEsZkSPzCwnTESEK5s.jpg",
            media_type="tv",
            score=9,
            progress=18,
            user=self.user,
            status="Completed",
            api="tmdb",
            start_date="2023-01-01",
            end_date="2023-01-02",
        )
        media.save()
        
        season = Season(
            media=media,
            number=10,
            score=9,
            progress=12,
            status="Completed",
        )
        season.save()

        session = self.client.session
        session["metadata"] = {'id': 1668, 'api': 'tmdb', 'media_type': 'tv', 'number_of_seasons': 10, 'title': 'Friends',
                               'seasons':[{"episode_count":39, "season_number": 0},
                                          {"episode_count":24, "season_number": 1},
                                          {"episode_count":24, "season_number": 2},
                                          {"episode_count":25, "season_number": 3},
                                          {"episode_count":24, "season_number": 4},
                                          {"episode_count":24, "season_number": 5},
                                          {"episode_count":25, "season_number": 6},
                                          {"episode_count":24, "season_number": 7},
                                          {"episode_count":24, "season_number": 8},
                                          {"episode_count":24, "season_number": 9},
                                          {"episode_count":18, "season_number": 10}]}
        session.save()


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
                "status": "Completed",
                "score": 9.5,
                "season": 10,
                "progress": 18,
                "start": "2023-01-01",
                "end": "2023-01-02",
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
                "status": "Watching",
                "score": 9,
                "season": 10,
                "progress": 18,
                "start": "2023-01-01",
                "end": "2023-01-02",
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

    
    def test_edit_tmdb_dates(self):
        self.assertEqual(
            Media.objects.filter(
                media_id=1668, user=self.user, start_date="2023-01-01"
            ).exists(),
            True,
        )

        self.assertEqual(
            Media.objects.filter(
                media_id=1668, user=self.user, start_date="2023-01-02"
            ).exists(),
            False,
        )

        self.assertEqual(
            Media.objects.filter(
                media_id=1668, user=self.user, end_date="2023-01-02"
            ).exists(),
            True,
        )

        self.assertEqual(
            Media.objects.filter(
                media_id=1668, user=self.user, end_date="2023-01-03"
            ).exists(),
            False,
        )

        self.client.post(
            reverse("home"),
            {
                "status": "Watching",
                "score": 9,
                "season": 10,
                "progress": 18,
                "start": "2023-01-02",
                "end": "2023-01-03",
            },
        )
        self.assertEqual(
            Media.objects.filter(
                media_id=1668, user=self.user, start_date="2023-01-02"
            ).exists(),
            True,
        )
        self.assertEqual(
            Media.objects.filter(
                media_id=1668, user=self.user, end_date="2023-01-03"
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
                "status": "Watching",
                "score": 9.5,
                "season": "all",
                "start": "2023-01-01",
                "end": "2023-01-02",
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