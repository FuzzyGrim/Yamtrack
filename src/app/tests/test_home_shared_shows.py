"""Home's TV shelves share one set of show records (``SharedShows``).

Every TV shelf on Home ranks the same shows, each in its own request. The
first computes each show's facts and released progress; the others read them
from the cache. These tests hold the shared reading to the fresh one: the same
order for every shelf and direction, cold and warm, and a cache that holds
values only and is cleared by a tracker write.
"""

import pickle
from collections import Counter
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection, models
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from app import cache_utils
from app.models import TV, Episode, Item, MediaTypes, Season, Status
from app.tests.test_tv_show_facts import build_library
from users.home_screen import (
    HOME_QUERY_DEFAULT_FILTERS,
    UP_NEXT_SORT,
    _library_row_executor,
    _normalized_filter_payload,
    resolve_home_row_direction,
)
from users.models import HomeScreenRow, HomeSortChoices

SHELVES = [
    (UP_NEXT_SORT, {}),
    (HomeSortChoices.UPCOMING, {}),
    (HomeSortChoices.RECENT, {}),
    (HomeSortChoices.COMPLETION, {}),
    (HomeSortChoices.EPISODES_LEFT, {}),
    (HomeSortChoices.RECENT, {"progress": "caught_up"}),
    (HomeSortChoices.RECENT, {"progress": "not_caught_up"}),
    (HomeSortChoices.COMPLETION, {"status": [Status.COMPLETED.value, Status.DROPPED.value]}),
    (HomeSortChoices.EPISODES_LEFT, {"status": []}),
]


def _executor(user, sort_by, filters, *, share):
    row = HomeScreenRow(
        user=user,
        media_type=MediaTypes.TV.value,
        sort_by=sort_by,
        direction=resolve_home_row_direction(sort_by),
        filters={**HOME_QUERY_DEFAULT_FILTERS, **filters},
    )
    executor = _library_row_executor(
        user,
        row,
        _normalized_filter_payload(row.filters, row.media_type),
        seed=0,
    )
    executor.share_shows = share
    return executor


