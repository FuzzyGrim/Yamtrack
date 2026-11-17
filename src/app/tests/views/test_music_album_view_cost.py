"""The album page and its native tracklist cost the same however many tracks were played."""

from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from app.models import Item, MediaTypes, Music, Sources, Status, Track
from app.music_views import ALBUM_TRACK_PAGE_SIZE
from app.tests.music_album_fixtures import build_long_album


class AlbumPageQueryCostTests(TestCase):
    """Per-track play history is read in one grouped query, not one per track."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="albumcost")
        cls.user.music_enabled = True
        cls.user.save()
        cls.small_artist, cls.small = build_long_album(
            cls.user,
            tracks=3,
            plays_per_track=3,
            title="Small",
        )
        cls.long_artist, cls.long = build_long_album(
            cls.user,
            tracks=150,
            plays_per_track=3,
            title="Long",
        )

    def setUp(self):
        cache.clear()
        self.client.force_login(self.user)

    def _page(self, artist, album):
        return self.client.get(
            reverse(
                "music_album_details",
                kwargs={
                    "artist_id": artist.id,
                    "artist_slug": "artist",
                    "album_id": album.id,
                    "album_slug": "album",
                },
            ),
        )

    def _queries(self, url_or_args):
        # Warm once so per-process caches do not count against either album.
        self._fetch(url_or_args)
        cache.clear()
        with CaptureQueriesContext(connection) as ctx:
            response = self._fetch(url_or_args)
        self.assertEqual(response.status_code, 200)
        return len(ctx.captured_queries)

    def _fetch(self, url_or_args):
        if isinstance(url_or_args, str):
            return self.client.get(url_or_args)
        return self._page(*url_or_args)

    def test_web_album_queries_do_not_grow_with_played_tracks(self):
        small = self._queries((self.small_artist, self.small))
        long = self._queries((self.long_artist, self.long))

        self.assertEqual(long, small)

    def test_native_tracklist_queries_do_not_grow_with_played_tracks(self):
        small = self._queries(f"/api/v1/music/albums/{self.small.id}/tracks/")
        long = self._queries(f"/api/v1/music/albums/{self.long.id}/tracks/")

        self.assertEqual(long, small)

    def test_native_tracklist_reports_each_tracks_play_count(self):
        response = self.client.get(f"/api/v1/music/albums/{self.small.id}/tracks/")

        counts = {row["title"]: row["play_count"] for row in response.json()["results"]}
        self.assertEqual(
            counts,
            {f"Small Track {index:04d}": 3 for index in (1, 2, 3)},
        )

    def test_track_rows_show_count_and_latest_play(self):
        response = self._page(self.long_artist, self.long)

        first = response.context["tracks"][0]
        self.assertEqual(first["play_count"], 3)
        # Plays are one day apart from 2026-01-01 12:00, so the latest is the 3rd.
        self.assertEqual(first["last_played"], datetime(2026, 1, 3, 12, 0, tzinfo=UTC))
        self.assertContains(response, "Listened 3 times")

    def test_long_album_renders_a_first_page_and_totals_cover_every_track(self):
        response = self._page(self.long_artist, self.long)

        self.assertEqual(len(response.context["tracks"]), ALBUM_TRACK_PAGE_SIZE)
        self.assertEqual(response.context["tracks_next_offset"], ALBUM_TRACK_PAGE_SIZE)
        self.assertEqual(response.context["total_tracks"], 150)
        self.assertEqual(response.context["library_track_count"], 150)
        rows_url = reverse("music_album_track_rows", args=[self.long.id])
        self.assertContains(response, f'hx-get="{rows_url}?offset=50"')
        # 150 tracks of 180,000 ms + index ms: 7h 30m.
        self.assertEqual(response.context["total_runtime"], "7h 30m")

    def test_scrolling_loads_every_remaining_row_in_disc_order(self):
        rows_url = reverse("music_album_track_rows", args=[self.long.id])
        seen = [
            row["track"]
            for row in self._page(self.long_artist, self.long).context["tracks"]
        ]
        offset = ALBUM_TRACK_PAGE_SIZE
        while offset is not None:
            response = self.client.get(f"{rows_url}?offset={offset}")
            self.assertEqual(response.status_code, 200)
            seen.extend(row["track"] for row in response.context["tracks"])
            offset = response.context["tracks_next_offset"]

        self.assertEqual(len(seen), 150)
        self.assertEqual(len({track.id for track in seen}), 150)
        # Disc 1 (tracks 1-100) before disc 2, numbered within each disc.
        self.assertEqual(
            [(track.disc_number, track.track_number) for track in seen[99:101]],
            [(1, 100), (2, 1)],
        )
        # The last page has no further trigger.
        self.assertNotContains(response, "album-tracks-load-more-trigger")
        self.assertContains(response, "Listened 3 times")

    def test_scrolled_in_rows_return_to_the_album_page(self):
        rows_url = reverse("music_album_track_rows", args=[self.long.id])
        response = self.client.get(f"{rows_url}?offset={ALBUM_TRACK_PAGE_SIZE}")

        html = response.content.decode()
        # Lists, history, notes and the track form send people back to the
        # album, never to this fragment; only the next-page trigger names it.
        self.assertEqual(html.count("track-rows/"), 1)
        self.assertIn(f'hx-get="{rows_url}?offset=100"', html)
        album_url = response.context["return_url"]
        self.assertIn(f"/album/{self.long.id}/", album_url)
        self.assertIn(f"next={album_url}", html)

    def test_track_rows_page_queries_do_not_grow_with_played_tracks(self):
        small = self._queries(
            reverse("music_album_track_rows", args=[self.small.id]) + "?offset=0",
        )
        long = self._queries(
            reverse("music_album_track_rows", args=[self.long.id]) + "?offset=50",
        )

        self.assertEqual(long, small)


class AlbumPageTrackRowTests(TestCase):
    """Row text for unplayed, once-played and recording-matched tracks."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="albumrows")
        cls.user.music_enabled = True
        cls.user.save()
        cls.artist, cls.album = build_long_album(cls.user, tracks=3, title="Rows")
        cls.tracks = list(Track.objects.filter(album=cls.album).order_by("id"))
        played_at = datetime(2026, 3, 4, 20, 0, tzinfo=UTC)
        # Track 1: one play linked by track id.
        Music.objects.create(
            user=cls.user,
            item=Item.objects.create(
                media_id="rows-direct",
                source=Sources.MUSICBRAINZ.value,
                media_type=MediaTypes.MUSIC.value,
                title="Direct",
            ),
            artist=cls.artist,
            album=cls.album,
            track=cls.tracks[0],
            status=Status.COMPLETED.value,
            start_date=played_at,
            end_date=played_at,
        )
        # Track 2: a scrobble with no track link, matched by recording id.
        cls.recording_entry = Music.objects.create(
            user=cls.user,
            item=Item.objects.create(
                media_id=cls.tracks[1].musicbrainz_recording_id,
                source=Sources.MUSICBRAINZ.value,
                media_type=MediaTypes.MUSIC.value,
                title="Recording",
            ),
            artist=cls.artist,
            album=cls.album,
            status=Status.COMPLETED.value,
            start_date=played_at,
            end_date=played_at,
        )
        cls.recording_entry.save()  # a second history row: played twice

    def setUp(self):
        cache.clear()
        self.client.force_login(self.user)

    def test_rows_keep_their_history_lines(self):
        response = self.client.get(
            reverse(
                "music_album_details",
                kwargs={
                    "artist_id": self.artist.id,
                    "artist_slug": "artist",
                    "album_id": self.album.id,
                    "album_slug": "album",
                },
            ),
        )

        rows = {row["track"].id: row for row in response.context["tracks"]}
        direct, recording, unplayed = (rows[track.id] for track in self.tracks)
        self.assertEqual((direct["play_count"], direct["music"] is not None), (1, True))
        self.assertEqual(recording["music"], self.recording_entry)
        self.assertEqual(recording["play_count"], 2)
        self.assertEqual((unplayed["play_count"], unplayed["last_played"]), (0, None))
        # Once on the row, once in its Track Song modal.
        self.assertContains(response, "Listened 2 times", count=2)
        self.assertContains(response, "Last listened:", count=2)
        self.assertContains(
            response,
            f'id="track-history-{self.tracks[2].id}"',
        )
        # Rows reuse the album already loaded rather than joining it per track.
        self.assertIs(direct["track"].album, response.context["album"])

    def test_a_slice_matches_entries_like_the_full_album(self):
        from app.music_views import album_track_rows

        full, _entries = album_track_rows(self.user, self.album)
        for index in range(3):
            page, _entries = album_track_rows(
                self.user,
                self.album,
                offset=index,
                limit=1,
            )
            self.assertEqual(
                [(row["track"].id, row["music"], row["play_count"]) for row in page],
                [
                    (
                        full[index]["track"].id,
                        full[index]["music"],
                        full[index]["play_count"],
                    ),
                ],
            )
