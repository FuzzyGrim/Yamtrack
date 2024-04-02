from django.test import TestCase
from users.models import User

from app.forms import AnimeForm, EpisodeForm, GameForm, SeasonForm, TVForm


class BasicMediaForm(TestCase):
    """Test the standard media form."""

    def setUp(self):
        """Create a user."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user: User = User.objects.create_user(**self.credentials)

    def test_valid_media_form(self):
        """Test the standard media form with valid data."""
        form_data = {
            "media_id": 1,
            "media_type": "anime",
            "title": "Sample",
            "image": "sample.jpg",
            "score": 7.5,
            "progress": 25,
            "status": "Paused",
            "repeats": 0,
            "start_date": "2023-02-01",
            "end_date": "2023-06-30",
            "user": self.user.id,
            "notes": "New notes",
        }
        form = AnimeForm(data=form_data)
        self.assertTrue(form.is_valid())

    def test_valid_tv_form(self):
        """Test the TV form with valid data."""
        form_data = {
            "media_id": 1,
            "media_type": "tv",
            "title": "Sample",
            "image": "sample.jpg",
            "score": 7.5,
            "status": "Completed",
            "repeats": 0,
            "user": self.user.id,
            "notes": "New notes",
        }
        form = TVForm(data=form_data)
        self.assertTrue(form.is_valid())

    def test_valid_season_form(self):
        """Test the season form with valid data."""
        form_data = {
            "media_id": 1,
            "media_type": "season",
            "title": "Sample",
            "image": "sample.jpg",
            "score": 7.5,
            "status": "Completed",
            "repeats": 0,
            "season_number": 1,
            "user": self.user.id,
            "notes": "New notes",
        }
        form = SeasonForm(data=form_data)
        self.assertTrue(form.is_valid())

    def test_valid_episode_form(self):
        """Test the episode form with valid data."""
        form_data = {
            "episode_number": 1,
            "watch_date": "2023-06-01",
        }
        form = EpisodeForm(data=form_data)
        self.assertTrue(form.is_valid())


class BasicGameForm(TestCase):
    """Test the game form."""

    def setUp(self):
        """Create a user."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user: User = User.objects.create_user(**self.credentials)

    def test_default_progress(self):
        """Test the game form using the default progress format."""
        form_data = {
            "media_id": 1,
            "media_type": "game",
            "status": "Completed",
            "progress": "25:00",
            "repeats": 0,
            "user": self.user.id,
        }
        form = GameForm(data=form_data)
        self.assertTrue(form.is_valid())

    def test_alternate_progress(self):
        """Test the game form using an alternate progress format."""
        form_data = {
            "media_id": 1,
            "media_type": "game",
            "status": "Completed",
            "progress": "25h 00min",
            "repeats": 0,
            "user": self.user.id,
        }
        form = GameForm(data=form_data)
        self.assertTrue(form.is_valid())

    def test_second_alternate_progress(self):
        """Test the game form using a second alternate progress format."""
        form_data = {
            "media_id": 1,
            "media_type": "game",
            "status": "Completed",
            "progress": "30min",
            "repeats": 0,
            "user": self.user.id,
        }
        form = GameForm(data=form_data)
        self.assertTrue(form.is_valid())

    def test_third_alternate_progress(self):
        """Test the game form using a second alternate progress format."""
        form_data = {
            "media_id": 1,
            "media_type": "game",
            "status": "Completed",
            "progress": "9h",
            "repeats": 0,
            "user": self.user.id,
        }
        form = GameForm(data=form_data)
        self.assertTrue(form.is_valid())

    def test_fourth_alternate_progress(self):
        """Test the game form using a second alternate progress format."""
        form_data = {
            "media_id": 1,
            "media_type": "game",
            "status": "Completed",
            "progress": "9h30min",
            "repeats": 0,
            "user": self.user.id,
        }
        form = GameForm(data=form_data)
        self.assertTrue(form.is_valid())

    def test_invalid_progress(self):
        """Test the game form using an invalid default progress format."""
        form_data = {
            "media_id": 1,
            "media_type": "game",
            "status": "Completed",
            "progress": "25:00m",
            "repeats": 0,
            "user": self.user.id,
        }
        form = GameForm(data=form_data)
        self.assertFalse(form.is_valid())

    def test_invalid_minutes(self):
        """Test the game form using an invalid default progress format."""
        form_data = {
            "media_id": 1,
            "media_type": "game",
            "status": "Completed",
            "progress": "25h61m",
            "repeats": 0,
            "user": self.user.id,
        }
        form = GameForm(data=form_data)
        self.assertFalse(form.is_valid())
