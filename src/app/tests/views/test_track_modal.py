from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.forms import get_form_class
from app.models import (
    TV,
    Anime,
    BoardGame,
    Book,
    Comic,
    Experience,
    Game,
    Item,
    Manga,
    MediaTypes,
    Movie,
    Season,
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
        self.assertEqual(
            response.context["form"]["status"].value(), Status.IN_PROGRESS.value
        )
        self.assertEqual(response.context["return_url"], "/home")

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
        self.assertEqual(
            response.context["form"]["status"].value(), Status.PLANNING.value
        )

    def test_new_media_forms_default_to_planning(self):
        """New Add to tracker forms default all supported media to Planning."""
        media_models = {
            MediaTypes.TV.value: TV,
            MediaTypes.SEASON.value: Season,
            MediaTypes.MOVIE.value: Movie,
            MediaTypes.ANIME.value: Anime,
            MediaTypes.MANGA.value: Manga,
            MediaTypes.GAME.value: Game,
            MediaTypes.BOOK.value: Book,
            MediaTypes.COMIC.value: Comic,
            MediaTypes.BOARDGAME.value: BoardGame,
            MediaTypes.EXPERIENCE.value: Experience,
        }

        for media_type, media_model in media_models.items():
            with self.subTest(media_type=media_type):
                form = get_form_class(media_type)(
                    initial={
                        "media_id": "new",
                        "source": Sources.MANUAL.value,
                        "media_type": media_type,
                    },
                )

                self.assertEqual(form["status"].value(), Status.PLANNING.value)
                self.assertEqual(media_model().status, Status.PLANNING.value)

    def test_track_modal_existing_completed_media_preserves_status(self):
        """Existing Add to tracker forms keep the saved status instead of defaulting."""
        self.movie.status = Status.COMPLETED.value
        self.movie.save()

        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                },
            )
            + "?return_url=/home"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["form"]["status"].value(), Status.COMPLETED.value
        )

    @patch("app.providers.services.get_media_metadata")
    def test_media_save_creates_new_media_as_planning(self, mock_get_metadata):
        """Saving the unchanged default from Add to tracker stores Planning."""
        mock_get_metadata.return_value = {
            "media_id": "278",
            "title": "New Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "max_progress": 1,
        }

        response = self.client.post(
            reverse("media_save") + "?next=/home",
            {
                "media_id": "278",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "status": Status.PLANNING.value,
            },
        )

        self.assertEqual(response.status_code, 302)
        movie = Movie.objects.get(item__media_id="278", user=self.user)
        self.assertEqual(movie.status, Status.PLANNING.value)
