from django.test import TestCase
from django.urls import reverse
from django.test import override_settings
from django.conf import settings

import shutil
import os

from app.models import User, Media, Season
from app.utils import details
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
            image="https://image.tmdb.org/t/p/w500/FkgA8CcmiLJGVCRYRQ2g2UfVtF.jpg",
            media_type="tv",
            score=10,
            progress=4,
            status="Watching",
            start_date="2023-01-01",
            end_date="2023-01-02",
            user=self.user,
            season_number=1,
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
            user=self.user,
            season_number=None,
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
            user=self.user,
            season_number=10,
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
            user=self.user,
            season_number=None,
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


class DetailsMedia(TestCase):

    def test_anime(self):
        response = details.anime_manga("anime", "1")
        self.assertEqual(response["title"], "Cowboy Bebop")
        self.assertEqual(response["image"], "https://api-cdn.myanimelist.net/images/anime/4/19644l.jpg")
        self.assertEqual(response["start_date"], "1998-04-03")
        self.assertEqual(response["status"], "Finished")
        self.assertEqual(response["num_episodes"], 26)

    def test_anime_unknown(self):
        # anime without picture, synopsis and duration
        response = details.anime_manga("anime", "23183")
        self.assertEqual(response["title"], "Itazura Post")
        self.assertEqual(response["image"], "none.svg")
        self.assertEqual(response["synopsis"], "No synopsis available.")
        self.assertEqual(response["runtime"], "Unknown")

    def test_anime_unknown_2(self):
        # anime without picture and genres
        response = details.anime_manga("anime", "28487")
        self.assertEqual(response["title"], "Ikite Iru")
        self.assertEqual(response["image"], "none.svg")
        self.assertEqual(response["genres"], [{"name": "Unknown"}])

    def test_manga(self):
        response = details.anime_manga("manga", "1")
        self.assertEqual(response["title"], "Monster")
        self.assertEqual(response["image"], "https://api-cdn.myanimelist.net/images/manga/3/258224l.jpg")
        self.assertEqual(response["start_date"], "1994-12-05")
        self.assertEqual(response["status"], "Finished")
        self.assertEqual(response["num_chapters"], 162)

    def test_tv(self):
        response = details.tv("1396")
        self.assertEqual(response["title"], "Breaking Bad")
        self.assertEqual(response["image"], "https://image.tmdb.org/t/p/w500/ggFHVNu6YYI5L9pCfOacjizRGt.jpg")
        self.assertEqual(response["start_date"], "2008-01-20")
        self.assertEqual(response["status"], "Ended")
        self.assertEqual(response["num_episodes"], 62)

    def test_tv_unknown(self):
        response = details.tv("24795")
        self.assertEqual(response["title"], "F.D.R.")
        self.assertEqual(response["image"], "none.svg")
        self.assertEqual(response["synopsis"], "No synopsis available.")
        self.assertEqual(response["genres"], [{"name": "Unknown"}])

    def test_movie(self):
        response = details.movie("10494")
        self.assertEqual(response["title"], "Perfect Blue")
        self.assertEqual(response["image"], "https://image.tmdb.org/t/p/w500/hwCTlm990H6NlrG8W7sk3pxdMtf.jpg")
        self.assertEqual(response["start_date"], "1997-07-25")
        self.assertEqual(response["status"], "Released")

    def test_movie_unknown(self):
        response = details.movie("402988")
        self.assertEqual(response["title"], "FDS")
        self.assertEqual(response["image"], "none.svg")
        self.assertEqual(response["start_date"], "Unknown")
        self.assertEqual(response["genres"], [{"name": "Unknown"}])
