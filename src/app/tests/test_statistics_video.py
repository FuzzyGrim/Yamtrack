import datetime

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from app.models import Item, MediaTypes, Sources, Status, Video
from app.statistics_cache import get_statistics_data


class VideoStatisticsTests(TestCase):
    """Watched videos count toward Statistics, one play per watched day."""

    def setUp(self):
        # Day and range statistics live in the shared cache, keyed by user id;
        # SQLite reuses ids after rollback, so start every test empty.
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="viewer", password="password123"
        )

    def _video(self, media_id, length_seconds):
        item = Item.objects.create(
            media_id=media_id,
            source=Sources.YOUTUBE.value,
            media_type=MediaTypes.VIDEO.value,
            title=f"Video {media_id}",
        )
        return Video.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            length_seconds=length_seconds,
        )

    def test_plays_and_hours_are_counted(self):
        tz = timezone.get_current_timezone()
        day = datetime.datetime(2026, 9, 1, 12, 0, tzinfo=tz)
        short = self._video("short", 30 * 60)
        long = self._video("long", 90 * 60)
        short.upsert_play("youtube:short:2026-09-01", 30 * 60, end_date=day)
        long.upsert_play("youtube:long:2026-09-02", 90 * 60, end_date=day + datetime.timedelta(days=1))

        data = get_statistics_data(self.user, start_date=None, end_date=None)

        video = data["video_consumption"]
        self.assertTrue(video["has_data"])
        self.assertEqual(data["hours_per_media_type"][MediaTypes.VIDEO.value], "2h 0min")
        self.assertEqual(data["minutes_per_media_type"][MediaTypes.VIDEO.value], 120)

    def test_no_videos_means_no_video_section(self):
        data = get_statistics_data(self.user, start_date=None, end_date=None)

        self.assertFalse(data["video_consumption"].get("has_data"))

    def test_a_sub_minute_video_still_counts_as_a_play(self):
        tz = timezone.get_current_timezone()
        day = datetime.datetime(2026, 9, 1, 12, 0, tzinfo=tz)
        clip = self._video("clip", 45)
        clip.upsert_play("youtube:clip:2026-09-01", 45, end_date=day)

        data = get_statistics_data(self.user, start_date=None, end_date=None)

        self.assertTrue(data["video_consumption"]["has_data"])
        self.assertEqual(data["minutes_per_media_type"][MediaTypes.VIDEO.value], 0.75)
