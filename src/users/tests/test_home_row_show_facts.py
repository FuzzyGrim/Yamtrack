"""Home shelves rank shows from grouped facts, not hydrated trackers.

A scan attaches ``app.tv_show_facts.TrackedShow`` rows when every sort and
filter of the query declares ``tv_facts``. These tests hold that path to the
hydrated one it stands in for: the same values, the same order in both
directions for every Home sort, the same Up Next cards, and no season, play or
event model along the way.
"""

from collections import Counter
from contextlib import contextmanager
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import models
from django.db.models import F
from django.test import TestCase

from app import helpers
from app.library_query import executor as executor_module
from app.library_query import filters as filter_registry
from app.library_query.adapters import from_home_row_filters
from app.library_query.executor import Candidate, LibraryQueryExecutor
from app.models import TV, BasicMedia, Episode, Item, MediaTypes, Season, Status
from app.tests.test_tv_show_facts import BASE, build_library
from app.tv_show_facts import TrackedShow
from users import home_screen
from users.models import DirectionChoices, HomeScreenRow, HomeScreenRowTypeChoices

HOME_SORTS = (
    home_screen.UP_NEXT_SORT,
    "home_upcoming",
    "home_recent",
    "home_completion",
    "home_episodes_left",
)
NEEDS = {filter_registry.NEEDS_MEDIA, filter_registry.NEEDS_MAX_PROGRESS}
WATCHED_ON = BASE + timedelta(days=600)


@contextmanager
def hydrated_trackers():
    """Make every scan load the tracker graph, as it did before facts."""
    attach = executor_module._attach_media

    def without_facts(user, batch, needs, **_kwargs):
        return attach(user, batch, needs)

    with mock.patch.object(executor_module, "_attach_media", without_facts):
        yield


@contextmanager
def counted_models():
    """Count the model instances built from database rows, by class."""
    built = Counter()
    original = models.Model.from_db.__func__

    @classmethod
    def from_db(cls, db, field_names, values):
        built[cls.__name__] += 1
        return original(cls, db, field_names, values)

    with mock.patch.object(models.Model, "from_db", from_db):
        yield built


class ShowFactsLibrary(TestCase):
    """A varied TV library, stored the way saved items are."""

    @classmethod
    def setUpTestData(cls):
        """Build the library once."""
        cls.user = get_user_model().objects.create_user(username="shelves", password="x")
        cls.user.tv_enabled = True
        cls.user.save()
        build_library(cls.user)
        # ``bulk_create`` skips ``Item.save``, which stores the library type
        # every saved item has. The episode catalogue is read by it.
        for media_type in (MediaTypes.TV, MediaTypes.SEASON, MediaTypes.EPISODE):
            Item.objects.filter(media_type=media_type.value, library_media_type="").update(
                library_media_type=F("media_type"),
            )
        cls.watch_up_to_the_unreleased_episodes()

    @classmethod
    def watch_up_to_the_unreleased_episodes(cls):
        """Leave three shows waiting on an episode that has not aired.

        These shows have two dated but unreleased episodes at the end of
        season 4, and the shared library never watches right up to them.
        """
        waiting = ("s8", "s32", "s56")
        seasons = {
            (season.item.media_id, season.item.season_number): season
            for season in Season.objects.filter(item__media_id__in=waiting).select_related("item")
        }
        watched = set(
            Episode.objects.filter(related_season__in=seasons.values()).values_list(
                "item__media_id", "item__season_number", "item__episode_number",
            ),
        )
        Episode.objects.bulk_create(
            Episode(
                item=item,
                related_season=seasons[(item.media_id, item.season_number)],
                end_date=WATCHED_ON,
                status=Status.COMPLETED.value,
            )
            for item in Item.objects.filter(
                media_type=MediaTypes.EPISODE.value,
                media_id__in=waiting,
                season_number__gt=0,
            )
            if (item.season_number, item.episode_number) <= (4, 4)
            and (item.media_id, item.season_number, item.episode_number) not in watched
        )

    def setUp(self):
        """Start every test with cold shelves."""
        cache.clear()

    def query(self, sort_key, *, statuses=(), progress="all"):
        """Return the query a TV shelf with these settings runs."""
        filters = home_screen._normalized_filter_payload(
            {"status": list(statuses), "progress": progress},
            MediaTypes.TV.value,
        )
        return from_home_row_filters(
            self.user,
            filters,
            (MediaTypes.TV.value,),
            sort_key=sort_key,
            direction=DirectionChoices.ASC,
        )

    def attached(self, **kwargs):
        """Return ``{item_id: tracker row}`` as one scan batch attaches them."""
        batch = [
            Candidate(item)
            for item in Item.objects.filter(media_type=MediaTypes.TV.value).order_by("pk")
        ]
        executor_module._attach_media(self.user, batch, NEEDS, **kwargs)
        return {candidate.item.pk: candidate.media for candidate in batch if candidate.media}


