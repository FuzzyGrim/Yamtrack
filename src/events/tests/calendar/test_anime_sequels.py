from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import Anime, Item, MediaTypes, Sources, Status
from app.providers import services
from events.calendar.anime_sequels import (
    check_anime_sequels,
    is_trackable_anime_sequel,
)


class AnimeSequelTests(TestCase):
    """Test auto-tracking of newly announced anime sequels."""

    def setUp(self):
        """Create users and anime tracking fixtures."""
        self.fetch_releases_patcher = patch("app.models.Item.fetch_releases")
        self.addCleanup(self.fetch_releases_patcher.stop)
        self.fetch_releases_patcher.start()

        self.credentials = {"username": "test", "password": "12345"}
        self.other_credentials = {"username": "other", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.other_user = get_user_model().objects.create_user(**self.other_credentials)

        self.completed_item = self._create_item("1", "Prequel Anime")
        self.planning_item = self._create_item("2", "Planning Anime")

        # bulk_create bypasses Media.save hooks (metadata fetch, calendar reload)
        Anime.objects.bulk_create(
            [
                Anime(
                    item=self.completed_item,
                    user=self.user,
                    status=Status.COMPLETED.value,
                ),
                Anime(
                    item=self.planning_item,
                    user=self.user,
                    status=Status.PLANNING.value,
                ),
            ],
        )

    def _create_item(self, media_id, title):
        """Create an anime Item."""
        return Item.objects.create(
            media_id=media_id,
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title=title,
            image="http://example.com/item.jpg",
        )

    def _related(
        self,
        media_id,
        relation_type="sequel",
        media_format="tv",
        title="Sequel Anime",
    ):
        """Build a related anime entry in the provider's metadata shape."""
        return {
            "media_id": media_id,
            "source": Sources.MAL.value,
            "title": title,
            "media_type": MediaTypes.ANIME.value,
            "image": "http://example.com/sequel.jpg",
            "relation_type": relation_type,
            "media_format": media_format,
        }

    def test_is_trackable_anime_sequel_accepts_sequel(self):
        """Only sequels in a trackable format are accepted."""
        self.assertTrue(is_trackable_anime_sequel(self._related("3")))
        self.assertTrue(
            is_trackable_anime_sequel(self._related("3", media_format="movie")),
        )
        self.assertTrue(
            is_trackable_anime_sequel(self._related("3", media_format="ona")),
        )

    def test_is_trackable_anime_sequel_rejects_non_sequels(self):
        """Non-sequel relations are explicitly ignored."""
        for relation_type in (
            "prequel",
            "side_story",
            "alternative_version",
            "spin_off",
            "summary",
            "parent_story",
            "full_story",
            "character",
            "other",
            None,
        ):
            self.assertFalse(
                is_trackable_anime_sequel(
                    self._related("3", relation_type=relation_type),
                ),
                relation_type,
            )

    def test_is_trackable_anime_sequel_rejects_ignored_formats(self):
        """OVAs, specials and music videos are not tracked even as sequels."""
        for media_format in ("ova", "special", "music"):
            self.assertFalse(
                is_trackable_anime_sequel(
                    self._related("3", media_format=media_format),
                ),
                media_format,
            )

    def test_is_trackable_anime_sequel_rejects_missing_data(self):
        """Entries without relation data are not trackable."""
        self.assertFalse(is_trackable_anime_sequel({}))
        self.assertFalse(is_trackable_anime_sequel(None))

    @patch("events.calendar.anime_sequels.services.get_media_metadata")
    def test_sequel_created_as_planning_for_completed_users(
        self,
        mock_get_media_metadata,
    ):
        """A newly announced sequel is created as Planning for completed users."""
        mock_get_media_metadata.return_value = {
            "related": {"related_anime": [self._related("3")]},
        }

        result = check_anime_sequels()

        sequel_item = Item.objects.get(
            media_id="3",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
        )
        self.assertEqual(sequel_item.title, "Sequel Anime")
        anime = Anime.objects.get(item=sequel_item, user=self.user)
        self.assertEqual(anime.status, Status.PLANNING.value)
        self.assertIn("Created 1 anime sequel", result)

    @patch("events.calendar.anime_sequels.services.get_media_metadata")
    def test_sequel_not_duplicated_when_already_tracked(
        self,
        mock_get_media_metadata,
    ):
        """No duplicate Media row is created when the sequel is already tracked."""
        sequel_item = self._create_item("3", "Sequel Anime")
        Anime.objects.bulk_create(
            [
                Anime(
                    item=sequel_item,
                    user=self.user,
                    status=Status.COMPLETED.value,
                ),
            ],
        )
        mock_get_media_metadata.return_value = {
            "related": {"related_anime": [self._related("3")]},
        }

        result = check_anime_sequels()

        self.assertEqual(
            Anime.objects.filter(item=sequel_item, user=self.user).count(),
            1,
        )
        self.assertIn("Created 0 anime sequel", result)

    @patch("events.calendar.anime_sequels.services.get_media_metadata")
    def test_only_items_with_completed_tracker_are_processed(
        self,
        mock_get_media_metadata,
    ):
        """Anime with no Completed tracker is not polled for sequels."""
        mock_get_media_metadata.return_value = {
            "related": {"related_anime": [self._related("3")]},
        }

        check_anime_sequels()

        mock_get_media_metadata.assert_called_once()
        self.assertEqual(
            mock_get_media_metadata.call_args.kwargs["media_id"],
            self.completed_item.media_id,
        )
        self.assertEqual(
            Item.objects.filter(
                media_id="3",
                source=Sources.MAL.value,
                media_type=MediaTypes.ANIME.value,
            ).count(),
            1,
        )

    @patch("events.calendar.anime_sequels.services.get_media_metadata")
    def test_shared_item_fetched_once_for_multiple_users(
        self,
        mock_get_media_metadata,
    ):
        """Related data is fetched once per item, not once per user."""
        Anime.objects.bulk_create(
            [
                Anime(
                    item=self.completed_item,
                    user=self.other_user,
                    status=Status.COMPLETED.value,
                ),
            ],
        )
        mock_get_media_metadata.return_value = {
            "related": {"related_anime": [self._related("3")]},
        }

        result = check_anime_sequels()

        mock_get_media_metadata.assert_called_once()
        sequel_item = Item.objects.get(
            media_id="3",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
        )
        self.assertEqual(Anime.objects.filter(item=sequel_item).count(), 2)
        self.assertTrue(
            Anime.objects.filter(
                item=sequel_item,
                user=self.user,
                status=Status.PLANNING.value,
            ).exists(),
        )
        self.assertTrue(
            Anime.objects.filter(
                item=sequel_item,
                user=self.other_user,
                status=Status.PLANNING.value,
            ).exists(),
        )
        self.assertIn("Created 2 anime sequel", result)

    @patch("events.calendar.anime_sequels.services.get_media_metadata")
    def test_missing_or_malformed_related_anime_is_skipped(
        self,
        mock_get_media_metadata,
    ):
        """Missing or malformed related_anime data is logged and skipped."""
        mock_get_media_metadata.return_value = {"related": {}}
        result = check_anime_sequels()
        self.assertIn("Created 0 anime sequel", result)

        mock_get_media_metadata.return_value = {"related": {"related_anime": "invalid"}}
        result = check_anime_sequels()
        self.assertIn("Created 0 anime sequel", result)

    @patch("events.calendar.anime_sequels.services.get_media_metadata")
    def test_provider_error_is_skipped(
        self,
        mock_get_media_metadata,
    ):
        """Provider API failures are tolerated without raising."""
        mock_get_media_metadata.side_effect = services.ProviderAPIError(
            Sources.MAL.value,
            requests.exceptions.HTTPError("error"),
        )

        result = check_anime_sequels()

        self.assertIn("Created 0 anime sequel", result)
