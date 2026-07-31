import logging
from base64 import b64encode
from collections import defaultdict
from datetime import UTC

import requests
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from app.models import Book, Item, MediaTypes, Sources, Status
from app.providers import services
from integrations.imports import helpers

logger = logging.getLogger(__name__)


DAYS_UNTIL_PAUSED = 60
DAYS_UNTIL_DROPPED = 90
MIN_COMPLETED_PERCENTAGE = 99
MIN_IN_PROGRESS_PERCENTAGE = 1
SOURCES = [Sources.HARDCOVER, Sources.OPENLIBRARY]
IDENTIFIERS_PRIORITY = {
    value: index
    for index, value in enumerate(["hardcover-id", "isbn", "isbn13", "isbn10"])
}
DIRECT_ID_SOURCES = {"hardcover-id": Sources.HARDCOVER}


class BookNotFoundError(Exception):
    """Raised when a book can't be matched in any configured source."""


def importer(username, user, mode, url, encrypted_password):
    """Import the user's books and progress from Calibre-Web-NextGen."""
    calibre_web_nextgen_importer = CalibreWebNextGenImporter(
        username, user, mode, url, encrypted_password
    )
    return calibre_web_nextgen_importer.import_data()


class CalibreWebNextGenImporter:
    """Class to handle importing books and progress data from Calibre-Web-NextGen."""

    def __init__(self, username, user, mode, url, encrypted_password):
        """Initialize the importer with user details and mode.

        Args:
            username (str): Username to import data from
            user: Django user object to import data for
            mode (str): Import mode ("new" or "overwrite")
            url (str): Calibre-Web-NextGen instance url
            encrypted_password (str): Encrypted authentication password
        """
        credentials = f"{username}:{helpers.decrypt(encrypted_password)}".encode()
        authorization = f"Basic {b64encode(credentials).decode('ascii')}"

        self.existing_media = helpers.get_existing_media(user)
        self.to_delete = defaultdict(lambda: defaultdict(set))
        self.processed_media = defaultdict(lambda: defaultdict(set))
        self.bulk_media = defaultdict(list)
        self.bulk_media_updates = defaultdict(list)
        self.failed_sources = set()
        self.warnings = []

        self.user = user
        self.mode = mode
        self.url = url
        self.headers = {"Authorization": authorization}

        logger.info(
            "Initialized Calibre-Web-NextGen API importer for user %s with mode %s",
            user.username,
            mode,
        )

    def import_data(self):
        """Import user's Calibre-Web-NextGen books and progress."""
        books_progress = self._get_books_progress()

        if not isinstance(books_progress, list):
            msg = "Calibre-Web-NextGen API returned an unexpected result."
            raise helpers.MediaImportUnexpectedError(msg)

        logger.info(
            "Fetched entries Calibre-Web-NextGen: %s. Starting processing.",
            len(books_progress),
        )

        try:
            for book_progress in books_progress:
                self._process_book_progress(book_progress)
        finally:
            # On fatal error keep partial progress
            helpers.cleanup_existing_media(self.to_delete, self.user)
            helpers.bulk_create_media(self.bulk_media, self.user)
            helpers.bulk_update_media(
                self.bulk_media_updates,
                {
                    MediaTypes.BOOK.value: [
                        "progress",
                        "status",
                        "start_date",
                        "end_date",
                    ]
                },
                self.user,
            )

        created_books = len(self.bulk_media[MediaTypes.BOOK.value])
        updated_books = len(self.bulk_media_updates[MediaTypes.BOOK.value])
        imported_counts = {}
        if created_books or updated_books:
            imported_counts[MediaTypes.BOOK.value] = created_books + updated_books

        logger.info(
            "Calibre-Web-NextGen import completed for user %s: %s",
            self.user.username,
            imported_counts,
        )

        return imported_counts, "\n".join(self.warnings) if self.warnings else ""

    def _get_books_progress(self):
        """Fetch books progress from Calibre-Web-NextGen API."""
        try:
            return services.api_request(
                "Calibre-Web-NextGen",
                "GET",
                f"{self.url.rstrip('/')}/kosync/export",
                headers=self.headers,
            )

        except requests.HTTPError as error:
            status_code = getattr(error.response, "status_code", None)
            if status_code in (requests.codes.forbidden, requests.codes.unauthorized):
                msg = "Invalid credentials."
                raise helpers.MediaImportError(msg) from error
            msg = f"Calibre-Web-NextGen API error: {status_code}"
            raise helpers.MediaImportError(msg) from error

        except (
            requests.exceptions.InvalidURL,
            requests.exceptions.InvalidSchema,
        ) as error:
            msg = "Invalid Calibre-Web-NextGen instance URL."
            raise helpers.MediaImportError(msg) from error

        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        ) as error:
            msg = f"Error connecting to Calibre-Web-NextGen instance: {error!s}"
            raise helpers.MediaImportError(msg) from error

    def _process_book_progress(self, book_progress):
        """Process a single book from Calibre-Web-NextGen API response."""
        logger.debug(
            "Processing %s (calibre_book_id: %s)",
            book_progress.get("title"),
            book_progress.get("calibre_book_id"),
        )
        try:
            media_id, source = self._search_book(book_progress)

            processed = self.processed_media[MediaTypes.BOOK.value][source.value]
            if media_id in processed:
                title = book_progress.get("title")
                calibre_book_id = book_progress.get("calibre_book_id")
                logger.debug(
                    "Duplicated Calibre-Web-NextGen match skipped %s (%s). "
                    "Source: %s - media_id: %s",
                    title,
                    calibre_book_id,
                    source.value,
                    media_id,
                )
                self.warnings.append(
                    f"{title} ({calibre_book_id}): duplicated match, skipped.",
                )
                return
            processed.add(media_id)

            book_metadata = services.get_media_metadata(
                MediaTypes.BOOK.value,
                media_id,
                source.value,
            )

            should_process = helpers.should_process_media(
                self.existing_media,
                self.to_delete,
                MediaTypes.BOOK.value,
                source.value,
                str(book_metadata.get("media_id")),
                self.mode,
            )

            if should_process:
                self._queue_create_book(book_metadata, book_progress, source)
            else:
                self._queue_existing_book_update(book_metadata, book_progress, source)

        except (BookNotFoundError, services.ProviderAPIError) as error:
            title = book_progress.get("title")
            calibre_book_id = book_progress.get("calibre_book_id")

            # BookNotFoundError: local no search match
            # ProviderAPIError 404: media_id not found on provider
            # Both mean the book or metadata aren't found so log and continue
            not_found = isinstance(error, BookNotFoundError) or (
                error.status_code == requests.codes.not_found
            )
            if not_found:
                sources = ", ".join(source.label for source in SOURCES)
                logger.debug(
                    "Skipping Calibre-Web-NextGen book %s (calibre_book_id: %s). "
                    "No match found in: %s",
                    title,
                    calibre_book_id,
                    sources,
                )
                self.warnings.append(
                    f"{title} ({calibre_book_id}): "
                    f"no match found in sources ({sources})",
                )
                return

            # log and continue on any other provider error so one bad book
            # doesn't abort the whole import
            logger.warning(
                "Error processing Calibre-Web-NextGen book %s (%s): %s",
                title,
                calibre_book_id,
                error,
            )
            self.warnings.append(f"{title} ({calibre_book_id}): {error!s}")

        except (ValueError, KeyError, TypeError) as error:
            title = book_progress.get("title")
            calibre_book_id = book_progress.get("calibre_book_id")
            logger.exception(
                "Failed to process Calibre-Web-NextGen book %s (%s)",
                title,
                calibre_book_id,
            )
            self.warnings.append(f"{title} ({calibre_book_id}): {error!s}")

    def _search_book(self, book_progress):
        """Search book via various identifiers with search services."""
        identifiers = book_progress.get("identifiers") or {}
        # book identifiers are filtered to avoid id clashes then sorted by priority
        sorted_identifiers = sorted(
            (
                (identifier_type, value)
                for identifier_type, value in identifiers.items()
                if identifier_type in IDENTIFIERS_PRIORITY
            ),
            key=lambda identifier: IDENTIFIERS_PRIORITY[identifier[0]],
        )

        title = book_progress.get("title", "")
        authors = book_progress.get("authors") or []
        if len(authors) > 0:
            for index, author in enumerate(authors):
                identifier_key = f"book_title_author_{index + 1}"
                identifier_value = f"{title} - {author}"
                sorted_identifiers.append((identifier_key, identifier_value))
        else:
            sorted_identifiers.append(("book_title", title))

        for identifier_type, identifier_value in sorted_identifiers:
            for source in SOURCES:
                # Skip sources that fatally failed on this import
                # (e.g. missing HARDCOVER_TOKEN in config)
                if source in self.failed_sources:
                    continue
                try:
                    media_id = self._match_media_id(
                        identifier_type, identifier_value, source
                    )
                except services.ProviderAPIError as error:
                    if self._is_fatal_source_error(error):
                        self._handle_source_failure(source, error)
                    else:
                        logger.debug(
                            "Calibre-Web-NextGen search error on %s for %s (%s), "
                            "trying next: %s",
                            source.label,
                            identifier_value,
                            identifier_type,
                            error,
                        )
                    continue
                if media_id is not None:
                    logger.debug(
                        "Match found - source: %s, identifier: %s (%s)",
                        source.label,
                        identifier_value,
                        identifier_type,
                    )
                    return media_id, source
        raise BookNotFoundError

    @staticmethod
    def _match_media_id(identifier_type, identifier_value, source):
        """Resolve an identifier to a media_id on a source, or None if no match."""
        # Shortcut for books that have direct id on matching source,
        # doesn't try to overfit source specific id on other sources
        direct_source = DIRECT_ID_SOURCES.get(identifier_type)
        if direct_source is not None:
            if source != direct_source:
                return None
            metadata = services.get_media_metadata(
                MediaTypes.BOOK.value, identifier_value, source.value
            )
            return str(metadata.get("media_id"))
        results = services.search(
            MediaTypes.BOOK.value, identifier_value, 1, source.value
        ).get("results", [])
        return str(results[0].get("media_id")) if results else None

    def _handle_source_failure(self, source, error):
        """Mark a fatally-errored source dead for this run, abort if all errored."""
        logger.warning(
            "Calibre-Web-NextGen source '%s' fatally errored, excluded for this run. "
            "Error: %s",
            source.label,
            error,
        )
        self.warnings.append(f"{source.label} fatally errored: {error}")
        self.failed_sources.add(source)

        if all(candidate in self.failed_sources for candidate in SOURCES):
            msg = "All book sources unavailable or received fatal error."
            raise helpers.MediaImportError(msg) from error

    @staticmethod
    def _is_fatal_source_error(error):
        """Check if fatal source error."""
        status_code = error.status_code
        return (
            status_code is None
            or status_code >= requests.codes.server_error
            or status_code in (requests.codes.unauthorized, requests.codes.forbidden)
        )

    def _queue_create_book(self, book_metadata, book_progress, source):
        """Create book instance and queue it for creation."""
        progress, status, last_updated, start_date, end_date = (
            self._get_book_progress_status(book_metadata, book_progress)
        )

        item, _ = Item.objects.update_or_create(
            media_id=str(book_metadata.get("media_id")),
            source=source.value,
            media_type=MediaTypes.BOOK.value,
            defaults={
                "title": book_metadata.get("title"),
                "image": book_metadata.get("image"),
            },
        )

        book = Book(
            item=item,
            user=self.user,
            status=status,
            score=None,
            progress=progress,
            notes="Imported from Calibre-Web-NextGen",
            start_date=start_date,
            end_date=end_date,
        )
        book._history_date = last_updated

        self.bulk_media[MediaTypes.BOOK.value].append(book)

    def _queue_existing_book_update(self, book_metadata, book_progress, source):
        """Check for updated metadata and eventually queue book for update."""
        changed = False

        progress, status, last_updated, start_date, end_date = (
            self._get_book_progress_status(book_metadata, book_progress)
        )

        existing_book = self.existing_media[MediaTypes.BOOK.value][source.value].get(
            str(book_metadata.get("media_id"))
        )

        prevent_progress_regression = (
            existing_book.status == Status.COMPLETED
            and status
            in (Status.PLANNING.value, Status.PAUSED.value, Status.DROPPED.value)
        )

        if existing_book.progress != progress and not prevent_progress_regression:
            existing_book.progress = progress
            changed = True

        # Check for updated status but prevent setting PLANNED/PAUSED/DROPPED
        # on a (manually or otherwise) completed book.
        # IN_PROGRESS change is kept to handle re-reads.
        if existing_book.status != status and not prevent_progress_regression:
            existing_book.status = status
            changed = True

        # Check for updated status but prevent wiping existing start_date
        if (
            existing_book.start_date != start_date
            and not (existing_book.start_date is not None and start_date is None)
            and not prevent_progress_regression
        ):
            existing_book.start_date = start_date
            changed = True

        if existing_book.end_date != end_date and not prevent_progress_regression:
            existing_book.end_date = end_date
            changed = True

        if changed:
            existing_book._history_date = last_updated
            self.bulk_media_updates[MediaTypes.BOOK.value].append(existing_book)
            logger.debug(
                "Queued Calibre-Web-NextGen update for existing book: %s", existing_book
            )

    def _get_book_progress_status(self, book_metadata, book_progress):
        """Determine book progress, status, last_updated, start_date and end_date."""
        progress_percentage = book_progress.get("percentage") or 0
        created_at = self._parse_datetime(book_progress.get("created_at"))
        last_updated = (
            self._parse_datetime(book_progress.get("last_modified")) or timezone.now()
        )

        if progress_percentage >= MIN_COMPLETED_PERCENTAGE:
            return (
                book_metadata.get("max_progress") or 1,
                Status.COMPLETED.value,
                last_updated,
                created_at,
                last_updated,
            )

        if progress_percentage >= MIN_IN_PROGRESS_PERCENTAGE:
            status = self._get_progress_status(last_updated)
            pages_progress = round(
                progress_percentage * (book_metadata.get("max_progress") or 1) / 100
            )
            return (
                max(pages_progress, 1),
                status,
                last_updated,
                created_at,
                None,
            )
        return 0, Status.PLANNING.value, last_updated, None, None

    @staticmethod
    def _get_progress_status(last_updated):
        """Determine book status from last updated date."""
        last_updated_delta = timezone.now() - last_updated

        if last_updated_delta.days >= DAYS_UNTIL_DROPPED:
            return Status.DROPPED.value

        if last_updated_delta.days >= DAYS_UNTIL_PAUSED:
            return Status.PAUSED.value
        return Status.IN_PROGRESS.value

    @staticmethod
    def _parse_datetime(value):
        """Parse datetime with timezone awareness."""
        if not value:
            return None
        parsed_datetime = parse_datetime(value)
        if parsed_datetime and timezone.is_naive(parsed_datetime):
            # Calibre-Web-NextGen stores and sends only UTC datetime
            parsed_datetime = timezone.make_aware(parsed_datetime, UTC)
        return parsed_datetime
