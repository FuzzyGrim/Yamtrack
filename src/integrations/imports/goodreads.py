import logging
from collections import defaultdict
from csv import DictReader
from datetime import datetime

from django.apps import apps
from django.utils import timezone

import app
from app.models import MediaTypes, Sources, Status
from app.providers import services
from integrations.imports import helpers
from integrations.imports.helpers import MediaImportError, MediaImportUnexpectedError

logger = logging.getLogger(__name__)


def importer(file, user, mode):
    """Import media from CSV file using the class-based importer."""
    csv_importer = GoodReadsImporter(file, user, mode)
    return csv_importer.import_data()


class GoodReadsImporter:
    """Class to handle importing goodreads data from CSV files."""

    def __init__(self, file, user, mode):
        """Initialize the importer with file, user, and mode.

        Args:
            file: Uploaded CSV file object
            user: Django user object to import data for
            mode (str): Import mode ("new" or "overwrite")
        """
        self.file = file
        self.user = user
        self.mode = mode
        self.warnings = []

        # Track existing media for "new" mode
        self.existing_media = helpers.get_existing_media(user)

        # Track media IDs to delete in overwrite mode
        self.to_delete = defaultdict(lambda: defaultdict(set))

        # Track bulk creation lists for each media type
        self.bulk_media = defaultdict(list)

        logger.info(
            "Initialized GoodReads CSV importer for user %s with mode %s",
            user.username,
            mode,
        )

    def import_data(self):
        """Import all GoodReads data from the CSV file."""
        try:
            decoded_file = self.file.read().decode("utf-8").splitlines()
        except UnicodeDecodeError as e:
            msg = "Invalid file format. Please upload a CSV file."
            raise MediaImportError(msg) from e

        reader = DictReader(decoded_file)

        for row in reader:
            try:
                self._process_row(row)
            except services.ProviderAPIError:
                error_msg = f"Error processing entry with ID {row['media_id']} "
                self.warnings.append(error_msg)
                continue
            except Exception as error:
                error_msg = f"Error processing entry: {row}"
                raise MediaImportUnexpectedError(error_msg) from error

        logger.debug("processed %s", self.bulk_media)

        helpers.cleanup_existing_media(self.to_delete, self.user)
        helpers.bulk_create_media(self.bulk_media, self.user)

        logger.debug("processed %s", self.bulk_media)

        imported_counts = {
            media_type: len(media_list)
            for media_type, media_list in self.bulk_media.items()
        }

        deduplicated_messages = "\n".join(dict.fromkeys(self.warnings))
        return imported_counts, deduplicated_messages

    def _process_row(self, row):
        """Process a single row from the CSV file."""
        book = self._search_book(row, Sources.HARDCOVER)

        if not book:
            self.warnings.append(
                f"{row['Title']}: Couldn't find this book via Title or ISBN in "
                f"{Sources.HARDCOVER.label}",
            )
            return

        logger.debug("Found book %s", book)

        media_id = book["media_id"]

        item, _ = self._create_or_update_item(book)

        # Check if we should process this entry based on mode
        if not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            MediaTypes.BOOK.value,
            Sources.HARDCOVER.value,
            str(media_id),
            self.mode,
        ):
            return

        instance = self._create_media_instance(item, row)
        self.bulk_media[MediaTypes.BOOK.value].append(instance)

    def _search_book(self, row, source):
        """Search for game and return result if found."""
        results = services.search(
            MediaTypes.BOOK.value,
            row["ISBN"],
            1,
            source.value,
        ).get(
            "results",
            [],
        )
        if results:
            return results[0]

        results = services.search(
            MediaTypes.BOOK.value,
            row["Title"],
            1,
            source.value,
        ).get(
            "results",
            [],
        )

        if not results:
            return None
        return results[0]

    def _create_or_update_item(self, book):
        """Create or update the item in database."""
        media_type = MediaTypes.BOOK.value
        return app.models.Item.objects.update_or_create(
            media_id=book["media_id"],
            source=Sources.HARDCOVER.value,
            media_type=media_type,
            title=book["title"],
            defaults={
                "title": book["title"],
                "image": book["image"],
            },
        )

    def _determine_status(self, row):
        status_mapping = {
            "read": Status.COMPLETED,
            "currently-reading": Status.IN_PROGRESS,
            "to-read": Status.PLANNING,
        }

        return status_mapping[row["Exclusive Shelf"]].value

    def _parse_goodreads_date(self, date_str):
        """Parse GoodReads date string (YYYY/MM/DD) into datetime object."""
        if not date_str:
            return None

        return datetime.strptime(date_str, "%Y/%m/%d").replace(
            hour=0,
            minute=0,
            second=0,
            tzinfo=timezone.get_current_timezone(),
        )

    def _create_media_instance(self, item, row):
        """Create media instance with all parameters."""
        model = apps.get_model(app_label="app", model_name=MediaTypes.BOOK.value)
        book_status = self._determine_status(row)
        book_progress = (
            int(row["Number of Pages"])
            if book_status is Status.COMPLETED.value
            and row["Number of Pages"].isnumeric()
            else 0
        )

        # Parse dates
        date_created = self._parse_goodreads_date(row.get("Date Added", ""))
        date_rated = self._parse_goodreads_date(row.get("Date Read", ""))

        # filter out None dates
        dates = [date_created, date_rated]
        most_recent_date = max(date for date in dates if date)

        instance = model(
            item=item,
            user=self.user,
            score=None if row["My Rating"] == "0" else int(row["My Rating"]) * 2,
            progress=book_progress,
            status=book_status,
            end_date=date_rated,
            notes=row["Private Notes"],
        )
        instance._history_date = most_recent_date or timezone.now()

        return instance
