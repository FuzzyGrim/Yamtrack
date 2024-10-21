import datetime
from datetime import date
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import TV, Anime, Episode, Item, Season

mock_path = Path(__file__).resolve().parent / "mock_data"


class ItemModel(TestCase):
    """Test case for the Item model."""

    def setUp(self):
        """Set up test data for Item model."""
        self.item = Item.objects.create(
            media_id=1,
            source="tmdb",
            media_type="movie",
            title="Test Movie",
            image="http://example.com/image.jpg",
        )

    def test_item_creation(self):
        """Test the creation of an Item instance."""
        self.assertEqual(self.item.media_id, 1)
        self.assertEqual(self.item.media_type, "movie")
        self.assertEqual(self.item.title, "Test Movie")
        self.assertEqual(self.item.image, "http://example.com/image.jpg")

    def test_item_str_representation(self):
        """Test the string representation of an Item."""
        self.assertEqual(str(self.item), "Test Movie")

    def test_item_with_season_and_episode(self):
        """Test the string representation of an Item with season and episode."""
        item = Item.objects.create(
            media_id=2,
            source="tmdb",
            media_type="episode",
            title="Test Show",
            image="http://example.com/image2.jpg",
            season_number=1,
            episode_number=2,
        )
        self.assertEqual(str(item), "Test Show S1E2")


class MediaModel(TestCase):
    """Test the custom save of the Media model."""

    def setUp(self):
        """Create a user."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        item_anime = Item.objects.create(
            media_id=1,
            source="mal",
            media_type="anime",
            title="Cowboy Bebop",
            image="http://example.com/image.jpg",
        )

        self.anime = Anime.objects.create(
            item=item_anime,
            user=self.user,
            status="Planned",
        )

    def test_completed_no_end(self):
        """When completed, if not specified end_date, it should be the current date."""
        self.anime.status = "Completed"
        self.anime.save()

        self.assertEqual(
            Anime.objects.get(item__media_id=1, user=self.user).end_date,
            datetime.datetime.now(tz=settings.TZ).date(),
        )

    def test_completed_end(self):
        """When completed, if specified end_date, it should be the specified date."""
        self.anime.status = "Completed"
        self.anime.end_date = "2023-06-01"
        self.anime.save()
        self.assertEqual(
            Anime.objects.get(item__media_id=1, user=self.user).end_date,
            date(2023, 6, 1),
        )

    def test_completed_progress(self):
        """When completed, the progress should be the total number of episodes."""
        self.anime.status = "Completed"
        self.anime.save()
        self.assertEqual(
            Anime.objects.get(item__media_id=1, user=self.user).progress,
            26,
        )

    def test_completed_from_repeating(self):
        """When completed from repeating, repeats should be incremented."""
        self.anime.status = "Repeating"
        self.anime.save()

        self.anime.status = "Completed"
        self.anime.save()

        self.assertEqual(Anime.objects.get(item__media_id=1, user=self.user).repeats, 1)

    def test_progress_is_max(self):
        """When progress is maximum number of episodes.

        Status should be completed and end_date the current date if not specified.
        """
        self.anime.status = "In progress"
        self.anime.progress = 26
        self.anime.save()

        self.assertEqual(
            Anime.objects.get(item__media_id=1, user=self.user).status,
            "Completed",
        )
        self.assertEqual(
            Anime.objects.get(item__media_id=1, user=self.user).end_date,
            datetime.datetime.now(tz=settings.TZ).date(),
        )

    def test_progress_is_max_from_repeating(self):
        """When progress is maximum number of episodes and status is repeating.

        Repeat should be incremented.
        """
        self.anime.status = "Repeating"
        self.anime.save()
        self.anime.progress = 26
        self.anime.save()
        self.assertEqual(
            Anime.objects.get(item__media_id=1, user=self.user).repeats,
            1,
        )

    def test_progress_bigger_than_max(self):
        """When progress is bigger than max, it should be set to max."""
        self.anime.status = "In progress"
        self.anime.progress = 30
        self.anime.save()
        self.assertEqual(
            Anime.objects.get(item__media_id=1, user=self.user).progress,
            26,
        )


class TVModel(TestCase):
    """Test the @properties and custom save of the TV model."""

    def setUp(self):
        """Create a user and a season with episodes."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        item_tv = Item.objects.create(
            media_id=1668,
            source="tmdb",
            media_type="tv",
            title="Friends",
            image="http://example.com/image.jpg",
        )

        self.tv = TV.objects.create(
            item=item_tv,
            user=self.user,
            status="In progress",
            notes="",
        )

        item_season1 = Item.objects.create(
            media_id=1668,
            source="tmdb",
            media_type="season",
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
        )

        # create first season
        season1 = Season.objects.create(
            item=item_season1,
            related_tv=self.tv,
            user=self.user,
            status="In progress",
        )

        item_ep1 = Item.objects.create(
            media_id=1668,
            source="tmdb",
            media_type="episode",
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=1,
        )
        Episode.objects.create(
            item=item_ep1,
            related_season=season1,
            watch_date=date(2023, 6, 1),
        )

        item_ep2 = Item.objects.create(
            media_id=1668,
            source="tmdb",
            media_type="episode",
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=2,
        )
        Episode.objects.create(
            item=item_ep2,
            related_season=season1,
            watch_date=date(2023, 6, 2),
        )

        item_season2 = Item.objects.create(
            media_id=1668,
            source="tmdb",
            media_type="season",
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=2,
        )

        # create second season
        season2 = Season.objects.create(
            item=item_season2,
            related_tv=self.tv,
            user=self.user,
            status="In progress",
        )

        item_ep3 = Item.objects.create(
            media_id=1668,
            source="tmdb",
            media_type="episode",
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=2,
            episode_number=1,
        )
        Episode.objects.create(
            item=item_ep3,
            related_season=season2,
            watch_date=date(2023, 6, 4),
        )

        item_ep4 = Item.objects.create(
            media_id=1668,
            source="tmdb",
            media_type="episode",
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=2,
            episode_number=2,
        )
        Episode.objects.create(
            item=item_ep4,
            related_season=season2,
            watch_date=date(2023, 6, 5),
        )

    def test_tv_progress(self):
        """Test the progress property of the Season model."""
        self.assertEqual(self.tv.progress, 4)

    def test_tv_start_date(self):
        """Test the start_date property of the Season model."""
        self.assertEqual(self.tv.start_date, date(2023, 6, 1))

    def test_tv_end_date(self):
        """Test the end_date property of the Season model."""
        self.assertEqual(self.tv.end_date, date(2023, 6, 5))

    def test_tv_save(self):
        """Test the custom save method of the TV model."""
        self.tv.status = "Completed"
        self.tv.save(update_fields=["status"])

        # check if all seasons are created with the status "Completed"
        self.assertEqual(self.tv.seasons.filter(status="Completed").count(), 10)


