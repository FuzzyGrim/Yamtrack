"""Eager (test) Statistics reads build their missing days in bulk.

Without a worker, a Statistics read builds every day of its range and of the
comparison period inline. Those days now go through the sync's slice builder
(one range-wide prefetch per slice) instead of ~13 queries per day. These
tests prove the bulk build gives the same day payloads, the same response
data and the same stored day rows as the one-day-at-a-time build.
"""

from datetime import date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app import statistics_cache, statistics_day_builder
from app.models import (
    TV,
    Album,
    Anime,
    Artist,
    BoardGame,
    Book,
    BookListeningSession,
    Comic,
    Episode,
    Game,
    Item,
    Manga,
    MediaTypes,
    Movie,
    Music,
    Podcast,
    PodcastEpisode,
    PodcastShow,
    Season,
    Sources,
    Status,
    Track,
    Video,
)
from app.statistics_day_builder import _iter_day_range
from app.statistics_day_cache import _day_cache_key

ZONE = ZoneInfo("America/New_York")
# Crosses the 2023-11-05 daylight-saving change and the 2023/2024 new year.
RANGE_START = date(2023, 10, 28)
RANGE_END = date(2024, 1, 6)

# One empty-library GET /statistics, default range plus its comparison
# period (730 days). Measured 527 queries after the bulk build (5,425
# before); custom ranges measured 456 (9,000 before).
EMPTY_STATISTICS_REQUEST_QUERY_BUDGET = 650


def _local(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ZONE)


def _item(media_type, media_id, **extra):
    return Item.objects.get_or_create(
        media_id=media_id,
        source=Sources.MANUAL.value,
        media_type=media_type,
        defaults={
            "title": f"{media_type} {media_id}",
            "image": "http://example.com/x.jpg",
            "runtime_minutes": 42,
            "genres": ["Drama"],
            **extra,
        },
    )[0]


def _without_build_time(payloads, *, fence=False):
    """Day payloads minus ``computed_at``, the wall-clock time of the build.

    With ``fence``, also minus ``history_version``: clearing the cache
    between two runs mints a new version, so each run stamps its own.
    """
    dropped = {"computed_at", "history_version"} if fence else {"computed_at"}
    return {
        day: {key: value for key, value in payload.items() if key not in dropped}
        for day, payload in payloads.items()
    }


def _per_day_build():
    """The previous eager path: each missing day built alone."""
    return patch.object(statistics_cache, "_eager_day_builder", lambda _user: None)


def _range_dt(day, *, end=False):
    return _local(day, 23, 59) if end else _local(day, 0)


