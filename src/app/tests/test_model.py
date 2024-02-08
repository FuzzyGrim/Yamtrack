from datetime import date
from pathlib import Path

from django.test import TestCase
from users.models import User

from app.models import TV, Episode, Season

mock_path = Path(__file__).resolve().parent / "mock_data"


class TVModel(TestCase):
    """Test the @properties and custom save of the TV model."""

    def setUp(self: "TVModel") -> None:
        """Create a user and a season with episodes."""

        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)

        tv = TV.objects.create(
            media_id=1668,
            title="Friends",
            status="In progress",
            user=self.user,
            notes="",
        )

        # create first season
        season = Season.objects.create(
            media_id=1668,
            title="Friends",
            status="In progress",
            season_number=1,
            user=self.user,
            notes="",
            related_tv=tv,
        )
        Episode.objects.create(
            related_season=season,
            episode_number=1,
            watch_date=date(2023, 6, 1),
        )
        Episode.objects.create(
            related_season=season,
            episode_number=2,
            watch_date=date(2023, 6, 2),
        )

        # create second season
        season2 = Season.objects.create(
            media_id=1668,
            title="Friends",
            status="In progress",
            season_number=2,
            user=self.user,
            notes="",
            related_tv=tv,
        )
        Episode.objects.create(
            related_season=season2,
            episode_number=1,
            watch_date=date(2023, 6, 4),
        )
        Episode.objects.create(
            related_season=season2,
            episode_number=2,
            watch_date=date(2023, 6, 5),
        )

    def test_tv_progress(self: "TVModel") -> None:
        """Test the progress property of the Season model."""

        tv = TV.objects.get(media_id=1668, user=self.user)
        self.assertEqual(tv.progress, 4)

    def test_tv_start_date(self: "TVModel") -> None:
        """Test the start_date property of the Season model."""

        tv = TV.objects.get(media_id=1668, user=self.user)
        self.assertEqual(tv.start_date, date(2023, 6, 1))

    def test_tv_end_date(self: "TVModel") -> None:
        """Test the end_date property of the Season model."""

        tv = TV.objects.get(media_id=1668, user=self.user)
        self.assertEqual(tv.end_date, date(2023, 6, 5))

    def test_tv_save(self: "TVModel") -> None:
        """Test the custom save method of the TV model."""

        tv = TV.objects.get(media_id=1668, user=self.user)
        tv.status = "Completed"
        tv.save()

        # check if all seasons are created with the status "Completed"
        self.assertEqual(tv.seasons.filter(status="Completed").count(), 10)


class SeasonModel(TestCase):
    """Test the @properties and custom save of the Season model."""

    def setUp(self: "SeasonModel") -> None:
        """Create a user and a season with episodes."""

        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)

        related_tv = TV.objects.create(
            media_id=1668,
            title="Friends",
            status="In progress",
            user=self.user,
            notes="",
        )

        season = Season.objects.create(
            media_id=1668,
            title="Friends",
            status="In progress",
            season_number=1,
            user=self.user,
            notes="",
            related_tv=related_tv,
        )
        Episode.objects.create(
            related_season=season,
            episode_number=1,
            watch_date=date(2023, 6, 1),
        )
        Episode.objects.create(
            related_season=season,
            episode_number=2,
            watch_date=date(2023, 6, 2),
        )

    def test_season_progress(self: "SeasonModel") -> None:
        """Test the progress property of the Season model."""

        season = Season.objects.get(media_id=1668, user=self.user)
        self.assertEqual(season.progress, 2)

    def test_season_start_date(self: "SeasonModel") -> None:
        """Test the start_date property of the Season model."""

        season = Season.objects.get(media_id=1668, user=self.user)
        self.assertEqual(season.start_date, date(2023, 6, 1))

    def test_season_end_date(self: "SeasonModel") -> None:
        """Test the end_date property of the Season model."""

        season = Season.objects.get(media_id=1668, user=self.user)
        self.assertEqual(season.end_date, date(2023, 6, 2))
