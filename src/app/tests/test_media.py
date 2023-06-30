from django.test import TestCase
from django.urls import reverse
from django.test import override_settings
from django.conf import settings

import json
import shutil
import os
from datetime import date
from unittest.mock import patch

from app.models import User, TV, Season, Episode, Movie, Anime
from app.utils import metadata


mock_path = os.path.join(os.path.dirname(__file__), "mock_data")


class CreateMedia(TestCase):
    def setUp(self):
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)
        self.client.login(**self.credentials)
        os.makedirs("create_media", exist_ok=True)

    @override_settings(MEDIA_ROOT=("create_media"))
    def test_create_tv(self):
        self.client.post(
            # it doesn't matter what url we use
            reverse("medialist", kwargs={"media_type": "tv"}),
            {
                "media_id": 5895,
                "media_type": "tv",
                "score": 9,
                "notes": "Nice",
                "save": "",
            },
        )
        self.assertEqual(
            TV.objects.filter(media_id=5895, user=self.user).exists(), True
        )
        # check if tv poster image is downloaded
        self.assertEqual(
            os.path.exists(settings.MEDIA_ROOT + "/tv-FkgA8CcmiLJGVCRYRQ2g2UfVtF.jpg"),
            True,
        )

    @override_settings(MEDIA_ROOT=("create_media"))
    def test_create_season(self):
        self.client.post(
            # it doesn't matter what url we use
            reverse("medialist", kwargs={"media_type": "tv"}),
            {
                "media_id": 1668,
                "media_type": "season",
                "season_number": 1,
                "score": 9,
                "status": "Completed",
                "notes": "Nice",
                "save": "",
            },
        )
        self.assertEqual(
            Season.objects.filter(media_id=1668, user=self.user).exists(), True
        )
        # check if season poster image is downloaded
        self.assertEqual(
            os.path.exists(
                settings.MEDIA_ROOT + "/season-f496cm9enuEsZkSPzCwnTESEK5s.jpg"
            ),
            True,
        )

    def test_create_episodes(self):
        self.client.post(
            reverse(
                "season_details",
                kwargs={"media_id": 1668, "title": "friends", "season_number": 1},
            ),
            {
                "media_id": 1668,
                "season_number": 1,
                "episode_number": [1, 2],
            },
        )
        self.assertEqual(
            Episode.objects.filter(
                tv_season__media_id=1668, tv_season__user=self.user
            ).count(),
            2,
        )

    def tearDown(self):
        shutil.rmtree("create_media")


class EditMedia(TestCase):
    def setUp(self):
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        Movie.objects.create(
            media_id=10494,
            title="Perfect Blue",
            score=9,
            status="Completed",
            user=self.user,
            notes="Nice",
            end_date=date(2023, 6, 1)
        )

    def test_edit_movie_score(self):
        self.client.post(
            # it doesn't matter what url we use
            reverse("medialist", kwargs={"media_type": "movie"}),
            {
                "media_id": 10494,
                "media_type": "movie",
                "score": 10,
                "status": "Completed",
                "notes": "Nice",
                "save": "",
            },
        )
        self.assertEqual(Movie.objects.get(media_id=10494).score, 10)


class DeleteMedia(TestCase):
    def setUp(self):
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_delete_movie(self):
        Movie.objects.create(
            media_id=10494,
            title="Perfect Blue",
            score=9,
            status="Completed",
            user=self.user,
            notes="Nice",
            end_date=date(2023, 6, 1)
        )

        self.assertEqual(Movie.objects.filter(user=self.user).count(), 1)

        self.client.post(
            # it doesn't matter what url we use
            reverse("medialist", kwargs={"media_type": "movie"}),
            {
                "media_id": 10494,
                "media_type": "movie",
                "delete": "",
            },
        )

        self.assertEqual(Movie.objects.filter(user=self.user).count(), 0)

    def test_delete_season(self):
        season = Season.objects.create(
            media_id=1668,
            title="Friends",
            score=9,
            status="Completed",
            season_number=1,
            user=self.user,
            notes="Nice",
        )
        Episode.objects.create(
            tv_season=season, episode_number=1, watch_date=date(2023, 6, 1)
        )

        self.client.post(
            # it doesn't matter what url we use
            reverse("medialist", kwargs={"media_type": "tv"}),
            {
                "media_id": 1668,
                "media_type": "season",
                "season_number": 1,
                "delete": "",
            },
        )

        self.assertEqual(Season.objects.filter(user=self.user).count(), 0)
        self.assertEqual(Episode.objects.filter(tv_season__user=self.user).count(), 0)


