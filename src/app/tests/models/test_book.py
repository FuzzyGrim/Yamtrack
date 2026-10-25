from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from app.mixins import disable_user_messages
from app.models import (
    Book,
    Item,
    MediaTypes,
    ProgressUnit,
    Sources,
    Status,
    UserMessage,
    UserMessageLevel,
)
from app.providers.services import ProviderAPIError


@patch("app.providers.services.get_media_metadata")
class BookModelTests(TestCase):
    """Test case for the Book model methods."""

    def setUp(self):
        """Set up test data for books."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        self.item = Item.objects.create(
            media_id="book123",
            source=Sources.OPENLIBRARY.value,
            media_type=MediaTypes.BOOK.value,
            title="Test Book",
        )

    def test_progress_unit_defaults_to_pages(self, mock_metadata):
        """Test the default unit is pages."""
        mock_metadata.return_value = {"max_progress": 200}
        book = Book.objects.create(
            item=self.item,
            user=self.user,
            status=Status.PLANNING.value,
        )
        book.refresh_from_db()

        self.assertEqual(book.get_progress_unit(), ProgressUnit.PAGES)

    def test_preference_does_not_reinterpret_stored_progress(self, mock_metadata):
        """Test the preference never rewrites progress."""
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

    def test_percentage_capped_outside_in_progress(self, mock_metadata):
        """Test a paused book caps at 100."""
        mock_metadata.return_value = {"max_progress": 400}
        book = Book.objects.create(
            item=self.item,
            user=self.user,
            status=Status.PAUSED.value,
            progress_unit=ProgressUnit.PERCENTAGE,
            progress=150,
        )
        book.refresh_from_db()

        self.assertEqual(book.progress, 100)
        self.assertEqual(book.status, Status.PAUSED.value)

    def test_progress_saved_when_provider_unavailable(self, mock_metadata):
        """Test a provider outage keeps the edit."""
        mock_metadata.side_effect = ProviderAPIError(
            Sources.OPENLIBRARY.value,
            Exception("unavailable"),
        )
        book = Book.objects.create(
            item=self.item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=120,
        )
        book.refresh_from_db()

        self.assertEqual(book.progress, 120)
        self.assertEqual(book.status, Status.IN_PROGRESS.value)

        message = UserMessage.objects.get(user=self.user)
        self.assertEqual(message.level, UserMessageLevel.WARNING)
        self.assertIn("without checking its total", message.message)

    def test_no_toast_during_bulk_import(self, mock_metadata):
        """Test bulk work suppresses the per-item toast."""
        mock_metadata.side_effect = ProviderAPIError(
            Sources.OPENLIBRARY.value,
            Exception("unavailable"),
        )
        with disable_user_messages():
            Book.objects.create(
                item=self.item,
                user=self.user,
                status=Status.IN_PROGRESS.value,
                progress=120,
            )

        self.assertFalse(UserMessage.objects.filter(user=self.user).exists())

    def test_progress_unit_is_stored_on_the_book(self, mock_metadata):
        """Test an explicit unit persists."""
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

        # Percentage needs no provider lookup.
        mock_metadata.assert_not_called()

    def test_formatted_progress_percentage(self, mock_metadata):
        """Test formatting a percentage progress."""
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
        """Test formatting a pages progress."""
        mock_metadata.return_value = {"max_progress": 200}
        book = Book.objects.create(
            item=self.item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=150,
            progress_unit=ProgressUnit.PAGES,
        )
        # MediaManager normally sets this annotation.
        book.max_progress = 300
        self.assertEqual(book.formatted_progress, "150 / 300")
