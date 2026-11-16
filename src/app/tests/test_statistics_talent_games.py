import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from app.models import (
    TV,
    CreditRoleType,
    Episode,
    Game,
    Item,
    ItemPersonCredit,
    ItemStudioCredit,
    MediaTypes,
    Movie,
    Person,
    PersonGender,
    Season,
    Sources,
    Status,
    Studio,
)
from app.statistics_cache import get_statistics_data
from app.statistics_talent import _aggregate_top_talent, get_person_talent_totals


class GamesInTopTalentAggregationTests(TestCase):
    def setUp(self):
        # Day and range statistics live in the shared cache, keyed by user id;
        # SQLite reuses ids after rollback, so start every test empty.
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="talent-tester",
            password="password123",
        )
        self.game_item = Item.objects.create(
            media_id="igdb-100",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Dispatch",
            image="http://example.com/game.jpg",
        )
        Game.objects.create(
            item=self.game_item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=1,
            end_date=timezone.now(),
        )
        self.game_person = Person.objects.create(
            source=Sources.IMDB.value,
            source_person_id="nm0000001",
            name="Alice Actor",
            gender=PersonGender.FEMALE.value,
        )
        ItemPersonCredit.objects.create(
            item=self.game_item,
            person=self.game_person,
            role_type=CreditRoleType.CAST.value,
            role="Sam",
        )
        self.game_studio = Studio.objects.create(
            source=Sources.IGDB.value,
            source_studio_id="studio-100",
            name="Dispatch Studio",
        )
        ItemStudioCredit.objects.create(item=self.game_item, studio=self.game_studio)

        self.movie_item = Item.objects.create(
            media_id="tmdb-200",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="A Movie",
            image="http://example.com/movie.jpg",
        )
        Movie.objects.create(
            item=self.movie_item,
            user=self.user,
            status=Status.COMPLETED.value,
            end_date=timezone.now(),
        )
        self.movie_person = Person.objects.create(
            source=Sources.TMDB.value,
            source_person_id="1",
            name="Bob Movie Star",
            gender=PersonGender.MALE.value,
        )
        ItemPersonCredit.objects.create(
            item=self.movie_item,
            person=self.movie_person,
            role_type=CreditRoleType.CAST.value,
            role="Hero",
        )

    def test_top_talent_credits_do_not_use_implicit_ordering(self):
        # Aggregation counts rows using sets and counters: ordering credits
        # only forces SQLite to materialise and sort a large joined result.
        with CaptureQueriesContext(connection) as captured:
            result = _aggregate_top_talent(
                self.user,
                None,
                None,
                limit=1,
                schedule_missing_backfill=False,
            )

        credits_sql = [
            query["sql"]
            for query in captured.captured_queries
            if 'FROM "app_itempersoncredit"' in query["sql"]
            or 'FROM "app_itemstudiocredit"' in query["sql"]
        ]
        self.assertTrue(credits_sql, "Expected at least one credits query")
        for sql in credits_sql:
            self.assertNotIn(" ORDER BY ", sql.upper())

        self.assertEqual(result["top_actors"][0]["name"], "Bob Movie Star")
        self.assertEqual(result["top_actresses"][0]["name"], "Alice Actor")
        self.assertEqual(result["top_studios"][0]["name"], "Dispatch Studio")

    def test_dense_sqlite_credit_scan_matches_sparse_result(self):
        if connection.vendor != "sqlite":
            self.skipTest("SQLite planner optimisation")

        # Dense scan sees this unrelated person's credit but must not count it.
        unwatched = Item.objects.create(
            media_id="unwatched-credit-999",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Never watched",
        )
        stranger = Person.objects.create(
            source=Sources.TMDB.value,
            source_person_id="unwatched-999",
            name="Never Watched Performer",
            gender=PersonGender.MALE.value,
        )
        ItemPersonCredit.objects.create(
            item=unwatched,
            person=stranger,
            role_type=CreditRoleType.CAST.value,
            role="Lead",
        )

        with (
            patch("app.statistics_talent.DENSE_CREDIT_SCAN_MIN_ITEMS", 1),
            patch("app.statistics_talent.DENSE_CREDIT_SCAN_RATIO", 0.6),
            CaptureQueriesContext(connection) as dense_queries,
        ):
            dense = _aggregate_top_talent(
                self.user,
                None,
                None,
                schedule_missing_backfill=False,
            )

        with (
            patch("app.statistics_talent.DENSE_CREDIT_SCAN_MIN_ITEMS", 100_000),
            CaptureQueriesContext(connection) as sparse_queries,
        ):
            sparse = _aggregate_top_talent(
                self.user,
                None,
                None,
                schedule_missing_backfill=False,
            )

        self.assertEqual(dense, sparse)
        self.assertNotIn(
            stranger.name,
            [person["name"] for person in dense["top_actors"]],
        )

        def joined_person_queries(captured):
            return [
                entry["sql"]
                for entry in captured.captured_queries
                if 'FROM "app_itempersoncredit"' in entry["sql"]
                and 'JOIN "app_person"' in entry["sql"]
            ]

        dense_sql = joined_person_queries(dense_queries)
        sparse_sql = joined_person_queries(sparse_queries)
        self.assertEqual(len(dense_sql), 1)
        self.assertEqual(len(sparse_sql), 1)
        self.assertNotIn(" WHERE ", dense_sql[0].upper())
        self.assertIn(" WHERE ", sparse_sql[0].upper())

    def test_talent_does_not_materialize_credit_models(self):
        # A person can have thousands of credits and a large biography. Only
        # scalar credit rows and display fields for the winners are needed.
        with (
            patch.object(ItemPersonCredit, "from_db", side_effect=AssertionError),
            patch.object(ItemStudioCredit, "from_db", side_effect=AssertionError),
        ):
            result = _aggregate_top_talent(
                self.user,
                None,
                None,
                limit=1,
                schedule_missing_backfill=False,
            )
        self.assertEqual(result["top_actors"][0]["name"], "Bob Movie Star")
        self.assertEqual(result["top_actresses"][0]["name"], "Alice Actor")
        self.assertEqual(result["top_studios"][0]["name"], "Dispatch Studio")

    def test_limited_rankings_preserve_ties_and_duplicate_credit_counts(self):
        for index in range(4):
            person = Person.objects.create(
                source=Sources.TMDB.value,
                source_person_id=f"tie-{index}",
                name="Same Name",
                gender=PersonGender.MALE.value,
            )
            for role in ("First character", "Second character"):
                ItemPersonCredit.objects.create(
                    item=self.movie_item,
                    person=person,
                    role_type=CreditRoleType.CAST.value,
                    role=role,
                )
        complete = _aggregate_top_talent(
            self.user,
            None,
            None,
            schedule_missing_backfill=False,
        )
        limited = _aggregate_top_talent(
            self.user,
            None,
            None,
            limit=3,
            schedule_missing_backfill=False,
        )
        for mode in ("plays", "time", "titles"):
            expected = complete["by_sort"][mode]["top_actors"][:3]
            self.assertEqual(limited["by_sort"][mode]["top_actors"], expected)
            for row in expected:
                self.assertEqual(row["plays"], 1)
                self.assertEqual(row["unique_movies"], 1)

    def test_game_cast_appears_in_top_actors(self):
        result = _aggregate_top_talent(
            self.user,
            start_date=None,
            end_date=None,
            schedule_missing_backfill=False,
        )

        actress_names = {row["name"] for row in result["top_actresses"]}
        self.assertIn("Alice Actor", actress_names)

        entry = next(
            row for row in result["top_actresses"] if row["name"] == "Alice Actor"
        )
        self.assertEqual(entry["unique_titles"], 1)
        self.assertEqual(entry["unique_games"], 1)
        self.assertEqual(entry["unique_movies"], 0)

    def test_game_studios_appear_in_top_studios(self):
        result = _aggregate_top_talent(
            self.user,
            start_date=None,
            end_date=None,
            schedule_missing_backfill=False,
        )

        studio_entry = next(
            row for row in result["top_studios"] if row["name"] == "Dispatch Studio"
        )
        self.assertEqual(studio_entry["plays"], 1)
        self.assertEqual(studio_entry["unique_titles"], 1)
        self.assertEqual(studio_entry["unique_games"], 1)
        self.assertEqual(studio_entry["unique_movies"], 0)
        self.assertEqual(studio_entry["unique_shows"], 0)

    def test_movie_filter_excludes_game_cast(self):
        result = _aggregate_top_talent(
            self.user,
            start_date=None,
            end_date=None,
            schedule_missing_backfill=False,
            media_type=MediaTypes.MOVIE.value,
        )

        actress_names = {row["name"] for row in result["top_actresses"]}
        actor_names = {row["name"] for row in result["top_actors"]}
        self.assertNotIn("Alice Actor", actress_names)
        self.assertIn("Bob Movie Star", actor_names)

    def test_game_filter_excludes_movie_cast(self):
        result = _aggregate_top_talent(
            self.user,
            start_date=None,
            end_date=None,
            schedule_missing_backfill=False,
            media_type=MediaTypes.GAME.value,
        )

        actor_names = {row["name"] for row in result["top_actors"]}
        actress_names = {row["name"] for row in result["top_actresses"]}
        self.assertNotIn("Bob Movie Star", actor_names)
        self.assertIn("Alice Actor", actress_names)

    def test_imdb_game_cast_with_unknown_gender_falls_back_to_actor_and_uses_game_minutes(
        self,
    ):
        unknown_item = Item.objects.create(
            media_id="igdb-101",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Unknown Gender Game",
            image="http://example.com/game-unknown.jpg",
        )
        Game.objects.create(
            item=unknown_item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=135,
            end_date=timezone.now(),
        )
        unknown_person = Person.objects.create(
            source=Sources.IMDB.value,
            source_person_id="nm0000009",
            name="Unknown Gender Performer",
            gender=PersonGender.UNKNOWN.value,
        )
        ItemPersonCredit.objects.create(
            item=unknown_item,
            person=unknown_person,
            role_type=CreditRoleType.CAST.value,
            role="Lead",
        )

        result = _aggregate_top_talent(
            self.user,
            start_date=None,
            end_date=None,
            schedule_missing_backfill=False,
        )

        actor_entry = next(
            row
            for row in result["top_actors"]
            if row["name"] == "Unknown Gender Performer"
        )
        self.assertEqual(actor_entry["watched_minutes"], 135)
        self.assertEqual(actor_entry["watched_time"], "2h 15min")

    def test_person_talent_totals_include_unknown_gender_imdb_game_cast(self):
        unknown_item = Item.objects.create(
            media_id="igdb-102",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Dispatch Two",
            image="http://example.com/game-two.jpg",
        )
        Game.objects.create(
            item=unknown_item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=90,
            end_date=timezone.now(),
        )
        unknown_person = Person.objects.create(
            source=Sources.IMDB.value,
            source_person_id="nm0000010",
            name="Dispatch Performer",
            gender=PersonGender.UNKNOWN.value,
        )
        ItemPersonCredit.objects.create(
            item=unknown_item,
            person=unknown_person,
            role_type=CreditRoleType.CAST.value,
            role="Lead",
        )

        totals = get_person_talent_totals(
            self.user,
            unknown_person.source,
            unknown_person.source_person_id,
        )

        self.assertEqual(totals["bucket"], "actor")
        self.assertEqual(totals["watched_minutes"], 90)
        self.assertEqual(totals["unique_titles"], 1)
        self.assertEqual(totals["unique_games"], 1)
        self.assertEqual(totals["unique_movies"], 0)

    # Several media types can be selected at once (issue #1317).

    def _names(self, media_type):
        result = _aggregate_top_talent(
            self.user,
            start_date=None,
            end_date=None,
            schedule_missing_backfill=False,
            media_type=media_type,
        )
        return {
            row["name"] for row in result["top_actors"] + result["top_actresses"]
        }

    def test_movie_and_game_together_include_both_casts(self):
        for selection in ("movie,game", ["game", "movie"]):
            self.assertEqual(
                self._names(selection),
                {"Alice Actor", "Bob Movie Star"},
            )

    def test_types_without_credits_are_ignored_next_to_a_real_one(self):
        self.assertEqual(self._names("music,movie"), {"Bob Movie Star"})

    def test_only_types_without_credits_yield_no_talent(self):
        self.assertEqual(self._names("music,book"), set())

    def test_all_and_empty_mean_no_filter(self):
        everyone = {"Alice Actor", "Bob Movie Star"}
        self.assertEqual(self._names("all"), everyone)
        self.assertEqual(self._names(""), everyone)
        self.assertEqual(self._names(None), everyone)


