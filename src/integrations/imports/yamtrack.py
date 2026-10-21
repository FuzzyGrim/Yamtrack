import logging
from collections import defaultdict
from csv import DictReader
from io import StringIO

from django.apps import apps
from django.conf import settings
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext as _

import app
from app import config
from app.models import MediaTypes, Sources, Status
from app.providers import services
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

        # Track existing seasons, watches and entries to only add what is missing
        # in "new" mode
        self.existing_seasons = {}
        self.existing_watches = {}
        self.existing_entries = {}
        if mode == "new":
            self.existing_seasons = helpers.get_existing_seasons(user)
            self.existing_watches = helpers.get_existing_watches(user)
            # Each row of the file is a single entry, so rows are matched to the
            # entries already in Yamtrack instead of to the media
            self.existing_entries = helpers.get_existing_entries(
                user,
                [
                    media_type
                    for media_type in MediaTypes.values
                    if media_type
                    not in (
                        MediaTypes.TV.value,
                        MediaTypes.SEASON.value,
                        MediaTypes.EPISODE.value,
                    )
                ],
            )

        # Track existing media changed in "new" mode, by primary key
        self.to_update = defaultdict(dict)

        # Track the status of shows and seasons in the file and the seasons that
        # got episodes never watched before, to reopen completed ones
        self.row_statuses = {}
        self.first_watches = set()

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
            decoded_file = self.file.read().decode("utf-8")
        except UnicodeDecodeError as e:
            msg = _("Invalid file format. Please upload a CSV file.")
            raise MediaImportError(msg) from e

        reader = DictReader(StringIO(decoded_file, newline=""))

        for row in reader:
            try:
                self._process_row(row)
            except services.ProviderAPIError as error:
                error_msg = _(
                    "Error processing entry with ID %(media_id)s "
                    "(%(media_type)s): %(error)s"
                ) % {
                    "media_id": row["media_id"],
                    "media_type": app_tags.media_type_readable(row["media_type"]),
                    "error": error,
                }
                self.warnings.append(error_msg)
                continue
            except Exception as error:
                error_msg = _("Error processing entry: %(row)s") % {"row": row}
                raise MediaImportUnexpectedError(error_msg) from error

        self._reopen_existing_shows()

        helpers.cleanup_existing_media(self.to_delete, self.user)
        helpers.bulk_create_media(self.bulk_media, self.user)
        helpers.bulk_update_media(
            self.to_update,
            helpers.EXISTING_MEDIA_UPDATE_FIELDS,
            self.user,
        )

        imported_counts = {
            media_type: len(media_list)
            for media_type, media_list in self.bulk_media.items()
        }

        deduplicated_messages = "\n".join(dict.fromkeys(self.warnings))
        return imported_counts, deduplicated_messages

    def _process_row(self, row):
        """Process a single row from the CSV file."""
        media_type = row["media_type"]

        season_number = (
            int(row["season_number"]) if row["season_number"] != "" else None
        )
        episode_number = (
            int(row["episode_number"]) if row["episode_number"] != "" else None
        )

        if row["progress"] == "":
            row["progress"] = 0

        # Rows without an ID only get one from the metadata
        if row["title"] == "" or row["image"] == "":
            self._handle_missing_metadata(
                row,
                media_type,
                season_number,
                episode_number,
            )

        parent_type = (
            MediaTypes.TV.value
            if media_type in (MediaTypes.SEASON.value, MediaTypes.EPISODE.value)
            else media_type
        )

        # Check if we should process this entry based on mode,
        # existing media in "new" mode is synced instead of skipped
        if self.mode != "new" and not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            parent_type,
            row["source"],
            str(row["media_id"]),
            self.mode,
        ):
            return

        # "new" mode doesn't replace the metadata of existing items with the file's
        get_item = (
            app.models.Item.objects.get_or_create
            if self.mode == "new"
            else app.models.Item.objects.update_or_create
        )
        item, _ = get_item(
            media_id=row["media_id"],
            source=row["source"],
            media_type=media_type,
            season_number=season_number,
            episode_number=episode_number,
            defaults={
                "title": row["title"],
                "image": row["image"],
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

            if self.mode != "new" or not self._sync_existing(
                media_type,
                form.instance,
            ):
                self.bulk_media[media_type].append(form.instance)
        else:
            error_msg = _("%(title)s (%(media_type)s): %(errors)s") % {
                "title": row["title"],
                "media_type": app_tags.media_type_readable(row["media_type"]),
                "errors": form.errors.as_json(),
            }
            self.warnings.append(error_msg)
            logger.error(error_msg)

    def _sync_existing(self, media_type, instance):
        """Sync the media of the row already in Yamtrack, return if it exists."""
        item = instance.item
        source = item.source
        media_id = str(item.media_id)

        if media_type == MediaTypes.EPISODE.value:
            return self._is_existing_episode(instance)

        if media_type == MediaTypes.TV.value:
            self.row_statuses[media_type, source, media_id] = instance.status
            existing = self.existing_media[media_type][source].get(media_id)
        elif media_type == MediaTypes.SEASON.value:
            key = (source, media_id, item.season_number)
            self.row_statuses[(media_type, *key)] = instance.status
            existing = self.existing_seasons.get(key)
        else:
            existing = self._match_existing_entry(media_type, instance)

        if existing is None:
            return False

        values = {
            field: getattr(instance, field)
            for field in helpers.EXISTING_MEDIA_UPDATE_FIELDS[media_type]
            if field != "progressed_at"
        }

        # Movies don't track their progress in the file
        if (
            media_type == MediaTypes.MOVIE.value
            and instance.status == Status.COMPLETED.value
        ):
            values["progress"] = 1

        if helpers.sync_existing_media(existing, values):
            self.to_update[media_type][existing.pk] = existing
        return True

    def _is_existing_episode(self, episode):
        """Return whether the watch of an episode is already in Yamtrack."""
        item = episode.item
        watch_key = helpers.get_watch_key(
            MediaTypes.EPISODE.value,
            item.source,
            item.media_id,
            item.season_number,
            item.episode_number,
        )
        if helpers.is_existing_watch(
            self.existing_watches,
            watch_key,
            episode.end_date,
        ):
            return True

        if watch_key not in self.existing_watches:
            self.first_watches.add(
                (item.source, str(item.media_id), item.season_number),
            )
        return False

    def _match_existing_entry(self, media_type, instance):
        """Match the entry of the row to one already in Yamtrack.

        The row is the same entry as one that ended at around the same date, or
        else as one still open in Yamtrack, or else as one with the same status
        where only one of them has an end date. Each entry is matched only once,
        so rows left unmatched are repeats missing from Yamtrack.
        """
        item = instance.item
        entries = self.existing_entries.get(
            (media_type, item.source, str(item.media_id)),
        )
        if not entries:
            return None

        matchers = (
            lambda entry: helpers.is_same_watch_date(
                entry.end_date,
                instance.end_date,
            ),
            lambda entry: (
                entry.end_date is None
                and entry.status in (Status.PLANNING.value, Status.IN_PROGRESS.value)
            ),
            lambda entry: (
                entry.status == instance.status
                and (entry.end_date is None) != (instance.end_date is None)
            ),
        )
        for matches in matchers:
            match = next((entry for entry in entries if matches(entry)), None)
            if match:
                entries.remove(match)
                return match

        return None

    def _reopen_existing_shows(self):
        """Reopen completed shows and seasons that got episodes never watched.

        Shows and seasons completed in the file are left as completed.
        """
        for source, media_id, season_number in self.first_watches:
            medias = (
                (
                    (MediaTypes.TV.value, source, media_id),
                    self.existing_media[MediaTypes.TV.value][source].get(media_id),
                ),
                (
                    (MediaTypes.SEASON.value, source, media_id, season_number),
                    self.existing_seasons.get((source, media_id, season_number)),
                ),
            )
            for key, media in medias:
                if (
                    media
                    and self.row_statuses.get(key) != Status.COMPLETED.value
                    and helpers.reopen_status(media)
                ):
                    self.to_update[key[0]][media.pk] = media

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

        msg = _("Missing metadata for: %(row)s") % {"row": row}
        raise MediaImportError(msg)
