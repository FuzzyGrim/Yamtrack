import logging
from collections import defaultdict
from csv import DictReader
from datetime import UTC, datetime

from django.apps import apps
from django.utils import timezone

import app
import app.providers
from app.models import MediaTypes, Sources, Status
from integrations.imports import helpers
from integrations.imports.helpers import MediaImportError, MediaImportUnexpectedError

logger = logging.getLogger(__name__)


def importer(file, user, mode):
    """Import media from CSV file."""
    hltb_importer = HowLongToBeatImporter(file, user, mode)
    return hltb_importer.import_data()


class HowLongToBeatImporter:
    """Class to handle importing user data from HowLongToBeat CSV."""

    def __init__(self, file, user, mode):
        """Initialize the importer with file, user, and mode.

        Args:
            file: Uploaded CSV file
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
            "Initialized HowLongToBeat importer for user %s with mode %s",
            user.username,
            mode,
        )

    def import_data(self):
        """Import all user data from CSV."""
        try:
            decoded_file = self.file.read().decode("utf-8").splitlines()
        except UnicodeDecodeError as e:
            msg = "Invalid file format. Please upload a CSV file."
            raise MediaImportError(msg) from e

        reader = DictReader(decoded_file)
        rows = list(reader)

        # Track media IDs and their titles from the import file
        media_id_counts = defaultdict(int)
        media_id_titles = defaultdict(list)

        # First pass: identify duplicates
        for row in rows:
            try:
                self._process_first_pass(row, media_id_counts, media_id_titles)
            except Exception as error:
                error_msg = f"Error processing entry: {row}"
                raise MediaImportUnexpectedError(error_msg) from error

        # Second pass: add non-duplicates to bulk_media
        for row in rows:
            try:
                self._process_second_pass(row, media_id_counts)
            except Exception as error:
                error_msg = f"Error processing entry: {row}"
                raise MediaImportUnexpectedError(error_msg) from error

        # Add consolidated warnings for duplicates
        self._add_duplicate_warnings(media_id_counts, media_id_titles)

        helpers.cleanup_existing_media(self.to_delete, self.user)
        helpers.bulk_create_media(self.bulk_media, self.user)

        imported_counts = {
            media_type: len(media_list)
            for media_type, media_list in self.bulk_media.items()
        }

        deduplicated_messages = "\n".join(dict.fromkeys(self.warnings))
        return imported_counts, deduplicated_messages if self.warnings else None

    def _process_first_pass(self, row, media_id_counts, media_id_titles):
        """First pass to identify duplicate games."""
        game = self._search_game(row)
        if not game:
            self.warnings.append(
                f"{row['Title']}: Couldn't find a game with this title in "
                f"{Sources.IGDB.label}",
            )
            return

        media_id = game["media_id"]
        media_id_counts[media_id] += 1
        media_id_titles[media_id].append(row["Title"])

    def _process_second_pass(self, row, media_id_counts):
        """Second pass to process non-duplicate games."""
        game = self._search_game(row)
        if not game:
            return  # Already added warning in first pass

        media_id = game["media_id"]

        # Skip if this media_id appears more than once
        if media_id_counts[media_id] > 1:
            return

        item, _ = self._create_or_update_item(game)

        # Check if we should process this entry based on mode
        if not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            MediaTypes.GAME.value,
            Sources.IGDB.value,
            str(media_id),
            self.mode,
        ):
            return

        instance = self._create_media_instance(item, row)
        self.bulk_media[MediaTypes.GAME.value].append(instance)

    def _add_duplicate_warnings(self, media_id_counts, media_id_titles):
        """Add warnings for duplicate games."""
        for media_id, count in media_id_counts.items():
            if count > 1:
                titles = media_id_titles[media_id]
                title_list = helpers.join_with_commas_and(titles)
                self.warnings.append(
                    f"{title_list}: They were matched to the same ID {media_id} "
                    "- none imported",
                )

    def _format_time(self, time):
        """Convert time from text to minutes.

        Could be '--' or '' or '8:35:30', '46:30' or '32'.
        """
        if time == "--":
            return None
        if time == "":
            return 0

        parts = time.split(":")
        if len(parts) == 3:  # format: '8:35:30' # noqa: PLR2004
            hours, minutes, seconds = parts
            return int(hours) * 60 + int(minutes) + round(int(seconds) / 60)
        if len(parts) == 2:  # format: '46:30' # noqa: PLR2004
            minutes, seconds = parts
            return int(minutes) + round(int(seconds) / 60)
        # format: '32' secs
        return round(int(time) / 60)

    def _search_game(self, row):
        """Search for game and return result if found."""
        results = app.providers.services.search(
            MediaTypes.GAME.value,
            row["Title"],
            1,
        ).get(
            "results",
            [],
        )
        if not results:
            return None
        return results[0]

    def _create_or_update_item(self, game):
        """Create or update the item in database."""
        media_type = MediaTypes.GAME.value
        return app.models.Item.objects.update_or_create(
            media_id=game["media_id"],
            source=Sources.IGDB.value,
            media_type=media_type,
            title=game["title"],
            defaults={
                "title": game["title"],
                "image": game["image"],
            },
        )

    def _format_notes(self, row):
        """Format all notes with prefixes."""
        notes_mapping = {
            "General": row["General Notes"],
            "Review": row["Review Notes"],
            "Main Story": row["Main Story Notes"],
            "Main + Extras": row["Main + Extras Notes"],
            "Completionist": row["Completionist Notes"],
        }

        formatted_notes = [
            f"{prefix}: {text}"
            for prefix, text in notes_mapping.items()
            if text.strip()
        ]

        return "\n".join(formatted_notes)

    def _determine_status(self, row):
        """Determine media status based on row data."""
        status_mapping = {
            "Completed": Status.COMPLETED,
            "Playing": Status.IN_PROGRESS,
            "Backlog": Status.PLANNING,
            "Replay": Status.IN_PROGRESS,
            "Retired": Status.DROPPED,
        }

        for field, status in status_mapping.items():
            if row[field] == "X":
                return status.value

        return Status.COMPLETED.value

    def _parse_hltb_date(self, date_str):
        """Parse HLTB date string (YYYY-MM-DD) into datetime object."""
        if not date_str:
            return None

        return datetime.strptime(date_str, "%Y-%m-%d").replace(
            hour=0,
            minute=0,
            second=0,
            tzinfo=timezone.get_current_timezone(),
        )

    def _create_media_instance(self, item, row):
        """Create media instance with all parameters."""
        progress = self._format_time(row["Progress"])
        main_story = self._format_time(row["Main Story"])
        main_extra = self._format_time(row["Main + Extras"])
        completionist = self._format_time(row["Completionist"])

        model = apps.get_model(app_label="app", model_name=MediaTypes.GAME.value)
        updated_at = datetime.strptime(
            row["Updated"],
            "%Y-%m-%d %H:%M:%S",
        ).replace(
            tzinfo=UTC,
        )

        instance = model(
            item=item,
            user=self.user,
            score=int(row["Review"]) / 10,
            progress=max(
                [
                    x
                    for x in [progress, main_story, main_extra, completionist]
                    if x is not None
                ],
                default=0,
            ),
            status=self._determine_status(row),
            start_date=self._parse_hltb_date(row["Start Date"]),
            end_date=self._parse_hltb_date(row["Completion Date"]),
            notes=self._format_notes(row),
        )
        instance._history_date = updated_at
        return instance
