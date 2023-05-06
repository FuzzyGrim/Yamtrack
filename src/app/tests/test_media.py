from django.test import TestCase
from django.urls import reverse
from django.test import override_settings
from django.conf import settings

import shutil
import os
import datetime

from app import database
from app.models import User, Media, Season
from app.utils import metadata


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

        database.media.add_media(
            media_id=5895,
            title="FLCL",
            image="https://image.tmdb.org/t/p/w500/FkgA8CcmiLJGVCRYRQ2g2UfVtF.jpg",
            media_type="tv",
            score=10,
            progress=4,
            status="Watching",
            start_date=datetime.datetime(2023, 1, 1),
            end_date=datetime.datetime(2023, 1, 2),
            notes="Nice",
            user=self.user,
        )

        self.assertEqual(
            Media.objects.filter(media_id=5895, user=self.user).exists(), True
        )
        # check if poster image is downloaded
        self.assertEqual(
            os.path.exists(settings.MEDIA_ROOT + "/tv-FkgA8CcmiLJGVCRYRQ2g2UfVtF.jpg"),
            True,
        )

        # check if FLCL in database
        media = Media.objects.get(media_id=5895, user=self.user)
        self.assertEqual(str(media), "FLCL")

        # check if it's in medialist
        response = self.client.get(reverse("medialist", kwargs={"media_type": "tv"}))
        self.assertContains(response, "FLCL")

        # check if FLCL in home because it's in progress
        response = self.client.get(reverse("home"))
        self.assertContains(response, "FLCL")

    def tearDown(self):
        shutil.rmtree("create_tmdb")


class EditMedia(TestCase):
    @override_settings(MEDIA_ROOT=("edit_media"))
    def setUp(self):
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)
        self.client.login(**self.credentials)
        os.makedirs("edit_media", exist_ok=True)

        media = Media(
            media_id=1668,
            title="Friends",
            image="/f496cm9enuEsZkSPzCwnTESEK5s.jpg",
            media_type="tv",
            score=9,
            progress=18,
            status="Completed",
            start_date=datetime.datetime(2023, 1, 1),
            end_date=datetime.datetime(2023, 1, 2),
            notes="Nice",
            user=self.user,
        )
        media.save()

    def test_edit_tmdb_score(self):
        self.assertEqual(
            Media.objects.filter(media_id=1668, user=self.user, score=9).exists(), True
        )

        database.media.edit_media(
            media_id=1668,
            title="Friends",
            image="/f496cm9enuEsZkSPzCwnTESEK5s.jpg",
            media_type="tv",
            score=9.5,
            progress=18,
            status="Completed",
            start_date=datetime.datetime(2023, 1, 1),
            end_date=datetime.datetime(2023, 1, 2),
            notes="Nice",
            user=self.user,
        )
        self.assertEqual(
            Media.objects.filter(media_id=1668, user=self.user, score=9).exists(), False
        )

        self.assertEqual(
            Media.objects.filter(media_id=1668, user=self.user, score=9.5).exists(),
            True,
        )

        # check if it's in medialist
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

        database.media.edit_media(
            media_id=1668,
            title="Friends",
            image="/f496cm9enuEsZkSPzCwnTESEK5s.jpg",
            media_type="tv",
            score=9,
            progress=18,
            status="Watching",
            start_date=datetime.datetime(2023, 1, 1),
            end_date=datetime.datetime(2023, 1, 2),
            notes="Nice",
            user=self.user,
        )

        self.assertEqual(
            Media.objects.filter(
                media_id=1668, user=self.user, status="Watching"
            ).exists(),
            True,
        )

    def test_edit_tmdb_dates(self):
        database.media.edit_media(
            media_id=1668,
            title="Friends",
            image="/f496cm9enuEsZkSPzCwnTESEK5s.jpg",
            media_type="tv",
            score=9,
            progress=18,
            status="Completed",
            start_date=datetime.datetime(2023, 1, 2),
            end_date=datetime.datetime(2023, 1, 3),
            notes="Nice",
            user=self.user,
        )

        self.assertEqual(
            Media.objects.filter(
                media_id=1668, user=self.user, start_date=datetime.datetime(2023, 1, 2)
            ).exists(),
            True,
        )
        self.assertEqual(
            Media.objects.filter(
                media_id=1668, user=self.user, end_date=datetime.datetime(2023, 1, 3)
            ).exists(),
            True,
        )

    def test_edit_tmdb_notes(self):
        database.media.edit_media(
            media_id=1668,
            title="Friends",
            image="/f496cm9enuEsZkSPzCwnTESEK5s.jpg",
            media_type="tv",
            score=9,
            progress=18,
            status="Completed",
            start_date=datetime.datetime(2023, 1, 1),
            end_date=datetime.datetime(2023, 1, 2),
            notes="test notes",
            user=self.user,
        )

        self.assertEqual(
            Media.objects.filter(
                media_id=1668, user=self.user, notes="test notes"
            ).exists(),
            True,
        )

    def tearDown(self):
        try:
            shutil.rmtree("edit_media")
        except OSError:
            pass


