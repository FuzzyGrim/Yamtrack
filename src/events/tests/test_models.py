import datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from app.models import (
    TV,
    Anime,
    Item,
    Manga,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Status,
)
from events.models import Event


class EventModelTests(TestCase):
    """Test the Event model."""

    def setUp(self):
        """Set up test data."""
        self.credentials = {"username": "testuser", "password": "testpassword"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        # Create test items
        self.season_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test TV Show",
            season_number=1,
        )

        self.movie_item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
        )

        self.anime_item = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Test Anime",
        )

        self.manga_item = Item.objects.create(
            media_id="66296374554",
            source=Sources.MANGAUPDATES.value,
            media_type=MediaTypes.MANGA.value,
            title="Test Manga",
        )

        self.season = Season.objects.create(
            user=self.user,
            item=self.season_item,
            status=Status.IN_PROGRESS.value,
        )

        self.movie = Movie.objects.create(
            user=self.user,
            item=self.movie_item,
            status=Status.PLANNING.value,
        )

        self.anime = Anime.objects.create(
            user=self.user,
            item=self.anime_item,
            status=Status.IN_PROGRESS.value,
        )

        self.manga = Manga.objects.create(
            user=self.user,
            item=self.manga_item,
            status=Status.IN_PROGRESS.value,
        )

        # Create events
        self.now = timezone.now()
        self.tomorrow = self.now + datetime.timedelta(days=1)
        self.next_week = self.now + datetime.timedelta(days=7)

        self.season_event = Event.objects.create(
            item=self.season_item,
            content_number=1,
            datetime=self.tomorrow,
        )

        self.movie_event = Event.objects.create(
            item=self.movie_item,
            datetime=self.next_week,
        )

        self.anime_event = Event.objects.create(
            item=self.anime_item,
            content_number=1,
            datetime=self.tomorrow,
        )

        self.manga_event = Event.objects.create(
            item=self.manga_item,
            content_number=1,
            datetime=self.tomorrow,
        )

    def test_event_string_representation(self):
        """Test the string representation of events."""
        # Season event
        self.assertEqual(
            str(self.season_event),
            "Test TV Show S01E01",
        )

        # Movie event
        self.assertEqual(str(self.movie_event), "Test Movie")

        # Anime event
        self.assertEqual(str(self.anime_event), "Test Anime E1")

        # Manga event
        self.assertEqual(str(self.manga_event), "Test Manga #1")


