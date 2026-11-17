"""Self-tests for the shared library-query engine.

These pin the engine's own contract - the SQL path and the batched scan agree,
pages tile the full ordering without gaps or repeats, and the cost of a page
does not grow with the library - independent of any surface that uses it.
"""

from collections import Counter
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import connection, models
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from app.library_query import FilterValues, LibraryQuery, LibraryQueryExecutor, SortSpec
from app.library_query import executor as executor_module
from app.library_query.sorts import SORTS
from app.library_query.spec import STATUS_MATCH_ANY
from app.models import (
    TV,
    CollectionEntry,
    Episode,
    Game,
    Item,
    ItemTag,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Status,
    Tag,
)

MOVIE = MediaTypes.MOVIE.value
SQL_SORT_KEYS = [d.keys[0] for d in SORTS if d.sql is not None]


def _query(**kwargs):
    filters = kwargs.pop("filters", FilterValues())
    sort = kwargs.pop("sort", SortSpec())
    return LibraryQuery(media_types=(MOVIE,), filters=filters, sort=sort, **kwargs)


class LibraryQueryTestCase(TestCase):
    """Builds a small movie library with varied, partly tied, sort values."""

    def setUp(self):
        """Create a user and a movie library."""
        self.user = get_user_model().objects.create_user(username="lq", password="x")
        self.now = timezone.now()

    def make_movie(self, index, **overrides):
        """Create one movie item and its tracker row."""
        item = Item.objects.create(
            media_id=str(1000 + index),
            source=Sources.TMDB.value,
            media_type=MOVIE,
            title=overrides.pop("title", f"Movie {index % 7}"),
            image="https://example.com/i.jpg",
            release_datetime=overrides.pop(
                "release_datetime",
                None if index % 5 == 0 else self.now - timedelta(days=index),
            ),
            provider_rating=overrides.pop(
                "provider_rating",
                None if index % 4 == 0 else Decimal(index % 3),
            ),
            genres=overrides.pop("genres", ["Drama"] if index % 2 else ["Comedy"]),
        )
        movie = self.track(
            item,
            status=overrides.pop("status", Status.COMPLETED.value),
            score=overrides.pop("score", None if index % 3 == 0 else Decimal(index % 10)),
            start_date=self.now - timedelta(days=index % 6),
            end_date=self.now - timedelta(days=index % 4),
        )
        return item, movie

    def track(self, item, model=Movie, **fields):
        """Create a tracker row without the save signals' provider refresh."""
        return model.objects.bulk_create([model(item=item, user=self.user, **fields)])[0]

    def library(self, size, start=0):
        """Create ``size`` movies."""
        return [self.make_movie(index)[0] for index in range(start, start + size)]

    def run_query(self, query, offset=0, limit=100):
        """Return one page."""
        return LibraryQueryExecutor(self.user, query).page(offset, limit)


class SqlAndScanAgreeTests(LibraryQueryTestCase):
    """Every SQL sort orders identically on the scan path."""

    def test_every_sql_sort_matches_the_scan_order(self):
        """SQL ORDER BY and the Python comparator agree, both directions."""
        self.library(30)
        for key in SQL_SORT_KEYS:
            for direction in ("asc", "desc"):
                with self.subTest(key=key, direction=direction):
                    query = _query(sort=SortSpec(key=key, direction=direction, seed=7))
                    executor = LibraryQueryExecutor(self.user, query)
                    sql_ids = [item.pk for item in executor.page(0, 100).items]
                    scan_ids = [row[-1] for row in executor._scan_ranked]
                    self.assertEqual(sql_ids, scan_ids)

    def test_pages_tile_the_full_order_without_gaps_or_repeats(self):
        """Consecutive pages concatenate to the full ordering."""
        self.library(23)
        for key in ("title", "score", "random"):
            with self.subTest(key=key):
                query = _query(sort=SortSpec(key=key, direction="desc", seed=3))
                full = [item.pk for item in self.run_query(query).items]
                paged = []
                for offset in range(0, 23, 5):
                    page = self.run_query(query, offset, 5)
                    self.assertEqual(page.total, 23)
                    paged.extend(item.pk for item in page.items)
                self.assertEqual(paged, full)
                self.assertEqual(len(set(paged)), 23)

    def test_random_order_is_fixed_per_seed(self):
        """The same seed repeats; another seed shuffles differently."""
        self.library(20)
        first = [i.pk for i in self.run_query(_query(sort=SortSpec("random", seed=1))).items]
        again = [i.pk for i in self.run_query(_query(sort=SortSpec("random", seed=1))).items]
        other = [i.pk for i in self.run_query(_query(sort=SortSpec("random", seed=2))).items]
        self.assertEqual(first, again)
        self.assertNotEqual(first, other)