class TrackedShowTests(ShowFactsLibrary):
    """A tracked show answers like the tracker for everything ranking reads."""

    def test_values_match_the_hydrated_tracker(self):
        """Progress, activity, released counts and caught-up-ness agree per show."""
        trackers = self.attached()
        shows = self.attached(show_facts=True)

        self.assertEqual(shows.keys(), trackers.keys())
        self.assertEqual(len(shows), TV.objects.filter(user=self.user).count())
        self.assertTrue(all(isinstance(show, TrackedShow) for show in shows.values()))
        self.assertTrue(all(isinstance(tracker, TV) for tracker in trackers.values()))

        def reading(media):
            entry = home_screen.HomeRowEntry(item=media.item, media=media)
            return {
                "pk": media.pk,
                "status": media.status,
                "created_at": media.created_at,
                "progress": media.progress,
                "progressed_at": media.progressed_at,
                "end_date": media.end_date,
                "max_progress": media.max_progress,
                "breakdown": dict(media.released_episode_breakdown),
                "episodes_left": media.episodes_left,
                "caught_up": helpers.is_caught_up_media(media),
                "recent": home_screen._entry_recent_timestamp(entry),
                "entry_progress": home_screen._entry_progress(entry),
                "order": media.active_episode_order_id,
            }

        for item_id, tracker in trackers.items():
            with self.subTest(item=item_id):
                self.assertEqual(reading(shows[item_id]), reading(tracker))

        readings = [reading(show) for show in shows.values()]
        # The comparison is only worth something on a library that varies.
        self.assertGreater(len({r["progress"] for r in readings}), 5)
        self.assertGreater(len({r["max_progress"] for r in readings}), 3)
        self.assertEqual({r["caught_up"] for r in readings}, {True, False})
        self.assertIn(None, {r["progressed_at"] for r in readings})

    def test_next_episode_matches_the_hydrated_tracker(self):
        """Up Next's projection is the same episode, release state and dates."""
        trackers = self.attached()
        shows = self.attached(show_facts=True)
        BasicMedia.objects.attach_episode_queue(list(trackers.values()))
        BasicMedia.objects.attach_episode_queue(list(shows.values()))

        for item_id, tracker in trackers.items():
            with self.subTest(item=item_id):
                self.assertEqual(shows[item_id].next_episode_queue, tracker.next_episode_queue)

        queues = [show.next_episode_queue for show in shows.values()]
        self.assertEqual({queue["released"] for queue in queues}, {True, False})
        self.assertGreater(len({queue.get("season_number") for queue in queues}), 2)
        self.assertTrue(any(queue.get("days_until") for queue in queues))
        # Episodes come from the stored catalogue as well as the calendar.
        self.assertTrue(any(queue.get("title") for queue in queues))


