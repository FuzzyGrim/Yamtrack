"""Cost and effects of marking a long show Completed and deleting a season.

Completing a show writes its remaining episodes in bulk; the response then
renders progress, next episode and dates for every season. Deleting a season
cascades to each of its episodes, and every one of them runs the delete signals.
These tests pin that neither grows with the number of episodes in ways the
fixes in the "Completed" write path removed, and that the fixes change nothing a
user sees.
"""

import re
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management.color import no_style
from django.db import connection, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app import history_cache, save_views
from app.models import (
    TV,
    Episode,
    Item,
    MediaTypes,
    MetadataBackfillField,
    MetadataBackfillState,
    Season,
    Sources,
    Status,
)
from events.calendar.helpers import date_parser

SHOW_ID = "990001"
SEASONS = 8


def _episode_meta(number):
    return {
        "episode_number": number,
        "name": f"Ep {number}",
        "title": f"Ep {number}",
        "air_date": "2020-01-01",
        "still_path": None,
        "runtime": 42,
    }


def _metadata(per_season):
    def season(number):
        return {
            "season_number": number,
            "title": f"Season {number}",
            "image": "https://example.com/s.jpg",
            "max_progress": per_season,
            "tvdb_id": None,
            "_tvdb_episode_image_map": {},
            "episodes": [_episode_meta(e) for e in range(1, per_season + 1)],
        }

    show = {
        "media_id": SHOW_ID,
        "source": Sources.TMDB.value,
        "media_type": MediaTypes.TV.value,
        "title": "Long Show",
        "image": "https://example.com/tv.jpg",
        "max_progress": SEASONS * per_season,
        "details": {"seasons": SEASONS, "episodes": SEASONS * per_season},
        "related": {
            "seasons": [
                {"season_number": n, "episode_count": per_season, "image": None}
                for n in range(1, SEASONS + 1)
            ],
        },
    }

    def fake(media_type, _media_id, _source, season_numbers=None, *_a, **_k):
        if media_type == "tv_with_seasons":
            payload = dict(show)
            for number in season_numbers or range(1, SEASONS + 1):
                payload[f"season/{number}"] = season(int(number))
            return payload
        if media_type == MediaTypes.SEASON.value:
            return season(int((season_numbers or [1])[0]))
        return dict(show)

    return fake


def _rewind_id_counters():
    """Make a rolled-back run hand out the same ids again on PostgreSQL.

    SQLite reuses ids after a rollback; PostgreSQL sequences do not, and the
    rendered page contains ids.
    """
    models = list(apps.get_app_config("app").get_models())
    statements = connection.ops.sequence_reset_sql(no_style(), models)
    if statements:
        with connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)


def _normalize(html):
    """Drop what differs between two renders of the same state."""
    html = re.sub(r'(csrfToken": "|X-CSRFToken": ")[A-Za-z0-9]*', r"\1T", html)
    html = re.sub(r"csrfmiddlewaretoken\" value=\"[^\"]+\"", "CSRF", html)
    return re.sub(r"(rating-(?:stars|chip)-|track-form-)[0-9a-f]+", r"\1X", html)


