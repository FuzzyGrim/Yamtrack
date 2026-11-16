import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID

from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.client import RequestFactory
from django.urls import reverse
from django.utils import timezone

from app import config
from app.models import (
    Album,
    Artist,
    BasicMedia,
    Book,
    Item,
    MediaTypes,
    Sources,
    Status,
    Studio,
    Track,
)
from app.templatetags import app_tags
from app.tests.card_props import card_model, card_text, render_card_template
from users.models import DateFormatChoices, TimeFormatChoices


class WatchedCountTests(SimpleTestCase):
    def test_counts_only_finished_plays(self):
        """An open check-in play and a dropped row are history, not watches."""
        history = [
            SimpleNamespace(status=Status.COMPLETED.value, dropped=False),
            SimpleNamespace(status=Status.IN_PROGRESS.value, dropped=False),
            SimpleNamespace(status=Status.DROPPED.value, dropped=True),
        ]
        self.assertEqual(app_tags.watched_count(history), 1)
        self.assertEqual(app_tags.watched_count(history[1:]), 0)
        self.assertEqual(app_tags.watched_count([]), 0)


class FutureDateTests(SimpleTestCase):
    def test_only_known_dates_after_today_are_future(self):
        today = timezone.localdate()
        for value in (None, "", "invalid", "2026-02-30", today, today - timedelta(days=1)):
            with self.subTest(value=value):
                self.assertFalse(app_tags.is_future_date(value))
        tomorrow = today + timedelta(days=1)
        self.assertTrue(app_tags.is_future_date(tomorrow))
        self.assertTrue(app_tags.is_future_date(tomorrow.isoformat()))
        self.assertTrue(app_tags.is_future_date(datetime.combine(tomorrow, datetime.min.time(), tzinfo=UTC)))