class ShelfOrderIdentityTests(ShowFactsLibrary):
    """Every Home sort orders shows as it did from hydrated trackers."""

    def test_each_sort_and_progress_filter_orders_alike(self):
        """Both directions agree for each sort, status scope and progress filter."""
        distinct_orders = set()
        for sort_key in (*HOME_SORTS, "title"):
            for statuses in ((), (Status.IN_PROGRESS.value,)):
                for progress in ("all", "not_caught_up", "caught_up"):
                    if sort_key == "title" and progress == "all":
                        continue  # Runs in SQL; nothing is attached.
                    query = self.query(sort_key, statuses=statuses, progress=progress)
                    with self.subTest(sort=sort_key, statuses=statuses, progress=progress):
                        from_facts = LibraryQueryExecutor(
                            self.user, query,
                        ).ranked_ids_both_directions()
                        with hydrated_trackers():
                            reference = LibraryQueryExecutor(
                                self.user, query,
                            ).ranked_ids_both_directions()
                        self.assertEqual(from_facts, reference)
                        self.assertTrue(from_facts[DirectionChoices.ASC])
                        if not statuses and progress == "all":
                            distinct_orders.add(tuple(from_facts[DirectionChoices.ASC]))
        # Five sorts that all gave one order would prove nothing.
        self.assertGreaterEqual(len(distinct_orders), 4)

    def test_up_next_shelf_shows_the_same_cards(self):
        """The shelf's membership, order and next episodes are unchanged."""
        row = HomeScreenRow.objects.create(
            user=self.user,
            media_type=MediaTypes.TV.value,
            row_type=HomeScreenRowTypeChoices.UP_NEXT,
            sort_by=home_screen.UP_NEXT_SORT,
            direction=DirectionChoices.ASC,
        )

        def cards():
            cache.clear()
            section = home_screen._build_row_section(self.user, row, row.media_type, 14)
            return section["total"], [
                (entry.item.pk, entry.next_episode) for entry in section["items"]
            ]

        from_facts = cards()
        with hydrated_trackers():
            reference = cards()

        self.assertEqual(from_facts, reference)
        self.assertGreater(from_facts[0], 14)

    def test_ranking_a_shelf_builds_no_tracker_graph(self):
        """A cold shelf loads its candidate items and nothing they own."""
        query = self.query(home_screen.UP_NEXT_SORT)
        with counted_models() as built:
            ranked = LibraryQueryExecutor(self.user, query).ranked_ids_both_directions()

        candidates = len(ranked[DirectionChoices.ASC])
        self.assertGreater(candidates, 200)
        self.assertEqual(dict(built), {"Item": candidates})


class FactsOptInTests(ShowFactsLibrary):
    """Only a scan whose readers all declare ``tv_facts`` gets facts."""

    def scan_modes(self, query):
        """Return the ``show_facts`` value of each batch a ranking attaches."""
        attach = executor_module._attach_media
        modes = []

        def record(user, batch, needs, **kwargs):
            modes.append(kwargs.get("show_facts", False))
            return attach(user, batch, needs, **kwargs)

        with mock.patch.object(executor_module, "_attach_media", record):
            LibraryQueryExecutor(self.user, query).ranked_ids()
        return set(modes)

    def test_home_sorts_and_the_progress_filter_use_facts(self):
        """Each declared sort, and the caught-up filter under a SQL sort, opt in."""
        for sort_key in HOME_SORTS:
            with self.subTest(sort=sort_key):
                self.assertEqual(self.scan_modes(self.query(sort_key)), {True})
        self.assertEqual(
            self.scan_modes(self.query("title", progress="caught_up")),
            {True},
        )

    def test_a_sort_that_reads_the_tracker_keeps_it(self):
        """Sorts that never declared facts still get hydrated trackers."""
        for sort_key in ("runtime", "time_watched", "time_left", "progressed_at"):
            for progress in ("all", "caught_up"):
                with self.subTest(sort=sort_key, progress=progress):
                    query = self.query(sort_key, progress=progress)
                    self.assertEqual(self.scan_modes(query), {False})