class LastWatchedSortTests(LibraryQueryTestCase):
    """Last watched means recent activity, not only a completion date."""

    def setUp(self):
        """Create one old completion and one recently played entry."""
        super().setUp()
        completed_item = Item.objects.create(
            media_id="last-watched-completed",
            source=Sources.TMDB.value,
            media_type=MOVIE,
            title="Finished Long Ago",
            image="https://example.com/i.jpg",
        )
        self.completed = self.track(
            completed_item,
            status=Status.COMPLETED.value,
            end_date=self.now - timedelta(days=10),
            progressed_at=self.now - timedelta(days=10),
        )
        active_item = Item.objects.create(
            media_id="last-watched-active",
            source=Sources.TMDB.value,
            media_type=MOVIE,
            title="Playing Now",
            image="https://example.com/i.jpg",
        )
        self.active = self.track(
            active_item,
            status=Status.IN_PROGRESS.value,
            end_date=None,
            progressed_at=self.now,
        )

    def test_last_watched_orders_in_progress_by_progressed_at(self):
        """A recently played in-progress item sorts above an older completion."""
        items = self.run_query(
            _query(sort=SortSpec("last_watched", "desc")),
        ).items
        self.assertEqual(
            [item.pk for item in items],
            [self.active.item_id, self.completed.item_id],
        )

    def test_duplicate_rows_use_the_latest_activity(self):
        """An in-progress row wins over an older completed row for one item."""
        item = Item.objects.create(
            media_id="last-watched-repeated",
            source=Sources.TMDB.value,
            media_type=MOVIE,
            title="Watched Again",
            image="https://example.com/i.jpg",
        )
        self.track(
            item,
            status=Status.COMPLETED.value,
            end_date=self.now - timedelta(days=20),
            progressed_at=self.now - timedelta(days=20),
        )
        self.track(
            item,
            status=Status.IN_PROGRESS.value,
            end_date=None,
            progressed_at=self.now + timedelta(days=1),
        )

        items = self.run_query(
            _query(sort=SortSpec("last_watched", "desc")),
        ).items
        self.assertEqual(items[0].pk, item.pk)

    def _undated_completed(self):
        item = Item.objects.create(
            media_id="last-watched-undated",
            source=Sources.TMDB.value,
            media_type=MOVIE,
            title="Logged Today Without Date",
            image="https://example.com/i.jpg",
        )
        self.track(
            item,
            status=Status.COMPLETED.value,
            end_date=None,
            progressed_at=self.now,
        )
        return item

    def test_undated_completion_does_not_count_as_recent(self):
        """A completed item with no watch date sorts last, not first."""
        undated = self._undated_completed()
        for direction in ("desc", "asc"):
            with self.subTest(direction=direction):
                ids = [
                    item.pk
                    for item in self.run_query(
                        _query(sort=SortSpec("last_watched", direction)),
                    ).items
                ]
                self.assertEqual(ids[-1], undated.pk)
        newest_first = [
            item.pk
            for item in self.run_query(
                _query(sort=SortSpec("last_watched", "desc")),
            ).items
        ]
        self.assertEqual(
            newest_first,
            [self.active.item_id, self.completed.item_id, undated.pk],
        )

    def test_later_edit_does_not_change_the_order(self):
        """Touching a row after its watch does not move it."""
        older = self.track(
            Item.objects.create(
                media_id="last-watched-edited",
                source=Sources.TMDB.value,
                media_type=MOVIE,
                title="Edited Later",
                image="https://example.com/i.jpg",
            ),
            status=Status.COMPLETED.value,
            end_date=self.now - timedelta(days=30),
            progressed_at=self.now - timedelta(days=30),
        )
        Movie.objects.filter(pk=older.pk).update(score=9)
        ids = [
            item.pk
            for item in self.run_query(
                _query(sort=SortSpec("last_watched", "desc")),
            ).items
        ]
        self.assertEqual(ids[-1], older.item_id)

    def test_end_date_stays_a_completion_sort(self):
        """Custom lists keep the literal end date ordering."""
        items = self.run_query(
            _query(sort=SortSpec("end_date", "desc")),
        ).items
        self.assertEqual(items[0].pk, self.completed.item_id)


