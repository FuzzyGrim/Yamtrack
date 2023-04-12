from django.test import TestCase
from django.urls import reverse
from django.test import override_settings
from django.conf import settings

import shutil
import os

from app.models import User, Media, Season
from app.utils.database import add_media, edit_media


class CreateMedia(TestCase):
    def setUp(self):
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)
        self.client.login(**self.credentials)
        os.makedirs("create_tmdb")

    @override_settings(MEDIA_ROOT=("create_tmdb"))
    def test_create_tmdb(self):
        self.assertEqual(
            Media.objects.filter(media_id=5895, user=self.user).exists(), False
        )

        add_media(
            media_id=5895,
            title="FLCL",
            image="/FkgA8CcmiLJGVCRYRQ2g2UfVtF.jpg",
            media_type="tv",
            score=10,
            progress=4,
            status="Watching",
            start_date="2023-01-01",
            end_date="2023-01-02",
            api="tmdb",
            user=self.user,
            season_selected=1,
            seasons=[
                {"episode_count": 6, "season_number": 1},
                {"episode_count": 6, "season_number": 2},
                {"episode_count": 6, "season_number": 3},
                {"episode_count": 0, "season_number": 4},
            ],
        )

        self.assertEqual(
            Media.objects.filter(media_id=5895, user=self.user).exists(), True
        )
        # check if poster image is downloaded
        self.assertEqual(
            os.path.exists(settings.MEDIA_ROOT + "/tv-FkgA8CcmiLJGVCRYRQ2g2UfVtF.jpg"),
            True,
        )

        # check if FLCL and season 1 in database
        media = Media.objects.get(media_id=5895, user=self.user)
        self.assertEqual(str(media), "FLCL")
        season = Season.objects.get(parent=media, number=1)
        self.assertEqual(str(season), "FLCL - Season 1")

        # check if it is in medialist
        response = self.client.get(reverse("medialist", kwargs={"media_type": "tv"}))
        self.assertContains(response, "FLCL")

        # check if FLCL in home because it is progress
        response = self.client.get(reverse("home"))
        self.assertContains(response, "FLCL")

    def tearDownClass():
        shutil.rmtree("create_tmdb")


class EditMedia(TestCase):
    @override_settings(MEDIA_ROOT=("edit"))
    def setUp(self):
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)
        self.client.login(**self.credentials)
        os.makedirs("edit", exist_ok=True)

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
            parent=media,
            number=10,
            score=9,
            progress=12,
            status="Completed",
        )
        season.save()

    def test_edit_tmdb_score(self):
        self.assertEqual(
            Media.objects.filter(media_id=1668, user=self.user, score=9).exists(), True
        )

        edit_media(
            media_id=1668,
            title="Friends",
            image="/f496cm9enuEsZkSPzCwnTESEK5s.jpg",
            media_type="tv",
            score=9.5,
            progress=18,
            status="Completed",
            start_date="2023-01-01",
            end_date="2023-01-02",
            api="tmdb",
            user=self.user,
            season_selected=None,
            seasons=None
        )
        self.assertEqual(
            Media.objects.filter(media_id=1668, user=self.user, score=9).exists(), False
        )

        self.assertEqual(
            Media.objects.filter(media_id=1668, user=self.user, score=9.5).exists(),
            True,
        )

        # check if it is in medialist
        response = self.client.get(reverse("medialist", kwargs={"media_type": "tv"}))
        self.assertContains(response, "9.5")

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

        edit_media(
            media_id=1668,
            title="Friends",
            image="/f496cm9enuEsZkSPzCwnTESEK5s.jpg",
            media_type="tv",
            score=9,
            progress=18,
            status="Watching",
            start_date="2023-01-01",
            end_date="2023-01-02",
            api="tmdb",
            user=self.user,
            season_selected=10,
            seasons=[
                {"episode_count": 39, "season_number": 0},
                {"episode_count": 24, "season_number": 1},
                {"episode_count": 24, "season_number": 2},
                {"episode_count": 25, "season_number": 3},
                {"episode_count": 24, "season_number": 4},
                {"episode_count": 24, "season_number": 5},
                {"episode_count": 25, "season_number": 6},
                {"episode_count": 24, "season_number": 7},
                {"episode_count": 24, "season_number": 8},
                {"episode_count": 24, "season_number": 9},
                {"episode_count": 18, "season_number": 10},
            ],
        )

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

        response = self.client.get(reverse("medialist", kwargs={"media_type": "tv"}))
        self.assertContains(response, "Watching")

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

        edit_media(
            media_id=1668,
            title="Friends",
            image="/f496cm9enuEsZkSPzCwnTESEK5s.jpg",
            media_type="tv",
            score=9,
            progress=18,
            status="Completed",
            start_date="2023-01-02",
            end_date="2023-01-03",
            api="tmdb",
            user=self.user,
            season_selected=10,
            seasons=[
                {"episode_count": 39, "season_number": 0},
                {"episode_count": 24, "season_number": 1},
                {"episode_count": 24, "season_number": 2},
                {"episode_count": 25, "season_number": 3},
                {"episode_count": 24, "season_number": 4},
                {"episode_count": 24, "season_number": 5},
                {"episode_count": 25, "season_number": 6},
                {"episode_count": 24, "season_number": 7},
                {"episode_count": 24, "season_number": 8},
                {"episode_count": 24, "season_number": 9},
                {"episode_count": 18, "season_number": 10},
            ],
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

    def tearDownClass():
        try:
            shutil.rmtree("edit")
        except OSError:
            pass
