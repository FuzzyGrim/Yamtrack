import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import Book, Sources, Status
from integrations.imports import hardcover, helpers
from integrations.imports.helpers import MediaImportError

mock_path = Path(__file__).resolve().parent.parent / "mock_data"


def user_book(book_id, status_id=3, **overrides):
    """Return a representative Hardcover user_books response entry."""
    data = {
        "book_id": book_id,
        "status_id": status_id,
        "rating": 4.5,
        "date_added": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-03-01T00:00:00+00:00",
        "private_notes": "Private note",
        "review_raw": "Public review",
        "book": {"title": f"Book {book_id}", "pages": 300, "cached_image": "cover"},
        "user_book_reads": [
            {
                "started_at": "2024-01-10T00:00:00+00:00",
                "finished_at": "2024-02-01T00:00:00+00:00",
                "progress_pages": 100,
            },
        ],
    }
    data.update(overrides)
    return data


class HardcoverImporterTests(TestCase):  # noqa: D101
    def setUp(self):
        """Create a user and encrypted test token."""
        self.user = get_user_model().objects.create_user(
            username="reader",
            password="test",  # noqa: S106
        )
        self.token = helpers.encrypt("token")
        with Path(mock_path / "import_hardcover.json").open() as file:
            self.sample_response = json.load(file)

    @patch("integrations.imports.hardcover.services.api_request")
    def test_import_maps_book_fields(self, mock_api_request):
        """Map Hardcover data into a completed Yamtrack book."""
        mock_api_request.return_value = self.sample_response

        counts, warnings = hardcover.importer(self.token, self.user, "new")

        book = Book.objects.get(user=self.user)
        self.assertEqual(counts, {"book": 1})
        self.assertEqual(warnings, "")
        self.assertEqual(book.item.source, Sources.HARDCOVER.value)
        self.assertEqual(book.item.media_id, "12")
        self.assertEqual(
            book.item.image,
            "https://assets.hardcover.app/external_data/cover.jpeg",
        )
        self.assertEqual(book.status, Status.COMPLETED.value)
        self.assertEqual(book.score, 9)
        self.assertEqual(book.progress, 300)
        self.assertEqual(book.notes, "Private note")
        self.assertEqual(book.start_date, datetime(2024, 1, 10, tzinfo=UTC))
        self.assertEqual(
            book.history.first().history_date,
            datetime(2024, 3, 1, tzinfo=UTC),
        )

    @patch("integrations.imports.hardcover.services.api_request")
    def test_handles_hardcover_me_array_response(self, mock_api_request):
        """Read a library response using Hardcover's list-shaped `me` field."""
        response = self.sample_response
        response["data"]["me"] = [response["data"]["me"]]
        mock_api_request.return_value = response

        counts, _ = hardcover.importer(self.token, self.user, "new")

        self.assertEqual(counts, {"book": 1})

    @patch("integrations.imports.hardcover.services.api_request")
    def test_reads_username_from_hardcover_me_array(self, mock_api_request):
        """Validate a token against Hardcover's list-shaped `me` field."""
        mock_api_request.return_value = {"data": {"me": [{"username": "reader"}]}}

        username = hardcover.get_username("token")

        self.assertEqual(username, "reader")

    @patch("integrations.imports.hardcover.services.api_request")
    def test_normalizes_naive_hardcover_dates(self, mock_api_request):
        """Allow naive Hardcover timestamps to be compared with aware timestamps."""
        response = self.sample_response
        response["data"]["me"]["user_books"][0]["updated_at"] = "2024-03-01T00:00:00"
        mock_api_request.return_value = response

        counts, _ = hardcover.importer(self.token, self.user, "new")

        self.assertEqual(counts, {"book": 1})
        self.assertTrue(
            timezone.is_aware(
                Book.objects.get(user=self.user).history.first().history_date
            )
        )

    def test_extracts_cover_url_from_object_response(self):
        """Support the object form returned by a bare cached_image query."""
        image = hardcover.HardcoverImporter._get_image(
            {"cached_image": {"url": "https://assets.hardcover.app/cover.jpeg"}},
        )

        self.assertEqual(image, "https://assets.hardcover.app/cover.jpeg")

    @patch("integrations.imports.hardcover.services.api_request")
    def test_paginates_until_a_short_page(self, mock_api_request):
        """Fetch each page until Hardcover returns fewer than the page limit."""
        first_page = [user_book(index) for index in range(100)]
        mock_api_request.side_effect = [
            {"data": {"me": {"user_books": first_page}}},
            {"data": {"me": {"user_books": [user_book(100)]}}},
        ]

        counts, _ = hardcover.importer(self.token, self.user, "new")

        self.assertEqual(counts, {"book": 101})
        self.assertEqual(mock_api_request.call_count, 2)
        variables = mock_api_request.call_args_list[1].kwargs["params"]["variables"]
        offset = variables["offset"]
        self.assertEqual(offset, 100)

    @patch("integrations.imports.hardcover.services.api_request")
    def test_new_skips_existing_and_overwrite_replaces_it(self, mock_api_request):
        """Respect new-mode skips and overwrite-mode replacement."""
        response = {"data": {"me": {"user_books": [user_book(12, rating=3)]}}}
        mock_api_request.return_value = response
        hardcover.importer(self.token, self.user, "new")

        counts, _ = hardcover.importer(self.token, self.user, "new")
        self.assertEqual(counts, {})
        self.assertEqual(Book.objects.filter(user=self.user).count(), 1)

        counts, _ = hardcover.importer(self.token, self.user, "overwrite")
        self.assertEqual(counts, {"book": 1})
        self.assertEqual(Book.objects.get(user=self.user).score, 6)

    @patch("integrations.imports.hardcover.services.api_request")
    def test_skips_ignored_status_with_warning(self, mock_api_request):
        """Skip non-importable Hardcover status IDs."""
        mock_api_request.return_value = {
            "data": {"me": {"user_books": [user_book(12, 6)]}},
        }

        counts, warnings = hardcover.importer(self.token, self.user, "new")

        self.assertEqual(counts, {})
        self.assertEqual(Book.objects.count(), 0)
        self.assertIn("Unsupported Hardcover status 6", warnings)

    @patch("integrations.imports.hardcover.services.api_request")
    def test_unauthorized_token_is_an_import_error(self, mock_api_request):
        """Surface HTTP 401 as an actionable import failure."""
        response = MagicMock(status_code=401)
        mock_api_request.side_effect = requests.exceptions.HTTPError(response=response)

        with self.assertRaisesMessage(
            MediaImportError,
            "Invalid or expired Hardcover token",
        ):
            hardcover.importer(self.token, self.user, "new")