class EventManagerTests(TestCase):
    """Test the EventManager custom manager."""

    def setUp(self):
        """Set up test data."""
        self.credentials = {"username": "testuser", "password": "testpassword"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        self.credentials_other = {"username": "otheruser", "password": "testpassword"}
        self.other_user = get_user_model().objects.create_user(**self.credentials_other)

        # Create test items
        self.tv_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test TV Show",
        )

        self.season_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test TV Show",
            season_number=1,
        )

        self.movie_item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
        )

        self.paused_movie_item = Item.objects.create(
            media_id="278",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Paused Movie",
        )

        self.dropped_movie_item = Item.objects.create(
            media_id="424",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Dropped Movie",
        )

        self.manga_item = Item.objects.create(
            media_id="66296374554",
            source=Sources.MANGAUPDATES.value,
            media_type=MediaTypes.MANGA.value,
            title="Test Manga",
        )

        # Create media objects
        self.tv = TV.objects.create(
            user=self.user,
            item=self.tv_item,
            status=Status.IN_PROGRESS.value,
        )

        self.other_tv = TV.objects.create(
            user=self.other_user,
            item=self.tv_item,
            status=Status.IN_PROGRESS.value,
        )

        self.movie = Movie.objects.create(
            user=self.user,
            item=self.movie_item,
            status=Status.PLANNING.value,
        )

        self.paused_movie = Movie.objects.create(
            user=self.user,
            item=self.paused_movie_item,
            status=Status.PAUSED.value,
        )

        self.dropped_movie = Movie.objects.create(
            user=self.user,
            item=self.dropped_movie_item,
            status=Status.DROPPED.value,
        )

        self.manga = Manga.objects.create(
            user=self.user,
            item=self.manga_item,
            status=Status.IN_PROGRESS.value,
        )

        # Use fixed dates instead of timezone.now()
        # Base date: April 15, 2025 at noon UTC
        self.base_date = datetime.datetime(2025, 4, 15, 12, 0, 0, tzinfo=datetime.UTC)
        self.yesterday = self.base_date - datetime.timedelta(days=1)  # April 14
        self.tomorrow = self.base_date + datetime.timedelta(days=1)  # April 16
        self.next_week = self.base_date + datetime.timedelta(days=7)  # April 22

        # Create events with fixed dates
        self.past_event = Event.objects.create(
            item=self.season_item,
            content_number=1,
            datetime=self.yesterday,  # April 14
        )

        self.movie_event = Event.objects.create(
            item=self.movie_item,
            datetime=self.next_week,  # April 22
        )

        self.paused_movie_event = Event.objects.create(
            item=self.paused_movie_item,
            datetime=self.next_week,  # April 22
        )

        self.dropped_movie_event = Event.objects.create(
            item=self.dropped_movie_item,
            datetime=self.next_week,  # April 22
        )

        self.season_event = Event.objects.create(
            item=self.season_item,
            content_number=2,
            datetime=self.tomorrow,  # April 16
        )

        # Manga with multiple events
        self.manga_event1 = Event.objects.create(
            item=self.manga_item,
            content_number=1,
            datetime=self.tomorrow,  # April 16
        )

        self.manga_event2 = Event.objects.create(
            item=self.manga_item,
            content_number=2,
            datetime=self.next_week,  # April 22
        )

    def test_get_user_events(self):
        """Test the get_user_events method."""
        # Use fixed dates for testing
        today = self.base_date.date()  # April 15
        next_week = today + datetime.timedelta(days=7)  # April 22

        # Get events for the user
        events = Event.objects.get_user_events(self.user, today, next_week)

        # Should include season, movie, and manga events
        self.assertEqual(events.count(), 4)
        self.assertIn(self.season_event, events)
        self.assertIn(self.manga_event1, events)
        self.assertIn(self.movie_event, events)
        self.assertIn(self.manga_event2, events)
        self.assertNotIn(self.past_event, events)

        # Get events for the other user
        other_events = Event.objects.get_user_events(self.other_user, today, next_week)

        # Other user has TV as active, so get season events
        self.assertEqual(other_events.count(), 1)

        # Test with a different date range
        tomorrow = today + datetime.timedelta(days=1)  # April 16
        limited_events = Event.objects.get_user_events(self.user, today, tomorrow)

        self.assertEqual(limited_events.count(), 2)
        self.assertIn(self.season_event, limited_events)  # Season event in range
        self.assertIn(self.manga_event1, limited_events)  # Manga event in range
        self.assertNotIn(self.movie_event, limited_events)  # Outside range
        self.assertNotIn(
            self.past_event,
            limited_events,
        )  # Past event, but filtered by active status

    def test_get_user_events_hides_dropped_season_and_later(self):
        """A dropped/paused season hides itself and every later season (regression for #1005 perf rewrite)."""
        dropped_tv_item = Item.objects.create(
            media_id="9999",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Dropped Partway Show",
        )
        TV.objects.create(
            user=self.user,
            item=dropped_tv_item,
            status=Status.IN_PROGRESS.value,
        )

        season1_item = Item.objects.create(
            media_id="9999",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Dropped Partway Show",
            season_number=1,
        )
        season2_item = Item.objects.create(
            media_id="9999",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Dropped Partway Show",
            season_number=2,
        )
        season3_item = Item.objects.create(
            media_id="9999",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Dropped Partway Show",
            season_number=3,
        )
        Season.objects.create(
            user=self.user,
            item=season2_item,
            status=Status.DROPPED.value,
        )

        season1_event = Event.objects.create(
            item=season1_item,
            content_number=1,
            datetime=self.tomorrow,
        )
        season2_event = Event.objects.create(
            item=season2_item,
            content_number=1,
            datetime=self.tomorrow,
        )
        season3_event = Event.objects.create(
            item=season3_item,
            content_number=1,
            datetime=self.tomorrow,
        )

        today = self.base_date.date()
        next_week = today + datetime.timedelta(days=7)
        events = Event.objects.get_user_events(self.user, today, next_week)

        self.assertIn(season1_event, events)
        self.assertNotIn(season2_event, events)
        self.assertNotIn(season3_event, events)


