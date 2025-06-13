from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase

from app.forms import (
    AnimeForm,
    EpisodeForm,
    GameForm,
    ManualItemForm,
    SeasonForm,
    TvForm,
)
from app.models import TV, Item, MediaTypes, Season, Sources, Status


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
        form = AnimeForm(data=form_data)
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
            "media_id": "1",
            "source": Sources.TMDB.value,
            "season_number": 1,
            "episode_number": 1,
            "end_date": "2023-06-01",
            "repeats": 0,
        }
        form = EpisodeForm(data=form_data)
        self.assertTrue(form.is_valid())


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
        self.assertEqual(item.media_id, "1")
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

        # IDs should be different but follow the pattern
        self.assertNotEqual(item1.media_id, item2.media_id)
        self.assertEqual(item1.media_id, "1")
        self.assertEqual(item2.media_id, "2")