class HardcoverImportViewTests(TestCase):  # noqa: D101
    def setUp(self):
        """Authenticate a user for import view requests."""
        self.user = get_user_model().objects.create_user(
            username="reader",
            password="test",  # noqa: S106
        )
        self.client.force_login(self.user)

    @patch("integrations.views.tasks.import_hardcover.delay")
    @patch("integrations.views.helpers.encrypt", return_value="encrypted-token")
    @patch("integrations.views.hardcover.get_username", return_value="reader")
    def test_queues_one_time_import(self, mock_username, mock_encrypt, mock_delay):
        """Validate and queue a one-time import with an encrypted token."""
        response = self.client.post(
            reverse("import_hardcover"),
            {"token": "token", "mode": "new", "frequency": "once", "time": "12:00"},
        )

        self.assertRedirects(response, reverse("import_data"))
        mock_username.assert_called_once_with("token")
        mock_encrypt.assert_called_once_with("token")
        mock_delay.assert_called_once_with(
            token="encrypted-token",  # noqa: S106
            user_id=self.user.id,
            mode="new",
        )

    @patch("integrations.views.helpers.create_import_schedule")
    @patch("integrations.views.helpers.encrypt", return_value="encrypted-token")
    @patch("integrations.views.hardcover.get_username", return_value="reader")
    def test_schedules_periodic_import(
        self,
        mock_username,
        mock_encrypt,
        mock_schedule,
    ):
        """Persist the encrypted token in periodic import task kwargs."""
        response = self.client.post(
            reverse("import_hardcover"),
            {
                "token": "token",
                "mode": "overwrite",
                "frequency": "daily",
                "time": "12:00",
            },
        )

        self.assertRedirects(response, reverse("import_data"))
        mock_username.assert_called_once_with("token")
        mock_encrypt.assert_called_once_with("token")
        mock_schedule.assert_called_once_with(
            "reader",
            response.wsgi_request,
            "overwrite",
            "daily",
            "12:00",
            "Hardcover",
            task_kwargs={"token": "encrypted-token"},
        )
