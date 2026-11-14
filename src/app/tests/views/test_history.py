from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import formats, timezone

from app import history_cache
from app.models import (
    CREDITS_BACKFILL_VERSION,
    TV,
    Album,
    Artist,
    Book,
    Comic,
    CreditRoleType,
    Episode,
    Game,
    Item,
    ItemPersonCredit,
    ItemStudioCredit,
    Manga,
    MediaTypes,
    MetadataBackfillField,
    MetadataBackfillState,
    Movie,
    Music,
    Person,
    PersonGender,
    Podcast,
    PodcastShow,
    Season,
    Sources,
    Status,
    Studio,
    Track,
)


def _begin_model_metadata_patches():
    """Start the provider patches and return them for the caller to stop.

    `setUpTestData` runs as a classmethod with no `addCleanup`, so a class-level
    fixture needs to start and stop these around its own object creation. Test
    bodies keep using `_start_model_metadata_patches`.
    """
    patchers = [
        patch(
            "app.models.item.providers.services.get_media_metadata",
            return_value={"max_progress": 1},
        ),
        patch(
            "app.models.media.providers.services.get_media_metadata",
            return_value={"max_progress": 1},
        ),
        patch("app.models.Item.fetch_releases"),
    ]
    for patcher in patchers:
        patcher.start()
    return patchers


def _start_model_metadata_patches(test_case):
    """Prevent history view tests from making provider calls during model saves."""
    mock_item_media_metadata = patch(
        "app.models.item.providers.services.get_media_metadata",
        return_value={"max_progress": 1},
    )
    mock_media_media_metadata = patch(
        "app.models.media.providers.services.get_media_metadata",
        return_value={"max_progress": 1},
    )
    mock_fetch_releases = patch("app.models.Item.fetch_releases")
    mock_item_media_metadata.start()
    mock_media_media_metadata.start()
    mock_fetch_releases.start()
    test_case.addCleanup(mock_item_media_metadata.stop)
    test_case.addCleanup(mock_media_media_metadata.stop)
    test_case.addCleanup(mock_fetch_releases.stop)


