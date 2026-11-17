from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from app.models import (
    Album,
    Artist,
    MusicReleasePreference,
    Track,
)


class MusicReleaseViewTests(TestCase):
    """Coverage for the per-user MusicBrainz release picker."""

    def setUp(self):
        self.credentials = {"username": "music-user", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)
        self.artist = Artist.objects.create(name="Test Artist")
        self.album = Album.objects.create(
            title="Test Album",
            artist=self.artist,
            musicbrainz_release_id="representative-release",
            musicbrainz_release_group_id="release-group",
            tracks_populated=True,
            image="https://example.com/default.jpg",
        )

    def _release(self, release_id, title, **fields):
        return {
            "release_id": release_id,
            "title": title,
            "release_date": "1977",
            "country": "US",
            "status": "official",
            "packaging": "Jewel Case",
            "format": "CD",
            "formats": ["CD"],
            "label": "Example Records",
            "labels": ["Example Records"],
            "catalog_numbers": ["ABC-123"],
            "barcode": "0123456789012",
            "track_count": 9,
            "image": "https://example.com/release.jpg",
            **fields,
        }

    @patch("app.music_album_views.musicbrainz.get_release_group_releases")
    def test_list_music_releases_renders_and_filters_results(self, mock_releases):
        mock_releases.return_value = [
            self._release("release-1", "Original Pressing"),
            self._release(
                "release-2",
                "Deluxe Remaster",
                format="Vinyl",
                country="DE",
            ),
        ]

        response = self.client.get(
            reverse("list_music_releases", kwargs={"album_id": self.album.id}),
            {"q": "vinyl"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Deluxe Remaster")
        self.assertContains(response, "Vinyl")
        self.assertNotContains(response, "Original Pressing")
        self.assertContains(
            response,
            reverse("set_music_release", kwargs={"album_id": self.album.id}),
        )
        mock_releases.assert_called_once_with("release-group")

    @patch("app.music_album_views.musicbrainz.get_release_group_releases")
    def test_list_music_releases_serves_json_to_the_shared_picker(self, mock_releases):
        mock_releases.return_value = [self._release("release-1", "Original Pressing")]

        response = self.client.get(
            reverse("list_music_releases", kwargs={"album_id": self.album.id}),
            {"format": "json"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["pickerType"], "music")
        self.assertEqual(response.json()["results"][0]["release_id"], "release-1")

    @patch("app.providers.musicbrainz.get_release")
    def test_album_track_modal_places_release_picker_in_metadata_panel(
        self,
        mock_get_release,
    ):
        MusicReleasePreference.objects.create(
            user=self.user,
            album=self.album,
            release_id="release-1",
        )
        mock_get_release.return_value = self._release(
            "release-1",
            "Original Pressing",
            release_group_id="release-group",
        )

        response = self.client.get(
            reverse("album_track_modal", kwargs={"album_id": self.album.id}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["metadata_tab_available"])
        self.assertEqual(
            response.context["music_release_picker"]["list_url"],
            reverse("list_music_releases", kwargs={"album_id": self.album.id}),
        )
        self.assertEqual(
            response.context["selected_music_release"]["release_id"],
            "release-1",
        )
        self.assertContains(response, "Metadata")
        self.assertContains(response, "Specific release")
        self.assertContains(response, "Original Pressing")
        self.assertContains(response, "Search releases...")
        self.assertNotContains(response, "Search editions...")
        mock_get_release.assert_called_once_with("release-1")

    @patch("app.providers.musicbrainz.get_release_group_genres", return_value=[])
    @patch("app.providers.musicbrainz.get_release")
    @patch("app.music_album_views.musicbrainz.get_release_group_releases")
    def test_set_music_release_persists_preference(
        self,
        mock_releases,
        mock_get_release,
        mock_genres,
    ):
        mock_releases.return_value = [self._release("release-1", "Original Pressing")]
        mock_get_release.return_value = self._release(
            "release-1",
            "Original Pressing",
            release_group_id="release-group",
            tracks=[
                {
                    "disc_number": 1,
                    "track_number": track_number,
                    "title": f"Track {track_number}",
                    "recording_id": f"rec-{track_number}",
                    "duration_ms": 1000,
                }
                for track_number in range(1, 10)
            ],
        )

        response = self.client.post(
            reverse("set_music_release", kwargs={"album_id": self.album.id}),
            {"release_id": "release-1", "return_url": "/music"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/music")
        preference = MusicReleasePreference.objects.get(
            user=self.user,
            album=self.album,
        )
        self.assertEqual(preference.release_id, "release-1")

        self.album.refresh_from_db()
        self.assertEqual(self.album.musicbrainz_release_id, "release-1")
        self.assertEqual(Track.objects.filter(album=self.album).count(), 9)

    @patch("app.providers.musicbrainz.get_release", side_effect=RuntimeError("offline"))
    @patch("app.music_album_views.musicbrainz.get_release_group_releases")
    def test_set_music_release_keeps_preference_when_sync_fails(
        self,
        mock_releases,
        mock_get_release,
    ):
        mock_releases.return_value = [self._release("release-1", "Original Pressing")]

        response = self.client.post(
            reverse("set_music_release", kwargs={"album_id": self.album.id}),
            {"release_id": "release-1", "return_url": "/music"},
        )

        self.assertEqual(response.status_code, 302)
        preference = MusicReleasePreference.objects.get(
            user=self.user,
            album=self.album,
        )
        self.assertEqual(preference.release_id, "release-1")

        self.album.refresh_from_db()
        self.assertEqual(self.album.musicbrainz_release_id, "representative-release")

    @patch("app.music_album_views.musicbrainz.get_release_group_releases")
    def test_set_music_release_rejects_release_from_another_group(self, mock_releases):
        mock_releases.return_value = [self._release("release-1", "Original Pressing")]

        response = self.client.post(
            reverse("set_music_release", kwargs={"album_id": self.album.id}),
            {"release_id": "other-group-release", "return_url": "/music"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            MusicReleasePreference.objects.filter(
                user=self.user,
                album=self.album,
            ).exists(),
        )

    @patch("app.providers.musicbrainz.get_release_group_genres", return_value=[])
    @patch("app.providers.musicbrainz.get_release")
    @patch("app.music_album_views.musicbrainz.get_release_group_releases")
    def test_set_music_release_rejects_protocol_relative_return_url(
        self, mock_releases, mock_get_release, mock_genres
    ):
        mock_releases.return_value = [self._release("release-1", "Original Pressing")]
        mock_get_release.return_value = self._release(
            "release-1",
            "Original Pressing",
            release_group_id="release-group",
            tracks=[],
        )

        response = self.client.post(
            reverse("set_music_release", kwargs={"album_id": self.album.id}),
            {"release_id": "release-1", "return_url": "//evil.example/"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(response["Location"], "//evil.example/")
        self.assertIn("/details/music/artist/", response["Location"])

    def _album_url(self):
        return reverse(
            "music_album_details",
            kwargs={
                "artist_id": self.artist.id,
                "artist_slug": "test-artist",
                "album_id": self.album.id,
                "album_slug": "test-album",
            },
        )

    @patch("app.tasks_music.repair_music_album_page.delay")
    @patch("app.providers.musicbrainz.get_release")
    def test_album_detail_uses_saved_release_for_display_only(
        self,
        mock_get_release,
        _mock_repair,
    ):
        from app.providers.musicbrainz import RELEASE_CACHE_VERSION

        MusicReleasePreference.objects.create(
            user=self.user,
            album=self.album,
            release_id="selected-release",
        )
        # Choosing the release fetched it; the page reads that from the cache.
        cache.set(
            f"musicbrainz_release_v{RELEASE_CACHE_VERSION}_selected-release",
            self._release(
                "selected-release",
                "Original Pressing",
                release_group_id="release-group",
                image="https://example.com/selected.jpg",
            ),
        )

        response = self.client.get(self._album_url())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["album_display_image"],
            "https://example.com/selected.jpg",
        )
        self.assertEqual(
            response.context["selected_music_release"]["release_id"],
            "selected-release",
        )
        mock_get_release.assert_not_called()
        self.album.refresh_from_db()
        self.assertEqual(self.album.musicbrainz_release_id, "representative-release")
        self.assertEqual(self.album.image, "https://example.com/default.jpg")

    @patch("app.tasks_music.repair_music_album_page.delay")
    @patch("app.providers.musicbrainz.get_release")
    def test_album_detail_queues_a_saved_release_missing_from_the_cache(
        self,
        mock_get_release,
        mock_repair,
    ):
        cache.clear()
        MusicReleasePreference.objects.create(
            user=self.user,
            album=self.album,
            release_id="selected-release",
        )

        response = self.client.get(self._album_url())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["album_display_image"],
            "https://example.com/default.jpg",
        )
        self.assertIsNone(response.context["selected_music_release"])
        mock_get_release.assert_not_called()
        mock_repair.assert_called_once_with(self.album.id, self.user.id)
        self.assertIsNotNone(response.context["music_repair_poll"])


class MusicAlbumSyncViewTests(TestCase):
    """Coverage for resync re-deriving a previously wrong release pick."""

    def setUp(self):
        self.credentials = {"username": "sync-user", "password": "12345"}
        get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)
        self.artist = Artist.objects.create(name="Test Artist")
        self.album = Album.objects.create(
            title="Test Album",
            artist=self.artist,
            musicbrainz_release_id="vinyl-reissue",
            musicbrainz_release_group_id="release-group",
            tracks_populated=True,
            image="https://example.com/default.jpg",
        )
        Track.objects.create(
            album=self.album,
            disc_number=1,
            track_number=1,
            title="Stale Track From Wrong Release",
        )
        cache.set("musicbrainz_release_for_group_release-group", "vinyl-reissue")

    @patch("app.music_album_views.musicbrainz.get_release")
    @patch("app.music_album_views.ensure_album_has_release_id")
    def test_resync_clears_stale_release_group_cache_and_rebuilds_tracks(
        self,
        mock_ensure_release_id,
        mock_get_release,
    ):
        def fake_ensure_release_id(album):
            # The view must clear the previous release_id before calling
            # this, otherwise a bad pick can never be re-derived.
            self.assertFalse(album.musicbrainz_release_id)
            album.musicbrainz_release_id = "digital-original"
            album.save(update_fields=["musicbrainz_release_id"])
            return True

        mock_ensure_release_id.side_effect = fake_ensure_release_id
        mock_get_release.return_value = {
            "release_group_id": "release-group",
            "genres": [],
            "tracks": [
                {
                    "disc_number": 1,
                    "track_number": track_number,
                    "title": f"Track {track_number}",
                    "recording_id": f"rec-{track_number}",
                    "duration_ms": 1000,
                }
                for track_number in range(1, 25)
            ],
        }

        response = self.client.post(
            reverse("sync_album_metadata", kwargs={"album_id": self.album.id}),
        )

        self.assertEqual(response.status_code, 204)
        self.album.refresh_from_db()
        self.assertEqual(self.album.musicbrainz_release_id, "digital-original")
        self.assertIsNone(
            cache.get("musicbrainz_release_for_group_release-group"),
        )
        tracks = Track.objects.filter(album=self.album)
        self.assertEqual(tracks.count(), 24)
        self.assertFalse(
            tracks.filter(title="Stale Track From Wrong Release").exists(),
        )


class PlayLinkLabelTests(TestCase):
    """The play-link label comes from the URL host, not from text anywhere in it."""

    def test_real_hosts_are_labelled(self):
        from app.music_views import _play_link_label

        self.assertEqual(
            _play_link_label("https://soundcloud.com/a/b"),
            "SoundCloud",
        )
        self.assertEqual(
            _play_link_label("https://open.spotify.com/track/1"),
            "Spotify",
        )

    def test_lookalike_urls_get_no_label(self):
        from app.music_views import _play_link_label

        self.assertEqual(_play_link_label("https://evil.example/?q=soundcloud.com"), "")
        self.assertEqual(_play_link_label("https://notspotify.com/track/1"), "")

    def test_same_title_tracks_keep_their_own_play_links(self):
        from types import SimpleNamespace

        from app.music_views import _external_play_links

        urls = [f"https://soundcloud.com/artist/{n}" for n in ("one", "two", "three")]
        tracks = [
            {"track": SimpleNamespace(title="Same Title"), "origin_url": url}
            for url in urls
        ]
        tracks.append(tracks[0])  # the same URL again adds no chip

        links = _external_play_links(tracks)

        self.assertEqual(sorted(links.values()), sorted(urls))
