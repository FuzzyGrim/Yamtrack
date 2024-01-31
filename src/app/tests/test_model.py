from datetime import date

from django.test import TestCase
from users.models import User

from app.models import Episode, Movie, Season


class SeasonProperties(TestCase):
    def setUp(self):
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)

        season = Season.objects.create(
            media_id=1,
            title="Season Example",
            score=9,
            status="Completed",
            season_number=1,
            user=self.user,
            notes="Nice",
        )
        Episode.objects.create(
            related_season=season, episode_number=1, watch_date=date(2023, 6, 1)
        )
        Episode.objects.create(
            related_season=season, episode_number=2, watch_date=date(2023, 6, 2)
        )
        Episode.objects.create(
            related_season=season, episode_number=3, watch_date=date(2023, 6, 3)
        )

    def test_season_progress(self):
        season = Season.objects.get(media_id=1, user=self.user)
        self.assertEqual(season.progress, 3)

    def test_season_start_date(self):
        season = Season.objects.get(media_id=1, user=self.user)
        self.assertEqual(season.start_date, date(2023, 6, 1))

    def test_season_end_date(self):
        season = Season.objects.get(media_id=1, user=self.user)
        self.assertEqual(season.end_date, date(2023, 6, 3))


class MovieProperties(TestCase):
    def setUp(self):
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)

        Movie.objects.create(
            media_id=1,
            title="Movie Example",
            score=9,
            status="Completed",
            user=self.user,
            notes="Nice",
            end_date=date(2023, 6, 1),
        )

    def test_movie_progress(self):
        # when a movie is completed, progress should be 1
        movie = Movie.objects.get(media_id=1, user=self.user)
        self.assertEqual(movie.progress, 1)

        movie.status = "In progress"
        movie.save()
        # when a movie is being watched, progress should be 0
        movie = Movie.objects.get(media_id=1, user=self.user)
        self.assertEqual(movie.progress, 0)