class HistoryModalViewTests(TestCase):
    """Test the history modal view."""

    def setUp(self):
        """Create a user and log in."""
        _start_model_metadata_patches(self)
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        self.item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
        )
        self.movie = Movie.objects.create(
            item=self.item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=0,
        )

        self.movie.status = Status.COMPLETED.value
        self.movie.progress = 1
        self.movie.score = 8
        self.movie.end_date = timezone.now()
        self.movie.save()

    def test_history_modal_view(self):
        """Test the history modal view."""
        response = self.client.get(
            reverse(
                "history_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                },
            )
            + "?return_url=/home",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/fill_history.html")

        self.assertIn("timeline", response.context)
        self.assertGreater(len(response.context["timeline"]), 0)

        first_entry = response.context["timeline"][0]
        self.assertIn("changes", first_entry)
        self.assertGreater(len(first_entry["changes"]), 0)

    def test_filtered_history_page_uses_full_track_modal_for_movie_cards(self):
        """Filtered history cards should open the standard track modal for editable plays."""
        response = self.client.get(
            reverse("history")
            + f"?media_type={MediaTypes.MOVIE.value}&media_id={self.item.media_id}&source={self.item.source}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'hx-get="{reverse("track_modal", kwargs={"source": Sources.TMDB.value, "media_type": MediaTypes.MOVIE.value, "media_id": "238"})}"',
            html=False,
        )
        self.assertContains(
            response,
            f'"instance_id": "{self.movie.id}"',
            html=False,
        )
        self.assertContains(response, '"standard_modal": "1"', html=False)
        self.assertContains(response, ">8<", html=False)
        self.assertNotContains(
            response,
            f'hx-get="{reverse("history_modal", kwargs={"source": Sources.TMDB.value, "media_type": MediaTypes.MOVIE.value, "media_id": "238"})}"',
            html=False,
        )

    def test_filtered_history_page_shows_game_score_inline(self):
        """Filtered game history cards should surface per-entry ratings without opening the editor."""
        game_item = Item.objects.create(
            media_id="game-1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.GAME.value,
            title="Test Game",
            image="http://example.com/game.jpg",
        )
        Game.objects.create(
            item=game_item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=120,
            score=9.5,
            start_date=timezone.now() - timedelta(hours=2),
            end_date=timezone.now(),
        )

        response = self.client.get(
            reverse("history")
            + f"?media_type={MediaTypes.GAME.value}&media_id={game_item.media_id}&source={game_item.source}&logging_style=sessions",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, ">9.5<", html=False)

    def test_in_progress_movie_history_shows_the_whole_chain(self):
        """Records written before the finish stay in the timeline (issue #1278)."""
        response = self.client.get(
            reverse(
                "history_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                },
            ),
        )

        # One entry for the in-progress creation, one for the finish.
        self.assertEqual(len(response.context["timeline"]), 2)


def _descriptions(entry):
    return [change["description"] for change in entry["changes"]]


@patch(
    "app.providers.services.get_media_metadata",
    return_value={
        "season/1": {"episodes": [{"episode_number": 1}, {"episode_number": 2}]},
        "max_progress": 2,
    },
)
class EpisodeHistoryModalTests(TestCase):
    """An in-progress episode play is listed, so it can be edited (issue #1278)."""

    def setUp(self):
        """Track one episode: a finished play, then an in-progress one."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)
        with patch(
            "app.providers.services.get_media_metadata",
            return_value={"season/1": {"episodes": [{"episode_number": 1}]}},
        ):
            tv_item = Item.objects.create(
                media_id="123",
                source=Sources.TMDB.value,
                media_type=MediaTypes.TV.value,
                title="Show",
            )
            tv = TV.objects.create(item=tv_item, user=self.user)
            season_item = Item.objects.create(
                media_id="123",
                source=Sources.TMDB.value,
                media_type=MediaTypes.SEASON.value,
                title="Show",
                season_number=1,
            )
            self.season = Season.objects.create(
                item=season_item,
                user=self.user,
                related_tv=tv,
                status=Status.IN_PROGRESS.value,
            )
            self.episode_item = Item.objects.create(
                media_id="123",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title="Show",
                season_number=1,
                episode_number=1,
            )
            self.finished_play = Episode.objects.create(
                item=self.episode_item,
                related_season=self.season,
                end_date=timezone.now() - timedelta(days=30),
            )
            self.started_at = timezone.now() - timedelta(hours=1)
            self.in_progress_play = Episode.objects.create(
                item=self.episode_item,
                related_season=self.season,
                status=Status.IN_PROGRESS.value,
                start_date=self.started_at,
                end_date=None,
            )

    def _history_modal(self):
        return self.client.get(
            reverse(
                "history_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.EPISODE.value,
                    "media_id": "123",
                    "season_number": 1,
                    "episode_number": 1,
                },
            ),
        )

    def test_in_progress_play_is_listed_and_editable(self, _mock_metadata):
        """Without an entry the play has no edit or delete control."""
        response = self._history_modal()

        entries = {
            entry["instance_id"]: entry for entry in response.context["timeline"]
        }
        self.assertIn(self.in_progress_play.id, entries)
        self.assertContains(response, f"instance_id={self.in_progress_play.id}")

    def test_instances_are_numbered_in_the_order_they_were_added(
        self,
        _mock_metadata,
    ):
        """Numbering must not depend on where a database sorts NULL end dates."""
        response = self._history_modal()

        numbers = {
            entry["instance_id"]: entry["media_entry_number"]
            for entry in response.context["timeline"]
        }
        self.assertEqual(numbers[self.finished_play.id], 1)
        self.assertEqual(numbers[self.in_progress_play.id], 2)

    def test_in_progress_play_reads_as_started_not_finished(self, _mock_metadata):
        """The open play has no end date, so it must not claim a finish."""
        response = self._history_modal()

        entry = next(
            entry
            for entry in response.context["timeline"]
            if entry["instance_id"] == self.in_progress_play.id
        )
        descriptions = _descriptions(entry)
        self.assertTrue(any(d.startswith("Started on") for d in descriptions))
        self.assertNotIn("Finished without date", descriptions)

    def test_finished_play_keeps_its_plain_timeline(self, _mock_metadata):
        """A normal play shows its finish date and no status line."""
        response = self._history_modal()

        entry = next(
            entry
            for entry in response.context["timeline"]
            if entry["instance_id"] == self.finished_play.id
        )
        descriptions = _descriptions(entry)
        self.assertTrue(any(d.startswith("Finished on") for d in descriptions))
        self.assertFalse(any(d.startswith("Marked as") for d in descriptions))

    def test_finishing_the_play_is_recorded(self, _mock_metadata):
        """Completing the open play adds a finish entry to its timeline."""
        self.in_progress_play.status = Status.COMPLETED.value
        self.in_progress_play.end_date = timezone.now()
        self.in_progress_play.save()

        response = self._history_modal()

        descriptions = [
            description
            for entry in response.context["timeline"]
            if entry["instance_id"] == self.in_progress_play.id
            for description in _descriptions(entry)
        ]
        self.assertTrue(any(d.startswith("Started on") for d in descriptions))
        self.assertTrue(any(d.startswith("Finished on") for d in descriptions))


class PodcastHistoryModalTests(TestCase):
    """Pocket Casts sync writes in-progress records the modal keeps hidden."""

    def setUp(self):
        """Track one podcast episode still in progress."""
        _start_model_metadata_patches(self)
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)
        item = Item.objects.create(
            media_id="podcast-episode",
            source=Sources.POCKETCASTS.value,
            media_type=MediaTypes.PODCAST.value,
            title="Podcast Episode",
        )
        show = PodcastShow.objects.create(podcast_uuid="show", title="Show")
        Podcast.objects.create(
            item=item,
            user=self.user,
            show=show,
            status=Status.IN_PROGRESS.value,
            progress=10,
        )

    def test_in_progress_sync_records_stay_hidden(self):
        """Only records with an end date describe a listen."""
        response = self.client.get(
            reverse(
                "history_modal",
                kwargs={
                    "source": Sources.POCKETCASTS.value,
                    "media_type": MediaTypes.PODCAST.value,
                    "media_id": "podcast-episode",
                },
            ),
        )

        self.assertEqual(response.context["timeline"], [])


class SessionHistoryModalTests(TestCase):
    """Test the in-place, day-grouped session history fragment."""

    def setUp(self):
        """Create a user and log in with provider work disabled."""
        _start_model_metadata_patches(self)
        self.credentials = {"username": "session-history", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)
        self.base_time = timezone.now().replace(second=0, microsecond=0)

    def _url(self, **params):
        return reverse("activity_sessions_modal"), params

    def _create_item(
        self,
        media_type,
        media_id,
        title,
        source=Sources.MANUAL.value,
        **extra,
    ):
        return Item.objects.create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            title=title,
            image="http://example.com/poster.jpg",
            runtime_minutes=42,
            **extra,
        )

    def test_modal_accepts_each_detail_filter_shape(self):
        """Each detail-page querystring shape returns the modal fragment."""
        movie_item = self._create_item(MediaTypes.MOVIE.value, "movie", "Movie")
        Movie.objects.create(
            item=movie_item,
            user=self.user,
            status=Status.COMPLETED.value,
            end_date=self.base_time,
        )

        tv_item = self._create_item(MediaTypes.TV.value, "show", "Show")
        tv = TV.objects.create(item=tv_item, user=self.user)
        season_item = self._create_item(
            MediaTypes.SEASON.value,
            "show",
            "Season",
            season_number=1,
        )
        season = Season.objects.create(item=season_item, related_tv=tv, user=self.user)
        episode_item = self._create_item(
            MediaTypes.EPISODE.value,
            "show",
            "Episode",
            season_number=1,
            episode_number=1,
        )
        Episode.objects.create(
            item=episode_item,
            related_season=season,
            end_date=self.base_time,
        )

        game_item = self._create_item(MediaTypes.GAME.value, "game", "Game")
        Game.objects.create(
            item=game_item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=30,
            start_date=self.base_time - timedelta(minutes=30),
            end_date=self.base_time,
        )

        show = PodcastShow.objects.create(
            podcast_uuid="session-history-show",
            title="Podcast Show",
        )
        podcast_item = self._create_item(
            MediaTypes.PODCAST.value,
            "podcast-episode",
            "Podcast Episode",
        )
        Podcast.objects.create(
            item=podcast_item,
            user=self.user,
            show=show,
            status=Status.COMPLETED.value,
            progress=20,
            end_date=self.base_time,
        )

        artist = Artist.objects.create(name="Session Artist")
        album = Album.objects.create(title="Session Album", artist=artist)
        track = Track.objects.create(album=album, title="Session Track", duration_ms=180000)
        music_item = self._create_item(MediaTypes.MUSIC.value, "music-track", "Track")
        Music.objects.create(
            item=music_item,
            user=self.user,
            album=album,
            artist=artist,
            track=track,
            status=Status.COMPLETED.value,
            progress=1,
            end_date=self.base_time,
        )

        book_item = self._create_item(MediaTypes.BOOK.value, "book", "Book")
        Book.objects.create(
            item=book_item,
            user=self.user,
            status=Status.COMPLETED.value,
            end_date=self.base_time,
        )

        cases = (
            {
                "media_type": MediaTypes.MOVIE.value,
                "media_id": "movie",
                "source": Sources.MANUAL.value,
            },
            {
                "media_type": MediaTypes.TV.value,
                "media_id": "show",
                "source": Sources.MANUAL.value,
            },
            {
                "media_type": MediaTypes.TV.value,
                "media_id": "show",
                "source": Sources.MANUAL.value,
                "season_number": 1,
            },
            {
                "media_type": MediaTypes.GAME.value,
                "media_id": "game",
                "source": Sources.MANUAL.value,
                "logging_style": "sessions",
            },
            {"media_type": MediaTypes.PODCAST.value, "podcast_show": show.id},
            {"album": album.id},
            {
                "media_type": MediaTypes.BOOK.value,
                "media_id": "book",
                "source": Sources.MANUAL.value,
            },
        )
        for params in cases:
            with self.subTest(params=params):
                response = self.client.get(*self._url(**params))
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(
                    response,
                    "app/components/session_history_modal.html",
                )

    def test_episode_modal_filters_both_season_and_episode_number(self):
        """An episode action must not show neighboring episodes or seasons."""
        tv_item = self._create_item(MediaTypes.TV.value, "scoped-show", "Show")
        tv = TV.objects.create(item=tv_item, user=self.user)
        for season_number in (1, 2):
            season_item = self._create_item(
                MediaTypes.SEASON.value,
                "scoped-show",
                "Season",
                season_number=season_number,
            )
            season = Season.objects.create(
                item=season_item, related_tv=tv, user=self.user,
            )
            for episode_number in (1, 2):
                episode_item = self._create_item(
                    MediaTypes.EPISODE.value,
                    "scoped-show",
                    f"Episode {episode_number}",
                    season_number=season_number,
                    episode_number=episode_number,
                )
                Episode.objects.create(
                    item=episode_item,
                    related_season=season,
                    end_date=self.base_time,
                    notes=f"unique-note-s{season_number}e{episode_number}",
                )

        response = self.client.get(
            *self._url(
                media_type=MediaTypes.TV.value,
                media_id="scoped-show",
                source=Sources.MANUAL.value,
                season_number=1,
                episode_number=2,
            ),
        )
        self.assertContains(response, "unique-note-s1e2")
        for excluded in ("unique-note-s1e1", "unique-note-s2e1", "unique-note-s2e2"):
            self.assertNotContains(response, excluded)

    def test_movie_repeat_rows_include_notes_scores_and_all_markers(self):
        """Repeated movie instances remain separate rows across their days."""
        item = self._create_item(MediaTypes.MOVIE.value, "repeat-movie", "Repeat Movie")
        first = Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            score=7,
            notes="First viewing note",
            end_date=self.base_time - timedelta(days=1),
        )
        second = Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            score=8,
            notes="Second viewing note",
            end_date=self.base_time,
        )

        response = self.client.get(
            *self._url(
                media_type=MediaTypes.MOVIE.value,
                media_id=item.media_id,
                source=item.source,
            ),
        )

        self.assertEqual(response.status_code, 200)
        days = response.context["history_days"]
        self.assertEqual([len(day["entries"]) for day in days], [1, 1])
        self.assertEqual(
            response.context["marker_days"],
            sorted(day["date"].isoformat() for day in days),
        )
        body = response.content.decode()
        self.assertIn("First viewing note", body)
        self.assertIn("Second viewing note", body)
        self.assertIn(">7<", body)
        self.assertIn(">8<", body)
        self.assertIn(f'"instance_id": "{first.id}"', body)
        self.assertIn(f'"instance_id": "{second.id}"', body)

    def test_episode_game_notes_and_long_note_snippet_render(self):
        """Rows show current notes/scores and truncate long plain-text notes."""
        item = self._create_item(MediaTypes.GAME.value, "noted-game", "Noted Game")
        Game.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=45,
            score=9.5,
            notes="x" * 200,
            start_date=self.base_time - timedelta(minutes=45),
            end_date=self.base_time,
        )
        response = self.client.get(
            *self._url(
                media_type=MediaTypes.GAME.value,
                media_id=item.media_id,
                source=item.source,
                logging_style="sessions",
            ),
        )
        self.assertContains(response, ">9.5<", html=False)
        self.assertContains(response, "Show more", html=False)
        self.assertContains(response, "…", html=False)
        self.assertContains(response, 'x-show="noteExpanded"', html=False)

    def test_session_history_stats_cover_filtered_gaps_streaks_and_weekdays(self):
        """Stats describe only the filtered item's dated activity."""
        item = self._create_item(MediaTypes.MOVIE.value, "stats-movie", "Stats Movie")
        other_item = self._create_item(
            MediaTypes.MOVIE.value,
            "stats-other-movie",
            "Other Movie",
        )
        local_today = timezone.localtime(self.base_time).date()
        monday = local_today - timedelta(days=local_today.weekday() + 14)
        active_dates = (monday, monday + timedelta(days=1), monday + timedelta(days=3), monday + timedelta(days=7))
        for active_date in active_dates:
            days_ago = (local_today - active_date).days
            Movie.objects.create(
                item=item,
                user=self.user,
                status=Status.COMPLETED.value,
                end_date=self.base_time - timedelta(days=days_ago),
            )
        Movie.objects.create(
            item=other_item,
            user=self.user,
            status=Status.COMPLETED.value,
            end_date=self.base_time,
        )

        response = self.client.get(
            *self._url(
                media_type=MediaTypes.MOVIE.value,
                media_id=item.media_id,
                source=item.source,
            ),
        )

        stats = {stat["label"]: stat["value"] for stat in response.context["session_history_stats"]}
        self.assertEqual(stats["Active days"], "4")
        self.assertEqual(stats["Activity entries"], "4")
        self.assertEqual(stats["Tracked time"], "2h 48min")
        self.assertEqual(stats["Average per active day"], "42min")
        self.assertEqual(stats["Average gap"], "2.3 days")
        self.assertEqual(stats["Longest streak"], "2 days")
        self.assertEqual(stats["Most active weekday"], formats.date_format(monday, "l"))
        self.assertContains(response, "At a glance", html=False)
        self.assertContains(response, "data-calendar-component", html=False)
        self.assertContains(response, "showMonthsView", html=False)
        self.assertContains(response, "showYearsView", html=False)
        self.assertContains(response, "calendarDayIsInteractive", html=False)

    def test_session_history_stats_ignore_undated_entries_for_calendar_metrics(self):
        """Undated rows count as activity but do not create calendar dates."""
        item = self._create_item(MediaTypes.BOOK.value, "stats-book", "Stats Book")
        dated = Book.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            end_date=self.base_time,
        )
        Book.objects.create(
            item=item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        response = self.client.get(
            *self._url(
                media_type=MediaTypes.BOOK.value,
                media_id=item.media_id,
                source=item.source,
            ),
        )

        stats = {stat["label"]: stat["value"] for stat in response.context["session_history_stats"]}
        self.assertEqual(stats["Active days"], "1")
        self.assertEqual(stats["Activity entries"], "2")
        self.assertEqual(stats["Tracked time"], "0min")
        self.assertEqual(stats["Average per active day"], "0min")
        self.assertEqual(stats["Average gap"], "-")
        self.assertEqual(stats["Longest streak"], "1 day")
        self.assertEqual(
            stats["Most active weekday"],
            formats.date_format(dated.end_date, "l"),
        )

    def test_undated_history_is_grouped_and_other_users_are_excluded(self):
        """Single-item filters include the unknown-date group but stay user-scoped."""
        item = self._create_item(MediaTypes.BOOK.value, "undated-book", "Undated Book")
        Book.objects.create(item=item, user=self.user, status=Status.COMPLETED.value)

        response = self.client.get(
            *self._url(
                media_type=MediaTypes.BOOK.value,
                media_id=item.media_id,
                source=item.source,
            ),
        )
        self.assertContains(response, "Unknown date", html=False)
        self.assertIsNone(response.context["history_days"][-1]["date"])
        # The unknown-date group must not read "Unknown Unknown date".
        self.assertNotContains(response, ">Unknown</span>", html=False)

        other_item = self._create_item(MediaTypes.BOOK.value, "other-book", "Other Book")
        other_user = get_user_model().objects.create_user(
            username="session-history-other",
            password="12345",
        )
        Book.objects.create(
            item=other_item,
            user=other_user,
            status=Status.COMPLETED.value,
            end_date=self.base_time,
        )
        other_response = self.client.get(
            *self._url(
                media_type=MediaTypes.BOOK.value,
                media_id=other_item.media_id,
                source=other_item.source,
            ),
        )
        self.assertFalse(other_response.context["history_days"])

    @patch("app.history_views.SESSION_HISTORY_MAX_DAYS", 1)
    def test_history_cap_shows_full_history_link(self):
        """The marker set uses all days while rendering only the capped prefix."""
        item = self._create_item(MediaTypes.BOOK.value, "capped-book", "Capped Book")
        for days_ago in (0, 1):
            Book.objects.create(
                item=item,
                user=self.user,
                status=Status.COMPLETED.value,
                end_date=self.base_time - timedelta(days=days_ago),
            )
        response = self.client.get(
            *self._url(
                media_type=MediaTypes.BOOK.value,
                media_id=item.media_id,
                source=item.source,
            ),
        )
        self.assertEqual(len(response.context["history_days"]), 1)
        self.assertEqual(len(response.context["marker_days"]), 2)
        self.assertEqual(len(response.context["visible_marker_days"]), 1)
        self.assertContains(response, "View History", html=False)


class DeleteHistoryRecordViewTests(TestCase):
    """Test the delete history record view."""

    def setUp(self):
        """Create a user and log in."""
        _start_model_metadata_patches(self)
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        self.item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
        )
        self.movie = Movie.objects.create(
            item=self.item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=0,
        )

        self.movie.status = Status.COMPLETED.value
        self.movie.progress = 1
        self.movie.score = 8
        self.movie.end_date = timezone.now()
        self.movie.save()

        self.history = self.movie.history.first()
        self.history_id = self.history.history_id

        # Manually update the history_user field
        self.history.history_user = self.user
        self.history.save()

    def test_delete_history_record(self):
        """Test deleting a history record."""
        # Verify the history record exists before deletion
        self.assertEqual(
            self.movie.history.filter(history_id=self.history_id).count(),
            1,
        )
        self.assertTrue(
            Movie.objects.filter(id=self.movie.id).exists(),
        )

        response = self.client.delete(
            reverse(
                "delete_history_record",
                kwargs={
                    "media_type": MediaTypes.MOVIE.value,
                    "history_id": self.history_id,
                },
            ),
        )

        self.assertEqual(response.status_code, 200)

        # Verify the history record is actually deleted from the database
        self.assertEqual(
            self.movie.history.filter(history_id=self.history_id).count(),
            0,
        )
        # Verify the live movie instance is removed
        self.assertFalse(
            Movie.objects.filter(id=self.movie.id).exists(),
        )

    def test_delete_failure_returns_the_written_message(self):
        """A deletion that fails answers with the service's own text."""
        from app.fork_services_history import HistoryDeletionError

        with patch(
            "app.history_views.fork_services_history.delete_history_record_core",
            side_effect=HistoryDeletionError("Deletion failed"),
        ):
            response = self.client.delete(
                reverse(
                    "delete_history_record",
                    kwargs={"media_type": "movie", "history_id": self.history_id},
                ),
            )
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.content.decode(), "Deletion failed")

    def test_delete_history_oob_uses_the_row_id(self):
        """The out-of-band fragments carry the loaded row's id, not the query text."""
        artist = Artist.objects.create(name="OOB Artist")
        album = Album.objects.create(title="OOB Album", artist=artist)
        music = Music.objects.create(
            item=Item.objects.create(
                media_id="oob-track",
                source=Sources.MANUAL.value,
                media_type=MediaTypes.MUSIC.value,
                title="OOB Track",
            ),
            user=self.user,
            album=album,
            artist=artist,
            status=Status.COMPLETED.value,
            progress=1,
        )
        podcast = Podcast.objects.create(
            item=Item.objects.create(
                media_id="oob-episode",
                source=Sources.POCKETCASTS.value,
                media_type=MediaTypes.PODCAST.value,
                title="OOB Episode",
            ),
            user=self.user,
            show=PodcastShow.objects.create(podcast_uuid="oob-show", title="Show"),
            status=Status.IN_PROGRESS.value,
            progress=10,
        )
        cases = (
            ("music", "music_id", music.id, ("track-history", "modal-listen-count")),
            ("podcast", "podcast_id", podcast.id, ("modal-listen-count",)),
        )
        for media_type, parameter, row_id, prefixes in cases:
            url = reverse(
                "delete_history_record",
                kwargs={"media_type": media_type, "history_id": 1},
            )
            with patch(
                "app.history_views.fork_services_history.delete_history_record_core",
            ):
                # int() accepts the padded form; the response must not echo it.
                response = self.client.delete(f"{url}?{parameter}=0{row_id}")
                missing = self.client.delete(f"{url}?{parameter}=999999")
            with self.subTest(media_type=media_type):
                self.assertEqual(response.status_code, 200)
                body = response.content.decode()
                for prefix in prefixes:
                    self.assertIn(f'id="{prefix}-{row_id}"', body)
                self.assertNotIn(f"-0{row_id}", body)
                self.assertEqual(missing.content.decode(), "")

    def test_delete_nonexistent_history_record(self):
        """Deleting a missing history record should return 404."""
        response = self.client.delete(
            reverse(
                "delete_history_record",
                kwargs={
                    "media_type": MediaTypes.MOVIE.value,
                    "history_id": 999999,
                },
            ),
        )

        self.assertEqual(response.status_code, 404)

    @patch("app.history_cache_reader.schedule_history_day_cache_coverage")
    @patch("app.history_cache_lifecycle.schedule_history_refresh", return_value=True)
    def test_delete_history_record_evicts_cached_day_before_refresh(
        self,
        mock_schedule_history_refresh,
        _mock_schedule_history_day_cache_coverage,
    ):
        """A deleted play must not survive while its async cache refresh waits."""
        cache.clear()
        logging_style = "repeats"
        earlier_time = self.movie.end_date - timedelta(days=400)
        earlier_movie = Movie.objects.create(
            item=self.item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=1,
            start_date=earlier_time,
            end_date=earlier_time,
        )
        earlier_history = earlier_movie.history.first()
        earlier_history.history_user = self.user
        earlier_history.save(update_fields=["history_user"])

        later_day_key = history_cache.history_day_key(self.movie.end_date)
        earlier_day_key = history_cache.history_day_key(earlier_movie.end_date)
        history_cache.refresh_history_cache(
            self.user.id,
            logging_style=logging_style,
            day_keys=[later_day_key, earlier_day_key],
        )
        later_cache_key = history_cache._day_cache_key(
            self.user.id,
            logging_style,
            later_day_key,
        )
        earlier_cache_key = history_cache._day_cache_key(
            self.user.id,
            logging_style,
            earlier_day_key,
        )
        self.assertTrue(cache.get(later_cache_key)["entries"])
        self.assertTrue(cache.get(earlier_cache_key)["entries"])
        mock_schedule_history_refresh.reset_mock()

        response = self.client.delete(
            reverse(
                "delete_history_record",
                kwargs={
                    "media_type": MediaTypes.MOVIE.value,
                    "history_id": self.history_id,
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(cache.get(later_cache_key))
        # Only the user's style (repeats) is rebuilt; the other is deleted.
        mock_schedule_history_refresh.assert_any_call(
            self.user.id,
            logging_style,
            warm_days=0,
            day_keys=[later_day_key],
        )
        self.assertEqual(
            {call.args[1] for call in mock_schedule_history_refresh.call_args_list},
            {logging_style},
        )

        later_page = self.client.get(
            reverse("history")
            + f"?year={self.movie.end_date.year}&m={self.movie.end_date.month}",
        )
        self.assertEqual(later_page.status_code, 200)
        self.assertFalse(
            any(
                entry["title"] == "Test Movie"
                for day in later_page.context["history_days"]
                for entry in day["entries"]
            ),
        )

        earlier_page = self.client.get(
            reverse("history")
            + f"?year={earlier_movie.end_date.year}&m={earlier_movie.end_date.month}",
        )
        self.assertEqual(earlier_page.status_code, 200)
        self.assertTrue(
            any(
                entry["title"] == "Test Movie"
                for day in earlier_page.context["history_days"]
                for entry in day["entries"]
            ),
        )


class HistoryMonthViewTests(TestCase):
    """Test unfiltered history month page behavior."""

    credentials = {"username": "month-view", "password": "12345"}

    @classmethod
    def setUpTestData(cls):
        """Build the fixture once for the class rather than once per test.

        This class spent 7.0s of its 22.0s re-creating the same user, show,
        season and episode for every test method. Django rolls each test back
        to this state, so building it here is equivalent and 20x cheaper.
        """
        patchers = _begin_model_metadata_patches()
        try:
            cls._build_fixture()
        finally:
            for patcher in reversed(patchers):
                patcher.stop()

    def setUp(self):
        # Patches and login stay per-test: the test client is per-test, and the
        # test bodies save models too.
        _start_model_metadata_patches(self)
        self.client.login(**self.credentials)
        cache.clear()

    @classmethod
    def _build_fixture(cls):
        cls.user = get_user_model().objects.create_user(**cls.credentials)
        now = timezone.now()

        item = Item.objects.create(
            media_id="month-movie",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MOVIE.value,
            title="Month View Movie",
            image="http://example.com/month-movie.jpg",
        )
        Movie.objects.create(
            item=item,
            user=cls.user,
            status=Status.COMPLETED.value,
            progress=1,
            start_date=now,
            end_date=now,
        )

        tv_item = Item.objects.create(
            media_id="month-tv",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.TV.value,
            title="Month View Show",
            image="http://example.com/month-tv.jpg",
        )
        tv = TV.objects.create(
            item=tv_item,
            user=cls.user,
            status=Status.COMPLETED.value,
        )
        season_item = Item.objects.create(
            media_id="month-tv",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.SEASON.value,
            title="Month View Show",
            image="http://example.com/month-tv-s1.jpg",
            season_number=1,
        )
        season = Season.objects.create(
            item=season_item,
            user=cls.user,
            related_tv=tv,
            status=Status.COMPLETED.value,
        )
        episode_item = Item.objects.create(
            media_id="month-tv",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.EPISODE.value,
            title="Month View Episode",
            image="http://example.com/month-tv-e1.jpg",
            season_number=1,
            episode_number=1,
        )
        with cls.captureOnCommitCallbacks(execute=True):
            Episode.objects.create(
                item=episode_item,
                related_season=season,
                end_date=now,
            )

    def test_default_month_view_does_not_bootstrap_cache_status_poll(self):
        response = self.client.get(reverse("history"))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["history_refreshing"])
        self.assertNotContains(response, "checkCacheStatus", html=False)
        self.assertNotContains(response, "/api/cache-status/", html=False)

    def test_month_nav_puts_older_months_left_and_newer_months_right(self):
        """Past is on the left and future on the right, like the Calendar."""
        response = self.client.get(reverse("history") + "?year=2020&m=6")
        html = response.content.decode()

        older = html.index("?year=2020&m=5")
        newer = html.index("?year=2020&m=7")
        back_a_year = html.index("?year=2019&m=6")
        current_month = html.index(f"?year={response.context['current_year']}&m=")

        self.assertLess(back_a_year, older)
        self.assertLess(older, html.index("<h2", older))
        self.assertLess(html.index("<h2", older), newer)
        self.assertLess(newer, current_month)

    def test_history_passes_media_type_from_query_into_context(self):
        response = self.client.get(
            reverse("history"),
            {"media_type": MediaTypes.MOVIE.value},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["media_type"], MediaTypes.MOVIE.value)

    def test_history_drops_invalid_media_type_from_query(self):
        response = self.client.get(
            reverse("history"),
            {"media_type": "not-a-media-type"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["media_type"])

    def test_history_keeps_the_users_search_type_in_the_search_bar(self):
        """The search bar shows what the user picked, not the page being browsed.

        A game's history link goes to ?media_type=game&...&logging_style=sessions;
        a user who last picked Books keeps Books in the bar.
        """
        self.user.last_search_type = MediaTypes.BOOK.value
        self.user.save(update_fields=["last_search_type"])
        response = self.client.get(
            reverse("history"),
            {
                "media_type": MediaTypes.GAME.value,
                "media_id": "1877",
                "source": "igdb",
                "logging_style": "sessions",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["media_type"], MediaTypes.GAME.value)
        self.assertContains(
            response,
            "selectedType: { display: 'Books', value: 'book' }",
        )

    def test_media_type_month_view_uses_cached_month_days(self):
        response = self.client.get(
            reverse("history"),
            {"media_type": MediaTypes.MOVIE.value},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["use_month_view"])
        self.assertFalse(response.context["history_refreshing"])
        self.assertContains(response, "Month View Movie")
        self.assertNotContains(response, "Month View Episode")
        self.assertContains(response, "media_type=movie", html=False)

    def test_media_type_month_view_multi_select(self):
        response = self.client.get(
            reverse("history"),
            {
                "media_type": f"{MediaTypes.MOVIE.value},{MediaTypes.TV.value}",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["use_month_view"])
        self.assertContains(response, "Month View Movie")
        self.assertContains(response, "Month View Episode")

    def test_media_type_multi_select_bypasses_month_cache(self):
        now = timezone.now()
        start_date = (now - timedelta(days=1)).date().isoformat()
        end_date = (now + timedelta(days=1)).date().isoformat()

        response = self.client.get(
            reverse("history"),
            {
                "media_type": f"{MediaTypes.MOVIE.value},{MediaTypes.TV.value}",
                "start-date": start_date,
                "end-date": end_date,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["use_month_view"])
        self.assertContains(response, "Month View Movie")
        self.assertContains(response, "Month View Episode")

    def _cache_large_history_day(self, *, keep_count=0, total_count=None):
        """Create and cache many same-day movie entries for pagination tests."""
        total_count = total_count or history_cache.HISTORY_ENTRIES_PER_DAY_PAGE + 5
        # Entries are spaced a second apart; keep them all on one local day even
        # when the suite runs in the first seconds after midnight.
        local_now = timezone.localtime()
        day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        now = max(local_now, day_start + timedelta(seconds=total_count))
        for index in range(total_count):
            item = Item.objects.create(
                media_id=f"large-history-{index}",
                source=Sources.MANUAL.value,
                media_type=MediaTypes.MOVIE.value,
                title=f"Large History {index:03d}",
                image=f"http://example.com/large-history-{index}.jpg",
                genres=["keep"] if index >= total_count - keep_count else ["skip"],
            )
            played_at = now - timedelta(seconds=index)
            Movie.objects.create(
                item=item,
                user=self.user,
                status=Status.COMPLETED.value,
                progress=1,
                start_date=played_at,
                end_date=played_at,
            )

        cache.clear()
        day_key = history_cache.history_day_key(now)
        day_payload = history_cache._build_and_cache_history_day(
            self.user,
            day_key,
            logging_style_override="repeats",
        )
        return day_key, len(day_payload["entries"])

    def test_large_month_day_renders_bounded_page_and_loads_more(self):
        day_key, total_entries = self._cache_large_history_day()

        response = self.client.get(reverse("history"))

        self.assertEqual(response.status_code, 200)
        day = next(
            day
            for day in response.context["history_days"]
            if day["day_key"] == day_key
        )
        self.assertEqual(
            day["entry_count"],
            total_entries,
        )
        self.assertEqual(
            len(day["entries"]),
            history_cache.HISTORY_ENTRIES_PER_DAY_PAGE,
        )
        self.assertTrue(day["has_more"])
        self.assertContains(response, "Load more")
        self.assertNotContains(response, "Large History 034")

        fragment = self.client.get(
            reverse("history_day_fragment", kwargs={"day_key": day_key}),
            {"entry_offset": history_cache.HISTORY_ENTRIES_PER_DAY_PAGE},
        )

        self.assertEqual(fragment.status_code, 200)
        self.assertContains(fragment, "Large History 034")
        self.assertContains(fragment, "Large History 030")
        self.assertNotContains(fragment, "Large History 000")
        self.assertNotContains(fragment, "Load more")

    def test_history_day_fragment_filters_before_pagination(self):
        day_key, _total_entries = self._cache_large_history_day(
            keep_count=5,
            total_count=history_cache.HISTORY_ENTRIES_PER_DAY_PAGE + 5,
        )
        music_item = Item.objects.create(
            media_id="large-history-music",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MUSIC.value,
            title="Large History Music",
            image="http://example.com/large-history-music.jpg",
            genres=["keep"],
        )
        played_at = timezone.now()
        Music.objects.create(
            item=music_item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=1,
            start_date=played_at,
            end_date=played_at,
        )
        cache.clear()
        history_cache._build_and_cache_history_day(
            self.user,
            day_key,
            logging_style_override="repeats",
        )

        response = self.client.get(
            reverse("history"),
            {"genre": "keep", "media_type": MediaTypes.MOVIE.value},
        )

        self.assertEqual(response.status_code, 200)
        day = next(
            day
            for day in response.context["history_days"]
            if day["day_key"] == day_key
        )
        self.assertEqual(day["entry_count"], 5)
        self.assertEqual(len(day["entries"]), 5)
        self.assertEqual(
            {entry["title"] for entry in day["entries"]},
            {f"Large History {index:03d}" for index in range(30, 35)},
        )
        self.assertNotContains(response, "Large History Music")
        self.assertFalse(day["has_more"])

    def test_history_page_embeds_a_bounded_feed_model(self):
        """The shared feed receives page metadata and bounded first slices."""
        day_key, total_entries = self._cache_large_history_day()

        response = self.client.get(reverse("history"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-history-feed-root")
        self.assertContains(response, "history-feed-props")
        model = response.context["history_feed"]
        self.assertEqual(model["mode"], "activity")
        day = next(day for day in model["days"] if day["key"] == day_key)
        self.assertEqual(day["entryCount"], total_entries)
        self.assertEqual(
            len(day["entries"]),
            history_cache.HISTORY_ENTRIES_PER_DAY_PAGE,
        )
        self.assertTrue(day["hasMore"])
        self.assertTrue(day["entries"][0]["canEdit"])
        self.assertFalse(day["entries"][0]["canDelete"])
        self.assertIn("format=json", day["nextUrl"])

    def test_history_feed_model_owns_month_nav_and_filters(self):
        """The shared feed receives the month nav and active filter state."""
        response = self.client.get(
            reverse("history"),
            {
                "year": 2020,
                "m": 6,
                "media_type": MediaTypes.MOVIE.value,
                "genre": "drama,comedy",
                "implied_genre": "mystery",
            },
        )

        self.assertEqual(response.status_code, 200)
        model = response.context["history_feed"]
        self.assertEqual(model["nav"]["monthLabel"], "June")
        self.assertIn("year=2020&m=5", model["nav"]["previousMonth"]["url"])
        self.assertIn("year=2020&m=7", model["nav"]["nextMonth"]["url"])
        self.assertIn("genre=drama%2Ccomedy", model["nav"]["previousMonth"]["url"])
        self.assertEqual(model["filters"]["mediaTypes"], ["movie"])
        self.assertEqual(model["filters"]["genres"], ["drama", "comedy"])
        self.assertEqual(model["filters"]["impliedGenres"], ["mystery"])
        self.assertEqual(model["filters"]["activeCount"], 3)
        self.assertEqual(
            [option["value"] for option in model["options"]["mediaTypes"]],
            [
                "tv", "movie", "anime", "game", "music", "book", "manga",
                "comic", "podcast", "video", "boardgame",
            ],
        )

    def test_history_feed_withholds_lazy_day_entries(self):
        """A lazy day must not ship its entries in the first response."""
        from app.history_views import _history_feed_day

        request = RequestFactory().get(reverse("history"))
        request.user = self.user
        day = {
            "day_key": "20261104",
            "weekday": "Wednesday",
            "date_display": "November 4, 2026",
            "entry_count": 40,
            "entry_offset": 0,
            "next_entry_offset": 30,
            "has_more": True,
            "remaining_entry_count": 10,
            "lazy": True,
            "entries": [{"entry_key": "hidden-entry", "title": "Hidden"}],
        }

        model = _history_feed_day(day, "activity", self.user, request)

        self.assertTrue(model["lazy"])
        self.assertTrue(model["hasMore"])
        self.assertEqual(model["entryCount"], 40)
        self.assertEqual(model["entries"], [])
        self.assertIn("format=json", model["loadUrl"])

    def test_history_feed_pagination_keeps_filters_and_elides_pages(self):
        """Filtered-view pagination is a feed intent, not template HTML."""
        from app.history_views import _history_feed_pagination

        request = RequestFactory().get(
            reverse("history"),
            {"media_type": MediaTypes.MOVIE.value, "page": "4"},
        )
        pagination = _history_feed_pagination(
            request,
            {"use_month_view": False, "total_pages": 12, "current_page": 4},
        )

        self.assertEqual(pagination["page"], 4)
        self.assertEqual(pagination["pageCount"], 12)
        self.assertIn("page=3", pagination["previousUrl"])
        self.assertIn("page=5", pagination["nextUrl"])
        self.assertIn("media_type=movie", pagination["previousUrl"])
        numbers = [page["number"] for page in pagination["pages"] if page]
        self.assertEqual(numbers, [1, 2, 3, 4, 5, 6, 12])
        self.assertIn(None, pagination["pages"])

    def test_activity_sessions_modal_json_matches_the_shared_dialog(self):
        """The activity dialog fetches bounded sessions as plain data."""
        response = self.client.get(
            reverse("activity_sessions_modal"),
            {
                "media_type": MediaTypes.MOVIE.value,
                "media_id": "month-movie",
                "source": Sources.MANUAL.value,
                "format": "json",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["sessions"]), 1)
        session = payload["sessions"][0]
        self.assertEqual(session["label"], "Watched")
        self.assertEqual(session["date"], timezone.localdate().isoformat())
        self.assertTrue(session["when"])
        self.assertIn("stats", payload)

    def test_history_day_fragment_json_returns_the_next_bounded_slice(self):
        """The browser feed loads one JSON day slice without whole-history payloads."""
        day_key, _total_entries = self._cache_large_history_day()
        offset = history_cache.HISTORY_ENTRIES_PER_DAY_PAGE

        response = self.client.get(
            reverse("history_day_fragment", kwargs={"day_key": day_key}),
            {"format": "json", "entry_offset": offset},
        )

        self.assertEqual(response.status_code, 200)
        day = response.json()["day"]
        self.assertEqual(day["key"], day_key)
        self.assertEqual(day["offset"], offset)
        self.assertFalse(day["hasMore"])
        self.assertIn("Large History 034", {entry["title"] for entry in day["entries"]})
        self.assertNotIn("Large History 000", {entry["title"] for entry in day["entries"]})
        self.assertIsNone(day["nextUrl"])

    def test_history_day_fragment_json_keeps_filters_and_bounds(self):
        """JSON day slices filter before pagination, like the server fragment."""
        day_key, _total_entries = self._cache_large_history_day(
            keep_count=5,
            total_count=history_cache.HISTORY_ENTRIES_PER_DAY_PAGE + 5,
        )

        response = self.client.get(
            reverse("history_day_fragment", kwargs={"day_key": day_key}),
            {
                "format": "json",
                "genre": "keep",
                "media_type": MediaTypes.MOVIE.value,
            },
        )

        self.assertEqual(response.status_code, 200)
        day = response.json()["day"]
        self.assertEqual(day["entryCount"], 5)
        self.assertEqual(len(day["entries"]), 5)
        self.assertFalse(day["hasMore"])
        self.assertEqual(
            {entry["title"] for entry in day["entries"]},
            {f"Large History {index:03d}" for index in range(30, 35)},
        )

    def test_history_day_fragment_rejects_invalid_day_and_offset(self):
        invalid_day = self.client.get(
            reverse("history_day_fragment", kwargs={"day_key": "20261301"}),
        )
        self.assertEqual(invalid_day.status_code, 400)

        day_key, total_entries = self._cache_large_history_day()
        out_of_range_offset = self.client.get(
            reverse("history_day_fragment", kwargs={"day_key": day_key}),
            {"entry_offset": total_entries + 1},
        )
        self.assertEqual(out_of_range_offset.status_code, 400)

        invalid_offset = self.client.get(
            reverse("history_day_fragment", kwargs={"day_key": "20260101"}),
            {"entry_offset": "not-an-integer"},
        )
        self.assertEqual(invalid_offset.status_code, 400)

        negative_offset = self.client.get(
            reverse("history_day_fragment", kwargs={"day_key": "20260101"}),
            {"entry_offset": -1},
        )
        self.assertEqual(negative_offset.status_code, 400)

    def test_history_day_fragment_does_not_expose_another_users_day(self):
        other_user = get_user_model().objects.create_user(
            username="history-other-user",
            password="12345",
        )
        played_at = timezone.now() - timedelta(days=120)
        item = Item.objects.create(
            media_id="foreign-history",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MOVIE.value,
            title="Foreign History Entry",
            image="http://example.com/foreign-history.jpg",
        )
        Movie.objects.create(
            item=item,
            user=other_user,
            status=Status.COMPLETED.value,
            progress=1,
            start_date=played_at,
            end_date=played_at,
        )
        day_key = history_cache.history_day_key(played_at)
        history_cache._build_and_cache_history_day(
            other_user,
            day_key,
            logging_style_override="repeats",
        )

        response = self.client.get(
            reverse("history_day_fragment", kwargs={"day_key": day_key}),
        )

        self.assertEqual(response.status_code, 404)
        self.assertNotIn("Foreign History Entry", response.content.decode())

    def _create_movie_history(self, item, older_played_at, newer_played_at):
        Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=1,
            start_date=older_played_at,
            end_date=older_played_at,
        )
        Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=1,
            start_date=newer_played_at,
            end_date=newer_played_at,
        )

    def _create_game_history(self, item, older_played_at, newer_played_at):
        Game.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=120,
            start_date=older_played_at - timedelta(hours=2),
            end_date=older_played_at,
        )
        Game.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=90,
            start_date=newer_played_at - timedelta(hours=1),
            end_date=newer_played_at,
        )

    def _create_book_history(self, item, older_played_at, newer_played_at):
        Book.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=320,
            start_date=older_played_at - timedelta(days=4),
            end_date=older_played_at,
        )
        Book.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=280,
            start_date=newer_played_at - timedelta(days=3),
            end_date=newer_played_at,
        )

    def test_item_filtered_history_shows_full_cross_month_history(self):
        older_played_at = timezone.now() - timedelta(days=40)
        newer_played_at = timezone.now()
        scenarios = [
            {
                "media_type": MediaTypes.MOVIE.value,
                "media_id": "cross-month-movie",
                "title": "Cross Month Movie",
                "create_entries": self._create_movie_history,
                "params": {},
            },
            {
                "media_type": MediaTypes.GAME.value,
                "media_id": "cross-month-game",
                "title": "Cross Month Game",
                "create_entries": self._create_game_history,
                "params": {"logging_style": "sessions"},
            },
            {
                "media_type": MediaTypes.BOOK.value,
                "media_id": "cross-month-book",
                "title": "Cross Month Book",
                "create_entries": self._create_book_history,
                "params": {},
            },
        ]

        for scenario in scenarios:
            with self.subTest(media_type=scenario["media_type"]):
                item = Item.objects.create(
                    media_id=scenario["media_id"],
                    source=Sources.MANUAL.value,
                    media_type=scenario["media_type"],
                    title=scenario["title"],
                    image=f"http://example.com/{scenario['media_id']}.jpg",
                )
                scenario["create_entries"](item, older_played_at, newer_played_at)

                response = self.client.get(
                    reverse("history"),
                    {
                        "media_type": scenario["media_type"],
                        "media_id": item.media_id,
                        "source": item.source,
                        **scenario["params"],
                    },
                )

                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.context["use_month_view"])
                self.assertEqual(
                    sum(
                        len(day.get("entries", []))
                        for day in response.context["history_days"]
                    ),
                    2,
                )
                self.assertEqual(
                    {
                        entry["title"]
                        for day in response.context["history_days"]
                        for entry in day.get("entries", [])
                    },
                    {scenario["title"]},
                )


class MusicScoreHistoryInvalidationTests(TestCase):
    """Test that music score edits invalidate only affected history days."""

    def setUp(self):
        self.credentials = {"username": "music-score", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        self.artist = Artist.objects.create(name="Score Artist")
        self.album = Album.objects.create(title="Score Album", artist=self.artist)
        first_track = Track.objects.create(
            album=self.album,
            title="Score Track One",
            track_number=1,
            duration_ms=180000,
        )
        second_track = Track.objects.create(
            album=self.album,
            title="Score Track Two",
            track_number=2,
            duration_ms=180000,
        )

        now = timezone.now()
        self.play_day_keys = sorted(
            {
                history_cache.history_day_key(now - timedelta(days=1)),
                history_cache.history_day_key(now),
            },
        )
        first_item = Item.objects.create(
            media_id="score-track-1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MUSIC.value,
            title="Score Track One",
            image="http://example.com/score-track-1.jpg",
            runtime_minutes=3,
        )
        second_item = Item.objects.create(
            media_id="score-track-2",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MUSIC.value,
            title="Score Track Two",
            image="http://example.com/score-track-2.jpg",
            runtime_minutes=3,
        )
        Music.objects.create(
            item=first_item,
            user=self.user,
            album=self.album,
            artist=self.artist,
            track=first_track,
            status=Status.COMPLETED.value,
            progress=1,
            end_date=now - timedelta(days=1),
        )
        Music.objects.create(
            item=second_item,
            user=self.user,
            album=self.album,
            artist=self.artist,
            track=second_track,
            status=Status.COMPLETED.value,
            progress=1,
            end_date=now,
        )

    @patch("app.views.history_cache.invalidate_history_cache")
    @patch("app.views.history_cache.invalidate_history_days")
    def test_update_album_score_invalidates_only_affected_history_days(
        self,
        mock_invalidate_history_days,
        mock_invalidate_history_cache,
    ):
        response = self.client.post(
            reverse("update_album_score", args=[self.album.id]),
            {"score": "8"},
        )

        self.assertEqual(response.status_code, 200)
        mock_invalidate_history_days.assert_called_once_with(
            self.user.id,
            day_keys=self.play_day_keys,
            logging_styles=("sessions", "repeats"),
            reason="album_score_change",
        )
        mock_invalidate_history_cache.assert_not_called()

    @patch("app.views.history_cache.invalidate_history_cache")
    @patch("app.views.history_cache.invalidate_history_days")
    def test_update_artist_score_invalidates_only_affected_history_days(
        self,
        mock_invalidate_history_days,
        mock_invalidate_history_cache,
    ):
        response = self.client.post(
            reverse("update_artist_score", args=[self.artist.id]),
            {"score": "9"},
        )

        self.assertEqual(response.status_code, 200)
        mock_invalidate_history_days.assert_called_once_with(
            self.user.id,
            day_keys=self.play_day_keys,
            logging_styles=("sessions", "repeats"),
            reason="artist_score_change",
        )
        mock_invalidate_history_cache.assert_not_called()


class HistoryViewPersonFilterTests(TestCase):
    """Test person-based filtering on the history page."""

    credentials = {"username": "test", "password": "12345"}

    @classmethod
    def setUpTestData(cls):
        """Build this class's fixture once instead of once per test."""
        patchers = _begin_model_metadata_patches()
        try:
            cls.user = get_user_model().objects.create_user(**cls.credentials)
            cls._build_fixture()
        finally:
            for patcher in reversed(patchers):
                patcher.stop()

    def setUp(self):
        # Per-test: the patches cover the test bodies' own saves, and the
        # test client is rebuilt for every test.
        _start_model_metadata_patches(self)
        self.client.login(**self.credentials)

    @classmethod
    def _build_fixture(cls):
        cls.person = Person.objects.create(
            source=Sources.TMDB.value,
            source_person_id="900",
            name="Filter Person",
            gender=PersonGender.MALE.value,
        )

        cls.movie_item = Item.objects.create(
            media_id="m1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MOVIE.value,
            title="Credited Movie",
            image="http://example.com/m1.jpg",
        )
        Movie.objects.create(
            item=cls.movie_item,
            user=cls.user,
            status=Status.COMPLETED.value,
            progress=1,
            start_date=timezone.now(),
            end_date=timezone.now(),
        )
        ItemPersonCredit.objects.create(
            item=cls.movie_item,
            person=cls.person,
            role_type=CreditRoleType.CAST.value,
            role="Lead",
        )

        other_movie_item = Item.objects.create(
            media_id="m2",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MOVIE.value,
            title="Other Movie",
            image="http://example.com/m2.jpg",
        )
        Movie.objects.create(
            item=other_movie_item,
            user=cls.user,
            status=Status.COMPLETED.value,
            progress=1,
            start_date=timezone.now(),
            end_date=timezone.now(),
        )

        tv_item = Item.objects.create(
            media_id="tv-1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.TV.value,
            title="Credited Show",
            image="http://example.com/tv1.jpg",
        )
        tv = TV.objects.create(
            item=tv_item,
            user=cls.user,
            status=Status.COMPLETED.value,
        )
        season_item = Item.objects.create(
            media_id="tv-1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.SEASON.value,
            title="Credited Show",
            image="http://example.com/tv1s1.jpg",
            season_number=1,
        )
        season = Season.objects.create(
            item=season_item,
            user=cls.user,
            related_tv=tv,
            status=Status.COMPLETED.value,
        )
        episode_item = Item.objects.create(
            media_id="tv-1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.EPISODE.value,
            title="Credited Episode",
            image="http://example.com/tv1e1.jpg",
            season_number=1,
            episode_number=1,
        )
        Episode.objects.create(
            item=episode_item,
            related_season=season,
            end_date=timezone.now(),
        )
        ItemPersonCredit.objects.create(
            item=tv_item,
            person=cls.person,
            role_type=CreditRoleType.CAST.value,
            role="Lead",
        )

        other_tv_item = Item.objects.create(
            media_id="tv-2",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.TV.value,
            title="Other Show",
            image="http://example.com/tv2.jpg",
        )
        other_tv = TV.objects.create(
            item=other_tv_item,
            user=cls.user,
            status=Status.COMPLETED.value,
        )
        other_season_item = Item.objects.create(
            media_id="tv-2",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.SEASON.value,
            title="Other Show",
            image="http://example.com/tv2s1.jpg",
            season_number=1,
        )
        other_season = Season.objects.create(
            item=other_season_item,
            user=cls.user,
            related_tv=other_tv,
            status=Status.COMPLETED.value,
        )
        other_episode_item = Item.objects.create(
            media_id="tv-2",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.EPISODE.value,
            title="Other Episode",
            image="http://example.com/tv2e1.jpg",
            season_number=1,
            episode_number=1,
        )
        Episode.objects.create(
            item=other_episode_item,
            related_season=other_season,
            end_date=timezone.now(),
        )
    @staticmethod
    def _credit(person, role="Lead", sort_order=0):
        return {
            "person": person,
            "role": role,
            "sort_order": sort_order,
        }

    def _mark_tmdb_credits_current(self, *items):
        for item in items:
            MetadataBackfillState.objects.update_or_create(
                item=item,
                field=MetadataBackfillField.CREDITS,
                defaults={
                    "last_success_at": timezone.now(),
                    "strategy_version": CREDITS_BACKFILL_VERSION,
                },
            )

    def _create_tmdb_tv_show(
        self, media_id, title, studio, show_credits, seasons, base_time
    ):
        tv_item = Item.objects.create(
            media_id=media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title=title,
            image=f"http://example.com/{media_id}.jpg",
        )
        tv = TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.COMPLETED.value,
        )
        ItemStudioCredit.objects.create(item=tv_item, studio=studio)
        for credit in show_credits:
            ItemPersonCredit.objects.create(
                item=tv_item,
                person=credit["person"],
                role_type=CreditRoleType.CAST.value,
                role=credit.get("role", "Lead"),
                sort_order=credit.get("sort_order"),
            )
        self._mark_tmdb_credits_current(tv_item)

        for season_number, season_spec in seasons.items():
            season_item = Item.objects.create(
                media_id=media_id,
                source=Sources.TMDB.value,
                media_type=MediaTypes.SEASON.value,
                title=title,
                image=f"http://example.com/{media_id}-s{season_number}.jpg",
                season_number=season_number,
            )
            season = Season.objects.create(
                item=season_item,
                user=self.user,
                related_tv=tv,
                status=Status.COMPLETED.value,
            )

            for credit in season_spec.get("season_credits", []):
                ItemPersonCredit.objects.create(
                    item=season_item,
                    person=credit["person"],
                    role_type=CreditRoleType.CAST.value,
                    role=credit.get("role", "Lead"),
                    sort_order=credit.get("sort_order"),
                )
            for credit in season_spec.get("crew", []):
                ItemPersonCredit.objects.create(
                    item=season_item,
                    person=credit["person"],
                    role_type=CreditRoleType.CREW.value,
                    role=credit.get("role", "Director"),
                    department=credit.get("department", "Directing"),
                    sort_order=credit.get("sort_order"),
                )
            self._mark_tmdb_credits_current(season_item)

            for episode_spec in season_spec.get("episodes", []):
                episode_number = episode_spec["episode_number"]
                episode_item = Item.objects.create(
                    media_id=media_id,
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.EPISODE.value,
                    title=episode_spec.get("title")
                    or f"{title} Episode {season_number}-{episode_number}",
                    image=episode_spec.get("image")
                    or f"http://example.com/{media_id}-s{season_number}e{episode_number}.jpg",
                    season_number=season_number,
                    episode_number=episode_number,
                    runtime_minutes=episode_spec.get("runtime_minutes", 45),
                )
                Episode.objects.create(
                    item=episode_item,
                    related_season=season,
                    end_date=base_time
                    + timedelta(minutes=episode_spec.get("offset_minutes", 0)),
                )
                for credit in episode_spec.get("credits", []):
                    ItemPersonCredit.objects.create(
                        item=episode_item,
                        person=credit["person"],
                        role_type=CreditRoleType.CAST.value,
                        role=credit.get("role", "Guest"),
                        sort_order=credit.get("sort_order"),
                    )
                for credit in episode_spec.get("crew", []):
                    ItemPersonCredit.objects.create(
                        item=episode_item,
                        person=credit["person"],
                        role_type=CreditRoleType.CREW.value,
                        role=credit.get("role", "Director"),
                        department=credit.get("department", "Directing"),
                        sort_order=credit.get("sort_order"),
                    )
                self._mark_tmdb_credits_current(episode_item)

    def test_history_filters_by_person_source_and_id(self):
        response = self.client.get(
            reverse("history") + "?person_source=tmdb&person_id=900",
        )

        self.assertEqual(response.status_code, 200)
        titles = [
            entry["title"]
            for day in response.context["history_days"]
            for entry in day.get("entries", [])
        ]
        self.assertIn("Credited Movie", titles)
        self.assertIn("Credited Episode", titles)
        self.assertNotIn("Other Movie", titles)
        self.assertNotIn("Other Episode", titles)

    def test_history_person_filter_matches_episode_or_show_person_credits(self):
        tv_item = Item.objects.create(
            media_id="tv-credits-fallback",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Credit Fallback Show",
            image="http://example.com/tvfallback.jpg",
        )
        tv = TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.COMPLETED.value,
        )
        season_item = Item.objects.create(
            media_id="tv-credits-fallback",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Credit Fallback Show",
            image="http://example.com/tvfallbacks1.jpg",
            season_number=1,
        )
        season = Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=tv,
            status=Status.COMPLETED.value,
        )

        target_person = self.person
        other_person = Person.objects.create(
            source=Sources.TMDB.value,
            source_person_id="901",
            name="Other Person",
            gender=PersonGender.MALE.value,
        )

        ItemPersonCredit.objects.create(
            item=tv_item,
            person=target_person,
            role_type=CreditRoleType.CAST.value,
            role="Show-level credit",
            sort_order=0,
        )

        episode_item_match = Item.objects.create(
            media_id="tv-credits-fallback",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Episode Specific Match",
            image="http://example.com/tvfallback-e1.jpg",
            season_number=1,
            episode_number=1,
        )
        episode_item_exclude = Item.objects.create(
            media_id="tv-credits-fallback",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Episode Specific Exclusion",
            image="http://example.com/tvfallback-e2.jpg",
            season_number=1,
            episode_number=2,
        )
        episode_item_fallback = Item.objects.create(
            media_id="tv-credits-fallback",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Fallback To Show Credit",
            image="http://example.com/tvfallback-e3.jpg",
            season_number=1,
            episode_number=3,
        )

        now = timezone.now()
        Episode.objects.create(
            item=episode_item_match,
            related_season=season,
            end_date=now,
        )
        Episode.objects.create(
            item=episode_item_exclude,
            related_season=season,
            end_date=now + timedelta(minutes=1),
        )
        Episode.objects.create(
            item=episode_item_fallback,
            related_season=season,
            end_date=now + timedelta(minutes=2),
        )

        ItemPersonCredit.objects.create(
            item=episode_item_match,
            person=target_person,
            role_type=CreditRoleType.CAST.value,
            role="Episode-level match",
        )
        ItemPersonCredit.objects.create(
            item=episode_item_exclude,
            person=other_person,
            role_type=CreditRoleType.CAST.value,
            role="Episode-level non-match",
        )
        self._mark_tmdb_credits_current(
            tv_item,
            season_item,
            episode_item_match,
            episode_item_exclude,
            episode_item_fallback,
        )

        response = self.client.get(
            reverse("history") + "?person_source=tmdb&person_id=900",
        )

        self.assertEqual(response.status_code, 200)
        titles = [
            entry["title"]
            for day in response.context["history_days"]
            for entry in day.get("entries", [])
        ]
        self.assertIn("Episode Specific Match", titles)
        self.assertIn("Fallback To Show Credit", titles)
        self.assertIn("Episode Specific Exclusion", titles)

    def test_history_tmdb_person_filter_excludes_high_order_show_guest_from_other_episodes(
        self,
    ):
        target_person = Person.objects.create(
            source=Sources.TMDB.value,
            source_person_id="990",
            name="TMDB Guest",
            gender=PersonGender.MALE.value,
        )
        other_person = Person.objects.create(
            source=Sources.TMDB.value,
            source_person_id="991",
            name="Other TMDB Guest",
            gender=PersonGender.MALE.value,
        )

        tv_item = Item.objects.create(
            media_id="tv-guest-only",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="TMDB Guest Show",
            image="http://example.com/tmdbgshow.jpg",
        )
        tv = TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.COMPLETED.value,
        )
        season_item = Item.objects.create(
            media_id="tv-guest-only",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="TMDB Guest Show",
            image="http://example.com/tmdbgshows1.jpg",
            season_number=1,
        )
        season = Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=tv,
            status=Status.COMPLETED.value,
        )

        episode_item_match = Item.objects.create(
            media_id="tv-guest-only",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="TMDB Guest Match",
            image="http://example.com/tmdbgmatch.jpg",
            season_number=1,
            episode_number=1,
        )
        episode_item_exclude = Item.objects.create(
            media_id="tv-guest-only",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="TMDB Guest Exclusion",
            image="http://example.com/tmdbgexclude.jpg",
            season_number=1,
            episode_number=2,
        )
        episode_item_fallback = Item.objects.create(
            media_id="tv-guest-only",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="TMDB Guest Fallback",
            image="http://example.com/tmdbgfallback.jpg",
            season_number=1,
            episode_number=3,
        )

        now = timezone.now()
        Episode.objects.create(
            item=episode_item_match,
            related_season=season,
            end_date=now,
        )
        Episode.objects.create(
            item=episode_item_exclude,
            related_season=season,
            end_date=now + timedelta(minutes=1),
        )
        Episode.objects.create(
            item=episode_item_fallback,
            related_season=season,
            end_date=now + timedelta(minutes=2),
        )

        ItemPersonCredit.objects.create(
            item=tv_item,
            person=target_person,
            role_type=CreditRoleType.CAST.value,
            role="Guest Star",
            sort_order=500,
        )
        ItemPersonCredit.objects.create(
            item=episode_item_match,
            person=target_person,
            role_type=CreditRoleType.CAST.value,
            role="Guest Star",
        )
        ItemPersonCredit.objects.create(
            item=episode_item_exclude,
            person=other_person,
            role_type=CreditRoleType.CAST.value,
            role="Guest Star",
        )
        self._mark_tmdb_credits_current(
            tv_item,
            season_item,
            episode_item_match,
            episode_item_exclude,
            episode_item_fallback,
        )

        response = self.client.get(
            reverse("history") + "?person_source=tmdb&person_id=990",
        )

        self.assertEqual(response.status_code, 200)
        titles = [
            entry["title"]
            for day in response.context["history_days"]
            for entry in day.get("entries", [])
        ]
        self.assertIn("TMDB Guest Match", titles)
        self.assertNotIn("TMDB Guest Exclusion", titles)
        self.assertNotIn("TMDB Guest Fallback", titles)

    @patch("app.providers.services.get_media_metadata", return_value={})
    @patch("app.tasks.enqueue_credits_backfill_items")
    def test_history_person_filter_uses_season_episode_and_show_fallback_ladder(
        self,
        _mock_enqueue,
        _mock_get_media_metadata,
    ):
        base_time = timezone.now().replace(second=0, microsecond=0)
        studio = Studio.objects.create(
            source=Sources.TMDB.value,
            source_studio_id="9800",
            name="History Ladder Studio",
        )

        stable_woman = Person.objects.create(
            source=Sources.TMDB.value,
            source_person_id="9801",
            name="History Stable Woman",
            gender=PersonGender.FEMALE.value,
        )
        stable_helper = Person.objects.create(
            source=Sources.TMDB.value,
            source_person_id="9802",
            name="History Stable Helper",
            gender=PersonGender.MALE.value,
        )
        left_man = Person.objects.create(
            source=Sources.TMDB.value,
            source_person_id="9803",
            name="History Left Man",
            gender=PersonGender.MALE.value,
        )
        left_other = Person.objects.create(
            source=Sources.TMDB.value,
            source_person_id="9804",
            name="History Left Other",
            gender=PersonGender.MALE.value,
        )
        left_helper = Person.objects.create(
            source=Sources.TMDB.value,
            source_person_id="9805",
            name="History Left Helper",
            gender=PersonGender.MALE.value,
        )
        join_man = Person.objects.create(
            source=Sources.TMDB.value,
            source_person_id="9806",
            name="History Join Man",
            gender=PersonGender.MALE.value,
        )
        join_other = Person.objects.create(
            source=Sources.TMDB.value,
            source_person_id="9807",
            name="History Join Other",
            gender=PersonGender.MALE.value,
        )
        join_helper = Person.objects.create(
            source=Sources.TMDB.value,
            source_person_id="9808",
            name="History Join Helper",
            gender=PersonGender.MALE.value,
        )
        guest_woman = Person.objects.create(
            source=Sources.TMDB.value,
            source_person_id="9809",
            name="History Guest Woman",
            gender=PersonGender.FEMALE.value,
        )
        guest_show_helper = Person.objects.create(
            source=Sources.TMDB.value,
            source_person_id="9810",
            name="History Guest Show Helper",
            gender=PersonGender.MALE.value,
        )
        guest_episode_helper = Person.objects.create(
            source=Sources.TMDB.value,
            source_person_id="9811",
            name="History Guest Episode Helper",
            gender=PersonGender.MALE.value,
        )
        season_regular_man = Person.objects.create(
            source=Sources.TMDB.value,
            source_person_id="9812",
            name="History Season Regular Man",
            gender=PersonGender.MALE.value,
        )
        fallback_man = Person.objects.create(
            source=Sources.TMDB.value,
            source_person_id="9813",
            name="History Fallback Man",
            gender=PersonGender.MALE.value,
        )
        mixed_episode_helper = Person.objects.create(
            source=Sources.TMDB.value,
            source_person_id="9814",
            name="History Mixed Episode Helper",
            gender=PersonGender.MALE.value,
        )
        mixed_season2_helper = Person.objects.create(
            source=Sources.TMDB.value,
            source_person_id="9815",
            name="History Mixed Season 2 Helper",
            gender=PersonGender.MALE.value,
        )
        mixed_season2_director = Person.objects.create(
            source=Sources.TMDB.value,
            source_person_id="9816",
            name="History Mixed Season 2 Director",
            gender=PersonGender.UNKNOWN.value,
        )

        self._create_tmdb_tv_show(
            "9801",
            "History Stable Ensemble",
            studio,
            show_credits=[self._credit(stable_woman, "Lead")],
            seasons={
                1: {
                    "season_credits": [self._credit(stable_woman, "Lead")],
                    "episodes": [
                        {
                            "episode_number": 1,
                            "offset_minutes": 0,
                            "credits": [self._credit(stable_helper, "Support")],
                        },
                    ],
                },
                2: {
                    "season_credits": [self._credit(stable_woman, "Lead")],
                    "episodes": [
                        {
                            "episode_number": 1,
                            "offset_minutes": 1,
                            "credits": [self._credit(stable_helper, "Support")],
                        },
                    ],
                },
            },
            base_time=base_time,
        )
        self._create_tmdb_tv_show(
            "9802",
            "History Leaving Cast",
            studio,
            show_credits=[self._credit(left_man, "Lead")],
            seasons={
                1: {
                    "season_credits": [self._credit(left_man, "Lead")],
                    "episodes": [
                        {
                            "episode_number": 1,
                            "offset_minutes": 2,
                            "credits": [self._credit(left_helper, "Support")],
                        },
                    ],
                },
                2: {
                    "season_credits": [self._credit(left_other, "Lead")],
                    "episodes": [
                        {
                            "episode_number": 1,
                            "offset_minutes": 3,
                            "credits": [self._credit(left_helper, "Support")],
                        },
                    ],
                },
            },
            base_time=base_time,
        )
        self._create_tmdb_tv_show(
            "9803",
            "History Joining Cast",
            studio,
            show_credits=[self._credit(join_man, "Lead")],
            seasons={
                1: {
                    "season_credits": [self._credit(join_other, "Lead")],
                    "episodes": [
                        {
                            "episode_number": 1,
                            "offset_minutes": 4,
                            "credits": [self._credit(join_helper, "Support")],
                        },
                    ],
                },
                2: {
                    "season_credits": [self._credit(join_man, "Lead")],
                    "episodes": [
                        {
                            "episode_number": 1,
                            "offset_minutes": 5,
                            "credits": [self._credit(join_helper, "Support")],
                        },
                    ],
                },
            },
            base_time=base_time,
        )
        self._create_tmdb_tv_show(
            "9804",
            "History Guest Star Show",
            studio,
            show_credits=[self._credit(guest_show_helper, "Lead")],
            seasons={
                1: {
                    "season_credits": [],
                    "episodes": [
                        {
                            "episode_number": 1,
                            "offset_minutes": 6,
                            "credits": [self._credit(guest_woman, "Guest")],
                        },
                        {
                            "episode_number": 2,
                            "offset_minutes": 7,
                            "credits": [self._credit(guest_episode_helper, "Guest")],
                        },
                    ],
                },
            },
            base_time=base_time,
        )
        self._create_tmdb_tv_show(
            "9805",
            "History Mixed Fallback Show",
            studio,
            show_credits=[self._credit(fallback_man, "Lead")],
            seasons={
                1: {
                    "season_credits": [self._credit(season_regular_man, "Lead")],
                    "episodes": [
                        {
                            "episode_number": 1,
                            "offset_minutes": 8,
                            "credits": [self._credit(mixed_episode_helper, "Guest")],
                        },
                        {
                            "episode_number": 2,
                            "offset_minutes": 9,
                            "credits": [self._credit(mixed_episode_helper, "Guest")],
                        },
                    ],
                },
                2: {
                    "season_credits": [],
                    "crew": [self._credit(mixed_season2_director, "Director")],
                    "episodes": [
                        {
                            "episode_number": 1,
                            "offset_minutes": 10,
                            "credits": [self._credit(mixed_season2_helper, "Guest")],
                        },
                    ],
                },
            },
            base_time=base_time,
        )

        expectations = {
            "9801": {
                "History Stable Woman": {
                    "History Stable Ensemble Episode 1-1",
                    "History Stable Ensemble Episode 2-1",
                }
            },
            "9803": {"History Left Man": {"History Leaving Cast Episode 1-1"}},
            "9806": {"History Join Man": {"History Joining Cast Episode 2-1"}},
            "9809": {"History Guest Woman": {"History Guest Star Show Episode 1-1"}},
            "9813": {
                "History Fallback Man": {"History Mixed Fallback Show Episode 2-1"}
            },
        }

        for person_id, person_expectations in expectations.items():
            response = self.client.get(
                reverse("history") + f"?person_source=tmdb&person_id={person_id}",
            )
            self.assertEqual(response.status_code, 200)
            titles = {
                entry["title"]
                for day in response.context["history_days"]
                for entry in day.get("entries", [])
            }
            expected_titles = set().union(*person_expectations.values())
            self.assertSetEqual(titles, expected_titles)


