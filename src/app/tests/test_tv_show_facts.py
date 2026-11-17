"""TV facts and the time-left order built from them.

The TV list ranks the library from grouped rows (``app.tv_show_facts``)
instead of hydrated seasons and plays. These tests hold the two readings
together: the facts must equal the model properties they stand in for, and the
order built from them must equal the order built from hydrated trackers.
"""

from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection, models
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from app.media_list_views import MediaListEntry
from app.models import (
    TV,
    BasicMedia,
    CollectionEntry,
    Episode,
    Item,
    MediaTypes,
    Season,
    Status,
)
from app.tv_show_facts import ShowFacts, TrackedShow, tv_show_facts
from app.tv_sort import (
    _sort_tv_media_by_time_left,
    build_time_left_records,
    sort_time_left_records,
)
from events.models import Event

SHOWS = 240
BASE = datetime(2020, 1, 1, tzinfo=UTC)
REWATCH_STARTED = BASE + timedelta(days=400, hours=9)
STATUSES = [
    Status.COMPLETED.value,
    Status.IN_PROGRESS.value,
    Status.PLANNING.value,
    Status.PAUSED.value,
    Status.DROPPED.value,
]


def build_library(user):
    """Build a deterministic library that reaches every branch of the order.

    Show ``i`` varies by residue: status, stored runtime (real, both unknown
    markers, none), source (the fallback runtime differs), specials, a dropped
    season inside a show, a season in a rewatch, unreleased episodes, seasons
    dated by calendar events, missing and marker episode runtimes, and six
    watch patterns including repeats, dropped plays and plays nothing counts
    (rating-only, archived).
    """
    now = timezone.now()
    shows = Item.objects.bulk_create(
        Item(
            media_id=f"s{i}",
            source="mal" if i % 23 == 0 else "tmdb",
            media_type=MediaTypes.TV.value,
            title=(f"Show {i % 200:03d}".upper() if i % 7 == 0 else f"Show {i % 200:03d}"),
            image="x",
            runtime_minutes={0: 45, 1: 999998, 2: 999999}.get(i % 5),
        )
        for i in range(SHOWS)
    )
    number = {item.media_id: int(item.media_id[1:]) for item in shows}
    trackers = TV.objects.bulk_create(
        TV(
            item=item,
            user=user,
            status=None if number[item.media_id] % 97 == 0 else STATUSES[number[item.media_id] % 5],
            score=Decimal(3) if number[item.media_id] % 4 == 0 else None,
        )
        for item in shows
    )
    tracker = {tv.item.media_id: tv for tv in trackers}

    season_items = Item.objects.bulk_create(
        Item(
            media_id=item.media_id,
            source=item.source,
            media_type=MediaTypes.SEASON.value,
            season_number=season_number,
            title=item.title,
            image="x",
        )
        for item in shows
        # One show in forty has no seasons at all.
        if number[item.media_id] % 40 != 39
        for season_number in ([0] if number[item.media_id] % 6 == 0 else []) + [1, 2, 3, 4]
    )
    seasons = Season.objects.bulk_create(
        Season(
            item=season_item,
            related_tv=tracker[season_item.media_id],
            user=user,
            status=(
                Status.DROPPED.value
                if number[season_item.media_id] % 11 == 0 and season_item.season_number == 2
                else tracker[season_item.media_id].status or Status.IN_PROGRESS.value
            ),
            rewatch_started_at=(
                REWATCH_STARTED
                if number[season_item.media_id] % 9 == 0 and season_item.season_number == 1
                else None
            ),
        )
        for season_item in season_items
    )
    season = {(row.item.media_id, row.item.season_number): row for row in seasons}

    episode_items = []
    for season_item in season_items:
        i = number[season_item.media_id]
        s = season_item.season_number
        for e in range(1, (2 if s == 0 else 6) + 1):
            release = BASE + timedelta(days=s * 30 + e)
            if i % 8 == 0 and s == 4 and e > 3:
                release = now + timedelta(days=30 + e)
            if i % 19 == 0 and s == 3:
                release = None
            runtime = 20 + (i * 7) % 53 + e
            if i % 10 == 3:
                runtime = None
            elif i % 10 == 4 and e > 3:
                runtime = 999998
            elif i % 10 == 5 and e == 2:
                runtime = None
            episode_items.append(
                Item(
                    media_id=season_item.media_id,
                    source=season_item.source,
                    media_type=MediaTypes.EPISODE.value,
                    season_number=s,
                    episode_number=e,
                    title=season_item.title,
                    image="x",
                    release_datetime=release,
                    runtime_minutes=runtime,
                ),
            )
    episode_items = Item.objects.bulk_create(episode_items)

    Event.objects.bulk_create(
        Event(
            item=season_item,
            content_number=e,
            datetime=(
                now + timedelta(days=30 + e)
                if number[season_item.media_id] % 8 == 0 and season_item.season_number == 4 and e > 4
                else BASE + timedelta(days=season_item.season_number * 30 + e)
            ),
        )
        for season_item in season_items
        if number[season_item.media_id] % 4 == 0 and season_item.season_number
        for e in range(1, 8 if number[season_item.media_id] % 16 == 0 else 7)
    )

    plays = []

    def play(episode_item, when, status=Status.COMPLETED.value, **extra):
        plays.append(
            Episode(
                item=episode_item,
                related_season=season[(episode_item.media_id, episode_item.season_number)],
                end_date=when,
                status=status,
                **extra,
            ),
        )

    for episode_item in episode_items:
        i = number[episode_item.media_id]
        s, e = episode_item.season_number, episode_item.episode_number
        when = BASE + timedelta(days=i % 500, hours=s * 10 + e)
        if i % 14 == 0 and e == 1:
            when = None
        if s == 0:
            if i % 12 == 0 and e == 1:
                play(episode_item, BASE + timedelta(days=700 + i % 50))
            continue
        if i % 9 == 0 and s == 1:
            # An old full pass, then part of the rewatch.
            play(episode_item, BASE + timedelta(days=10 + e))
            if e <= i % 4:
                play(episode_item, BASE + timedelta(days=401 + e))
            if i % 18 == 0 and e == 5:
                # Dated only, on the day the rewatch began: local midnight.
                play(
                    episode_item,
                    timezone.localtime(REWATCH_STARTED).replace(
                        hour=0, minute=0, second=0, microsecond=0
                    ),
                )
            continue
        pattern = i % 6
        if pattern in (1, 4):
            play(episode_item, when)
            if pattern == 4 and i % 5 == 0 and e == 2:
                play(episode_item, now, rating_only=True)
                play(episode_item, now, order_archived=True)
        elif pattern == 2:
            if s == 1 or (s == 2 and e <= 3):
                play(episode_item, when)
        elif pattern == 3:
            if s == 1 and e in (1, 3):
                play(episode_item, when)
        elif pattern == 5:
            if s == 1:
                play(episode_item, when)
                if e % 2:
                    play(episode_item, when + timedelta(days=30) if when else None)
            elif (s, e) == (2, 1) or (s, e) == (3, 4):
                play(episode_item, when, status=Status.DROPPED.value)
            elif (s, e) == (3, 1):
                play(episode_item, when)
    Episode.objects.bulk_create(plays)

    untracked = Item.objects.create(
        media_id="untracked",
        source="tmdb",
        media_type=MediaTypes.TV.value,
        title="Untracked",
        image="x",
    )
    CollectionEntry.objects.create(user=user, item=untracked)
    return untracked


