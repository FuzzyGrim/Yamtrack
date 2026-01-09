from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from app.models import (
    TV,
    Episode,
    Item,
    MediaTypes,
    Season,
    Sources,
    Status,
)

mock_path = Path(__file__).resolve().parent.parent / "mock_data"


class SeasonModel(TestCase):
    """Test the @properties and custom save of the Season model."""

    def setUp(self):
        """Create a user and a season with episodes."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        item_season = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
        )

        self.season = Season.objects.create(
            item=item_season,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        item_ep1 = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=1,
        )
        Episode.objects.create(
            item=item_ep1,
            related_season=self.season,
            end_date=datetime(2023, 6, 1, 0, 0, tzinfo=UTC),
        )

        item_ep2 = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=2,
        )
        Episode.objects.create(
            item=item_ep2,
            related_season=self.season,
            end_date=datetime(2023, 6, 2, 0, 0, tzinfo=UTC),
        )

    def test_season_progress(self):
        """Test the progress property of the Season model."""
        self.assertEqual(self.season.progress, 2)

    def test_season_start_date(self):
        """Test the start_date property of the Season model."""
        self.assertEqual(
            self.season.start_date,
            datetime(2023, 6, 1, 0, 0, tzinfo=UTC),
        )

    def test_season_end_date(self):
        """Test the end_date property of the Season model."""
        self.assertEqual(
            self.season.end_date,
            datetime(2023, 6, 2, 0, 0, tzinfo=UTC),
        )

    def test_season_save(self):
        """Test the custom save method of the Season model."""
        self.season.status = Status.COMPLETED.value
        self.season.save(update_fields=["status"])

        self.assertEqual(self.season.episodes.count(), 24)

    @patch("app.models.Season.get_episode_item")
    def test_watch_method(self, mock_get_episode_item):
        """Test the watch method of the Season model."""
        episode_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=3,
        )
        mock_get_episode_item.return_value = episode_item

        self.season.watch(3, datetime(2023, 6, 3, 0, 0, tzinfo=UTC))

        episode = Episode.objects.get(
            related_season=self.season,
            item=episode_item,
        )
        self.assertEqual(episode.end_date, datetime(2023, 6, 3, 0, 0, tzinfo=UTC))

        self.season.watch(3, datetime(2023, 6, 4, 0, 0, tzinfo=UTC))

        episodes = Episode.objects.filter(
            related_season=self.season,
            item=episode_item,
        )
        self.assertEqual(
            episodes.first().end_date,
            datetime(2023, 6, 4, 0, 0, tzinfo=UTC),
        )
        self.assertEqual(episodes.count(), 2)

    @patch("app.models.Season.get_episode_item")
    def test_watch_with_none_date(self, mock_get_episode_item):
        """Test the watch method with None date."""
        episode_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=3,
        )
        mock_get_episode_item.return_value = episode_item

        self.season.watch(3, None)

        episode = Episode.objects.get(
            related_season=self.season,
            item=episode_item,
        )
        self.assertIsNone(episode.end_date)

    @patch("app.models.Season.get_episode_item")
    def test_unwatch_method(self, mock_get_episode_item):
        """Test the unwatch method of the Season model."""
        episode_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=3,
        )
        mock_get_episode_item.return_value = episode_item

        Episode.objects.create(
            related_season=self.season,
            item=episode_item,
            end_date=datetime(2023, 6, 3, 0, 0, tzinfo=UTC),
        )

        self.season.unwatch(3)

        with self.assertRaises(Episode.DoesNotExist):
            Episode.objects.get(
                related_season=self.season,
                item=episode_item,
            )

    @patch("app.models.Season.get_episode_item")
    def test_unwatch_with_repeats(self, mock_get_episode_item):
        """Test the unwatch method with an episode that has repeats."""
        episode_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=3,
        )
        mock_get_episode_item.return_value = episode_item

        Episode.objects.create(
            related_season=self.season,
            item=episode_item,
            end_date=datetime(2023, 6, 3, 0, 0, tzinfo=UTC),
        )
        Episode.objects.create(
            related_season=self.season,
            item=episode_item,
            end_date=datetime(2024, 6, 3, 0, 0, tzinfo=UTC),
        )

        self.season.unwatch(3)

        episodes = Episode.objects.filter(
            related_season=self.season,
            item=episode_item,
        )
        self.assertEqual(episodes.count(), 1)

    @patch("app.models.Season.get_episode_item")
    def test_unwatch_nonexistent_episode(self, mock_get_episode_item):
        """Test unwatching a non-existent episode."""
        episode_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=3,
        )
        mock_get_episode_item.return_value = episode_item

        self.season.unwatch(3)

        with self.assertRaises(Episode.DoesNotExist):
            Episode.objects.get(
                related_season=self.season,
                item=episode_item,
            )


class SeasonStatusTests(TestCase):
    """Test Season model status change behaviors."""

    def setUp(self):
        """Create test data."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        self.tv_item = Item.objects.create(
            media_id="123",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test Show",
            image="http://example.com/image.jpg",
        )

        self.tv = TV.objects.create(
            item=self.tv_item,
            user=self.user,
            status=Status.PLANNING.value,
        )

        self.season_item = Item.objects.create(
            media_id="123",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test Show",
            image="http://example.com/image.jpg",
            season_number=1,
        )

        self.season = Season.objects.create(
            item=self.season_item,
            user=self.user,
            related_tv=self.tv,
            status=Status.PLANNING.value,
        )

    @patch("app.models.providers.services.get_media_metadata")
    def test_completed_status_creates_remaining_episodes(self, mock_get_metadata):
        """Test setting status to COMPLETED creates remaining episodes."""
        mock_metadata = {
            "episodes": [
                {"episode_number": 1, "image": "img1.jpg"},
                {"episode_number": 2, "image": "img2.jpg"},
                {"episode_number": 3, "image": "img3.jpg"},
            ],
            "image": "season_img.jpg",
        }
        mock_get_metadata.return_value = mock_metadata

        self.season.status = Status.COMPLETED.value
        self.season.save()

        self.assertEqual(self.season.episodes.count(), 3)
        episode_numbers = set(
            self.season.episodes.values_list("item__episode_number", flat=True),
        )
        self.assertEqual(episode_numbers, {1, 2, 3})

    def test_dropped_status_updates_tv_status(self):
        """Test setting status to DROPPED updates TV status."""
        self.season.status = Status.DROPPED.value
        self.season.save()

        self.tv.refresh_from_db()
        self.assertEqual(self.tv.status, Status.DROPPED.value)

    def test_in_progress_status_updates_tv_status(self):
        """Test setting status to IN_PROGRESS updates TV status."""
        self.season.status = Status.IN_PROGRESS.value
        self.season.save()

        self.tv.refresh_from_db()
        self.assertEqual(self.tv.status, Status.IN_PROGRESS.value)

    def test_status_change_does_not_affect_tv_if_already_same_status(self):
        """Test status change doesn't update TV if already same status."""
        self.tv.status = Status.IN_PROGRESS.value
        self.tv.save()

        with patch.object(TV, "save") as mock_tv_save:
            self.season.status = Status.IN_PROGRESS.value
            self.season.save()

            # TV save shouldn't have been called
            mock_tv_save.assert_not_called()

    @patch("app.models.providers.services.get_media_metadata")
    def test_completed_status_noop_if_no_remaining_episodes(self, mock_get_metadata):
        """Test COMPLETED status does nothing if no remaining episodes."""
        mock_metadata = {
            "episodes": [
                {"episode_number": 1, "image": "img1.jpg"},
            ],
            "image": "season_img.jpg",
        }
        mock_get_metadata.return_value = mock_metadata

        ep_item = Item.objects.create(
            media_id="123",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Test Episode",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=1,
        )
        Episode.objects.bulk_create(
            [
                Episode(
                    item=ep_item,
                    related_season=self.season,
                    end_date=timezone.now(),
                ),
            ],
        )

        with patch("app.models.bulk_create_with_history") as mock_bulk_create:
            self.season.status = Status.COMPLETED.value
            self.season.save()

            # bulk_create shouldn't have been called
            mock_bulk_create.assert_not_called()

    def test_get_tv_creates_tv_if_not_exists(self):
        """Test get_tv creates TV instance if it doesn't exist."""
        self.tv.delete()

        with patch(
            "app.models.providers.services.get_media_metadata",
        ) as mock_get_metadata:
            mock_metadata = {
                "title": "Test Show",
                "image": "tv_img.jpg",
                "details": {"seasons": 1},
            }
            mock_get_metadata.return_value = mock_metadata

            # Call get_tv
            tv = self.season.get_tv()

            self.assertIsNotNone(tv)
            self.assertEqual(tv.item.title, "Test Show")
            self.assertEqual(tv.status, Status.PLANNING.value)