class HistoryViewAuthorFilterTests(TestCase):
    """Test author-based reading filters on the history page."""

    credentials = {"username": "author-filter-user", "password": "12345"}

    @classmethod
    def setUpTestData(cls):
        """Build this class's fixture once instead of once per test."""
        patchers = _begin_model_metadata_patches()
        try:
            cls.user = get_user_model().objects.create_user(**cls.credentials)
            cls._build_fixture()
        finally:
            for patcher in reversed(patchers):
                patcher.stop()

    def setUp(self):
        # Per-test: the patches cover the test bodies' own saves, and the
        # test client is rebuilt for every test.
        _start_model_metadata_patches(self)
        self.client.login(**self.credentials)

    @classmethod
    def _build_fixture(cls):
        cls.person = Person.objects.create(
            source=Sources.OPENLIBRARY.value,
            source_person_id="OL1A",
            name="Open Author",
            gender=PersonGender.UNKNOWN.value,
        )

        now = timezone.now()

        cls.book_item = Item.objects.create(
            media_id="OL123M",
            source=Sources.OPENLIBRARY.value,
            media_type=MediaTypes.BOOK.value,
            title="Credited Book",
            image="http://example.com/book.jpg",
        )
        Book.objects.create(
            user=cls.user,
            item=cls.book_item,
            status=Status.COMPLETED.value,
            progress=350,
            start_date=now,
            end_date=now,
        )
        ItemPersonCredit.objects.create(
            item=cls.book_item,
            person=cls.person,
            role_type=CreditRoleType.AUTHOR.value,
            role="Author",
        )

        cls.comic_item = Item.objects.create(
            media_id="comic-1",
            source=Sources.OPENLIBRARY.value,
            media_type=MediaTypes.COMIC.value,
            title="Credited Comic",
            image="http://example.com/comic.jpg",
        )
        Comic.objects.create(
            user=cls.user,
            item=cls.comic_item,
            status=Status.COMPLETED.value,
            progress=10,
            start_date=now,
            end_date=now,
        )
        ItemPersonCredit.objects.create(
            item=cls.comic_item,
            person=cls.person,
            role_type=CreditRoleType.AUTHOR.value,
            role="Writer",
        )

        cls.manga_item = Item.objects.create(
            media_id="manga-1",
            source=Sources.OPENLIBRARY.value,
            media_type=MediaTypes.MANGA.value,
            title="Credited Manga",
            image="http://example.com/manga.jpg",
        )
        Manga.objects.create(
            user=cls.user,
            item=cls.manga_item,
            status=Status.COMPLETED.value,
            progress=50,
            start_date=now,
            end_date=now,
        )
        ItemPersonCredit.objects.create(
            item=cls.manga_item,
            person=cls.person,
            role_type=CreditRoleType.AUTHOR.value,
            role="Author",
        )

        cls.uncredited_book_item = Item.objects.create(
            media_id="OL999M",
            source=Sources.OPENLIBRARY.value,
            media_type=MediaTypes.BOOK.value,
            title="Uncredited Book",
            image="http://example.com/other-book.jpg",
        )
        Book.objects.create(
            user=cls.user,
            item=cls.uncredited_book_item,
            status=Status.COMPLETED.value,
            progress=200,
            start_date=now,
            end_date=now,
        )
    def test_history_person_filter_includes_credited_reading_entries(self):
        response = self.client.get(
            reverse("history") + "?person_source=openlibrary&person_id=OL1A",
        )

        self.assertEqual(response.status_code, 200)
        titles = [
            entry["title"]
            for day in response.context["history_days"]
            for entry in day.get("entries", [])
        ]
        self.assertIn("Credited Book", titles)
        self.assertIn("Credited Comic", titles)
        self.assertIn("Credited Manga", titles)
        self.assertNotIn("Uncredited Book", titles)

    def test_history_without_person_filter_is_not_narrowed_to_credited_entries(self):
        """Unfiltered history shows every read, credited to this person or not.

        This asserted the opposite until reading types were put on the History
        timeline generally rather than only under a matching filter (see
        'feat(history): show books, comics, manga, and flat anime on the
        History timeline'). The old assertion outlived that change because the
        cached month view renders only indexed days and reading days were
        never indexed, so the entries stayed invisible on the default view.
        What the person filter does is narrow to credited items, which is what
        the sibling test above covers.
        """
        response = self.client.get(reverse("history"))

        self.assertEqual(response.status_code, 200)
        titles = [
            entry["title"]
            for day in response.context["history_days"]
            for entry in day.get("entries", [])
        ]
        self.assertIn("Credited Book", titles)
        self.assertIn("Uncredited Book", titles)