class AppTagsTests(TestCase):
    """Test the app template tags."""

    def setUp(self):
        """Set up test data."""
        self.user = get_user_model().objects.create_user(
            username="templater",
            password="12345",
        )
        self.request_factory = RequestFactory()

        # Create a sample item for testing
        self.tv_item = Item(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test TV Show",
        )

        self.season_item = Item(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test TV Show",
            season_number=1,
        )

        self.episode_item = Item(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Test TV Show",
            season_number=1,
            episode_number=1,
        )

        # Create a dict version for testing dict-based functions
        self.tv_dict = {
            "media_id": "1668",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "title": "Test TV Show",
        }

        self.season_dict = {
            "media_id": "1668",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.SEASON.value,
            "title": "Test TV Show",
            "season_number": 1,
        }

        self.episode_dict = {
            "media_id": "1668",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.EPISODE.value,
            "title": "Test TV Show",
            "season_number": 1,
            "episode_number": 1,
        }

    @patch("pathlib.Path.stat")
    def test_get_static_file_mtime(self, mock_stat):
        """Test the get_static_file_mtime tag."""
        # Mock the stat method to return a fixed mtime
        mock_stat_result = MagicMock()
        mock_stat_result.st_mtime = 1234567890
        mock_stat.return_value = mock_stat_result

        # Test with a valid file
        result = app_tags.get_static_file_mtime("css/style.css")
        self.assertEqual(result, "?1234567890")

        # Test with file not found
        mock_stat.side_effect = OSError()
        result = app_tags.get_static_file_mtime("nonexistent.css")
        self.assertEqual(result, "")

    def test_related_section_label(self):
        """DLCs keep their acronym casing; other sections are title-cased."""
        self.assertEqual(app_tags.related_section_label("dlcs"), "DLCs")
        self.assertEqual(
            app_tags.related_section_label("standalone_expansions"),
            "Standalone Expansions",
        )

    def test_no_underscore(self):
        """Test the no_underscore filter."""
        self.assertEqual(app_tags.no_underscore("hello_world"), "hello world")
        self.assertEqual(
            app_tags.no_underscore("test_string_with_underscores"),
            "test string with underscores",
        )
        self.assertEqual(
            app_tags.no_underscore("no_underscores_here"),
            "no underscores here",
        )

    def test_watch_operation_id_returns_fresh_uuid(self):
        """Each rendered first-party watch control receives a fresh UUID."""
        first = app_tags.watch_operation_id()
        second = app_tags.watch_operation_id()

        self.assertEqual(str(UUID(first)), first)
        self.assertNotEqual(first, second)

    def test_slug(self):
        """Test the slug filter."""
        # Test normal slugification
        self.assertEqual(app_tags.slug("Hello World"), "hello-world")

        # Test with special characters
        self.assertEqual(app_tags.slug("Anime: 31687"), "anime-31687")
        self.assertEqual(app_tags.slug("★★★"), "%E2%98%85%E2%98%85%E2%98%85")
        self.assertEqual(app_tags.slug("[Oshi no Ko]"), "oshi-no-ko")
        self.assertEqual(app_tags.slug("_____"), "_____")

    def test_slug_is_one_path_segment(self):
        """Titles made of path characters still produce a usable URL segment."""
        self.assertEqual(app_tags.slug("/"), "2f")
        self.assertEqual(app_tags.slug("//"), "2f2f")
        self.assertEqual(app_tags.slug("."), "2e")
        self.assertEqual(app_tags.slug(".."), "2e2e")
        self.assertEqual(app_tags.slug("..."), "...")

    def test_media_url_episode_titled_slash(self):
        """An episode titled "/" links to its page instead of raising (#1322)."""
        episode = {
            "media_type": MediaTypes.EPISODE.value,
            "source": Sources.TMDB.value,
            "media_id": "1411",
            "title": "/",
            "season_number": 3,
            "episode_number": 17,
        }

        url = app_tags.media_url(episode)

        self.assertEqual(url, "/details/tmdb/tv/1411/2f/season/3/episode/17")

    def test_title_preserve_acronyms(self):
        """Test acronym-preserving title casing."""
        self.assertEqual(app_tags.title_preserve_acronyms("rom"), "Rom")
        self.assertEqual(app_tags.title_preserve_acronyms("ROM"), "ROM")
        self.assertEqual(
            app_tags.title_preserve_acronyms("digital deluxe"),
            "Digital Deluxe",
        )

    def test_media_type_readable(self):
        """Test the media_type_readable filter."""
        # Test all media types from the MediaTypes class
        for media_type, label in MediaTypes.choices:
            self.assertEqual(app_tags.media_type_readable(media_type), label)

    def test_media_type_readable_plural(self):
        """Test the media_type_readable_plural filter."""
        # Test all media types from the MediaTypes class
        for media_type, label in MediaTypes.choices:
            singular = label

            # Special cases that don't change in plural form
            if singular.lower() in [
                MediaTypes.ANIME.value,
                MediaTypes.MANGA.value,
                MediaTypes.MUSIC.value,
            ]:
                expected = singular
            else:
                expected = f"{singular}s"

            self.assertEqual(app_tags.media_type_readable_plural(media_type), expected)

    def test_media_status_readable_preserves_unknown_provider_status(self):
        """Provider metadata statuses must not break tracking-status rendering."""
        self.assertEqual(app_tags.media_status_readable("Released"), "Released")

    def test_default_source(self):
        """Test the default_source filter."""
        # Test all media types from the MediaTypes class
        for media_type in MediaTypes.values:
            result = app_tags.default_source(media_type)

            # Check that it returns a non-empty string
            self.assertTrue(isinstance(result, str))
            self.assertTrue(len(result) > 0)

            # This implicitly checks that all media types are handled
        try:
            app_tags.default_source(media_type)
        except KeyError:
            self.fail(f"default_source raised KeyError for {media_type}")

    def test_media_past_verb(self):
        """Test the media_past_verb filter."""
        # Test all media types
        for media_type in MediaTypes.values:
            result = app_tags.media_past_verb(media_type)

            # Check that it returns a non-empty string
            self.assertTrue(isinstance(result, str))

    def test_browse_url(self):
        """Test the browse_url filter."""
        expected_media_type = {
            MediaTypes.SEASON.value: MediaTypes.TV.value,
            MediaTypes.EPISODE.value: MediaTypes.TV.value,
            MediaTypes.COMIC_ISSUE.value: MediaTypes.COMIC.value,
        }

        # Test all media types
        for media_type in MediaTypes.values:
            result = app_tags.browse_url(media_type)

            self.assertIn("/discover", result)
            self.assertNotIn("q=", result)

            mapped_media_type = expected_media_type.get(media_type, media_type)
            if mapped_media_type in config.DISCOVER_ALLOWED_MEDIA_TYPES:
                self.assertIn(f"media_type={mapped_media_type}", result)
            else:
                self.assertIn("media_type=all", result)

    def test_media_color(self):
        """Test the media_color filter."""
        # Test all media types
        for media_type in MediaTypes.values:
            result = app_tags.media_color(media_type)

            # Check that it returns a non-empty string
            self.assertTrue(isinstance(result, str))

    @override_settings(TIME_ZONE="America/New_York")
    def test_release_year_handles_year_one_sentinel_datetime(self):
        """release_year must not raise OverflowError for a year-0001 release_datetime.

        A negative UTC offset (e.g. America/New_York) underflows below
        datetime.MINYEAR when converting the sentinel to local time.
        """
        item = Item(
            media_id="118340",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            release_datetime=datetime(1, 1, 1, tzinfo=UTC),
        )
        self.assertIsNone(app_tags.release_year(item))

    def test_natural_day(self):
        """Test the natural_day filter."""
        # Create mock user with date_format preference
        mock_user = MagicMock()
        mock_user.date_format = DateFormatChoices.ISO_8601
        mock_user.time_format = TimeFormatChoices.HH_MM

        # Mock current date to March 29, 2025
        with patch("django.utils.timezone.now") as mock_now:
            # Use timezone.datetime to create timezone-aware datetimes
            mock_now.return_value = timezone.datetime(
                2025,
                3,
                29,
                12,
                0,
                0,
                tzinfo=timezone.get_current_timezone(),
            )

            # Test today
            today = timezone.datetime(
                2025,
                3,
                29,
                15,
                0,
                0,
                tzinfo=timezone.get_current_timezone(),
            )
            self.assertEqual(app_tags.natural_day(today, mock_user), "Today")

            # Test tomorrow
            tomorrow = timezone.datetime(
                2025,
                3,
                30,
                15,
                0,
                0,
                tzinfo=timezone.get_current_timezone(),
            )
            self.assertEqual(app_tags.natural_day(tomorrow, mock_user), "Tomorrow")

            # Test further away
            further = timezone.datetime(
                2025,
                4,
                10,
                15,
                0,
                0,
                tzinfo=timezone.get_current_timezone(),
            )
            self.assertEqual(
                app_tags.natural_day(further, mock_user),
                "2025-04-10 15:00",
            )

    def test_iso_date_format_respects_user_preference(self):
        """iso_date_format should handle user choice keys without raising."""
        iso_user = SimpleNamespace(date_format=DateFormatChoices.ISO_8601)
        month_user = SimpleNamespace(date_format=DateFormatChoices.MONTH_D_YYYY)

        self.assertEqual(
            app_tags.iso_date_format("2026-03-04", iso_user),
            "2026-03-04",
        )
        self.assertEqual(
            app_tags.iso_date_format("2026-03-04", month_user),
            "Mar 04, 2026",
        )
        self.assertEqual(
            app_tags.iso_date_format(timezone.datetime(2026, 3, 4).date(), iso_user),
            "2026-03-04",
        )
        self.assertEqual(
            app_tags.iso_date_format("not-a-date", iso_user),
            "not-a-date",
        )

    @patch("app.templatetags.app_tags.date")
    def test_format_date_range_display_prefers_this_month_over_last_7_days(
        self, mock_date
    ):
        """Month-to-date ranges should keep the month label even when they span 7 days."""
        today = timezone.datetime(2026, 5, 7).date()
        mock_date.today.return_value = today

        self.assertEqual(
            app_tags.format_date_range_display(today.replace(day=1), today),
            "This Month",
        )

    def test_music_artist_url_returns_canonical_details_path(self):
        """Music artists should resolve to the canonical shared details route."""
        artist = Artist.objects.create(name="The Amazing Artist")

        self.assertEqual(
            app_tags.music_artist_url(artist),
            reverse(
                "music_artist_details",
                kwargs={
                    "artist_id": artist.id,
                    "artist_slug": "the-amazing-artist",
                },
            ),
        )

    def test_music_artist_join_phrase_normalizes_display_spacing(self):
        """Album artist separators should have consistent display spacing."""
        self.assertEqual(app_tags.music_artist_join_phrase("\u2014 "), " \u2014 ")
        self.assertEqual(app_tags.music_artist_join_phrase(" & "), " & ")
        self.assertEqual(app_tags.music_artist_join_phrase(","), ", ")
        self.assertEqual(app_tags.music_artist_join_phrase(""), "")

    def test_music_album_url_returns_nested_canonical_details_path(self):
        """Music albums should resolve to the nested artist/album shared route."""
        artist = Artist.objects.create(name="The Amazing Artist")
        album = Album.objects.create(title="First Record", artist=artist)

        self.assertEqual(
            app_tags.music_album_url(album),
            reverse(
                "music_album_details",
                kwargs={
                    "artist_id": artist.id,
                    "artist_slug": "the-amazing-artist",
                    "album_id": album.id,
                    "album_slug": "first-record",
                },
            ),
        )

    def test_music_album_url_accepts_statistics_track_rollup_dict(self):
        """Track rollup dicts with album metadata should still resolve canonically."""
        self.assertEqual(
            app_tags.music_album_url(
                {
                    "album_id": 17,
                    "album": "Live at Home",
                    "album_artist_id": 9,
                    "album_artist_name": "Short Name",
                },
            ),
            reverse(
                "music_album_details",
                kwargs={
                    "artist_id": 9,
                    "artist_slug": "short-name",
                    "album_id": 17,
                    "album_slug": "live-at-home",
                },
            ),
        )

    def test_music_track_url_returns_nested_canonical_details_path(self):
        """Music tracks should resolve under their album on the shared route."""
        artist = Artist.objects.create(name="The Amazing Artist")
        album = Album.objects.create(title="First Record", artist=artist)
        track = Track.objects.create(album=album, title="Opening Song")

        self.assertEqual(
            app_tags.music_track_url(track),
            reverse(
                "music_track_details",
                kwargs={
                    "artist_id": artist.id,
                    "artist_slug": "the-amazing-artist",
                    "album_id": album.id,
                    "album_slug": "first-record",
                    "track_id": track.id,
                    "track_slug": "opening-song",
                },
            ),
        )

    def test_studio_url_returns_canonical_details_path(self):
        """Studio objects should resolve to the canonical shared details route."""
        studio = Studio.objects.create(
            source=Sources.IGDB.value,
            source_studio_id="123",
            name="CD Projekt Red",
        )

        self.assertEqual(
            app_tags.studio_url(studio),
            reverse(
                "studio_detail",
                kwargs={
                    "source": Sources.IGDB.value,
                    "studio_id": studio.source_studio_id,
                    "name": "cd-projekt-red",
                },
            ),
        )

    def test_studio_url_accepts_metadata_dict(self):
        """Studio metadata dicts should still resolve canonically."""
        self.assertEqual(
            app_tags.studio_url(
                {
                    "source": Sources.TMDB.value,
                    "studio_id": 44,
                    "name": "Pixar Animation Studios",
                },
            ),
            reverse(
                "studio_detail",
                kwargs={
                    "source": Sources.TMDB.value,
                    "studio_id": 44,
                    "name": "pixar-animation-studios",
                },
            ),
        )

    def test_media_card_uses_canonical_music_album_url(self):
        """Music media cards should link through the nested shared album route."""
        artist = Artist.objects.create(name="Card Artist")
        album = Album.objects.create(title="Card Album", artist=artist)
        item = Item.objects.create(
            media_id="track-card-1",
            source=Sources.MUSICBRAINZ.value,
            media_type=MediaTypes.MUSIC.value,
            title="Card Song",
            image="http://example.com/card-album.jpg",
        )
        request = self.request_factory.get("/library")
        request.user = self.user

        content = render_card_template(
            {
                "item": item,
                "media": SimpleNamespace(
                    album=album,
                    status=None,
                    progress=None,
                    next_event=None,
                    episodes_left=0,
                ),
                "user": self.user,
                "title": item.title,
                "show_status_chip": False,
            },
            request=request,
        )

        self.assertIn(
            reverse(
                "music_album_details",
                kwargs={
                    "artist_id": artist.id,
                    "artist_slug": "card-artist",
                    "album_id": album.id,
                    "album_slug": "card-album",
                },
            ),
            card_model(content)["href"],
        )

    def _render_book_card(self, *, status, progress, percentage=False, audiobook=False):
        """Render media_card.html for a book and return its markup."""
        item = Item.objects.create(
            media_id=f"book-card-{status}-{audiobook}",
            source=Sources.OPENLIBRARY.value,
            media_type=MediaTypes.BOOK.value,
            title="Card Book",
            image="http://example.com/book.jpg",
            release_datetime=datetime(2020, 1, 1, tzinfo=UTC),
            format="audiobook" if audiobook else "",
            runtime_minutes=600 if audiobook else None,
            number_of_pages=None if audiobook else 350,
        )
        self.user.book_comic_manga_progress_percentage = percentage
        self.user.save(update_fields=["book_comic_manga_progress_percentage"])

        media = Book.objects.create(
            item=item, user=self.user, status=status, progress=progress
        )
        BasicMedia.objects.annotate_max_progress([media], MediaTypes.BOOK.value)

        request = self.request_factory.get("/library")
        request.user = self.user
        return render_card_template(
            {
                "item": item,
                "media": media,
                "user": self.user,
                "title": item.title,
                "show_status_chip": False,
                "from_grid": True,
            },
            request=request,
        )

    def test_media_card_shows_book_progress_when_dropped(self):
        """A dropped book keeps showing how far the reader got."""
        content = self._render_book_card(status=Status.DROPPED.value, progress=120)

        self.assertIn("120/350 pages", card_text(content))

    def test_media_card_shows_book_progress_when_paused(self):
        """A paused book keeps showing how far the reader got."""
        content = self._render_book_card(status=Status.PAUSED.value, progress=120)

        self.assertIn("120/350 pages", card_text(content))

    def test_media_card_shows_percentage_without_raw_total(self):
        """Percentage mode renders a bare percentage, never '34% / 350'."""
        content = self._render_book_card(
            status=Status.DROPPED.value, progress=120, percentage=True
        )

        self.assertIn("34%", card_text(content))
        lines = json.dumps(card_model(content)["lines"])
        self.assertNotIn("% /", lines)
        self.assertNotIn("350", lines)

    def test_media_card_shows_audiobook_progress_as_listening_time(self):
        """Audiobooks read as h/min on both sides of the slash, never as pages."""
        content = self._render_book_card(
            status=Status.IN_PROGRESS.value, progress=150, audiobook=True
        )

        self.assertIn("2h 30min/10h 00min", card_text(content))
        self.assertNotIn("pages", json.dumps(card_model(content)["lines"]))

    def test_media_card_shows_audiobook_percentage(self):
        """The percentage preference applies to audiobooks too."""
        content = self._render_book_card(
            status=Status.IN_PROGRESS.value,
            progress=150,
            percentage=True,
            audiobook=True,
        )

        self.assertIn("25%", card_text(content))

    def test_genres_cell_falls_back_to_plain_text_for_blank_media_type(self):
        """Genres cell shouldn't crash reversing 'medialist' for a blank media_type."""
        item = Item(
            media_id="blank-type-1",
            source=Sources.TMDB.value,
            media_type="",
            title="Legacy Item",
            genres=["Drama"],
        )

        content = render_to_string(
            "app/components/cells/media_genres_cell.html",
            {"media": SimpleNamespace(item=item)},
        )

        self.assertIn("Drama", content)
        self.assertNotIn("<a href", content)

    def test_progress_changer_uses_episode_label_for_tv_and_season(self):
        """Quick progress controls should use episode labels for TV-derived progress."""
        tv_content = render_to_string(
            "app/components/progress_changer.html",
            {
                "media": SimpleNamespace(
                    id=1,
                    item=self.tv_item,
                    progress=1,
                    max_progress=10,
                    formatted_progress="1",
                ),
                "csrf_token": "token",
                "MediaTypes": MediaTypes,
            },
        )
        season_content = render_to_string(
            "app/components/progress_changer.html",
            {
                "media": SimpleNamespace(
                    id=2,
                    item=self.season_item,
                    progress=1,
                    max_progress=10,
                    formatted_progress="1",
                ),
                "csrf_token": "token",
                "MediaTypes": MediaTypes,
            },
        )

        self.assertIn("Episode", tv_content)
        self.assertNotIn('progress-unit"> s', tv_content)
        self.assertIn("Episodes", season_content)

    def test_media_card_teleports_alt_title_tooltip(self):
        """Grid media cards should teleport alternate-title tooltips outside the clipped shell."""
        item = Item(
            media_id="tooltip-card-1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.ANIME.value,
            title="Attack on Titan",
            localized_title="Attack on Titan",
            original_title="Shingeki no Kyojin",
            image="http://example.com/tooltip-card.jpg",
        )
        request = self.request_factory.get("/search")
        request.user = self.user

        content = render_card_template(
            {
                "item": item,
                "media": SimpleNamespace(
                    album=None,
                    status=None,
                    progress=None,
                    next_event=None,
                    episodes_left=0,
                ),
                "user": self.user,
                "title": item.title,
                "from_grid": True,
                "show_status_chip": False,
            },
            request=request,
        )

        self.assertIn("media-card-visual", content)
        self.assertTrue(card_model(content)["altTitle"])

    def test_history_card_uses_canonical_music_album_url(self):
        """Music history cards should link to the shared nested album route."""
        artist = Artist.objects.create(name="History Artist")
        album = Album.objects.create(title="History Album", artist=artist)
        item = Item.objects.create(
            media_id="track-history-1",
            source=Sources.MUSICBRAINZ.value,
            media_type=MediaTypes.MUSIC.value,
            title="History Song",
            image="http://example.com/history-album.jpg",
        )
        request = self.request_factory.get("/history")
        request.user = self.user

        content = render_to_string(
            "app/components/history_card.html",
            {
                "entry": SimpleNamespace(
                    media_type=MediaTypes.MUSIC.value,
                    album=album,
                    item=item,
                    poster=item.image,
                    status=None,
                    runtime_display=None,
                    display_title=item.title,
                    title=item.title,
                    played_at_local=timezone.now(),
                    time_range_display="6:00 PM",
                    play_count=1,
                    progress_display=None,
                    episode_label=None,
                    episode_code=None,
                    show=None,
                    score=None,
                    entry_key="music-entry-1",
                    instance_id=1,
                ),
                "card_class": "search-result-card-square",
                "history_mode": "history",
                "user": self.user,
            },
            request=request,
        )

        self.assertIn(
            reverse(
                "music_album_details",
                kwargs={
                    "artist_id": artist.id,
                    "artist_slug": "history-artist",
                    "album_id": album.id,
                    "album_slug": "history-album",
                },
            ),
            content,
        )

    def test_history_modal_actions_are_visible_on_touch_and_keyboard_focus(self):
        """History actions remain discoverable without a mouse hover."""
        request = self.request_factory.get("/media/movie/238")
        request.user = self.user
        content = render_to_string(
            "app/components/fill_history.html",
            {
                "edit_modal_url": "/track-modal/movie/238",
                "entry": None,
                "media_type": MediaTypes.MOVIE.value,
                "return_url": "/media/movie/238",
                "timeline": [
                    SimpleNamespace(
                        id=12,
                        instance_id=34,
                        date=timezone.now(),
                        media_entry_number=1,
                        changes=["Completed"],
                    ),
                ],
                "user": self.user,
            },
            request=request,
        )

        self.assertEqual(content.count("pointer-coarse:opacity-100"), 2)
        self.assertEqual(content.count("focus-visible:opacity-100"), 2)
        self.assertIn('hx-confirm="Delete this activity entry? This cannot be undone."', content)

    def test_media_card_touch_reveal_respects_overlay_preference_and_bulk_selection(self):
        """The card handler yields to immediate navigation and bulk selection."""
        self.user.clickable_media_cards = True
        request = self.request_factory.get("/media")
        request.user = self.user
        content = render_card_template(
            {
                "item": self.tv_item,
                "media": SimpleNamespace(
                    album=None,
                    artist=None,
                    status=None,
                    progress=None,
                    next_event=None,
                    episodes_left=0,
                ),
                "user": self.user,
                "title": self.tv_item.title,
                "from_grid": True,
                "show_status_chip": False,
                "enable_bulk_select": True,
            },
            request=request,
        )

        self.assertIn("media-card-hide-overlay", content)
        model = card_model(content)
        self.assertEqual(model["bulkItemId"], self.tv_item.id)
        self.assertTrue(model["actions"]["items"])

    def test_history_card_episode_shows_watched_status_and_uses_track_modal(self):
        """Episode history cards show watched status and open the track modal."""
        item = Item.objects.create(
            media_id="episode-history-1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="History Episode",
            image="http://example.com/history-episode.jpg",
            season_number=1,
            episode_number=2,
        )
        request = self.request_factory.get("/history")
        request.user = self.user

        context = {
            "entry": SimpleNamespace(
                media_type=MediaTypes.EPISODE.value,
                album=None,
                item=item,
                poster=item.image,
                status=None,
                runtime_display=None,
                display_title=item.title,
                title=item.title,
                played_at_local=timezone.now(),
                time_range_display="6:00 PM",
                play_count=1,
                progress_display=None,
                episode_label="S1E2",
                episode_code="S1E2",
                show=None,
                score=8,
                entry_key="episode-entry-1",
                instance_id=7,
            ),
            "card_class": "search-result-card",
            "history_mode": "activity",
            "user": self.user,
        }
        content = render_to_string(
            "app/components/history_card.html", context, request=request
        )

        expected_track_url = reverse(
            "track_modal",
            kwargs={
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.EPISODE.value,
                "media_id": "episode-history-1",
                "season_number": 1,
            },
        )
        self.assertIn(f'hx-get="{expected_track_url}"', content)
        self.assertIn('"instance_id": "7"', content)
        self.assertIn('"standard_modal": "1"', content)
        self.assertNotIn('hx-get="/history_modal/', content)
        self.assertIn("media-status-chip", content)
        self.assertIn("Status: Completed", content)
        self.assertIn('aria-hidden="true"', content)

        release_content = render_to_string(
            "app/components/history_card.html",
            {**context, "history_mode": "release"},
            request=request,
        )
        self.assertNotIn("media-status-chip", release_content)
        self.assertNotIn("Status: Completed", release_content)

    def test_history_card_teleports_alt_title_tooltip(self):
        """History cards should teleport alternate-title tooltips outside the clipped shell."""
        item = Item.objects.create(
            media_id="tooltip-history-1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.ANIME.value,
            title="Attack on Titan",
            localized_title="Attack on Titan",
            original_title="Shingeki no Kyojin",
            image="http://example.com/tooltip-history.jpg",
        )
        request = self.request_factory.get("/history")
        request.user = self.user

        content = render_to_string(
            "app/components/history_card.html",
            {
                "entry": SimpleNamespace(
                    media_type=MediaTypes.ANIME.value,
                    album=None,
                    item=item,
                    poster=item.image,
                    status=None,
                    runtime_display=None,
                    display_title=item.title,
                    title=item.title,
                    played_at_local=timezone.now(),
                    time_range_display="6:00 PM",
                    play_count=1,
                    progress_display=None,
                    episode_label=None,
                    episode_code=None,
                    show=None,
                    score=None,
                    entry_key="history-entry-1",
                    instance_id=1,
                ),
                "card_class": "search-result-card",
                "history_mode": "history",
                "user": self.user,
            },
            request=request,
        )

        self.assertIn("media-card-visual", content)
        self.assertIn('aria-label="Show alternative title"', content)
        self.assertIn('x-teleport="body"', content)
        self.assertIn('x-ref="panel"', content)

    def test_match_percent_clamps_and_rounds(self):
        """match_percent should clamp values to [0,100] and round."""
        self.assertEqual(app_tags.match_percent(0.9123), 91)
        self.assertEqual(app_tags.match_percent(1.6), 100)
        self.assertEqual(app_tags.match_percent(-0.4), 0)
        self.assertEqual(app_tags.match_percent(None), None)

    def test_media_url(self):
        """Test the media_url filter."""
        # Test with object for TV
        tv_url = app_tags.media_url(self.tv_item)
        expected_tv_url = reverse(
            "media_details",
            kwargs={
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.TV.value,
                "media_id": "1668",
                "title": "test-tv-show",
            },
        )
        self.assertEqual(tv_url, expected_tv_url)

        # Test with dict for TV
        tv_dict_url = app_tags.media_url(self.tv_dict)
        self.assertEqual(tv_dict_url, expected_tv_url)

        # Test with object for Season
        season_url = app_tags.media_url(self.season_item)
        expected_season_url = reverse(
            "season_details",
            kwargs={
                "source": Sources.TMDB.value,
                "media_id": "1668",
                "title": "test-tv-show",
                "season_number": 1,
            },
        )
        self.assertEqual(season_url, expected_season_url)

        # Test with dict for Season
        season_dict_url = app_tags.media_url(self.season_dict)
        self.assertEqual(season_dict_url, expected_season_url)

    def test_component_id(self):
        """Test the component_id tag."""
        # Test with object for TV
        tv_id = app_tags.component_id("card", self.tv_item)
        self.assertEqual(tv_id, "card-tv-1668")

        # Test with dict for TV
        tv_dict_id = app_tags.component_id("card", self.tv_dict)
        self.assertEqual(tv_dict_id, "card-tv-1668")

        # Test with object for Season
        season_id = app_tags.component_id("card", self.season_item)
        self.assertEqual(season_id, "card-season-1668-1")

        # Test with dict for Season
        season_dict_id = app_tags.component_id("card", self.season_dict)
        self.assertEqual(season_dict_id, "card-season-1668-1")

        # Test with object for Episode
        episode_id = app_tags.component_id("card", self.episode_item)
        self.assertEqual(episode_id, "card-episode-1668-1-1")

        # Test with dict for Episode
        episode_dict_id = app_tags.component_id("card", self.episode_dict)
        self.assertEqual(episode_dict_id, "card-episode-1668-1-1")

        # Objects without season/episode attributes should still resolve safely
        candidate_like = SimpleNamespace(
            media_type=MediaTypes.TV.value,
            media_id="1668",
        )
        self.assertEqual(app_tags.component_id("card", candidate_like), "card-tv-1668")

        # Podcast media_ids contain ":" (e.g. "itunes:12345"), which is invalid
        # inside a CSS id selector used by hx-target="#...". It must be
        # sanitized so htmx doesn't throw a querySelectorAll SyntaxError (#502).
        podcast_like = SimpleNamespace(
            media_type=MediaTypes.PODCAST.value,
            media_id="itunes:1247343210",
        )
        podcast_id = app_tags.component_id("track", podcast_like, 4)
        self.assertNotIn(":", podcast_id)
        self.assertEqual(podcast_id, "track-podcast-itunes-1247343210-4")

        # URL-shaped podcast media IDs must produce valid CSS selectors (#949).
        podcast_url_like = SimpleNamespace(
            media_type=MediaTypes.PODCAST.value,
            media_id="https://api.spreaker.com/episode/74415563",
        )
        podcast_url_id = app_tags.component_id("history", podcast_url_like)
        self.assertEqual(
            podcast_url_id,
            "history-podcast-https---api-spreaker-com-episode-74415563",
        )
        self.assertRegex(podcast_url_id, r"^[A-Za-z0-9_-]+$")

    def test_season_card_title(self):
        """Test the season_card_title tag includes the parent show title (#1060)."""
        # Numbered season, object input
        self.assertEqual(
            app_tags.season_card_title(self.season_item),
            "Test TV Show Season 1",
        )

        # Numbered season, dict input
        self.assertEqual(
            app_tags.season_card_title(self.season_dict),
            "Test TV Show Season 1",
        )

        # Specials (season 0), object input
        specials_item = Item(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test TV Show",
            season_number=0,
        )
        self.assertEqual(
            app_tags.season_card_title(specials_item),
            "Test TV Show Specials",
        )

        # Specials (season 0), dict input
        specials_dict = {**self.season_dict, "season_number": 0}
        self.assertEqual(
            app_tags.season_card_title(specials_dict),
            "Test TV Show Specials",
        )

        # Named provider/arc title, object input
        arc_item = SimpleNamespace(
            title="Test TV Show",
            season_number=1,
            season_title="Indigo League",
        )
        self.assertEqual(
            app_tags.season_card_title(arc_item),
            "Test TV Show: Indigo League",
        )

        # Named provider/arc title, dict input
        arc_dict = {
            "title": "Test TV Show",
            "season_number": 1,
            "season_title": "Indigo League",
        }
        self.assertEqual(
            app_tags.season_card_title(arc_dict),
            "Test TV Show: Indigo League",
        )

        # Missing fallback title falls back to the bare season string
        no_title_item = SimpleNamespace(title="", season_number=2, season_title=None)
        self.assertEqual(app_tags.season_card_title(no_title_item), "Season 2")

        # Missing fallback title with a named arc falls back to the bare arc title
        no_title_arc_item = SimpleNamespace(
            title="",
            season_number=1,
            season_title="Indigo League",
        )
        self.assertEqual(
            app_tags.season_card_title(no_title_arc_item),
            "Indigo League",
        )

    def test_media_view_url(self):
        """Test the media_view_url tag."""
        # Test with object for TV
        tv_modal = app_tags.media_view_url("track_modal", self.tv_item)
        expected_tv_modal = reverse(
            "track_modal",
            kwargs={
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.TV.value,
                "media_id": "1668",
            },
        )
        self.assertEqual(tv_modal, expected_tv_modal)

        # Test with dict for TV
        tv_dict_modal = app_tags.media_view_url("track_modal", self.tv_dict)
        self.assertEqual(tv_dict_modal, expected_tv_modal)

        # Test with object for Episode
        episode_modal = app_tags.media_view_url("history_modal", self.episode_item)
        expected_episode_modal = reverse(
            "history_modal",
            kwargs={
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.EPISODE.value,
                "media_id": "1668",
                "season_number": 1,
                "episode_number": 1,
            },
        )
        self.assertEqual(episode_modal, expected_episode_modal)

        # Test with dict for Episode
        episode_dict_modal = app_tags.media_view_url(
            "history_modal",
            self.episode_dict,
        )
        self.assertEqual(episode_dict_modal, expected_episode_modal)

        expected_episode_track_modal = reverse(
            "track_modal",
            kwargs={
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.EPISODE.value,
                "media_id": "1668",
                "season_number": 1,
            },
        )
        self.assertEqual(
            app_tags.media_view_url("track_modal", self.episode_item),
            expected_episode_track_modal,
        )
        self.assertEqual(
            app_tags.media_view_url("track_modal", self.episode_dict),
            expected_episode_track_modal,
        )

        # Test with podcast ID containing path separators
        podcast_episode_dict = {
            "source": Sources.POCKETCASTS.value,
            "media_type": MediaTypes.PODCAST.value,
            "media_id": "gid://art19-episode-locator/V0/MCjgWTshRbS9H7f24imvk8a2E6Zsyb6NQJHy6B0h6hQ",
        }
        podcast_lists_modal = app_tags.media_view_url(
            "lists_modal",
            podcast_episode_dict,
        )
        expected_podcast_lists_modal = reverse(
            "lists_modal",
            kwargs={
                "source": Sources.POCKETCASTS.value,
                "media_type": MediaTypes.PODCAST.value,
                "media_id": podcast_episode_dict["media_id"],
            },
        )
        self.assertEqual(podcast_lists_modal, expected_podcast_lists_modal)

        # Objects without season/episode attributes should still resolve safely
        candidate_like = SimpleNamespace(
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            media_id="1668",
        )
        self.assertEqual(
            app_tags.media_view_url("track_modal", candidate_like),
            expected_tv_modal,
        )

        # TMDB includes season_number=0 on top-level anime metadata. It is not
        # a season route and must not become /lists_modal/.../<id>/0.
        anime_dict = {
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.ANIME.value,
            "media_id": "83611",
            "season_number": 0,
        }
        self.assertEqual(
            app_tags.media_view_url("lists_modal", anime_dict),
            reverse(
                "lists_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.ANIME.value,
                    "media_id": "83611",
                },
            ),
        )

    def test_unicode_icon(self):
        """Test the unicode_icon tag for all media types."""
        # Test all media types from MediaTypes
        for media_type in MediaTypes.values:
            try:
                result = app_tags.unicode_icon(media_type)
                # Just check that we get a non-empty string
                self.assertTrue(isinstance(result, str))
                self.assertTrue(len(result) > 0)
            except KeyError:
                self.fail(f"unicode_icon raised KeyError for {media_type}")

    def test_icon_media_types(self):
        """Test the icon tag for all media types."""
        # Test all media types from MediaTypes
        for media_type in MediaTypes.values:
            try:
                # Test with both active and inactive states
                active_result = app_tags.icon(media_type, is_active=True)
                inactive_result = app_tags.icon(media_type, is_active=False)

                # Just check that we get a non-empty string
                self.assertTrue(isinstance(active_result, str))
                self.assertTrue(len(active_result) > 0)
                self.assertTrue(isinstance(inactive_result, str))
                self.assertTrue(len(inactive_result) > 0)
            except KeyError:
                self.fail(f"icon raised KeyError for {media_type}")

    def test_show_media_score(self):
        """Test if we should show media rating or not."""
        # Create mock users
        mock_user_show = MagicMock()
        mock_user_show.hide_zero_rating = False

        mock_user_hide = MagicMock()
        mock_user_hide.hide_zero_rating = True

        # With hide_zero_rating=False, show all non-None scores
        self.assertTrue(app_tags.show_media_score(1, mock_user_show))
        self.assertTrue(app_tags.show_media_score(0, mock_user_show))
        self.assertFalse(app_tags.show_media_score(None, mock_user_show))

        # With hide_zero_rating=True, hide zero scores
        self.assertTrue(app_tags.show_media_score(1, mock_user_hide))
        self.assertFalse(app_tags.show_media_score(0, mock_user_hide))
        self.assertFalse(app_tags.show_media_score(None, mock_user_hide))