class EventManagerCrossProviderDedupTests(TestCase):
    """The calendar must not show the same real episode twice (#639)."""

    def setUp(self):
        self.credentials = {"username": "dedup-cal-user", "password": "testpass123"}
        self.user = get_user_model().objects.create_user(**self.credentials)

    def _tv_pair(self, tvdb_id="81189"):
        """Create a show tracked under both a TMDB and a verified TVDB identity."""
        tmdb_show = Item.objects.create(
            title="Breaking Bad",
            media_id="1396",
            media_type=MediaTypes.TV.value,
            source=Sources.TMDB.value,
            image="",
            provider_external_ids={"tvdb_id": tvdb_id},
        )
        tvdb_show = Item.objects.create(
            title="Breaking Bad",
            media_id=tvdb_id,
            media_type=MediaTypes.TV.value,
            source=Sources.TVDB.value,
            image="",
        )
        TV.objects.create(
            item=tmdb_show,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        TV.objects.create(
            item=tvdb_show,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        tmdb_season = Item.objects.create(
            title="Breaking Bad",
            media_id="1396",
            media_type=MediaTypes.SEASON.value,
            source=Sources.TMDB.value,
            image="",
            season_number=1,
        )
        tvdb_season = Item.objects.create(
            title="Breaking Bad",
            media_id=tvdb_id,
            media_type=MediaTypes.SEASON.value,
            source=Sources.TVDB.value,
            image="",
            season_number=1,
        )
        return tmdb_season, tvdb_season

    def test_get_user_events_hides_non_preferred_duplicate(self):
        tmdb_season, tvdb_season = self._tv_pair()
        when = timezone.now() + datetime.timedelta(days=1)
        tmdb_event = Event.objects.create(
            item=tmdb_season,
            content_number=1,
            datetime=when,
        )
        tvdb_event = Event.objects.create(
            item=tvdb_season,
            content_number=1,
            datetime=when,
        )
        self.user.tv_metadata_source_default = Sources.TMDB.value
        self.user.save(update_fields=["tv_metadata_source_default"])

        events = Event.objects.get_user_events(self.user, when.date(), when.date())

        self.assertEqual(list(events), [tmdb_event])
        self.assertNotIn(tvdb_event, events)

    def test_get_user_events_follows_preferred_source_switch(self):
        tmdb_season, tvdb_season = self._tv_pair()
        when = timezone.now() + datetime.timedelta(days=1)
        tmdb_event = Event.objects.create(
            item=tmdb_season,
            content_number=1,
            datetime=when,
        )
        tvdb_event = Event.objects.create(
            item=tvdb_season,
            content_number=1,
            datetime=when,
        )
        self.user.tv_metadata_source_default = Sources.TVDB.value
        self.user.save(update_fields=["tv_metadata_source_default"])

        events = Event.objects.get_user_events(self.user, when.date(), when.date())

        self.assertEqual(list(events), [tvdb_event])
        self.assertNotIn(tmdb_event, events)

    def test_get_user_events_keeps_unrelated_shows_sharing_a_title(self):
        """Two unrelated shows sharing a title but no verified tvdb link both survive."""
        remake_show = Item.objects.create(
            title="Breaking Bad",
            media_id="999999",
            media_type=MediaTypes.TV.value,
            source=Sources.TMDB.value,
            image="",
        )
        remake_season = Item.objects.create(
            title="Breaking Bad",
            media_id="999999",
            media_type=MediaTypes.SEASON.value,
            source=Sources.TMDB.value,
            image="",
            season_number=1,
        )
        tvdb_show = Item.objects.create(
            title="Breaking Bad",
            media_id="81189",
            media_type=MediaTypes.TV.value,
            source=Sources.TVDB.value,
            image="",
        )
        tvdb_season = Item.objects.create(
            title="Breaking Bad",
            media_id="81189",
            media_type=MediaTypes.SEASON.value,
            source=Sources.TVDB.value,
            image="",
            season_number=1,
        )
        TV.objects.create(
            item=remake_show,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        TV.objects.create(
            item=tvdb_show,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        when = timezone.now() + datetime.timedelta(days=1)
        remake_event = Event.objects.create(
            item=remake_season,
            content_number=1,
            datetime=when,
        )
        tvdb_event = Event.objects.create(
            item=tvdb_season,
            content_number=1,
            datetime=when,
        )

        events = Event.objects.get_user_events(self.user, when.date(), when.date())

        self.assertCountEqual(list(events), [remake_event, tvdb_event])

    def test_get_user_events_hides_absolute_numbered_show_seasons(self):
        """TMDB absolute numbering vs TVDB split seasons must fully dedupe (#1007).

        TMDB puts every episode under one absolute "Season 1"; TVDB splits the
        same show into Seasons 1-2. Season-number matching alone only hides
        TVDB Season 1 (leaving TVDB Season 2 to duplicate the calendar), so
        the show-level dedupe in `_active_tv_show_media_ids` must exclude the
        non-preferred show's media_id before its seasons are ever queried.
        """
        tvdb_id = "81189"
        tmdb_show = Item.objects.create(
            title="Re:Zero",
            media_id="1396",
            media_type=MediaTypes.TV.value,
            source=Sources.TMDB.value,
            image="",
            provider_external_ids={"tvdb_id": tvdb_id},
        )
        tvdb_show = Item.objects.create(
            title="Re:Zero",
            media_id=tvdb_id,
            media_type=MediaTypes.TV.value,
            source=Sources.TVDB.value,
            image="",
        )
        TV.objects.create(
            item=tmdb_show,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        TV.objects.create(
            item=tvdb_show,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        tmdb_season1 = Item.objects.create(
            title="Re:Zero",
            media_id="1396",
            media_type=MediaTypes.SEASON.value,
            source=Sources.TMDB.value,
            image="",
            season_number=1,
        )
        tvdb_season1 = Item.objects.create(
            title="Re:Zero",
            media_id=tvdb_id,
            media_type=MediaTypes.SEASON.value,
            source=Sources.TVDB.value,
            image="",
            season_number=1,
        )
        tvdb_season2 = Item.objects.create(
            title="Re:Zero",
            media_id=tvdb_id,
            media_type=MediaTypes.SEASON.value,
            source=Sources.TVDB.value,
            image="",
            season_number=2,
        )
        self.user.tv_metadata_source_default = Sources.TMDB.value
        self.user.save(update_fields=["tv_metadata_source_default"])
        when = timezone.now() + datetime.timedelta(days=1)
        tmdb_event = Event.objects.create(
            item=tmdb_season1,
            content_number=1,
            datetime=when,
        )
        Event.objects.create(item=tvdb_season1, content_number=1, datetime=when)
        Event.objects.create(item=tvdb_season2, content_number=1, datetime=when)

        events = Event.objects.get_user_events(self.user, when.date(), when.date())

        self.assertEqual(list(events), [tmdb_event])

    def test_get_user_events_stays_a_queryset_for_media_type_filtering(self):
        """download_calendar chains a media-type filter after get_user_events."""
        tmdb_season, _tvdb_season = self._tv_pair()
        when = timezone.now() + datetime.timedelta(days=1)
        Event.objects.create(item=tmdb_season, content_number=1, datetime=when)

        events = Event.objects.get_user_events(self.user, when.date(), when.date())
        filtered = events.filter(item__media_type__in=[MediaTypes.SEASON.value])

        self.assertEqual(filtered.count(), 1)


class EventManagerCrossBucketAnimeDedupTests(TestCase):
    """The calendar must not show the same real episode twice across buckets (#968)."""

    def setUp(self):
        self.credentials = {"username": "dedup-anime-user", "password": "testpass123"}
        self.user = get_user_model().objects.create_user(**self.credentials)

    def _anime_item(self, mal_id="52991"):
        anime_item = Item.objects.create(
            title="Test Anime",
            media_id=mal_id,
            media_type=MediaTypes.ANIME.value,
            source=Sources.MAL.value,
            image="",
        )
        Anime.objects.create(
            item=anime_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        return anime_item

    def _tv_show(self, media_id="1396", source=Sources.TMDB.value):
        tv_item = Item.objects.create(
            title="Test TV Show",
            media_id=media_id,
            media_type=MediaTypes.TV.value,
            source=source,
            image="",
        )
        season_item = Item.objects.create(
            title="Test TV Show - Season 1",
            media_id=media_id,
            media_type=MediaTypes.SEASON.value,
            source=source,
            image="",
            season_number=1,
        )
        TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        return season_item

    def test_hides_anime_duplicate_with_matching_tmdb_show(self):
        anime_item = self._anime_item()
        season_item = self._tv_show(media_id="1396", source=Sources.TMDB.value)
        when = timezone.now() + datetime.timedelta(days=1)
        anime_event = Event.objects.create(
            item=anime_item,
            content_number=1,
            datetime=when,
        )
        season_event = Event.objects.create(
            item=season_item,
            content_number=1,
            datetime=when,
        )

        with mock.patch(
            "events.models.resolve_provider_series_id",
            side_effect=lambda mal_id, provider: (
                "1396" if provider == "tmdb" else None
            ),
        ):
            events = Event.objects.get_user_events(self.user, when.date(), when.date())

        self.assertEqual(list(events), [season_event])
        self.assertNotIn(anime_event, events)

    def test_hides_anime_duplicate_with_matching_tvdb_show(self):
        anime_item = self._anime_item()
        season_item = self._tv_show(media_id="81189", source=Sources.TVDB.value)
        when = timezone.now() + datetime.timedelta(days=1)
        anime_event = Event.objects.create(
            item=anime_item,
            content_number=1,
            datetime=when,
        )
        season_event = Event.objects.create(
            item=season_item,
            content_number=1,
            datetime=when,
        )

        with mock.patch(
            "events.models.resolve_provider_series_id",
            side_effect=lambda mal_id, provider: (
                "81189" if provider == "tvdb" else None
            ),
        ):
            events = Event.objects.get_user_events(self.user, when.date(), when.date())

        self.assertEqual(list(events), [season_event])
        self.assertNotIn(anime_event, events)

    def test_keeps_anime_event_without_tv_counterpart(self):
        anime_item = self._anime_item()
        when = timezone.now() + datetime.timedelta(days=1)
        anime_event = Event.objects.create(
            item=anime_item,
            content_number=1,
            datetime=when,
        )

        with mock.patch(
            "events.models.resolve_provider_series_id",
            return_value=None,
        ):
            events = Event.objects.get_user_events(self.user, when.date(), when.date())

        self.assertEqual(list(events), [anime_event])

    def test_keeps_both_when_mapping_does_not_match_tracked_show(self):
        anime_item = self._anime_item()
        season_item = self._tv_show(media_id="1396", source=Sources.TMDB.value)
        when = timezone.now() + datetime.timedelta(days=1)
        anime_event = Event.objects.create(
            item=anime_item,
            content_number=1,
            datetime=when,
        )
        season_event = Event.objects.create(
            item=season_item,
            content_number=1,
            datetime=when,
        )

        with mock.patch(
            "events.models.resolve_provider_series_id",
            return_value=None,
        ):
            events = Event.objects.get_user_events(self.user, when.date(), when.date())

        self.assertCountEqual(list(events), [anime_event, season_event])

    def test_hides_anime_duplicate_via_title_fallback_when_mapping_misses(self):
        anime_item = Item.objects.create(
            title="Re:Zero Season 4",
            media_id="63830",
            media_type=MediaTypes.ANIME.value,
            source=Sources.MAL.value,
            image="",
        )
        Anime.objects.create(
            item=anime_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        tv_item = Item.objects.create(
            title="Re:Zero",
            media_id="1396",
            media_type=MediaTypes.TV.value,
            source=Sources.TMDB.value,
            image="",
        )
        season_item = Item.objects.create(
            title="Re:Zero - Season 4",
            media_id="1396",
            media_type=MediaTypes.SEASON.value,
            source=Sources.TMDB.value,
            image="",
            season_number=4,
        )
        TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        when = timezone.now() + datetime.timedelta(days=1)
        anime_event = Event.objects.create(
            item=anime_item,
            content_number=1,
            datetime=when,
        )
        season_event = Event.objects.create(
            item=season_item,
            content_number=1,
            datetime=when,
        )

        with mock.patch(
            "events.models.resolve_provider_series_id",
            return_value=None,
        ):
            events = Event.objects.get_user_events(self.user, when.date(), when.date())

        self.assertEqual(list(events), [season_event])
        self.assertNotIn(anime_event, events)


class CalendarActiveShowLoadTests(TestCase):
    """The calendar reads the user's active shows once, and only the columns it needs."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="cal", password="pw")
        self.when = timezone.now() + datetime.timedelta(days=1)

    def _add_shows(self, count, start=0):
        for index in range(start, start + count):
            show = Item.objects.create(
                media_id=f"cal-show-{index}",
                source=Sources.TMDB.value,
                media_type=MediaTypes.TV.value,
                title=f"Show {index}",
            )
            TV.objects.create(
                item=show,
                user=self.user,
                status=Status.IN_PROGRESS.value,
            )
            season = Item.objects.create(
                media_id=f"cal-show-{index}",
                source=Sources.TMDB.value,
                media_type=MediaTypes.SEASON.value,
                title=f"Show {index}",
                season_number=1,
            )
            Event.objects.create(item=season, content_number=1, datetime=self.when)

    def _run(self):
        with CaptureQueriesContext(connection) as captured:
            events = list(
                Event.objects.get_user_events(
                    self.user,
                    self.when.date(),
                    self.when.date(),
                ),
            )
        return events, captured.captured_queries

    def test_active_shows_are_loaded_once_without_unneeded_columns(self):
        self._add_shows(3)

        events, queries = self._run()

        self.assertEqual(len(events), 3)
        active_show_reads = [
            query["sql"]
            for query in queries
            if 'FROM "app_item"' in query["sql"] and '"app_tv"' in query["sql"]
        ]
        self.assertEqual(len(active_show_reads), 1)
        self.assertNotIn('"app_item"."title"', active_show_reads[0])

    def test_query_count_does_not_grow_with_library_size(self):
        self._add_shows(3)
        _events, small = self._run()
        self._add_shows(30, start=3)
        _events, large = self._run()

        self.assertEqual(len(large), len(small))


class CalendarActiveAnimeLoadTests(TestCase):
    """The anime dedup pass reads only the columns it uses, not the large JSON ones."""

    def test_active_anime_items_load_only_needed_columns(self):
        user = get_user_model().objects.create_user(username="cal-anime", password="pw")
        anime = Item.objects.create(
            media_id="52991",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Test Anime",
            synopsis="A long synopsis the calendar never prints.",
        )
        Anime.objects.create(
            item=anime,
            user=user,
            status=Status.IN_PROGRESS.value,
        )
        when = timezone.now() + datetime.timedelta(days=1)
        Event.objects.create(item=anime, content_number=1, datetime=when)

        with CaptureQueriesContext(connection) as captured:
            list(Event.objects.get_user_events(user, when.date(), when.date()))

        anime_reads = [
            query["sql"]
            for query in captured.captured_queries
            if 'FROM "app_item"' in query["sql"]
            and '"app_anime"' in query["sql"]
            and "events_event" not in query["sql"]
        ]
        self.assertEqual(len(anime_reads), 1)
        self.assertIn('"app_item"."title"', anime_reads[0])
        self.assertNotIn('"app_item"."synopsis"', anime_reads[0])


class EventDatetimeIndexTests(TestCase):
    """The calendar's month range must not scan every event ever stored."""

    def test_datetime_has_an_index(self):
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(
                cursor,
                Event._meta.db_table,
            )

        self.assertTrue(
            any(
                details["index"] and details["columns"] == ["datetime"]
                for details in constraints.values()
            ),
            "events_event.datetime must be indexed for the calendar's date range",
        )


class CalendarOtherUserStatusIsolationTests(TestCase):
    """One account's Paused/Dropped tracker must not hide another's events (#1873)."""

    def setUp(self):
        """Two users track the same items; only the second one pauses them."""
        User = get_user_model()
        self.user = User.objects.create_user(username="active", password="pw")
        self.other = User.objects.create_user(username="paused", password="pw")
        self.when = timezone.now()

    def _events_for(self, user):
        return list(
            Event.objects.get_user_events(user, self.when.date(), self.when.date()),
        )

    def test_tv_season_event_survives_other_users_paused_show(self):
        """A season event stays visible when only another user paused the show."""
        show = Item.objects.create(
            media_id="1396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Shared Show",
            image="",
        )
        season = Item.objects.create(
            media_id="1396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Shared Show",
            image="",
            season_number=1,
        )
        TV.objects.create(item=show, user=self.user, status=Status.IN_PROGRESS.value)
        TV.objects.create(item=show, user=self.other, status=Status.PAUSED.value)
        Season.objects.create(
            item=season,
            user=self.user,
            related_tv=TV.objects.get(item=show, user=self.user),
            status=Status.IN_PROGRESS.value,
        )
        event = Event.objects.create(item=season, content_number=1, datetime=self.when)

        self.assertEqual(self._events_for(self.user), [event])

    def test_movie_event_survives_other_users_dropped_movie(self):
        """A movie event stays visible when only another user dropped it."""
        movie = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Shared Movie",
            image="",
        )
        Movie.objects.create(item=movie, user=self.user, status=Status.PLANNING.value)
        Movie.objects.create(item=movie, user=self.other, status=Status.DROPPED.value)
        event = Event.objects.create(item=movie, content_number=1, datetime=self.when)

        self.assertEqual(self._events_for(self.user), [event])

    def test_anime_event_survives_other_users_paused_anime(self):
        """An anime event stays visible when only another user paused it."""
        anime = Item.objects.create(
            media_id="52991",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Shared Anime",
            image="",
        )
        Anime.objects.create(item=anime, user=self.user, status=Status.IN_PROGRESS.value)
        Anime.objects.create(item=anime, user=self.other, status=Status.PAUSED.value)
        event = Event.objects.create(item=anime, content_number=1, datetime=self.when)

        self.assertEqual(self._events_for(self.user), [event])

    def test_anime_dedupe_ignores_other_users_paused_anime(self):
        """The anime/TV duplicate is still hidden when another user paused the anime."""
        anime = Item.objects.create(
            media_id="52992",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Dup Anime",
            image="",
        )
        Anime.objects.create(item=anime, user=self.user, status=Status.IN_PROGRESS.value)
        Anime.objects.create(item=anime, user=self.other, status=Status.PAUSED.value)
        show = Item.objects.create(
            media_id="1396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Dup Anime",
            image="",
        )
        season = Item.objects.create(
            media_id="1396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Dup Anime",
            image="",
            season_number=1,
        )
        TV.objects.create(item=show, user=self.user, status=Status.IN_PROGRESS.value)
        anime_event = Event.objects.create(item=anime, content_number=1, datetime=self.when)
        season_event = Event.objects.create(item=season, content_number=1, datetime=self.when)

        with mock.patch(
            "events.models.resolve_provider_series_id",
            side_effect=lambda mal_id, provider: (
                "1396" if provider == "tmdb" else None
            ),
        ):
            events = self._events_for(self.user)

        self.assertEqual(events, [season_event])
        self.assertNotIn(anime_event, events)

    def test_own_paused_movie_is_still_hidden(self):
        """The user's own Paused tracker keeps hiding the event."""
        movie = Item.objects.create(
            media_id="239",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Own Paused Movie",
            image="",
        )
        Movie.objects.create(item=movie, user=self.user, status=Status.PAUSED.value)
        Event.objects.create(item=movie, content_number=1, datetime=self.when)

        self.assertEqual(self._events_for(self.user), [])


class CalendarDroppedSeasonTests(TestCase):
    """Dropped or paused seasons hide themselves and later seasons (SURFACE-16).

    The check used to be a correlated EXISTS that SQLite ran once per event,
    walking every season the user tracks each time. These pin the visible
    rows and that the month query no longer reads seasons per event.
    """

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="drop", password="pw")
        self.other = get_user_model().objects.create_user(username="drop-other", password="pw")
        self.when = timezone.now() + datetime.timedelta(days=1)

    def _show(self, media_id, numbers):
        show = Item.objects.create(
            media_id=media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title=f"Show {media_id}",
        )
        TV.objects.create(item=show, user=self.user, status=Status.IN_PROGRESS.value)
        seasons = {}
        for number in numbers:
            seasons[number] = Item.objects.create(
                media_id=media_id,
                source=Sources.TMDB.value,
                media_type=MediaTypes.SEASON.value,
                title=f"Show {media_id}",
                season_number=number,
            )
            Event.objects.create(item=seasons[number], content_number=1, datetime=self.when)
        return seasons

    def _visible_items(self):
        day = self.when.date()
        return {
            (event.item.media_id, event.item.season_number)
            for event in Event.objects.get_user_events(self.user, day, day)
        }

    def test_paused_season_hides_itself_and_later_seasons_only(self):
        seasons = self._show("701", [1, 2, 3, 4])
        Season.objects.create(user=self.user, item=seasons[3], status=Status.PAUSED.value)

        self.assertEqual(self._visible_items(), {("701", 1), ("701", 2)})

    def test_earliest_inactive_season_decides(self):
        seasons = self._show("702", [1, 2, 3])
        Season.objects.create(user=self.user, item=seasons[3], status=Status.DROPPED.value)
        Season.objects.create(user=self.user, item=seasons[2], status=Status.PAUSED.value)
        Season.objects.create(user=self.user, item=seasons[1], status=Status.IN_PROGRESS.value)

        self.assertEqual(self._visible_items(), {("702", 1)})

    def test_another_users_dropped_season_hides_nothing(self):
        seasons = self._show("703", [1, 2])
        other_show = Item.objects.get(media_id="703", media_type=MediaTypes.TV.value)
        TV.objects.create(item=other_show, user=self.other, status=Status.IN_PROGRESS.value)
        Season.objects.create(user=self.other, item=seasons[1], status=Status.DROPPED.value)

        self.assertEqual(self._visible_items(), {("703", 1), ("703", 2)})

    def test_dropped_season_of_one_show_leaves_other_shows(self):
        dropped = self._show("704", [1, 2])
        self._show("705", [1, 2])
        Season.objects.create(user=self.user, item=dropped[1], status=Status.DROPPED.value)

        self.assertEqual(self._visible_items(), {("705", 1), ("705", 2)})

    def test_month_query_does_not_read_seasons_per_event(self):
        seasons = self._show("706", [1, 2])
        Season.objects.create(user=self.user, item=seasons[2], status=Status.DROPPED.value)
        day = self.when.date()

        with CaptureQueriesContext(connection) as captured:
            list(Event.objects.get_user_events(self.user, day, day))

        month_query = next(
            query["sql"] for query in captured.captured_queries
            if query["sql"].startswith('SELECT "events_event"."id"')
        )
        self.assertNotIn('"app_season"', month_query)