class SharedShowsTests(TestCase):
    """Shared show records rank every TV shelf exactly as fresh ones do."""

    @classmethod
    def setUpTestData(cls):
        """Build a library that reaches every branch of the TV facts."""
        cls.user = get_user_model().objects.create_user(username="shared", password="x")
        cls.other = get_user_model().objects.create_user(username="other", password="x")
        build_library(cls.user)

    def setUp(self):
        cache.clear()

    def _orders(self, *, share):
        return {
            (sort_by, str(filters)): _executor(self.user, sort_by, filters, share=share)
            .ranked_ids_both_directions()
            for sort_by, filters in SHELVES
        }

    def test_every_shelf_ranks_the_same_cold_and_warm(self):
        """Both directions of every shelf equal the fresh order, whoever computed the records."""
        fresh = self._orders(share=False)
        self.assertIsNone(cache.get(cache_utils.build_home_tv_shows_cache_key(self.user.id)))
        # Cold: the first shelf computes the records, the rest read them.
        self.assertEqual(self._orders(share=True), fresh)
        self.assertIsNotNone(cache.get(cache_utils.build_home_tv_shows_cache_key(self.user.id)))
        # Warm: every shelf reads records it did not compute.
        self.assertEqual(self._orders(share=True), fresh)
        # The library must actually separate the shelves.
        self.assertGreater(len({tuple(order["asc"]) for order in fresh.values()}), 4)
        self.assertTrue(all(order["asc"] for order in fresh.values()))

    def test_a_shelf_that_needs_released_progress_completes_records_without_it(self):
        """A record stored without released progress gains it when a shelf needs it."""
        recent = _executor(self.user, HomeSortChoices.RECENT, {}, share=False).ranked_ids_both_directions()
        completion = _executor(self.user, HomeSortChoices.COMPLETION, {}, share=False).ranked_ids_both_directions()
        self.assertEqual(_executor(self.user, HomeSortChoices.RECENT, {}, share=True).ranked_ids_both_directions(), recent)
        self.assertEqual(
            _executor(self.user, HomeSortChoices.COMPLETION, {}, share=True).ranked_ids_both_directions(),
            completion,
        )

    def test_a_warm_shelf_reads_no_season_or_play(self):
        """Once one shelf has run, the next builds no tracker models and asks for less."""
        with CaptureQueriesContext(connection) as cold:
            _executor(self.user, HomeSortChoices.COMPLETION, {}, share=True).ranked_ids()
        built = Counter()
        original = models.Model.from_db.__func__

        @classmethod
        def from_db(cls, db, field_names, values):
            built[cls.__name__] += 1
            return original(cls, db, field_names, values)

        with mock.patch.object(models.Model, "from_db", from_db), CaptureQueriesContext(connection) as warm:
            _executor(self.user, HomeSortChoices.EPISODES_LEFT, {}, share=True).ranked_ids()
        self.assertEqual(built[TV.__name__] + built[Season.__name__] + built[Episode.__name__], 0)
        self.assertLess(len(warm), len(cold))

    def test_the_cache_holds_values_only(self):
        """No model instance is stored: the records pickle without Django models."""
        _executor(self.user, HomeSortChoices.COMPLETION, {}, share=True).ranked_ids()
        records = cache.get(cache_utils.build_home_tv_shows_cache_key(self.user.id))
        self.assertTrue(records)

        def walk(value):
            if isinstance(value, models.Model):
                self.fail(f"model instance cached: {value!r}")
            if isinstance(value, dict):
                for key, inner in value.items():
                    walk(key)
                    walk(inner)
            elif isinstance(value, (list, tuple, set)):
                for inner in value:
                    walk(inner)

        walk(records)
        self.assertTrue(pickle.loads(pickle.dumps(records)))

    def test_records_are_per_user(self):
        """Another user's shelves never see these records."""
        _executor(self.user, HomeSortChoices.COMPLETION, {}, share=True).ranked_ids()
        self.assertNotEqual(
            cache_utils.build_home_tv_shows_cache_key(self.user.id),
            cache_utils.build_home_tv_shows_cache_key(self.other.id),
        )
        self.assertIsNone(cache.get(cache_utils.build_home_tv_shows_cache_key(self.other.id)))
        self.assertEqual(_executor(self.other, HomeSortChoices.COMPLETION, {}, share=True).ranked_ids(), [])

    def test_a_tracker_write_clears_the_records(self):
        """Watching an episode clears the shared records with the rest of Home."""
        _executor(self.user, HomeSortChoices.COMPLETION, {}, share=True).ranked_ids()
        key = cache_utils.build_home_tv_shows_cache_key(self.user.id)
        self.assertIsNotNone(cache.get(key))
        season = Season.objects.filter(user=self.user, status=Status.IN_PROGRESS.value).select_related("item").first()
        episode_item = Item.objects.filter(
            media_type=MediaTypes.EPISODE.value,
            media_id=season.item.media_id,
            source=season.item.source,
            season_number=season.item.season_number,
        ).first()
        # Home caches are cleared once the write commits.
        with self.captureOnCommitCallbacks(execute=True):
            Episode.objects.create(item=episode_item, related_season=season, status=Status.COMPLETED.value)
        self.assertTrue(cache.get(key) is None, "shared show records survived a tracker write")

    def test_the_records_expire_with_air_date_dependent_orders(self):
        """The lifetime is Home's bound for orders that move with air dates."""
        with mock.patch("django.core.cache.cache.set", wraps=cache.set) as setter:
            _executor(self.user, HomeSortChoices.COMPLETION, {}, share=True).ranked_ids()
        key = cache_utils.build_home_tv_shows_cache_key(self.user.id)
        calls = [call for call in setter.call_args_list if call.args[0] == key]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].args[2], cache_utils.HOME_ROW_RELEASED_CACHE_TTL)