class NextEpisodeUrlTests(TestCase):
    """Test the next_episode_url template tag."""

    def setUp(self):
        """Set up a user for tracked media."""
        self.user = get_user_model().objects.create_user(
            username="nextep",
            password="12345",
        )

    def _create_tv_with_completed_season(self, media_id="1668", progress=8):
        """Return a TV show whose only tracked season is completed."""
        from app.models import TV, Season, Status

        tv_item = Item.objects.create(
            media_id=media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Untracked Next Season TV",
            image="http://example.com/tv.jpg",
        )
        tv = TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        season_item = Item.objects.create(
            media_id=media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Untracked Next Season TV",
            image="http://example.com/tv-s1.jpg",
            season_number=1,
        )
        # Create as PLANNING then flip via update() so the fixture does not
        # trigger the completed-on-create fan-out (which fetches metadata).
        season = Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=tv,
            status=Status.PLANNING.value,
        )
        Season.objects.filter(pk=season.pk).update(
            status=Status.COMPLETED.value,
        )
        return tv_item, tv

    def test_tv_show_falls_back_to_untracked_season_events(self):
        """Link the first event episode of the next untracked season."""
        from events.models import Event

        tv_item, tv = self._create_tv_with_completed_season()
        untracked_season_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Untracked Next Season TV",
            image="http://example.com/tv-s2.jpg",
            season_number=2,
        )
        Event.objects.create(
            item=untracked_season_item,
            content_number=1,
            datetime=timezone.now(),
            notification_sent=False,
        )

        url = app_tags.next_episode_url(tv_item, tv)

        self.assertEqual(
            url,
            reverse(
                "episode_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "1668",
                    "title": "untracked-next-season-tv",
                    "season_number": 2,
                    "episode_number": 1,
                },
            ),
        )

    def test_tv_show_without_untracked_events_returns_empty(self):
        """No fallback URL when the untracked season has no events."""
        tv_item, tv = self._create_tv_with_completed_season()

        self.assertEqual(app_tags.next_episode_url(tv_item, tv), "")

    def test_flat_mal_anime_links_next_episode_redirect(self):
        """Flat MAL anime cards link the click-time resolver endpoint."""
        from app.models import Anime, Status

        anime_item = Item.objects.create(
            media_id="51553",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Witch Hat Atelier",
            image="http://example.com/anime.jpg",
        )
        anime = Anime.objects.create(
            item=anime_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=3,
        )

        url = app_tags.next_episode_url(anime_item, anime)

        self.assertEqual(
            url,
            reverse(
                "anime_next_episode",
                kwargs={"media_id": "51553", "title": "witch-hat-atelier"},
            ),
        )

    def _create_tv_with_season_stuck_planning(
        self,
        media_id="90210",
        season_number=1,
        watched_episodes=5,
    ):
        """Return a TV show whose tracked season has real progress but a
        stale PLANNING status (as left behind by a failed metadata fetch
        during Episode.save, e.g. a bulk import — see issue #517).
        """
        from app.models import TV, Episode, Season, Status
        from app.providers.services import ProviderAPIError

        tv_item = Item.objects.create(
            media_id=media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Stuck Planning TV",
            image="http://example.com/tv.jpg",
        )
        tv = TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        season_item = Item.objects.create(
            media_id=media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Stuck Planning TV",
            image=f"http://example.com/tv-s{season_number}.jpg",
            season_number=season_number,
        )
        season = Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=tv,
            status=Status.PLANNING.value,
        )
        from events.models import Event

        for episode_number in range(1, watched_episodes + 2):
            Event.objects.create(
                item=season_item,
                content_number=episode_number,
                datetime=timezone.now(),
            )
        for episode_number in range(1, watched_episodes + 1):
            episode_item = Item.objects.create(
                media_id=media_id,
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                season_number=season_number,
                episode_number=episode_number,
                title=f"Episode {episode_number}",
                image="",
            )
            # Episode.save() tries a provider metadata fetch to sync the
            # season's status; force it to fail (as it does in production
            # when the metadata call errors during a bulk import), leaving
            # the season's raw status untouched at PLANNING despite this
            # real watch progress.
            with patch(
                "app.models.tv.providers.services.get_media_metadata",
                side_effect=ProviderAPIError("tmdb", Exception("boom")),
            ):
                Episode.objects.create(
                    item=episode_item,
                    related_season=season,
                    end_date=timezone.now(),
                )
        season.refresh_from_db()
        self.assertEqual(season.status, Status.PLANNING.value)
        return tv_item, tv, season_item

    def test_tv_show_uses_derived_status_for_only_season_stuck_planning(self):
        """A single season with real progress but stale PLANNING status still
        routes to its own next episode, not the untracked-season fallback.
        """
        tv_item, tv, season_item = self._create_tv_with_season_stuck_planning(
            season_number=1,
            watched_episodes=5,
        )

        url = app_tags.next_episode_url(tv_item, tv)

        self.assertEqual(
            url,
            reverse(
                "episode_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": season_item.media_id,
                    "title": "stuck-planning-tv",
                    "season_number": 1,
                    "episode_number": 6,
                },
            ),
        )

    def test_tv_show_does_not_skip_to_next_season_when_current_stuck_planning(self):
        """A completed earlier season shouldn't cause the button to skip past
        a later, still-in-progress season whose status is stuck at PLANNING.
        """
        from app.models import Episode, Season, Status
        from app.providers.services import ProviderAPIError

        tv_item, tv = self._create_tv_with_completed_season(media_id="90211")
        season_item = Item.objects.create(
            media_id="90211",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Untracked Next Season TV",
            image="http://example.com/tv-s2.jpg",
            season_number=2,
        )
        season = Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=tv,
            status=Status.PLANNING.value,
        )
        from events.models import Event

        for episode_number in range(1, 5):
            Event.objects.create(
                item=season_item,
                content_number=episode_number,
                datetime=timezone.now(),
            )
        for episode_number in (1, 2, 3):
            episode_item = Item.objects.create(
                media_id="90211",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                season_number=2,
                episode_number=episode_number,
                title=f"Episode {episode_number}",
                image="",
            )
            with patch(
                "app.models.tv.providers.services.get_media_metadata",
                side_effect=ProviderAPIError("tmdb", Exception("boom")),
            ):
                Episode.objects.create(
                    item=episode_item,
                    related_season=season,
                    end_date=timezone.now(),
                )
        season.refresh_from_db()
        self.assertEqual(season.status, Status.PLANNING.value)

        url = app_tags.next_episode_url(tv_item, tv)

        self.assertEqual(
            url,
            reverse(
                "episode_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "90211",
                    "title": "untracked-next-season-tv",
                    "season_number": 2,
                    "episode_number": 4,
                },
            ),
        )

    def test_tv_show_uses_next_tracked_season_after_completed_season(self):
        """A completed season must not produce a nonexistent trailing episode."""
        from app.models import Episode, Season, Status
        from app.providers.services import ProviderAPIError
        from events.models import Event

        tv_item, tv = self._create_tv_with_completed_season(media_id="202851")
        season_item = Item.objects.create(
            media_id="202851",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Big Boys",
            image="http://example.com/tv-s2.jpg",
            season_number=2,
        )
        season = Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=tv,
            status=Status.PLANNING.value,
        )
        for episode_number in (1, 2):
            Event.objects.create(
                item=season_item,
                content_number=episode_number,
                datetime=timezone.now(),
            )
        episode_item = Item.objects.create(
            media_id="202851",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=2,
            episode_number=1,
            title="Big Boys",
            image="",
        )
        with patch(
            "app.models.tv.providers.services.get_media_metadata",
            side_effect=ProviderAPIError("tmdb", Exception("boom")),
        ):
            Episode.objects.create(
                item=episode_item,
                related_season=season,
                end_date=timezone.now(),
            )

        url = app_tags.next_episode_url(tv_item, tv)

        self.assertEqual(
            url,
            reverse(
                "episode_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "202851",
                    "title": "big-boys",
                    "season_number": 2,
                    "episode_number": 2,
                },
            ),
        )

    def test_tv_show_starts_next_planned_season_after_completed_season(self):
        """Deleting the first play of a tracked next season still shows S1E1."""
        from app.models import Episode, Season, Status
        from app.providers.services import ProviderAPIError
        from events.models import Event

        tv_item, tv = self._create_tv_with_completed_season(media_id="202851")
        season_item = Item.objects.create(
            media_id="202851",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Big Boys",
            image="http://example.com/tv-s2.jpg",
            season_number=2,
        )
        season = Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=tv,
            status=Status.PLANNING.value,
        )
        for episode_number in (1, 2):
            Event.objects.create(
                item=season_item,
                content_number=episode_number,
                datetime=timezone.now(),
            )
        episode_item = Item.objects.create(
            media_id="202851",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=2,
            episode_number=1,
            title="Big Boys",
            image="",
        )
        with patch(
            "app.models.tv.providers.services.get_media_metadata",
            side_effect=ProviderAPIError("tmdb", Exception("boom")),
        ):
            Episode.objects.create(
                item=episode_item,
                related_season=season,
                end_date=timezone.now(),
            )
        Episode.objects.filter(item=episode_item, related_season=season).delete()

        self.assertEqual(
            app_tags.next_episode_url(tv_item, tv),
            reverse(
                "episode_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "202851",
                    "title": "big-boys",
                    "season_number": 2,
                    "episode_number": 1,
                },
            ),
        )

    def test_tv_show_does_not_link_past_last_known_episode(self):
        """A fully watched season without a next event has no Next ep link."""
        from app.models import TV, Season, Status
        from events.models import Event

        tv_item, tv, _season_item = self._create_tv_with_season_stuck_planning(
            media_id="2382",
            watched_episodes=5,
        )

        season = Season.objects.get(related_tv=tv)
        Season.objects.filter(pk=season.pk).update(status=Status.COMPLETED.value)
        TV.objects.filter(pk=tv.pk).update(status=Status.COMPLETED.value)
        Event.objects.filter(item=season.item, content_number=6).delete()
        tv.refresh_from_db()
        season.refresh_from_db()

        self.assertEqual(app_tags.next_episode_url(tv_item, tv), "")

    def test_tv_show_uses_later_episode_when_only_later_season_is_tracked(self):
        """The resolver stays on a tracked later season instead of season one."""
        tv_item, tv, _season_item = self._create_tv_with_season_stuck_planning(
            media_id="2352",
            season_number=3,
            watched_episodes=17,
        )

        url = app_tags.next_episode_url(tv_item, tv)

        self.assertEqual(
            url,
            reverse(
                "episode_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "2352",
                    "title": "stuck-planning-tv",
                    "season_number": 3,
                    "episode_number": 18,
                },
            ),
        )

    def _create_issue_567_tv(self, media_id, title, seasons, statuses=None):
        """Create the watched/released episode shape from issue #567.

        `statuses` overrides the derived season status per season number, for
        the paused-season shapes reported in issue #634.
        """
        from app.models import TV, Episode, Season, Status
        from app.providers.services import ProviderAPIError
        from events.models import Event

        tv_item = Item.objects.create(
            media_id=media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title=title,
            image="",
        )
        tv = TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        for season_number, (watched, available) in seasons.items():
            season_item = Item.objects.create(
                media_id=media_id,
                source=Sources.TMDB.value,
                media_type=MediaTypes.SEASON.value,
                title=title,
                image="",
                season_number=season_number,
            )
            season = Season.objects.create(
                item=season_item,
                user=self.user,
                related_tv=tv,
                status=Status.PLANNING.value,
            )
            for episode_number in range(1, available + 1):
                Event.objects.create(
                    item=season_item,
                    content_number=episode_number,
                    datetime=timezone.now(),
                )
            for episode_number in range(1, watched + 1):
                episode_item = Item.objects.create(
                    media_id=media_id,
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.EPISODE.value,
                    season_number=season_number,
                    episode_number=episode_number,
                    title=title,
                    image="",
                )
                with patch(
                    "app.models.tv.providers.services.get_media_metadata",
                    side_effect=ProviderAPIError("tmdb", Exception("boom")),
                ):
                    Episode.objects.create(
                        item=episode_item,
                        related_season=season,
                        end_date=timezone.now(),
                    )
            if watched == available:
                Season.objects.filter(pk=season.pk).update(
                    status=Status.COMPLETED.value,
                )

            override = (statuses or {}).get(season_number)
            if override is not None:
                Season.objects.filter(pk=season.pk).update(status=override)

        return tv_item, tv

    def test_issue_567_examples_resolve_to_expected_next_episode(self):
        """All six reported examples resolve from the same episode contract."""
        cases = (
            ("202851", "Big Boys", {1: (6, 6), 2: (1, 2)}, (2, 2)),
            ("2352", "The Nanny", {3: (17, 18)}, (3, 18)),
            ("90282", "The Morning Show", {1: (10, 10), 2: (3, 4)}, (2, 4)),
            ("69050", "Riverdale", {1: (3, 4)}, (1, 4)),
            ("2382", "Freaks and Geeks", {1: (5, 5)}, None),
            (
                "18202",
                "Cougar Town",
                {1: (5, 5), 2: (5, 5), 3: (5, 5), 4: (5, 5), 5: (5, 5), 6: (1, 2)},
                (6, 2),
            ),
        )

        for media_id, title, seasons, expected in cases:
            with self.subTest(title=title):
                tv_item, tv = self._create_issue_567_tv(media_id, title, seasons)
                url = app_tags.next_episode_url(tv_item, tv)

                if expected is None:
                    self.assertEqual(url, "")
                    continue

                season_number, episode_number = expected
                self.assertEqual(
                    url,
                    reverse(
                        "episode_details",
                        kwargs={
                            "source": Sources.TMDB.value,
                            "media_id": media_id,
                            "title": title.lower().replace(" ", "-"),
                            "season_number": season_number,
                            "episode_number": episode_number,
                        },
                    ),
                )

    def test_issue_634_paused_season_is_the_next_episode_target(self):
        """A paused season the user is partway through is still watchable next."""
        from app.models import Status

        cases = (
            # Freaks and Geeks: the only season is paused at 5 of 18.
            (
                "2382",
                "Freaks and Geeks",
                {1: (5, 18)},
                {1: Status.PAUSED.value},
                (1, 6),
            ),
            # Big Boys: completed S1, paused S2 at 1 of 6, planned S3.
            (
                "202851",
                "Big Boys",
                {1: (6, 6), 2: (1, 6), 3: (0, 6)},
                {2: Status.PAUSED.value},
                (2, 2),
            ),
        )

        for media_id, title, seasons, statuses, expected in cases:
            with self.subTest(title=title):
                tv_item, tv = self._create_issue_567_tv(
                    media_id,
                    title,
                    seasons,
                    statuses=statuses,
                )
                season_number, episode_number = expected

                self.assertEqual(
                    app_tags.next_episode_url(tv_item, tv),
                    reverse(
                        "episode_details",
                        kwargs={
                            "source": Sources.TMDB.value,
                            "media_id": media_id,
                            "title": title.lower().replace(" ", "-"),
                            "season_number": season_number,
                            "episode_number": episode_number,
                        },
                    ),
                )

    def test_paused_season_does_not_outrank_later_in_progress_season(self):
        """Pausing a season and moving on keeps the later season as the target."""
        from app.models import Status

        tv_item, tv = self._create_issue_567_tv(
            "69050",
            "Riverdale",
            {1: (3, 6), 2: (2, 6)},
            statuses={1: Status.PAUSED.value},
        )

        self.assertEqual(
            app_tags.next_episode_url(tv_item, tv),
            reverse(
                "episode_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "69050",
                    "title": "riverdale",
                    "season_number": 2,
                    "episode_number": 3,
                },
            ),
        )

    def test_paused_season_does_not_hand_off_to_next_planned_season(self):
        """A paused season blocks the planning continuation that follows it."""
        from app.models import Status

        tv_item, tv = self._create_issue_567_tv(
            "90282",
            "The Morning Show",
            {1: (10, 10), 2: (3, 6), 3: (0, 6)},
            statuses={2: Status.PAUSED.value},
        )

        self.assertEqual(
            app_tags.next_episode_url(tv_item, tv),
            reverse(
                "episode_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "90282",
                    "title": "the-morning-show",
                    "season_number": 2,
                    "episode_number": 4,
                },
            ),
        )

    def test_fully_watched_paused_season_still_starts_next_planned_season(self):
        """A caught-up paused season hands off like any other finished season."""
        from app.models import Status

        tv_item, tv = self._create_issue_567_tv(
            "18202",
            "Cougar Town",
            {1: (6, 6), 2: (0, 6)},
            statuses={1: Status.PAUSED.value},
        )

        self.assertEqual(
            app_tags.next_episode_url(tv_item, tv),
            reverse(
                "episode_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "18202",
                    "title": "cougar-town",
                    "season_number": 2,
                    "episode_number": 1,
                },
            ),
        )

    def test_dropped_season_is_still_skipped(self):
        """Dropped seasons stay transparent to the next-episode target."""
        from app.models import Status

        tv_item, tv = self._create_issue_567_tv(
            "2352",
            "The Nanny",
            {1: (6, 6), 2: (2, 6), 3: (0, 6)},
            statuses={2: Status.DROPPED.value},
        )

        self.assertEqual(
            app_tags.next_episode_url(tv_item, tv),
            reverse(
                "episode_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "2352",
                    "title": "the-nanny",
                    "season_number": 3,
                    "episode_number": 1,
                },
            ),
        )


