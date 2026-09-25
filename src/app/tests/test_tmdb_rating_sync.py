from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from app import tmdb_rating_sync
from app.models import Item, MediaTypes, Sources


class TmdbRatingBucketTests(TestCase):
    """Test the stable hash-bucket helpers used to spread refreshes."""

    def test_rating_refresh_bucket_is_stable(self):
        """The same item identity always maps to the same bucket."""
        item = Item.objects.create(
            media_id="550",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Fight Club",
            image="http://example.com/image.jpg",
        )

        bucket = tmdb_rating_sync.rating_refresh_bucket(item)

        self.assertEqual(bucket, tmdb_rating_sync.rating_refresh_bucket(item))
        self.assertIn(bucket, range(tmdb_rating_sync.BUCKET_COUNT))

    def test_should_refresh_item_never_fetched_always_true(self):
        """Items that have never been fetched are always due for refresh."""
        item = Item.objects.create(
            media_id="550",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Fight Club",
            image="http://example.com/image.jpg",
        )

        self.assertTrue(tmdb_rating_sync.should_refresh_item(item))

    def test_should_refresh_item_only_on_its_bucket_day(self):
        """An already-fetched item only refreshes on its own bucket's day."""
        item = Item.objects.create(
            media_id="550",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Fight Club",
            image="http://example.com/image.jpg",
            tmdb_rating_updated_at=timezone.now(),
        )
        bucket = tmdb_rating_sync.rating_refresh_bucket(item)
        today = timezone.localdate()
        offset = (bucket - today.toordinal()) % tmdb_rating_sync.BUCKET_COUNT
        matching_day = today + timedelta(days=offset)
        other_day = matching_day + timedelta(days=1)

        self.assertTrue(
            tmdb_rating_sync.should_refresh_item(item, today=matching_day),
        )
        self.assertFalse(
            tmdb_rating_sync.should_refresh_item(item, today=other_day),
        )

    def test_should_refresh_item_force_ignores_bucket(self):
        """Force always refreshes regardless of the bucket."""
        item = Item.objects.create(
            media_id="550",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Fight Club",
            image="http://example.com/image.jpg",
            tmdb_rating_updated_at=timezone.now(),
        )

        with patch.object(
            tmdb_rating_sync, "is_todays_refresh_bucket", return_value=False
        ):
            self.assertTrue(tmdb_rating_sync.should_refresh_item(item, force=True))


class RefreshTmdbRatingsTaskTests(TestCase):
    """Test the refresh_tmdb_ratings backfill/refresh task."""

    def setUp(self):
        """Clear the refresh lock between tests."""
        cache.delete(tmdb_rating_sync.REFRESH_LOCK_KEY)
        self.addCleanup(cache.delete, tmdb_rating_sync.REFRESH_LOCK_KEY)

    def test_backfills_never_fetched_items(self):
        """Items with no tmdb_rating_updated_at are always populated."""
        item = Item.objects.create(
            media_id="550",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Fight Club",
            image="http://example.com/image.jpg",
        )

        with (
            patch("app.providers.tmdb.cached_score", return_value=None),
            patch("app.providers.tmdb.fetch_vote_average", return_value=8.8),
        ):
            result = tmdb_rating_sync.refresh_tmdb_ratings()

        item.refresh_from_db()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["refreshed"], 1)
        self.assertEqual(float(item.tmdb_rating), 8.8)
        self.assertIsNotNone(item.tmdb_rating_updated_at)

    def test_periodic_refresh_skips_items_outside_todays_bucket(self):
        """Already-fetched items outside today's bucket are left untouched."""
        item = Item.objects.create(
            media_id="550",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Fight Club",
            image="http://example.com/image.jpg",
            tmdb_rating=5.0,
            tmdb_rating_updated_at=timezone.now(),
        )

        with (
            patch.object(
                tmdb_rating_sync, "is_todays_refresh_bucket", return_value=False
            ),
            patch("app.providers.tmdb.fetch_vote_average") as mock_fetch,
        ):
            result = tmdb_rating_sync.refresh_tmdb_ratings()

        mock_fetch.assert_not_called()
        item.refresh_from_db()
        self.assertEqual(float(item.tmdb_rating), 5.0)
        self.assertEqual(result["refreshed"], 0)
        self.assertEqual(result["skipped"], 1)

    def test_force_refreshes_regardless_of_bucket(self):
        """force=True refetches every TMDB item, bypassing the cache."""
        item = Item.objects.create(
            media_id="550",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Fight Club",
            image="http://example.com/image.jpg",
            tmdb_rating=5.0,
            tmdb_rating_updated_at=timezone.now(),
        )

        with (
            patch.object(
                tmdb_rating_sync, "is_todays_refresh_bucket", return_value=False
            ),
            patch("app.providers.tmdb.delete_metadata_cache") as mock_delete,
            patch(
                "app.providers.tmdb.fetch_vote_average", return_value=9.5
            ) as mock_fetch,
        ):
            result = tmdb_rating_sync.refresh_tmdb_ratings(force=True)

        mock_delete.assert_called_once_with(item)
        mock_fetch.assert_called_once_with(item)
        item.refresh_from_db()
        self.assertEqual(float(item.tmdb_rating), 9.5)
        self.assertEqual(result["refreshed"], 1)

    def test_lock_prevents_overlapping_runs(self):
        """A second run is skipped while the refresh lock is held."""
        cache.set(tmdb_rating_sync.REFRESH_LOCK_KEY, True, timeout=60)  # noqa: FBT003

        with patch("app.providers.tmdb.fetch_vote_average") as mock_fetch:
            result = tmdb_rating_sync.refresh_tmdb_ratings()

        mock_fetch.assert_not_called()
        self.assertEqual(result["status"], "already_running")
