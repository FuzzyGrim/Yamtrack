import json
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import call, patch
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from app import (
    statistics_aggregator,
    statistics_cache,
    statistics_refresh,
    statistics_sync,
)
from app.models import (
    Album,
    Artist,
    BoardGame,
    Book,
    BookListeningSession,
    Item,
    MediaTypes,
    Movie,
    Music,
    Podcast,
    PodcastEpisode,
    PodcastShow,
    Sources,
    Status,
    Track,
)
from app.statistics_aggregator import (
    _aggregate_minutes_per_media_type_from_days,
    _build_combined_hours_charts,
    _build_platform_breakdown,
    _build_weekday_hour_charts,
)
from app.statistics_day_cache import STATISTICS_DAY_PREFIX, _normalize_day_value


class StatisticsDayBatchingTests(SimpleTestCase):
    @patch("app.statistics_aggregator.cache.get_many", return_value={})
    def test_thousands_of_days_are_fetched_in_fixed_batches(self, get_many):
        days = [date(2020, 1, 1) + timedelta(days=index) for index in range(1000)]

        result = _aggregate_minutes_per_media_type_from_days(
            SimpleNamespace(id=42),
            days,
        )

        self.assertEqual(result, {})
        self.assertEqual(get_many.call_count, 20)
        self.assertTrue(
            all(len(call_args.args[0]) <= 50 for call_args in get_many.call_args_list)
        )


class StatisticsRefreshPayloadRetentionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="stats-refresh-payloads",
            password="secret123",
        )
        self.days = []
        for offset in (1, 3):
            item = Item.objects.create(
                media_id=f"stats-refresh-payload-{offset}",
                source=Sources.MANUAL.value,
                media_type=MediaTypes.MOVIE.value,
                title=f"Statistics payload movie {offset}",
                runtime_minutes=90,
            )
            watched_at = timezone.now() - timedelta(days=offset)
            Movie.objects.create(
                user=self.user,
                item=item,
                status=Status.COMPLETED.value,
                end_date=watched_at,
            )
            self.days.append(_normalize_day_value(watched_at))

    def tearDown(self):
        cache.clear()

    def test_refresh_rebuilds_days_whose_cache_write_failed(self):
        """A day whose payload write failed is rebuilt by the aggregate."""
        failed_key = statistics_refresh._day_cache_key(self.user.id, self.days[0])
        cache.delete_many(
            [statistics_refresh._day_cache_key(self.user.id, day) for day in self.days]
        )
        original_set_many = cache.set_many

        def set_many_dropping_one_day(values, timeout=None, version=None):
            original_set_many(
                {key: value for key, value in values.items() if key != failed_key},
                timeout=timeout,
                version=version,
            )
            return [failed_key] if failed_key in values else []

        with (
            patch.object(
                statistics_sync.cache,
                "set_many",
                side_effect=set_many_dropping_one_day,
            ),
            patch(
                "app.statistics_aggregator.build_stats_for_day",
                wraps=statistics_aggregator.build_stats_for_day,
            ) as rebuild,
        ):
            result = statistics_refresh.refresh_statistics_cache(
                self.user.id,
                "All Time",
            )

        self.assertIsNotNone(result)
        self.assertIn(
            self.days[0],
            [call_args.args[1] for call_args in rebuild.call_args_list],
        )
        self.assertEqual(
            result["hours_per_media_type"],
            statistics_cache.get_statistics_data(self.user, None, None, "All Time")[
                "hours_per_media_type"
            ],
        )


@override_settings(CELERY_TASK_ALWAYS_EAGER=False, TESTING=False)
class StatisticsRefreshSchedulingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="stats-refresh-user",
            password="secret123",
        )

    def tearDown(self):
        cache.clear()

    @patch("app.tasks_interactive.statistics_sync_task.apply_async")
    def test_background_sync_runs_on_the_bulk_lane_but_keeps_its_priority(
        self, enqueue
    ):
        scheduled = statistics_cache.schedule_statistics_refresh(
            self.user.id, "This Month", allow_inline=False
        )

        self.assertTrue(scheduled)
        priority = enqueue.call_args.kwargs["priority"]
        self.assertEqual(priority, settings.CELERY_TASK_PRIORITY_STATISTICS_SYNC)
        self.assertGreater(priority, settings.CELERY_TASK_PRIORITY_INTERACTIVE)
        self.assertLess(priority, settings.CELERY_TASK_PRIORITY_FOLLOWUP)
        self.assertEqual(enqueue.call_args.kwargs["queue"], "celery")
        route = settings.CELERY_TASK_ROUTES["app.tasks.statistics_sync_task"]
        self.assertEqual(route["queue"], "celery")

    @patch("app.tasks_interactive.statistics_sync_task.apply_async")
    def test_forced_refresh_stays_on_the_interactive_lane(self, enqueue):
        statistics_cache.schedule_statistics_refresh(
            self.user.id, "This Month", force=True
        )

        self.assertEqual(
            enqueue.call_args.kwargs["priority"],
            settings.CELERY_TASK_PRIORITY_INTERACTIVE,
        )
        self.assertEqual(enqueue.call_args.kwargs["queue"], "interactive")

    def test_talent_fragment_rebuild_runs_on_the_bulk_lane(self):
        route = settings.CELERY_TASK_ROUTES["Refresh statistics talent fragment"]
        self.assertEqual(route["queue"], "celery")

    def test_schedule_all_ranges_refresh_records_a_change(self):
        before = statistics_sync.current_generation(self.user.id)
        with self.captureOnCommitCallbacks(execute=False):
            statistics_cache.schedule_all_ranges_refresh(self.user.id)

        self.assertGreater(statistics_sync.current_generation(self.user.id), before)


