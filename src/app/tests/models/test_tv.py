from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

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


class TVModel(TestCase):
    """Test the @properties and custom save of the TV model."""

    def setUp(self):
        """Create a user and a season with episodes."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        item_season1 = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
        )

        season1 = Season.objects.create(
            item=item_season1,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        self.tv = TV.objects.get(user=self.user)

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
            related_season=season1,
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
            related_season=season1,
            end_date=datetime(2023, 6, 2, 0, 0, tzinfo=UTC),
        )

        item_season2 = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=2,
        )

        season2 = Season.objects.create(
            item=item_season2,
            related_tv=self.tv,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        item_ep3 = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=2,
            episode_number=1,
        )
        Episode.objects.create(
            item=item_ep3,
            related_season=season2,
            end_date=datetime(2023, 6, 4, 0, 0, tzinfo=UTC),
        )

        item_ep4 = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=2,
            episode_number=2,
        )
        Episode.objects.create(
            item=item_ep4,
            related_season=season2,
            end_date=datetime(2023, 6, 5, 0, 0, tzinfo=UTC),
        )

    def test_tv_progress(self):
        """Test the progress property of the Season model."""
        self.assertEqual(self.tv.progress, 4)

    def test_tv_start_date(self):
        """Test the start_date property of the Season model."""
        self.assertEqual(
            self.tv.start_date,
            datetime(2023, 6, 1, 0, 0, tzinfo=UTC),
        )

    def test_tv_end_date(self):
        """Test the end_date property of the Season model."""
        self.assertEqual(
            self.tv.end_date,
            datetime(2023, 6, 5, 0, 0, tzinfo=UTC),
        )

    def test_tv_save(self):
        """Test the custom save method of the TV model."""
        self.tv.status = Status.COMPLETED.value
        self.tv.save(update_fields=["status"])

        self.assertEqual(
            self.tv.seasons.filter(status=Status.COMPLETED.value).count(),
            10,
        )


class TVStatusTests(TestCase):
    """Test TV model status change behaviors."""

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

        self.season1_item = Item.objects.create(
            media_id="123",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test Show",
            image="http://example.com/image.jpg",
            season_number=1,
        )

        self.season1 = Season.objects.create(
            item=self.season1_item,
            user=self.user,
            related_tv=self.tv,
            status=Status.IN_PROGRESS.value,
        )

        self.season2_item = Item.objects.create(
            media_id="123",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test Show",
            image="http://example.com/image.jpg",
            season_number=2,
        )

        self.season2 = Season.objects.create(
            item=self.season2_item,
            user=self.user,
            related_tv=self.tv,
            status=Status.PLANNING.value,
        )

    @patch("app.models.providers.services.get_media_metadata")
    def test_completed_status_creates_all_seasons(self, mock_get_metadata):
        """Test setting status to COMPLETED creates all seasons."""
        mock_metadata = {
            "max_progress": 10,
            "related": {
                "seasons": [
                    {"season_number": 1, "image": "img1.jpg"},
                    {"season_number": 2, "image": "img2.jpg"},
                    {"season_number": 3, "image": "img3.jpg"},
                ],
            },
            "season/1": {
                "image": "http://example.com/image.jpg",
                "season_number": 1,
                "episodes": [{"episode_number": 1}] * 10,
            },
            "season/2": {
                "image": "http://example.com/image.jpg",
                "season_number": 2,
                "episodes": [{"episode_number": 1}] * 10,
            },
            "season/3": {
                "image": "http://example.com/image.jpg",
                "season_number": 3,
                "episodes": [{"episode_number": 1}] * 10,
            },
        }
        mock_get_metadata.return_value = mock_metadata

        self.tv.status = Status.COMPLETED.value
        self.tv.save()

        self.assertEqual(self.tv.seasons.count(), 3)
        self.assertEqual(
            self.tv.seasons.filter(status=Status.COMPLETED.value).count(),
            3,
        )

        for season in self.tv.seasons.all():
            self.assertTrue(season.episodes.exists())

    def test_dropped_status_marks_in_progress_seasons_dropped(self):
        """Test setting status to DROPPED marks in-progress seasons as dropped."""
        season3_item = Item.objects.create(
            media_id="123",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test Show",
            image="http://example.com/image.jpg",
            season_number=3,
        )

        Season.objects.create(
            item=season3_item,
            user=self.user,
            related_tv=self.tv,
            status=Status.IN_PROGRESS.value,
        )

        self.tv.status = Status.DROPPED.value
        self.tv.save()

        self.assertEqual(
            self.tv.seasons.filter(status=Status.DROPPED.value).count(),
            2,  # season1 and season3
        )
        self.assertEqual(
            self.tv.seasons.filter(status=Status.PLANNING.value).count(),
            1,
        )

    @patch("app.models.providers.services.get_media_metadata")
    def test_in_progress_status_activates_next_season(self, _):
        """Test setting status to IN_PROGRESS activates next available season."""
        self.season1.status = Status.COMPLETED.value
        self.season1.save()

        self.tv.status = Status.IN_PROGRESS.value
        self.tv.save()

        season2 = Season.objects.get(pk=self.season2.pk)
        self.assertEqual(season2.status, Status.IN_PROGRESS.value)

    @patch("app.models.providers.services.get_media_metadata")
    def test_in_progress_status_creates_new_season_if_needed(self, mock_get_metadata):
        """Test setting status to IN_PROGRESS creates new season if needed."""
        self.season1.status = Status.COMPLETED.value
        self.season1.save()
        self.season2.status = Status.COMPLETED.value
        self.season2.save()

        mock_metadata = {
            "related": {
                "seasons": [
                    {"season_number": 1, "image": "img1.jpg"},
                    {"season_number": 2, "image": "img2.jpg"},
                    {"season_number": 3, "image": "img3.jpg"},
                ],
            },
        }
        mock_get_metadata.return_value = mock_metadata

        self.tv.status = Status.IN_PROGRESS.value
        self.tv.save()

        season3 = self.tv.seasons.get(item__season_number=3)
        self.assertEqual(season3.status, Status.IN_PROGRESS.value)

    def test_in_progress_status_noop_if_already_has_in_progress_season(self):
        """Test IN_PROGRESS status change does nothing if season already in progress."""
        original_season1_status = self.season1.status

        self.tv.status = Status.IN_PROGRESS.value
        self.tv.save()

        season1 = Season.objects.get(pk=self.season1.pk)
        self.assertEqual(season1.status, original_season1_status)