class EagerDayBuildEquivalenceTests(TestCase):
    """Bulk and per-day builds agree for every source the day builder reads."""

    @classmethod
    def setUpTestData(cls):
        user = cls.user = get_user_model().objects.create_user(
            username="eager-days", password="12345",
        )
        done = Status.COMPLETED.value
        # Active days; everything else in the range stays empty.
        nov_dst = date(2023, 11, 5)
        nye = date(2023, 12, 31)
        days = [date(2023, 10, 30), nov_dst, date(2023, 11, 20), nye, date(2024, 1, 2)]
        cls.late_night = nye
        tv = TV.objects.create(
            item=_item(MediaTypes.TV.value, "tv"), user=user,
            status=Status.IN_PROGRESS.value,
        )
        season = Season.objects.create(
            item=_item(MediaTypes.SEASON.value, "tv", season_number=1),
            user=user, related_tv=tv, status=Status.IN_PROGRESS.value,
        )
        artist = Artist.objects.create(name="Artist")
        album = Album.objects.create(title="Album", artist=artist)
        track = Track.objects.create(
            album=album, title="Track", track_number=1, duration_ms=180000,
        )
        show = PodcastShow.objects.create(
            podcast_uuid="s", source=Sources.POCKETCASTS.value, title="Pod",
        )
        video = Video.objects.create(
            item=_item(MediaTypes.VIDEO.value, "v1"), user=user, status=done,
        )
        for index, day in enumerate(days):
            # 23:30 local is the next UTC day: the classic day-boundary miss.
            when = _local(day, 23, 30)
            Movie.objects.create(
                item=_item(MediaTypes.MOVIE.value, f"m{index}"),
                user=user, status=done, progress=1, end_date=when,
            )
            Episode.objects.create(
                item=_item(
                    MediaTypes.EPISODE.value, "tv",
                    season_number=1, episode_number=index + 1,
                ),
                related_season=season, end_date=_local(day, 0, 30),
            )
            Anime.objects.create(
                item=_item(MediaTypes.ANIME.value, f"a{index}"),
                user=user, status=done, progress=3, end_date=when,
            )
            book = Book.objects.create(
                item=_item(MediaTypes.BOOK.value, f"b{index}"),
                user=user, status=done, progress=100, end_date=when,
            )
            BookListeningSession.objects.create(
                book=book, session_id=f"s{index}", started_at=when, minutes=30,
            )
            Manga.objects.create(
                item=_item(MediaTypes.MANGA.value, f"ma{index}"),
                user=user, status=done, progress=5, end_date=when,
            )
            Comic.objects.create(
                item=_item(MediaTypes.COMIC.value, f"c{index}"),
                user=user, status=done, progress=2, end_date=when,
            )
            Music.objects.create(
                item=_item(MediaTypes.MUSIC.value, f"mu{index}"),
                user=user, album=album, artist=artist, track=track,
                status=done, progress=1, start_date=when, end_date=when,
            )
            episode = PodcastEpisode.objects.create(
                show=show, episode_uuid=f"e{index}", title=f"E{index}",
                published=when, duration=1800,
            )
            Podcast.objects.create(
                item=_item(MediaTypes.PODCAST.value, f"e{index}"),
                user=user, show=show, episode=episode, status=done, progress=30,
                start_date=when, end_date=when,
            )
            video.upsert_play(f"yt:{index}", 60, end_date=when)
        # Entries that span days: nothing starts on the middle days, which
        # must still carry them.
        cls.spanned_days = [date(2023, 12, 29), date(2023, 12, 30)]
        Game.objects.create(
            item=_item(MediaTypes.GAME.value, "g1"),
            user=user, status=done, progress=300,
            start_date=_local(date(2023, 12, 28), 22), end_date=_local(nye, 23, 30),
        )
        BoardGame.objects.create(
            item=_item(MediaTypes.BOARDGAME.value, "bg1"),
            user=user, status=done, progress=4,
            start_date=_local(date(2023, 12, 28), 21), end_date=_local(nye, 20),
        )
        cls.empty_user = get_user_model().objects.create_user(
            username="eager-days-empty", password="12345",
        )

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        timezone.activate(ZONE)
        self.addCleanup(timezone.deactivate)
        self.day_list = _iter_day_range(RANGE_START, RANGE_END)

    def _stored_days(self, user):
        keys = {_day_cache_key(user.id, day): day for day in self.day_list}
        return {keys[key]: value for key, value in cache.get_many(keys).items()}

    def test_bulk_day_payloads_match_per_day_builds(self):
        for user in (self.user, self.empty_user):
            with self.subTest(user=user.username):
                cache.clear()
                expected = {
                    day: statistics_day_builder.build_stats_for_day(
                        user.id, day, defer_cache_write=True,
                    )
                    for day in self.day_list
                }
                statistics_cache._eager_day_builder(user)(self.day_list)
                actual = self._stored_days(user)
                self.assertEqual(set(actual), set(self.day_list))
                expected = _without_build_time(expected)
                actual = _without_build_time(actual)
                for day in self.day_list:
                    self.assertEqual(expected[day], actual[day], day)

        # The fixture exercises what it claims to.
        def minutes(day):
            return expected_full[day]["totals"]["minutes_by_type"]

        cache.clear()
        expected_full = {
            day: statistics_day_builder.build_stats_for_day(
                self.user.id, day, defer_cache_write=True,
            )
            for day in self.day_list
        }
        late = minutes(self.late_night)
        for media_type in (
            MediaTypes.MOVIE.value, MediaTypes.TV.value, MediaTypes.MUSIC.value,
            MediaTypes.PODCAST.value, MediaTypes.BOOK.value,
        ):
            self.assertGreater(late.get(media_type, 0), 0, media_type)
        self.assertFalse(minutes(self.late_night + timedelta(days=1)))
        for day in self.spanned_days:
            self.assertEqual(expected_full[day]["activity"]["count"], 2, day)
            self.assertTrue(expected_full[day]["game"]["by_game"], day)
        self.assertFalse(minutes(date(2023, 12, 1)))

    def _custom_range(self, user):
        start = _range_dt(RANGE_START)
        end = _range_dt(RANGE_END, end=True)
        return (
            statistics_cache.get_statistics_data(user, start, end),
            statistics_cache.get_statistics_minutes_by_type(user, start, end),
            self._stored_days(user),
        )

    def test_statistics_payload_matches_per_day_path(self):
        for user in (self.user, self.empty_user):
            with self.subTest(user=user.username):
                cache.clear()
                with _per_day_build():
                    expected = self._custom_range(user)
                cache.clear()
                actual = self._custom_range(user)
                self.assertEqual(expected[0].keys(), actual[0].keys())
                for key in expected[0]:
                    self.assertEqual(expected[0][key], actual[0][key], key)
                self.assertEqual(expected[1], actual[1])
                # Same stored day rows, the empty ones included.
                self.assertEqual(
                    _without_build_time(expected[2], fence=True),
                    _without_build_time(actual[2], fence=True),
                )
                version = statistics_cache.get_history_version(user.id)
                self.assertEqual(
                    {payload["history_version"] for payload in actual[2].values()},
                    {version},
                )
                self.assertEqual(set(actual[2]), set(self.day_list))
                if user is self.user:
                    self.assertGreater(
                        actual[1].get(MediaTypes.MOVIE.value, 0), 0,
                    )

    def test_minutes_path_builds_missing_days_in_bulk(self):
        start = _range_dt(RANGE_START)
        end = _range_dt(RANGE_END, end=True)
        with _per_day_build():
            expected = statistics_cache.get_statistics_minutes_by_type(
                self.user, start, end,
            )
        self.assertGreater(expected.get(MediaTypes.MOVIE.value, 0), 0)
        cache.clear()
        with CaptureQueriesContext(connection) as queries:
            actual = statistics_cache.get_statistics_minutes_by_type(
                self.user, start, end,
            )
        self.assertEqual(expected, actual)
        # 71 days at ~13 queries each before; one prefetch per 25-day slice now.
        self.assertLess(len(queries), 100)


class EmptyStatisticsRequestQueryBudgetTests(TestCase):
    """An empty library's Statistics page must not build its days one by one."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = get_user_model().objects.create_user(
            username="eager-days-budget", password="12345",
        )
        self.client.force_login(self.user)

    def test_empty_library_statistics_request_query_budget(self):
        for url in (
            reverse("statistics"),
            reverse("statistics") + "?start-date=2023-01-01&end-date=2023-12-31",
        ):
            with self.subTest(url=url):
                cache.clear()
                with CaptureQueriesContext(connection) as queries:
                    response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertLessEqual(
                    len(queries), EMPTY_STATISTICS_REQUEST_QUERY_BUDGET,
                )
