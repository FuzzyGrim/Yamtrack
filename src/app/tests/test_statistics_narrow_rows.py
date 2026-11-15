"""Statistics range payloads carry narrow item rows.

A range payload keeps model rows for its cards and is cached for days. The
cards read a few item columns (title, image, ids), never the wide ones such as
``watch_providers`` (~146 KiB a title from TMDB). These tests pin that the
wide columns stay out, that nothing reads them back late, and that the page
and API render exactly what they rendered from whole rows, including from
whole-row entries cached before the change.
"""

import json
import pickle
import random
import re
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app import statistics_cache, statistics_sync
from app.history_cache_utils import HISTORY_UNREAD_ITEM_FIELDS
from app.models import (
    TV,
    Anime,
    Episode,
    Item,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Status,
)

ALL_RANGES = (*statistics_sync.HOT_RANGES, *statistics_sync.HEAVY_RANGES)
WIDE_MOVIES = 20
# 50 KiB of provider JSON on every item, like a real TMDB title (smaller).
WIDE_PROVIDERS = {
    "US": {
        "flatrate": [
            {"provider_name": f"Provider {index}", "logo_path": "/" + "x" * 60}
            for index in range(700)
        ]
    }
}
# Whole rows put WIDE_MOVIES x 50 KiB in the range several times over; narrow
# rows leave a few KiB per card.
PAYLOAD_CEILING = 256 * 1024
WIDE_COLUMN_SELECT = re.compile(
    r'"app_item"\."(' + "|".join(HISTORY_UNREAD_ITEM_FIELDS) + r')"'
)


@contextmanager
def whole_rows():
    """Patch the Statistics loaders back to whole item rows (the old shape)."""
    no_defer = lambda *_paths: ()  # noqa: E731
    with (
        patch("app.statistics_aggregator.history_deferred_item_fields", no_defer),
        patch("app.statistics_sync.history_deferred_item_fields", no_defer),
        patch("app.stats_score.history_deferred_item_fields", no_defer),
        patch("app.stats_score.HISTORY_UNREAD_ITEM_FIELDS", ()),
    ):
        yield


class NarrowRowsTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="narrow-rows", password="password"
        )
        now = timezone.now()
        wide = {"watch_providers": WIDE_PROVIDERS, "synopsis": "s" * 2000}
        movies = Item.objects.bulk_create(
            [
                Item(
                    media_id=f"narrow-movie-{index}",
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.MOVIE.value,
                    title=f"Narrow Movie {index}",
                    image=f"https://img.test/movie-{index}.jpg",
                    runtime_minutes=100,
                    **wide,
                )
                for index in range(WIDE_MOVIES)
            ]
        )
        Movie.objects.bulk_create(
            [
                Movie(
                    user=cls.user,
                    item=item,
                    status=Status.COMPLETED.value,
                    score=1 + index % 10,
                    start_date=now - timedelta(days=index + 1, hours=2),
                    end_date=now - timedelta(days=index + 1),
                )
                for index, item in enumerate(movies)
            ]
        )

        show_item = Item.objects.create(
            media_id="narrow-show",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Narrow Show",
            image="https://img.test/show.jpg",
            **wide,
        )
        show = TV.objects.create(
            user=cls.user, item=show_item, status=Status.COMPLETED.value, score=9
        )
        season_item = Item.objects.create(
            media_id="narrow-show",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=1,
            title="Narrow Show Season 1",
            **wide,
        )
        season = Season.objects.create(
            user=cls.user,
            item=season_item,
            related_tv=show,
            status=Status.COMPLETED.value,
            score=8,
        )
        for number in range(1, 5):
            Episode.objects.create(
                item=Item.objects.create(
                    media_id="narrow-show",
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.EPISODE.value,
                    season_number=1,
                    episode_number=number,
                    title=f"Narrow Episode {number}",
                    runtime_minutes=45,
                    **wide,
                ),
                related_season=season,
                end_date=now - timedelta(days=number, hours=5),
            )

        # A manual anime: its progress total comes from its custom details,
        # so it is the one row loaded whole while the payload is built.
        Anime.objects.create(
            user=cls.user,
            item=Item.objects.create(
                media_id="narrow-anime",
                source=Sources.MANUAL.value,
                media_type=MediaTypes.ANIME.value,
                title="Narrow Manual Anime",
                manual_metadata={"details": {"episodes": 12}},
                studios=["Studio Narrow"],
            ),
            status=Status.IN_PROGRESS.value,
            progress=3,
            score=7,
            start_date=now - timedelta(days=6),
            end_date=now - timedelta(days=2),
        )

    def setUp(self):
        cache.clear()
        self.client.force_login(self.user)

    def tearDown(self):
        # Cached ranges are keyed by user id, which the next test may reuse.
        cache.clear()

    def publish_all(self):
        random.seed("narrow-rows")
        outcome = statistics_sync.run_sync(self.user.id, only_ranges=ALL_RANGES)
        self.assertEqual(outcome["status"], "done")

    def entry(self, range_name):
        return cache.get(statistics_cache._cache_key(self.user.id, range_name))

    def drop_cached_entries(self):
        """Forget the cached copies; the durable snapshots stay."""
        for name in ALL_RANGES:
            cache.delete(statistics_cache._cache_key(self.user.id, name))

    def render(self, range_name):
        """Return (React props, API body) for one range, and the queries run."""
        self.user.statistics_default_range = range_name
        self.user.save(update_fields=["statistics_default_range"])
        with recorded_queries() as queries:
            page = self.client.get(reverse("statistics"), {"format": "json"})
            html = self.client.get(reverse("statistics"))
            api = self.client.get("/api/v1/statistics/overview/")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(html.status_code, 200)
        self.assertEqual(api.status_code, 200)
        rendered = {
            "props": _strip_volatile(page.json()),
            "html": _strip_csrf(html.content.decode()),
            "api": _strip_volatile(api.json()),
        }
        return rendered, queries

    def render_all(self):
        return {name: self.render(name) for name in ALL_RANGES}