class NoDateEntriesInAllTimeTopTalentTests(TestCase):
    """Regression tests for #1098: entries with no start/end date should still
    count toward "All Time" top talent, but not toward a concrete date range.
    """

    # A past range guaranteed not to overlap any dateless entry.
    RANGE_START = datetime.datetime(2000, 1, 1, tzinfo=datetime.UTC)
    RANGE_END = datetime.datetime(2000, 1, 31, tzinfo=datetime.UTC)

    def setUp(self):
        # Day and range statistics live in the shared cache, keyed by user id;
        # SQLite reuses ids after rollback, so start every test empty.
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="no-date-talent-tester",
            password="password123",
        )

    def test_movie_with_no_dates_included_in_all_time_top_actors(self):
        movie_item = Item.objects.create(
            media_id="tmdb-300",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Undated Movie",
        )
        Movie.objects.create(
            item=movie_item,
            user=self.user,
            status=Status.COMPLETED.value,
            start_date=None,
            end_date=None,
        )
        person = Person.objects.create(
            source=Sources.TMDB.value,
            source_person_id="300",
            name="Undated Movie Actor",
            gender=PersonGender.MALE.value,
        )
        ItemPersonCredit.objects.create(
            item=movie_item,
            person=person,
            role_type=CreditRoleType.CAST.value,
            role="Lead",
        )

        all_time_result = _aggregate_top_talent(
            self.user,
            start_date=None,
            end_date=None,
            schedule_missing_backfill=False,
        )
        actor_names = {row["name"] for row in all_time_result["top_actors"]}
        self.assertIn("Undated Movie Actor", actor_names)

        ranged_result = _aggregate_top_talent(
            self.user,
            start_date=self.RANGE_START,
            end_date=self.RANGE_END,
            schedule_missing_backfill=False,
        )
        ranged_actor_names = {row["name"] for row in ranged_result["top_actors"]}
        self.assertNotIn("Undated Movie Actor", ranged_actor_names)

    def test_game_with_no_dates_included_in_all_time_top_actors(self):
        game_item = Item.objects.create(
            media_id="igdb-300",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Undated Game",
        )
        Game.objects.create(
            item=game_item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=1,
            start_date=None,
            end_date=None,
        )
        person = Person.objects.create(
            source=Sources.IMDB.value,
            source_person_id="nm0000300",
            name="Undated Game Actor",
            gender=PersonGender.MALE.value,
        )
        ItemPersonCredit.objects.create(
            item=game_item,
            person=person,
            role_type=CreditRoleType.CAST.value,
            role="Lead",
        )

        all_time_result = _aggregate_top_talent(
            self.user,
            start_date=None,
            end_date=None,
            schedule_missing_backfill=False,
        )
        actor_names = {row["name"] for row in all_time_result["top_actors"]}
        self.assertIn("Undated Game Actor", actor_names)

        ranged_result = _aggregate_top_talent(
            self.user,
            start_date=self.RANGE_START,
            end_date=self.RANGE_END,
            schedule_missing_backfill=False,
        )
        ranged_actor_names = {row["name"] for row in ranged_result["top_actors"]}
        self.assertNotIn("Undated Game Actor", ranged_actor_names)

    def test_episode_with_no_end_date_included_in_all_time_top_actors(self):
        show_item = Item.objects.create(
            media_id="tmdb-400",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Undated Show",
        )
        tv = TV.objects.create(
            item=show_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        season_item = Item.objects.create(
            media_id="tmdb-400",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=1,
            title="Undated Show",
        )
        season = Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=tv,
            status=Status.IN_PROGRESS.value,
        )
        episode_item = Item.objects.create(
            media_id="tmdb-400",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=1,
            title="Undated Episode",
        )
        Episode.objects.create(
            item=episode_item,
            related_season=season,
            end_date=None,
        )
        person = Person.objects.create(
            source=Sources.TMDB.value,
            source_person_id="400",
            name="Undated Episode Actor",
            gender=PersonGender.MALE.value,
        )
        ItemPersonCredit.objects.create(
            item=episode_item,
            person=person,
            role_type=CreditRoleType.CAST.value,
            role="Lead",
        )

        all_time_result = _aggregate_top_talent(
            self.user,
            start_date=None,
            end_date=None,
            schedule_missing_backfill=False,
        )
        actor_names = {row["name"] for row in all_time_result["top_actors"]}
        self.assertIn("Undated Episode Actor", actor_names)

        ranged_result = _aggregate_top_talent(
            self.user,
            start_date=self.RANGE_START,
            end_date=self.RANGE_END,
            schedule_missing_backfill=False,
        )
        ranged_actor_names = {row["name"] for row in ranged_result["top_actors"]}
        self.assertNotIn("Undated Episode Actor", ranged_actor_names)

    def test_all_time_page_shows_talent_when_only_activity_is_dateless(self):
        """Regression test for the day-cache gap flagged in PR #1126 review.

        When a user's only movie/TV activity has no recorded date at all, the
        day-bucketed play counts that gate top-talent computation never see
        it, so the "All Time" page must fall back to a direct existence
        check (see `_has_dateless_movie_or_episode_activity` in
        statistics_aggregator.py) rather than reporting empty top talent.
        """
        movie_item = Item.objects.create(
            media_id="tmdb-500",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Only Undated Movie",
        )
        Movie.objects.create(
            item=movie_item,
            user=self.user,
            status=Status.COMPLETED.value,
            start_date=None,
            end_date=None,
        )
        person = Person.objects.create(
            source=Sources.TMDB.value,
            source_person_id="500",
            name="Only Undated Movie Actor",
            gender=PersonGender.MALE.value,
        )
        ItemPersonCredit.objects.create(
            item=movie_item,
            person=person,
            role_type=CreditRoleType.CAST.value,
            role="Lead",
        )

        data = get_statistics_data(self.user, start_date=None, end_date=None)

        actor_names = {
            row["name"] for row in data["top_talent"]["by_sort"]["plays"]["top_actors"]
        }
        self.assertIn("Only Undated Movie Actor", actor_names)


