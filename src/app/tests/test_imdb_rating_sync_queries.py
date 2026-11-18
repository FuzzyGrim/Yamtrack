from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from app.models import Item, MediaTypes, Sources
from app.services import imdb_ratings

SHOWS = 12
SEASONS = 2
EPISODES = 3


class ImdbRatingSyncQueryCountTests(TestCase):
    """The nightly episode/season sync must not issue queries per show."""

    @classmethod
    def setUpTestData(cls):
        rows = []
        for show in range(SHOWS):
            media_id = str(1000 + show)
            rows.append(
                Item(
                    media_id=media_id,
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.TV.value,
                    title=f"Show {show}",
                    provider_external_ids={"imdb_id": f"tt{media_id}"},
                ),
            )
            for season in range(1, SEASONS + 1):
                rows.append(
                    Item(
                        media_id=media_id,
                        source=Sources.TMDB.value,
                        media_type=MediaTypes.SEASON.value,
                        title=f"Show {show}",
                        season_number=season,
                    ),
                )
                rows.extend(
                    Item(
                        media_id=media_id,
                        source=Sources.TMDB.value,
                        media_type=MediaTypes.EPISODE.value,
                        title=f"Show {show}",
                        season_number=season,
                        episode_number=episode,
                        # Episodes carry their own tconst after the first sync
                        # pass. provider_external_ids is a sync-identity field:
                        # rewriting it every run would invalidate every tracked
                        # item's media-server sync context nightly.
                        provider_external_ids={
                            "imdb_id": f"tt{media_id}-{season}-{episode}"
                        },
                    )
                    for episode in range(1, EPISODES + 1)
                )
        Item.objects.bulk_create(rows)

    def _episode_map(self, parents):
        return {
            parent: {
                (s, e): f"{parent}-{s}-{e}"
                for s in range(1, SEASONS + 1)
                for e in range(1, EPISODES + 1)
            }
            for parent in parents
        }

    def test_episode_then_season_sync_queries_do_not_scale_with_shows(self):
        with (
            patch(
                "app.providers.imdb_datasets.download_episode_map",
                side_effect=self._episode_map,
            ),
            patch(
                "app.providers.imdb_datasets.download_ratings",
                side_effect=lambda tconsts: dict.fromkeys(tconsts, (8.0, 100)),
            ),
            CaptureQueriesContext(connection) as episode_queries,
        ):
            episodes = imdb_ratings.sync_episode_ratings()
        with CaptureQueriesContext(connection) as season_queries:
            seasons = imdb_ratings.sync_season_ratings()

        self.assertEqual(episodes, SHOWS * SEASONS * EPISODES)
        self.assertEqual(seasons, SHOWS * SEASONS)
        self.assertLessEqual(len(episode_queries), 4)
        self.assertLessEqual(len(season_queries), 5)
        self.assertFalse(
            Item.objects.filter(
                media_type=MediaTypes.SEASON.value,
                imdb_rating_count__isnull=True,
            ).exists(),
        )