@override_settings(CELERY_TASK_ALWAYS_EAGER=False, TESTING=False)
class StatisticsStaleResultTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="stats-stale-user")
        self.data = statistics_cache._get_empty_statistics_data()
        self.data["hours_per_media_type"]["movie"] = "2h"
        statistics_cache.cache_statistics_data(self.user.id, "This Month", self.data)

    def tearDown(self):
        cache.clear()

    def _invalidate(self, range_name=None):
        with self.captureOnCommitCallbacks(execute=False):
            statistics_cache.invalidate_statistics_cache(self.user.id, range_name)
        cache.delete(statistics_sync._gate_key(self.user.id))

    @patch("app.tasks_interactive.statistics_sync_task.apply_async")
    def test_invalidation_serves_previous_result_and_queues_a_sync(self, enqueue):
        for range_name in ("This Month", None):
            with self.subTest(range_name=range_name):
                self._invalidate(range_name)
                enqueue.reset_mock()

                result = statistics_cache.get_statistics_data(
                    self.user, None, None, "This Month"
                )

                self.assertEqual(result, self.data)
                enqueue.assert_called_once()

    @patch("app.statistics_cache.refresh_statistics_cache")
    @patch(
        "app.tasks_interactive.statistics_sync_task.apply_async",
        side_effect=OSError("offline"),
    )
    def test_unavailable_worker_keeps_previous_result_without_inline_rebuild(
        self, enqueue, rebuild
    ):
        self._invalidate()

        result = statistics_cache.get_statistics_data(
            self.user, None, None, "This Month"
        )

        self.assertEqual(result, self.data)
        enqueue.assert_called_once()
        rebuild.assert_not_called()

    def test_completed_refresh_replaces_previous_result(self):
        self._invalidate()
        updated = statistics_cache._get_empty_statistics_data()
        statistics_cache.cache_statistics_data(self.user.id, "This Month", updated)

        with patch("app.tasks_interactive.statistics_sync_task.apply_async") as enqueue:
            result = statistics_cache.get_statistics_data(
                self.user, None, None, "This Month"
            )

        self.assertEqual(result, updated)
        enqueue.assert_not_called()

    @patch("app.tasks_interactive.statistics_sync_task.apply_async")
    def test_previous_day_result_refreshes_even_without_new_activity(self, enqueue):
        later = timezone.now() + timedelta(days=1)
        with patch("django.utils.timezone.now", return_value=later):
            result = statistics_cache.get_statistics_data(
                self.user, None, None, "This Month"
            )
            # Repeat visits while the task waits must not multiply the work.
            statistics_cache.get_statistics_data(self.user, None, None, "This Month")

        self.assertEqual(result, self.data)
        enqueue.assert_called_once()

    @patch("app.tasks_interactive.statistics_sync_task.apply_async")
    def test_local_midnight_refreshes_a_recent_today_snapshot(self, enqueue):
        # UTC is still the same date, but Tokyo has crossed midnight.
        before = datetime(2026, 6, 1, 14, 59, tzinfo=UTC)
        after = before + timedelta(minutes=2)
        with timezone.override(ZoneInfo("Asia/Tokyo")):
            with patch("django.utils.timezone.now", return_value=before):
                statistics_cache.cache_statistics_data(self.user.id, "Today", self.data)
            with patch("django.utils.timezone.now", return_value=after):
                result = statistics_cache.get_statistics_data(
                    self.user, None, None, "Today"
                )

        self.assertEqual(result, self.data)
        enqueue.assert_called_once()

    @patch("app.tasks_interactive.statistics_sync_task.apply_async")
    def test_polling_reports_and_queues_a_previous_day_result(self, enqueue):
        self.client.force_login(self.user)
        later = timezone.now() + timedelta(days=1)
        with patch("django.utils.timezone.now", return_value=later):
            response = self.client.get(
                reverse("cache_status"),
                {"cache_type": "statistics", "range_name": "This Month"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["exists"])
        self.assertTrue(response.json()["is_stale"])
        self.assertTrue(response.json()["is_refreshing"])
        enqueue.assert_called_once()

    @patch("app.tasks_interactive.statistics_sync_task.apply_async")
    def test_unchanged_same_day_snapshot_does_not_rebuild_every_fifteen_minutes(
        self, enqueue
    ):
        before = datetime(2026, 6, 1, 12, tzinfo=UTC)
        with timezone.override(ZoneInfo("UTC")):
            with patch("django.utils.timezone.now", return_value=before):
                statistics_cache.cache_statistics_data(self.user.id, "Today", self.data)
            with patch(
                "django.utils.timezone.now",
                return_value=before + timedelta(hours=2),
            ):
                result = statistics_cache.get_statistics_data(
                    self.user, None, None, "Today"
                )
        self.assertEqual(result, self.data)
        enqueue.assert_not_called()

    @patch("app.tasks_interactive.statistics_sync_task.apply_async")
    def test_lightweight_statistics_reads_refresh_retained_stale_results(self, enqueue):
        for reader, key in (
            (statistics_cache.get_statistics_minutes_by_type, "minutes_per_media_type"),
            (statistics_cache.get_statistics_media_count, "media_count"),
            (statistics_cache.get_top_talent_data, "top_talent"),
        ):
            with self.subTest(reader=reader.__name__):
                self._invalidate()
                enqueue.reset_mock()
                result = reader(self.user, None, None, "This Month")
                self.assertEqual(result, self.data[key])
                enqueue.assert_called_once()

    def test_page_snapshot_survives_between_daily_visits(self):
        ttl = cache.ttl(statistics_cache._cache_key(self.user.id, "This Month"))
        self.assertGreater(ttl, 24 * 60 * 60)

    @patch("app.tasks_interactive.statistics_sync_task.apply_async")
    def test_snapshot_survives_a_cache_flush(self, enqueue):
        cache.clear()

        result = statistics_cache.get_statistics_data(
            self.user, None, None, "This Month"
        )

        self.assertEqual(
            result["hours_per_media_type"], self.data["hours_per_media_type"]
        )
        self.assertNotIn("statistics_building", result)

    @patch("app.tasks_interactive.statistics_sync_task.apply_async")
    def test_never_built_range_reports_building_and_queues_urgently(self, enqueue):
        result = statistics_cache.get_statistics_data(self.user, None, None, "All Time")

        self.assertTrue(result["statistics_building"])
        self.assertEqual(
            enqueue.call_args.kwargs["priority"],
            settings.CELERY_TASK_PRIORITY_INTERACTIVE,
        )


class StatisticsHourBucketTests(TestCase):
    """Time-of-day statistics omit date-only midnight placeholders."""

    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="stats-hour-user",
            password="secret123",
        )

    def tearDown(self):
        cache.clear()

    def test_exact_midnight_is_excluded_but_later_midnight_activity_remains(self):
        """Only exact 00:00 placeholders are omitted from hour buckets."""
        midnight = timezone.make_aware(datetime(2025, 1, 15))
        for index, (watched_at, runtime) in enumerate(
            ((midnight, 120), (midnight + timedelta(minutes=30), 90)),
            start=1,
        ):
            item = Item.objects.create(
                media_id=f"midnight-movie-{index}",
                source=Sources.MANUAL.value,
                media_type=MediaTypes.MOVIE.value,
                title=f"Midnight Movie {index}",
                runtime_minutes=runtime,
            )
            Movie.objects.create(
                item=item,
                user=self.user,
                status=Status.COMPLETED.value,
                progress=1,
                start_date=watched_at,
                end_date=watched_at,
            )

        day_stats = statistics_cache.build_stats_for_day(
            self.user.id,
            midnight.date(),
        )

        self.assertEqual(day_stats["hour_counts"][MediaTypes.MOVIE.value], {"0": 1})
        self.assertEqual(
            day_stats["hour_minutes"][MediaTypes.MOVIE.value],
            {"0": 90},
        )
        self.assertEqual(
            day_stats["totals"]["minutes_by_type"][MediaTypes.MOVIE.value],
            210,
        )
        self.assertEqual(
            day_stats["totals"]["plays_by_type"][MediaTypes.MOVIE.value],
            2,
        )
        # Day payloads cross the cache boundary before the range aggregator
        # consumes them. IDs/hours and datetimes must survive serialization.
        serialized_day = json.loads(json.dumps(day_stats))
        self.assertEqual(serialized_day["day"], midnight.date().isoformat())
        self.assertEqual(serialized_day["activity"], {"count": 2})
        self.assertEqual(
            set(serialized_day["items"]["movie"]),
            {str(item_id) for item_id in Item.objects.values_list("id", flat=True)},
        )
        self.assertEqual(
            {
                entry["activity_dt"]
                for entry in serialized_day["items"]["movie"].values()
            },
            {midnight.isoformat(), (midnight + timedelta(minutes=30)).isoformat()},
        )
        self.assertEqual(
            _aggregate_minutes_per_media_type_from_days(self.user, [midnight.date()]),
            {"movie": 210},
        )