class SafeCountFilterTests(TestCase):
    """Test the safe_count template filter used as blocktranslate's count arg."""

    def test_passes_through_int(self):
        self.assertEqual(app_tags.safe_count(5), 5)

    def test_coerces_numeric_string(self):
        self.assertEqual(app_tags.safe_count("7"), 7)

    def test_none_defaults_to_zero(self):
        self.assertEqual(app_tags.safe_count(None), 0)

    def test_non_numeric_string_defaults_to_zero(self):
        self.assertEqual(app_tags.safe_count("TBA"), 0)


class DetailPromotedFactsTests(TestCase):
    def test_media_type_field_priority_and_missing_values(self):
        details_by_type = {
            MediaTypes.TV.value: {
                "format": "TV",
                "first_air_date": "2022-02-03",
                "last_air_date": "2026-09-16",
                "status": "Returning Series",
                "runtime": "48m",
                "total_runtime": "25h 34min",
                "episodes": 32,
                "country": "United States of America",
                "languages": ["English"],
            },
            MediaTypes.MOVIE.value: {
                "format": "Movie",
                "release_date": "1991-11-21",
                "status": "Released",
                "runtime": "1h 15min",
                "certification": "PG",
                "country": "United States of America",
                "languages": ["English"],
            },
            MediaTypes.SEASON.value: {
                "first_air_date": "2022-02-03",
                "last_air_date": "2022-03-31",
                "episodes": 8,
                "runtime": "47m",
                "total_runtime": "6h 16min",
            },
            MediaTypes.GAME.value: {
                "release_date": "2025-03-18",
                "platforms": ["Windows", "PlayStation 5"],
                "format": "Main Game",
            },
        }
        expected_keys = {
            MediaTypes.TV.value: [
                "status",
                "air_dates",
                "total_runtime",
                "locale",
            ],
            MediaTypes.MOVIE.value: [
                "format",
                "release_date",
                "status",
                "runtime",
                "certification",
                "locale",
            ],
            MediaTypes.SEASON.value: [
                "air_dates",
                "episodes",
                "total_runtime",
            ],
            MediaTypes.GAME.value: ["release_date", "platforms", "format"],
        }

        for media_type, details in details_by_type.items():
            with self.subTest(media_type=media_type):
                promotion = app_tags.detail_promoted_facts(media_type, details)
                self.assertEqual(
                    [field["key"] for field in promotion["fields"]],
                    expected_keys[media_type],
                )

        missing = app_tags.detail_promoted_facts(
            MediaTypes.TV.value,
            {"format": "TV", "runtime": None, "country": ""},
        )
        self.assertEqual([field["key"] for field in missing["fields"]], ["format"])
        self.assertEqual(missing["suppressed_keys"], {"format"})
        tv_facts = app_tags.detail_promoted_facts(
            MediaTypes.TV.value,
            {
                "format": "TV",
                "status": "Returning Series",
                "country": "United States of America",
                "languages": ["English", "French"],
            },
        )
        self.assertEqual(tv_facts["fields"][0]["label"], "TV series status")
        self.assertEqual(
            tv_facts["fields"][-1]["value"],
            {"languages": ["English", "French"], "country": "United States of America"},
        )
        movie_facts = app_tags.detail_promoted_facts(
            MediaTypes.MOVIE.value,
            {"runtime": "1h 42m", "total_runtime": "1h 42min"},
        )
        self.assertEqual(movie_facts["suppressed_keys"], {"runtime", "total_runtime"})
        self.assertEqual(app_tags.country_code("United States of America"), "US")
        self.assertEqual(app_tags.country_code("Canada"), "CA")


