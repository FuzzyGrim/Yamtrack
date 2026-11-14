"""Discover rebuilds after tracked writes only for people who use Discover.

A replay of 200 tracker writes over two and a half simulated hours counts the
tab rebuilds a write queues. Everyone else gets their tabs marked stale; the
next visit serves those rows marked stale and rebuilds at once.
"""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from app.discover import tab_cache
from app.discover.schemas import RowResult
from app.models import DiscoverRowCache, DiscoverTasteProfile, MediaTypes
from app.tasks import refresh_discover_tab_cache

WRITE_INTERVAL_SECONDS = 45
# Measured on the revision before the window existed (prompt 26, Stage 0).
BASELINE_REBUILDS = 58
# One evening of scrobbling: mostly episodes, with films, anime and a book.
WRITE_PATTERN = [
    MediaTypes.EPISODE.value,
    MediaTypes.EPISODE.value,
    MediaTypes.EPISODE.value,
    MediaTypes.MOVIE.value,
    MediaTypes.EPISODE.value,
    MediaTypes.EPISODE.value,
    MediaTypes.ANIME.value,
    MediaTypes.EPISODE.value,
    MediaTypes.BOOK.value,
    MediaTypes.EPISODE.value,
]
REPLAY_WRITES = WRITE_PATTERN * 20


class _Clock:
    """One simulated clock for Django's now() and the cache's expiry."""

    def __init__(self):
        self.now = timezone.now()

    def datetime(self):
        return self.now

    def timestamp(self):
        return self.now.timestamp()

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