class StatisticsBoardGameDayTests(TestCase):
    """Board game plays contribute minutes, one play and spread daily time."""

    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="stats-boardgame-user",
            password="secret123",
        )
        self.day = timezone.make_aware(datetime(2025, 1, 15, 12))

    def tearDown(self):
        cache.clear()

    def _boardgame(self, key, progress, start, end):
        item = Item.objects.create(
            media_id=key,
            source=Sources.MANUAL.value,
            media_type=MediaTypes.BOARDGAME.value,
            title=key,
        )
        BoardGame.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=progress,
            start_date=start,
            end_date=end,
        )
        return item

    def _day_stats(self):
        return statistics_cache.build_stats_for_day(self.user.id, self.day.date())

    def test_play_ending_today_counts_minutes_play_and_top_played(self):
        item = self._boardgame("bg-same-day", 90, self.day, self.day)

        stats = self._day_stats()

        key = MediaTypes.BOARDGAME.value
        self.assertEqual(stats["totals"]["minutes_by_type"][key], 90)
        self.assertEqual(stats["daily_minutes_by_type"][key], 90)
        self.assertEqual(stats["top_played"][key][str(item.id)]["plays"], 1)
        self.assertEqual(stats["top_played"][key][str(item.id)]["minutes"], 90)
        self.assertIn(str(item.id), stats["items"][key])

    def test_multi_day_span_spreads_daily_time_without_counting_a_play(self):
        self._boardgame(
            "bg-span",
            300,
            self.day - timedelta(days=1),
            self.day + timedelta(days=1),
        )

        stats = self._day_stats()

        key = MediaTypes.BOARDGAME.value
        self.assertEqual(stats["daily_minutes_by_type"][key], 100)
        self.assertEqual(stats["totals"]["minutes_by_type"].get(key, 0), 0)
        self.assertEqual(stats["top_played"].get(key, {}), {})

    def test_zero_progress_records_item_but_no_time(self):
        item = self._boardgame("bg-zero", 0, self.day, self.day)

        stats = self._day_stats()

        key = MediaTypes.BOARDGAME.value
        self.assertIn(str(item.id), stats["items"][key])
        self.assertEqual(stats["totals"]["minutes_by_type"].get(key, 0), 0)
        self.assertEqual(stats["daily_minutes_by_type"].get(key, 0), 0)


class StatisticsAudiobookDayTests(TestCase):
    """Pages and listening minutes are counted apart, never summed."""

    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="stats-audiobook-user",
            password="secret123",
        )
        self.day = timezone.make_aware(datetime(2025, 3, 10, 12))

    def tearDown(self):
        cache.clear()

    def _book(self, key, fmt, progress, *, end=None):
        item = Item.objects.create(
            media_id=key,
            source=Sources.MANUAL.value,
            media_type=MediaTypes.BOOK.value,
            title=key,
            format=fmt,
        )
        return Book.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=progress,
            start_date=end or self.day,
            end_date=end or self.day,
        )

    def test_audiobook_minutes_do_not_become_pages(self):
        self._book("ebook", "ebook", 120)
        audio = self._book("audio", "audiobook", 300)
        BookListeningSession.objects.create(
            book=audio, session_id="s1", started_at=self.day, minutes=84,
        )

        stats = statistics_cache.build_stats_for_day(self.user.id, self.day.date())

        key = MediaTypes.BOOK.value
        self.assertEqual(stats["daily_minutes_by_type"][key], 120)
        self.assertEqual(stats["totals"]["listening_minutes"], 84)

    def test_listening_only_day_is_active(self):
        audio = self._book("audio2", "audiobook", 300, end=self.day - timedelta(days=9))
        BookListeningSession.objects.create(
            book=audio, session_id="s1", started_at=self.day, minutes=47,
        )

        stats = statistics_cache.build_stats_for_day(self.user.id, self.day.date())

        self.assertEqual(stats["totals"]["listening_minutes"], 47)
        self.assertGreaterEqual(stats["activity"]["count"], 1)

    def test_saving_a_session_marks_its_statistics_day_dirty(self):
        from app.models import StatisticsDirtyDay

        audio = self._book("audio3", "audiobook", 10)
        StatisticsDirtyDay.objects.all().delete()

        BookListeningSession.objects.create(
            book=audio, session_id="s1", started_at=self.day, minutes=30,
        )

        self.assertTrue(
            StatisticsDirtyDay.objects.filter(
                user=self.user, day=self.day.date(),
            ).exists(),
        )

    def test_legacy_consumption_keeps_pages_and_listening_apart(self):
        from app import statistics as stats_module

        self._book("ebook2", "ebook", 120)
        audio = self._book("audio4", "audiobook", 300)
        BookListeningSession.objects.create(
            book=audio, session_id="s1", started_at=self.day, minutes=84,
        )

        result = stats_module.get_reading_consumption_stats(
            {MediaTypes.BOOK.value: Book.objects.filter(user=self.user)},
            None,
            None,
            MediaTypes.BOOK.value,
        )

        self.assertEqual(result["units"]["total"], 120)
        self.assertEqual(result["listening"]["minutes"], 84)
        self.assertEqual(result["listening"]["formatted"], "1h 24min")


class StatisticsMusicDayTests(TestCase):
    """Music plays count once, with runtime from the track and rollups."""

    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="stats-music-user",
            password="secret123",
        )
        self.day = timezone.make_aware(datetime(2025, 1, 15, 12))
        self.artist = Artist.objects.create(name="Day Artist", genres=["rock"])
        self.album = Album.objects.create(title="Day Album", artist=self.artist)

    def tearDown(self):
        cache.clear()

    def _music(self, key, duration_ms):
        track = Track.objects.create(
            album=self.album, title=key, track_number=1, duration_ms=duration_ms
        )
        item = Item.objects.create(
            media_id=key,
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MUSIC.value,
            title=key,
        )
        return Music.objects.create(
            item=item,
            user=self.user,
            album=self.album,
            artist=self.artist,
            track=track,
            status=Status.COMPLETED.value,
            progress=1,
            start_date=self.day,
            end_date=self.day,
        )

    def test_play_adds_runtime_play_hour_and_rollups(self):
        music = self._music("song", 180000)

        stats = statistics_cache.build_stats_for_day(self.user.id, self.day.date())

        key = MediaTypes.MUSIC.value
        self.assertEqual(stats["totals"]["minutes_by_type"][key], 3)
        self.assertEqual(stats["totals"]["plays_by_type"][key], 1)
        self.assertEqual(stats["daily_minutes_by_type"][key], 3)
        self.assertEqual(stats["hour_counts"][key], {"12": 1})
        self.assertEqual(stats["top_played"][key][str(music.item_id)]["plays"], 1)
        rollups = stats["music"]
        self.assertEqual(rollups["tracks"][str(music.id)]["minutes"], 3)
        self.assertEqual(rollups["artists"][str(self.artist.id)]["plays"], 1)
        self.assertEqual(rollups["albums"][str(self.album.id)]["minutes"], 3)
        self.assertEqual(rollups["genres"]["Rock"]["plays"], 1)

    def test_play_without_runtime_counts_play_but_no_minutes(self):
        self._music("silent", 0)

        stats = statistics_cache.build_stats_for_day(self.user.id, self.day.date())

        key = MediaTypes.MUSIC.value
        self.assertEqual(stats["totals"]["plays_by_type"][key], 1)
        self.assertEqual(stats["totals"]["minutes_by_type"].get(key, 0), 0)
        self.assertEqual(stats["top_played"].get(key, {}), {})