class DateAddedSortTests(LibraryQueryTestCase):
    """Date added is when the item was first tracked, not its latest row."""

    def test_rewatch_does_not_make_an_old_item_look_new(self):
        """A later tracker row must not move an item to the top of Date Added."""
        old_item, old_row = self.make_movie(0)
        Movie.objects.filter(pk=old_row.pk).update(
            created_at=self.now - timedelta(days=30),
        )
        rewatch = self.track(old_item, status=Status.COMPLETED.value)
        Movie.objects.filter(pk=rewatch.pk).update(created_at=self.now)
        new_item, new_row = self.make_movie(1)
        Movie.objects.filter(pk=new_row.pk).update(
            created_at=self.now - timedelta(days=1),
        )

        ids = [
            item.pk
            for item in self.run_query(
                _query(sort=SortSpec("date_added", "desc")),
            ).items
        ]
        self.assertEqual(ids, [new_item.pk, old_item.pk])


class UpdatedSortTests(LibraryQueryTestCase):
    """Updated reads progressed_at off the newest row, in SQL and on a scan."""

    def _item(self, key):
        return Item.objects.create(
            media_id=f"updated-{key}",
            source=Sources.TMDB.value,
            media_type=MOVIE,
            title=f"Updated {key}",
            image="https://example.com/i.jpg",
        )

    def _row(self, item, *, created, progressed):
        row = self.track(item, status=Status.COMPLETED.value)
        Movie.objects.filter(pk=row.pk).update(
            created_at=self.now - timedelta(days=created),
            progressed_at=self.now - timedelta(days=progressed),
        )
        return row

    def _orders(self, direction):
        executor = LibraryQueryExecutor(
            self.user, _query(sort=SortSpec("updated", direction)),
        )
        page = executor.page(0, 100)
        return page, [item.pk for item in page.items], [row[-1] for row in executor._scan_ranked]

    def test_newest_row_wins_over_the_latest_progress(self):
        """Repeat rows: the newest row's value counts, not the maximum."""
        # The older row has the most recent progress; the newest row does not.
        repeated = self._item("repeated")
        self._row(repeated, created=30, progressed=1)
        self._row(repeated, created=2, progressed=20)
        self._row(repeated, created=10, progressed=5)
        middle = self._item("middle")
        self._row(middle, created=3, progressed=10)
        # Two rows created together: the higher id is the newest.
        tied = self._item("tied")
        first = self._row(tied, created=4, progressed=2)
        second = self._row(tied, created=4, progressed=15)
        self.assertGreater(second.pk, first.pk)
        self.library(6)

        for direction in ("desc", "asc"):
            with self.subTest(direction=direction):
                page, sql_ids, scan_ids = self._orders(direction)
                self.assertTrue(page.used_sql)
                self.assertEqual(sql_ids, scan_ids)
                ours = [pk for pk in sql_ids if pk in {repeated.pk, middle.pk, tied.pk}]
                expected = [middle.pk, tied.pk, repeated.pk]
                self.assertEqual(ours, expected if direction == "desc" else expected[::-1])

    def test_tracked_podcast_show_has_no_value(self):
        """A show tracker replaces the podcast row, and it has no progressed_at."""
        from app.models import Podcast, PodcastShow, PodcastShowTracker

        podcast = MediaTypes.PODCAST.value
        for index in range(4):
            item = Item.objects.create(
                media_id=f"pod-{index}",
                source=Sources.POCKETCASTS.value,
                media_type=podcast,
                title=f"Podcast {index}",
                image="https://example.com/i.jpg",
            )
            row = self.track(item, model=Podcast, status=Status.IN_PROGRESS.value)
            Podcast.objects.filter(pk=row.pk).update(progressed_at=self.now - timedelta(days=index))
        show = PodcastShow.objects.create(
            podcast_uuid="pod-1", source=Sources.POCKETCASTS.value, title="Show", image="/c.jpg",
        )
        PodcastShowTracker.objects.create(user=self.user, show=show)
        for include_shows in (False, True):
            for direction in ("desc", "asc"):
                with self.subTest(include_shows=include_shows, direction=direction):
                    query = LibraryQuery(
                        media_types=(podcast,),
                        sort=SortSpec("updated", direction),
                        include_podcast_shows=include_shows,
                    )
                    executor = LibraryQueryExecutor(self.user, query)
                    page = executor.page(0, 100)
                    self.assertTrue(page.used_sql)
                    sql_ids = [item.pk for item in page.items]
                    self.assertEqual(sql_ids, [row[-1] for row in executor._scan_ranked])
                    self.assertEqual(
                        Item.objects.get(pk=sql_ids[-1]).media_id, "pod-1",
                    )

    def test_shows_scan_and_agree(self):
        """TV derives progressed_at from episodes, so it stays on the scan path."""
        query = LibraryQuery(
            media_types=(MediaTypes.TV.value,), sort=SortSpec("updated", "desc"),
        )
        self.assertFalse(LibraryQueryExecutor(self.user, query).uses_sql)