class EditSeasons(TestCase):
    @override_settings(MEDIA_ROOT=("edit_season"))
    def setUp(self):
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)
        self.client.login(**self.credentials)
        os.makedirs("edit_season", exist_ok=True)

        media = Media(
            media_id=1668,
            title="Friends",
            image="/f496cm9enuEsZkSPzCwnTESEK5s.jpg",
            media_type="tv",
            score=10,
            progress=18,
            status="Completed",
            start_date=datetime.datetime(2023, 1, 1),
            end_date=datetime.datetime(2023, 1, 2),
            notes="Nice",
            user=self.user,
        )
        media.save()

        season_1 = Season(
            parent=media,
            title="Friends",
            number=1,
            score=10,
            progress=24,
            status="Completed",
            start_date=datetime.datetime(2023, 1, 1),
            end_date=datetime.datetime(2023, 1, 2),
        )
        season_1.save()

        season_10 = Season(
            parent=media,
            title="Friends",
            number=10,
            score=10,
            progress=18,
            status="Completed",
            start_date=datetime.datetime(2023, 1, 1),
            end_date=datetime.datetime(2023, 1, 2),
        )
        season_10.save()

        self.seasons = (
            [
                {
                    "episode_count": 39,
                    "season_number": 0,
                    "poster_path": "/xaEj0Vw0LOmp7kBeX2vmYPb5sTg.jpg",
                },
                {
                    "episode_count": 24,
                    "season_number": 1,
                    "poster_path": "/odCW88Cq5hAF0ZFVOkeJmeQv1nV.jpg",
                },
                {
                    "episode_count": 24,
                    "season_number": 2,
                    "poster_path": "/kC9VHoMh1KkoAYfsY3QlHpZRxDy.jpg",
                },
                {
                    "episode_count": 25,
                    "season_number": 3,
                    "poster_path": "/n9u4pslqb6tpiLc8soldL5IbAyG.jpg",
                },
                {
                    "episode_count": 24,
                    "season_number": 4,
                    "poster_path": "/3WdH3FNMXgp3Qlx21T7kwKS8Mtc.jpg",
                },
                {
                    "episode_count": 24,
                    "season_number": 5,
                    "poster_path": "/aEwLXWbo6gV1TNIv9veu4rRwsPZ.jpg",
                },
                {
                    "episode_count": 25,
                    "season_number": 6,
                    "poster_path": "/7EU6bV6d8j1Xbc1F8QoNkOZrpsi.jpg",
                },
                {
                    "episode_count": 24,
                    "season_number": 7,
                    "poster_path": "/yvUZVChjOnqCjB9rjdEqEmpDdnQ.jpg",
                },
                {
                    "episode_count": 24,
                    "season_number": 8,
                    "poster_path": "/eh6PPkrzkXsEksRJDcdtx9lZsqX.jpg",
                },
                {
                    "episode_count": 24,
                    "season_number": 9,
                    "poster_path": "/1IvIdN4I5jJ0bwC3BkmDCy4pQ9j.jpg",
                },
                {
                    "episode_count": 18,
                    "season_number": 10,
                    "poster_path": "/67ETB6XIqYc5vZkyAjN8XINOX5i.jpg",
                },
            ],
        )

    # Editing season status, changes media status
    def test_edit_season_status(self):
        database.season.edit_season(
            media_id=1668,
            media_type="tv",
            score=10,
            progress=18,
            status="Paused",
            start_date=datetime.datetime(2023, 1, 1),
            end_date=datetime.datetime(2023, 1, 2),
            notes="Nice",
            user=self.user,
            season_number=10,
        )
        self.assertEqual(
            Season.objects.filter(
                parent__media_id=1668,
                parent__user=self.user,
                number=10,
                status="Paused",
            ).exists(),
            True,
        )
        self.assertEqual(
            Media.objects.filter(
                media_id=1668, user=self.user, status="Paused"
            ).exists(),
            True,
        )

    # media gets average score of all seasons
    def test_edit_season_score(self):
        database.season.edit_season(
            media_id=1668,
            media_type="tv",
            score=0,
            progress=24,
            status="Completed",
            start_date=datetime.datetime(2023, 1, 1),
            end_date=datetime.datetime(2023, 1, 2),
            notes="Nice",
            user=self.user,
            season_number=1,
        )
        self.assertEqual(
            Season.objects.filter(
                parent__media_id=1668, parent__user=self.user, number=1, score=0
            ).exists(),
            True,
        )
        self.assertEqual(
            Media.objects.filter(media_id=1668, user=self.user, score=5).exists(),
            True,
        )

    # media gets earliest start date of all seasons
    def test_edit_season_start_date(self):
        database.season.edit_season(
            media_id=1668,
            media_type="tv",
            score=10,
            progress=24,
            status="Completed",
            start_date=datetime.datetime(2022, 1, 1),
            end_date=datetime.datetime(2023, 1, 2),
            notes="Nice",
            user=self.user,
            season_number=1,
        )
        self.assertEqual(
            Season.objects.filter(
                parent__media_id=1668,
                parent__user=self.user,
                number=1,
                start_date=datetime.datetime(2022, 1, 1),
            ).exists(),
            True,
        )
        self.assertEqual(
            Media.objects.filter(
                media_id=1668, user=self.user, start_date=datetime.datetime(2022, 1, 1)
            ).exists(),
            True,
        )

    # edits start_date, but media start_date is unchanged,
    # because there is a season with an earlier start_date
    def test_edit_start_date_unchanged(self):
        database.season.edit_season(
            media_id=1668,
            media_type="tv",
            score=10,
            progress=24,
            status="Completed",
            start_date=datetime.datetime(2023, 1, 2),
            end_date=datetime.datetime(2023, 1, 2),
            notes="Nice",
            user=self.user,
            season_number=10,
        )
        self.assertEqual(
            Season.objects.filter(
                parent__media_id=1668,
                parent__user=self.user,
                number=10,
                start_date=datetime.datetime(2023, 1, 2),
            ).exists(),
            True,
        )
        self.assertEqual(
            Media.objects.filter(
                media_id=1668, user=self.user, start_date=datetime.datetime(2023, 1, 1)
            ).exists(),
            True,
        )

    # media gets latest end date of all seasons
    def test_edit_season_end_date(self):
        database.season.edit_season(
            media_id=1668,
            media_type="tv",
            score=10,
            progress=24,
            status="Completed",
            start_date=datetime.datetime(2023, 1, 1),
            end_date=datetime.datetime(2024, 1, 2),
            notes="Nice",
            user=self.user,
            season_number=10,
        )
        self.assertEqual(
            Season.objects.filter(
                parent__media_id=1668,
                parent__user=self.user,
                number=10,
                end_date=datetime.datetime(2024, 1, 2),
            ).exists(),
            True,
        )
        self.assertEqual(
            Media.objects.filter(
                media_id=1668, user=self.user, end_date=datetime.datetime(2024, 1, 2)
            ).exists(),
            True,
        )

    # edits end_date, but media end_date is unchanged,
    # because there is a season with a later end_date
    def test_edit_end_date_unchanged(self):
        database.season.edit_season(
            media_id=1668,
            media_type="tv",
            score=10,
            progress=24,
            status="Completed",
            start_date=datetime.datetime(2022, 1, 1),
            end_date=datetime.datetime(2022, 1, 2),
            notes="Nice",
            user=self.user,
            season_number=1,
        )
        self.assertEqual(
            Season.objects.filter(
                parent__media_id=1668,
                parent__user=self.user,
                number=1,
                end_date=datetime.datetime(2022, 1, 2),
            ).exists(),
            True,
        )
        self.assertEqual(
            Media.objects.filter(
                media_id=1668, user=self.user, end_date=datetime.datetime(2023, 1, 2)
            ).exists(),
            True,
        )

    def tearDown(self):
        try:
            shutil.rmtree("edit_season")
        except OSError:
            pass


