"""Query and model budget for one Discover tab rebuild, per media type.

A rebuild is personalised, so its cost grows with the library. These pins run
every tab on the same synthetic mixed library with provider responses fixed,
and fail when a change adds queries or model objects to a rebuild. Tighten a
pin in the commit that earns it.
"""

import hashlib
from collections import Counter
from contextlib import ExitStack
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.db.models.signals import post_init
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from app.benchmark_data import build_dataset
from app.discover import provider_candidates, service, tab_cache
from app.discover.adapters import TMDB_ADAPTER, TRAKT_ADAPTER
from app.discover.registry import ALL_MEDIA_KEY
from app.discover.schemas import CandidateItem
from app.models import DiscoverRowCache, DiscoverTasteProfile, Item, MediaTypes

LIBRARY_SIZE = 600
CANDIDATES_PER_FETCH = 100

# (queries, model objects built) for one cold rebuild of each tab.
REBUILD_BUDGETS = {
    # The base revision built 538 objects for movie and 596 for all: every
    # "already tracked" lookup turned each library row into a model.
    MediaTypes.MOVIE.value: (112, 202),
    MediaTypes.TV.value: (135, 489),
    MediaTypes.ANIME.value: (115, 77),
    MediaTypes.MUSIC.value: (76, 60),
    MediaTypes.PODCAST.value: (82, 64),
    MediaTypes.BOOK.value: (76, 62),
    MediaTypes.COMIC.value: (65, 6),
    MediaTypes.MANGA.value: (82, 70),
    MediaTypes.GAME.value: (82, 66),
    MediaTypes.BOARDGAME.value: (65, 6),
    ALL_MEDIA_KEY: (198, 380),
}

PROVIDER_FETCHERS = {
    "_mal_manga_ranking_candidates": (MediaTypes.MANGA.value, "mal"),
    "_mal_anime_ranking_candidates": (MediaTypes.ANIME.value, "mal"),
    "_igdb_games_candidates": (MediaTypes.GAME.value, "igdb"),
    "_openlibrary_trending_candidates": (MediaTypes.BOOK.value, "openlibrary"),
    "_openlibrary_coming_soon_candidates": (MediaTypes.BOOK.value, "openlibrary"),
    "_comicvine_volume_candidates": (MediaTypes.COMIC.value, "comicvine"),
    "_comicvine_coming_soon_volume_candidates": (MediaTypes.COMIC.value, "comicvine"),
    "_bgg_hot_candidates": (MediaTypes.BOARDGAME.value, "bgg"),
    "_itunes_top_podcasts_candidates": (MediaTypes.PODCAST.value, "pocketcasts"),
    "_lastfm_top_tracks_candidates": (MediaTypes.MUSIC.value, "musicbrainz"),
    "_musicbrainz_coming_soon_recording_candidates": (
        MediaTypes.MUSIC.value,
        "musicbrainz",
    ),
}