class CompletedShowTests(TestCase):
    """Marking a show Completed from its page."""

    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="long", password="x")
        self.client.force_login(self.user)

    def _show(self, per_season, *, watched=3):
        """A show with catalogue items for every episode and a few watched."""
        tv_item = Item.objects.create(
            media_id=SHOW_ID, source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value, library_media_type=MediaTypes.TV.value,
            title="Long Show", image="https://example.com/tv.jpg",
        )
        tv = TV.objects.create(item=tv_item, user=self.user, status=Status.IN_PROGRESS.value)
        season_item = Item.objects.create(
            media_id=SHOW_ID, source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value, library_media_type=MediaTypes.SEASON.value,
            season_number=1, title="Long Show", image="https://example.com/s.jpg",
        )
        season = Season.objects.create(
            item=season_item, user=self.user, related_tv=tv, status=Status.IN_PROGRESS.value,
        )
        items = Item.objects.bulk_create(
            Item(
                media_id=SHOW_ID, source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                library_media_type=MediaTypes.EPISODE.value,
                season_number=s, episode_number=e,
                image="https://example.com/e.jpg", runtime_minutes=42,
                release_datetime=date_parser("2020-01-01"),
                **Item.title_fields_from_episode_metadata(
                    _episode_meta(e), fallback_title="Long Show",
                ),
            )
            for s in range(1, SEASONS + 1)
            for e in range(1, per_season + 1)
        )
        Episode.objects.bulk_create(
            Episode(item=item, related_season=season, end_date=timezone.now())
            for item in items
            if item.season_number == 1 and item.episode_number <= watched
        )
        return tv

    def _complete(self, tv, per_season):
        with (
            patch(
                "app.providers.services.get_media_metadata",
                side_effect=_metadata(per_season),
            ),
            CaptureQueriesContext(connection) as queries,
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.client.post(
                reverse("media_save"),
                {
                    "media_id": SHOW_ID, "source": Sources.TMDB.value,
                    "media_type": MediaTypes.TV.value, "instance_id": tv.id,
                    "status": Status.COMPLETED.value, "score": "", "notes": "",
                    "start_date": "", "end_date": "2026-11-10",
                },
                HTTP_HX_REQUEST="true",
            )
        self.assertEqual(response.status_code, 200)
        return response, len(queries)

    def _count_for(self, per_season):
        with transaction.atomic():
            tv = self._show(per_season)
            _response, count = self._complete(tv, per_season)
            self.assertEqual(
                Episode.objects.filter(related_season__related_tv=tv).count(),
                SEASONS * per_season,
            )
            transaction.set_rollback(True)
        cache.clear()
        return count

    def test_query_count_does_not_grow_with_episodes(self):
        """40 and 200 episodes cost the same; it was two queries per episode."""
        small = self._count_for(5)
        large = self._count_for(25)
        self.assertLessEqual(large - small, 5, (small, large))
        # About 210 at 200 episodes, including the one History refresh after commit.
        self.assertLessEqual(large, 230)

    def test_one_load_for_the_response_changes_nothing_on_the_page(self):
        """The response is the same with or without the shared prefetch."""
        renders = []
        for prefetch in (False, True):
            _rewind_id_counters()
            with transaction.atomic():
                tv = self._show(10)
                if prefetch:
                    response, _count = self._complete(tv, 10)
                else:
                    with patch.object(save_views, "_prefetch_show_trees"):
                        response, _count = self._complete(tv, 10)
                renders.append(
                    _normalize(response.content.decode())
                    + _normalize(response["HX-Trigger"]),
                )
                transaction.set_rollback(True)
            cache.clear()
        self.assertIn("Completed", renders[1])
        self.assertEqual(renders[0], renders[1])

    def test_season_stats_without_prefetch_read_episodes_in_one_query(self):
        """Reading progress off a bare season does not load items one by one."""
        tv = self._show(25, watched=20)
        season = Season.objects.get(related_tv=tv)
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(season.progress, 20)
            self.assertEqual(len(season.watched_once_numbers), 20)
        self.assertEqual(len(queries), 1)


class SeasonDeleteTests(TestCase):
    """Deleting a season cascades to its episodes."""

    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="del", password="x")
        self.client.force_login(self.user)
        tv_item = Item.objects.create(
            media_id=SHOW_ID, source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value, library_media_type=MediaTypes.TV.value,
            title="Long Show", image="https://example.com/tv.jpg",
        )
        self.tv = TV.objects.create(item=tv_item, user=self.user, status=Status.IN_PROGRESS.value)

    def _season(self, number, episodes):
        item = Item.objects.create(
            media_id=SHOW_ID, source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value, library_media_type=MediaTypes.SEASON.value,
            season_number=number, title="Long Show", image="https://example.com/s.jpg",
        )
        season = Season.objects.create(
            item=item, user=self.user, related_tv=self.tv, status=Status.IN_PROGRESS.value,
        )
        items = Item.objects.bulk_create(
            Item(
                media_id=SHOW_ID, source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                library_media_type=MediaTypes.EPISODE.value,
                season_number=number, episode_number=e, title=f"Ep {e}",
                image="https://example.com/e.jpg",
            )
            for e in range(1, episodes + 1)
        )
        Episode.objects.bulk_create(
            Episode(item=item, related_season=season, end_date=timezone.now())
            for item in items
        )
        return season

    def _delete(self, season):
        with (
            CaptureQueriesContext(connection) as queries,
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.client.post(
                reverse("media_delete"),
                {"instance_id": season.id, "media_type": MediaTypes.SEASON.value},
                HTTP_HX_REQUEST="true",
            )
        self.assertLess(response.status_code, 400)
        return [query["sql"] for query in queries]

    def test_user_lock_is_taken_once_for_the_whole_cascade(self):
        """Every deleted episode used to look up and lock the user's row again."""
        sql = self._delete(self._season(1, 12))
        self.assertFalse(Season.objects.filter(related_tv=self.tv).exists())
        locks = [q for q in sql if "app_watchstatesequence" in q and "FOR UPDATE" in q.upper()]
        # SQLite drops FOR UPDATE; count the lookups instead.
        lookups = [q for q in sql if q.startswith("SELECT") and "app_watchstatesequence" in q]
        self.assertLessEqual(len(locks), 1)
        self.assertLessEqual(len(lookups), 2, lookups)

    def test_show_is_stamped_once_and_the_dying_season_not_at_all(self):
        """The cascade stamps the surviving show, once, and skips the season."""
        season = self._season(1, 6)
        stale = timezone.now() - timedelta(days=10)
        TV.objects.filter(pk=self.tv.pk).update(updated_at=stale)
        sql = self._delete(season)
        self.tv.refresh_from_db()
        self.assertGreater(self.tv.updated_at, stale + timedelta(days=9))
        self.assertEqual(
            sum(q.startswith('UPDATE "app_tv" SET "updated_at"') for q in sql), 1,
        )
        self.assertFalse(any(q.startswith('UPDATE "app_season" SET "updated_at"') for q in sql))

    def test_deleting_one_episode_still_stamps_its_season_and_show(self):
        """Outside a season delete each episode change stamps both owners."""
        season = self._season(1, 2)
        stale = timezone.now() - timedelta(days=10)
        Season.objects.filter(pk=season.pk).update(updated_at=stale)
        TV.objects.filter(pk=self.tv.pk).update(updated_at=stale)
        Episode.objects.filter(related_season=season).first().delete()
        season.refresh_from_db()
        self.tv.refresh_from_db()
        self.assertGreater(season.updated_at, stale + timedelta(days=9))
        self.assertGreater(self.tv.updated_at, stale + timedelta(days=9))

    def test_cost_per_deleted_episode_stays_down(self):
        """Per-episode delete work is the signals that must run, nothing more."""
        small = len(self._delete(self._season(1, 5)))
        large = len(self._delete(self._season(2, 25)))
        # 31 queries per episode before; the watched-state recompute, history
        # row and import fence that remain are about 22.
        self.assertLessEqual((large - small) / 20, 24, (small, large))


class NewItemBackfillStateTests(TestCase):
    """A just-created item has no backfill state to clear."""

    def test_creating_an_item_skips_the_backfill_state_lookups(self):
        with CaptureQueriesContext(connection) as queries:
            Item.objects.create(
                media_id="1", source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                library_media_type=MediaTypes.EPISODE.value,
                season_number=1, episode_number=1, title="One",
                image="https://example.com/e.jpg", runtime_minutes=30,
            )
        self.assertFalse(
            any("app_metadatabackfillstate" in q["sql"] for q in queries.captured_queries),
        )

    def test_saving_a_known_runtime_still_clears_runtime_state(self):
        item = Item.objects.create(
            media_id="2", source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            library_media_type=MediaTypes.EPISODE.value,
            season_number=1, episode_number=1, title="One",
            image="https://example.com/e.jpg",
        )
        MetadataBackfillState.objects.create(item=item, field=MetadataBackfillField.RUNTIME)
        item.runtime_minutes = 30
        item.save(update_fields=["runtime_minutes"])
        self.assertFalse(
            MetadataBackfillState.objects.filter(
                item=item, field=MetadataBackfillField.RUNTIME,
            ).exists(),
        )


class CompletedHistoryTests(CompletedShowTests):
    """History shows the episodes a completion writes in bulk, right away."""

    def _history_day(self):
        day_key = history_cache.history_day_key(datetime(2026, 11, 10, 12, tzinfo=UTC))
        response = self.client.get(
            reverse("history_day_fragment", args=[day_key]), {"format": "json"},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["day"]["entryCount"]

    def _show_watched_on_the_day(self, per_season):
        tv = self._show(per_season)
        Episode.objects.filter(related_season__related_tv=tv).update(
            end_date=datetime(2026, 11, 10, 12, tzinfo=UTC),
        )
        return tv

    def test_completing_a_show_refreshes_its_history_day(self):
        tv = self._show_watched_on_the_day(5)
        self.assertEqual(self._history_day(), 3)  # cached with the 3 watched
        self._complete(tv, 5)
        self.assertEqual(self._history_day(), SEASONS * 5)

    def test_completing_a_season_refreshes_its_history_day(self):
        tv = self._show_watched_on_the_day(5)
        self.assertEqual(self._history_day(), 3)
        season = Season.objects.get(related_tv=tv)
        with (
            patch("app.providers.services.get_media_metadata", side_effect=_metadata(5)),
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.client.post(
                reverse("media_save"),
                {
                    "media_id": SHOW_ID, "source": Sources.TMDB.value,
                    "media_type": MediaTypes.SEASON.value, "season_number": 1,
                    "instance_id": season.id, "status": Status.COMPLETED.value,
                    "score": "", "notes": "", "start_date": "", "end_date": "2026-11-10",
                },
                HTTP_HX_REQUEST="true",
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Episode.objects.filter(related_season=season).count(), 5)
        self.assertEqual(self._history_day(), 5)

    test_query_count_does_not_grow_with_episodes = None
    test_one_load_for_the_response_changes_nothing_on_the_page = None
    test_season_stats_without_prefetch_read_episodes_in_one_query = None