class StatisticsPodcastDayTests(TestCase):
    """Podcast plays count once, with runtime from the episode and show rollups."""

    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="stats-podcast-user",
            password="secret123",
        )
        self.day = timezone.make_aware(datetime(2025, 1, 15, 12))
        self.show = PodcastShow.objects.create(
            podcast_uuid="show-uuid-1", title="Day Show", slug="day-show"
        )

    def tearDown(self):
        cache.clear()

    def _podcast(self, key, duration_seconds, *, with_episode=True, progress=0):
        item = Item.objects.create(
            media_id=key,
            source=Sources.MANUAL.value,
            media_type=MediaTypes.PODCAST.value,
            title=key,
        )
        episode = (
            PodcastEpisode.objects.create(
                show=self.show,
                episode_uuid=f"ep-{key}",
                title=f"Episode {key}",
                duration=duration_seconds,
            )
            if with_episode
            else None
        )
        return Podcast.objects.create(
            item=item,
            user=self.user,
            show=self.show if with_episode else None,
            episode=episode,
            status=Status.COMPLETED.value,
            progress=progress,
            start_date=self.day,
            end_date=self.day,
        )

    def _day_stats(self):
        return statistics_cache.build_stats_for_day(self.user.id, self.day.date())

    def test_play_adds_runtime_play_hour_and_rollups(self):
        podcast = self._podcast("pod", 1800)

        stats = self._day_stats()

        key = MediaTypes.PODCAST.value
        self.assertEqual(stats["totals"]["minutes_by_type"][key], 30)
        self.assertEqual(stats["totals"]["plays_by_type"][key], 1)
        self.assertEqual(stats["daily_minutes_by_type"][key], 30)
        self.assertEqual(stats["hour_counts"][key], {"12": 1})
        self.assertIn(str(podcast.item_id), stats["items"][key])
        rollups = stats["podcast"]
        self.assertEqual(rollups["shows"][str(self.show.id)]["plays"], 1)
        self.assertEqual(rollups["shows"][str(self.show.id)]["title"], "Day Show")
        episode_stats = rollups["episodes"][str(podcast.episode_id)]
        self.assertEqual(episode_stats["title"], f"Episode {podcast.item.title}")
        self.assertEqual(episode_stats["duration_seconds"], 1800)

    def test_play_without_runtime_is_skipped(self):
        self._podcast("silent", 0, with_episode=False)

        stats = self._day_stats()

        key = MediaTypes.PODCAST.value
        self.assertEqual(stats["totals"]["plays_by_type"].get(key, 0), 0)
        self.assertEqual(stats["totals"]["minutes_by_type"].get(key, 0), 0)

    def test_progress_stands_in_when_episode_runtime_is_unknown(self):
        podcast = self._podcast("progress", 0, with_episode=False, progress=12)

        stats = self._day_stats()

        key = MediaTypes.PODCAST.value
        self.assertEqual(stats["totals"]["minutes_by_type"][key], 12)
        self.assertEqual(stats["totals"]["plays_by_type"][key], 1)
        # Without a show, the rollup is keyed by the entry and titled from the item.
        self.assertEqual(
            stats["podcast"]["shows"][str(podcast.id)]["title"], podcast.item.title
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PORTRAIT_POSTER = "https://image.tmdb.org/t/p/w500/poster.jpg"
BACKDROP_URL = "https://image.tmdb.org/t/p/w1280/backdrop.jpg"
IGDB_BACKDROP_URL = "https://images.igdb.com/igdb/image/upload/t_screenshot_big/abc.jpg"


def _tv_item_dict(media_id="1396", title="Breaking Bad"):
    """Serialised TMDB TV show dict (as stored in history day cache)."""
    return {
        "id": 1,
        "media_type": MediaTypes.TV.value,
        "media_id": str(media_id),
        "source": Sources.TMDB.value,
        "title": title,
    }


def _movie_item_dict(
    media_id="1865", title="Pirates of the Caribbean: On Stranger Tides"
):
    """Serialised TMDB movie dict."""
    return {
        "id": 2,
        "media_type": MediaTypes.MOVIE.value,
        "media_id": str(media_id),
        "source": Sources.TMDB.value,
        "title": title,
    }


def _episode_item_dict(media_id="1399", title="Game of Thrones S01E01"):
    """Serialised TMDB episode dict. media_id matches the parent TV show."""
    return {
        "id": 3,
        "media_type": MediaTypes.EPISODE.value,
        "media_id": str(media_id),
        "source": Sources.TMDB.value,
        "title": title,
    }


def _anime_item_dict(media_id="94954", title="Devil May Cry"):
    """Serialised TMDB anime dict.

    Anime has its own MediaTypes.ANIME ("anime") value distinct from "tv".
    When sourced from TMDB the item carries source=tmdb + media_type=anime,
    and the TMDB API still exposes it via the /tv/{id} endpoint.
    """
    return {
        "id": 4,
        "media_type": MediaTypes.ANIME.value,
        "media_id": str(media_id),
        "source": Sources.TMDB.value,
        "title": title,
    }


def _mal_anime_item_dict(media_id="40591", title="Jujutsu Kaisen"):
    """Serialised MAL-sourced anime dict (no backdrop available)."""
    return {
        "id": 6,
        "media_type": MediaTypes.ANIME.value,
        "media_id": str(media_id),
        "source": Sources.MAL.value,
        "title": title,
    }


def _tvdb_tv_item_dict(tvdb_id="121361", tmdb_id="1399", title="Game of Thrones"):
    """
    Serialised TVDB TV show dict with a TMDB cross-reference.

    TVDB items carry provider_external_ids populated by the TVDB provider
    when it finds a TMDB counterpart. A user who added GoT / Breaking Bad /
    HIMYM / Murdock Mysteries via TVDB ends up with source=tvdb, and the
    only way to get a backdrop is to use the tmdb_id cross-reference.
    """
    return {
        "id": 7,
        "media_type": MediaTypes.TV.value,
        "media_id": str(tvdb_id),
        "source": Sources.TVDB.value,
        "title": title,
        "provider_external_ids": {"tmdb_id": tmdb_id, "tvdb_id": tvdb_id},
    }


def _tvdb_episode_item_dict(tvdb_id="121361", tmdb_id="1399", title="GoT S01E01"):
    """Serialised TVDB episode — provider_external_ids carries show-level tmdb_id."""
    return {
        "id": 8,
        "media_type": MediaTypes.EPISODE.value,
        "media_id": str(tvdb_id),
        "source": Sources.TVDB.value,
        "title": title,
        "provider_external_ids": {"tmdb_id": tmdb_id, "tvdb_id": tvdb_id},
    }


def _game_item_dict(media_id="9630", title="Devil May Cry 5"):
    """Serialised IGDB game dict."""
    return {
        "id": 5,
        "media_type": MediaTypes.GAME.value,
        "media_id": str(media_id),
        "source": Sources.IGDB.value,
        "title": title,
    }


def _highlight_entry(item_dict, image=PORTRAIT_POSTER):
    """Minimal history highlight payload as produced by _history_entry_card_payload."""
    return {
        "item": item_dict,
        "title": item_dict.get("title", ""),
        "media_type": item_dict.get("media_type"),
        "image": image,
        "played_at": None,
    }


# ---------------------------------------------------------------------------
# _get_horizontal_history_image
# ---------------------------------------------------------------------------


class GetHorizontalHistoryImageTests(TestCase):
    """
    Tests for statistics_cache._get_horizontal_history_image.

    Every test mocks backdrops.get_tmdb_backdrop / _get_igdb_backdrop so no
    real network calls are made. The Redis cache is cleared before each test so
    _cached_horizontal_backdrop always starts cold unless explicitly primed.
    """

    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    # ------------------------------------------------------------------
    # None / missing item
    # ------------------------------------------------------------------

    def test_none_item_returns_fallback(self):
        result = statistics_cache._get_horizontal_history_image(None, PORTRAIT_POSTER)
        self.assertEqual(result, PORTRAIT_POSTER)

    def test_none_item_and_no_fallback_returns_img_none(self):
        result = statistics_cache._get_horizontal_history_image(None, "")
        self.assertEqual(result, settings.IMG_NONE)

    def test_dict_item_missing_source_returns_fallback(self):
        """Items without a provider (e.g. manual entries) fall back to the poster."""
        item = {"media_type": MediaTypes.MOVIE.value, "media_id": "999"}
        result = statistics_cache._get_horizontal_history_image(item, PORTRAIT_POSTER)
        self.assertEqual(result, PORTRAIT_POSTER)

    def test_dict_item_missing_media_id_returns_fallback(self):
        item = {"media_type": MediaTypes.MOVIE.value, "source": Sources.TMDB.value}
        result = statistics_cache._get_horizontal_history_image(item, PORTRAIT_POSTER)
        self.assertEqual(result, PORTRAIT_POSTER)

    # ------------------------------------------------------------------
    # Cached backdrop already in Redis — no network call needed
    # ------------------------------------------------------------------

    @patch("app.backdrops.get_tmdb_backdrop")
    def test_tv_show_uses_redis_cache_without_network_call(self, mock_backdrop):
        item = _tv_item_dict()  # Breaking Bad, media_id=1396
        cache.set("tmdb_backdrop_tv_1396", BACKDROP_URL, 60)

        result = statistics_cache._get_horizontal_history_image(item, PORTRAIT_POSTER)

        self.assertEqual(result, BACKDROP_URL)
        mock_backdrop.assert_not_called()

    @patch("app.backdrops.get_tmdb_backdrop")
    def test_movie_uses_redis_cache_without_network_call(self, mock_backdrop):
        item = _movie_item_dict()  # On Stranger Tides, media_id=1865
        cache.set("tmdb_backdrop_movie_1865", BACKDROP_URL, 60)

        result = statistics_cache._get_horizontal_history_image(item, PORTRAIT_POSTER)

        self.assertEqual(result, BACKDROP_URL)
        mock_backdrop.assert_not_called()

    # ------------------------------------------------------------------
    # Network fetch — TMDB TV (Breaking Bad)
    # ------------------------------------------------------------------

    @patch("app.backdrops.get_tmdb_backdrop", return_value=BACKDROP_URL)
    def test_tv_show_fetches_tmdb_backdrop_when_cache_cold(self, mock_backdrop):
        """Breaking Bad (tv, 1396): cold Redis → should call TMDB and return backdrop."""
        item = _tv_item_dict(media_id="1396", title="Breaking Bad")

        result = statistics_cache._get_horizontal_history_image(
            item, PORTRAIT_POSTER, allow_network=True
        )

        self.assertEqual(result, BACKDROP_URL)
        mock_backdrop.assert_called_once_with(MediaTypes.TV.value, "1396")

    @patch("app.backdrops.get_tmdb_backdrop", return_value=BACKDROP_URL)
    def test_anime_tmdb_fetches_backdrop_via_tv_endpoint(self, mock_backdrop):
        """
        Devil May Cry (media_type=anime, source=tmdb): cold Redis → backdrop fetched.

        This is the core edge case from issue #211. MediaTypes.ANIME ("anime") is
        distinct from MediaTypes.TV ("tv"), so without the fix the two TMDB blocks
        (`media_type in ("movie","tv")` and the episode/season block) both miss it
        and fall through to the portrait poster.
        """
        item = _anime_item_dict(media_id="94954", title="Devil May Cry")

        result = statistics_cache._get_horizontal_history_image(
            item, PORTRAIT_POSTER, allow_network=True
        )

        self.assertEqual(result, BACKDROP_URL)
        # Anime from TMDB must use the /tv/{id} endpoint
        mock_backdrop.assert_called_once_with(MediaTypes.TV.value, "94954")

    @patch("app.backdrops.get_tmdb_backdrop")
    def test_anime_tmdb_redis_cache_hit_skips_network(self, mock_backdrop):
        """TMDB anime whose backdrop is already in Redis must not re-fetch."""
        item = _anime_item_dict(media_id="94954")
        # Backdrops for anime are stored under the "tv" key (same endpoint)
        cache.set("tmdb_backdrop_tv_94954", BACKDROP_URL, 60)

        result = statistics_cache._get_horizontal_history_image(item, PORTRAIT_POSTER)

        self.assertEqual(result, BACKDROP_URL)
        mock_backdrop.assert_not_called()

    @patch("app.backdrops.get_tmdb_backdrop")
    def test_anime_mal_source_falls_back_to_portrait(self, mock_backdrop):
        """
        MAL-sourced anime has no backdrop provider, so the portrait poster is
        the correct fallback. No TMDB call should be attempted.
        """
        item = _mal_anime_item_dict()

        result = statistics_cache._get_horizontal_history_image(
            item, PORTRAIT_POSTER, allow_network=True
        )

        self.assertEqual(result, PORTRAIT_POSTER)
        mock_backdrop.assert_not_called()

    # ------------------------------------------------------------------
    # Network fetch — TMDB movie (Pirates of the Caribbean: On Stranger Tides)
    # ------------------------------------------------------------------

    @patch("app.backdrops.get_tmdb_backdrop", return_value=BACKDROP_URL)
    def test_movie_fetches_tmdb_backdrop_when_cache_cold(self, mock_backdrop):
        """On Stranger Tides (movie, 1865): cold Redis → should call TMDB."""
        item = _movie_item_dict(
            media_id="1865",
            title="Pirates of the Caribbean: On Stranger Tides",
        )

        result = statistics_cache._get_horizontal_history_image(
            item, PORTRAIT_POSTER, allow_network=True
        )

        self.assertEqual(result, BACKDROP_URL)
        mock_backdrop.assert_called_once_with(MediaTypes.MOVIE.value, "1865")

    # ------------------------------------------------------------------
    # Network fetch — TMDB episode (Game of Thrones)
    # ------------------------------------------------------------------

    @patch("app.backdrops.get_tmdb_backdrop", return_value=BACKDROP_URL)
    def test_episode_fetches_show_backdrop_via_tv_path(self, mock_backdrop):
        """
        Game of Thrones episode (media_type=episode, media_id=1399).
        Episodes share the parent show's media_id and should use the TV backdrop path.
        """
        item = _episode_item_dict(media_id="1399", title="Game of Thrones S01E01")

        result = statistics_cache._get_horizontal_history_image(
            item, PORTRAIT_POSTER, allow_network=True
        )

        self.assertEqual(result, BACKDROP_URL)
        # Must request the TV backdrop, not an episode-specific path
        mock_backdrop.assert_called_once_with(MediaTypes.TV.value, "1399")

    @patch("app.backdrops.get_tmdb_backdrop", return_value=BACKDROP_URL)
    def test_season_fetches_show_backdrop_via_tv_path(self, mock_backdrop):
        """Season items (media_type=season) also resolve via the TV backdrop path."""
        item = {
            "media_type": MediaTypes.SEASON.value,
            "media_id": "1396",
            "source": Sources.TMDB.value,
            "title": "Breaking Bad S1",
        }

        result = statistics_cache._get_horizontal_history_image(
            item, PORTRAIT_POSTER, allow_network=True
        )

        self.assertEqual(result, BACKDROP_URL)
        mock_backdrop.assert_called_once_with(MediaTypes.TV.value, "1396")

    # ------------------------------------------------------------------
    # Network fetch — IGDB game
    # ------------------------------------------------------------------

    @patch("lists.models.CustomList._get_igdb_backdrop", return_value=IGDB_BACKDROP_URL)
    def test_game_fetches_igdb_backdrop(self, mock_igdb):
        item = _game_item_dict(media_id="9630")

        result = statistics_cache._get_horizontal_history_image(
            item, PORTRAIT_POSTER, allow_network=True
        )

        self.assertEqual(result, IGDB_BACKDROP_URL)
        mock_igdb.assert_called_once_with("9630")

    # ------------------------------------------------------------------
    # Media types that have no backdrop path — should fall back gracefully
    # ------------------------------------------------------------------

    @patch("app.backdrops.get_tmdb_backdrop")
    def test_music_media_type_falls_back_to_portrait(self, mock_backdrop):
        """Music has no backdrop source; portrait poster should be returned."""
        item = {
            "media_type": MediaTypes.MUSIC.value,
            "media_id": "12345",
            "source": Sources.TMDB.value,
            "title": "Some Album",
        }

        result = statistics_cache._get_horizontal_history_image(
            item, PORTRAIT_POSTER, allow_network=True
        )

        self.assertEqual(result, PORTRAIT_POSTER)
        mock_backdrop.assert_not_called()

    # ------------------------------------------------------------------
    # allow_network=False — must NOT make network calls
    # ------------------------------------------------------------------

    @patch("app.backdrops.get_tmdb_backdrop")
    def test_allow_network_false_returns_fallback_without_network_call(
        self, mock_backdrop
    ):
        """
        When allow_network=False and Redis is cold, the portrait poster must be
        returned without any TMDB call. This is the pre-fix behaviour for the
        serve-time normaliser — verified here to confirm it was always a dead end.
        """
        item = _tv_item_dict(media_id="1396", title="Breaking Bad")

        result = statistics_cache._get_horizontal_history_image(
            item, PORTRAIT_POSTER, allow_network=False
        )

        self.assertEqual(result, PORTRAIT_POSTER)
        mock_backdrop.assert_not_called()

    # ------------------------------------------------------------------
    # TMDB returns IMG_NONE — fall back to portrait
    # ------------------------------------------------------------------

    @patch("app.backdrops.get_tmdb_backdrop", return_value=settings.IMG_NONE)
    def test_tmdb_returns_img_none_falls_back_to_portrait(self, mock_backdrop):
        item = _tv_item_dict()

        result = statistics_cache._get_horizontal_history_image(
            item, PORTRAIT_POSTER, allow_network=True
        )

        self.assertEqual(result, PORTRAIT_POSTER)

    # ------------------------------------------------------------------
    # TMDB raises an exception — fall back gracefully
    # ------------------------------------------------------------------

    @patch(
        "app.backdrops.get_tmdb_backdrop",
        side_effect=Exception("network error"),
    )
    def test_tmdb_exception_falls_back_to_portrait(self, mock_backdrop):
        item = _tv_item_dict()

        result = statistics_cache._get_horizontal_history_image(
            item, PORTRAIT_POSTER, allow_network=True
        )

        self.assertEqual(result, PORTRAIT_POSTER)

    # ------------------------------------------------------------------
    # Model instance (not dict) — Breaking Bad as a real Item object
    # ------------------------------------------------------------------

    @patch("app.backdrops.get_tmdb_backdrop", return_value=BACKDROP_URL)
    def test_model_instance_tv_fetches_backdrop(self, mock_backdrop):
        """_get_horizontal_history_image must work with Django Item instances too."""
        item = Item.objects.create(
            media_id="1396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Breaking Bad",
            image=PORTRAIT_POSTER,
        )

        result = statistics_cache._get_horizontal_history_image(
            item, PORTRAIT_POSTER, allow_network=True
        )

        self.assertEqual(result, BACKDROP_URL)
        mock_backdrop.assert_called_once_with(MediaTypes.TV.value, "1396")

    # ------------------------------------------------------------------
    # TVDB source — cross-reference to TMDB via provider_external_ids
    # ------------------------------------------------------------------

    @patch("app.backdrops.get_tmdb_backdrop", return_value=BACKDROP_URL)
    def test_tvdb_tv_show_uses_tmdb_cross_reference(self, mock_backdrop):
        """
        Game of Thrones / Breaking Bad / HIMYM added via TVDB:
        source=tvdb, no TMDB backdrop path unless we use provider_external_ids.

        The TVDB provider stores a tmdb_id in provider_external_ids when it
        finds a TMDB counterpart. _get_horizontal_history_image must use that
        to fetch the TMDB backdrop.
        """
        item = _tvdb_tv_item_dict(
            tvdb_id="121361", tmdb_id="1399", title="Game of Thrones"
        )

        result = statistics_cache._get_horizontal_history_image(
            item, PORTRAIT_POSTER, allow_network=True
        )

        self.assertEqual(result, BACKDROP_URL)
        mock_backdrop.assert_called_once_with(MediaTypes.TV.value, "1399")

    @patch("app.backdrops.get_tmdb_backdrop", return_value=BACKDROP_URL)
    def test_tvdb_episode_uses_tmdb_cross_reference(self, mock_backdrop):
        """TVDB episodes carry the show-level provider_external_ids tmdb_id."""
        item = _tvdb_episode_item_dict(tvdb_id="121361", tmdb_id="1399")

        result = statistics_cache._get_horizontal_history_image(
            item, PORTRAIT_POSTER, allow_network=True
        )

        self.assertEqual(result, BACKDROP_URL)
        mock_backdrop.assert_called_once_with(MediaTypes.TV.value, "1399")

    @patch("app.backdrops.get_tmdb_backdrop")
    def test_tvdb_tv_redis_cache_hit_skips_network(self, mock_backdrop):
        """TVDB show whose TMDB backdrop is already cached avoids a network call."""
        item = _tvdb_tv_item_dict(tvdb_id="121361", tmdb_id="1399")
        cache.set("tmdb_backdrop_tv_1399", BACKDROP_URL, 60)

        result = statistics_cache._get_horizontal_history_image(item, PORTRAIT_POSTER)

        self.assertEqual(result, BACKDROP_URL)
        mock_backdrop.assert_not_called()

    @patch("app.backdrops.get_tmdb_backdrop")
    def test_tvdb_tv_without_tmdb_cross_reference_falls_back_to_portrait(
        self, mock_backdrop
    ):
        """
        TVDB show with no tmdb_id in provider_external_ids (e.g. obscure show
        not indexed on TMDB) must fall back to the portrait poster gracefully.
        """
        item = {
            "id": 9,
            "media_type": MediaTypes.TV.value,
            "media_id": "99999",
            "source": Sources.TVDB.value,
            "title": "Obscure Local Show",
            "provider_external_ids": {"tvdb_id": "99999"},  # no tmdb_id
        }

        result = statistics_cache._get_horizontal_history_image(
            item, PORTRAIT_POSTER, allow_network=True
        )

        self.assertEqual(result, PORTRAIT_POSTER)
        mock_backdrop.assert_not_called()

    @patch("app.backdrops.get_tmdb_backdrop")
    def test_tvdb_tv_missing_provider_external_ids_falls_back_to_portrait(
        self, mock_backdrop
    ):
        """Old serialised history entries that pre-date the provider_external_ids
        field must not crash and must fall back to the portrait poster.
        """
        item = {
            "id": 10,
            "media_type": MediaTypes.TV.value,
            "media_id": "121361",
            "source": Sources.TVDB.value,
            "title": "Game of Thrones",
            # provider_external_ids absent (old cache entry)
        }

        result = statistics_cache._get_horizontal_history_image(
            item, PORTRAIT_POSTER, allow_network=True
        )

        self.assertEqual(result, PORTRAIT_POSTER)
        mock_backdrop.assert_not_called()


# ---------------------------------------------------------------------------
# normalize_highlight_images  (issue #211, made cache-only by issue #1249)
# ---------------------------------------------------------------------------


def _fill_tmdb_backdrop_cache(media_type, media_id):
    """Stand-in for backdrops.get_tmdb_backdrop that caches like the real one."""
    cache.set(f"tmdb_backdrop_{media_type}_{media_id}", BACKDROP_URL, 60)
    return BACKDROP_URL


def _portrait_highlights():
    return {
        "first_play": _highlight_entry(_tv_item_dict(), image=PORTRAIT_POSTER),
        "last_play": _highlight_entry(_movie_item_dict(), image=PORTRAIT_POSTER),
        "today_card": {
            "entry": _highlight_entry(_episode_item_dict(), image=PORTRAIT_POSTER),
        },
        "today_month": 5,
        "today_day": 20,
    }


class NormalizeHistoryHighlightImagesTests(TestCase):
    """
    Tests for statistics_cache.normalize_highlight_images.

    It runs on every stats page serve and before a rebuilt payload is
    published on the interactive worker, so it must never call a provider
    (#1249). A cold backdrop cache is repaired by a background warm, and the
    next serve picks the landscape artwork up from Redis (#211).
    """

    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    @patch("app.tasks_backdrops.warm_backdrops_task.apply_async")
    @patch("app.backdrops.get_tmdb_backdrop", return_value=BACKDROP_URL)
    def test_serve_never_calls_the_provider(self, mock_backdrop, mock_warm):
        """Core regression test for issue #1249: a cold cache means no network."""
        data = {"history_highlights": _portrait_highlights()}

        statistics_cache.normalize_highlight_images(data)

        mock_backdrop.assert_not_called()
        highlights = data["history_highlights"]
        self.assertEqual(highlights["first_play"]["image"], PORTRAIT_POSTER)
        self.assertEqual(highlights["today_card"]["entry"]["image"], PORTRAIT_POSTER)
        # One background task carries every missing backdrop.
        mock_warm.assert_called_once()
        identities = mock_warm.call_args.kwargs["args"][0]
        self.assertEqual(
            sorted((i["media_type"], i["media_id"]) for i in identities),
            [
                (MediaTypes.EPISODE.value, "1399"),
                (MediaTypes.MOVIE.value, "1865"),
                (MediaTypes.TV.value, "1396"),
            ],
        )

    @patch(
        "app.backdrops.get_tmdb_backdrop",
        side_effect=_fill_tmdb_backdrop_cache,
    )
    def test_portrait_poster_recovers_on_next_serve(self, mock_backdrop):
        """
        Issue #211 recovery, now off the request path.

        The stats cache stores portrait posters and Redis has no backdrops. The
        first serve queues the warm (run inline by eager Celery here); the next
        serve swaps in the backdrop from Redis without calling the provider.
        """
        statistics_cache.normalize_highlight_images(
            {"history_highlights": _portrait_highlights()}
        )
        self.assertEqual(mock_backdrop.call_count, 3)  # the background warm

        mock_backdrop.reset_mock()
        data = {"history_highlights": _portrait_highlights()}
        statistics_cache.normalize_highlight_images(data)

        mock_backdrop.assert_not_called()
        highlights = data["history_highlights"]
        for entry in (
            highlights["first_play"],
            highlights["last_play"],
            highlights["today_card"]["entry"],
        ):
            self.assertEqual(entry["image"], BACKDROP_URL)
            self.assertTrue(entry["image_is_backdrop"])

    @patch("app.tasks_backdrops.warm_backdrops_task.apply_async")
    def test_repeat_serves_queue_the_warm_once(self, mock_warm):
        for _ in range(3):
            statistics_cache.normalize_highlight_images(
                {"history_highlights": _portrait_highlights()}
            )

        mock_warm.assert_called_once()

    @patch("app.tasks_backdrops.warm_backdrops_task.apply_async")
    @patch("app.backdrops.cached_backdrop")
    def test_marked_backdrop_is_served_without_any_lookup(self, mock_cached, mock_warm):
        """A payload built after #1249 keeps its backdrop after Redis expiry."""
        entry = _highlight_entry(_tv_item_dict(), image=BACKDROP_URL)
        entry["image_is_backdrop"] = True
        data = {"history_highlights": {"first_play": entry}}

        statistics_cache.normalize_highlight_images(data)

        self.assertEqual(entry["image"], BACKDROP_URL)
        mock_cached.assert_not_called()
        mock_warm.assert_not_called()

    @patch("app.tasks_backdrops.warm_backdrops_task.apply_async")
    def test_per_type_highlights_are_upgraded(self, mock_warm):
        cache.set("tmdb_backdrop_tv_1396", BACKDROP_URL, 60)
        entry = _highlight_entry(_tv_item_dict(media_id="1396"))
        data = {"history_highlights_by_type": {"tv": {"first_play": entry}}}

        statistics_cache.normalize_highlight_images(data)

        self.assertEqual(entry["image"], BACKDROP_URL)
        self.assertTrue(entry["image_is_backdrop"])
        mock_warm.assert_not_called()

    @patch("app.tasks_backdrops.warm_backdrops_task.apply_async")
    def test_items_without_backdrops_queue_nothing(self, mock_warm):
        podcast = {
            "media_type": MediaTypes.PODCAST.value,
            "media_id": "p1",
            "source": Sources.GPODDER.value,
        }
        data = {
            "history_highlights": {
                "first_play": _highlight_entry(podcast),
                "last_play": {"item": None, "image": PORTRAIT_POSTER},
            }
        }

        statistics_cache.normalize_highlight_images(data)

        mock_warm.assert_not_called()
        self.assertEqual(
            data["history_highlights"]["last_play"]["image"], PORTRAIT_POSTER
        )

    def test_non_dict_highlights_returns_without_error(self):
        """Passing None or non-dict must be a no-op."""
        for value in (None, "not-a-dict", []):
            statistics_cache.normalize_highlight_images(value)
            statistics_cache._normalize_history_highlight_images(value)
            statistics_cache._normalize_history_highlights_by_type(value)

    @patch("app.backdrops.get_tmdb_backdrop", return_value=BACKDROP_URL)
    def test_backdrop_already_stored_in_cache_is_reused(self, mock_backdrop):
        """A warm Redis backdrop (e.g. from a Lists Hub visit) is used directly."""
        cache.set("tmdb_backdrop_tv_1396", BACKDROP_URL, 60)
        highlights = {"first_play": _highlight_entry(_tv_item_dict(media_id="1396"))}

        statistics_cache._normalize_history_highlight_images(highlights)

        self.assertEqual(highlights["first_play"]["image"], BACKDROP_URL)
        mock_backdrop.assert_not_called()


class HighlightArtworkRequestPathTests(TestCase):
    """End-to-end: building and serving highlights never calls a provider."""

    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="stats-highlight-artwork",
            password="secret123",
        )

    def tearDown(self):
        cache.clear()

    @patch("app.tasks_backdrops.warm_backdrops_task.apply_async")
    @patch("app.backdrops.get_tmdb_backdrop", return_value=BACKDROP_URL)
    def test_warm_statistics_cache_serve_makes_no_provider_call(
        self, mock_backdrop, mock_warm
    ):
        data = statistics_cache._get_empty_statistics_data()
        data["history_highlights"] = _portrait_highlights()
        statistics_cache.cache_statistics_data(self.user.id, "Last 30 Days", data)

        start, end = statistics_refresh._get_predefined_range_dates("Last 30 Days")
        served = statistics_cache.get_statistics_data(
            self.user, start, end, range_name="Last 30 Days"
        )

        mock_backdrop.assert_not_called()
        mock_warm.assert_called_once()
        self.assertEqual(
            served["history_highlights"]["first_play"]["image"], PORTRAIT_POSTER
        )

    @patch("app.tasks_backdrops.warm_backdrops_task.apply_async")
    def test_release_candidates_are_not_resolved_before_selection(self, _mock_warm):
        """Only the chosen "Today in history" card is ever given a backdrop."""
        release = timezone.now().replace(year=2001)
        for index in range(3):
            item = Item.objects.create(
                media_id=f"98{index}",
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title=f"Released today {index}",
                image=PORTRAIT_POSTER,
                release_datetime=release,
            )
            Movie.objects.create(
                user=self.user, item=item, status=Status.COMPLETED.value
            )

        with (
            patch("app.backdrops.cached_backdrop") as mock_cached,
            patch("app.backdrops.resolve_backdrop") as mock_resolve,
        ):
            entry, year = statistics_cache._get_today_release_entry(self.user)

        self.assertEqual(year, 2001)
        self.assertEqual(entry["image"], PORTRAIT_POSTER)
        mock_cached.assert_not_called()
        mock_resolve.assert_not_called()

    def test_today_release_candidates_are_scanned_once_per_window(self):
        """Every range and media type of a sync pass shares one library scan.

        The scan cannot use a date index (it matches a month/day across all
        years), so repeating it per range is what made range rebuilds slow.
        """
        release = timezone.now().replace(year=2001)
        item = Item.objects.create(
            media_id="release-cache-1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MOVIE.value,
            title="Released today once",
            release_datetime=release,
        )
        Movie.objects.create(
            user=self.user, item=item, status=Status.COMPLETED.value
        )

        statistics_cache._get_today_release_entry(self.user)
        with self.assertNumQueries(0):
            entry, year = statistics_cache._get_today_release_entry(self.user)

        self.assertEqual(year, 2001)
        self.assertEqual(entry["item"].pk, item.pk)


