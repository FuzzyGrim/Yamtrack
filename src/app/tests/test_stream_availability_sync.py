from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from app import stream_availability_sync
from app.models import Item, MediaTypes, Sources
from app.providers.services import ProviderAPIError


class RefreshBucketTests(TestCase):
    """Test the stable hash-bucket helpers used to spread refreshes."""

    def test_refresh_bucket_is_stable(self):
        """The same item identity always maps to the same bucket."""
        item = Item.objects.create(
            media_id="550",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Fight Club",
            image="http://example.com/image.jpg",
        )

        bucket = stream_availability_sync.refresh_bucket(item)

        self.assertEqual(bucket, stream_availability_sync.refresh_bucket(item))
        self.assertIn(bucket, range(stream_availability_sync.BUCKET_COUNT))

    def test_should_refresh_item_never_fetched_always_true(self):
        """Items that have never been fetched are always due for refresh."""
        item = Item.objects.create(
            media_id="550",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Fight Club",
            image="http://example.com/image.jpg",
        )

        self.assertTrue(stream_availability_sync.should_refresh_item(item))

    def test_should_refresh_item_only_on_its_bucket_day(self):
        """An already-fetched item only refreshes on its own bucket's day."""
        item = Item.objects.create(
            media_id="550",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Fight Club",
            image="http://example.com/image.jpg",
            stream_availability_updated_at=timezone.now(),
        )
        bucket = stream_availability_sync.refresh_bucket(item)
        today = timezone.localdate()
        offset = (bucket - today.toordinal()) % stream_availability_sync.BUCKET_COUNT
        matching_day = today + timedelta(days=offset)
        other_day = matching_day + timedelta(days=1)

        self.assertTrue(
            stream_availability_sync.should_refresh_item(item, today=matching_day),
        )
        self.assertFalse(
            stream_availability_sync.should_refresh_item(item, today=other_day),
        )

    def test_should_refresh_item_force_ignores_bucket(self):
        """Force always refreshes regardless of the bucket."""
        item = Item.objects.create(
            media_id="550",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Fight Club",
            image="http://example.com/image.jpg",
            stream_availability_updated_at=timezone.now(),
        )

        with patch.object(
            stream_availability_sync, "is_todays_refresh_bucket", return_value=False
        ):
            self.assertTrue(
                stream_availability_sync.should_refresh_item(item, force=True),
            )


class RefreshStreamAvailabilityTaskTests(TestCase):
    """Test the refresh_stream_availability backfill/refresh task."""

    def setUp(self):
        """Clear the refresh lock and configure a region for the task to use."""
        cache.delete(stream_availability_sync.REFRESH_LOCK_KEY)
        self.addCleanup(cache.delete, stream_availability_sync.REFRESH_LOCK_KEY)
        credentials = {"username": "regioned", "password": "12345"}
        get_user_model().objects.create_user(
            **credentials,
            watch_provider_region="US",
        )

    def test_no_regions_configured_skips_entirely(self):
        """With no user region configured, the task does nothing."""
        get_user_model().objects.all().delete()
        Item.objects.create(
            media_id="550",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Fight Club",
            image="http://example.com/image.jpg",
        )

        with patch("app.providers.tmdb.fetch_watch_providers") as mock_fetch:
            result = stream_availability_sync.refresh_stream_availability()

        mock_fetch.assert_not_called()
        self.assertEqual(result["status"], "no_regions_configured")

    def test_backfills_never_fetched_items(self):
        """Items with no stream_availability_updated_at are always populated."""
        item = Item.objects.create(
            media_id="550",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Fight Club",
            image="http://example.com/image.jpg",
        )

        with patch(
            "app.providers.tmdb.fetch_watch_providers",
            return_value={"US": {"flatrate": [{"provider_id": 8}]}},
        ):
            result = stream_availability_sync.refresh_stream_availability()

        item.refresh_from_db()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["refreshed"], 1)
        self.assertEqual(item.stream_availability, {"US": 1})
        self.assertIsNotNone(item.stream_availability_updated_at)

    def test_periodic_refresh_skips_items_outside_todays_bucket(self):
        """Already-fetched items outside today's bucket are left untouched."""
        item = Item.objects.create(
            media_id="550",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Fight Club",
            image="http://example.com/image.jpg",
            stream_availability={"US": 1},
            stream_availability_updated_at=timezone.now(),
        )

        with (
            patch.object(
                stream_availability_sync,
                "is_todays_refresh_bucket",
                return_value=False,
            ),
            patch("app.providers.tmdb.fetch_watch_providers") as mock_fetch,
        ):
            result = stream_availability_sync.refresh_stream_availability()

        mock_fetch.assert_not_called()
        item.refresh_from_db()
        self.assertEqual(item.stream_availability, {"US": 1})
        self.assertEqual(result["refreshed"], 0)
        self.assertEqual(result["skipped"], 1)

    def test_force_refreshes_regardless_of_bucket(self):
        """force=True refetches every eligible item, bypassing the bucket."""
        item = Item.objects.create(
            media_id="550",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Fight Club",
            image="http://example.com/image.jpg",
            stream_availability={"US": 1},
            stream_availability_updated_at=timezone.now(),
        )

        with (
            patch.object(
                stream_availability_sync,
                "is_todays_refresh_bucket",
                return_value=False,
            ),
            patch(
                "app.providers.tmdb.fetch_watch_providers",
                return_value={"US": {"buy": [{"provider_id": 2}]}},
            ) as mock_fetch,
        ):
            result = stream_availability_sync.refresh_stream_availability(force=True)

        mock_fetch.assert_called_once_with(item)
        item.refresh_from_db()
        self.assertEqual(item.stream_availability, {"US": 2})
        self.assertEqual(result["refreshed"], 1)

    def test_lock_prevents_overlapping_runs(self):
        """A second run is skipped while the refresh lock is held."""
        cache.set(stream_availability_sync.REFRESH_LOCK_KEY, True, timeout=60)  # noqa: FBT003

        with patch("app.providers.tmdb.fetch_watch_providers") as mock_fetch:
            result = stream_availability_sync.refresh_stream_availability()

        mock_fetch.assert_not_called()
        self.assertEqual(result["status"], "already_running")

    def test_404_marks_item_and_skips_future_retries(self):
        """A 404 from TMDB marks the item as checked rather than retrying forever."""
        item = Item.objects.create(
            media_id="550",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Fight Club",
            image="http://example.com/image.jpg",
        )
        error = ProviderAPIError(Sources.TMDB.value, Exception("not found"))
        error.status_code = 404

        with patch(
            "app.providers.tmdb.fetch_watch_providers",
            side_effect=error,
        ):
            result = stream_availability_sync.refresh_stream_availability()

        item.refresh_from_db()
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["failed"], 0)
        self.assertIsNotNone(item.stream_availability_updated_at)

    def test_other_errors_are_counted_as_failed(self):
        """Non-404 provider errors are counted as failures, not silently skipped."""
        Item.objects.create(
            media_id="550",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Fight Club",
            image="http://example.com/image.jpg",
        )
        error = ProviderAPIError(Sources.TMDB.value, Exception("server error"))
        error.status_code = 500

        with patch(
            "app.providers.tmdb.fetch_watch_providers",
            side_effect=error,
        ):
            result = stream_availability_sync.refresh_stream_availability()

        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["refreshed"], 0)
