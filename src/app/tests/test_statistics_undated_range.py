import datetime

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from app.models import (
    Album,
    AlbumTracker,
    Anime,
    Item,
    MediaTypes,
    Movie,
    Sources,
    Status,
)
from app.statistics_cache import get_statistics_data


class UndatedEntriesRangeTests(TestCase):
    """Entries with no dates belong to All Time only, never to a date range (#1521)."""

    def setUp(self):
        # Day and range statistics live in the shared cache, keyed by user id;
        # SQLite reuses ids after rollback, so start every test empty.
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="viewer", password="password123"
        )
        self.today = timezone.localtime().replace(
            hour=12, minute=0, second=0, microsecond=0
        )

    def _undated(self, model, media_type, media_id, status, score=None):
        item = Item.objects.create(
            media_id=media_id,
            source=Sources.MANUAL.value,
            media_type=media_type,
            title=f"Undated {media_id}",
        )
        return model.objects.create(
            user=self.user, item=item, status=status, score=score
        )

    def _stats(self, start, end):
        return get_statistics_data(self.user, start, end)

    def test_date_range_ignores_undated_planning_and_scored_entries(self):
        self._undated(Anime, MediaTypes.ANIME.value, "plan", Status.PLANNING.value)
        self._undated(
            Anime, MediaTypes.ANIME.value, "done", Status.COMPLETED.value, score=0.0
        )
        self._undated(
            Movie, MediaTypes.MOVIE.value, "film", Status.COMPLETED.value, score=8.0
        )
        watched = self._undated(
            Movie, MediaTypes.MOVIE.value, "today", Status.COMPLETED.value, score=6.0
        )
        watched.start_date = watched.end_date = self.today
        watched.save()

        today_start = self.today.replace(hour=0)
        today_end = self.today.replace(hour=23, minute=59)
        data = self._stats(today_start, today_end)

        self.assertEqual(data["media_count"]["total"], 1)
        self.assertEqual(data["media_count"].get(MediaTypes.ANIME.value, 0), 0)
        self.assertEqual(data["status_distribution"]["total_completed"], 1)

    def test_all_time_still_counts_undated_entries(self):
        self._undated(Anime, MediaTypes.ANIME.value, "plan", Status.PLANNING.value)
        self._undated(
            Anime, MediaTypes.ANIME.value, "done", Status.COMPLETED.value, score=0.0
        )

        data = self._stats(None, None)

        self.assertEqual(data["media_count"][MediaTypes.ANIME.value], 2)
        self.assertEqual(data["status_distribution"]["total_completed"], 1)

    def test_range_for_a_past_day_ignores_undated_entries(self):
        self._undated(Anime, MediaTypes.ANIME.value, "plan", Status.PLANNING.value)
        last_week = self.today - datetime.timedelta(days=7)

        data = self._stats(last_week.replace(hour=0), last_week.replace(hour=23))

        self.assertEqual(data["media_count"]["total"], 0)

    def test_album_trackers_count_without_creating_listening_activity(self):
        album = Album.objects.create(title="Album")
        tracker = AlbumTracker.objects.create(
            user=self.user, album=album, status=Status.COMPLETED.value,
            score=8, start_date=self.today, end_date=self.today,
        )
        data = self._stats(self.today.replace(hour=0), self.today.replace(hour=23))
        self.assertEqual(data["media_count"][MediaTypes.MUSIC.value], 1)
        self.assertEqual(data["status_distribution"]["total_completed"], 1)
        self.assertEqual(data["score_distribution"]["total_scored"], 1)
        self.assertFalse(data["music_consumption"]["has_data"])
        self.assertEqual(data["minutes_per_media_type"].get(MediaTypes.MUSIC.value, 0), 0)
        self.assertEqual(data["media_type_distribution"]["datasets"][0]["data"], [])
        tracker.start_date = tracker.end_date = None
        tracker.save()
        self.assertEqual(self._stats(None, None)["media_count"][MediaTypes.MUSIC.value], 1)
        self.assertEqual(self._stats(self.today.replace(hour=0), self.today.replace(hour=23))["media_count"]["total"], 0)

    def test_album_changes_refresh_predefined_statistics(self):
        album = Album.objects.create(title="Album")
        tracker = AlbumTracker.objects.create(user=self.user, album=album, score=5)
        data = get_statistics_data(self.user, None, None, range_name="All Time")
        self.assertEqual(data["score_distribution"]["average_score"], 5)
        tracker.score = 9
        tracker.save()
        data = get_statistics_data(self.user, None, None, range_name="All Time")
        self.assertEqual(data["score_distribution"]["average_score"], 9)
        tracker.delete()
        data = get_statistics_data(self.user, None, None, range_name="All Time")
        self.assertEqual(data["media_count"]["total"], 0)
