"""History must not hydrate the item columns it never reads.

A filtered `/api/v1/history/` request over 6,454 episode plays grew a web
worker by ~790 MiB to return a 56 KiB response. Almost all of it was
`Item.watch_providers` -- TMDB's availability for every region it knows --
decoded three times per play, once for the episode, its season and its show.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from app import history_cache
from app.history_cache_utils import HISTORY_UNREAD_ITEM_FIELDS
from app.models import (
    TV,
    Episode,
    Item,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Status,
)

# Big enough that a per-row reload of a deferred column is unmistakable in the
# query count, small enough to stay a fast test.
EPISODE_COUNT = 12
PROVIDERS = {f"REGION{index}": [{"provider_id": index}] for index in range(139)}


class HistoryItemProjectionTests(TestCase):
    """The heavy item columns must be absent from the query, and stay unread."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="history-projection",
            password="12345",
        )
        tv_item = Item.objects.create(
            media_id="projection-show",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Projection Show",
            image="http://example.com/show.jpg",
            genres=["Drama"],
            watch_providers=PROVIDERS,
            synopsis="x" * 2000,
        )
        tv = TV.objects.create(
            item=tv_item, user=cls.user, status=Status.IN_PROGRESS.value,
        )
        season_item = Item.objects.create(
            media_id="projection-show",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=1,
            title="Projection Show Season 1",
            watch_providers=PROVIDERS,
        )
        season = Season.objects.create(
            item=season_item,
            user=cls.user,
            related_tv=tv,
            status=Status.IN_PROGRESS.value,
        )
        now = timezone.now()
        for number in range(1, EPISODE_COUNT + 1):
            episode_item = Item.objects.create(
                media_id="projection-show",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                season_number=1,
                episode_number=number,
                title=f"Projection Episode {number}",
                runtime_minutes=42,
                watch_providers=PROVIDERS,
            )
            Episode.objects.create(
                item=episode_item,
                related_season=season,
                end_date=now - timezone.timedelta(days=number),
            )
        movie_item = Item.objects.create(
            media_id="projection-movie",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Projection Movie",
            runtime_minutes=100,
            watch_providers=PROVIDERS,
        )
        Movie.objects.create(
            item=movie_item,
            user=cls.user,
            status=Status.COMPLETED.value,
            end_date=now,
        )

    def setUp(self):
        """History reads through a cache; a warm one would hide the queries."""
        cache.clear()

    def test_the_heavy_item_columns_are_never_selected(self):
        """No history query may name a column the builders do not read.

        This covers the deferred-read case too: Django loads a deferred column
        with a query that names it, so a reader anywhere would show up here.
        """
        with CaptureQueriesContext(connection) as captured:
            history_cache.build_history_days(self.user)

        loaded = {
            field
            for field in HISTORY_UNREAD_ITEM_FIELDS
            for query in captured.captured_queries
            if f'"{field}"' in query["sql"]
        }
        self.assertEqual(
            loaded,
            set(),
            f"history loaded item columns it never reads: {sorted(loaded)}",
        )

    def test_the_query_count_does_not_grow_with_the_history(self):
        """Doubling the plays must not add queries; that is the N+1 tripwire."""
        with CaptureQueriesContext(connection) as before:
            history_cache.build_history_days(self.user)

        season = Season.objects.get(user=self.user)
        now = timezone.now()
        for number in range(EPISODE_COUNT + 1, EPISODE_COUNT * 2 + 1):
            episode_item = Item.objects.create(
                media_id="projection-show",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                season_number=1,
                episode_number=number,
                title=f"Projection Episode {number}",
                runtime_minutes=42,
                watch_providers=PROVIDERS,
            )
            Episode.objects.create(
                item=episode_item,
                related_season=season,
                end_date=now - timezone.timedelta(days=number),
            )

        with CaptureQueriesContext(connection) as after:
            history_cache.build_history_days(self.user)

        self.assertEqual(
            len(after.captured_queries),
            len(before.captured_queries),
        )

    def test_the_cards_still_carry_what_they_render(self):
        """Projection is invisible to the response: same fields, same values."""
        days = history_cache.build_history_days(self.user)
        entries = [entry for day in days for entry in day["entries"]]
        episodes = [
            entry for entry in entries
            if entry["media_type"] == MediaTypes.EPISODE.value
        ]

        self.assertEqual(len(episodes), EPISODE_COUNT)
        first = episodes[0]
        self.assertEqual(first["title"], "Projection Episode 1")
        self.assertEqual(first["poster"], "http://example.com/show.jpg")
        self.assertEqual(first["runtime_minutes"], 42)
        self.assertEqual(first["episode_label"], "S01E01")
        self.assertEqual(first["genres"], ["Drama"])
        self.assertEqual(first["item"]["media_id"], "projection-show")
