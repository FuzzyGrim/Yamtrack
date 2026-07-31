from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import Book, Item, MediaTypes, Sources, Status
from app.providers import services
from integrations.imports import calibre_web_nextgen, helpers
from integrations.imports.helpers import MediaImportError, MediaImportUnexpectedError


def book_progress(
    title,
    percentage,
    calibre_book_id,
    isbn,
    created_at="2024-01-01T09:00:00+00:00",
    last_modified="2024-06-01T09:00:00+00:00",
    authors=("Some Author",),
):
    """Build a single /kosync/export entry."""
    entry = {
        "title": title,
        "percentage": percentage,
        "calibre_book_id": calibre_book_id,
        "identifiers": {"isbn": isbn},
        "authors": list(authors),
        "created_at": created_at,
    }
    if last_modified is not None:
        entry["last_modified"] = last_modified
    return entry


@patch("integrations.imports.calibre_web_nextgen.services.api_request")
@patch("integrations.imports.calibre_web_nextgen.services.search")
@patch("integrations.imports.calibre_web_nextgen.services.get_media_metadata")
class ImportCalibreWebNextGen(TestCase):
    """Test importing books and progress from Calibre-Web-NextGen."""

    def setUp(self):
        """Create user and encrypted credentials for the tests."""
        self.user = get_user_model().objects.create_user(
            username="test",
            password="12345",  # noqa: S106
        )
        self.password_token = helpers.encrypt("secret")

    def _import(self, mode="new"):
        return calibre_web_nextgen.importer(
            "admin",
            self.user,
            mode,
            "http://localhost:8083",
            self.password_token,
        )

    @staticmethod
    def _search_by_isbn(media_id_by_isbn):
        """Return a search side effect resolving each isbn to a media_id."""

        def _search(_media_type, query, _page, _source):
            media_id = media_id_by_isbn.get(query)
            if media_id is None:
                return {"results": []}
            return {"results": [{"media_id": media_id}]}

        return _search

    @staticmethod
    def _metadata(metadata_by_id):
        """Return a get_media_metadata side effect keyed by media_id."""

        def _get(_media_type, media_id, _source):
            return metadata_by_id[str(media_id)]

        return _get

    def test_import_books_by_status(self, mock_metadata, mock_search, mock_api):
        """Test completed, in-progress and planning books are stored correctly."""
        recent = datetime.now(UTC).isoformat()
        mock_api.return_value = [
            book_progress("Done", 100, 1, "isbn-done"),
            book_progress("Reading", 50, 2, "isbn-reading", last_modified=recent),
            book_progress("Someday", 0, 3, "isbn-planning"),
        ]
        mock_search.side_effect = self._search_by_isbn(
            {"isbn-done": "10", "isbn-reading": "20", "isbn-planning": "30"},
        )
        mock_metadata.side_effect = self._metadata(
            {
                "10": {
                    "media_id": "10",
                    "title": "Done",
                    "image": "d.jpg",
                    "max_progress": 300,
                },
                "20": {
                    "media_id": "20",
                    "title": "Reading",
                    "image": "r.jpg",
                    "max_progress": 300,
                },
                "30": {
                    "media_id": "30",
                    "title": "Someday",
                    "image": "s.jpg",
                    "max_progress": 300,
                },
            },
        )

        imported_counts, warnings = self._import()

        self.assertEqual(imported_counts[MediaTypes.BOOK.value], 3)
        self.assertEqual(warnings, "")

        done = Book.objects.get(item__title="Done")
        self.assertEqual(done.status, Status.COMPLETED.value)
        self.assertEqual(done.progress, 300)
        self.assertIsNotNone(done.start_date)
        self.assertIsNotNone(done.end_date)
        # history is dated to the export's last_modified, not import time
        self.assertEqual(
            done.history.get().history_date, datetime(2024, 6, 1, 9, tzinfo=UTC)
        )

        reading = Book.objects.get(item__title="Reading")
        self.assertEqual(reading.status, Status.IN_PROGRESS.value)
        self.assertEqual(reading.progress, 150)
        self.assertIsNone(reading.end_date)

        someday = Book.objects.get(item__title="Someday")
        self.assertEqual(someday.status, Status.PLANNING.value)
        self.assertEqual(someday.progress, 0)
        self.assertIsNone(someday.start_date)

    def test_progress_never_below_one_page(self, mock_metadata, mock_search, mock_api):
        """Test progress floors to 1 for unknown page counts and barely-read books."""
        recent = datetime.now(UTC).isoformat()
        mock_api.return_value = [
            book_progress("No Pages", 100, 1, "isbn-x"),
            book_progress("Tiny", 1, 2, "isbn-tiny", last_modified=recent),
        ]
        mock_search.side_effect = self._search_by_isbn(
            {"isbn-x": "40", "isbn-tiny": "60"},
        )
        mock_metadata.side_effect = self._metadata(
            {
                # completed, page count unknown -> fallback to 1
                "40": {
                    "media_id": "40",
                    "title": "No Pages",
                    "image": "x.jpg",
                    "max_progress": None,
                },
                # 1% of 10 rounds to 0 -> clamped to 1
                "60": {
                    "media_id": "60",
                    "title": "Tiny",
                    "image": "t.jpg",
                    "max_progress": 10,
                },
            },
        )

        self._import()

        self.assertEqual(Book.objects.get(item__title="No Pages").progress, 1)
        self.assertEqual(Book.objects.get(item__title="Tiny").progress, 1)

    def test_empty_library(self, mock_metadata, mock_search, mock_api):
        """Test an empty export imports nothing without erroring."""
        mock_api.return_value = []

        imported_counts, warnings = self._import()

        self.assertEqual(imported_counts, {})
        self.assertEqual(warnings, "")
        self.assertEqual(Book.objects.filter(user=self.user).count(), 0)
        mock_search.assert_not_called()
        mock_metadata.assert_not_called()

    def test_no_match_skipped_with_warning(self, mock_metadata, mock_search, mock_api):
        """Test a book with no source match is skipped and warned about."""
        mock_api.return_value = [book_progress("Unknown", 100, 99, "isbn-none")]
        mock_search.return_value = {"results": []}

        imported_counts, warnings = self._import()

        self.assertEqual(imported_counts, {})
        self.assertEqual(Book.objects.filter(user=self.user).count(), 0)
        self.assertIn("Unknown (99)", warnings)
        self.assertIn("no match found in sources", warnings)
        mock_metadata.assert_not_called()

    def test_naive_and_missing_dates(self, mock_metadata, mock_search, mock_api):
        """Test naive timestamps become aware and a missing last_modified is handled."""
        mock_api.return_value = [
            book_progress(
                "Naive",
                50,
                1,
                "isbn-naive",
                created_at="2024-03-01T09:00:00",
                last_modified=None,
            ),
        ]
        mock_search.side_effect = self._search_by_isbn({"isbn-naive": "50"})
        mock_metadata.side_effect = self._metadata(
            {
                "50": {
                    "media_id": "50",
                    "title": "Naive",
                    "image": "n.jpg",
                    "max_progress": 200,
                }
            },
        )

        imported_counts, _ = self._import()

        self.assertEqual(imported_counts[MediaTypes.BOOK.value], 1)
        book = Book.objects.get(item__title="Naive")
        self.assertEqual(book.status, Status.IN_PROGRESS.value)
        self.assertIsNotNone(book.start_date.tzinfo)

    def test_unexpected_api_response_raises(self, mock_metadata, mock_search, mock_api):
        """Test a non-list export payload raises an unexpected-error."""
        mock_api.return_value = {"unexpected": "shape"}

        with self.assertRaises(MediaImportUnexpectedError):
            self._import()
        mock_search.assert_not_called()
        mock_metadata.assert_not_called()

    def test_duplicate_match_imported_once(self, mock_metadata, mock_search, mock_api):
        """Test two entries resolving to the same book import a single row."""
        mock_api.return_value = [
            book_progress("First", 100, 1, "isbn-a"),
            book_progress("Second", 100, 2, "isbn-b"),
        ]
        mock_search.side_effect = self._search_by_isbn(
            {"isbn-a": "10", "isbn-b": "10"},
        )
        mock_metadata.side_effect = self._metadata(
            {"10": {"media_id": "10", "title": "First", "image": "f.jpg"}},
        )

        imported_counts, warnings = self._import()

        self.assertEqual(imported_counts[MediaTypes.BOOK.value], 1)
        self.assertEqual(Book.objects.filter(user=self.user).count(), 1)
        self.assertIn("Second (2)", warnings)
        self.assertIn("duplicated match", warnings)

    def test_dead_source_excluded_other_source_matches(
        self, mock_metadata, mock_search, mock_api
    ):
        """Test a hard-failing source is dropped while the other still matches."""
        mock_api.return_value = [book_progress("Book", 100, 1, "isbn-x")]
        server_error = services.ProviderAPIError(
            "hardcover",
            requests.HTTPError(response=SimpleNamespace(status_code=500, text="boom")),
        )

        def _search(_media_type, _query, _page, source):
            if source == Sources.HARDCOVER.value:
                raise server_error
            return {"results": [{"media_id": "10"}]}

        mock_search.side_effect = _search
        mock_metadata.side_effect = self._metadata(
            {"10": {"media_id": "10", "title": "Book", "image": "b.jpg"}},
        )

        imported_counts, warnings = self._import()

        self.assertEqual(imported_counts[MediaTypes.BOOK.value], 1)
        self.assertIn("Hardcover fatally errored", warnings)

    def test_all_sources_dead_aborts_import(self, mock_metadata, mock_search, mock_api):
        """Test the import aborts once every source has hard-failed."""
        mock_api.return_value = [book_progress("Book", 100, 1, "isbn-x")]
        mock_search.side_effect = services.ProviderAPIError(
            "hardcover",
            requests.HTTPError(response=SimpleNamespace(status_code=500, text="boom")),
        )

        with self.assertRaises(MediaImportError):
            self._import()
        mock_metadata.assert_not_called()

    def test_abort_still_flushes_already_processed_books(
        self, mock_metadata, mock_search, mock_api
    ):
        """Test a book queued before an aborting run is still persisted."""
        mock_api.return_value = [
            book_progress("Kept", 100, 1, "isbn-a"),
            book_progress("Lost", 100, 2, "isbn-b"),
        ]
        server_error = services.ProviderAPIError(
            "hardcover",
            requests.HTTPError(response=SimpleNamespace(status_code=500, text="boom")),
        )

        def _search(_media_type, query, _page, source):
            if query == "isbn-a":
                if source == Sources.HARDCOVER.value:
                    return {"results": [{"media_id": "10"}]}
                return {"results": []}
            raise server_error

        mock_search.side_effect = _search
        mock_metadata.side_effect = self._metadata(
            {"10": {"media_id": "10", "title": "Kept", "image": "k.jpg"}},
        )

        with self.assertRaises(MediaImportError):
            self._import()

        # the book matched before the abort survives the finally-flush
        self.assertTrue(Book.objects.filter(item__title="Kept").exists())

    def test_nonfatal_source_error_falls_through_to_other_source(
        self, mock_metadata, mock_search, mock_api
    ):
        """Test a non-fatal error on one source still tries the other source."""
        mock_api.return_value = [book_progress("Book", 100, 1, "isbn-x")]
        not_found = services.ProviderAPIError(
            "hardcover",
            requests.HTTPError(response=SimpleNamespace(status_code=404, text="nope")),
        )

        def _search(_media_type, _query, _page, source):
            if source == Sources.HARDCOVER.value:
                raise not_found
            return {"results": [{"media_id": "10"}]}

        mock_search.side_effect = _search
        mock_metadata.side_effect = self._metadata(
            {"10": {"media_id": "10", "title": "Book", "image": "b.jpg"}},
        )

        imported_counts, _ = self._import()

        # Hardcover 404 doesn't abandon the book; OpenLibrary still matches it
        self.assertEqual(imported_counts[MediaTypes.BOOK.value], 1)
        self.assertTrue(Book.objects.filter(item__title="Book").exists())

    def test_null_identifiers_and_authors_do_not_abort(
        self, mock_metadata, mock_search, mock_api
    ):
        """Test null identifiers/authors fall back to title without aborting."""
        entry = book_progress("Weird", 100, 1, "unused")
        entry["identifiers"] = None
        entry["authors"] = None
        mock_api.return_value = [entry]
        mock_search.side_effect = self._search_by_isbn({"Weird": "10"})
        mock_metadata.side_effect = self._metadata(
            {"10": {"media_id": "10", "title": "Weird", "image": "w.jpg"}},
        )

        imported_counts, _ = self._import()

        self.assertEqual(imported_counts[MediaTypes.BOOK.value], 1)
        self.assertTrue(Book.objects.filter(item__title="Weird").exists())

    def test_unknown_identifier_types_are_not_searched(
        self, mock_metadata, mock_search, mock_api
    ):
        """Test unknown identifier types are never used as search queries."""
        entry = book_progress("Book", 100, 1, "isbn-x")
        entry["identifiers"] = {"amazon": "B00XXXX", "isbn": "isbn-x"}
        mock_api.return_value = [entry]
        mock_search.side_effect = self._search_by_isbn({"isbn-x": "10"})
        mock_metadata.side_effect = self._metadata(
            {"10": {"media_id": "10", "title": "Book", "image": "b.jpg"}},
        )

        self._import()

        queried = [call.args[1] for call in mock_search.call_args_list]
        self.assertNotIn("B00XXXX", queried)
        self.assertIn("isbn-x", queried)

    def test_provider_error_logs_and_continues(
        self, mock_metadata, mock_search, mock_api
    ):
        """Test a non-404 provider error warns and doesn't abort the import."""
        mock_api.return_value = [
            book_progress("Broken", 100, 1, "isbn-broken"),
            book_progress("Good", 100, 2, "isbn-good"),
        ]
        mock_search.side_effect = self._search_by_isbn(
            {"isbn-broken": "70", "isbn-good": "80"},
        )

        server_error = services.ProviderAPIError(
            "hardcover",
            requests.HTTPError(response=SimpleNamespace(status_code=500, text="boom")),
        )

        def _get(_media_type, media_id, _source):
            if str(media_id) == "70":
                raise server_error
            return {
                "media_id": "80",
                "title": "Good",
                "image": "g.jpg",
                "max_progress": 300,
            }

        mock_metadata.side_effect = _get

        imported_counts, warnings = self._import()

        # the good book still imported, the broken one only warned about
        self.assertEqual(imported_counts[MediaTypes.BOOK.value], 1)
        self.assertEqual(Book.objects.filter(user=self.user).count(), 1)
        self.assertTrue(Book.objects.filter(item__title="Good").exists())
        self.assertIn("Broken (1)", warnings)