class TVShowFactsTests(TestCase):
    """Facts read as rows equal what the models derive from their graph."""

    @classmethod
    def setUpTestData(cls):
        """Build the library once."""
        cls.user = get_user_model().objects.create_user(username="facts", password="x")
        cls.other = get_user_model().objects.create_user(username="other", password="x")
        cls.untracked = build_library(cls.user)

    def _hydrated(self):
        return list(
            BasicMedia.objects._apply_prefetch_related(
                TV.objects.filter(user=self.user).select_related("item"),
                MediaTypes.TV.value,
                list_mode=True,
            ),
        )

    def test_facts_match_model_properties(self):
        """Progress, first and last play dates agree with TV and Season for every show."""
        facts = tv_show_facts(self.user)
        hydrated = self._hydrated()
        self.assertEqual(len(hydrated), SHOWS)
        rewatching = 0
        for tv in hydrated:
            show = facts.get(tv.pk) or ShowFacts()
            label = tv.item.media_id
            self.assertEqual(show.progress, tv.progress, label)
            self.assertEqual(show.end_date, tv.end_date, label)
            self.assertEqual(show.completed_episode_count, tv.completed_episode_count, label)
            self.assertEqual(
                TrackedShow(tv.pk, tv.item, tv.status, tv.created_at, None, show).start_date,
                tv.start_date,
                label,
            )
            self.assertEqual(
                sorted(
                    (season.season_number, season.status, season.progress)
                    for season in show.seasons
                ),
                sorted(
                    (season.item.season_number, season.status, season.progress)
                    for season in tv.seasons.all()
                ),
                label,
            )
            rewatching += tv.is_rewatching
        # The library must actually hold the hard cases.
        self.assertGreater(rewatching, 5)
        self.assertTrue(any(tv.pk not in facts for tv in hydrated))

    def test_facts_for_selected_shows_match_the_whole_library(self):
        """Narrowing by id, in either query form, changes nothing but the keys."""
        every = tv_show_facts(self.user)
        few = sorted(every)[:7]
        self.assertEqual(tv_show_facts(self.user, few), {pk: every[pk] for pk in few})
        with mock.patch("app.tv_show_facts.MAX_SHOW_ID_PARAMETERS", 3):
            self.assertEqual(
                tv_show_facts(self.user, few), {pk: every[pk] for pk in few}
            )
        self.assertEqual(tv_show_facts(self.user, []), {})

    def test_facts_never_read_another_users_shows(self):
        """Ids of someone else's trackers return nothing."""
        tv_ids = list(TV.objects.filter(user=self.user).values_list("pk", flat=True))
        self.assertEqual(tv_show_facts(self.other, tv_ids[:5]), {})
        self.assertEqual(tv_show_facts(self.other), {})

    def test_facts_cost_does_not_grow_with_the_library(self):
        """Three value queries and no models, for one show or all of them."""
        tv_ids = list(TV.objects.filter(user=self.user).values_list("pk", flat=True))
        with self.assertNumQueries(3):
            tv_show_facts(self.user, tv_ids[:1])
        with (
            self.assertNumQueries(3),
            mock.patch.object(
                models.Model,
                "from_db",
                side_effect=AssertionError("built a model"),
            ),
        ):
            tv_show_facts(self.user)