class DetailScoreChipsTemplateTests(TestCase):
    """Non-numeric provider score_count must not crash blocktranslate (#1147).

    Season metadata is a plain dict assembled by different code paths per
    provider/sync state, so score_count isn't guaranteed to be a real int
    even when it isn't None (e.g. a freshly-migrated TVDB season). Without
    the `|safe_count` filter this raises
    `TemplateSyntaxError: 'count' argument to 'blocktranslate' tag must be a
    number.` and 500s the season detail page.
    """

    def _render(self, score_count):
        return render_to_string(
            "app/components/detail_score_chips.html",
            {
                "media": {"media_id": "1", "source": "tvdb", "score_count": score_count},
                "display_provider": Sources.TVDB.value,
                "Sources": Sources,
                "MediaTypes": MediaTypes,
                "trakt_score": None,
                "imdb_score": None,
                "mal_score": None,
                "user_medias": None,
                "current_instance": None,
                "public_view": False,
                "media_type": MediaTypes.SEASON.value,
                "provider_series_graph_data": None,
                "trakt_series_graph_data": None,
                "imdb_series_graph_data": None,
                "csrf_token": "x",
                "user": None,
            },
        )

    def test_non_numeric_score_count_does_not_crash(self):
        html = self._render("")
        self.assertIn("votes", html)

    def test_numeric_score_count_still_renders(self):
        html = self._render(42)
        self.assertIn("42", html)

    def test_chip_row_fades_the_edge_it_can_scroll_towards(self):
        html = self._render(42)
        self.assertIn("'detail-score-card-row--fade-left': hasOverflow && !atStart", html)
        self.assertIn("'detail-score-card-row--fade-right': hasOverflow && !atEnd", html)
        # Layout can change after init (carousel arrives, Duo unfolds).
        self.assertIn("new ResizeObserver(() => updateScroll()).observe($refs.scoreRow)", html)


