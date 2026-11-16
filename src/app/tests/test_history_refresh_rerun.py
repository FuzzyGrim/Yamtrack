"""A save that lands while its day's History refresh runs is not dropped.

``schedule_history_refresh`` dedupes requests for the same days while a
refresh for them is queued or running. A request deduped that way used to
vanish: if its save committed after the running refresh had read its rows,
the day kept the pre-save payload until something else changed it.
"""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from app import history_cache, history_cache_reader
from app.history_cache_utils import _day_cache_key, history_day_key
from app.models import Item, MediaTypes, Movie, Sources, Status
from app.signals import suppress_media_change_side_effects

STYLE = "repeats"


def _movie(user, media_id, when):
    return Movie.objects.create(
        user=user,
        item=Item.objects.create(
            media_id=media_id,
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MOVIE.value,
            title=f"Title {media_id}",
        ),
        status=Status.COMPLETED.value,
        end_date=when,
    )


@patch("app.tasks.refresh_history_cache_task.apply_async")
class HistoryRefreshRerunTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="rerun", game_logging_style=STYLE
        )
        self.when = timezone.now() - timedelta(days=2)
        self.day = history_day_key(timezone.localtime(self.when).date())
        with suppress_media_change_side_effects():
            _movie(self.user, "first", self.when)

    def schedule(self, day_keys=None):
        return history_cache.schedule_history_refresh(
            self.user.id,
            STYLE,
            warm_days=0,
            day_keys=day_keys or [self.day],
            allow_inline=False,
        )

    def refresh(self, day_keys=None):
        return history_cache.refresh_history_cache(
            self.user.id,
            logging_style=STYLE,
            warm_days=0,
            day_keys=day_keys or [self.day],
        )

    def cached_titles(self):
        payload = history_cache._deserialize_history_day(
            cache.get(_day_cache_key(self.user.id, STYLE, self.day))
        )
        return sorted(entry["title"] for entry in payload["entries"])

    def queued_day_keys(self, apply_async):
        return [
            call.kwargs["kwargs"].get("day_keys") for call in apply_async.call_args_list
        ]

    def test_save_during_refresh_queues_another(self, apply_async):
        self.assertTrue(self.schedule())
        real_build = history_cache_reader._build_and_cache_history_day

        def build_then_save(*args, **kwargs):
            payload = real_build(*args, **kwargs)
            # The second save commits after this refresh read its rows.
            with suppress_media_change_side_effects():
                _movie(self.user, "second", self.when)
            self.assertFalse(self.schedule())
            return payload

        with patch.object(
            history_cache_reader, "_build_and_cache_history_day", build_then_save
        ):
            self.refresh()

        self.assertEqual(self.cached_titles(), ["Title first"])
        self.assertEqual(self.queued_day_keys(apply_async), [[self.day], [self.day]])

        # The follow-up refresh publishes the second save.
        self.refresh()
        self.assertEqual(self.cached_titles(), ["Title first", "Title second"])

    def test_save_before_refresh_starts_needs_no_rerun(self, apply_async):
        self.assertTrue(self.schedule())
        with suppress_media_change_side_effects():
            _movie(self.user, "second", self.when)
        # Deduped: the queued refresh has not read any rows yet.
        self.assertFalse(self.schedule())

        self.refresh()

        self.assertEqual(self.cached_titles(), ["Title first", "Title second"])
        self.assertEqual(self.queued_day_keys(apply_async), [[self.day]])
        self.assertTrue(self.schedule())

    def test_refresh_releases_only_its_own_day_lock(self, apply_async):
        other_day = history_day_key(
            timezone.localtime(self.when).date() - timedelta(days=5)
        )
        self.assertTrue(self.schedule())
        # Queued after the first, still waiting to run.
        self.assertTrue(self.schedule([other_day]))

        self.refresh()

        self.assertTrue(self.schedule())
        self.assertFalse(self.schedule([other_day]))

    def test_rerun_after_rerun_is_bounded_by_saves(self, apply_async):
        self.assertTrue(self.schedule())
        self.refresh()
        self.refresh()
        # No save landed during either refresh: nothing is queued again.
        self.assertEqual(self.queued_day_keys(apply_async), [[self.day]])
