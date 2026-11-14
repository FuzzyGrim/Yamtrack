from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db.models.signals import post_init
from django.test import TestCase

from app.discover.filters import (
    dedupe_candidates,
    exclude_owned_items,
    exclude_tracked_items,
    get_feedback_keys_by_media_type,
    get_owned_keys_by_media_type,
    get_tracked_keys_by_media_type,
)
from app.discover.schemas import CandidateItem
from app.models import (
    CollectionEntry,
    DiscoverFeedback,
    DiscoverFeedbackType,
    Item,
    MediaTypes,
    Movie,
    Sources,
    Status,
)


class DiscoverFilterTests(TestCase):
    """Tests for Discover candidate filtering helpers."""

    def setUp(self):
        self.signal_patches = [
            patch("app.signals._handle_media_cache_change"),
            patch("app.signals._sync_owner_smart_lists_for_items"),
            patch("app.signals._schedule_credits_backfill_if_needed"),
            patch("app.models.Item.fetch_releases"),
        ]
        for patcher in self.signal_patches:
            patcher.start()

        self.user = get_user_model().objects.create_user(
            username="discover-user", password="testpass"
        )
        self.item = Item.objects.create(
            media_id="100",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Tracked Movie",
            image="http://example.com/image.jpg",
        )
        with patch(
            "app.models.providers.services.get_media_metadata",
            return_value={"max_progress": 1},
        ):
            Movie.objects.create(
                item=self.item,
                user=self.user,
                status=Status.COMPLETED.value,
            )
            planning_item = Item.objects.create(
                media_id="101",
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title="Planning Movie",
                image="http://example.com/planning.jpg",
            )
            Movie.objects.create(
                item=planning_item,
                user=self.user,
                status=Status.PLANNING.value,
            )

    def tearDown(self):
        for patcher in reversed(self.signal_patches):
            patcher.stop()

    def test_get_tracked_keys_by_media_type(self):
        tracked = get_tracked_keys_by_media_type(self.user, MediaTypes.MOVIE.value)
        self.assertIn((MediaTypes.MOVIE.value, Sources.TMDB.value, "100"), tracked)
        self.assertNotIn((MediaTypes.MOVIE.value, Sources.TMDB.value, "101"), tracked)

    def test_get_tracked_keys_by_media_type_accepts_custom_statuses(self):
        tracked = get_tracked_keys_by_media_type(
            self.user,
            MediaTypes.MOVIE.value,
            statuses={
                Status.COMPLETED.value,
                Status.DROPPED.value,
                Status.PLANNING.value,
            },
        )
        self.assertIn((MediaTypes.MOVIE.value, Sources.TMDB.value, "100"), tracked)
        self.assertIn((MediaTypes.MOVIE.value, Sources.TMDB.value, "101"), tracked)

    def test_identity_lookups_read_columns_without_building_models(self):
        """A rebuild runs these several times over the whole library."""
        DiscoverFeedback.objects.create(
            user=self.user,
            item=self.item,
            feedback_type=DiscoverFeedbackType.NOT_INTERESTED.value,
        )
        CollectionEntry.objects.create(user=self.user, item=self.item)
        built = []

        def count(sender, **_kwargs):
            built.append(sender.__name__)

        post_init.connect(count)
        self.addCleanup(post_init.disconnect, count)

        tracked = get_tracked_keys_by_media_type(self.user, MediaTypes.MOVIE.value)
        feedback = get_feedback_keys_by_media_type(self.user, MediaTypes.MOVIE.value)
        owned = get_owned_keys_by_media_type(self.user, MediaTypes.MOVIE.value)

        identity = {(MediaTypes.MOVIE.value, Sources.TMDB.value, "100")}
        self.assertEqual((tracked, feedback, owned), (identity, identity, identity))
        self.assertEqual(built, [])

    def test_exclude_tracked_items_filters_candidates(self):
        tracked = get_tracked_keys_by_media_type(self.user, MediaTypes.MOVIE.value)
        candidates = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source=Sources.TMDB.value,
                media_id="100",
                title="Tracked Movie",
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source=Sources.TMDB.value,
                media_id="101",
                title="New Movie",
            ),
        ]

        filtered = exclude_tracked_items(candidates, tracked)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].media_id, "101")

    def test_get_feedback_keys_by_media_type_returns_hidden_items(self):
        feedback_item = Item.objects.create(
            media_id="202",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Dismissed Movie",
            image="http://example.com/dismissed.jpg",
        )
        DiscoverFeedback.objects.create(
            user=self.user,
            item=feedback_item,
            feedback_type=DiscoverFeedbackType.NOT_INTERESTED.value,
        )

        feedback_keys = get_feedback_keys_by_media_type(
            self.user,
            MediaTypes.MOVIE.value,
        )

        self.assertEqual(
            feedback_keys,
            {(MediaTypes.MOVIE.value, Sources.TMDB.value, "202")},
        )

    def test_dedupe_candidates_respects_seen_set(self):
        seen = {(MediaTypes.MOVIE.value, Sources.TMDB.value, "100")}
        candidates = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source=Sources.TMDB.value,
                media_id="100",
                title="Duplicate",
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source=Sources.TMDB.value,
                media_id="200",
                title="Unique",
            ),
        ]

        deduped = dedupe_candidates(candidates, seen_identities=seen)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0].media_id, "200")

    def test_get_owned_keys_by_media_type_returns_collected_items(self):
        owned_item = Item.objects.create(
            media_id="303",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Owned Movie",
            image="http://example.com/owned.jpg",
        )
        CollectionEntry.objects.create(user=self.user, item=owned_item)

        owned_keys = get_owned_keys_by_media_type(self.user, MediaTypes.MOVIE.value)

        self.assertEqual(
            owned_keys,
            {(MediaTypes.MOVIE.value, Sources.TMDB.value, "303")},
        )

    def test_exclude_owned_items_filters_candidates(self):
        owned_keys = {(MediaTypes.MOVIE.value, Sources.TMDB.value, "303")}
        candidates = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source=Sources.TMDB.value,
                media_id="303",
                title="Owned Movie",
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source=Sources.TMDB.value,
                media_id="304",
                title="Unowned Movie",
            ),
        ]

        filtered = exclude_owned_items(candidates, owned_keys)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].media_id, "304")

    def test_exclude_owned_items_no_owned_keys_returns_all(self):
        candidates = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source=Sources.TMDB.value,
                media_id="305",
                title="Any Movie",
            ),
        ]

        filtered = exclude_owned_items(candidates, set())
        self.assertEqual(filtered, candidates)