class DetailActivitySubtitleTemplateTests(SimpleTestCase):
    """The phone subtitle breaks at the bullet on narrow widths instead of wrapping mid-date."""

    def _render(self, **subtitle):
        return render_to_string(
            "app/components/detail_activity_subtitle.html",
            {
                "activity_subtitle": subtitle,
                "media": {"media_id": "1", "source": "tmdb"},
                "media_type": MediaTypes.MOVIE.value,
                "MediaTypes": MediaTypes,
                "user": None,
            },
        )

    def test_duration_subtitle_uses_shared_presentation(self):
        html = self._render(
            duration_text="52h 42min watched",
            primary_text="Progress: 35/44",
            date_start=None,
            date_end=None,
        )
        self.assertIn("data-detail-activity-subtitle-root", html)
        self.assertIn('data-duration="52h 42min watched"', html)

    def test_dates_precede_duration_and_bullet_is_white(self):
        html = self._render(
            duration_text="1h watched",
            date_start=date(2020, 10, 10),
            date_end=date(2026, 9, 26),
        )
        self.assertIn('style="color: white"', html)
        self.assertIn('data-duration="1h watched"', html)


class EntrySourceLabelTests(TestCase):
    """Test the entry_source_label display filter (issue #1258)."""

    def test_built_in_lowercase_sources_are_capitalized(self):
        self.assertEqual(app_tags.entry_source_label("plex"), "Plex")
        self.assertEqual(app_tags.entry_source_label("jellyfin"), "Jellyfin")

    def test_special_casing_and_underscores(self):
        self.assertEqual(app_tags.entry_source_label("lastfm"), "Last.fm")
        self.assertEqual(app_tags.entry_source_label("imdb"), "IMDb")
        self.assertEqual(
            app_tags.entry_source_label("jellyfin_playback_reporting"),
            "Jellyfin playback reporting",
        )

    def test_user_typed_text_is_kept_as_typed(self):
        self.assertEqual(app_tags.entry_source_label("Theatre"), "Theatre")
        self.assertEqual(app_tags.entry_source_label("my BluRay"), "my BluRay")

    def test_empty_values(self):
        self.assertEqual(app_tags.entry_source_label(""), "")
        self.assertEqual(app_tags.entry_source_label(None), "")


