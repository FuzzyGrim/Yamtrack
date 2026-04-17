from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import (
    Game,
    Item,
    MediaTypes,
    Sources,
    Status,
)

mock_path = Path(__file__).resolve().parent.parent / "mock_data"


class GameModel(TestCase):
    """Test case for the Game model methods."""

    def setUp(self):
        """Set up test data for Game model tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        self.game_item = Item.objects.create(
            media_id="1234",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="The Last of Us",
            image="http://example.com/tlou.jpg",
        )

        self.game = Game.objects.create(
            item=self.game_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=60,  # 60 minutes
        )

    def test_increase_progress(self):
        """Test increasing the progress of a game."""
        initial_progress = self.game.progress
        self.game.increase_progress()

        self.assertEqual(self.game.progress, initial_progress + 30)

    def test_decrease_progress(self):
        """Test decreasing the progress of a game."""
        initial_progress = self.game.progress
        self.game.decrease_progress()

        self.assertEqual(self.game.progress, initial_progress - 30)

    def test_field_tracker(self):
        """Test that the field tracker is tracking changes."""
        # Initially, there should be no changes
        self.assertFalse(self.game.tracker.changed())

        # Change the progress
        self.game.progress = 90

        # Now there should be changes
        self.assertTrue(self.game.tracker.changed())
        self.assertEqual(self.game.tracker.previous("progress"), 60)

    def test_multiple_progress_changes(self):
        """Test multiple progress changes."""
        # Increase progress twice
        self.game.increase_progress()
        self.game.increase_progress()

        self.assertEqual(self.game.progress, 120)

        # Decrease progress once
        self.game.decrease_progress()

        self.assertEqual(self.game.progress, 90)
