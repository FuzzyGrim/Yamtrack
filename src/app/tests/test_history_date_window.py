"""A date-filtered history request must page the day index, not rebuild it.

start_date/end_date are whole-day bounds, so a date range only ever drops
whole days from the history. Routing such a request through the builder made
its cost the size of the matching history rather than the size of the page it
returns -- 6,454 entries built to emit a 56 KiB response.
"""

from datetime import timedelta
from http import HTTPStatus
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app import history_cache, history_cache_reader
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

DAYS = 8
EPISODES_PER_DAY = 3


class HistoryWindowFixture(TestCase):
    """Eight days of episodes plus one movie, cold cache per case."""

    @classmethod
    def setUpTestData(cls):
        cls.credentials = {"username": "history-window", "password": "12345"}
        cls.user = get_user_model().objects.create_user(**cls.credentials)
        tv_item = Item.objects.create(
            media_id="window-show",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Window Show",
            image="http://example.com/show.jpg",
            genres=["Drama"],
        )
        tv = TV.objects.create(
            item=tv_item, user=cls.user, status=Status.IN_PROGRESS.value,
        )
        season_item = Item.objects.create(
            media_id="window-show",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=1,
            title="Window Show Season 1",
        )
        season = Season.objects.create(
            item=season_item,
            user=cls.user,
            related_tv=tv,
            status=Status.IN_PROGRESS.value,
        )
        cls.today = timezone.now().replace(hour=12, minute=0, second=0, microsecond=0)
        number = 0
        for day in range(DAYS):
            for _ in range(EPISODES_PER_DAY):
                number += 1
                episode_item = Item.objects.create(
                    media_id="window-show",
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.EPISODE.value,
                    season_number=1,
                    episode_number=number,
                    title=f"Window Episode {number}",
                    runtime_minutes=30,
                )
                Episode.objects.create(
                    item=episode_item,
                    related_season=season,
                    end_date=cls.today - timedelta(days=day),
                )
        # A second media type, so a type filter has something to exclude.
        movie_item = Item.objects.create(
            media_id="window-movie",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Window Movie",
            runtime_minutes=90,
        )
        Movie.objects.create(
            item=movie_item,
            user=cls.user,
            status=Status.COMPLETED.value,
            end_date=cls.today - timedelta(days=2),
        )

    def setUp(self):
        """Every case starts cold, so nothing is answered from a stale index."""
        cache.clear()

    def _range(self):
        start = (self.today - timedelta(days=5)).date().isoformat()
        end = (self.today - timedelta(days=1)).date().isoformat()
        return {"start_date": start, "end_date": end}

    def _comparable(self, days):
        return [
            (
                day["date"],
                day["total_minutes"],
                [entry["entry_key"] for entry in day["entries"]],
            )
            for day in days
        ]

class HistoryDateWindowTests(HistoryWindowFixture):
    """The indexed window and the builder must agree on a date range."""

    def test_the_window_matches_the_builder_over_the_same_range(self):
        """Same days, same totals, same entries, in the same order."""
        date_filters = self._range()
        built = history_cache.get_history_days(
            self.user, date_filters=date_filters,
        )
        cache.clear()
        windowed, total_days = history_cache_reader.get_cached_history_window(
            self.user,
            limit=len(built) or 1,
            offset=0,
            date_filters=date_filters,
        )

        self.assertEqual(self._comparable(windowed), self._comparable(built))
        self.assertEqual(total_days, len(built))

    def test_the_range_bounds_are_inclusive(self):
        """A day named by start_date or end_date stays in the result."""
        date_filters = self._range()
        windowed, _ = history_cache_reader.get_cached_history_window(
            self.user, limit=50, offset=0, date_filters=date_filters,
        )

        dates = [day["date"].isoformat() for day in windowed]
        self.assertIn(date_filters["start_date"], dates)
        self.assertIn(date_filters["end_date"], dates)
        self.assertEqual(len(dates), 5)

    def test_a_type_filter_still_applies_inside_the_range(self):
        """Combining the two filters must not widen either one."""
        date_filters = {"start_date": (self.today - timedelta(days=2)).date().isoformat()}
        windowed, _ = history_cache_reader.get_cached_history_window(
            self.user,
            limit=50,
            offset=0,
            filters={"media_type": MediaTypes.TV.value},
            date_filters=date_filters,
        )

        media_types = {
            entry["media_type"] for day in windowed for entry in day["entries"]
        }
        self.assertEqual(media_types, {MediaTypes.EPISODE.value})

    def test_filtered_cold_day_does_not_poison_the_shared_cache(self):
        """A later unfiltered reader still sees every category on the day."""
        day = (self.today - timedelta(days=2)).date().isoformat()
        params = {"limit": 1, "offset": 0, "date_filters": {
            "start_date": day, "end_date": day,
        }}
        cold, _ = history_cache_reader.get_cached_history_window(
            self.user, filters={"media_type": MediaTypes.TV.value}, **params,
        )
        self.assertEqual(
            {entry["media_type"] for entry in cold[0]["entries"]},
            {MediaTypes.EPISODE.value},
        )
        complete, _ = history_cache_reader.get_cached_history_window(self.user, **params)
        self.assertEqual(
            {entry["media_type"] for entry in complete[0]["entries"]},
            {MediaTypes.EPISODE.value, MediaTypes.MOVIE.value},
        )
        warm, _ = history_cache_reader.get_cached_history_window(
            self.user, filters={"media_type": MediaTypes.MOVIE.value}, **params,
        )
        self.assertEqual(
            {entry["media_type"] for entry in warm[0]["entries"]},
            {MediaTypes.MOVIE.value},
        )

    def test_enabled_types_alone_read_and_fill_the_shared_day_cache(self):
        """A viewer with disabled types must not rebuild days on every request."""
        date_filters = self._range()
        allowed = {MediaTypes.EPISODE.value, MediaTypes.SEASON.value}
        params = {
            "limit": 50, "offset": 0, "date_filters": date_filters,
            "allowed_media_types": allowed,
        }
        cold, _ = history_cache_reader.get_cached_history_window(self.user, **params)
        self.assertEqual(
            {entry["media_type"] for day in cold for entry in day["entries"]},
            {MediaTypes.EPISODE.value},
        )
        with CaptureQueriesContext(connection) as warm_queries:
            warm, _ = history_cache_reader.get_cached_history_window(
                self.user, **params,
            )
        self.assertEqual(self._comparable(warm), self._comparable(cold))
        # The index is cached too, so only the cache is read.
        self.assertEqual(len(warm_queries), 0)

    def test_the_api_page_does_not_scale_with_the_history(self):
        """Adding days outside the page must not add queries or entries."""
        date_filters = self._range()
        params = {
            "media_type": MediaTypes.TV.value,
            "limit": 2,
            **date_filters,
        }
        url = reverse("api_history")
        auth = {"HTTP_AUTHORIZATION": f"Bearer {self.user.token}"}

        with CaptureQueriesContext(connection) as before:
            first = self.client.get(url, params, **auth)

        self.assertEqual(first.status_code, HTTPStatus.OK)
        payload = first.json()
        self.assertEqual(len(payload["results"]), 2)
        self.assertEqual(payload["pagination"]["total"], 5)

        season = Season.objects.get(user=self.user)
        number = DAYS * EPISODES_PER_DAY
        for day in range(DAYS, DAYS * 10):
            for _ in range(EPISODES_PER_DAY):
                number += 1
                episode_item = Item.objects.create(
                    media_id="window-show",
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.EPISODE.value,
                    season_number=1,
                    episode_number=number,
                    title=f"Window Episode {number}",
                    runtime_minutes=30,
                )
                Episode.objects.create(
                    item=episode_item,
                    related_season=season,
                    end_date=self.today - timedelta(days=day),
                )
        cache.clear()

        with CaptureQueriesContext(connection) as after:
            second = self.client.get(url, params, **auth)

        self.assertEqual(second.json(), payload)
        self.assertLessEqual(
            len(after.captured_queries),
            len(before.captured_queries),
            "a 10x history made the same page cost more queries",
        )