class StatusSemanticsTests(LibraryQueryTestCase):
    """``latest`` follows the newest row; ``any`` accepts any row."""

    def setUp(self):
        """Create an item completed once, then re-watched and dropped."""
        super().setUp()
        self.item, first = self.make_movie(1, status=Status.COMPLETED.value)
        Movie.objects.filter(pk=first.pk).update(end_date=self.now - timedelta(days=30))
        self.track(self.item, status=Status.DROPPED.value, end_date=self.now)

    def ids(self, **filter_kwargs):
        """Return matching ids."""
        query = _query(filters=FilterValues(**filter_kwargs))
        return LibraryQueryExecutor(self.user, query).ids()

    def test_latest_uses_the_newest_row(self):
        """The item's status is Dropped now."""
        self.assertEqual(self.ids(statuses=(Status.DROPPED.value,)), {self.item.pk})
        self.assertEqual(self.ids(statuses=(Status.COMPLETED.value,)), set())

    def test_any_accepts_an_older_row(self):
        """Smart lists saved before the engine match any row."""
        self.assertEqual(
            self.ids(statuses=(Status.COMPLETED.value,), status_match=STATUS_MATCH_ANY),
            {self.item.pk},
        )

    def test_statusless_rows_are_not_in_all(self):
        """An imported rating with no status only appears with "no status"."""
        rated_only = Item.objects.create(
            media_id="9", source=Sources.TMDB.value, media_type=MOVIE, title="Rated",
            image="https://example.com/i.jpg",
        )
        self.track(rated_only, status=None, score=7)
        self.assertNotIn(rated_only.pk, self.ids())
        self.assertEqual(self.ids(include_no_status=True), {rated_only.pk})
        self.assertIn(
            rated_only.pk,
            self.ids(statuses=(Status.DROPPED.value,), include_no_status=True),
        )


