"""Music pages render what is stored and queue their repairs.

``app.tests.test_read_pages_do_not_write`` checks the pages make no write and
no provider call. These tests check what the pages queue, that a reload does
not queue again, that the poller ends, and that the repair task does what the
page used to do inline.
"""

from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app import music_page_repair
from app.models import Album, AlbumArtist, Artist, Track
from app.templatetags.app_tags import (
    music_album_url,
    music_artist_url,
    music_track_url,
)

ARTIST_MBID = "11111111-2222-3333-4444-555555555555"


def _seed_artist_cache(artist):
    cache.set(
        f"musicbrainz_artist_{artist.musicbrainz_id}",
        {"type": "Group", "bio": "A stored biography.", "genres": []},
    )
    cache.set(f"music:artist-view-sync:{artist.id}", True)


class MusicPageRepairTests(TestCase):
    """Pages read stored rows; one task per artist or album does the repair."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="repairs")
        cls.artist = Artist.objects.create(
            name="Healthy Artist",
            musicbrainz_id=ARTIST_MBID,
            discography_synced_at=timezone.now(),
            members_synced_at=timezone.now(),
        )
        cls.album = Album.objects.create(
            title="Healthy Album",
            artist=cls.artist,
            musicbrainz_release_id="release-1",
            musicbrainz_release_group_id="group-1",
            tracks_populated=True,
            image="https://example.com/cover.jpg",
        )
        AlbumArtist.objects.create(album=cls.album, artist=cls.artist)
        cls.track = Track.objects.create(
            album=cls.album,
            title="Healthy Track",
            track_number=1,
            musicbrainz_recording_id="recording-1",
        )

    def setUp(self):
        cache.clear()
        self.client.force_login(self.user)

    def _healthy_cache(self):
        _seed_artist_cache(self.artist)
        cache.set(f"music:maintenance:album:{self.album.id}", True)
        cache.set("musicbrainz_recording_recording-1", {"details": {}})

    def test_a_healthy_artist_and_album_need_nothing(self):
        self._healthy_cache()

        self.assertEqual(music_page_repair.artist_repair_reasons(self.artist), [])
        self.assertEqual(
            music_page_repair.album_repair_reasons(self.album, self.user),
            [],
        )
        self.assertEqual(
            music_page_repair.track_recording_lookup(self.track)[1],
            False,
        )

    @patch("app.tasks_music.repair_music_track_page.delay")
    @patch("app.tasks_music.repair_music_album_page.delay")
    @patch("app.tasks_music.repair_music_artist_page.delay")
    def test_healthy_pages_queue_nothing_and_do_not_poll(
        self,
        artist_task,
        album_task,
        track_task,
    ):
        self._healthy_cache()

        for url in (
            music_artist_url(self.artist),
            music_album_url(self.album),
            music_track_url(self.track),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertIsNone(response.context["music_repair_poll"])
                self.assertNotContains(response, "music-repair-poll")
        artist_task.assert_not_called()
        album_task.assert_not_called()
        track_task.assert_not_called()

    @patch("app.tasks_music.repair_music_album_page.delay")
    @patch("app.providers.musicbrainz.get_release")
    def test_an_album_without_tracks_renders_and_queues_them_once(
        self,
        get_release,
        album_task,
    ):
        Album.objects.filter(pk=self.album.pk).update(tracks_populated=False)
        Track.objects.filter(album=self.album).delete()

        first = self.client.get(music_album_url(self.album))
        second = self.client.get(music_album_url(self.album))

        self.assertEqual(first.status_code, 200)
        self.assertContains(first, "Loading tracks from MusicBrainz.")
        self.assertContains(first, 'id="music-repair-poll"')
        self.assertTrue(first.context["tracks_pending"])
        # A reload while the repair runs keeps polling but queues nothing new.
        self.assertIsNotNone(second.context["music_repair_poll"])
        album_task.assert_called_once_with(self.album.id, self.user.id)
        get_release.assert_not_called()

    @patch("app.tasks_music.repair_music_artist_page.delay")
    @patch("app.providers.musicbrainz.get_artist")
    def test_an_artist_page_reads_metadata_from_the_cache_only(
        self,
        get_artist,
        artist_task,
    ):
        cold = self.client.get(music_artist_url(self.artist))
        self.assertEqual(cold.status_code, 200)
        self.assertEqual(cold.context["bio"], "")
        self.assertIsNotNone(cold.context["music_repair_poll"])
        artist_task.assert_called_once_with(self.artist.id)

        _seed_artist_cache(self.artist)
        music_page_repair.finish_repair("artist", self.artist.id)
        warm = self.client.get(music_artist_url(self.artist))
        self.assertEqual(warm.context["bio"], "A stored biography.")
        get_artist.assert_not_called()

    def test_the_poller_asks_again_reloads_when_done_and_stops_at_the_cap(self):
        url = reverse("music_repair_status", args=["album", self.album.id])
        cache.set(f"music:repair-pending:album:{self.album.id}", True)

        running = self.client.get(url, {"attempt": 3})
        capped = self.client.get(
            url,
            {"attempt": music_page_repair.REPAIR_POLL_MAX_ATTEMPTS},
        )
        music_page_repair.finish_repair("album", self.album.id)
        done = self.client.get(url, {"attempt": 4})

        self.assertContains(running, "attempt=4")
        self.assertEqual(capped.content, b"")
        self.assertEqual(done["HX-Refresh"], "true")
        self.assertEqual(
            self.client.get(reverse("music_repair_status", args=["x", 1])).status_code,
            404,
        )

    def test_a_merged_album_redirects_to_its_survivor(self):
        duplicate = Album.objects.create(title="HEALTHY ALBUM", artist=self.artist)
        duplicate_url = music_album_url(duplicate)
        duplicate_id = duplicate.id

        with (
            patch("app.services.music.sync_artist_discography", return_value=0),
            patch("app.providers.musicbrainz.get_release", side_effect=OSError),
            patch(
                "app.providers.musicbrainz.get_release_group_genres",
                return_value=[],
            ),
        ):
            music_page_repair.run_album_repair(duplicate_id)

        self.assertFalse(Album.objects.filter(id=duplicate_id).exists())
        response = self.client.get(duplicate_url)
        self.assertRedirects(
            response,
            music_album_url(self.album),
            fetch_redirect_response=False,
        )
        poll = self.client.get(
            reverse("music_repair_status", args=["album", duplicate_id]),
        )
        self.assertEqual(poll["HX-Redirect"], music_album_url(self.album))
        api = self.client.get(f"/api/v1/music/albums/{duplicate_id}/")
        self.assertEqual(api.json()["id"], self.album.id)

    @patch("app.services.music.sync_artist_members", return_value=0)
    @patch("app.services.music.sync_artist_discography", return_value=0)
    @patch("app.providers.musicbrainz.get_artist")
    def test_the_artist_task_stores_metadata_and_clears_the_marker(
        self,
        get_artist,
        _sync,
        members,
    ):
        get_artist.return_value = {"type": "Group", "country": "GB"}
        cache.set(f"music:repair-pending:artist:{self.artist.id}", True)

        from app.tasks_music import repair_music_artist_page

        repair_music_artist_page(self.artist.id)

        self.artist.refresh_from_db()
        self.assertEqual(self.artist.artist_type, "Group")
        self.assertEqual(self.artist.country, "GB")
        members.assert_called_once()
        self.assertFalse(music_page_repair.repair_pending("artist", self.artist.id))

    @patch("app.services.music.populate_album_tracks", return_value=2)
    @patch("app.providers.musicbrainz.get_release_group_genres", return_value=[])
    @patch("app.providers.musicbrainz.get_release", side_effect=OSError)
    def test_the_album_task_fills_tracks_and_clears_the_marker(
        self,
        _release,
        _genres,
        populate,
    ):
        Album.objects.filter(pk=self.album.pk).update(tracks_populated=False)
        cache.set(f"music:repair-pending:album:{self.album.id}", True)

        from app.tasks_music import repair_music_album_page

        summary = repair_music_album_page(self.album.id, self.user.id)

        self.assertEqual(summary["tracks"], 2)
        populate.assert_called_once()
        self.assertFalse(music_page_repair.repair_pending("album", self.album.id))
        self.assertIsNotNone(cache.get(f"music:maintenance:album:{self.album.id}"))

    @patch("app.tasks_music.repair_music_album_page.delay")
    def test_the_api_tracks_endpoint_queues_and_flags_pending(self, album_task):
        Album.objects.filter(pk=self.album.pk).update(tracks_populated=False)
        Track.objects.filter(album=self.album).delete()

        payload = self.client.get(f"/api/v1/music/albums/{self.album.id}/tracks/")

        self.assertEqual(payload.json(), {"results": [], "pending": True})
        album_task.assert_called_once_with(self.album.id, self.user.id)

    @patch("app.tasks_music.repair_music_artist_page.delay")
    def test_the_api_artist_endpoint_flags_a_pending_repair(self, artist_task):
        cold = self.client.get(f"/api/v1/music/artists/{self.artist.id}/").json()
        self.assertTrue(cold["repair_pending"])
        artist_task.assert_called_once_with(self.artist.id)

        self._healthy_cache()
        music_page_repair.finish_repair("artist", self.artist.id)
        warm = self.client.get(f"/api/v1/music/artists/{self.artist.id}/").json()
        self.assertFalse(warm["repair_pending"])

    @patch("app.tasks_music.repair_music_track_page.delay")
    @patch("app.providers.musicbrainz.recording")
    def test_an_unlistened_track_reads_its_recording_from_the_cache(
        self,
        recording,
        track_task,
    ):
        cold = self.client.get(music_track_url(self.track))
        self.assertEqual(cold.status_code, 200)
        self.assertIsNotNone(cold.context["music_repair_poll"])
        track_task.assert_called_once_with(self.track.id)

        cache.set(
            "musicbrainz_recording_recording-1",
            {"details": {"release_date": "1999-01-01"}, "genres": []},
        )
        music_page_repair.finish_repair("track", self.track.id)
        warm = self.client.get(music_track_url(self.track))
        self.assertEqual(warm.context["recording_release_date"], "1999-01-01")
        recording.assert_not_called()