class DetailsMedia(TestCase):
    def test_anime(self):
        response = metadata.anime_manga("anime", "1")
        self.assertEqual(response["title"], "Cowboy Bebop")
        self.assertEqual(response["start_date"], "1998-04-03")
        self.assertEqual(response["status"], "Finished")
        self.assertEqual(response["num_episodes"], 26)

    @patch("requests.get")
    def test_anime_unknown(self, mock_data):
        with open(mock_path + "/media_unknown_anime.json", "r") as file:
            anime_response = json.load(file)
        mock_data.return_value.json.return_value = anime_response

        # anime without picture, synopsis, duration and genres
        response = metadata.anime_manga("anime", "0")
        self.assertEqual(response["title"], "Unknown Example")
        self.assertEqual(response["image"], "none.svg")
        self.assertEqual(response["synopsis"], "No synopsis available.")
        self.assertEqual(response["runtime"], "Unknown")
        self.assertEqual(response["genres"], [{"name": "Unknown"}])

    def test_manga(self):
        response = metadata.anime_manga("manga", "1")
        self.assertEqual(response["title"], "Monster")
        self.assertEqual(response["start_date"], "1994-12-05")
        self.assertEqual(response["status"], "Finished")
        self.assertEqual(response["num_chapters"], 162)

    def test_tv(self):
        response = metadata.tv("1396")
        self.assertEqual(response["title"], "Breaking Bad")
        self.assertEqual(
            response["image"],
            "https://image.tmdb.org/t/p/w500/ggFHVNu6YYI5L9pCfOacjizRGt.jpg",
        )
        self.assertEqual(response["start_date"], "2008-01-20")
        self.assertEqual(response["status"], "Ended")
        self.assertEqual(response["num_episodes"], 62)

    def test_movie(self):
        response = metadata.movie("10494")
        self.assertEqual(response["title"], "Perfect Blue")
        self.assertEqual(
            response["image"],
            "https://image.tmdb.org/t/p/w500/hwCTlm990H6NlrG8W7sk3pxdMtf.jpg",
        )
        self.assertEqual(response["start_date"], "1997-07-25")
        self.assertEqual(response["status"], "Released")

    @patch("requests.get")
    def test_movie_unknown(self, mock_data):
        with open(mock_path + "/media_unknown_movie.json", "r") as file:
            movie_response = json.load(file)
        mock_data.return_value.json.return_value = movie_response

        response = metadata.movie("0")
        self.assertEqual(response["title"], "Unknown Movie")
        self.assertEqual(response["image"], "none.svg")
        self.assertEqual(response["start_date"], "Unknown")
        self.assertEqual(response["synopsis"], "No synopsis available.")
        self.assertEqual(response["runtime"], "Unknown")
        self.assertEqual(response["genres"], [{"name": "Unknown"}])


class ProgressEditSeason(TestCase):
    def setUp(self):
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        Season.objects.create(
            media_id=1668,
            title="Friends",
            score=9,
            status="Watching",
            season_number=1,
            user=self.user,
            notes="Nice",
        )

        Episode.objects.create(
            tv_season=Season.objects.get(media_id=1668),
            episode_number=1,
            watch_date=date(2023, 6, 1),
        )

    def test_progress_increase(self):
        self.client.post(
            reverse("progress_edit"),
            {
                "media_id": 1668,
                "media_type": "season",
                "operation": "increase",
                "season_number": 1,
            },
        )

        self.assertEqual(
            Episode.objects.filter(tv_season__media_id=1668).count(), 2
        )

        # episode with media_id 1668 and episode_number 2 should exist
        self.assertTrue(
            Episode.objects.filter(
                tv_season__media_id=1668, episode_number=2
            ).exists()
        )

    def test_progress_decrease(self):
        self.client.post(
            reverse("progress_edit"),
            {
                "media_id": 1668,
                "media_type": "season",
                "operation": "decrease",
                "season_number": 1,
            },
        )

        self.assertEqual(
            Episode.objects.filter(tv_season__media_id=1668).count(), 0
        )


class ProgressEditAnime(TestCase):
    def setUp(self):
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        Anime.objects.create(
            media_id=1,
            title="Cowboy Bebop",
            score=9,
            status="Watching",
            progress=2,
            start_date=date(2023, 6, 1),
            end_date=None,
            user=self.user,
            notes="",
        )

    def test_progress_increase(self):
        self.client.post(
            reverse("progress_edit"),
            {
                "media_id": 1,
                "media_type": "anime",
                "operation": "increase",
            },
        )

        self.assertEqual(Anime.objects.get(media_id=1).progress, 3)

    def test_progress_decrease(self):
        self.client.post(
            reverse("progress_edit"),
            {
                "media_id": 1,
                "media_type": "anime",
                "operation": "decrease",
            },
        )

        self.assertEqual(Anime.objects.get(media_id=1).progress, 1)