class HistoryPageDateWindowTests(HistoryWindowFixture):
    """The History page pages the day index and matches the builder."""

    def setUp(self):
        super().setUp()
        self.client.login(**self.credentials)

    def _page(self, **params):
        response = self.client.get(reverse("history"), params)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        return response.context

    def test_page_matches_the_builder_for_a_range_and_type(self):
        """Same days, entries, totals and pagination as rebuilding the range."""
        start = (self.today - timedelta(days=6)).date().isoformat()
        for extra in ({}, {"media_type": MediaTypes.TV.value}):
            with self.subTest(extra=extra):
                params = {"start-date": start, **extra}
                cache.clear()
                indexed = self._page(**params)
                built = history_cache.get_history_days(
                    self.user,
                    filters=extra,
                    date_filters={"start_date": start},
                )
                self.assertEqual(
                    self._comparable(indexed["history_days"]),
                    self._comparable(built),
                )
                self.assertEqual(indexed["total_days"], len(built))

    def test_page_does_not_rebuild_the_history(self):
        """The builder is not called for a date-bounded page."""
        start = (self.today - timedelta(days=6)).date().isoformat()
        with mock.patch.object(
            history_cache, "get_history_days", side_effect=AssertionError("rebuilt"),
        ):
            context = self._page(**{"start-date": start})
        self.assertEqual(context["total_days"], 7)

    def test_page_past_the_end_shows_the_last_page(self):
        """An out-of-range page number clamps like Paginator."""
        context = self._page(**{"start-date": "2000-01-01", "page": 99})
        self.assertEqual(context["current_page"], context["total_pages"])


class HistoryAnniversaryTests(HistoryWindowFixture):
    """The anniversary view reads the cached index and days, not the tables."""

    def test_second_anniversary_read_touches_no_tables(self):
        from app.history_views import _build_anniversary_history_days

        target = self.today - timedelta(days=2)
        first = _build_anniversary_history_days(
            self.user, target.month, target.day,
        )
        self.assertTrue(first)
        with CaptureQueriesContext(connection) as queries:
            second = _build_anniversary_history_days(
                self.user, target.month, target.day,
            )
        self.assertEqual(
            [day["date"] for day in second], [day["date"] for day in first],
        )
        self.assertEqual(len(queries), 0)


class HistoryLazyMonthTests(HistoryWindowFixture):
    """Days below the fold of a month view are placeholders, not cards."""

    def test_month_view_defers_days_past_the_first_few(self):
        from app.history_views import _first_view_day_count

        self.client.login(**self.credentials)
        # Eight consecutive days may straddle a month boundary; ask for the month
        # holding the most recent one and count what that month renders.
        response = self.client.get(
            reverse("history"),
            {"year": self.today.year, "m": self.today.month},
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        content = response.content.decode()
        days = content.count('class="history-day')
        lazy = content.count('hx-trigger="intersect once"')
        eager = _first_view_day_count(response.context["history_days"])
        self.assertEqual(lazy, max(days - eager, 0))
        if lazy:
            self.assertIn("/history/day/", content)