class RequestPathDayBuildTests(TestCase):
    """Custom-range statistics never build day caches inline in a request."""

    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="stats-daybuild-user",
            password="secret123",
        )
        self.start = timezone.make_aware(datetime(2024, 1, 1))
        self.end = timezone.make_aware(datetime(2024, 1, 31, 23, 59, 59))

    def tearDown(self):
        cache.clear()

    @patch("app.tasks.build_statistics_days_task.apply_async")
    @patch("app.statistics_aggregator.build_stats_for_day")
    @patch("app.statistics_cache._eager_statistics_mode", return_value=False)
    def test_minutes_by_type_schedules_background_build(
        self,
        _mock_eager,
        mock_build_day,
        mock_apply_async,
    ):
        result = statistics_cache.get_statistics_minutes_by_type(
            self.user,
            self.start,
            self.end,
            range_name="Custom",
        )

        self.assertEqual(result, {})
        mock_build_day.assert_not_called()
        mock_apply_async.assert_called_once()

    @patch("app.tasks.build_statistics_days_task.apply_async")
    @patch("app.statistics_aggregator.build_stats_for_day")
    @patch("app.statistics_cache._eager_statistics_mode", return_value=False)
    def test_schedule_is_debounced(
        self,
        _mock_eager,
        mock_build_day,
        mock_apply_async,
    ):
        for _ in range(2):
            statistics_cache.get_statistics_minutes_by_type(
                self.user,
                self.start,
                self.end,
                range_name="Custom",
            )

        mock_build_day.assert_not_called()
        mock_apply_async.assert_called_once()

    def test_eager_mode_still_builds_inline(self):
        # Eager reads build missing days through the sync's slice builder,
        # which calls the day builder from its own module.
        with patch(
            "app.statistics_day_builder.build_stats_for_day",
            return_value={"totals": {"minutes_by_type": {"movie": 60}}},
        ) as mock_build_day:
            result = statistics_cache.get_statistics_minutes_by_type(
                self.user,
                self.start,
                self.end,
                range_name="Custom",
            )

        self.assertEqual(result, {"movie": 60 * 31})
        self.assertEqual(mock_build_day.call_count, 31)


