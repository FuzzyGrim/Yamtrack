import json
import logging
from collections import defaultdict
from csv import DictReader
from io import StringIO

from django import forms
from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.dateparse import parse_datetime

import app
from app import config, stage
from app.models import MediaTypes, Sources
from app.providers import commons, services
from app.templatetags import app_tags
from integrations.imports import helpers
from integrations.imports.helpers import MediaImportError, MediaImportUnexpectedError

logger = logging.getLogger(__name__)


def importer(file, user, mode):
    """Import media from CSV file using the class-based importer."""
    csv_importer = YamtrackImporter(file, user, mode)
    return csv_importer.import_data()


class YamtrackImporter:
    """Class to handle importing user data from CSV files."""

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
            "Initialized Yamtrack CSV importer for user %s with mode %s",
            user.username,
            mode,
        )

    def import_data(self):
        """Import all user data from the CSV file."""
        try:
            decoded_file = StringIO(self.file.read().decode("utf-8"))
        except UnicodeDecodeError as e:
            msg = "Invalid file format. Please upload a CSV file."
            raise MediaImportError(msg) from e

        reader = DictReader(decoded_file)

        for row in reader:
            try:
                self._process_row(row)
            except services.ProviderAPIError as error:
                error_msg = (
                    f"Error processing entry with ID {row['media_id']} "
                    f"({app_tags.media_type_readable(row['media_type'])}): {error}"
                )
                self.warnings.append(error_msg)
                continue
            except Exception as error:
                error_msg = f"Error processing entry: {row}"
                raise MediaImportUnexpectedError(error_msg) from error

        helpers.cleanup_existing_media(self.to_delete, self.user)
        helpers.bulk_create_media(self.bulk_media, self.user)

        imported_counts = {
            media_type: len(media_list)
            for media_type, media_list in self.bulk_media.items()
        }

        deduplicated_messages = "\n".join(dict.fromkeys(self.warnings))
        return imported_counts, deduplicated_messages

    def _process_row(self, row):
        """Process a single row from the CSV file."""
        media_type = row["media_type"]

        stage_defaults = self._stage_defaults(row)
        if stage_defaults is None:
            return

        season_number = (
            int(row["season_number"]) if row["season_number"] != "" else None
        )
        episode_number = (
            int(row["episode_number"]) if row["episode_number"] != "" else None
        )

        if row["progress"] == "":
            row["progress"] = 0

        parent_type = (
            MediaTypes.TV.value
            if media_type in (MediaTypes.SEASON.value, MediaTypes.EPISODE.value)
            else media_type
        )

        # Check if we should process this movie based on mode
        if not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            parent_type,
            row["source"],
            row["media_id"],
            self.mode,
        ):
            return

        if row["title"] == "" or row["image"] == "":
            self._handle_missing_metadata(
                row,
                media_type,
                season_number,
                episode_number,
            )

        item_writer = (
            app.models.Item.objects.get_or_create
            if media_type == MediaTypes.STAGE.value
            else app.models.Item.objects.update_or_create
        )
        item, _ = item_writer(
            media_id=row["media_id"],
            source=row["source"],
            media_type=media_type,
            season_number=season_number,
            episode_number=episode_number,
            defaults={
                "title": row["title"],
                "image": row["image"],
                **stage_defaults,
            },
        )

        model = apps.get_model(app_label="app", model_name=media_type)
        instance = model(item=item)
        if media_type != MediaTypes.EPISODE.value:  # episode has no user field
            instance.user = self.user

        row["item"] = item
        form = app.forms.get_form_class(media_type)(
            row,
            instance=instance,
        )

        if form.is_valid():
            # Episodes have no progressed_at field, so their exported column is
            # blank; fall back to end_date (the watch date) so the activity
            # heatmap lands on the day watched instead of the import day.
            progressed_at = row.get("progressed_at") or row.get("end_date")
            if progressed_at:
                form.instance._history_date = parse_datetime(progressed_at)
            self.bulk_media[media_type].append(form.instance)
        else:
            error_msg = f"{row['title']} ({media_type}): {form.errors.as_json()}"
            self.warnings.append(error_msg)
            logger.error(error_msg)

    @staticmethod
    def _stage_identity_error(row):
        """Identify unsupported or malformed standalone Stage identities."""
        if any(row.get(field) for field in ("season_number", "episode_number")):
            return "Stage import cannot include season or episode numbers."
        if row.get("source") not in {Sources.MANUAL.value, Sources.WIKIDATA.value}:
            return "Stage import requires a manual or Wikidata source."
        if row["source"] == Sources.WIKIDATA.value:
            try:
                forms.RegexField(
                    regex=r"\AQ[1-9][0-9]*\Z", max_length=36, strip=False
                ).clean(row.get("media_id"))
            except ValidationError:
                return "Stage import requires a valid Wikidata work ID."
        return ""

    def _stage_defaults(self, row):
        """Validate Stage before overwrite bookkeeping or shared item changes."""
        if row["media_type"] != MediaTypes.STAGE.value:
            return {}
        identity_error = self._stage_identity_error(row)
        if identity_error:
            self.warnings.append(identity_error)
            return None
        if not row.get("title", "").strip():
            self.warnings.append("Stage import requires a work title.")
            return None
        try:
            work_forms = forms.MultipleChoiceField(
                choices=app.models.StageForms.choices,
            ).clean(json.loads(row.get("stage_forms") or "[]"))
        except (ValueError, TypeError, ValidationError):
            self.warnings.append(f"{row['title']} (stage): Invalid stage forms.")
            return None
        attendance = app.forms.StageForm(row)
        if not attendance.is_valid():
            self.warnings.append(
                f"{row['title']} (stage): {attendance.errors.as_json()}",
            )
            return None
        if not row.get("image"):
            row["image"] = settings.IMG_NONE
        artwork = self._stage_artwork(row)
        if row["source"] == Sources.WIKIDATA.value:
            row["media_id"] = stage.canonical_id(row["media_id"])
            if artwork:
                artwork = stage.retarget_artwork(artwork, row["media_id"])
        return {"stage_forms": work_forms, "stage_artwork": artwork}

    def _stage_artwork(self, row):
        """Restore current source credits without loading development formats."""
        if row["source"] != Sources.WIKIDATA.value:
            return {}
        try:
            record = json.loads(row.get("stage_artwork") or "{}")
            artwork = commons.restored_artwork(
                record,
                row["media_id"],
                row["image"],
            )
        except (ValueError, TypeError):
            artwork = {}
        if not artwork and row["image"] != settings.IMG_NONE:
            self.warnings.append(
                f"{row['title']}: Artwork omitted because its credit is "
                "missing or invalid."
            )
            row["image"] = settings.IMG_NONE
        return artwork

    def _handle_missing_metadata(self, row, media_type, season_number, episode_number):
        """Handle missing metadata by fetching from provider."""
        if row["source"] == Sources.MANUAL.value and row["image"] == "":
            row["image"] = settings.IMG_NONE
            return

        if row.get("media_id", "") != "":
            metadata = services.get_media_metadata(
                media_type,
                row["media_id"],
                row["source"],
                [season_number],
                episode_number,
            )
            row["title"] = metadata["title"]
            row["image"] = metadata["image"]
            return

        if row.get("title", "") != "":
            source = row.get("source", "")
            if source == "":
                source = config.get_default_source_name(media_type).value

            metadata = services.search(
                media_type,
                row["title"],
                1,
                source,
            )

            first_result = metadata["results"][0]
            row["title"] = first_result["title"]
            row["source"] = first_result["source"]
            row["media_id"] = first_result["media_id"]
            row["media_type"] = media_type
            row["image"] = first_result["image"]

            logger.info("Added title from %s: %s", source, row["title"])
            logger.info("Obtained media id: %s", row["media_id"])
            return

        msg = f"Missing metadata for: {row}"
        raise MediaImportError(msg)
