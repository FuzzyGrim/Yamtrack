from io import BytesIO

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image

from app.forms import (
    AnimeForm,
    EpisodeForm,
    GameForm,
    ManualItemForm,
    MovieForm,
    SeasonForm,
    TvForm,
)
from app.models import TV, Anime, Item, MediaTypes, Movie, Season, Sources, Status


class BasicMediaForm(TestCase):
    """Test the standard media form."""

    def setUp(self):
        """Create a user."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Test Anime",
            image="http://example.com/image.jpg",
        )

        Item.objects.create(
            media_id="1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test tv",
            image="http://example.com/image.jpg",
        )

    def test_provider_media_form_hides_manual_image_fields(self):
        """Provider-backed items should not expose manual image upload controls."""
        anime = Anime.objects.create(
            item=Item.objects.get(source=Sources.MAL.value),
            user=self.user,
            status=Status.PLANNING.value,
        )

        form = AnimeForm(instance=anime)

        self.assertNotIn("uploaded_image", form.fields)
        self.assertNotIn("clear_uploaded_image", form.fields)

    def test_manual_media_form_shows_manual_image_fields(self):
        """Manual items should expose optional local image controls."""
        item = Item.objects.create(
            media_id="manual_movie",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MOVIE.value,
            title="Manual Movie",
            image="http://example.com/manual.jpg",
        )
        movie = Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.PLANNING.value,
        )

        form = MovieForm(instance=movie)

        self.assertIn("uploaded_image", form.fields)
        self.assertIn("clear_uploaded_image", form.fields)

    def test_valid_media_form(self):
        """Test the standard media form with valid data."""
        form_data = {
            "media_id": "1",
            "source": Sources.MAL.value,
            "media_type": MediaTypes.ANIME.value,
            "user": self.user.id,
            "score": 7.5,
            "progress": 25,
            "status": Status.PAUSED.value,
            "repeats": 0,
            "start_date": "2023-02-01",
            "end_date": "2023-06-30",
            "notes": "New notes",
        }
        form = AnimeForm(
            data=form_data,
            custom_link_entries=[
                {"label": "Netflix", "url": "https://www.netflix.com/title/1"},
            ],
        )
        self.assertTrue(form.is_valid())

    def test_valid_tv_form(self):
        """Test the TV form with valid data."""
        form_data = {
            "media_id": "1",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "user": self.user.id,
            "score": 7.5,
            "status": Status.COMPLETED.value,
            "repeats": 0,
            "notes": "New notes",
        }
        form = TvForm(data=form_data)
        self.assertTrue(form.is_valid())

    def test_media_date_fields_are_optional(self):
        """Start and end dates should remain optional in tracking forms."""
        form = AnimeForm()

        self.assertFalse(form.fields["start_date"].required)
        self.assertFalse(form.fields["end_date"].required)

    def test_valid_season_form(self):
        """Test the season form with valid data."""
        form_data = {
            "media_id": "1",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.SEASON.value,
            "user": self.user.id,
            "score": 7.5,
            "status": Status.COMPLETED.value,
            "repeats": 0,
            "season_number": 1,
            "notes": "New notes",
        }
        form = SeasonForm(data=form_data)
        self.assertTrue(form.is_valid())

    def test_valid_episode_form(self):
        """Test the episode form with valid data."""
        form_data = {
            "end_date": "2023-06-01",
        }
        form = EpisodeForm(data=form_data)
        self.assertTrue(form.is_valid())

    def test_valid_episode_datetime_form(self):
        """Test the episode form with valid data."""
        form_data = {
            "end_date": "2023-06-01T12:00:00Z",
        }
        form = EpisodeForm(data=form_data)
        self.assertTrue(form.is_valid())


    def test_links_data_accepts_valid_urls(self):
        form_data = {
            "media_id": "1",
            "source": Sources.MAL.value,
            "media_type": MediaTypes.ANIME.value,
            "status": Status.PAUSED.value,
        }
        form = AnimeForm(
            data=form_data,
            custom_link_entries=[
                {"label": "Netflix", "url": "https://www.netflix.com/title/1"},
            ],
        )
        self.assertTrue(form.is_valid())

    def test_links_data_rejects_invalid_url(self):
        form_data = {
            "media_id": "1",
            "source": Sources.MAL.value,
            "media_type": MediaTypes.ANIME.value,
            "status": Status.PAUSED.value,
        }
        form = AnimeForm(
            data=form_data,
            custom_link_entries=[{"label": "Bad", "url": "not-a-url"}],
        )
        self.assertFalse(form.is_valid())
        self.assertIn("__all__", form.errors)


    def test_links_data_rejects_too_long_url(self):
        form_data = {
            "media_id": "1",
            "source": Sources.MAL.value,
            "media_type": MediaTypes.ANIME.value,
            "status": Status.PAUSED.value,
        }
        form = AnimeForm(
            data=form_data,
            custom_link_entries=[
                {"label": "Long", "url": f"https://example.com/{'a' * 510}"},
            ],
        )
        self.assertFalse(form.is_valid())
        self.assertIn("__all__", form.errors)

class BasicGameForm(TestCase):
    """Test the game form."""

    def setUp(self):
        """Create a user."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.item = Item.objects.create(
            media_id="1",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Test Game",
            image="http://example.com/image.jpg",
        )

    def test_default_progress(self):
        """Test the game form using the default progress format."""
        form_data = {
            "media_id": "1",
            "source": Sources.IGDB.value,
            "media_type": MediaTypes.GAME.value,
            "user": self.user.id,
            "status": Status.COMPLETED.value,
            "progress": "25:00",
            "repeats": 0,
        }
        form = GameForm(data=form_data)
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["progress"], 1500)

    def test_plain_number_progress(self):
        """Test the game form with a plain number for hours (e.g., '5')."""
        form_data = {
            "media_id": "1",
            "source": Sources.IGDB.value,
            "media_type": MediaTypes.GAME.value,
            "user": self.user.id,
            "status": Status.COMPLETED.value,
            "progress": "5",
            "repeats": 0,
        }
        form = GameForm(data=form_data)
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["progress"], 300)

    def test_alternate_progress(self):
        """Test the game form using an alternate progress format."""
        form_data = {
            "media_id": "1",
            "source": Sources.IGDB.value,
            "media_type": MediaTypes.GAME.value,
            "user": self.user.id,
            "status": Status.COMPLETED.value,
            "progress": "25h 00min",
            "repeats": 0,
        }
        form = GameForm(data=form_data)
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["progress"], 1500)

    def test_second_alternate_progress(self):
        """Test the game form using a second alternate progress format."""
        form_data = {
            "media_id": "1",
            "source": Sources.IGDB.value,
            "media_type": MediaTypes.GAME.value,
            "user": self.user.id,
            "status": Status.COMPLETED.value,
            "progress": "30min",
            "repeats": 0,
        }
        form = GameForm(data=form_data)
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["progress"], 30)

    def test_third_alternate_progress(self):
        """Test the game form using a second alternate progress format."""
        form_data = {
            "media_id": "1",
            "source": Sources.IGDB.value,
            "media_type": MediaTypes.GAME.value,
            "user": self.user.id,
            "status": Status.COMPLETED.value,
            "progress": "9h",
            "repeats": 0,
        }
        form = GameForm(data=form_data)
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["progress"], 540)

    def test_fourth_alternate_progress(self):
        """Test the game form using a second alternate progress format."""
        form_data = {
            "media_id": "1",
            "source": Sources.IGDB.value,
            "media_type": MediaTypes.GAME.value,
            "user": self.user.id,
            "status": Status.COMPLETED.value,
            "progress": "9h30min",
            "repeats": 0,
        }
        form = GameForm(data=form_data)
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["progress"], 570)

    def test_float_progress(self):
        """Test the game form with float progress format (e.g., 1.5 hours)."""
        form_data = {
            "media_id": "1",
            "source": Sources.IGDB.value,
            "media_type": MediaTypes.GAME.value,
            "user": self.user.id,
            "status": Status.COMPLETED.value,
            "progress": "1.5",
            "repeats": 0,
        }
        form = GameForm(data=form_data)
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["progress"], 90)

    def test_float_progress_half_hour(self):
        """Test the game form with 0.5 float progress (30 minutes)."""
        form_data = {
            "media_id": "1",
            "source": Sources.IGDB.value,
            "media_type": MediaTypes.GAME.value,
            "user": self.user.id,
            "status": Status.COMPLETED.value,
            "progress": "0.5",
            "repeats": 0,
        }
        form = GameForm(data=form_data)
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["progress"], 30)

    def test_invalid_negative_float_progress(self):
        """Test that negative float progress is rejected."""
        form_data = {
            "media_id": "1",
            "source": Sources.IGDB.value,
            "media_type": MediaTypes.GAME.value,
            "user": self.user.id,
            "status": Status.COMPLETED.value,
            "progress": "-1.5",
            "repeats": 0,
        }
        form = GameForm(data=form_data)
        self.assertFalse(form.is_valid())

    def test_invalid_inf_progress(self):
        """Test that infinity progress is rejected."""
        form_data = {
            "media_id": "1",
            "source": Sources.IGDB.value,
            "media_type": MediaTypes.GAME.value,
            "user": self.user.id,
            "status": Status.COMPLETED.value,
            "progress": "inf",
            "repeats": 0,
        }
        form = GameForm(data=form_data)
        self.assertFalse(form.is_valid())

    def test_invalid_nan_progress(self):
        """Test that NaN progress is rejected."""
        form_data = {
            "media_id": "1",
            "source": Sources.IGDB.value,
            "media_type": MediaTypes.GAME.value,
            "user": self.user.id,
            "status": Status.COMPLETED.value,
            "progress": "nan",
            "repeats": 0,
        }
        form = GameForm(data=form_data)
        self.assertFalse(form.is_valid())

    def test_invalid_progress(self):
        """Test the game form using an invalid default progress format."""
        form_data = {
            "media_id": "1",
            "source": Sources.IGDB.value,
            "media_type": MediaTypes.GAME.value,
            "user": self.user.id,
            "status": Status.COMPLETED.value,
            "progress": "25:00m",
            "repeats": 0,
        }
        form = GameForm(data=form_data)
        self.assertFalse(form.is_valid())

    def test_invalid_minutes(self):
        """Test the game form using an invalid default progress format."""
        form_data = {
            "media_id": "1",
            "source": Sources.IGDB.value,
            "media_type": MediaTypes.GAME.value,
            "user": self.user.id,
            "status": Status.COMPLETED.value,
            "progress": "25h61m",
            "repeats": 0,
        }
        form = GameForm(data=form_data)
        self.assertFalse(form.is_valid())


