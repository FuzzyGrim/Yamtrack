from pathlib import Path
from unittest.mock import MagicMock, patch

import requests
from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import (
    Book,
    Sources,
    Status,
)
from app.providers import services
from integrations.imports import (
    goodreads,
)

mock_path = Path(__file__).resolve().parent.parent / "mock_data"
app_mock_path = (
    Path(__file__).resolve().parent.parent.parent.parent / "app" / "tests" / "mock_data"
)


class ImportGoodreads(TestCase):
    """Test importing media from GoodReads CSV."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.search_patcher = patch(
            "integrations.imports.goodreads.services.search",
            side_effect=self._mock_search,
        )
        self.search_patcher.start()
        self.addCleanup(self.search_patcher.stop)

        with Path(mock_path / "import_goodreads.csv").open("rb") as file:
            self.import_results = goodreads.importer(file, self.user, "new")

    def _mock_search(self, _media_type, query, _page, _source):
        """Return deterministic book search results for the Goodreads CSV fixture."""
        normalized_query = query.strip('="')
        if not normalized_query:
            return {"results": []}

        if normalized_query == "9780756404734":
            title = "The Wise Man's Fear (The Kingkiller Chronicle, #2)"
            media_id = "1215032"
        elif "White Sand" in normalized_query:
            title = "White Sand, Volume 3 (White Sand, #3)"
            media_id = "39298848"
        else:
            title = "Ghosts of the Tristan Basin (Powder Mage, #0.8)"
            media_id = "28825810"

        return {
            "results": [
                {
                    "media_id": media_id,
                    "title": title,
                    "image": "http://example.com/book.jpg",
                },
            ],
        }

    def test_import_counts(self):
        """Test basic counts of imported books."""
        self.assertEqual(Book.objects.filter(user=self.user).count(), 3)

    def test_historical_records(self):
        """Test historical records creation during import."""
        book = Book.objects.filter(user=self.user).first()
        self.assertEqual(book.history.count(), 1)

    def test_stored_progress(self):
        """Test progress of imported books."""
        read_book = Book.objects.get(status=Status.COMPLETED.value)
        self.assertEqual(read_book.status, Status.COMPLETED.value)
        self.assertEqual(read_book.progress, 994)

        read_book = Book.objects.get(status=Status.IN_PROGRESS.value)
        self.assertEqual(read_book.status, Status.IN_PROGRESS.value)
        self.assertEqual(read_book.progress, 0)


class ImportGoodreadsProviderErrors(TestCase):
    """Test GoodReads provider error handling."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

    @patch("integrations.imports.goodreads.GoodReadsImporter._process_row")
    def test_provider_error_warns_with_goodreads_fields(self, mock_process_row):
        """Test provider failures warn and continue without requiring media_id."""
        response = MagicMock(status_code=408, text='{"error":"Request timeout"}')
        error = requests.exceptions.HTTPError(response=response)
        mock_process_row.side_effect = services.ProviderAPIError(
            Sources.HARDCOVER.value,
            error,
        )

        with Path(mock_path / "import_goodreads.csv").open("rb") as file:
            imported_counts, warnings = goodreads.importer(file, self.user, "new")

        self.assertEqual(imported_counts, {})
        self.assertIn(
            "Ghosts of the Tristan Basin (Powder Mage, #0.8) (Goodreads ID 28825810)",
            warnings,
        )
        self.assertIn("There was an error contacting the Hardcover API", warnings)