class DetailActionRowTests(SimpleTestCase):
    """Which icons the detail action row shows, and which fold into More on phones."""

    def row(
        self,
        *,
        source=Sources.TMDB.value,
        media_type="movie",
        seerr=False,
        links=False,
        podcast_show=None,
    ):
        user = SimpleNamespace(
            seerr_url="https://seerr.test" if seerr else "",
            seerr_api_key="key" if seerr else "",
            seerr_user_id=1 if seerr else None,
        )
        context = {
            "request": SimpleNamespace(user=user),
            "detail_link_sections": [{"title": "Links"}] if links else [],
            "podcast_show": podcast_show,
        }
        return app_tags.detail_action_row(context, {"source": source}, media_type)

    def test_plain_page_keeps_every_icon_inline(self):
        row = self.row()
        self.assertEqual(row["overflow"], [])
        self.assertFalse(row["link"])
        self.assertFalse(row["seerr"])

    def test_manual_items_have_no_sync_icon(self):
        self.assertFalse(self.row(source=Sources.MANUAL.value)["sync"])
        self.assertTrue(self.row()["sync"])

    def test_seerr_needs_a_configured_server_and_a_tmdb_movie_or_show(self):
        self.assertTrue(self.row(seerr=True)["seerr"])
        self.assertTrue(self.row(seerr=True, media_type="tv")["seerr"])
        self.assertFalse(self.row(seerr=False)["seerr"])
        self.assertFalse(self.row(seerr=True, media_type="season")["seerr"])
        self.assertFalse(self.row(seerr=True, source=Sources.MAL.value)["seerr"])

    def test_podcast_shows_have_no_notes_icon(self):
        self.assertFalse(self.row(podcast_show=object())["notes"])
        self.assertTrue(self.row()["notes"])

    def test_five_icons_do_not_overflow(self):
        # tags, notes, lists, history, sync
        self.assertEqual(self.row()["overflow"], [])
        # tags, notes, lists, history (manual: no sync) + links
        self.assertEqual(
            self.row(source=Sources.MANUAL.value, links=True)["overflow"], []
        )

    def test_six_icons_move_the_last_two_into_more(self):
        row = self.row(links=True)
        self.assertEqual(row["overflow"], ["history", "sync"])

    def test_seven_icons_move_the_last_three_into_more(self):
        row = self.row(links=True, seerr=True)
        self.assertEqual(row["overflow"], ["seerr", "history", "sync"])

    def test_overflow_never_holds_the_first_four_icons(self):
        for links in (False, True):
            for seerr in (False, True):
                for source in (Sources.TMDB.value, Sources.MANUAL.value):
                    overflow = self.row(links=links, seerr=seerr, source=source)[
                        "overflow"
                    ]
                    self.assertTrue(set(overflow) <= {"history", "seerr", "sync"})


