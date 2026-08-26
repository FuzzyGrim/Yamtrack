import itertools
from datetime import UTC, datetime, timedelta
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


class RewatchTests(TestCase):
    """Test the rewatch feature: cascading, single-active-rewatch, progress reset."""

    def setUp(self):
        """Create a TV show with 3 watched seasons and TMDB metadata for them."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self._media_id_counter = itertools.count(1)

        self.episodes_metadata = [
            {
                "episode_number": episode_number,
                "image": f"http://example.com/image{episode_number}.jpg",
                "air_date": datetime(2019, 1, 1, tzinfo=UTC),
            }
            for episode_number in range(1, 6)
        ]

        self.season_metadata = {
            f"season/{n}": {
                "episodes": self.episodes_metadata,
                "image": f"http://example.com/image{n}.jpg",
                "season_number": n,
            }
            for n in (1, 2, 3)
        }

        self.tv_metadata = {
            "title": "Test Show",
            "max_progress": 3,
            "image": "http://example.com/image.jpg",
            "episodes": self.episodes_metadata,
            "related": {
                "seasons": [
                    {
                        "season_number": n,
                        "first_air_date": datetime(2020, 1, 1, tzinfo=UTC),
                    }
                    for n in (1, 2, 3)
                ],
            },
        }

        def mock_metadata(
            media_type,
            *args,  # noqa: ARG001
            **kwargs,  # noqa: ARG001
        ):
            if media_type == "tv_with_seasons":
                return self.season_metadata
            return self.tv_metadata

        self.mock_metadata = mock_metadata

        self.metadata_patcher = patch(
            "app.models.providers.services.get_media_metadata",
        )
        self.mock_get_metadata = self.metadata_patcher.start()
        self.mock_get_metadata.side_effect = mock_metadata
        self.addCleanup(self.metadata_patcher.stop)

        self.tv_item = Item.objects.create(
            media_id="1652",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Breaking Bad",
            image="http://example.com/image.jpg",
        )
        self.tv = TV.objects.create(
            item=self.tv_item,
            user=self.user,
            status=Status.COMPLETED.value,
        )

        self.seasons = {}
        for season_number in (1, 2, 3):
            season_item = Item.objects.create(
                media_id=f"123{season_number}",
                source=Sources.TMDB.value,
                media_type=MediaTypes.SEASON.value,
                title="Breaking Bad",
                image=f"http://example.com/image{season_number}.jpg",
                season_number=season_number,
            )
            season = Season.objects.create(
                item=season_item,
                user=self.user,
                related_tv=self.tv,
                status=Status.COMPLETED.value,
            )
            self.seasons[season_number] = season

            for episode_number in range(1, 6):
                episode_item = Item.objects.create(
                    media_id=f"634{episode_number}",
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.EPISODE.value,
                    title="Test Episode",
                    image=f"http://example.com/image{episode_number}.jpg",
                    season_number=season_number,
                    episode_number=episode_number,
                )
                Episode.objects.create(
                    item=episode_item,
                    related_season=season,
                    end_date=timezone.now() - timedelta(days=30),
                )

    def _watch_episode(self, season, episode_number, end_date=None):
        """Create and watch a single episode of a season."""
        item = Item.objects.create(
            media_id=str(next(self._media_id_counter)),
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Test Episode",
            image="http://example.com/image.jpg",
            season_number=season.item.season_number,
            episode_number=episode_number,
        )
        return Episode.objects.create(
            item=item,
            related_season=season,
            end_date=end_date or timezone.now(),
        )

    def test_setting_tv_rewatching_starts_season_one(self):
        """Setting TV status to REWATCHING should start season 1 rewatching."""
        self.tv.status = Status.REWATCHING.value
        self.tv.save()

        self.seasons[1].refresh_from_db()
        self.assertEqual(self.seasons[1].status, Status.REWATCHING.value)
        self.assertIsNotNone(self.seasons[1].rewatch_started_at)

    def test_rewatch_advances_through_watched_seasons(self):
        """Finishing a rewatched season should advance to the next watched season."""
        self.seasons[1].status = Status.REWATCHING.value
        self.seasons[1].save()
        self.seasons[1].refresh_from_db()

        self._watch_episode(self.seasons[1], 5)

        self.seasons[1].refresh_from_db()
        self.seasons[2].refresh_from_db()
        self.assertEqual(self.seasons[1].status, Status.COMPLETED.value)
        self.assertEqual(self.seasons[2].status, Status.REWATCHING.value)

    def test_rewatch_stops_at_never_watched_season(self):
        """Rewatch chain should end at a season with no prior episode history."""
        # Season 3 has never been watched: remove its episode.
        Episode.objects.filter(related_season=self.seasons[3]).delete()
        self.seasons[3].status = Status.PLANNING.value
        self.seasons[3].save()

        self.seasons[2].status = Status.REWATCHING.value
        self.seasons[2].save()
        self.seasons[2].refresh_from_db()

        self._watch_episode(self.seasons[2], 5)

        self.seasons[2].refresh_from_db()
        self.seasons[3].refresh_from_db()
        self.tv.refresh_from_db()

        self.assertEqual(self.seasons[2].status, Status.COMPLETED.value)
        self.assertEqual(self.seasons[3].status, Status.IN_PROGRESS.value)
        self.assertEqual(self.tv.status, Status.IN_PROGRESS.value)

    def test_only_one_season_rewatches_at_a_time(self):
        """Starting a rewatch on a season should demote any other rewatching seasons."""
        self.seasons[1].status = Status.REWATCHING.value
        self.seasons[1].save()

        self.seasons[2].status = Status.REWATCHING.value
        self.seasons[2].save()

        self.seasons[1].refresh_from_db()
        self.seasons[2].refresh_from_db()

        self.assertNotEqual(self.seasons[1].status, Status.REWATCHING.value)
        self.assertEqual(self.seasons[2].status, Status.REWATCHING.value)

    def test_demoted_rewatch_with_no_prior_history_goes_to_planning(self):
        """A rewatching season with no watch history at all, demoted -> PLANNING."""
        season_item = Item.objects.create(
            media_id="9991",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Breaking Bad",
            image="http://example.com/image9991.jpg",
            season_number=4,
        )
        self.season_metadata["season/4"] = self.season_metadata["season/1"]
        self.tv_metadata["related"]["seasons"].append(
            {
                "season_number": 4,
                "first_air_date": datetime(2020, 1, 1, tzinfo=UTC),
            },
        )
        never_watched_season = Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=self.tv,
            status=Status.PLANNING.value,
        )

        never_watched_season.status = Status.REWATCHING.value
        never_watched_season.save()

        self.seasons[2].status = Status.REWATCHING.value
        self.seasons[2].save()

        never_watched_season.refresh_from_db()
        self.assertEqual(never_watched_season.status, Status.PLANNING.value)
        self.assertIsNone(never_watched_season.rewatch_started_at)

    def test_demoted_rewatch_with_all_episodes_watched_goes_to_completed(self):
        """A rewatching season demoted after finishing its cycle goes to COMPLETED."""
        self.seasons[1].status = Status.REWATCHING.value
        self.seasons[1].save()
        self.seasons[1].refresh_from_db()

        self._watch_episode(self.seasons[1], 5)

        self.seasons[2].status = Status.REWATCHING.value
        self.seasons[2].save()

        self.seasons[1].refresh_from_db()
        self.assertEqual(self.seasons[1].status, Status.COMPLETED.value)

    def test_demoted_rewatch_with_some_episodes_watched_goes_to_in_progress(self):
        """A rewatching season demoted mid-cycle goes to IN_PROGRESS."""
        self.seasons[1].status = Status.REWATCHING.value
        self.seasons[1].save()
        self.seasons[1].refresh_from_db()

        self._watch_episode(self.seasons[1], 1)
        self._watch_episode(self.seasons[1], 2)

        self.seasons[2].status = Status.REWATCHING.value
        self.seasons[2].save()

        self.seasons[1].refresh_from_db()
        self.assertEqual(self.seasons[1].status, Status.IN_PROGRESS.value)

    def test_manual_exit_from_rewatching_resumes_prior_progress(self):
        """Manually switching out of REWATCHING should resume prior progresses."""
        Episode.objects.filter(related_season=self.seasons[1]).delete()
        for episode_number in (1, 2, 3):
            self._watch_episode(
                self.seasons[1],
                episode_number,
                end_date=timezone.now() - timedelta(days=30),
            )
        self.seasons[1].status = Status.REWATCHING.value
        self.seasons[1].refresh_from_db()

        self.seasons[1].status = Status.REWATCHING.value
        self.seasons[1].save()
        self.seasons[1].refresh_from_db()

        self.assertIsNotNone(self.seasons[1].rewatch_started_at)
        self.assertEqual(self.seasons[1]._get_latest_watched_episode_number(), 0)

        self.seasons[1].status = Status.IN_PROGRESS.value
        self.seasons[1].save()
        self.seasons[1].refresh_from_db()

        self.assertIsNone(self.seasons[1].rewatch_started_at)
        self.assertEqual(self.seasons[1]._get_latest_watched_episode_number(), 3)

    def test_progress_resets_after_rewatch_starts(self):
        """Progress should only count episodes watched since rewatch_started_at."""
        self.assertEqual(self.seasons[1].progress, 5)

        self.seasons[1].status = Status.REWATCHING.value
        self.seasons[1].save()
        self.seasons[1].refresh_from_db()

        self.assertEqual(self.seasons[1]._get_latest_watched_episode_number(), 0)

    def test_start_rewatch_creates_episode_in_new_cycle(self):
        """start_rewatch should create the episode under the new rewatch cycle."""
        self.seasons[1].rewatch(1, timezone.now())
        self.seasons[1].refresh_from_db()

        self.assertEqual(self.seasons[1].status, Status.REWATCHING.value)
        self.assertEqual(self.seasons[1]._get_latest_watched_episode_number(), 1)
