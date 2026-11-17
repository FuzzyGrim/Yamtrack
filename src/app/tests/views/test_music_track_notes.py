"""Private music track notes must not become listens."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.fork_services_music import record_song_play
from app.models import Album, Artist, Item, MediaTypes, Music, Sources, Status, Track
from app.templatetags.app_tags import music_album_url, music_track_url


class MusicTrackNotesTests(TestCase):
    """Exercise the notes editor, privacy, and listen accounting."""

    def setUp(self):
        """Create a local catalog track without provider dependencies."""
        self.user = get_user_model().objects.create_user(username="listener")
        self.other_user = get_user_model().objects.create_user(username="other")
        self.artist = Artist.objects.create(name="Artist")
        self.album = Album.objects.create(title="Album", artist=self.artist)
        self.track = Track.objects.create(title="Song", album=self.album)
        self.url = reverse("music_track_notes", args=[self.track.pk])
        self.client.force_login(self.user)

    def _music(self, user=None):
        item, _ = Item.objects.get_or_create(
            source=Sources.MUSICBRAINZ.value,
            media_type=MediaTypes.MUSIC.value,
            media_id=f"track_{self.track.pk}",
            defaults={"title": self.track.title},
        )
        return Music.objects.create(
            user=user or self.user,
            item=item,
            artist=self.artist,
            album=self.album,
            track=self.track,
            status=Status.COMPLETED.value,
            end_date=timezone.now(),
            notes="Original note",
        )

    def test_edit_and_clear_notes_preserve_listens_and_tracking(self):
        """A notes save changes neither tracking fields nor history rows."""
        music = self._music()
        original_end_date = music.end_date
        history_count = music.history.count()
        for notes in ("## Favourite song\n\nGreat bass.", ""):
            with self.subTest(notes=notes):
                response = self.client.post(
                    self.url,
                    {"notes": notes, "status": Status.DROPPED.value, "progress": "99"},
                )
                self.assertRedirects(response, music_track_url(self.track))
                music.refresh_from_db()
                self.assertEqual(music.notes, notes)
                self.assertEqual(music.status, Status.COMPLETED.value)
                self.assertEqual(music.end_date, original_end_date)
                self.assertEqual(music.progress, 0)
                self.assertEqual(music.history.count(), history_count)

    def test_unplayed_track_can_have_notes_without_a_listen(self):
        """Notes on a catalog track create a planned entry with no play history."""
        response = self.client.post(self.url, {"notes": "Try the live version"})
        self.assertEqual(response.status_code, 302)
        music = Music.objects.get(user=self.user, track=self.track)
        self.assertEqual(music.notes, "Try the live version")
        self.assertEqual(music.status, Status.PLANNING.value)
        self.assertIsNone(music.end_date)
        self.assertEqual(music.history.count(), 0)

        record_song_play(self.user, self.album, track=self.track)
        music.refresh_from_db()
        self.assertEqual(music.status, Status.COMPLETED.value)
        self.assertEqual(music.notes, "Try the live version")
        self.assertEqual(music.history.count(), 1)
        self.assertIsNotNone(music.end_date)

    def test_blank_note_on_untracked_song_does_not_create_tracking(self):
        """Opening or saving an empty editor does not add a library entry."""
        response = self.client.get(self.url)
        self.assertContains(response, 'name="notes"')
        self.client.post(self.url, {"notes": ""})
        self.assertFalse(Music.objects.filter(user=self.user).exists())
        self.assertFalse(
            Item.objects.filter(media_type=MediaTypes.MUSIC.value).exists()
        )

    @patch(
        "app.music_page_repair.track_recording_lookup",
        return_value=(None, False),
    )
    def test_notes_are_scoped_to_the_current_user(self, _lookup):
        """Shared catalog and Item rows must not expose another user's notes."""
        other_music = self._music(self.other_user)
        response = self.client.get(self.url, {"instance_id": other_music.pk})
        self.assertNotContains(response, "Original note")
        response = self.client.get(music_track_url(self.track))
        self.assertNotContains(response, "Original note")
        self.client.post(
            self.url, {"notes": "My own note", "instance_id": other_music.pk}
        )
        other_music.refresh_from_db()
        self.assertEqual(other_music.notes, "Original note")
        self.assertEqual(other_music.history.count(), 1)
        self.assertEqual(Music.objects.get(user=self.user).notes, "My own note")

    def test_detail_page_displays_notes_and_editor(self):
        """The track page uses the shared Markdown notes section."""
        self._music()
        response = self.client.get(music_track_url(self.track))
        self.assertContains(response, "Your Notes")
        self.assertContains(response, "Original note")
        self.assertContains(response, self.url)
        self.assertContains(response, "data-markdown-preview")
        self.assertContains(response, 'aria-label="Track notes"')

    @patch("app.services.music_scrobble.is_incomplete_album", return_value=False)
    @patch("app.services.music_scrobble.dedupe_artist_albums")
    def test_album_exposes_editor_for_unplayed_tracks(self, _dedupe, _incomplete):
        """The album row offers notes before a track has a Music entry."""
        self.album.tracks_populated = True
        self.album.save(update_fields=["tracks_populated"])
        response = self.client.get(music_album_url(self.album))
        self.assertContains(response, self.url)
        self.assertContains(response, 'aria-label="Track notes"')

    def test_notes_editor_requires_login(self):
        """Both reads and writes inherit the authenticated route policy."""
        self.client.logout()
        for method in (self.client.get, self.client.post):
            response = method(self.url, {"notes": "Private"})
            self.assertEqual(response.status_code, 302)
            self.assertIn("/accounts/login/", response.url)
        self.assertFalse(Music.objects.exists())

    def test_return_url_is_local(self):
        """Return to the calling page while rejecting external redirects."""
        response = self.client.post(
            self.url, {"notes": "Note", "return_url": "/music?q=song"}
        )
        self.assertEqual(response.url, "/music?q=song")
        response = self.client.post(
            self.url,
            {"notes": "Note", "return_url": "https://example.com/"},
        )
        self.assertEqual(response.url, music_track_url(self.track))

    def test_missing_track_and_unsupported_method(self):
        """The editor accepts only GET/POST for an existing catalog track."""
        response = self.client.get(
            reverse("music_track_notes", args=[self.track.pk + 1])
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.delete(self.url).status_code, 405)