class GetBooksProgressErrors(TestCase):
    """Test error handling while fetching the export from the API."""

    def setUp(self):
        """Create user and encrypted credentials for the tests."""
        self.user = get_user_model().objects.create_user(
            username="test",
            password="12345",  # noqa: S106
        )
        self.password_token = helpers.encrypt("secret")

    def _import(self):
        return calibre_web_nextgen.importer(
            "admin",
            self.user,
            "new",
            "http://localhost:8083",
            self.password_token,
        )

    @patch("integrations.imports.calibre_web_nextgen.services.api_request")
    def _assert_raises_import_error(self, side_effect, mock_api, match=None):
        mock_api.side_effect = side_effect
        with self.assertRaisesMessage(MediaImportError, match or ""):
            self._import()

    def test_auth_error_is_invalid_credentials(self):
        """Test 401 and 403 map to an invalid-credentials error."""
        for status_code in (401, 403):
            with self.subTest(status_code=status_code):
                error = requests.HTTPError(
                    response=SimpleNamespace(status_code=status_code)
                )
                self._assert_raises_import_error(error, match="Invalid credentials.")

    def test_non_auth_http_error_is_reported(self):
        """Test non-auth HTTP errors, with or without a response attached."""
        cases = [
            requests.HTTPError(response=SimpleNamespace(status_code=500)),
            requests.HTTPError(),  # HTTPError carrying no response
        ]
        for error in cases:
            with self.subTest(error=error):
                self._assert_raises_import_error(
                    error, match="Calibre-Web-NextGen API error"
                )

    def test_connection_error_is_reported(self):
        """Test an unreachable instance maps to a friendly import error."""
        self._assert_raises_import_error(
            requests.exceptions.ConnectionError("refused"),
            match="Error connecting to Calibre-Web-NextGen instance",
        )


