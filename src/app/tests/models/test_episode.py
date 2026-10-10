from datetime import UTC, datetime, timedelta
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
    UserMessage,
    UserMessageLevel,
)

mock_path = Path(__file__).resolve().parent.parent / "mock_data"


class EpisodeModel(TestCase):
    """Test the custom save of the Episode model."""

    def setUp(self):
        """Create a user and a season."""
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
            notes="",
        )

    def test_episode_save(self):
        """Test the custom save method of the Episode model."""
        for i in range(1, 25):
            item_episode = Item.objects.create(
                media_id="1668",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title="Friends",
                image="http://example.com/image.jpg",
                season_number=1,
                episode_number=i,
            )
            Episode.objects.create(
                item=item_episode,
                related_season=self.season,
                end_date=datetime(2023, 6, i, 0, 0, tzinfo=UTC),
            )

        self.assertEqual(self.season.status, Status.COMPLETED.value)


class EpisodeStatusTests(TestCase):
    """Test how Episode model affects Season and TV statuses."""

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

        self.episode_item = Item.objects.create(
            media_id="123",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Test Episode",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=1,
        )

    @patch("app.models.providers.services.get_media_metadata")
    def test_first_episode_sets_season_in_progress(self, mock_get_metadata):
        """Test first episode sets season to IN_PROGRESS."""
        mock_metadata = {
            "season/1": {
                "episodes": [{"episode_number": 1}, {"episode_number": 2}],
            },
            "related": {
                "seasons": [{"season_number": 1}],
            },
        }
        mock_get_metadata.return_value = mock_metadata

        Episode.objects.create(
            item=self.episode_item,
            related_season=self.season,
            end_date=timezone.now(),
        )

        self.season.refresh_from_db()
        self.assertEqual(self.season.status, Status.IN_PROGRESS.value)

        self.tv.refresh_from_db()
        self.assertEqual(self.tv.status, Status.IN_PROGRESS.value)

    @patch("app.models.providers.services.get_media_metadata")
    def test_last_episode_sets_season_completed(self, mock_get_metadata):
        """Test last episode sets season to COMPLETED."""
        mock_metadata = {
            "season/1": {
                "episodes": [{"episode_number": 1}],
            },
            "related": {
                "seasons": [{"season_number": 1}],
            },
        }
        mock_get_metadata.return_value = mock_metadata

        Episode.objects.create(
            item=self.episode_item,
            related_season=self.season,
            end_date=timezone.now(),
        )

        self.season.refresh_from_db()
        self.assertEqual(self.season.status, Status.COMPLETED.value)

        self.tv.refresh_from_db()
        self.assertEqual(self.tv.status, Status.COMPLETED.value)

    @patch("app.models.providers.services.get_media_metadata")
    def test_middle_episode_does_not_change_status(self, mock_get_metadata):
        """Test middle episode doesn't change season/TV status."""
        mock_metadata = {
            "season/1": {
                "episodes": [
                    {"episode_number": 1},
                    {"episode_number": 2},
                    {"episode_number": 3},
                ],
            },
            "related": {
                "seasons": [{"season_number": 1}, {"season_number": 2}],
            },
        }
        mock_get_metadata.return_value = mock_metadata

        Episode.objects.create(
            item=self.episode_item,
            related_season=self.season,
            end_date=timezone.now(),
        )

        ep_item2 = Item.objects.create(
            media_id="123",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Test Episode 2",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=2,
        )

        with patch("app.models.bulk_update_with_history") as mock_bulk_update:
            Episode.objects.create(
                item=ep_item2,
                related_season=self.season,
                end_date=timezone.now(),
            )

            mock_bulk_update.assert_not_called()

    @patch("app.models.providers.services.get_media_metadata")
    def test_last_season_completes_tv_show(self, mock_get_metadata):
        """Test last season completion also completes TV show."""
        mock_metadata = {
            "season/1": {
                "episodes": [{"episode_number": 1}],
            },
            "related": {
                "seasons": [{"season_number": 1}],  # Only one season
            },
        }
        mock_get_metadata.return_value = mock_metadata

        Episode.objects.create(
            item=self.episode_item,
            related_season=self.season,
            end_date=timezone.now(),
        )

        self.tv.refresh_from_db()
        self.assertEqual(self.tv.status, Status.COMPLETED.value)
        self.assertTrue(
            UserMessage.objects.filter(
                user=self.user,
                level=UserMessageLevel.SUCCESS,
                message=f"{self.tv} was marked as completed automatically.",
            ).exists(),
        )

    @patch("app.models.providers.services.get_media_metadata")
    def test_non_last_season_starts_next_season(self, mock_get_metadata):
        """Test non-last season completion starts the next season."""
        next_season_item = Item.objects.create(
            media_id="123",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test Show",
            image="http://example.com/image2.jpg",
            season_number=2,
        )
        next_season = Season.objects.create(
            item=next_season_item,
            user=self.user,
            related_tv=self.tv,
            status=Status.PLANNING.value,
        )

        mock_metadata = {
            "season/1": {
                "episodes": [{"episode_number": 1}],
            },
            "related": {
                "seasons": [
                    {
                        "season_number": 1,
                        "first_air_date": datetime(2020, 1, 1, tzinfo=UTC),
                    },
                    {
                        "season_number": 2,
                        "first_air_date": datetime(2020, 1, 1, tzinfo=UTC),
                    },
                ],
            },
        }
        mock_get_metadata.return_value = mock_metadata

        Episode.objects.create(
            item=self.episode_item,
            related_season=self.season,
            end_date=timezone.now(),
        )

        next_season.refresh_from_db()
        self.assertEqual(next_season.status, Status.IN_PROGRESS.value)

        self.tv.refresh_from_db()
        self.assertEqual(self.tv.status, Status.IN_PROGRESS.value)
        self.assertTrue(
            UserMessage.objects.filter(
                user=self.user,
                level=UserMessageLevel.SUCCESS,
                message=f"{self.season} was marked as completed automatically.",
            ).exists(),
        )
        self.assertTrue(
            UserMessage.objects.filter(
                user=self.user,
                level=UserMessageLevel.INFO,
                message=f"{self.tv} Season 2 was marked as in progress automatically.",
            ).exists(),
        )

    @patch("app.models.providers.services.get_media_metadata")
    def test_non_last_season_with_future_next_season_keeps_planning(
        self,
        mock_get_metadata,
    ):
        """Future seasons should not auto-start when the current one completes."""
        next_season_item = Item.objects.create(
            media_id="123",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test Show",
            image="http://example.com/image2.jpg",
            season_number=2,
        )
        next_season = Season.objects.create(
            item=next_season_item,
            user=self.user,
            related_tv=self.tv,
            status=Status.PLANNING.value,
        )

        mock_get_metadata.return_value = {
            "season/1": {
                "episodes": [{"episode_number": 1}],
            },
            "related": {
                "seasons": [
                    {
                        "season_number": 1,
                        "first_air_date": datetime(2020, 1, 1, tzinfo=UTC),
                    },
                    {
                        "season_number": 2,
                        "first_air_date": datetime(2999, 1, 1, tzinfo=UTC),
                    },
                ],
            },
        }

        Episode.objects.create(
            item=self.episode_item,
            related_season=self.season,
            end_date=timezone.now(),
        )

        next_season.refresh_from_db()
        self.tv.refresh_from_db()

        self.assertEqual(next_season.status, Status.PLANNING.value)
        self.assertEqual(self.tv.status, Status.IN_PROGRESS.value)

    @patch("app.models.providers.services.get_media_metadata")
    def test_rewatch_last_episode_does_not_duplicate_completion_messages(
        self,
        mock_get_metadata,
    ):
        """Rewatching a finale should not create duplicate completion toasts."""
        mock_get_metadata.return_value = {
            "season/1": {
                "episodes": [{"episode_number": 1}],
            },
            "related": {
                "seasons": [{"season_number": 1}],
            },
        }

        first_watch_time = timezone.now()
        Episode.objects.create(
            item=self.episode_item,
            related_season=self.season,
            end_date=first_watch_time,
        )
        second_watch_time = first_watch_time + timedelta(days=1)
        Episode.objects.create(
            item=self.episode_item,
            related_season=self.season,
            end_date=second_watch_time,
        )

        self.season.refresh_from_db()
        self.tv.refresh_from_db()

        self.assertEqual(
            UserMessage.objects.filter(
                user=self.user,
                level=UserMessageLevel.SUCCESS,
                message=f"{self.season} was marked as completed automatically.",
            ).count(),
            1,
        )
        self.assertEqual(self.season.status, Status.COMPLETED.value)
        self.assertEqual(self.tv.status, Status.COMPLETED.value)
        self.assertEqual(
            UserMessage.objects.filter(
                user=self.user,
                level=UserMessageLevel.SUCCESS,
                message=f"{self.tv} was marked as completed automatically.",
            ).count(),
            1,
        )