class FilterSemanticsTests(LibraryQueryTestCase):
    """Representative filters from each evaluation form."""

    def ids(self, **kwargs):
        """Return matching ids."""
        return LibraryQueryExecutor(self.user, _query(**kwargs)).ids()

    def test_rating_uses_the_latest_scored_row(self):
        """A later unscored rewatch keeps the earlier score."""
        item, movie = self.make_movie(1, score=Decimal(8))
        self.track(item, status=Status.COMPLETED.value)
        unrated, _ = self.make_movie(3, score=None)
        self.assertIn(item.pk, self.ids(filters=FilterValues(rating="rated")))
        self.assertIn(unrated.pk, self.ids(filters=FilterValues(rating="not_rated")))
        self.assertEqual(
            self.ids(filters=FilterValues(rating_min="7.5", rating_max="9")),
            {item.pk},
        )

    def test_tags_modes(self):
        """Tag modes and, or and not match names case-insensitively."""
        a, _ = self.make_movie(1)
        b, _ = self.make_movie(2)
        c, _ = self.make_movie(3)
        red = Tag.objects.create(user=self.user, name="Red")
        blue = Tag.objects.create(user=self.user, name="Blue")
        ItemTag.objects.create(tag=red, item=a)
        ItemTag.objects.create(tag=blue, item=a)
        ItemTag.objects.create(tag=red, item=b)
        tags = ("red", "BLUE")
        self.assertEqual(self.ids(filters=FilterValues(tags=tags, tag_mode="and")), {a.pk})
        self.assertEqual(
            self.ids(filters=FilterValues(tags=tags, tag_mode="or")),
            {a.pk, b.pk},
        )
        self.assertEqual(self.ids(filters=FilterValues(tags=tags, tag_mode="not")), {c.pk})

    def test_genre_matches_case_insensitively(self):
        """JSON-array filters compile to SQL."""
        drama, _ = self.make_movie(1, genres=["Drama"])
        self.make_movie(2, genres=["Comedy"])
        self.assertEqual(self.ids(filters=FilterValues(genre="drama")), {drama.pk})

    def test_collected_platform_overrides_item_platforms(self):
        """A collected copy's platform wins over the provider's list, for every type."""
        item, _ = self.make_movie(1)
        Item.objects.filter(pk=item.pk).update(platforms=["Blu-ray"])
        other, _ = self.make_movie(2)
        Item.objects.filter(pk=other.pk).update(platforms=["Blu-ray"])
        CollectionEntry.objects.create(user=self.user, item=item, resolution="4K UHD")
        self.assertEqual(
            self.ids(filters=FilterValues(platforms=("blu-ray",))),
            {other.pk},
        )
        self.assertEqual(
            self.ids(filters=FilterValues(platforms=("4k uhd",))),
            {item.pk},
        )

    def test_collection_only_items_join_only_when_allowed(self):
        """Untracked collected items appear unless a status or rating is asked for."""
        tracked, _ = self.make_movie(1)
        untracked = Item.objects.create(
            media_id="77", source=Sources.TMDB.value, media_type=MOVIE, title="Shelf",
            image="https://example.com/i.jpg",
        )
        CollectionEntry.objects.create(user=self.user, item=untracked)
        self.assertEqual(
            self.ids(include_collection_only=True, filters=FilterValues(include_no_status=True)),
            {untracked.pk},
        )
        self.assertEqual(
            self.ids(
                include_collection_only=True,
                filters=FilterValues(
                    statuses=(Status.COMPLETED.value,),
                    include_no_status=True,
                ),
            ),
            {tracked.pk, untracked.pk},
        )
        self.assertEqual(
            self.ids(
                include_collection_only=True,
                filters=FilterValues(statuses=(Status.COMPLETED.value,)),
            ),
            {tracked.pk},
        )

    def test_author_predicate_uses_the_scan_path(self):
        """A Python-only filter narrows correctly and pages from the scan."""
        items = self.library(6)
        Item.objects.filter(pk=items[2].pk).update(authors=[{"name": "Le Guin"}])
        executor = LibraryQueryExecutor(self.user, _query(filters=FilterValues(author="le guin")))
        page = executor.page(0, 10)
        self.assertFalse(page.used_sql)
        self.assertEqual([item.pk for item in page.items], [items[2].pk])
        self.assertEqual(page.total, 1)

    def test_union_lists_bypass_filters(self):
        """The smart-list ``list`` rule adds members whatever the filters say."""
        from lists.models import CustomList, CustomListItem

        item, _ = self.make_movie(1, genres=["Comedy"])
        pinned, _ = self.make_movie(2, genres=["Horror"])
        linked = CustomList.objects.create(owner=self.user, name="Linked")
        CustomListItem.objects.create(custom_list=linked, item=pinned)
        self.assertEqual(
            self.ids(filters=FilterValues(genre="comedy"), union_list_ids=(linked.pk,)),
            {item.pk, pinned.pk},
        )


