from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import (
    TV,
    Book,
    Episode,
    Item,
    MediaTypes,
    Movie,
    MoviePlay,
    Music,
    Podcast,
    Season,
    Sources,
    Status,
    Video,
    VideoPlay,
)


class HistoryCalendarFeedTests(TestCase):
    """Tests for the watch history calendar link."""

    def setUp(self):
        """Set up a user and the helpers the tests build plays with."""
        # The rendered feed is cached per user; rolled-back test users reuse ids.
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="historyuser",
            password="testpassword",
        )
        self.other_user = get_user_model().objects.create_user(
            username="otheruser",
            password="testpassword",
        )
        self.when = timezone.now().replace(microsecond=0) - timedelta(days=3, hours=5)

    def _item(self, media_type, title, media_id=None, **extra):
        return Item.objects.create(
            media_id=media_id or title,
            source=Sources.MANUAL.value,
            media_type=media_type,
            title=title,
            image="https://example.com/cover.jpg",
            **extra,
        )

    def _movie(self, title, end_date, user=None, status=Status.COMPLETED.value):
        return Movie.objects.create(
            item=self._item(MediaTypes.MOVIE.value, title),
            user=user or self.user,
            status=status,
            end_date=end_date,
        )

    def _book(self, title, end_date, status=Status.COMPLETED.value):
        return Book.objects.create(
            item=self._item(MediaTypes.BOOK.value, title),
            user=self.user,
            status=status,
            end_date=end_date,
        )

    def _feed(self, token=None, **params):
        response = self.client.get(
            reverse(
                "download_history_calendar",
                kwargs={"token": token or self.user.token},
            ),
            params,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/calendar")
        return response.content.decode()

    def test_invalid_token_returns_401(self):
        """Unknown links are rejected."""
        response = self.client.get(
            reverse("download_history_calendar", kwargs={"token": "missing"}),
        )

        self.assertEqual(response.status_code, 401)

    def test_empty_history_is_a_valid_calendar(self):
        """A user with no plays still gets a calendar a client can subscribe to."""
        body = self._feed()

        self.assertIn("BEGIN:VCALENDAR", body)
        self.assertIn("END:VCALENDAR", body)
        self.assertNotIn("BEGIN:VEVENT", body)

    def test_movie_play_is_a_timed_entry(self):
        """A play with a time is filed at that moment, named for what happened."""
        self._movie("Dune", self.when)

        body = self._feed()

        self.assertIn("SUMMARY:Watched: Dune", body)
        stamp = self.when.strftime("%Y%m%dT%H%M%SZ")
        self.assertIn(f"DTSTART:{stamp}", body)

    def test_date_only_play_is_an_all_day_entry(self):
        """A play stored at local midnight has no known time, so it is all-day."""
        day = (timezone.localdate() - timedelta(days=4))
        midnight = timezone.make_aware(datetime(day.year, day.month, day.day))
        self._movie("Heat", midnight)

        body = self._feed()

        self.assertIn(f"DTSTART;VALUE=DATE:{day:%Y%m%d}", body)
        next_day = day + timedelta(days=1)
        self.assertIn(f"DTEND;VALUE=DATE:{next_day:%Y%m%d}", body)

    def test_movie_watched_twice_has_two_entries(self):
        """Each logged play is its own entry, with its own stable id."""
        movie = self._movie("Aliens", self.when)
        MoviePlay.objects.filter(movie=movie).delete()
        MoviePlay.objects.create(movie=movie, end_date=self.when)
        MoviePlay.objects.create(movie=movie, end_date=self.when - timedelta(days=9))

        body = self._feed()

        self.assertEqual(body.count("SUMMARY:Watched: Aliens"), 2)
        uids = [line for line in body.splitlines() if line.startswith("UID:")]
        self.assertEqual(len(set(uids)), 2)

    def test_movie_without_play_rows_uses_its_end_date(self):
        """A finished movie with no play rows still shows once."""
        movie = self._movie("Past Lives", self.when)
        MoviePlay.objects.filter(movie=movie).delete()

        body = self._feed()

        self.assertEqual(body.count("SUMMARY:Watched: Past Lives"), 1)

    def test_episode_play_names_show_season_and_episode(self):
        """TV plays are episodes, titled with the show and its numbers."""
        tv = TV.objects.create(
            item=self._item(MediaTypes.TV.value, "Severance", media_id="sev"),
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        season = Season.objects.create(
            item=self._item(
                MediaTypes.SEASON.value,
                "Severance",
                media_id="sev",
                season_number=2,
            ),
            related_tv=tv,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        Episode.objects.create(
            item=self._item(
                MediaTypes.EPISODE.value,
                "Severance",
                media_id="sev",
                season_number=2,
                episode_number=5,
            ),
            related_season=season,
            end_date=self.when,
        )

        body = self._feed()

        self.assertIn("SUMMARY:Watched: Severance S02E05", body)

    def test_reading_uses_its_own_verb(self):
        """Books are read, not watched."""
        self._book("Dune", self.when)

        self.assertIn("SUMMARY:Read: Dune", self._feed())

    def test_unfinished_entries_are_left_out(self):
        """Only completed plays are listed."""
        self._book("Planned Book", self.when, status=Status.PLANNING.value)
        self._book("Half Book", self.when, status=Status.IN_PROGRESS.value)
        self._book("Dropped Book", self.when, status=Status.DROPPED.value)
        self._book("Done Book", self.when)

        body = self._feed()

        self.assertIn("Read: Done Book", body)
        self.assertNotIn("Planned Book", body)
        self.assertNotIn("Half Book", body)
        self.assertNotIn("Dropped Book", body)

    def test_entry_without_a_date_is_left_out(self):
        """A completed entry with no date has no day to put on a calendar."""
        self._book("Undated Book", None)

        self.assertNotIn("Undated Book", self._feed())

    def test_plays_older_than_a_year_are_left_out(self):
        """The link reaches back a year so each poll stays small."""
        self._book("Old Book", timezone.now() - timedelta(days=400))
        self._book("Recent Book", timezone.now() - timedelta(days=360))

        body = self._feed()

        self.assertNotIn("Old Book", body)
        self.assertIn("Recent Book", body)

    def test_other_users_plays_are_not_included(self):
        """A link only ever shows its owner's plays."""
        self._movie("Mine", self.when)
        self._movie("Theirs", self.when, user=self.other_user)

        body = self._feed()

        self.assertIn("Watched: Mine", body)
        self.assertNotIn("Theirs", body)

    def test_music_starts_off_but_can_be_added(self):
        """Bulk media types stay out until the link asks for them."""
        Music.objects.create(
            item=self._item(MediaTypes.MUSIC.value, "Kind of Blue"),
            user=self.user,
            status=Status.COMPLETED.value,
            end_date=self.when,
        )
        self._book("A Book", self.when)

        default_body = self._feed()
        music_body = self._feed(media_types=["music"])

        self.assertNotIn("Kind of Blue", default_body)
        self.assertIn("Listened to: Kind of Blue", music_body)
        self.assertNotIn("A Book", music_body)

    def test_unfinished_movie_without_plays_is_left_out(self):
        """The no-play-rows fallback also needs a completed movie."""
        for status in (
            Status.DROPPED.value,
            Status.IN_PROGRESS.value,
            Status.PLANNING.value,
        ):
            movie = self._movie(f"Unfinished {status}", self.when, status=status)
            MoviePlay.objects.filter(movie=movie).delete()

        self.assertNotIn("Unfinished", self._feed())

    def test_song_played_again_keeps_every_listen(self):
        """Each listen is its own entry, and saving for another reason adds none."""
        music = Music.objects.create(
            item=self._item(MediaTypes.MUSIC.value, "So What"),
            user=self.user,
            status=Status.COMPLETED.value,
            end_date=self.when - timedelta(days=2),
        )
        music.end_date = self.when
        music.save()
        music.score = 9
        music.save()

        body = self._feed(media_types=["music"])

        self.assertEqual(body.count("SUMMARY:Listened to: So What"), 2)
        uids = [line for line in body.splitlines() if line.startswith("UID:")]
        self.assertEqual(len(set(uids)), 2)

    def test_podcast_listens_are_included_when_asked_for(self):
        """Podcasts read their listens the same way."""
        Podcast.objects.create(
            item=self._item(MediaTypes.PODCAST.value, "Hardcore History"),
            user=self.user,
            status=Status.COMPLETED.value,
            end_date=self.when,
        )

        self.assertNotIn("Hardcore History", self._feed())
        self.assertIn(
            "Listened to: Hardcore History",
            self._feed(media_types=["podcast"]),
        )

    def test_video_watched_on_two_days_has_two_entries(self):
        """Each viewing of a video is its own entry."""
        video = Video.objects.create(
            item=self._item(MediaTypes.VIDEO.value, "Lecture"),
            user=self.user,
            status=Status.COMPLETED.value,
            end_date=self.when,
        )
        VideoPlay.objects.filter(video=video).delete()
        VideoPlay.objects.create(video=video, external_id="a", end_date=self.when)
        VideoPlay.objects.create(
            video=video,
            external_id="b",
            end_date=self.when - timedelta(days=5),
        )

        body = self._feed(media_types=["video"])

        self.assertEqual(body.count("SUMMARY:Watched: Lecture"), 2)

    def test_video_without_plays_needs_to_be_completed(self):
        """A video with no viewing rows falls back to its own date if finished."""
        for title, status in (
            ("Done Video", Status.COMPLETED.value),
            ("Half Video", Status.IN_PROGRESS.value),
        ):
            video = Video.objects.create(
                item=self._item(MediaTypes.VIDEO.value, title),
                user=self.user,
                status=status,
                end_date=self.when,
            )
            VideoPlay.objects.filter(video=video).delete()

        body = self._feed(media_types=["video"])

        self.assertIn("Watched: Done Video", body)
        self.assertNotIn("Half Video", body)

    def test_media_types_narrow_the_feed(self):
        """Choosing a type leaves the others out."""
        self._movie("Only Movie", self.when)
        self._book("Only Book", self.when)

        body = self._feed(media_types=["movie"])

        self.assertIn("Only Movie", body)
        self.assertNotIn("Only Book", body)

    def test_every_switch_off_gives_an_empty_calendar(self):
        """A link with nothing selected is empty, not the defaults."""
        self._movie("Dune", self.when)

        body = self._feed(media_types=["none"])

        self.assertNotIn("BEGIN:VEVENT", body)

    def test_unknown_media_types_fall_back_to_defaults(self):
        """A mistyped link still shows the usual plays."""
        self._movie("Dune", self.when)

        self.assertIn("Watched: Dune", self._feed(media_types=["nonsense"]))

    def test_titles_are_escaped_and_folded(self):
        """Commas and semicolons are escaped and long lines wrap at 75 bytes."""
        self._movie("Dr. Strangelove; or, " + "How I Learned " * 8, self.when)

        body = self._feed()

        self.assertIn("Dr. Strangelove\\; or\\, ", body)
        for line in body.split("\r\n"):
            self.assertLessEqual(len(line.encode()), 75)

    def test_feed_is_cached_per_selection(self):
        """A repeat poll reuses the feed until a play is added, then shows it."""
        self._movie("Dune", self.when)
        first = self._feed()

        self.assertEqual(self._feed(), first)
        self._book("Added Later", self.when)
        self.assertIn("Added Later", self._feed())
        self.assertIn("Added Later", self._feed(media_types=["book"]))


class CalendarPageHistoryLinkTests(TestCase):
    """The Calendar page offers the watch history link next to releases."""

    def setUp(self):
        """Log in a user."""
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="pageuser",
            password="testpassword",
        )
        self.client.force_login(self.user)

    def test_calendar_page_lists_both_links(self):
        """Both links are built from the user's private token."""
        response = self.client.get(reverse("calendar"))

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Calendar links", body)
        self.assertIn("Upcoming releases", body)
        self.assertIn("Watch history", body)
        self.assertIn(
            reverse("download_calendar", kwargs={"token": self.user.token}),
            body,
        )
        self.assertIn(
            reverse("download_history_calendar", kwargs={"token": self.user.token}),
            body,
        )

    def test_history_switches_start_with_the_defaults(self):
        """Music, podcasts and videos start off in the history link."""
        response = self.client.get(reverse("calendar"))

        self.assertEqual(response.context["history_default_media_types"][0], "tv")
        self.assertNotIn("music", response.context["history_default_media_types"])
        self.assertIn("music", response.context["history_media_types"])