class SeasonModel(TestCase):
    """Test the @properties and custom save of the Season model."""

    def setUp(self):
        """Create a user and a season with episodes."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        item_tv = Item.objects.create(
            media_id=1668,
            source="tmdb",
            media_type="tv",
            title="Friends",
            image="http://example.com/image.jpg",
        )

        related_tv = TV.objects.create(
            item=item_tv,
            user=self.user,
            status="In progress",
        )

        item_season = Item.objects.create(
            media_id=1668,
            source="tmdb",
            media_type="season",
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
        )

        self.season = Season.objects.create(
            item=item_season,
            related_tv=related_tv,
            user=self.user,
            status="In progress",
        )

        item_ep1 = Item.objects.create(
            media_id=1668,
            source="tmdb",
            media_type="episode",
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=1,
        )
        Episode.objects.create(
            item=item_ep1,
            related_season=self.season,
            watch_date=date(2023, 6, 1),
        )

        item_ep2 = Item.objects.create(
            media_id=1668,
            source="tmdb",
            media_type="episode",
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=2,
        )
        Episode.objects.create(
            item=item_ep2,
            related_season=self.season,
            watch_date=date(2023, 6, 2),
        )

    def test_season_progress(self):
        """Test the progress property of the Season model."""
        self.assertEqual(self.season.progress, 2)

    def test_season_start_date(self):
        """Test the start_date property of the Season model."""
        self.assertEqual(self.season.start_date, date(2023, 6, 1))

    def test_season_end_date(self):
        """Test the end_date property of the Season model."""
        self.assertEqual(self.season.end_date, date(2023, 6, 2))

    def test_season_save(self):
        """Test the custom save method of the Season model."""
        self.season.status = "Completed"
        self.season.save(update_fields=["status"])

        # check if all episodes are created
        self.assertEqual(self.season.episodes.count(), 24)


class EpisodeModel(TestCase):
    """Test the custom save of the Episode model."""

    def setUp(self):
        """Create a user and a season."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        item_tv = Item.objects.create(
            media_id=1668,
            source="tmdb",
            media_type="tv",
            title="Friends",
            image="http://example.com/image.jpg",
        )

        related_tv = TV.objects.create(
            item=item_tv,
            user=self.user,
            notes="",
            status="In progress",
        )

        item_season = Item.objects.create(
            media_id=1668,
            source="tmdb",
            media_type="season",
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
        )

        self.season = Season.objects.create(
            item=item_season,
            related_tv=related_tv,
            user=self.user,
            status="In progress",
            notes="",
        )

    def test_episode_save(self):
        """Test the custom save method of the Episode model."""
        for i in range(1, 25):
            item_episode = Item.objects.create(
                media_id=1668,
                source="tmdb",
                media_type="episode",
                title="Friends",
                image="http://example.com/image.jpg",
                season_number=1,
                episode_number=i,
            )
            Episode.objects.create(
                item=item_episode,
                related_season=self.season,
                watch_date=date(2023, 6, i),
            )

        # if when all episodes are created, the season status should be "Completed"
        self.assertEqual(self.season.status, "Completed")