@override_settings(DISCOVER_TASKS_EAGER_REFRESH=True, DISCOVER_EAGER_REBUILD_DAYS=3)
class DemandGatedRebuildTests(TestCase):
    """Count the rebuilds tracked writes queue, with and without Discover use."""

    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="discover-replay",
            password="x",
        )
        self.clock = _Clock()
        self.queued = []
        self.pending = []
        for target, kwargs in (
            ("time.time", {"side_effect": self.clock.timestamp}),
            ("django.utils.timezone.now", {"side_effect": self.clock.datetime}),
            # The rebuild protocol runs for real; building the rows does not.
            ("app.discover.service.get_discover_rows", {"return_value": []}),
        ):
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(
            refresh_discover_tab_cache,
            "apply_async",
            side_effect=self._enqueue,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        cache.clear()

    def _enqueue(self, args=None, kwargs=None, countdown=0, priority=None, **_):
        user_id, media_type = args
        self.queued.append((user_id, media_type, priority))
        self.pending.append(
            (self.clock.now + timedelta(seconds=countdown or 0), args, kwargs or {}),
        )

    def _run_due_tasks(self):
        due = [task for task in self.pending if task[0] <= self.clock.now]
        self.pending = [task for task in self.pending if task[0] > self.clock.now]
        for _run_at, args, kwargs in due:
            refresh_discover_tab_cache(*args, **kwargs)

    def _replay(self, writes=REPLAY_WRITES, *, user=None):
        user = user or self.user
        for media_type in writes:
            self._run_due_tasks()
            tab_cache.invalidate_for_media_change(user.id, media_type)
            self.clock.advance(WRITE_INTERVAL_SECONDS)
        self.clock.advance(60 * 60)
        self._run_due_tasks()
        return list(self.queued)

    @override_settings(DISCOVER_EAGER_REBUILD_DAYS=0)
    def test_rollback_setting_keeps_the_cooldown_limited_schedule(self):
        queued = self._replay()

        # Every tab the writes touch, about once per cooldown: the schedule
        # from before the window existed.
        self.assertEqual(len(queued), BASELINE_REBUILDS, queued)

    def test_writes_without_a_visit_queue_no_rebuilds_but_mark_tabs_stale(self):
        versions = []
        for media_type in REPLAY_WRITES:
            tab_cache.invalidate_for_media_change(self.user.id, media_type)
            versions.append(tab_cache.get_activity_version(self.user.id, "all"))
            self.clock.advance(WRITE_INTERVAL_SECONDS)

        self.assertEqual(self.queued, [])
        # Every write moved the version, so any cached tab now reads as stale.
        self.assertEqual(len(set(versions)), len(REPLAY_WRITES))

    def test_writes_after_a_recent_visit_rebuild_as_before(self):
        tab_cache.mark_active(self.user.id, "all")
        # The 45-second "looking at it now" context has passed; the visit counts.
        self.clock.advance(60)

        queued = self._replay()

        self.assertEqual(len(queued), BASELINE_REBUILDS, queued)

    def test_visit_window_lasts_the_configured_days(self):
        tab_cache.mark_active(self.user.id, "all")

        self.clock.advance(2 * 24 * 60 * 60)
        self.assertTrue(tab_cache.recently_used(self.user.id))
        self.clock.advance(2 * 24 * 60 * 60)
        self.assertFalse(tab_cache.recently_used(self.user.id))

    def test_after_the_window_the_next_visit_serves_stale_rows_and_rebuilds_once(self):
        row = RowResult(
            key="trending_right_now",
            title="Trending",
            mission="",
            why="",
            source="trakt",
            items=[],
        )
        tab_cache.mark_active(self.user.id, "all")
        tab_cache.set_tab_cache(self.user.id, "all", [row])
        self.clock.advance(4 * 24 * 60 * 60)

        self._replay(REPLAY_WRITES[:10])
        self.assertEqual(self.queued, [])

        rows = tab_cache.get_tab_rows(self.user, "all")

        self.assertEqual([served.key for served in rows], ["trending_right_now"])
        self.assertTrue(rows[0].is_stale)
        self.assertEqual(
            self.queued,
            [
                (
                    self.user.id,
                    "all",
                    tab_cache.DISCOVER_TASK_PRIORITY_INTERACTIVE,
                ),
            ],
        )

    def test_another_users_visit_does_not_rebuild_for_this_user(self):
        other = get_user_model().objects.create_user(username="other", password="x")
        tab_cache.mark_active(other.id, "all")
        self.clock.advance(60)

        self._replay(REPLAY_WRITES[:20])

        self.assertEqual(self.queued, [])

    def test_unanswered_cache_falls_back_to_rebuilding(self):
        for failure in ({"return_value": None}, {"side_effect": OSError("down")}):
            with self.subTest(failure=failure):
                cache.clear()
                self.queued.clear()
                self.pending.clear()
                with patch.object(tab_cache.cache, "has_key", **failure):
                    self.assertIsNone(tab_cache.recently_used(self.user.id))
                    tab_cache.invalidate_for_media_change(
                        self.user.id, MediaTypes.MOVIE.value
                    )

                self.assertEqual(
                    [media_type for _user, media_type, _priority in self.queued],
                    [MediaTypes.MOVIE.value, "all"],
                )

    def _row_cache(self, row_key, activity_version):
        return DiscoverRowCache.objects.create(
            user=self.user,
            media_type=MediaTypes.MOVIE.value,
            row_key=row_key,
            payload={"meta": {"activity_version": activity_version}},
            expires_at=self.clock.now + timedelta(hours=6),
        )

    def test_a_deferred_write_leaves_rows_for_the_rebuild_to_delete(self):
        movie = MediaTypes.MOVIE.value
        tab_cache.set_tab_cache(self.user.id, movie, [])
        before = tab_cache.get_activity_version(self.user.id, movie)
        self._row_cache("top_picks_for_you", before)
        DiscoverTasteProfile.objects.create(
            user=self.user,
            media_type=movie,
            expires_at=self.clock.now + timedelta(hours=6),
        )

        with CaptureQueriesContext(connection) as write:
            tab_cache.invalidate_for_media_change(self.user.id, movie)

        statements = " ".join(query["sql"] for query in write.captured_queries)
        self.assertNotIn("app_discoverrowcache", statements)
        self.assertTrue(DiscoverRowCache.objects.filter(user=self.user).exists())
        # The profile has readers outside a rebuild that check no version.
        self.assertFalse(DiscoverTasteProfile.objects.filter(user=self.user).exists())

        current = tab_cache.get_activity_version(self.user.id, movie)
        # A row refresh that ran after the write built one row for the new version.
        self._row_cache("coming_soon", current)
        rows_seen_by_build = []

        def build(user, media_type, **_kwargs):
            rows_seen_by_build.extend(
                DiscoverRowCache.objects.filter(
                    user=user, media_type=media_type
                ).values_list("row_key", flat=True),
            )
            return []

        with patch("app.discover.service.get_discover_rows", side_effect=build):
            tab_cache.refresh_tab_cache(self.user, movie)

        self.assertEqual(rows_seen_by_build, ["coming_soon"])

    def test_a_rebuild_with_an_unchanged_tab_keeps_its_rows(self):
        movie = MediaTypes.MOVIE.value
        version = tab_cache.get_activity_version(self.user.id, movie)
        tab_cache.set_tab_cache(self.user.id, movie, [], activity_version=version)
        self._row_cache("top_picks_for_you", version)

        with CaptureQueriesContext(connection) as rebuild:
            tab_cache.refresh_tab_cache(self.user, movie)

        statements = " ".join(query["sql"] for query in rebuild.captured_queries)
        self.assertNotIn("app_discoverrowcache", statements)
        self.assertTrue(DiscoverRowCache.objects.filter(user=self.user).exists())

    def test_feedback_from_discover_rebuilds_without_a_recorded_visit(self):
        tab_cache.invalidate_for_feedback_change(self.user.id, MediaTypes.MOVIE.value)

        self.assertEqual(
            [media_type for _user, media_type, _priority in self.queued],
            [MediaTypes.MOVIE.value, "all"],
        )