class DayCacheContractTests(SimpleTestCase):
    def test_cache_key_preserves_supported_day_formats(self):
        for day in (date(2026, 9, 5), "2026-09-05", "20260905"):
            with self.subTest(day=day):
                self.assertEqual(
                    statistics_cache._day_cache_key(42, day),
                    f"{STATISTICS_DAY_PREFIX}:42:2026-09-05",
                )
        self.assertEqual(statistics_cache._day_cache_key(42, "invalid"), "")

    def test_aware_datetime_uses_local_day(self):
        with timezone.override("America/Chicago"):
            self.assertEqual(
                _normalize_day_value(
                    datetime.fromisoformat("2026-09-05T01:00:00+00:00")
                ),
                date(2026, 9, 4),
            )

    def test_history_version_round_trips_through_cache_facade(self):
        cache.clear()
        self.addCleanup(cache.clear)
        initial = statistics_cache.get_history_version(42)
        self.assertEqual(statistics_cache.get_history_version(42), initial)
        statistics_cache._set_history_version(42, "changed")
        self.assertEqual(statistics_cache.get_history_version(42), "changed")


class PlatformBreakdownTests(SimpleTestCase):
    def test_collection_choice_wins_and_repeated_games_count_once(self):
        result = _build_platform_breakdown(
            [
                {"item_id": 1, "hours": 2},
                {"item_id": 1, "hours": 3},
                {"item_id": 2, "hours": 1},
            ],
            {1: "Steam Deck"},
            {1: {"platforms": ["PC"]}, 2: {"platforms": ["  Switch  "]}},
        )
        self.assertEqual(
            [(entry["name"], entry["games"], entry["hours"]) for entry in result],
            [("Steam Deck", 1, 5), ("Switch", 1, 1)],
        )

    def test_ambiguous_or_missing_platforms_are_omitted(self):
        self.assertEqual(
            _build_platform_breakdown(
                [{"item_id": item_id, "hours": 1} for item_id in (1, 2, 3, None)],
                {},
                {1: {"platforms": ["PC", "Switch"]}, 2: {"platforms": "PC"}},
            ),
            [],
        )