class TimeLeftOrderIdentityTests(TestCase):
    """The order ranked from facts is the order ranked from hydrated trackers."""

    @classmethod
    def setUpTestData(cls):
        """Build the library once."""
        cls.user = get_user_model().objects.create_user(username="order", password="x")
        cls.untracked = build_library(cls.user)

    def _entries(self, *, prefetch_graph):
        tracked = list(
            BasicMedia.objects.get_media_list(
                user=self.user,
                media_type=MediaTypes.TV.value,
                status_filter=(),
                sort_filter="time_left",
                prefetch_graph=prefetch_graph,
            ),
        )
        statusless = TV.objects.filter(user=self.user, status__isnull=True)
        statusless = statusless.select_related("item")
        if prefetch_graph:
            statusless = BasicMedia.objects._apply_prefetch_related(
                statusless, MediaTypes.TV.value, list_mode=True
            )
        entries = [MediaListEntry.from_media(media) for media in tracked]
        entries += [MediaListEntry.from_media(media) for media in statusless]
        entries.append(MediaListEntry(item=self.untracked, media=None))
        return entries

    def _from_hydrated_trackers(self, direction):
        entries = self._entries(prefetch_graph=True)
        BasicMedia.objects.annotate_max_progress(
            [entry.media for entry in entries if entry.media is not None],
            MediaTypes.TV.value,
        )
        return [
            (entry.item_id, entry.time_left_display, entry.episodes_left_display)
            for entry in _sort_tv_media_by_time_left(entries, direction)
        ]

    def _from_facts(self, direction):
        records = build_time_left_records(
            self.user, self._entries(prefetch_graph=False)
        )
        return [
            (record.item_pk, record.time_left_display, record.episodes_left_display)
            for record in sort_time_left_records(records, direction)
        ]

    def test_both_producers_give_the_same_order_and_display(self):
        """Same ids, same display values, both directions."""
        ascending = self._from_facts("asc")
        self.assertEqual(ascending, self._from_hydrated_trackers("asc"))
        self.assertEqual(self._from_facts("desc"), self._from_hydrated_trackers("desc"))
        self.assertEqual(self._from_facts("desc"), ascending[::-1])
        self.assertEqual(len(ascending), SHOWS + 1)

    def test_library_reaches_every_group(self):
        """Guard the fixture: an identity over one group would prove little."""
        records = build_time_left_records(
            self.user, self._entries(prefetch_graph=False)
        )
        ordered = sort_time_left_records(records, "asc")
        by_display = Counter()
        for record in ordered:
            display = record.time_left_display
            if record.status == Status.DROPPED.value:
                by_display["dropped", display in ("-", "0m")] += 1
            elif display == "-":
                by_display["tail"] += 1
            elif display == "0m":
                by_display["caught up", record.status] += 1
            else:
                by_display["active"] += 1
        for group in (
            "active",
            "tail",
            ("caught up", Status.IN_PROGRESS.value),
            ("caught up", Status.COMPLETED.value),
            ("dropped", True),
            ("dropped", False),
        ):
            self.assertGreater(by_display[group], 0, group)
        # The untracked entry and the statusless trackers close the list.
        self.assertIsNone(ordered[-1].media_pk)

    def test_ranking_builds_no_season_or_play_models(self):
        """Five value queries rank the library; trackers arrive with their item."""
        entries = self._entries(prefetch_graph=False)
        built = Counter()
        original = models.Model.from_db.__func__

        def from_db(cls, db, field_names, values):
            built[cls.__name__] += 1
            return original(cls, db, field_names, values)

        with (
            mock.patch.object(models.Model, "from_db", classmethod(from_db)),
            CaptureQueriesContext(connection) as queries,
        ):
            records = build_time_left_records(self.user, entries)
            sort_time_left_records(records, "asc")
        self.assertEqual(dict(built), {})
        # Seasons, plays, rewatch plays, two release counts, episode runtimes.
        self.assertLessEqual(len(queries), 6)


