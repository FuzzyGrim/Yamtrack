"""Genres on music and podcast detail pages, and the scrobble-time fill."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import (
    Album,
    Artist,
    Item,
    MediaTypes,
    PodcastShow,
    Sources,
    Track,
)
from app.services import music as music_service
from app.services.music_scrobble import MusicPlaybackEvent, record_music_playback


class StoreMatchedGenresTests(TestCase):
    """The first non-empty genre list is copied onto rows that have none."""

    def setUp(self):
        self.artist = Artist.objects.create(name="Genre Artist")
        self.album = Album.objects.create(title="Genre Album", artist=self.artist)
        self.track = Track.objects.create(album=self.album, title="Genre Track")
        self.item = Item.objects.create(
            media_id="genre-track",
            source=Sources.MUSICBRAINZ.value,
            media_type=MediaTypes.MUSIC.value,
            title="Genre Track",
        )

    def _store(self):
        return music_service.store_matched_genres(
            artist=self.artist,
            album=self.album,
            track=self.track,
            item=self.item,
        )

    def test_album_genres_fill_empty_rows(self):
        self.album.genres = ["Rock"]
        self.album.save()

        self.assertEqual(self._store(), ["Rock"])

        for row in (self.artist, self.track, self.item):
            row.refresh_from_db()
            self.assertEqual(row.genres, ["Rock"])

    def test_artist_genres_fill_when_nothing_else_has_any(self):
        self.artist.genres = ["Jazz"]
        self.artist.save()

        self.assertEqual(self._store(), ["Jazz"])

        for row in (self.album, self.track, self.item):
            row.refresh_from_db()
            self.assertEqual(row.genres, ["Jazz"])

    def test_row_with_genres_is_left_alone(self):
        self.album.genres = ["Rock"]
        self.album.save()
        self.track.genres = ["Folk"]
        self.track.save()

        self._store()

        self.track.refresh_from_db()
        self.assertEqual(self.track.genres, ["Folk"])
        self.item.refresh_from_db()
        self.assertEqual(self.item.genres, ["Rock"])

    def test_nothing_to_copy_changes_nothing(self):
        self.assertEqual(self._store(), [])

        self.album.refresh_from_db()
        self.assertEqual(self.album.genres, [])

    def test_album_without_genres_reads_artist_genres(self):
        self.artist.genres = ["Jazz", "Blues"]
        self.artist.save()

        self.assertEqual(
            music_service._music_item_direct_genres(self.album),
            ["Jazz", "Blues"],
        )

        self.album.genres = ["Rock"]
        self.assertEqual(
            music_service._music_item_direct_genres(self.album),
            ["Rock"],
        )


class GenreChipRenderingTests(TestCase):
    """Artist, album, and podcast pages show their stored genres."""

    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="genre-viewer")
        self.user.music_enabled = True
        self.user.save()
        self.client.force_login(self.user)

    def _chip(self, name):
        return f">{name}</span>"

    @patch("app.services.music.resolve_artist_mbid", return_value=(None, 0, ""))
    def test_artist_page_shows_stored_genres(self, _resolve):
        artist = Artist.objects.create(name="Chip Artist", genres=["Shoegaze"])

        response = self.client.get(
            reverse("music_artist_details", args=[artist.id, "chip-artist"]),
        )

        self.assertContains(response, self._chip("Shoegaze"))

    def test_album_page_shows_album_genres_else_artist_genres(self):
        artist = Artist.objects.create(name="Fallback Artist", genres=["Dub"])
        own = Album.objects.create(title="Own", artist=artist, genres=["Reggae"])
        bare = Album.objects.create(title="Bare", artist=artist)

        own_page = self.client.get(
            reverse(
                "music_album_details",
                args=[artist.id, "fallback-artist", own.id, "own"],
            ),
        )
        bare_page = self.client.get(
            reverse(
                "music_album_details",
                args=[artist.id, "fallback-artist", bare.id, "bare"],
            ),
        )

        self.assertContains(own_page, self._chip("Reggae"))
        self.assertNotContains(own_page, self._chip("Dub"))
        self.assertContains(bare_page, self._chip("Dub"))

    def test_podcast_show_page_shows_genres(self):
        show = PodcastShow.objects.create(
            podcast_uuid="genre-show",
            title="Genre Show",
            genres=["Comedy"],
        )

        response = self.client.get(
            reverse("podcast_show_detail", args=[show.id]),
            follow=True,
        )

        # The show page links each genre chip to the filtered podcast list.
        self.assertContains(response, 'data-tone="genre">Comedy</a>')


class ScrobbleGenreFillTests(TestCase):
    """A completed scrobble leaves genres on the rows it touched."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="genre-scrobbler")
        self.user.music_enabled = True
        self.user.save()
        for target in ("search", "search_artists"):
            patcher = patch(
                f"app.services.music_scrobble.musicbrainz.{target}",
                return_value={"results": [], "total_results": 0},
            )
            patcher.start()
            self.addCleanup(patcher.stop)

    def _scrobble(self):
        return record_music_playback(
            MusicPlaybackEvent(
                user=self.user,
                artist_name="Scrobble Artist",
                album_title="Scrobble Album",
                track_title="Scrobble Track",
                duration_ms=180000,
                plex_rating_key="genre-scrobble",
                external_ids={},
                completed=True,
                played_at=timezone.now(),
            ),
        )

    def test_artist_genres_fall_back_onto_album_item_and_track(self):
        artist = Artist.objects.create(name="Scrobble Artist", genres=["Ambient"])
        Album.objects.create(title="Scrobble Album", artist=artist)

        music = self._scrobble()

        music.album.refresh_from_db()
        music.track.refresh_from_db()
        music.item.refresh_from_db()
        self.assertEqual(music.album.genres, ["Ambient"])
        self.assertEqual(music.track.genres, ["Ambient"])
        self.assertEqual(music.item.genres, ["Ambient"])

    def test_empty_album_is_filled_from_its_release_group(self):
        artist = Artist.objects.create(name="Scrobble Artist", genres=["Ambient"])
        Album.objects.create(
            title="Scrobble Album",
            artist=artist,
            musicbrainz_release_group_id="release-group-1",
        )

        with (
            patch(
                "app.providers.musicbrainz.get_release_group_genres",
                return_value=["Drone"],
            ) as mock_genres,
            patch("app.providers.musicbrainz.get_genre_parents", return_value=[]),
        ):
            music = self._scrobble()

        mock_genres.assert_called_with("release-group-1")
        music.album.refresh_from_db()
        music.item.refresh_from_db()
        # The release group's list wins over the artist fallback.
        self.assertEqual(music.album.genres, ["Drone"])
        self.assertEqual(music.item.genres, ["Drone"])
        # The artist keeps its own list; only empty rows are filled.
        artist.refresh_from_db()
        self.assertEqual(artist.genres, ["Ambient"])

    def test_release_group_without_genres_keeps_the_artist_fallback(self):
        artist = Artist.objects.create(name="Scrobble Artist", genres=["Ambient"])
        Album.objects.create(
            title="Scrobble Album",
            artist=artist,
            musicbrainz_release_group_id="release-group-2",
        )

        with patch(
            "app.providers.musicbrainz.get_release_group_genres",
            return_value=[],
        ):
            music = self._scrobble()

        music.album.refresh_from_db()
        self.assertEqual(music.album.genres, ["Ambient"])
