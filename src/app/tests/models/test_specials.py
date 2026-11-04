from datetime import UTC, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from app.models import (
    TV,
    Episode,
    Item,
    MediaTypes,
    Season,
    Sources,
    Status,
)


class SpecialsStatusTests(TestCase):
    """Test how specials affect the status of their show."""

    def setUp(self):
        """Create a completed show with a completed season and a special."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        self.episodes_metadata = [
            {
                "episode_number": i,
                "image": f"img{i}.jpg",
                "air_date": datetime(2023, 1, i, tzinfo=UTC),
            }
            for i in range(1, 4)
        ]

        def mock_metadata(
            media_type,
            _media_id,
            _source,
            season_numbers=None,
            **_kwargs,
        ):
            season_numbers = season_numbers or [1]
            if media_type == "tv_with_seasons":
                return {
                    f"season/{season}": {"episodes": self.episodes_metadata}
                    for season in season_numbers
                } | {
                    "related": {
                        "seasons": [
                            {"season_number": season} for season in season_numbers
                        ],
                    },
                }
            return {
                "episodes": self.episodes_metadata,
                "image": "season_img.jpg",
                "max_progress": len(self.episodes_metadata),
                "related": {
                    "seasons": [
                        {
                            "season_number": 1,
                            "image": "season_img.jpg",
                            "first_air_date": "2023-01-01",
                        },
                    ],
                },
            }

        self.metadata_patcher = patch(
            "app.models.providers.services.get_media_metadata",
        )
        self.mock_get_metadata = self.metadata_patcher.start()
        self.mock_get_metadata.side_effect = mock_metadata
        self.addCleanup(self.metadata_patcher.stop)

        tv_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Friends",
            image="http://example.com/image.jpg",
        )
        self.tv = TV(
            item=tv_item,
            user=self.user,
            status=Status.COMPLETED.value,
        )
        TV.save_base(self.tv)

        self.special = self._create_season(season_number=0)

    def _create_season(self, season_number, status=Status.IN_PROGRESS.value):
        """Create a season without running the status side effects."""
        item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=season_number,
        )
        season = Season(
            item=item,
            user=self.user,
            related_tv=self.tv,
            status=status,
        )
        Season.save_base(season)
        return season

    def _watch_special_episode(self, episode_number):
        """Mark an episode of the special season as watched."""
        episode_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=0,
            episode_number=episode_number,
        )
        return Episode.objects.create(
            item=episode_item,
            related_season=self.special,
            end_date=timezone.now(),
        )

    def _tv_status(self):
        """Return the current status of the show."""
        self.tv.refresh_from_db()
        return self.tv.status

    def test_excluded_special_episode_keeps_show_completed(self):
        """Watching a special does not reopen a completed show."""
        self.user.include_specials = False
        self.user.save(update_fields=["include_specials"])

        self._watch_special_episode(1)

        self.assertEqual(self._tv_status(), Status.COMPLETED.value)

    def test_included_special_episode_reopens_show(self):
        """Watching a special reopens the show when specials are included."""
        self._watch_special_episode(1)

        self.assertEqual(self._tv_status(), Status.IN_PROGRESS.value)

    def test_excluded_special_finale_does_not_advance_show(self):
        """Finishing a special season does not start the next season."""
        self.user.include_specials = False
        self.user.save(update_fields=["include_specials"])

        for episode_number in range(1, len(self.episodes_metadata) + 1):
            self._watch_special_episode(episode_number)

        self.special.refresh_from_db()
        self.assertEqual(self.special.status, Status.COMPLETED.value)
        self.assertEqual(self._tv_status(), Status.COMPLETED.value)
        self.assertFalse(
            Season.objects.filter(item__season_number=1).exists(),
        )

    def test_excluded_special_status_change_keeps_show_status(self):
        """Changing the status of a special leaves the show untouched."""
        self.user.include_specials = False
        self.user.save(update_fields=["include_specials"])

        self.special.status = Status.DROPPED.value
        self.special.save()

        self.assertEqual(self._tv_status(), Status.COMPLETED.value)

    def test_included_special_status_change_updates_show(self):
        """Changing the status of a special updates a show including specials."""
        self.special.status = Status.DROPPED.value
        self.special.save()

        self.assertEqual(self._tv_status(), Status.DROPPED.value)

    def test_excluded_special_unwatch_keeps_show_completed(self):
        """Unwatching a special does not reopen a completed show."""
        self.user.include_specials = False
        self.user.save(update_fields=["include_specials"])

        episode = self._watch_special_episode(1)
        Season.objects.filter(pk=self.special.pk).update(
            status=Status.COMPLETED.value,
        )
        TV.objects.filter(pk=self.tv.pk).update(status=Status.COMPLETED.value)

        episode.delete()

        self.assertEqual(self._tv_status(), Status.COMPLETED.value)

    def test_regular_season_still_updates_show(self):
        """Regular seasons keep updating the show when specials are excluded."""
        self.user.include_specials = False
        self.user.save(update_fields=["include_specials"])

        season = self._create_season(season_number=1)
        season.status = Status.DROPPED.value
        season.save()

        self.assertEqual(self._tv_status(), Status.DROPPED.value)
