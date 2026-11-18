from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.models import (
    Item,
    MediaTypes,
    Movie,
    Sources,
    Status,
)


class TrackModalViewTests(TestCase):
    """Test the track modal view."""

    def setUp(self):
        """Create a user and log in."""
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

    def test_track_modal_view_existing_media(self):
        """Test the track modal view for existing media."""
        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                },
            )
            + "?return_url=/home",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/fill_track.html")

        self.assertIn("form", response.context)
        self.assertIn("media", response.context)
        self.assertEqual(response.context["media"], self.movie)
        self.assertEqual(response.context["return_url"], "/home")

    def test_track_modal_uses_default_edit_entry_date_for_new_entry(self):
        """The configured preference leaves new entry end date empty."""
        self.user.default_edit_entry_date = "no_date"
        self.user.save()

        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "278",
                },
            )
            + "?return_url=/home&is_create=true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].initial["end_date"], "")

    def test_track_modal_uses_default_edit_entry_date_for_existing_entry(self):
        """The preference is exposed to the form when editing existing media."""
        self.user.default_edit_entry_date = "no_date"
        self.user.save()

        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                },
            )
            + f"?return_url=/home&instance_id={self.movie.id}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-default-edit-entry-date="no_date"')
        self.assertContains(
            response,
            '<input type="datetime-local" name="end_date" class=',
        )

    @patch("app.providers.services.get_media_metadata")
    def test_track_modal_default_edit_entry_date_current_date(self, mock_get_metadata):
        """The default preference preserves the normal completed date behavior."""
        mock_get_metadata.return_value = {"title": "New Movie"}
        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "278",
                },
            )
            + "?return_url=/home&is_create=true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("end_date", response.context["form"].initial)

    @patch("app.providers.services.get_media_metadata")
    def test_track_modal_view_new_media(self, mock_get_metadata):
        """Test the track modal view for new media."""
        mock_get_metadata.return_value = {
            "media_id": "278",
            "title": "New Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "max_progress": 1,
        }

        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "278",
                },
            )
            + "?return_url=/home&title=New+Movie",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/fill_track.html")

        self.assertIn("form", response.context)
        self.assertEqual(response.context["form"].initial["media_id"], "278")
        self.assertEqual(
            response.context["form"].initial["media_type"],
            MediaTypes.MOVIE.value,
        )
