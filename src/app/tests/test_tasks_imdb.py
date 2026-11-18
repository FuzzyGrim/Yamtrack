from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from app.models import (
    CREDITS_BACKFILL_VERSION,
    CreditRoleType,
    Item,
    ItemPersonCredit,
    MediaTypes,
    MetadataBackfillField,
    Person,
    Sources,
)
from app.services.imdb_ratings import sync_season_ratings
from app.tasks_backfill_state import _record_backfill_success
from app.tasks_imdb import (
    backfill_imdb_game_person_profiles,
    refresh_imdb_game_credits_from_datasets,
    sync_imdb_ratings_from_datasets,
)


class RefreshImdbGameCreditsTaskTests(TestCase):
    def test_backfills_images_even_when_no_new_credits_to_sync(self):
        """Regression: a game already synced in a prior run has no new tconsts to
        fetch, but its cast can still be missing images from before this backfill
        existed — that shouldn't skip the image backfill step entirely.
        """
        item = Item.objects.create(
            media_id="igdb-1",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Dispatch",
            image="http://example.com/game.jpg",
            provider_external_ids={"imdb_id": "tt1111111"},
        )
        person = Person.objects.create(
            source=Sources.IMDB.value,
            source_person_id="nm0000001",
            name="Alice Actor",
            image="",
        )
        ItemPersonCredit.objects.create(
            item=item,
            person=person,
            role_type=CreditRoleType.CAST.value,
            role="Sam",
        )
        # Mark the game as already synced with the current strategy so
        # _resolved_game_items excludes it — the "no new credits" scenario.
        _record_backfill_success(
            item,
            MetadataBackfillField.CREDITS.value,
            strategy_version=CREDITS_BACKFILL_VERSION,
        )

        with (
            patch(
                "app.providers.imdb_datasets.download_videogame_title_index",
                return_value={},
            ),
            # Defensive: the excluded item means these should never be called,
            # but a live multi-hundred-MB dataset download must never be a
            # possible test outcome if the filtering contract changes again.
            patch(
                "app.providers.imdb_datasets.download_principals",
                return_value={},
            ) as mock_principals,
            patch(
                "app.providers.imdb_datasets.download_names",
                return_value={},
            ),
            patch(
                "app.providers.tmdb.search_person_profile",
                return_value={
                    "image": "https://image.tmdb.org/t/p/h632/alice.jpg",
                    "gender": "female",
                },
            ) as mock_search,
            patch(
                "app.services.imdb_game_credits.backfill_missing_game_studios",
                return_value=1,
            ) as mock_studios,
        ):
            result = refresh_imdb_game_credits_from_datasets()

        mock_principals.assert_not_called()
        mock_studios.assert_called_once_with()
        mock_search.assert_called_once_with("Alice Actor")
        person.refresh_from_db()
        self.assertEqual(person.image, "https://image.tmdb.org/t/p/h632/alice.jpg")
        self.assertEqual(result["studios_backfilled"], 1)
        self.assertEqual(result["profiles_backfilled"], 1)
        self.assertEqual(result["synced"], 0)


class BackfillImdbGamePersonProfilesTaskTests(TestCase):
    def test_task_returns_profile_backfill_count(self):
        Person.objects.create(
            source=Sources.IMDB.value,
            source_person_id="nm0000001",
            name="Alice Actor",
            image="",
        )

        with patch(
            "app.providers.tmdb.search_person_profile",
            return_value={
                "image": "https://image.tmdb.org/t/p/h632/alice.jpg",
                "gender": "female",
            },
        ):
            result = backfill_imdb_game_person_profiles()

        self.assertEqual(result, {"profiles_backfilled": 1})