class TimeLeftColdBuildTests(TestCase):
    """A list request with no cached order hydrates one page, not the library."""

    @classmethod
    def setUpTestData(cls):
        """Build the library once."""
        cls.user = get_user_model().objects.create_user(username="cold", password="x")
        build_library(cls.user)

    def setUp(self):
        """Start cold and signed in."""
        cache.clear()
        self.client.force_login(self.user)

    def _request(self, query=""):
        built = Counter()
        original = models.Model.from_db.__func__

        def from_db(cls, db, field_names, values):
            built[cls.__name__] += 1
            return original(cls, db, field_names, values)

        with mock.patch.object(models.Model, "from_db", classmethod(from_db)):
            response = self.client.get(
                f"/medialist/tv?sort=time_left&direction=asc&status=All{query}",
            )
        self.assertEqual(response.status_code, 200)
        return response, built

    def test_cold_build_hydrates_the_same_graph_as_a_cached_page(self):
        """Season, play and event models are built for the page in both cases."""
        cold_response, cold = self._request()
        warm_response, warm = self._request()
        self.assertGreater(cold_response.context["media_list"].paginator.count, 64)
        for model_name in ("Season", "Episode", "Event"):
            self.assertEqual(cold[model_name], warm[model_name], model_name)
        self.assertEqual(
            [entry.item_id for entry in cold_response.context["media_list"]],
            [entry.item_id for entry in warm_response.context["media_list"]],
        )
        self.assertEqual(
            [entry.time_left_display for entry in cold_response.context["media_list"]],
            [entry.time_left_display for entry in warm_response.context["media_list"]],
        )

    def test_progress_filter_matches_the_hydrated_answer(self):
        """Caught-up filtering from facts keeps the shows the helper keeps."""
        from app import helpers

        hydrated = list(
            BasicMedia.objects.get_media_list(
                user=self.user,
                media_type=MediaTypes.TV.value,
                status_filter=(),
                sort_filter="time_left",
            ),
        )
        BasicMedia.objects.annotate_max_progress(hydrated, MediaTypes.TV.value)
        caught_up = {
            media.item_id for media in hydrated if helpers.is_caught_up_media(media)
        }
        self.assertTrue(caught_up)
        self.assertLess(len(caught_up), len(hydrated))
        for value, expected in (
            ("caught_up", caught_up),
            ("not_caught_up", {media.item_id for media in hydrated} - caught_up),
        ):
            cache.clear()
            response, built = self._request(f"&progress={value}")
            page = response.context["media_list"]
            self.assertEqual(page.paginator.count, len(expected), value)
            self.assertLessEqual({entry.item_id for entry in page}, expected, value)
            self.assertLess(built["Season"], 5 * 40, value)


class ShowOrderPageCostTests(TestCase):
    """The library's progress and date orders rank shows without their graph."""

    @classmethod
    def setUpTestData(cls):
        """Build the library once."""
        cls.user = get_user_model().objects.create_user(username="orders", password="x")
        build_library(cls.user)

    def setUp(self):
        """Sign in."""
        self.client.force_login(self.user)

    def test_each_order_hydrates_one_page_of_shows(self):
        """Shows, seasons and plays are built for the page, not the library."""
        for sort in ("progress", "plays", "start_date", "end_date", "runtime", "time_watched"):
            for direction in ("asc", "desc"):
                cache.clear()
                built = Counter()
                original = models.Model.from_db.__func__

                def from_db(cls, db, field_names, values, built=built, original=original):
                    built[cls.__name__] += 1
                    return original(cls, db, field_names, values)

                with mock.patch.object(models.Model, "from_db", classmethod(from_db)):
                    response = self.client.get(
                        f"/medialist/tv?sort={sort}&direction={direction}&status=All",
                    )
                page = response.context["media_list"]
                cards = len(page.object_list)
                with self.subTest(sort=sort, direction=direction):
                    self.assertGreater(page.paginator.count, 2 * cards)
                    self.assertLessEqual(built["TV"], cards)
                    self.assertLess(built["Season"], 6 * cards)