class HistoryImpliedGenreFilterTests(TestCase):
    def setUp(self):
        _start_model_metadata_patches(self)
        self.credentials = {"username": "history-music-user", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        now = timezone.now()
        self.artist = Artist.objects.create(name="History Artist")
        self.album = Album.objects.create(
            title="History Album",
            artist=self.artist,
            implied_genres=["Rock"],
        )
        self.track = Track.objects.create(
            album=self.album,
            title="History Track",
            track_number=1,
            duration_ms=180000,
        )
        self.music_item = Item.objects.create(
            media_id="recording-1",
            source=Sources.MUSICBRAINZ.value,
            media_type=MediaTypes.MUSIC.value,
            title="History Track",
            image="http://example.com/track.jpg",
            genres=["Krautrock"],
            implied_genres=["Rock"],
        )
        Music.objects.create(
            item=self.music_item,
            user=self.user,
            artist=self.artist,
            album=self.album,
            track=self.track,
            status=Status.COMPLETED.value,
            start_date=now - timedelta(minutes=3),
            end_date=now,
        )

        movie_item = Item.objects.create(
            media_id="movie-1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Other Movie",
            image="http://example.com/movie.jpg",
            genres=["Drama"],
        )
        Movie.objects.create(
            item=movie_item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=1,
            start_date=now - timedelta(days=1),
            end_date=now - timedelta(days=1),
        )
        history_cache.invalidate_history_cache(self.user.id)

    def test_history_genres_api_returns_direct_and_implied_music_genres(self):
        response = self.client.get(reverse("history_genres"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["genres"], ["Drama", "Krautrock"])
        self.assertEqual(response.json()["implied_genres"], ["Rock"])

    def test_history_implied_genre_filter_limits_results(self):
        response = self.client.get(reverse("history"), {"implied_genre": "Rock"})

        self.assertEqual(response.status_code, 200)
        titles = [
            entry["title"]
            for day in response.context["history_days"]
            for entry in day.get("entries", [])
        ]
        self.assertEqual(titles, ["History Album"])