class StatisticsChartPreparationTests(SimpleTestCase):
    def test_heatmap_combines_types_and_preserves_empty_cells(self):
        counts = {
            "movie": defaultdict(lambda: defaultdict(int)),
            "tv": defaultdict(lambda: defaultdict(int)),
        }
        counts["movie"][2][13] = 2
        counts["tv"][2][13] = 3
        result = _build_weekday_hour_charts(counts)
        self.assertEqual(result["all"][2][13], 5)
        self.assertEqual(result["movie"][2][13], 2)
        self.assertEqual(result["all"][0][0], 0)
        self.assertEqual(len(result["all"]), 7)
        self.assertTrue(all(len(row) == 24 for row in result["all"]))

    def test_hour_charts_combine_elapsed_time_without_reading_units(self):
        result = _build_combined_hours_charts(
            {
                "movie": {"2026-09-05": 90},
                "tv": {"2026-09-05": 30},
                "book": {"2026-09-05": 600},
            },
            {"movie": {"13": 90}, "tv": {"13": 30}, "book": {"13": 600}},
        )
        self.assertEqual(result["by_month"]["all"]["datasets"][0]["data"][8], 2)
        self.assertEqual(result["by_time_of_day"]["all"]["datasets"][0]["data"][13], 2)
        self.assertEqual(result["by_month"]["movie"]["datasets"][0]["data"][8], 1.5)
        self.assertNotIn("book", result["by_month"])