class ProgressStatusFromDate(TestCase):
    """Test status derived from the last update date."""

    def test_status_thresholds(self):
        """Test recent stays in progress, 60d pauses, 90d drops."""
        cases = [
            (5, Status.IN_PROGRESS.value),
            (70, Status.PAUSED.value),
            (100, Status.DROPPED.value),
        ]
        importer_cls = calibre_web_nextgen.CalibreWebNextGenImporter
        for days_ago, expected in cases:
            with self.subTest(days_ago=days_ago):
                last_updated = datetime.now(UTC) - timedelta(days=days_ago)
                self.assertEqual(
                    importer_cls._get_progress_status(last_updated), expected
                )


@patch("integrations.imports.calibre_web_nextgen.services.api_request")
@patch("integrations.imports.calibre_web_nextgen.services.search")
@patch("integrations.imports.calibre_web_nextgen.services.get_media_metadata")
class ImportCalibreWebNextGenExisting(TestCase):
    """Test behavior when a matched book already exists for the user."""

    def setUp(self):
        """Create user, credentials and an existing in-progress book."""
        self.user = get_user_model().objects.create_user(
            username="test",
            password="12345",  # noqa: S106
        )
        self.password_token = helpers.encrypt("secret")

        self.item = Item.objects.create(
            media_id="55",
            source=Sources.HARDCOVER.value,
            media_type=MediaTypes.BOOK.value,
            title="Existing",
            image="e.jpg",
        )
        self.book = Book.objects.create(
            item=self.item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=10,
            notes="Imported from Calibre-Web-NextGen",
        )

    def _setup_mocks(self, mock_metadata, mock_search, mock_api):
        mock_api.return_value = [book_progress("Existing", 100, 1, "isbn-existing")]
        mock_search.return_value = {"results": [{"media_id": "55"}]}
        mock_metadata.return_value = {
            "media_id": "55",
            "title": "Existing",
            "image": "e.jpg",
            "max_progress": 300,
        }

    def _import(self, mode):
        return calibre_web_nextgen.importer(
            "admin",
            self.user,
            mode,
            "http://localhost:8083",
            self.password_token,
        )

    def test_new_mode_skips_existing(
        self,
        mock_metadata,
        mock_search,
        mock_api,
    ):
        """Test new mode leaves an existing book untouched (add-only)."""
        self._setup_mocks(mock_metadata, mock_search, mock_api)

        imported_counts, _ = self._import("new")

        self.book.refresh_from_db()
        self.assertEqual(imported_counts, {})
        self.assertEqual(self.book.status, Status.IN_PROGRESS.value)
        self.assertEqual(self.book.progress, 10)
        self.assertEqual(self.book.history.count(), 1)

    def test_overwrite_mode_updates_existing_in_place(
        self,
        mock_metadata,
        mock_search,
        mock_api,
    ):
        """Test overwrite updates the existing row in place, keeping its history."""
        self._setup_mocks(mock_metadata, mock_search, mock_api)

        imported_counts, _ = self._import("overwrite")

        self.book.refresh_from_db()
        self.assertEqual(imported_counts[MediaTypes.BOOK.value], 1)
        self.assertEqual(Book.objects.filter(user=self.user).count(), 1)
        # same row, not a delete+recreate
        self.assertEqual(self.book.pk, Book.objects.get(user=self.user).pk)
        self.assertEqual(self.book.status, Status.COMPLETED.value)
        self.assertEqual(self.book.progress, 300)
        self.assertEqual(self.book.history.count(), 2)

    def test_overwrite_mode_no_change_is_not_requeued(
        self,
        mock_metadata,
        mock_search,
        mock_api,
    ):
        """Test an unchanged existing book is left untouched."""
        self._setup_mocks(mock_metadata, mock_search, mock_api)
        # Bypass save() to avoid the metadata-based progress clamp and history.
        # Dates must match what the import derives, or the book is not unchanged.
        Book.objects.filter(pk=self.book.pk).update(
            progress=300,
            status=Status.COMPLETED.value,
            start_date=datetime(2024, 1, 1, 9, tzinfo=UTC),
            end_date=datetime(2024, 6, 1, 9, tzinfo=UTC),
        )

        imported_counts, _ = self._import("overwrite")

        self.book.refresh_from_db()
        self.assertEqual(imported_counts, {})
        self.assertEqual(self.book.history.count(), 1)

    def test_completed_frozen_on_stale_or_reset_export(
        self,
        mock_metadata,
        mock_search,
        mock_api,
    ):
        """Test a stale or reset export leaves a completed book untouched."""
        self._setup_mocks(mock_metadata, mock_search, mock_api)
        kept_start = datetime(2020, 1, 1, tzinfo=UTC)
        kept_end = datetime(2020, 2, 1, tzinfo=UTC)
        Book.objects.filter(pk=self.book.pk).update(
            status=Status.COMPLETED.value,
            progress=300,
            start_date=kept_start,
            end_date=kept_end,
        )
        stale = (datetime.now(UTC) - timedelta(days=100)).isoformat()
        # would-be status once stale (>=90d) or reset (0%): dropped, planning
        cases = [50, 0]
        for percentage in cases:
            with self.subTest(percentage=percentage):
                mock_api.return_value = [
                    book_progress(
                        "Existing", percentage, 1, "isbn-existing", last_modified=stale
                    ),
                ]

                imported_counts, _ = self._import("overwrite")

                self.book.refresh_from_db()
                # nothing changes: status, progress and dates are all held
                self.assertEqual(imported_counts, {})
                self.assertEqual(self.book.status, Status.COMPLETED.value)
                self.assertEqual(self.book.progress, 300)
                self.assertEqual(self.book.start_date, kept_start)
                self.assertEqual(self.book.end_date, kept_end)
                self.assertEqual(self.book.history.count(), 1)

    def test_completed_reread_recent_progress_updates(
        self,
        mock_metadata,
        mock_search,
        mock_api,
    ):
        """Test recent activity on a completed book is treated as a re-read."""
        self._setup_mocks(mock_metadata, mock_search, mock_api)
        Book.objects.filter(pk=self.book.pk).update(
            status=Status.COMPLETED.value,
            progress=300,
            end_date=datetime(2020, 2, 1, tzinfo=UTC),
        )
        recent = datetime.now(UTC).isoformat()
        mock_api.return_value = [
            book_progress("Existing", 50, 1, "isbn-existing", last_modified=recent),
        ]

        imported_counts, _ = self._import("overwrite")

        self.book.refresh_from_db()
        # recent in-progress export isn't a regression, so it syncs through
        self.assertEqual(imported_counts[MediaTypes.BOOK.value], 1)
        self.assertEqual(self.book.status, Status.IN_PROGRESS.value)
        self.assertEqual(self.book.progress, 150)
        # a re-read is no longer completed, so the stale end_date is cleared
        self.assertIsNone(self.book.end_date)

    def test_existing_dates_not_wiped_by_null_export(
        self,
        mock_metadata,
        mock_search,
        mock_api,
    ):
        """Test a null export date doesn't clear an existing start/end date."""
        self._setup_mocks(mock_metadata, mock_search, mock_api)
        kept_start = datetime(2020, 1, 1, tzinfo=UTC)
        Book.objects.filter(pk=self.book.pk).update(
            start_date=kept_start,
            end_date=datetime(2020, 2, 1, tzinfo=UTC),
        )
        mock_api.return_value = [
            book_progress("Existing", 100, 1, "isbn-existing", created_at=None),
        ]

        self._import("overwrite")

        self.book.refresh_from_db()
        # missing created_at keeps the old start_date; end_date still updates
        self.assertEqual(self.book.start_date, kept_start)
        self.assertEqual(self.book.end_date, datetime(2024, 6, 1, 9, tzinfo=UTC))