class DetailsMedia(TestCase):
    def test_anime(self):
        response = metadata.anime_manga("anime", "1")
        self.assertEqual(response["title"], "Cowboy Bebop")
        self.assertEqual(
            response["image"],
            "https://api-cdn.myanimelist.net/images/anime/4/19644l.jpg",
        )
        self.assertEqual(response["start_date"], "1998-04-03")
        self.assertEqual(response["status"], "Finished")
        self.assertEqual(response["num_episodes"], 26)

    def test_anime_unknown(self):
        # anime without picture, synopsis and duration
        response = metadata.anime_manga("anime", "23183")
        self.assertEqual(response["title"], "Itazura Post")
        self.assertEqual(response["image"], "none.svg")
        self.assertEqual(response["synopsis"], "No synopsis available.")
        self.assertEqual(response["runtime"], "Unknown")

    def test_anime_unknown_2(self):
        # anime without picture and genres
        response = metadata.anime_manga("anime", "28487")
        self.assertEqual(response["title"], "Ikite Iru")
        self.assertEqual(response["image"], "none.svg")
        self.assertEqual(response["genres"], [{"name": "Unknown"}])

    def test_manga(self):
        response = metadata.anime_manga("manga", "1")
        self.assertEqual(response["title"], "Monster")
        self.assertEqual(
            response["image"],
            "https://api-cdn.myanimelist.net/images/manga/3/258224l.jpg",
        )
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

    def test_tv_unknown(self):
        response = metadata.tv("24795")
        self.assertEqual(response["title"], "F.D.R.")
        self.assertEqual(response["image"], "none.svg")
        self.assertEqual(response["synopsis"], "No synopsis available.")
        self.assertEqual(response["genres"], [{"name": "Unknown"}])

    def test_movie(self):
        response = metadata.movie("10494")
        self.assertEqual(response["title"], "Perfect Blue")
        self.assertEqual(
            response["image"],
            "https://image.tmdb.org/t/p/w500/hwCTlm990H6NlrG8W7sk3pxdMtf.jpg",
        )
        self.assertEqual(response["start_date"], "1997-07-25")
        self.assertEqual(response["status"], "Released")

    def test_movie_unknown(self):
        response = metadata.movie("865447")
        self.assertEqual(response["title"], "Production no 1 (Rain On Films Pvt. Ltd)")
        self.assertEqual(response["image"], "none.svg")
        self.assertEqual(response["start_date"], "Unknown")
        self.assertEqual(response["synopsis"], "No synopsis available.")
        self.assertEqual(response["runtime"], "Unknown")
        self.assertEqual(response["genres"], [{"name": "Unknown"}])