class ManualItemFormTest(TestCase):
    """Test the manual item form functionality."""

    def setUp(self):
        """Create a user and necessary parent items."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        # Create a manual TV show
        self.tv_item = Item.objects.create(
            media_id="manual_tv_1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.TV.value,
            title="Test Manual TV",
            image="http://example.com/tv.jpg",
        )
        self.tv = TV.objects.create(
            item=self.tv_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        # Create a manual Season
        self.season_item = Item.objects.create(
            media_id="manual_tv_1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.SEASON.value,
            title="Test Manual TV",
            season_number=1,
            image="http://example.com/season.jpg",
        )
        self.season = Season.objects.create(
            item=self.season_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

    def test_init_with_user(self):
        """Test form initialization with user parameter."""
        form = ManualItemForm(user=self.user)
        self.assertEqual(form.fields["parent_tv"].queryset.count(), 1)
        self.assertEqual(form.fields["parent_season"].queryset.count(), 1)

    def test_init_without_user(self):
        """Test form initialization without user parameter."""
        form = ManualItemForm()
        self.assertEqual(form.fields["parent_tv"].queryset.count(), 0)
        self.assertEqual(form.fields["parent_season"].queryset.count(), 0)

    def test_valid_standalone_media(self):
        """Test creating a standalone media item (movie, anime, etc.)."""
        form_data = {
            "media_type": MediaTypes.MOVIE.value,
            "title": "Test Manual Movie",
            "image": "http://example.com/movie.jpg",
        }
        form = ManualItemForm(data=form_data, user=self.user)
        self.assertTrue(form.is_valid())

        # Save and verify
        item = form.save()
        self.assertEqual(item.source, Sources.MANUAL.value)
        self.assertTrue(item.media_id)
        self.assertIsNone(item.season_number)
        self.assertIsNone(item.episode_number)

    def test_valid_season_creation(self):
        """Test creating a season for an existing TV show."""
        form_data = {
            "media_type": MediaTypes.SEASON.value,
            "parent_tv": self.tv.id,
            "season_number": 2,
        }
        form = ManualItemForm(data=form_data, user=self.user)
        self.assertTrue(form.is_valid())

        # Save and verify
        item = form.save()
        self.assertEqual(item.source, Sources.MANUAL.value)
        self.assertEqual(item.media_id, self.tv_item.media_id)
        self.assertEqual(item.title, self.tv_item.title)
        self.assertEqual(item.season_number, 2)
        self.assertIsNone(item.episode_number)

    def test_valid_episode_creation(self):
        """Test creating an episode for an existing season."""
        form_data = {
            "media_type": MediaTypes.EPISODE.value,
            "parent_season": self.season.id,
            "episode_number": 5,
        }
        form = ManualItemForm(data=form_data, user=self.user)
        self.assertTrue(form.is_valid())

        # Save and verify
        item = form.save()
        self.assertEqual(item.source, Sources.MANUAL.value)
        self.assertEqual(item.media_id, self.season_item.media_id)
        self.assertEqual(item.title, self.season_item.title)
        self.assertEqual(item.season_number, self.season_item.season_number)
        self.assertEqual(item.episode_number, 5)

    def test_missing_title_for_standalone(self):
        """Test that title is required for standalone."""
        form_data = {
            "media_type": MediaTypes.MOVIE.value,
        }
        form = ManualItemForm(data=form_data, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn("title", form.errors)

    def test_missing_parent_for_season(self):
        """Test that parent TV is required for seasons."""
        form_data = {
            "media_type": MediaTypes.SEASON.value,
            "season_number": 3,
        }
        form = ManualItemForm(data=form_data, user=self.user)
        self.assertFalse(form.is_valid())

    def test_missing_parent_for_episode(self):
        """Test that parent season is required for episodes."""
        form_data = {
            "media_type": MediaTypes.EPISODE.value,
            "episode_number": 2,
        }
        form = ManualItemForm(data=form_data, user=self.user)
        self.assertFalse(form.is_valid())

    def test_default_image(self):
        """Test that default image is used when none provided."""
        form_data = {
            "media_type": MediaTypes.BOOK.value,
            "title": "Test Manual Book",
        }
        form = ManualItemForm(data=form_data, user=self.user)
        self.assertTrue(form.is_valid())

        # Save and verify
        item = form.save()
        self.assertEqual(item.image, settings.IMG_NONE)

    @override_settings(MEDIA_ROOT="/tmp/yamtrack-test-media")  # noqa: S108
    def test_uploaded_manual_image_takes_display_priority(self):
        """Uploaded images are saved and used ahead of the URL for manual items."""
        image_file = BytesIO()
        Image.new("RGB", (1, 1), color="red").save(image_file, format="JPEG")
        image_file.seek(0)
        image = SimpleUploadedFile(
            "cover.jpg",
            image_file.read(),
            content_type="image/jpeg",
        )
        form_data = {
            "media_type": MediaTypes.MOVIE.value,
            "title": "Uploaded Manual Movie",
            "image": "http://example.com/movie.jpg",
        }

        form = ManualItemForm(
            data=form_data,
            files={"uploaded_image": image},
            user=self.user,
        )

        self.assertTrue(form.is_valid(), form.errors)
        item = form.save()
        self.assertTrue(item.uploaded_image.name.startswith("manual_items/"))
        self.assertEqual(item.display_image, item.uploaded_image.url)

    def test_unsupported_manual_image_type_is_rejected(self):
        """Unsupported local image uploads fail validation instead of crashing."""
        upload = SimpleUploadedFile(
            "not-image.txt",
            b"not an image",
            content_type="text/plain",
        )
        form_data = {
            "media_type": MediaTypes.MOVIE.value,
            "title": "Bad Upload",
        }

        form = ManualItemForm(
            data=form_data,
            files={"uploaded_image": upload},
            user=self.user,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("uploaded_image", form.errors)

    def test_manual_id_generation(self):
        """Test that unique manual IDs are generated."""
        # Create first item
        form1 = ManualItemForm(
            data={"media_type": MediaTypes.ANIME.value, "title": "Test Anime 1"},
            user=self.user,
        )
        self.assertTrue(form1.is_valid())
        item1 = form1.save()

        # Create second item
        form2 = ManualItemForm(
            data={"media_type": MediaTypes.ANIME.value, "title": "Test Anime 2"},
            user=self.user,
        )
        self.assertTrue(form2.is_valid())
        item2 = form2.save()

        # IDs should be different
        self.assertNotEqual(item1.media_id, item2.media_id)
        self.assertTrue(item1.media_id)
        self.assertTrue(item2.media_id)
