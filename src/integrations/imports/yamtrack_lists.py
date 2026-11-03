import logging
from collections import Counter
from csv import DictReader
from io import StringIO

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _

from app.models import Item
from app.providers import services
from app.templatetags import app_tags
from integrations.exports import LIST_ROW_TYPE
from integrations.imports.helpers import MediaImportError, MediaImportUnexpectedError
from integrations.imports.yamtrack import YamtrackImporter
from lists.models import CustomList, CustomListItem

logger = logging.getLogger(__name__)


def importer(file, user, mode):
    """Import custom lists from CSV file using the class-based importer."""
    csv_importer = YamtrackListsImporter(file, user, mode)
    return csv_importer.import_data()


class YamtrackListsImporter(YamtrackImporter):
    """Class to handle importing custom lists from CSV files.

    Only shares the handling of missing metadata with the media importer.
    """

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

        # Track the lists in the file by name, with their description and items
        self.lists = {}

        logger.info(
            "Initialized Yamtrack lists CSV importer for user %s with mode %s",
            user.username,
            mode,
        )

    def import_data(self):
        """Import all custom lists from the CSV file."""
        try:
            decoded_file = self.file.read().decode("utf-8")
        except UnicodeDecodeError as e:
            msg = _("Invalid file format. Please upload a CSV file.")
            raise MediaImportError(msg) from e

        reader = DictReader(StringIO(decoded_file, newline=""))

        for row in reader:
            try:
                self._process_row(row)
            except Exception as error:
                error_msg = _("Error processing entry: %(row)s") % {"row": row}
                raise MediaImportUnexpectedError(error_msg) from error

        imported_counts = self._save_lists()

        deduplicated_messages = "\n".join(dict.fromkeys(self.warnings))
        return imported_counts, deduplicated_messages

    def _process_row(self, row):
        """Process a single row from the CSV file."""
        name = row["list_name"]
        if not name:
            self.warnings.append(_("Missing list name: %(row)s") % {"row": row})
            return

        if row["media_type"] == LIST_ROW_TYPE:
            self.lists.setdefault(
                name,
                {"description": row["list_description"], "items": []},
            )
            return

        # Items depend on their list, so its row has to be before them
        custom_list = self.lists.get(name)
        if custom_list is None:
            self.warnings.append(
                _("Missing list row for %(list_name)s: %(row)s")
                % {"list_name": name, "row": row},
            )
            return

        try:
            custom_list["items"].append(self._get_item(row))
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
        except (ValidationError, IntegrityError) as error:
            error_msg = _("%(title)s (%(media_type)s): %(errors)s") % {
                "title": row["title"],
                "media_type": row["media_type"],
                "errors": error,
            }
            self.warnings.append(error_msg)
            logger.warning(error_msg)

    def _get_item(self, row):
        """Return the item of the row."""
        season_number = (
            int(row["season_number"]) if row["season_number"] != "" else None
        )
        episode_number = (
            int(row["episode_number"]) if row["episode_number"] != "" else None
        )

        if row["title"] == "" or row["image"] == "":
            self._handle_missing_metadata(
                row,
                row["media_type"],
                season_number,
                episode_number,
            )

        item = Item(
            media_id=row["media_id"],
            source=row["source"],
            media_type=row["media_type"],
            title=row["title"],
            image=row["image"],
            season_number=season_number,
            episode_number=episode_number,
        )
        item.clean_fields()

        # "new" mode doesn't replace the metadata of existing items with the file's
        get_item = (
            Item.objects.get_or_create
            if self.mode == "new"
            else Item.objects.update_or_create
        )
        return get_item(
            media_id=item.media_id,
            source=item.source,
            media_type=item.media_type,
            season_number=season_number,
            episode_number=episode_number,
            defaults={
                "title": item.title,
                "image": item.image,
            },
        )[0]

    @transaction.atomic
    def _save_lists(self):
        """Save the lists in the file, return the imported counts."""
        existing_lists = {
            custom_list.name: custom_list
            for custom_list in CustomList.objects.filter(owner=self.user)
        }
        imported_counts = Counter()

        for name, values in self.lists.items():
            custom_list = existing_lists.get(name)

            if custom_list is None:
                custom_list = CustomList.objects.create(
                    name=name,
                    description=values["description"],
                    owner=self.user,
                )
            elif self.mode == "overwrite":
                custom_list.description = values["description"]
                custom_list.save(update_fields=["description"])
                custom_list.items.clear()

            existing_items = set(custom_list.items.all())
            new_items = [
                item
                for item in dict.fromkeys(values["items"])
                if item not in existing_items
            ]
            CustomListItem.objects.bulk_create(
                CustomListItem(item=item, custom_list=custom_list) for item in new_items
            )
            imported_counts.update(item.media_type for item in new_items)

        return imported_counts