class SyncImdbRatingsTaskTests(TestCase):
    def test_syncs_movie_show_and_episode_ratings(self):
        movie = Item.objects.create(
            media_id="tmdb-1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="A Movie",
            provider_external_ids={"imdb_id": "tt0000001"},
        )
        show = Item.objects.create(
            media_id="tmdb-2",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="A Show",
            provider_external_ids={"imdb_id": "tt0000002"},
        )
        episode = Item.objects.create(
            media_id="tmdb-2",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Pilot",
            season_number=1,
            episode_number=1,
        )
        season = Item.objects.create(
            media_id="tmdb-2",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Season 1",
            season_number=1,
        )

        with (
            patch(
                "app.providers.imdb_datasets.download_ratings",
                side_effect=[
                    {"tt0000001": (8.1, 1000), "tt0000002": (7.5, 2000)},
                    {"tt0000003": (9.0, 500)},
                ],
            ),
            patch(
                "app.providers.imdb_datasets.download_episode_map",
                return_value={"tt0000002": {(1, 1): "tt0000003"}},
            ),
        ):
            result = sync_imdb_ratings_from_datasets()

        movie.refresh_from_db()
        show.refresh_from_db()
        episode.refresh_from_db()
        season.refresh_from_db()
        self.assertEqual(movie.imdb_rating, 8.1)
        self.assertEqual(movie.imdb_rating_count, 1000)
        self.assertEqual(show.imdb_rating, 7.5)
        self.assertEqual(show.imdb_rating_count, 2000)
        self.assertEqual(episode.imdb_rating, 9.0)
        self.assertEqual(episode.imdb_rating_count, 500)
        self.assertEqual(
            episode.provider_external_ids["imdb_id"],
            "tt0000003",
        )
        self.assertEqual(season.imdb_rating, 9.0)
        self.assertEqual(season.imdb_rating_count, 500)
        self.assertEqual(
            result,
            {
                "movies_and_shows_updated": 2,
                "episodes_updated": 1,
                "seasons_updated": 1,
            },
        )

    def test_episode_sync_stores_tconst_when_rating_is_unchanged(self):
        """An episode whose rating predates the imdb_id column write still gets
        its tconst stored, so the detail chip can link out on the next pass.
        """
        Item.objects.create(
            media_id="tmdb-4",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="A Show",
            provider_external_ids={"imdb_id": "tt0000010"},
        )
        episode = Item.objects.create(
            media_id="tmdb-4",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Pilot",
            season_number=1,
            episode_number=1,
            imdb_rating=9.0,
            imdb_rating_count=500,
        )

        with (
            patch(
                "app.providers.imdb_datasets.download_ratings",
                side_effect=[{}, {"tt0000011": (9.0, 500)}],
            ),
            patch(
                "app.providers.imdb_datasets.download_episode_map",
                return_value={"tt0000010": {(1, 1): "tt0000011"}},
            ),
        ):
            result = sync_imdb_ratings_from_datasets()

        episode.refresh_from_db()
        self.assertEqual(episode.provider_external_ids["imdb_id"], "tt0000011")
        # The rating itself did not move, so it is not counted as updated.
        self.assertEqual(result["episodes_updated"], 0)


class SyncImdbRatingsMemoryTests(TestCase):
    """The sync must not load columns it never reads (watch_providers is ~146 KiB)."""

    def test_sync_never_selects_heavy_columns(self):
        show = Item.objects.create(
            media_id="tmdb-9",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="A Show",
            provider_external_ids={"imdb_id": "tt0000009"},
            watch_providers={"US": {"flatrate": [{"provider_name": "x" * 1000}]}},
        )
        episode = Item.objects.create(
            media_id="tmdb-9",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Pilot",
            season_number=1,
            episode_number=1,
        )
        season = Item.objects.create(
            media_id="tmdb-9",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Season 1",
            season_number=1,
        )

        with (
            patch(
                "app.providers.imdb_datasets.download_ratings",
                side_effect=[{"tt0000009": (7.0, 10)}, {"tt0000010": (8.0, 20)}],
            ),
            patch(
                "app.providers.imdb_datasets.download_episode_map",
                return_value={"tt0000009": {(1, 1): "tt0000010"}},
            ),
            CaptureQueriesContext(connection) as queries,
        ):
            result = sync_imdb_ratings_from_datasets()

        self.assertEqual(
            result,
            {
                "movies_and_shows_updated": 1,
                "episodes_updated": 1,
                "seasons_updated": 1,
            },
        )
        select_sql = [q["sql"] for q in queries if q["sql"].startswith("SELECT")]
        self.assertTrue(select_sql)
        for sql in select_sql:
            self.assertNotIn("watch_providers", sql)
            self.assertNotIn("synopsis", sql)
        show.refresh_from_db()
        episode.refresh_from_db()
        season.refresh_from_db()
        self.assertEqual((show.imdb_rating, show.imdb_rating_count), (7.0, 10))
        self.assertEqual((episode.imdb_rating, episode.imdb_rating_count), (8.0, 20))
        self.assertEqual((season.imdb_rating, season.imdb_rating_count), (8.0, 20))
        # The sync must not wipe the columns it left unloaded.
        self.assertIn("US", show.watch_providers)


class SyncSeasonRatingsBucketTests(TestCase):
    """Regression: TV and grouped-anime buckets must aggregate independently."""

    def test_does_not_pool_ratings_across_buckets(self):
        Item.objects.create(
            media_id="tmdb-3",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            library_media_type="",
            title="TV Episode",
            season_number=1,
            episode_number=1,
            imdb_rating=8.0,
            imdb_rating_count=100,
        )
        Item.objects.create(
            media_id="tmdb-3",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            library_media_type=MediaTypes.ANIME.value,
            title="Anime Episode",
            season_number=1,
            episode_number=1,
            imdb_rating=6.0,
            imdb_rating_count=10,
        )
        tv_season = Item.objects.create(
            media_id="tmdb-3",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            library_media_type="",
            title="TV Season 1",
            season_number=1,
        )
        anime_season = Item.objects.create(
            media_id="tmdb-3",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            library_media_type=MediaTypes.ANIME.value,
            title="Anime Season 1",
            season_number=1,
        )

        updated = sync_season_ratings()

        tv_season.refresh_from_db()
        anime_season.refresh_from_db()
        self.assertEqual(updated, 2)
        self.assertEqual(tv_season.imdb_rating, 8.0)
        self.assertEqual(tv_season.imdb_rating_count, 100)
        self.assertEqual(anime_season.imdb_rating, 6.0)
        self.assertEqual(anime_season.imdb_rating_count, 10)