class CrossProviderAliasTests(LibraryQueryTestCase):
    """A TMDB show whose TVDB alias is also listed is hidden (#639)."""

    def test_single_provider_does_not_materialize_alias_candidates(self):
        items = [
            Item.objects.create(
                media_id=str(9000 + index),
                source=Sources.TMDB.value,
                media_type=MediaTypes.TV.value,
                title=f"Show {index}",
                provider_external_ids={"tvdb_id": str(index)},
            )
            for index in range(25)
        ]
        query = LibraryQuery(media_types=(MediaTypes.TV.value,))
        executor = LibraryQueryExecutor(self.user, query)
        with mock.patch.object(Item, "from_db", wraps=Item.from_db) as hydrate:
            hidden = executor._cross_provider_hidden_ids(
                Item.objects.filter(pk__in=[item.pk for item in items])
            )
        self.assertEqual(hidden, set())
        hydrate.assert_not_called()

    def test_tmdb_alias_is_hidden(self):
        """Only the preferred provider's identity is listed."""
        tvdb = Item.objects.create(
            media_id="555", source=Sources.TVDB.value, media_type=MediaTypes.TV.value,
            title="Show", image="https://example.com/i.jpg",
        )
        tmdb = Item.objects.create(
            media_id="42", source=Sources.TMDB.value, media_type=MediaTypes.TV.value,
            title="Show", image="https://example.com/i.jpg",
            provider_external_ids={"tvdb_id": "555"},
        )
        for item in (tvdb, tmdb):
            self.track(item, model=TV, status=Status.IN_PROGRESS.value)
        self.user.tv_metadata_source_default = Sources.TVDB.value
        query = LibraryQuery(media_types=(MediaTypes.TV.value,))
        self.assertEqual(LibraryQueryExecutor(self.user, query).ids(), {tvdb.pk})
        query = LibraryQuery(media_types=(MediaTypes.TV.value,), dedupe_cross_provider=False)
        self.assertEqual(LibraryQueryExecutor(self.user, query).ids(), {tvdb.pk, tmdb.pk})


class PageCostTests(LibraryQueryTestCase):
    """A page costs the same however large the library is."""

    def _queries_for_page(self, query):
        with CaptureQueriesContext(connection) as ctx:
            LibraryQueryExecutor(self.user, query).page(0, 10)
        return ctx.captured_queries

    def test_sql_path_query_count_does_not_scale(self):
        """Filtering, ordering, COUNT and LIMIT all run in the database."""
        query = _query(
            filters=FilterValues(statuses=(Status.COMPLETED.value,), genre="drama"),
            sort=SortSpec("score", "desc"),
        )
        self.library(12)
        small = self._queries_for_page(query)
        self.library(120, start=12)
        big = self._queries_for_page(query)
        self.assertEqual(len(small), len(big))
        self.assertTrue(any("LIMIT 10" in q["sql"] for q in big))

    def test_scan_path_hydrates_only_batches_and_the_page(self):
        """Tracker rows are loaded per batch, never for the whole library at once."""
        self.library(60)
        seen_batch_sizes = []
        original = executor_module._attach_media

        def spy(user, batch, needs, **kwargs):
            seen_batch_sizes.append(len(batch))
            return original(user, batch, needs, **kwargs)

        query = _query(sort=SortSpec("runtime", "asc"))
        with mock.patch.object(executor_module, "_attach_media", spy):
            page = LibraryQueryExecutor(self.user, query, batch_size=16).page(0, 10)
        self.assertFalse(page.used_sql)
        self.assertEqual(page.total, 60)
        self.assertEqual(len(page.items), 10)
        self.assertTrue(seen_batch_sizes)
        self.assertLessEqual(max(seen_batch_sizes), 16)

    def test_a_sort_that_reads_only_the_item_loads_no_tracker(self):
        """Time to beat and author scan the candidate items alone."""
        self.library(60)
        for key in ("time_to_beat", "author"):
            with self.subTest(sort=key), mock.patch.object(
                executor_module, "_attach_media", side_effect=AssertionError(key),
            ):
                page = LibraryQueryExecutor(
                    self.user, _query(sort=SortSpec(key, "asc")), batch_size=16,
                ).page(0, 10)
                self.assertFalse(page.used_sql)
                self.assertEqual(page.total, 60)


class ItemOnlySortPageTests(TestCase):
    """Ordering games by time to beat hydrates one page of trackers."""

    def test_time_to_beat_builds_trackers_for_the_page_only(self):
        """The scan reads game items; Game rows are built for the cards."""
        user = get_user_model().objects.create_user(username="ttb", password="x")
        items = Item.objects.bulk_create(
            Item(
                media_id=str(5000 + index),
                source=Sources.IGDB.value,
                media_type=MediaTypes.GAME.value,
                title=f"Game {index}",
                image="https://example.com/i.jpg",
                provider_game_lengths={"igdb": {"summary": {"normally_seconds": index * 600}}} if index % 3 else {},
            )
            for index in range(90)
        )
        Game.objects.bulk_create(
            Game(item=item, user=user, status=Status.COMPLETED.value) for item in items
        )
        self.client.force_login(user)
        built = Counter()
        original = models.Model.from_db.__func__

        def from_db(cls, db, field_names, values):
            built[cls.__name__] += 1
            return original(cls, db, field_names, values)

        with mock.patch.object(models.Model, "from_db", classmethod(from_db)):
            response = self.client.get("/medialist/game?sort=time_to_beat&direction=asc&status=All")
        page = response.context["media_list"]
        self.assertEqual(page.paginator.count, 90)
        self.assertLessEqual(built["Game"], len(page.object_list))


