from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import (
    Book,
    Item,
    MediaTypes,
    ProgressUnit,
    Sources,
    Status,
)


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

    def test_progress_unit_defaults_to_pages(self, mock_metadata):
        """Test that a new book records pages by default."""
        mock_metadata.return_value = {"max_progress": 200}
        book = Book.objects.create(
            item=self.item,
            user=self.user,
            status=Status.PLANNING.value,
        )
        book.refresh_from_db()

        self.assertEqual(book.get_progress_unit(), ProgressUnit.PAGES)

    def test_preference_does_not_reinterpret_stored_progress(self, mock_metadata):
        """Test that the preference never rewrites recorded progress."""
        mock_metadata.return_value = {"max_progress": 400}
        book = Book.objects.create(
            item=self.item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=250,
        )

        self.user.book_progress_unit = ProgressUnit.PERCENTAGE
        self.user.save()

        book.refresh_from_db()
        self.assertEqual(book.get_progress_unit(), ProgressUnit.PAGES)
        self.assertEqual(book.progress, 250)
        self.assertEqual(book.formatted_progress, "250")

    def test_progress_unit_is_stored_on_the_book(self, mock_metadata):
        """Test that an explicit unit persists to the database."""
        mock_metadata.return_value = {"max_progress": 200}
        book = Book.objects.create(
            item=self.item,
            user=self.user,
            status=Status.PLANNING.value,
            progress_unit=ProgressUnit.PERCENTAGE,
        )
        book.refresh_from_db()

        self.assertEqual(book.get_progress_unit(), ProgressUnit.PERCENTAGE)

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
