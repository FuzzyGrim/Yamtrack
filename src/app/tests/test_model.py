import datetime
from datetime import date
from pathlib import Path

from django.conf import settings
from django.test import TestCase
from users.models import User

from app.models import TV, Anime, Episode, Movie, Season

mock_path = Path(__file__).resolve().parent / "mock_data"


class MediaModel(TestCase):
    """Test the custom save of the Media model."""

    def setUp(self: "TVModel") -> None:
        """Create a user."""

        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)

    def test_movie_completed_no_end(self: "MediaModel") -> None:
        """When completed, if not specified end_date, it should be the current date."""

        Movie.objects.create(
            media_id=10494,
            title="Perfect Blue",
            status="Completed",
            user=self.user,
            notes="",
        )
        self.assertEqual(
            Movie.objects.get(media_id=10494, user=self.user).end_date,
            datetime.datetime.now(tz=settings.TZ).date(),
        )

    def test_movie_completed_end(self: "MediaModel") -> None:
        """When completed, if specified end_date, it should be the specified date."""

        Movie.objects.create(
            media_id=10494,
            title="Perfect Blue",
            status="Completed",
            user=self.user,
            notes="",
            end_date=date(2023, 6, 1),
        )
        self.assertEqual(
            Movie.objects.get(media_id=10494, user=self.user).end_date,
            date(2023, 6, 1),
        )

    def test_anime_completed_progress(self: "MediaModel") -> None:
        """When completed, the progress should be the total number of episodes."""

        Anime.objects.create(
            media_id=1,
            title="Cowboy Bebop",
            status="Completed",
            user=self.user,
            notes="",
        )
        self.assertEqual(Anime.objects.get(media_id=1, user=self.user).progress, 26)

    def test_progress_is_max(self: "MediaModel") -> None:
        """Test when progress is set to the maximum number of episodes.

        Status should be completed and end_date the current date if not specified.
        """

        Anime.objects.create(
            media_id=1,
            title="Cowboy Bebop",
            status="In progress",
            progress=26,
            user=self.user,
            notes="",
        )
        self.assertEqual(
            Anime.objects.get(media_id=1, user=self.user).status,
            "Completed",
        )
        self.assertEqual(
            Anime.objects.get(media_id=1, user=self.user).end_date,
            datetime.datetime.now(tz=settings.TZ).date(),
        )

    def test_progress_bigger_than_max(self: "MediaModel") -> None:
        """When progress is bigger than max, it should be set to max."""

        Anime.objects.create(
            media_id=1,
            title="Cowboy Bebop",
            status="In progress",
            progress=30,
            user=self.user,
            notes="",
        )
        self.assertEqual(Anime.objects.get(media_id=1, user=self.user).progress, 26)


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
        tv.save(update_fields=["status"])

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
        )

        season = Season.objects.create(
            media_id=1668,
            title="Friends",
            status="In progress",
            season_number=1,
            user=self.user,
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

        season = Season.objects.get(media_id=1668, season_number=1, user=self.user)
        self.assertEqual(season.progress, 2)

    def test_season_start_date(self: "SeasonModel") -> None:
        """Test the start_date property of the Season model."""

        season = Season.objects.get(media_id=1668, season_number=1, user=self.user)
        self.assertEqual(season.start_date, date(2023, 6, 1))

    def test_season_end_date(self: "SeasonModel") -> None:
        """Test the end_date property of the Season model."""

        season = Season.objects.get(media_id=1668, season_number=1, user=self.user)
        self.assertEqual(season.end_date, date(2023, 6, 2))

    def test_season_save(self: "SeasonModel") -> None:
        """Test the custom save method of the Season model."""

        season = Season.objects.get(media_id=1668, season_number=1, user=self.user)
        season.status = "Completed"
        season.save(update_fields=["status"])

        # check if all episodes are created
        self.assertEqual(season.episodes.count(), 24)


class EpisodeModel(TestCase):
    """Test the custom save of the Episode model."""

    def setUp(self: "EpisodeModel") -> None:
        """Create a user and a season."""

        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)

        related_tv = TV.objects.create(
            media_id=1668,
            title="Friends",
            status="In progress",
            user=self.user,
            notes="",
        )

        Season.objects.create(
            media_id=1668,
            title="Friends",
            status="In progress",
            season_number=1,
            user=self.user,
            notes="",
            related_tv=related_tv,
        )

    def test_episode_save(self: "EpisodeModel") -> None:
        """Test the custom save method of the Episode model."""

        season = Season.objects.get(media_id=1668, season_number=1, user=self.user)

        for i in range(1, 25):
            Episode.objects.create(
                related_season=season,
                episode_number=i,
                watch_date=date(2023, 6, i),
            )

        # if when all episodes are created, the season status should be "Completed"
        self.assertEqual(season.status, "Completed")
