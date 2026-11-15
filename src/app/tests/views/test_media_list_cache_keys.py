"""Which media-list requests share a cache entry.

The keys are built from the parsed filters (``MediaListFilters.cache_token``)
plus a few explicit view-state values. These tests pin the observable contract
rather than the key strings: for a matrix of real page requests, which ones
read the same list, filter-menu and time-left entries. The partitions below
were recorded against the previous hand-written builders, so a request that
used to share an entry still does, and one that did not still does not.
"""

import dataclasses
from collections import defaultdict
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app import cache_utils, media_list_views
from app.media_list_filters import MediaListFilters
from app.models import TV, Item, MediaTypes, Movie, Sources, Status

MAIN = f"{cache_utils.MEDIA_LIST_CACHE_PREFIX}_"
FILTERS = f"{cache_utils.MEDIA_LIST_FILTER_CACHE_PREFIX}_"
TIME_LEFT = f"{cache_utils.TIME_LEFT_CACHE_PREFIX}_"
DEFAULT_TOKEN = "f0c7e6f70e1ee6d6c1770a92"


class _RecordingCache:
    """Delegate to the real cache and remember which view keys were read."""

    def __init__(self):
        self.reads = []

    def get(self, key, *args, **kwargs):
        self.reads.append(key)
        return cache.get(key, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(cache, name)


MOVIE_BASE = {"sort": "title", "direction": "asc", "status": "Completed"}
TV_BASE = {"sort": "time_left", "direction": "asc", "status": "In progress"}

# label -> (media type, query, user changes applied before the request)
MOVIE_REQUESTS = {
    "base": {},
    "base_again": {},
    "page_2": {"page": "2"},
    "layout_table": {"layout": "table"},
    "sort_release": {"sort": "release_date"},
    "direction_desc": {"direction": "desc"},
    "status_two": {"status": ["Completed", "Planning"]},
    "status_two_reordered": {"status": ["Planning", "Completed"]},
    "status_no_status": {"status": ["Completed", "no_status"]},
    "search": {"search": "alpha"},
    "search_spaced": {"search": " alpha"},
    "initial_a": {"initial": "A"},
    "initial_b": {"initial": "B"},
    "rating": {"rating": "rated"},
    "rating_min": {"rating_min": "5"},
    "rating_max": {"rating_max": "8"},
    "collection": {"collection": "collected"},
    "progress_masked": {"progress": "caught_up"},
    "genre": {"genre": "Drama"},
    "implied_genre_masked": {"implied_genre": "Rock"},
    "year": {"year": "2020"},
    "completed_from": {"completed_date_from": "2024-01-01"},
    "completed_to": {"completed_date_to": "2024-12-31"},
    "completed_within": {"completed_date_within": "7"},
    "release": {"release": "released"},
    "release_from": {"release_date_from": "2020-01-01"},
    "added_from": {"date_added_from": "2020-01-01"},
    "source": {"source": "tmdb"},
    "media_status": {"media_status": "Released"},
    "language": {"language": "en"},
    "country": {"country": "US"},
    "platform_two": {"platform": ["PC", "Switch"]},
    "platform_two_reordered": {"platform": ["Switch", "PC"]},
    "platform_mode": {"platform": ["PC"], "platform_mode": "and"},
    "origin": {"origin": "JP"},
    "format": {"format": "Blu-ray"},
    "author": {"author": "Someone"},
    "provider": {"provider": "Netflix"},
    "tag_two": {"tag": ["a", "b"]},
    "tag_two_reordered": {"tag": ["b", "a"]},
    "tag_mode": {"tag": ["a"], "tag_mode": "and"},
}
MOVIE_USER_REQUESTS = {
    # Status comes from the saved preference when the query leaves it out.
    "status_saved": ({"status": None}, {}),
    "title_display_no_initial": ({}, {"title_display_preference": "original"}),
    "title_display_initial_a": (
        {"initial": "A"},
        {"title_display_preference": "original"},
    ),
    "pinned_providers": ({}, {"pinned_watch_providers": ["Netflix", "Hulu"]}),
    "pinned_providers_reordered": (
        {},
        {"pinned_watch_providers": ["Hulu", "Netflix"]},
    ),
    "separate_entries": ({}, {"movie_show_each_play": True}),
    "anime_mode_movie": ({}, {"anime_library_mode": "tv"}),
}
TV_REQUESTS = {
    "tv_base": {},
    "tv_base_again": {},
    "tv_direction_desc": {"direction": "desc"},
    "tv_status_two": {"status": ["In progress", "Completed"]},
    "tv_status_two_reordered": {"status": ["Completed", "In progress"]},
    "tv_search": {"search": "show"},
    "tv_initial_a": {"initial": "A"},
    "tv_rating": {"rating": "rated"},
    "tv_rating_min": {"rating_min": "5"},
    "tv_collection": {"collection": "collected"},
    "tv_progress": {"progress": "caught_up"},
    "tv_genre": {"genre": "Drama"},
    "tv_implied_genre_masked": {"implied_genre": "Rock"},
    "tv_year": {"year": "2020"},
    "tv_completed_from": {"completed_date_from": "2024-01-01"},
    "tv_release": {"release": "released"},
    "tv_release_from": {"release_date_from": "2020-01-01"},
    "tv_source": {"source": "tmdb"},
    "tv_media_status": {"media_status": "Ended"},
    "tv_language": {"language": "en"},
    "tv_country": {"country": "US"},
    "tv_platform": {"platform": ["PC"]},
    "tv_platform_mode": {"platform": ["PC"], "platform_mode": "and"},
    "tv_origin": {"origin": "JP"},
    "tv_format": {"format": "Blu-ray"},
    "tv_author": {"author": "Someone"},
    "tv_provider": {"provider": "Netflix"},
    "tv_tag": {"tag": ["a"]},
    "tv_tag_mode": {"tag": ["a"], "tag_mode": "and"},
}
TV_USER_REQUESTS = {
    "tv_anime_mode": ({}, {"anime_library_mode": "both"}),
    "tv_title_display_initial_a": (
        {"initial": "A"},
        {"title_display_preference": "original"},
    ),
    "tv_pinned_providers": ({}, {"pinned_watch_providers": ["Netflix"]}),
}

# Requests that read the same entry, per cache. Every label not listed here
# reads an entry of its own. Recorded against the pre-token builders.
EXPECTED_SHARED = {
    "main": [
        # Masked filters, display-only state and the anime mode (TV and anime
        # only) do not change a movie list.
        [
            "anime_mode_movie",
            "base",
            "base_again",
            "implied_genre_masked",
            "layout_table",
            "page_2",
            "progress_masked",
            "status_saved",
            "title_display_no_initial",
        ],
        ["pinned_providers", "pinned_providers_reordered"],
        ["platform_two", "platform_two_reordered"],
        ["status_two", "status_two_reordered"],
        ["tag_two", "tag_two_reordered"],
    ],
    "filters": [
        # The filter menu ignores order, rating, collection, origin, the
        # rating and date ranges, and the entry-grouping mode.
        [
            "added_from",
            "anime_mode_movie",
            "base",
            "base_again",
            "collection",
            "direction_desc",
            "implied_genre_masked",
            "layout_table",
            "origin",
            "page_2",
            "progress_masked",
            "rating",
            "rating_max",
            "rating_min",
            "release_from",
            "separate_entries",
            "sort_release",
            "status_saved",
            "title_display_no_initial",
        ],
        ["pinned_providers", "pinned_providers_reordered"],
        ["platform_two", "platform_two_reordered"],
        ["status_two", "status_two_reordered"],
        ["tag_two", "tag_two_reordered"],
        [
            "tv_base",
            "tv_base_again",
            "tv_collection",
            "tv_direction_desc",
            "tv_implied_genre_masked",
            "tv_origin",
            "tv_rating",
            "tv_rating_min",
            "tv_release_from",
        ],
        ["tv_status_two", "tv_status_two_reordered"],
    ],
    "time_left": [
        # The time-left order ignores author and format (not TV filters) and
        # the anime library mode.
        [
            "tv_anime_mode",
            "tv_author",
            "tv_base",
            "tv_base_again",
            "tv_format",
            "tv_implied_genre_masked",
        ],
        ["tv_status_two", "tv_status_two_reordered"],
    ],
}
# Which requests read each cache at all.
EXPECTED_USERS = {
    "main": set(MOVIE_REQUESTS) | set(MOVIE_USER_REQUESTS),
    "filters": set(MOVIE_REQUESTS)
    | set(MOVIE_USER_REQUESTS)
    | set(TV_REQUESTS)
    | set(TV_USER_REQUESTS),
    "time_left": set(TV_REQUESTS) | set(TV_USER_REQUESTS),
}


class MediaListCacheKeyContractTests(TestCase):
    """Pin which page requests share each media-list cache entry."""

    def setUp(self):
        self.credentials = {"username": "cache-keys", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)
        now = timezone.now()
        for index, title in enumerate(("Alpha", "Bravo")):
            item = Item.objects.create(
                media_id=f"cache-key-movie-{index}",
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title=title,
                image="http://example.com/movie.jpg",
            )
            Movie.objects.create(
                item=item,
                user=self.user,
                status=Status.COMPLETED.value,
                end_date=now - timedelta(days=index + 1),
            )
            show = Item.objects.create(
                media_id=f"cache-key-show-{index}",
                source=Sources.TMDB.value,
                media_type=MediaTypes.TV.value,
                title=f"{title} Show",
                image="http://example.com/show.jpg",
            )
            TV.objects.create(
                item=show,
                user=self.user,
                status=Status.IN_PROGRESS.value,
            )
        self._defaults = {
            field: getattr(self.user, field)
            for field in (
                "title_display_preference",
                "pinned_watch_providers",
                "movie_show_each_play",
                "anime_library_mode",
            )
        }
        cache.clear()

    def _read_keys(self, media_type, base, query, user_changes):
        get_user_model().objects.filter(pk=self.user.pk).update(
            **{**self._defaults, **user_changes},
        )
        params = {**base, **query}
        params = {name: value for name, value in params.items() if value is not None}
        recorder = _RecordingCache()
        cache.clear()
        with mock.patch.object(media_list_views, "cache", recorder):
            response = self.client.get(
                reverse("medialist", args=[media_type]),
                params,
            )
        self.assertEqual(response.status_code, 200)
        keys = {"main": None, "filters": None, "time_left": None}
        for key in recorder.reads:
            if key.startswith(FILTERS):
                keys["filters"] = key
            elif key.startswith(MAIN):
                keys["main"] = key
            elif key.startswith(TIME_LEFT):
                keys["time_left"] = key
        return keys

    def _observed_keys(self):
        observed = {}
        # Save "Completed" as the movie status first, so "status_saved" reads it.
        self._read_keys(MediaTypes.MOVIE.value, MOVIE_BASE, {}, {})
        for label, query in MOVIE_REQUESTS.items():
            observed[label] = self._read_keys(
                MediaTypes.MOVIE.value, MOVIE_BASE, query, {}
            )
        for label, (query, changes) in MOVIE_USER_REQUESTS.items():
            observed[label] = self._read_keys(
                MediaTypes.MOVIE.value, MOVIE_BASE, query, changes
            )
        for label, query in TV_REQUESTS.items():
            observed[label] = self._read_keys(MediaTypes.TV.value, TV_BASE, query, {})
        for label, (query, changes) in TV_USER_REQUESTS.items():
            observed[label] = self._read_keys(
                MediaTypes.TV.value, TV_BASE, query, changes
            )
        return observed

    @staticmethod
    def _shared_groups(observed, kind):
        by_key = defaultdict(list)
        for label, keys in observed.items():
            if keys[kind] is not None:
                by_key[keys[kind]].append(label)
        return sorted(
            (sorted(labels) for labels in by_key.values() if len(labels) > 1),
        )

    def test_requests_share_cache_entries_exactly_as_before(self):
        observed = self._observed_keys()
        for kind, groups in EXPECTED_SHARED.items():
            with self.subTest(cache=kind):
                self.assertEqual(
                    {label for label, keys in observed.items() if keys[kind]},
                    EXPECTED_USERS[kind],
                )
                self.assertEqual(
                    self._shared_groups(observed, kind),
                    sorted(sorted(group) for group in groups),
                )


class MediaListCacheWarmReadTests(TestCase):
    """A warm (cached) page shows the same items as the cold one."""

    def setUp(self):
        self.credentials = {"username": "cache-warm", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)
        now = timezone.now()
        for index, title in enumerate(("Alpha", "Bravo", "Charlie")):
            item = Item.objects.create(
                media_id=f"cache-warm-movie-{index}",
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title=title,
                image="http://example.com/movie.jpg",
            )
            Movie.objects.create(
                item=item,
                user=self.user,
                status=Status.COMPLETED.value,
                score=index * 4,
                end_date=now - timedelta(days=index + 1),
            )
            show = Item.objects.create(
                media_id=f"cache-warm-show-{index}",
                source=Sources.TMDB.value,
                media_type=MediaTypes.TV.value,
                title=f"{title} Show",
                image="http://example.com/show.jpg",
            )
            TV.objects.create(item=show, user=self.user, status=Status.IN_PROGRESS.value)
        cache.clear()

    def _item_ids(self, media_type, params):
        response = self.client.get(reverse("medialist", args=[media_type]), params)
        self.assertEqual(response.status_code, 200)
        return [entry.item_id for entry in response.context["media_list"]]

    def test_warm_requests_show_the_cold_items(self):
        cases = [
            # One card per play: the order cache serves the warm read.
            (MediaTypes.MOVIE.value, {"sort": "title", "rating_min": "4"}, True),
            (MediaTypes.MOVIE.value, {"sort": "title", "initial": "B"}, True),
            # TV time-left keeps its own order cache.
            (MediaTypes.TV.value, {"sort": "time_left", "search": "Show"}, False),
        ]
        for media_type, params, separate in cases:
            with self.subTest(media_type=media_type, params=params):
                get_user_model().objects.filter(pk=self.user.pk).update(
                    movie_show_each_play=separate,
                )
                cache.clear()
                cold = self._item_ids(media_type, params)
                warm = self._item_ids(media_type, params)
                self.assertTrue(cold)
                self.assertEqual(warm, cold)


class MediaListFiltersCacheTokenTests(TestCase):
    """``MediaListFilters.cache_token`` is the one list of what filters a page."""

    # Fields that cannot change which entries match (see the dataclass).
    NOT_IN_TOKEN = frozenset(
        {
            "completed_date_within",
            "completed_date_within_unit",
            "release_date_within",
            "release_date_within_unit",
            "date_added_within",
            "date_added_within_unit",
        },
    )

    @staticmethod
    def _flipped(item):
        value = item.default
        if isinstance(value, bool):
            return not value
        if isinstance(value, tuple):
            return ("flipped",)
        if value is None:
            return "flipped"
        return f"{value}-flipped"

    def test_every_field_changes_the_token_unless_declared_otherwise(self):
        base = MediaListFilters()
        for item in dataclasses.fields(MediaListFilters):
            with self.subTest(field=item.name):
                changed = dataclasses.replace(base, **{item.name: self._flipped(item)})
                if item.name in self.NOT_IN_TOKEN:
                    self.assertEqual(changed.cache_token(), base.cache_token())
                else:
                    self.assertNotEqual(changed.cache_token(), base.cache_token())
        declared = {
            item.name
            for item in dataclasses.fields(MediaListFilters)
            if item.metadata.get("cache_token") is False
        }
        self.assertEqual(declared, self.NOT_IN_TOKEN)

    def test_multi_value_order_does_not_matter(self):
        for name in ("statuses", "platforms", "tags", "pinned_providers", "exclude"):
            with self.subTest(field=name):
                self.assertEqual(
                    MediaListFilters(**{name: ("a", "b")}).cache_token(),
                    MediaListFilters(**{name: ("b", "a")}).cache_token(),
                )

    def test_token_is_stable_across_processes(self):
        # A fixed digest: ``hash()`` would differ per process and miss on restart.
        self.assertEqual(MediaListFilters().cache_token(), DEFAULT_TOKEN)

    def test_exclude_drops_a_field_and_rejects_unknown_names(self):
        rated = MediaListFilters(rating="rated")
        self.assertEqual(
            rated.cache_token(exclude=frozenset({"rating"})),
            MediaListFilters().cache_token(exclude=frozenset({"rating"})),
        )
        with self.assertRaises(ValueError):
            rated.cache_token(exclude=frozenset({"not_a_field"}))

    def test_each_key_ignores_only_real_fields(self):
        names = {item.name for item in dataclasses.fields(MediaListFilters)}
        self.assertLessEqual(cache_utils.FILTER_MENU_IGNORED_FILTERS, names)
        self.assertLessEqual(cache_utils.TIME_LEFT_IGNORED_FILTERS, names)

    def test_alphabet_letter_carries_the_title_display(self):
        plain = MediaListFilters(media_type=MediaTypes.MOVIE.value)
        lettered = dataclasses.replace(plain, initial="A")
        for build in (
            lambda filters, title: cache_utils.build_media_list_cache_key(
                1, filters, sort="title", title_display=title
            ),
            lambda filters, title: cache_utils.build_media_list_filter_cache_key(
                1, filters, title_display=title
            ),
            lambda filters, title: cache_utils.build_time_left_cache_key(
                1, filters, title_display=title
            ),
        ):
            self.assertEqual(build(plain, "localized"), build(plain, "original"))
            self.assertNotEqual(
                build(lettered, "localized"), build(lettered, "original")
            )
            self.assertNotEqual(build(plain, "localized"), build(lettered, "localized"))