class MovieRowOrderTests(LibraryQueryTestCase):
    """Runtime and time watched rank movies from their columns, as from models."""

    def setUp(self):
        """Movies with every kind of runtime and repeat viewing."""
        super().setUp()
        runtimes = [(95, ""), (None, "1h 30m"), (999998, "2h"), (999999, ""), (None, ""), (0, "45min"), (120, "")]
        for index in range(42):
            item, _movie = self.make_movie(index)
            minutes, text = runtimes[index % len(runtimes)]
            Item.objects.filter(pk=item.pk).update(runtime_minutes=minutes, runtime=text)
            Movie.objects.filter(item=item).update(progress=index % 3)
            for repeat in range(index % 4):
                self.track(
                    item,
                    status=Status.COMPLETED.value if repeat % 2 else Status.IN_PROGRESS.value,
                    progress=repeat,
                    end_date=self.now - timedelta(days=repeat) if repeat % 3 else None,
                )

    def test_light_rows_order_like_hydrated_movies(self):
        """Both directions agree, and the light path builds no Movie model."""
        attach = executor_module._attach_media

        def hydrated(user, batch, needs, **kwargs):
            kwargs.pop("light_movies", None)
            return attach(user, batch, needs, **kwargs)

        orders = set()
        for key in ("runtime", "time_watched"):
            query = _query(sort=SortSpec(key, "asc"))
            built = Counter()
            original = models.Model.from_db.__func__

            def from_db(cls, db, field_names, values, built=built, original=original):
                built[cls.__name__] += 1
                return original(cls, db, field_names, values)

            with self.subTest(sort=key):
                with mock.patch.object(models.Model, "from_db", classmethod(from_db)):
                    light = LibraryQueryExecutor(self.user, query).ranked_ids_both_directions()
                with mock.patch.object(executor_module, "_attach_media", hydrated):
                    reference = LibraryQueryExecutor(self.user, query).ranked_ids_both_directions()
                self.assertEqual(light, reference)
                self.assertEqual(set(built), {"Item"})
                orders.add(tuple(light["asc"]))
        # The two sorts differ, so the fixture exercises the play count.
        self.assertEqual(len(orders), 2)

    def test_list_page_builds_movies_for_its_cards_only(self):
        """The media list ordered by runtime hydrates one page of trackers."""
        self.library(200, start=42)
        self.client.force_login(self.user)
        for key in ("runtime", "time_watched"):
            built = Counter()
            original = models.Model.from_db.__func__

            def from_db(cls, db, field_names, values, built=built, original=original):
                built[cls.__name__] += 1
                return original(cls, db, field_names, values)

            with mock.patch.object(models.Model, "from_db", classmethod(from_db)):
                response = self.client.get(f"/medialist/movie?sort={key}&direction=asc&status=All")
            page = response.context["media_list"]
            with self.subTest(sort=key):
                self.assertEqual(page.paginator.count, 242)
                # The cards' rows and repeat viewings, not the library's.
                self.assertLess(built["Movie"], Movie.objects.count() // 2)


class LargeTvBatchTests(LibraryQueryTestCase):
    """A batch with over 1000 episodes loads on SQLite (#1450)."""

    def test_tv_batch_with_over_a_thousand_episode_items_loads(self):
        """Django 5.2 turns a forward-FK prefetch into an OR chain SQLite caps at depth 1000."""
        shows, seasons_per_show = 4, 300
        tv_items = Item.objects.bulk_create(
            Item(media_id=str(n), source=Sources.TMDB.value, media_type=MediaTypes.TV.value, title=f"Show {n}", image="x")
            for n in range(shows)
        )
        tvs = TV.objects.bulk_create(
            TV(item=item, user=self.user, status=Status.IN_PROGRESS.value) for item in tv_items
        )
        season_items = Item.objects.bulk_create(
            Item(media_id=str(n), source=Sources.TMDB.value, media_type=MediaTypes.SEASON.value, season_number=number, title=f"Show {n}", image="x")
            for n in range(shows)
            for number in range(1, seasons_per_show + 1)
        )
        season_rows = Season.objects.bulk_create(
            Season(item=item, related_tv=tvs[index // seasons_per_show], user=self.user, status=Status.IN_PROGRESS.value)
            for index, item in enumerate(season_items)
        )
        episode_items = Item.objects.bulk_create(
            Item(media_id=str(item.media_id), source=Sources.TMDB.value, media_type=MediaTypes.EPISODE.value, season_number=item.season_number, episode_number=1, title=item.title, image="x")
            for item in season_items
        )
        Episode.objects.bulk_create(
            Episode(item=item, related_season=season) for item, season in zip(episode_items, season_rows)
        )
        # Episode.item is nullable: such a row must not break the batch.
        Episode.objects.bulk_create([Episode(item=None, related_season=season_rows[0])])

        batch = [executor_module.Candidate(item) for item in tv_items]
        executor_module._attach_media(self.user, batch, {executor_module.filter_registry.NEEDS_MEDIA})

        self.assertTrue(all(candidate.media is not None for candidate in batch))
        episodes = [
            episode
            for candidate in batch
            for season in candidate.media.seasons.all()
            for episode in season.episodes.all()
        ]
        with_items = [episode for episode in episodes if episode.item_id]
        self.assertEqual(len(with_items), shows * seasons_per_show)
        self.assertEqual(len(episodes), len(with_items) + 1)
        with self.assertNumQueries(0):
            self.assertTrue(all(episode.item.episode_number == 1 for episode in with_items))


class SeasonScanShowBlobTests(LibraryQueryTestCase):
    """A season scan leaves each show's provider blob in the database."""

    def test_season_rows_do_not_decode_their_shows_provider_blob(self):
        """Every season row joins its show; the show's watch providers stay unread."""
        from django.db import models

        from app.library_query import filters as filter_registry

        blob = {
            f"R{region:03d}": {"flatrate": [{"provider_name": f"P{region}-{k}"} for k in range(16)]}
            for region in range(96)
        }
        show = Item.objects.create(
            media_id="77", source=Sources.TMDB.value, media_type=MediaTypes.TV.value,
            title="Wide Show", image="x", watch_providers=blob,
        )
        tv = TV.objects.create(item=show, user=self.user, status=Status.COMPLETED.value)
        season_items = [
            Item.objects.create(
                media_id="77", source=Sources.TMDB.value, media_type=MediaTypes.SEASON.value,
                season_number=number, title="Wide Show", image="x",
            )
            for number in (1, 2, 3)
        ]
        seasons = Season.objects.bulk_create(
            Season(item=item, related_tv=tv, user=self.user, status=Status.COMPLETED.value)
            for item in season_items
        )
        for season in seasons:
            episode = Item.objects.create(
                media_id="77", source=Sources.TMDB.value, media_type=MediaTypes.EPISODE.value,
                season_number=season.item.season_number, episode_number=1, title="Wide Show", image="x",
                # The provider is unreachable in tests, so the season's episode
                # count comes from its released episodes.
                release_datetime=self.now - timedelta(days=30),
            )
            Episode.objects.create(item=episode, related_season=season, status=Status.COMPLETED.value, end_date=self.now)

        decoded = []
        original = models.JSONField.from_db_value

        def counting(field, value, expression, conn):
            if isinstance(value, (str, bytes)):
                decoded.append(len(value))
            return original(field, value, expression, conn)

        batch = [executor_module.Candidate(item) for item in season_items]
        with mock.patch.object(models.JSONField, "from_db_value", counting):
            executor_module._attach_media(
                self.user, batch, {filter_registry.NEEDS_MEDIA, filter_registry.NEEDS_MAX_PROGRESS},
            )

        self.assertGreater(len(str(blob)), 40_000)
        self.assertLess(sum(decoded), 10_000)
        for candidate in batch:
            show_item = candidate.media.related_tv.item
            self.assertIn("watch_providers", show_item.get_deferred_fields())
            self.assertEqual(show_item.title, "Wide Show")
            self.assertEqual(candidate.media.max_progress, 1)
            self.assertEqual(
                filter_registry.season_effective_status(candidate.media), Status.COMPLETED.value,
            )