def _strip_volatile(value):
    if isinstance(value, dict):
        return {
            key: _strip_volatile(item)
            for key, item in value.items()
            if key not in {"built_at", "generated_at", "last_updated", "updated_at"}
        }
    if isinstance(value, list):
        return [_strip_volatile(item) for item in value]
    return value


def _strip_csrf(html):
    html = re.sub(r'(csrfmiddlewaretoken"? value=|csrf_token["\']?:\s*)"[^"]*"', "", html)
    # Per-request values: CSRF tokens inside page JSON, and the random element
    # id components get on every render.
    html = re.sub(r"""(["'])[A-Za-z0-9]{40,}\1""", '"token"', html)
    # When the range was built ("Updated 0 minutes ago", with the time).
    html = re.sub(r'title="\w+\.? \d{1,2}, \d{4}, [^"]*"', 'title="built"', html)
    html = re.sub(r"Updated [^<]*", "Updated", html)
    return re.sub(r"-[0-9a-f]{8}\b", "-id", html)


@contextmanager
def recorded_queries():
    """Record every query's SQL (CaptureQueriesContext stops at 9,000)."""
    queries = []

    def record(execute, sql, params, many, context):
        queries.append(sql)
        return execute(sql, params, many, context)

    with connection.execute_wrapper(record):
        yield queries


def late_column_loads(queries):
    """Queries that fetch a deferred item column on its own.

    Reading a deferred column issues ``SELECT id, <column> FROM app_item``.
    A lazy load of a whole related item selects ``title`` too; that is not a
    deferred column coming back, so it is not counted here.
    """
    return [
        sql
        for sql in queries
        if WIDE_COLUMN_SELECT.search(sql) and '"app_item"."title"' not in sql
    ]


class NarrowRowsTests(NarrowRowsTestCase):
    maxDiff = None

    def test_last_30_days_payload_stays_under_the_ceiling(self):
        with whole_rows():
            self.publish_all()
        whole = len(pickle.dumps(self.entry("Last 30 Days")))
        cache.clear()
        self.publish_all()
        narrow = len(pickle.dumps(self.entry("Last 30 Days")))

        # The old shape is far over the ceiling, so the bound is meaningful.
        self.assertGreater(whole, PAYLOAD_CEILING)
        self.assertLess(narrow, PAYLOAD_CEILING)

    def test_building_never_loads_a_wide_item_column(self):
        with recorded_queries() as queries:
            self.publish_all()
        self.assertTrue(queries)
        self.assertEqual(late_column_loads(queries), [])

    def test_rendering_never_loads_a_wide_item_column(self):
        self.publish_all()
        for source in ("cached", "hydrated"):
            if source == "hydrated":
                self.drop_cached_entries()
            for name, (_rendered, queries) in self.render_all().items():
                with self.subTest(source=source, range=name):
                    self.assertEqual(late_column_loads(queries), [])

    def test_narrow_rows_render_exactly_what_whole_rows_rendered(self):
        with whole_rows():
            self.publish_all()
            before = {name: rendered for name, (rendered, _q) in self.render_all().items()}

        # After an upgrade: entries cached with whole rows still render the same.
        upgraded = self.render_all()
        # A cache miss hydrates the durable snapshot with narrow rows.
        self.drop_cached_entries()
        hydrated = self.render_all()
        # A fresh build stores narrow rows.
        cache.clear()
        self.publish_all()
        fresh = self.render_all()

        for label, after in (
            ("old cached entry", upgraded),
            ("hydrated snapshot", hydrated),
            ("fresh build", fresh),
        ):
            for name in ALL_RANGES:
                with self.subTest(source=label, range=name):
                    rendered, _queries = after[name]
                    self.assertEqual(
                        json.dumps(rendered["props"], sort_keys=True, default=str),
                        json.dumps(before[name]["props"], sort_keys=True, default=str),
                    )
                    self.assertEqual(rendered["api"], before[name]["api"])
                    self.assertEqual(
                        rendered["html"].splitlines(),
                        before[name]["html"].splitlines(),
                    )