class PersonCreditBatchingTests(TestCase):
    """The person-credit fetch batches large item-ID lists (#1855)."""

    def test_credits_are_aggregated_across_id_batches(self):
        user = get_user_model().objects.create_user(
            username="talent-batch-tester",
            password="password123",
        )
        person = Person.objects.create(
            source=Sources.TMDB.value,
            source_person_id="batch-1",
            name="Batch Actor",
            gender=PersonGender.MALE.value,
        )
        played_at = timezone.now()
        items = Item.objects.bulk_create(
            [
                Item(
                    media_id=f"batch-movie-{offset}",
                    source=Sources.MANUAL.value,
                    media_type=MediaTypes.MOVIE.value,
                    title=f"Batch Movie {offset}",
                )
                for offset in range(501)
            ]
        )
        Movie.objects.bulk_create(
            [
                Movie(
                    user=user,
                    item=item,
                    status=Status.COMPLETED.value,
                    end_date=played_at,
                )
                for item in items
            ]
        )
        ItemPersonCredit.objects.bulk_create(
            [
                ItemPersonCredit(
                    item=item,
                    person=person,
                    role_type=CreditRoleType.CAST.value,
                    role="Lead",
                )
                for item in items
            ]
        )

        result = _aggregate_top_talent(
            user,
            None,
            None,
            schedule_missing_backfill=False,
        )

        top_actor = result["top_actors"][0]
        self.assertEqual(top_actor["name"], "Batch Actor")
        self.assertEqual(top_actor["plays"], 501)
        self.assertEqual(top_actor["unique_movies"], 501)
