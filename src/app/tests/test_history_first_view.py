"""History's first response carries about two screens of entries.

A busy month put 144 cards (five whole days) into the first response at the
20k benchmark fixture: about 2.5 MB of HTML and most of the server time. Later
days are placeholders the feed loads as the reader scrolls near them.
"""

import json
from datetime import timedelta
from http import HTTPStatus

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.history_views import HISTORY_FIRST_VIEW_ENTRIES, _first_view_day_count
from app.models import TV, Episode, Item, MediaTypes, Season, Sources, Status

DAYS = 6
EPISODES_PER_DAY = 10


class HistoryFirstViewTests(TestCase):
    """Six days of ten episodes each: three days fit the first view."""

    @classmethod
    def setUpTestData(cls):
        cls.credentials = {"username": "history-first-view", "password": "12345"}
        cls.user = get_user_model().objects.create_user(**cls.credentials)
        tv_item = Item.objects.create(
            media_id="first-view-show",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="First View Show",
            image="http://example.com/show.jpg",
        )
        tv = TV.objects.create(item=tv_item, user=cls.user, status=Status.IN_PROGRESS.value)
        season_item = Item.objects.create(
            media_id="first-view-show",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=1,
            title="First View Show Season 1",
        )
        season = Season.objects.create(
            item=season_item, user=cls.user, related_tv=tv, status=Status.IN_PROGRESS.value,
        )
        cls.today = timezone.now().replace(hour=12, minute=0, second=0, microsecond=0)
        number = 0
        for day in range(DAYS):
            for _ in range(EPISODES_PER_DAY):
                number += 1
                episode_item = Item.objects.create(
                    media_id="first-view-show",
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.EPISODE.value,
                    season_number=1,
                    episode_number=number,
                    title=f"First View Episode {number}",
                    runtime_minutes=30,
                )
                Episode.objects.create(
                    item=episode_item,
                    related_season=season,
                    end_date=cls.today - timedelta(days=day),
                )

    def setUp(self):
        cache.clear()
        self.client.login(**self.credentials)

    def _feed(self, params):
        response = self.client.get(reverse("history"), params)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        return response, response.context["history_feed_props"]["model"]

    def _range(self):
        return {
            "start-date": (self.today - timedelta(days=DAYS - 1)).date().isoformat(),
            "end-date": self.today.date().isoformat(),
        }

    def test_first_response_holds_about_two_screens_of_entries(self):
        response, model = self._feed(self._range())
        days = model["days"]
        self.assertEqual(len(days), DAYS, "every day is still listed")
        eager = [day for day in days if not day["lazy"]]
        waiting = [day for day in days if day["lazy"]]
        entries = sum(len(day["entries"]) for day in days)
        self.assertLessEqual(entries, HISTORY_FIRST_VIEW_ENTRIES)
        self.assertEqual(len(eager), 3)
        self.assertEqual(entries, 3 * EPISODES_PER_DAY)
        # Waiting days come last, keep their counts and say where to load from.
        self.assertEqual(days[: len(eager)], eager)
        for day in waiting:
            self.assertEqual(day["entries"], [])
            self.assertEqual(day["entryCount"], EPISODES_PER_DAY)
            self.assertIn("/history/day/", day["loadUrl"])
        # The server fallback renders the same split.
        content = response.content.decode()
        self.assertEqual(content.count('hx-trigger="intersect once"'), len(waiting))

    def test_a_waiting_day_loads_the_same_entries(self):
        _response, model = self._feed(self._range())
        waiting = next(day for day in model["days"] if day["lazy"])
        loaded = self.client.get(waiting["loadUrl"]).json()["day"]
        self.assertEqual(len(loaded["entries"]), EPISODES_PER_DAY)
        self.assertFalse(loaded["lazy"])

    def test_month_view_uses_the_same_budget(self):
        _response, model = self._feed({"year": self.today.year, "m": self.today.month})
        counts = [len(day["entries"]) for day in model["days"]]
        self.assertLessEqual(sum(counts), HISTORY_FIRST_VIEW_ENTRIES)

    def test_first_view_day_count(self):
        def days(*counts):
            return [{"entries": [None] * count} for count in counts]

        self.assertEqual(_first_view_day_count([]), 0)
        self.assertEqual(_first_view_day_count(days(30, 30, 30)), 1)
        self.assertEqual(_first_view_day_count(days(50)), 1)
        self.assertEqual(_first_view_day_count(days(10, 10, 10, 10)), 3)
        self.assertEqual(_first_view_day_count(days(12, 12, 12, 1)), 3)
        self.assertEqual(_first_view_day_count(days(10, 30, 1, 1)), 1)
