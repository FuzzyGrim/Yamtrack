from pathlib import Path

from django.test import TestCase

from app.models import (
    Item,
    MediaTypes,
    Sources,
)

mock_path = Path(__file__).resolve().parent.parent / "mock_data"


class ItemModel(TestCase):
    """Test case for the Item model."""

    def setUp(self):
        """Set up test data for Item model."""
        self.item = Item.objects.create(
            media_id="1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
        )

    def test_item_creation(self):
        """Test the creation of an Item instance."""
        self.assertEqual(self.item.media_id, "1")
        self.assertEqual(self.item.media_type, MediaTypes.MOVIE.value)
        self.assertEqual(self.item.title, "Test Movie")
        self.assertEqual(self.item.image, "http://example.com/image.jpg")

    def test_item_str_representation(self):
        """Test the string representation of an Item."""
        self.assertEqual(str(self.item), "Test Movie")

    def test_item_with_season_and_episode(self):
        """Test the string representation of an Item with season and episode."""
        item = Item.objects.create(
            media_id="2",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Test Show",
            image="http://example.com/image2.jpg",
            season_number=1,
            episode_number=2,
        )
        self.assertEqual(str(item), "Test Show S1E2")