class MusicRelationsReactPropsTests(SimpleTestCase):
    """The shared artist relation grid receives plain, linkable rows."""

    def test_relation_rows_carry_status_score_and_href(self):
        member = SimpleNamespace(
            id=7,
            name="Elva Run",
            image="https://example.com/elva.jpg",
        )
        tracker = SimpleNamespace(status=Status.IN_PROGRESS.value, score=8.5)
        result = app_tags.music_relations_react(
            {},
            [
                {
                    "artist": member,
                    "role": "Vocals",
                    "is_current": False,
                    "tracker": tracker,
                },
                {"artist": None, "role": "Skipped", "is_current": True, "tracker": None},
            ],
        )

        self.assertEqual(result["props"]["labels"]["former"], "Former")
        self.assertEqual(
            result["props"]["relations"],
            [
                {
                    "id": 7,
                    "name": "Elva Run",
                    "image": "https://example.com/elva.jpg",
                    "href": reverse(
                        "music_artist_details",
                        kwargs={"artist_id": 7, "artist_slug": "elva-run"},
                    ),
                    "role": "Vocals",
                    "current": False,
                    "status": Status.IN_PROGRESS.value,
                    "score": 8.5,
                },
            ],
        )


class MusicRelationGridTemplateTests(SimpleTestCase):
    """The relation grid keeps its server fallback and mounts shared cards."""

    def test_grid_mounts_with_server_fallback(self):
        member = SimpleNamespace(id=7, name="Grid Member", image="")
        html = render_to_string(
            "app/components/artist_relation_grid.html",
            {
                "relations": [
                    {
                        "artist": member,
                        "role": "Drums",
                        "is_current": True,
                        "tracker": None,
                    },
                ],
            },
        )

        self.assertIn("data-music-relations-root", html)
        self.assertIn("Grid Member", html)
        self.assertIn("Drums", html)
        self.assertIn("Former", html)


class MusicAlbumsReactPropsTests(SimpleTestCase):
    """The shared discography grid receives plain, linkable album rows."""

    def test_album_rows_carry_release_year_plays_and_score(self):
        artist = SimpleNamespace(id=3, name="Mira Vale")
        album = SimpleNamespace(
            id=11,
            title="Glass Meridian",
            image="https://example.com/glass.jpg",
            artist=artist,
            release_type="Album",
            release_date=date(2025, 3, 14),
            play_count=64,
            score=9,
        )
        result = app_tags.music_albums_react({}, [album])

        self.assertEqual(
            result["props"]["albums"],
            [
                {
                    "id": 11,
                    "title": "Glass Meridian",
                    "image": "https://example.com/glass.jpg",
                    "href": reverse(
                        "music_album_details",
                        kwargs={
                            "artist_id": 3,
                            "artist_slug": "mira-vale",
                            "album_id": 11,
                            "album_slug": "glass-meridian",
                        },
                    ),
                    "releaseType": "Album",
                    "year": 2025,
                    "plays": 64,
                    "score": 9.0,
                    "lines": [
                        {
                            "clamp": True,
                            "dormant": False,
                            "parts": [{"text": "Mira Vale"}],
                        },
                        {
                            "clamp": True,
                            "dormant": False,
                            "parts": [{"text": "2025"}],
                        },
                    ],
                },
            ],
        )


class MusicAlbumGridTemplateTests(SimpleTestCase):
    """The discography grid keeps its server fallback and mounts shared cards."""

    def test_grid_mounts_with_server_fallback(self):
        artist = SimpleNamespace(id=3, name="Mira Vale")
        album = SimpleNamespace(
            id=11,
            title="Grid Album",
            image="",
            artist=artist,
            release_type="Album",
            release_date=None,
            play_count=0,
            score=None,
        )
        html = render_to_string(
            "app/components/album_grid.html",
            {"all_albums": [album]},
        )

        self.assertIn("data-music-albums-root", html)
        self.assertIn("Grid Album", html)
        self.assertIn('data-media-type="music"', html)
