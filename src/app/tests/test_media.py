import json
import os
import shutil
from datetime import date
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from users.models import User

from app.models import TV, Anime, Episode, Movie, Season
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
            reverse(
                "media_details",
                kwargs={"media_type": "tv", "media_id": 5895, "title": "FLCL"},
            ),
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

    @override_settings(MEDIA_ROOT=("create_media"))
    def test_create_season(self):
        self.client.post(
            reverse(
                "season_details",
                kwargs={"media_id": 1668, "title": "Friends", "season_number": 1},
            ),
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

    def test_create_episodes(self):
        self.client.post(
            reverse(
                "season_details",
                kwargs={"media_id": 1668, "title": "friends", "season_number": 1},
            ),
            {
                "media_id": 1668,
                "season_number": 1,
                "episode_number": 1,
            },
        )
        self.assertEqual(
            Episode.objects.filter(
                related_season__media_id=1668,
                related_season__user=self.user,
                episode_number=1,
            ).exists(),
            True,
        )

    def tearDown(self):
        shutil.rmtree("create_media")


class EditMedia(TestCase):
    def setUp(self):
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_edit_movie_score(self):
        Movie.objects.create(
            media_id=10494,
            title="Perfect Blue",
            score=9,
            status="Completed",
            user=self.user,
            notes="Nice",
            end_date=date(2023, 6, 1),
        )

        self.client.post(
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


class CleanFormMedia(TestCase):
    def setUp(self):
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_movie_complete(self):
        self.client.post(
            reverse(
                "media_details",
                kwargs={
                    "media_type": "movie",
                    "media_id": 10494,
                    "title": "Perfect Blue",
                },
            ),
            {
                "media_id": 10494,
                "media_type": "movie",
                "score": 10,
                "status": "Completed",
                "save": "",
            },
        )
        self.assertEqual(Movie.objects.get(media_id=10494).end_date, date.today())

    def test_anime_complete(self):
        """
        When media completed, end_date = today and progress = total episodes
        """
        self.client.post(
            reverse(
                "media_details",
                kwargs={"media_type": "anime", "media_id": 1, "title": "Cowboy Bebop"},
            ),
            {
                "media_id": 1,
                "media_type": "anime",
                "score": 10,
                "progress": 0,
                "status": "Completed",
                "save": "",
            },
        )
        self.assertEqual(Anime.objects.get(media_id=1).progress, 26)
        self.assertEqual(Anime.objects.get(media_id=1).end_date, date.today())

    def test_season_complete(self):
        """
        When season completed, create remaining episodes
        """
        self.client.post(
            reverse(
                "season_details",
                kwargs={"media_id": 1668, "title": "Friends", "season_number": 1},
            ),
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
            Episode.objects.filter(related_season__media_id=1668).count(),
            24,
        )

    def test_progress_set_max(self):
        """
        When progress is set to max and status not explicitly edited, status should be set to completed
        """
        Anime.objects.create(
            media_id=1,
            title="Cowboy Bebop",
            status="Watching",
            user=self.user,
            progress=2,
            start_date=date(2021, 6, 1),
        )
        self.client.post(
            reverse(
                "media_details",
                kwargs={"media_type": "anime", "media_id": 1, "title": "Cowboy Bebop"},
            ),
            {
                "media_id": 1,
                "media_type": "anime",
                "progress": 26,
                "status": "Watching",
                "save": "",
            },
        )
        self.assertEqual(Anime.objects.get(media_id=1).status, "Completed")

    def test_progress_bigger_than_max(self):
        """
        When progress is bigger than max, progress should be set to max
        """
        self.client.post(
            reverse(
                "media_details",
                kwargs={"media_type": "anime", "media_id": 1, "title": "Cowboy Bebop"},
            ),
            {
                "media_id": 1,
                "media_type": "anime",
                "progress": 27,
                "status": "Watching",
                "save": "",
            },
        )
        self.assertEqual(Anime.objects.get(media_id=1).progress, 26)


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
            end_date=date(2023, 6, 1),
        )

        self.assertEqual(Movie.objects.filter(user=self.user).count(), 1)

        self.client.post(
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
            related_season=season, episode_number=1, watch_date=date(2023, 6, 1)
        )

        self.client.post(
            reverse("medialist", kwargs={"media_type": "tv"}),
            {
                "media_id": 1668,
                "media_type": "season",
                "season_number": 1,
                "delete": "",
            },
        )

        self.assertEqual(Season.objects.filter(user=self.user).count(), 0)
        self.assertEqual(
            Episode.objects.filter(related_season__user=self.user).count(), 0
        )

    def test_unwatch_episode(self):
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
            related_season=season, episode_number=1, watch_date=date(2023, 6, 1)
        )

        self.client.post(
            reverse(
                "season_details",
                kwargs={"media_id": 1668, "title": "friends", "season_number": 1},
            ),
            {
                "media_id": 1668,
                "season_number": 1,
                "episode_number": 1,
                "unwatch": "",
            },
        )

        self.assertEqual(
            Episode.objects.filter(related_season__user=self.user).count(), 0,
        )


class DetailsMedia(TestCase):
    def test_anime(self):
        response = metadata.anime_manga("anime", "1")
        self.assertEqual(response["title"], "Cowboy Bebop")
        self.assertEqual(response["start_date"], "1998-04-03")
        self.assertEqual(response["status"], "Finished")
        self.assertEqual(response["num_episodes"], 26)

    @patch("requests.get")
    def test_anime_unknown(self, mock_data):
        with open(mock_path + "/metadata_anime_unknown.json", "r") as file:
            anime_response = json.load(file)
        mock_data.return_value.json.return_value = anime_response

        # anime without picture, synopsis, duration and genres
        response = metadata.anime_manga("anime", "0")
        self.assertEqual(response["title"], "Unknown Example")
        self.assertEqual(response["image"], settings.IMG_NONE)
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
        self.assertEqual(response["start_date"], "2008-01-20")
        self.assertEqual(response["status"], "Ended")
        self.assertEqual(response["num_episodes"], 62)

    def test_movie(self):
        response = metadata.movie("10494")
        self.assertEqual(response["title"], "Perfect Blue")
        self.assertEqual(response["start_date"], "1998-02-28")
        self.assertEqual(response["status"], "Released")

    @patch("requests.get")
    def test_movie_unknown(self, mock_data):
        with open(mock_path + "/metadata_movie_unknown.json", "r") as file:
            movie_response = json.load(file)
        mock_data.return_value.json.return_value = movie_response

        response = metadata.movie("0")
        self.assertEqual(response["title"], "Unknown Movie")
        self.assertEqual(response["image"], settings.IMG_NONE)
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
            related_season=Season.objects.get(media_id=1668),
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
            Episode.objects.filter(related_season__media_id=1668).count(), 2
        )

        # episode with media_id 1668 and episode_number 2 should exist
        self.assertTrue(
            Episode.objects.filter(
                related_season__media_id=1668, episode_number=2
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
            Episode.objects.filter(related_season__media_id=1668).count(), 0
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
