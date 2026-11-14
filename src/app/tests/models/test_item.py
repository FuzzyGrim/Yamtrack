from pathlib import Path
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext, override_settings

from app.models import (
    Item,
    MediaTypes,
    Sources,
)

mock_path = Path(__file__).resolve().parent.parent / "mock_data"


class ItemModel(TestCase):
    """Test case for the Item model."""

    def setUp(self):
        """Set up test data for Item model."""
        self.item = Item.objects.create(
            media_id="1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
        )

    def test_item_creation(self):
        """Test the creation of an Item instance."""
        self.assertEqual(self.item.media_id, "1")
        self.assertEqual(self.item.media_type, MediaTypes.MOVIE.value)
        self.assertEqual(self.item.title, "Test Movie")
        self.assertEqual(self.item.image, "http://example.com/image.jpg")

    def test_runtime_only_save_keeps_provider_payload_deferred_and_unchanged(self):
        providers = {"US": [{"provider_id": 1, "logo_path": "x" * 1000}]}
        Item.objects.filter(pk=self.item.pk).update(watch_providers=providers)
        item = Item.objects.defer("watch_providers").get(pk=self.item.pk)
        item.runtime_minutes = 45
        with CaptureQueriesContext(connection) as captured:
            item.save(update_fields=["runtime_minutes"])
        self.assertIn("watch_providers", item.get_deferred_fields())
        self.assertFalse(any('"watch_providers"' in query["sql"] for query in captured))
        self.item.refresh_from_db()
        self.assertEqual(self.item.watch_providers, providers)
        self.assertEqual(self.item.runtime_minutes, 45)

    def test_explicit_provider_save_still_normalizes_none(self):
        item = Item.objects.defer("watch_providers").get(pk=self.item.pk)
        item.watch_providers = None
        item.save(update_fields=["watch_providers"])
        item.refresh_from_db()
        self.assertEqual(item.watch_providers, {})

    def test_full_save_still_loads_and_preserves_deferred_provider_payload(self):
        providers = {"US": [{"provider_id": 1}]}
        Item.objects.filter(pk=self.item.pk).update(watch_providers=providers)
        item = Item.objects.defer("watch_providers").get(pk=self.item.pk)
        item.title = "Updated Movie"
        item.save()
        self.assertNotIn("watch_providers", item.get_deferred_fields())
        item.refresh_from_db()
        self.assertEqual(item.watch_providers, providers)
        self.assertEqual(item.title, "Updated Movie")

    def test_item_str_representation(self):
        """Test the string representation of an Item."""
        self.assertEqual(str(self.item), "Test Movie")

    def test_episode_coordinates_in_item_names(self):
        item = Item(title="Show")
        for season, episode, expected in (
            (0, 3, "Show S00E03"),
            (3, 3, "Show S03E03"),
            (123, 456, "Show S123E456"),
            (3, None, "Show S3"),
            (None, None, "Show"),
        ):
            with self.subTest(season=season, episode=episode):
                item.season_number, item.episode_number = season, episode
                self.assertEqual(str(item), expected)

    def test_item_with_season_and_episode(self):
        """Test the string representation of an Item with season and episode."""
        item = Item.objects.create(
            media_id="2",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Test Show",
            image="http://example.com/image2.jpg",
            season_number=1,
            episode_number=2,
        )
        self.assertEqual(str(item), "Test Show S01E02")


class FetchReleasesBucketTests(TestCase):
    """fetch_releases must cope with a show stored in two library buckets (#1530)."""

    @override_settings(TESTING=False)
    @patch("events.tasks.reload_calendar")
    def test_season_reloads_oldest_tv_row_when_show_is_in_two_buckets(self, reload):
        identity = {
            "media_id": "1396",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "title": "Breaking Bad",
            "image": "http://example.com/bb.jpg",
        }
        first = Item.objects.create(library_media_type=MediaTypes.TV.value, **identity)
        Item.objects.create(library_media_type=MediaTypes.SEASON.value, **identity)
        season = Item.objects.create(
            media_id="1396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Breaking Bad",
            image="http://example.com/s1.jpg",
            season_number=1,
        )

        season.fetch_releases(delay=False)

        reload.assert_called_once_with(item_ids=[first.id])
