from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import (
    Book,
    Item,
    MediaTypes,
    Sources,
    Status,
)
from users.models import ProgressUnit


@patch("app.providers.services.get_media_metadata")
class BookModelTests(TestCase):
    """Test case for the Book model methods."""

    def setUp(self):
        """Set up test data for Book model tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        self.item = Item.objects.create(
            media_id="book123",
            source=Sources.OPENLIBRARY.value,
            media_type=MediaTypes.BOOK.value,
            title="Test Book",
        )

    def test_get_progress_unit_fallback(self, mock_metadata):
        """Test that get_progress_unit falls back to user preference."""
        mock_metadata.return_value = {"max_progress": 200}
        book = Book.objects.create(
            item=self.item,
            user=self.user,
            status=Status.PLANNING.value,
        )

        # Default user preference is PAGES
        self.assertEqual(book.get_progress_unit(), ProgressUnit.PAGES)

        # Update user preference to PERCENTAGE
        self.user.book_progress_unit = ProgressUnit.PERCENTAGE
        self.user.save()
        self.assertEqual(book.get_progress_unit(), ProgressUnit.PERCENTAGE)

        # Override on book specifically
        book.progress_unit = ProgressUnit.PAGES
        book.save()
        self.assertEqual(book.get_progress_unit(), ProgressUnit.PAGES)

    def test_process_progress_percentage(self, mock_metadata):
        """Test progress processing when using percentage unit."""
        mock_metadata.return_value = {"max_progress": 200}

        book = Book.objects.create(
            item=self.item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress_unit=ProgressUnit.PERCENTAGE,
        )

        # Set progress to 50%
        book.progress = 50
        book.save()
        self.assertEqual(book.status, Status.IN_PROGRESS.value)

        # Set progress to 100%
        book.progress = 100
        book.save()
        self.assertEqual(book.status, Status.COMPLETED.value)
        self.assertIsNotNone(book.end_date)

        # Set progress > 100% should be capped
        book.status = Status.IN_PROGRESS.value
        book.progress = 150
        book.save()
        self.assertEqual(book.progress, 100)

    def test_formatted_progress_percentage(self, mock_metadata):
        """Test formatting of progress when using percentage unit."""
        mock_metadata.return_value = {"max_progress": 200}
        book = Book.objects.create(
            item=self.item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=45,
            progress_unit=ProgressUnit.PERCENTAGE,
        )
        self.assertEqual(book.formatted_progress, "45%")

    def test_formatted_progress_pages(self, mock_metadata):
        """Test formatting of progress when using pages unit."""
        mock_metadata.return_value = {"max_progress": 200}
        book = Book.objects.create(
            item=self.item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=150,
            progress_unit=ProgressUnit.PAGES,
        )
        # Mock max_progress annotation which usually comes from MediaManager
        book.max_progress = 300
        self.assertEqual(book.formatted_progress, "150 / 300")