def _candidates(media_type, label, source, tracked_ids):
    """A fixed provider answer; a fifth of it already in the library."""
    seed = int(hashlib.sha256(label.encode()).hexdigest()[:6], 16) % 5000
    tracked = [
        CandidateItem(
            media_type=media_type,
            source="manual",
            media_id=media_id,
            title=f"Tracked {media_id}",
            image="https://img.example/tracked.jpg",
            release_date="2020-01-01",
            genres=["Drama" if index % 2 else "Comedy"],
            popularity=100.0 - index,
            rating=7.5,
            rating_count=1000,
        )
        for index, media_id in enumerate(tracked_ids[: CANDIDATES_PER_FETCH // 5])
    ]
    fresh = [
        CandidateItem(
            media_type=media_type,
            source=source,
            media_id=str(1_000_000 + seed * 100 + index),
            title=f"{label} {index}",
            image="https://img.example/new.jpg",
            release_date=f"20{10 + index % 15:02d}-0{1 + index % 9}-15",
            genres=[("Drama", "Comedy", "Thriller", "Action")[index % 4]],
            popularity=float(500 - index),
            rating=6.0 + (index % 30) / 10,
            rating_count=100 + index,
        )
        for index in range(CANDIDATES_PER_FETCH - len(tracked))
    ]
    return tracked + fresh


class DiscoverRebuildBudgetTests(TestCase):
    """One cold rebuild of each tab stays inside its query and model budget."""

    @classmethod
    def setUpTestData(cls):
        build_dataset(LIBRARY_SIZE, seed=7)
        cls.user = get_user_model().objects.get(username="bench-user-1")
        cls.tracked_ids = {
            media_type: list(
                Item.objects.filter(media_type=media_type, source="manual")
                .order_by("pk")
                .values_list("media_id", flat=True)[:40],
            )
            for media_type in REBUILD_BUDGETS
        }

    def setUp(self):
        cache.clear()
        stack = ExitStack()
        self.addCleanup(stack.close)
        for method in (
            "trending",
            "top_rated",
            "upcoming",
            "current_cycle",
            "related",
            "genre_discovery",
        ):
            stack.enter_context(
                patch.object(TMDB_ADAPTER, method, side_effect=self._tmdb(method)),
            )
        for method, default_type in (
            ("movie_watched_weekly", MediaTypes.MOVIE.value),
            ("movie_popular", MediaTypes.MOVIE.value),
            ("movie_anticipated", MediaTypes.MOVIE.value),
            ("show_watched_weekly", MediaTypes.TV.value),
            ("show_popular", MediaTypes.TV.value),
            ("show_anticipated", MediaTypes.TV.value),
        ):
            stack.enter_context(
                patch.object(
                    TRAKT_ADAPTER,
                    method,
                    side_effect=self._trakt(method, default_type),
                ),
            )
        for name, (media_type, source) in PROVIDER_FETCHERS.items():
            stack.enter_context(
                patch.object(
                    provider_candidates,
                    name,
                    side_effect=self._fetcher(name, media_type, source),
                ),
            )
        stack.enter_context(
            patch.object(
                service,
                "_mal_anime_fallback_candidates",
                side_effect=self._fetcher(
                    "mal_fallback", MediaTypes.ANIME.value, "mal"
                ),
            ),
        )
        # Anything that reaches a provider unmocked fails the test.
        for target in (
            "app.providers.services.api_request",
            "app.providers.services.get_media_metadata",
        ):
            stack.enter_context(patch(target, side_effect=AssertionError(target)))

    def tearDown(self):
        cache.clear()

    def _tmdb(self, method):
        def fetch(media_type, *args, **_kwargs):
            return _candidates(
                media_type,
                f"tmdb.{method}.{args}",
                "tmdb",
                self.tracked_ids.get(media_type, []),
            )

        return fetch

    def _trakt(self, method, default_type):
        def fetch(*_args, media_type=None, page=None, **_kwargs):
            media_type = media_type or default_type
            return _candidates(
                media_type,
                f"trakt.{method}.{page}",
                "tmdb",
                self.tracked_ids.get(media_type, []),
            )

        return fetch

    def _fetcher(self, name, media_type, source):
        def fetch(*_args, row_key=None, **kwargs):
            kwargs.pop("limit", None)
            return _candidates(
                media_type,
                f"{name}.{row_key}.{sorted(kwargs.items())}",
                source,
                self.tracked_ids.get(media_type, []),
            )

        return fetch

    def _rebuild(self, media_type):
        built = Counter()

        def count(sender, **_kwargs):
            built[sender.__name__] += 1

        post_init.connect(count)
        try:
            with CaptureQueriesContext(connection) as queries:
                rows = tab_cache.refresh_tab_cache(self.user, media_type)
        finally:
            post_init.disconnect(count)
        return rows, len(queries.captured_queries), built

    def test_each_tab_rebuild_stays_inside_its_budget(self):
        for media_type, (max_queries, max_built) in REBUILD_BUDGETS.items():
            with self.subTest(media_type=media_type):
                cache.clear()
                DiscoverRowCache.objects.all().delete()
                DiscoverTasteProfile.objects.all().delete()
                rows, queries, built = self._rebuild(media_type)

                self.assertTrue(rows)
                self.assertLessEqual(queries, max_queries, f"{queries} queries")
                self.assertLessEqual(sum(built.values()), max_built, dict(built))
