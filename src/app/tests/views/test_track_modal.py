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
    BookProgressUnits,
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

    def test_new_book_forms_default_to_chapters(self):
        """New Book tracker forms default progress units to Chapters."""
        form = get_form_class(MediaTypes.BOOK.value)(
            initial={
                "media_id": "new-book",
                "source": Sources.MANUAL.value,
                "media_type": MediaTypes.BOOK.value,
            },
        )

        self.assertIn("progress_unit", form.fields)
        self.assertEqual(
            form["progress_unit"].value(), BookProgressUnits.CHAPTERS.value
        )
        self.assertEqual(Book().progress_unit, BookProgressUnits.CHAPTERS.value)

    @patch("app.providers.services.get_media_metadata")
    def test_media_save_preserves_book_progress_when_unit_changes(
        self, mock_get_metadata
    ):
        """Changing a Book progress unit does not reset the saved progress number."""
        mock_get_metadata.return_value = {
            "media_id": "book-1",
            "title": "Book One",
            "media_type": MediaTypes.BOOK.value,
            "source": Sources.HARDCOVER.value,
            "image": "http://example.com/book.jpg",
            "max_progress": 350,
        }
        item = Item.objects.create(
            media_id="book-1",
            source=Sources.HARDCOVER.value,
            media_type=MediaTypes.BOOK.value,
            title="Book One",
            image="http://example.com/book.jpg",
        )
        book = Book.objects.create(
            item=item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=7,
            progress_unit=BookProgressUnits.CHAPTERS.value,
        )

        response = self.client.post(
            reverse("media_save") + "?next=/home",
            {
                "instance_id": book.id,
                "media_id": "book-1",
                "source": Sources.HARDCOVER.value,
                "media_type": MediaTypes.BOOK.value,
                "score": "",
                "progress": 7,
                "progress_unit": BookProgressUnits.PAGES.value,
                "status": Status.IN_PROGRESS.value,
                "start_date": "",
                "end_date": "",
                "notes": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        book.refresh_from_db()
        self.assertEqual(book.progress, 7)
        self.assertEqual(book.progress_unit, BookProgressUnits.PAGES.value)

    def test_percent_book_form_clamps_progress(self):
        """Percent Book tracker form clamps progress to 100."""
        form = get_form_class(MediaTypes.BOOK.value)(
            data={
                "media_id": "book-2",
                "source": Sources.HARDCOVER.value,
                "media_type": MediaTypes.BOOK.value,
                "score": "",
                "progress": 142,
                "progress_unit": BookProgressUnits.PERCENT.value,
                "status": Status.IN_PROGRESS.value,
                "start_date": "",
                "end_date": "",
                "notes": "",
            },
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["progress"], 100)

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
