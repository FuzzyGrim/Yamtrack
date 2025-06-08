import json
import logging
from collections import defaultdict
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.utils.dateparse import parse_datetime

import app
from app.models import MediaTypes, Sources, Status
from integrations.imports import helpers
from integrations.imports.helpers import MediaImportError, MediaImportUnexpectedError

logger = logging.getLogger(__name__)


def importer(kitsu_id, user, mode):
    """Import anime and manga ratings from Kitsu."""
    kitsu_importer = KitsuImporter(kitsu_id, user, mode)
    return kitsu_importer.import_data()


class KitsuImporter:
    """Class to handle importing user data from Kitsu."""

    KITSU_API_BASE_URL = "https://kitsu.io/api/edge"
    KITSU_PAGE_LIMIT = 500

    def __init__(self, kitsu_id, user, mode):
        """Initialize the importer with Kitsu ID, user, and mode.

        Args:
            kitsu_id (str): Kitsu username or user ID to import from
            user: Django user object to import data for
            mode (str): Import mode ("new" or "overwrite")
        """
        self.kitsu_id = kitsu_id
        self.user = user
        self.mode = mode
        self.warnings = []

        # Track existing media for "new" mode
        self.existing_media = helpers.get_existing_media(user)

        # Track media IDs to delete in overwrite mode
        self.to_delete = defaultdict(lambda: defaultdict(set))

        # Track bulk creation lists for each media type
        self.bulk_media = defaultdict(list)

        # Load Kitsu-MU mapping data
        current_file_dir = Path(__file__).resolve().parent
        json_file_path = current_file_dir / "data" / "kitsu-mu-mapping.json"
        with json_file_path.open() as f:
            self.kitsu_mu_mapping = json.load(f)

        logger.info(
            "Initialized Kitsu importer for user %s with mode %s",
            kitsu_id,
            mode,
        )

    def import_data(self):
        """Import all user data from Kitsu."""
        # Check if given ID is a username
        if not self.kitsu_id.isdigit():
            self.kitsu_id = self._get_kitsu_id(self.kitsu_id)

        logger.info("Starting Kitsu import for user id %s", self.kitsu_id)

        self._process_media_type(MediaTypes.ANIME.value)
        self._process_media_type(MediaTypes.MANGA.value)

        helpers.cleanup_existing_media(self.to_delete, self.user)
        helpers.bulk_create_media(self.bulk_media, self.user)

        imported_counts = {
            media_type: len(media_list)
            for media_type, media_list in self.bulk_media.items()
        }

        deduplicated_messages = "\n".join(dict.fromkeys(self.warnings))
        return imported_counts, deduplicated_messages

    def _get_kitsu_id(self, username):
        """Get the user ID from Kitsu."""
        url = f"{self.KITSU_API_BASE_URL}/users"
        response = app.providers.services.api_request(
            "KITSU",
            "GET",
            url,
            params={"filter[name]": username},
        )

        if not response["data"]:
            msg = f"User {username} not found."
            raise MediaImportError(msg)
        if len(response["data"]) > 1:
            msg = (
                f"Multiple users found for {username}, please use your user ID. "
                "User IDs can be found in the URL when viewing your Kitsu profile."
            )
            raise MediaImportError(msg)

        return response["data"][0]["id"]

    def _process_media_type(self, media_type):
        """Process all media of a specific type from Kitsu."""
        logger.info("Fetching %s from Kitsu", media_type)
        response = self._get_media_response(media_type)

        media_lookup = {
            item["id"]: item
            for item in response["included"]
            if item["type"] == media_type
        }
        mapping_lookup = {
            item["id"]: item
            for item in response["included"]
            if item["type"] == "mappings"
        }

        for entry in response["entries"]:
            try:
                self._process_entry(entry, media_type, media_lookup, mapping_lookup)
            except MediaImportError as error:
                self.warnings.append(str(error))
            except Exception as error:
                kitsu_id = entry["relationships"][media_type]["data"]["id"]
                kitsu_metadata = media_lookup[kitsu_id]
                title = kitsu_metadata["attributes"]["canonicalTitle"]
                msg = f"Error processing entry: {title} ({kitsu_id}) - {entry}"
                raise MediaImportUnexpectedError(msg) from error

    def _get_media_response(self, media_type):
        """Get all media entries for a user from Kitsu."""
        url = f"{self.KITSU_API_BASE_URL}/library-entries"

        if media_type == MediaTypes.ANIME.value:
            media_fields = "canonicalTitle,posterImage,episodeCount,mappings"
        else:
            media_fields = "canonicalTitle,posterImage,chapterCount,mappings"

        params = {
            "filter[user_id]": self.kitsu_id,
            "filter[kind]": media_type,
            "include": f"{media_type},{media_type}.mappings",
            f"fields[{media_type}]": media_fields,
            "fields[mappings]": "externalSite,externalId",
            "page[limit]": self.KITSU_PAGE_LIMIT,
        }

        all_data = {"entries": [], "included": []}

        while url:
            data = app.providers.services.api_request(
                "KITSU",
                "GET",
                url,
                params=params,
            )
            all_data["entries"].extend(data["data"])
            all_data["included"].extend(data.get("included", []))
            url = data["links"].get("next")
            params = {}  # Clear params for subsequent requests

        return all_data

    def _process_entry(self, entry, media_type, media_lookup, mapping_lookup):
        """Process a single entry from Kitsu."""
        attributes = entry["attributes"]
        relationship = entry["relationships"][media_type]

        if relationship["data"]:
            kitsu_id = relationship["data"]["id"]
            kitsu_metadata = media_lookup[kitsu_id]
        else:
            # NSFW content are hidden, fetch from related URL
            kitsu_metadata, mapping_lookup = self._fetch_media_from_related_url(
                relationship,
                media_type,
            )

        item = self._create_or_get_item(
            media_type,
            kitsu_metadata,
            mapping_lookup,
        )

        # Check if we should process this entry based on mode
        if not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            media_type,
            item.source,
            item.media_id,
            self.mode,
        ):
            return

        model = apps.get_model(app_label="app", model_name=media_type)
        updated_at = parse_datetime(attributes["updatedAt"])

        max_progress = kitsu_metadata["attributes"].get(
            "episodeCount",
        ) or kitsu_metadata["attributes"].get("chapterCount")

        # Handle completed repeats
        repeats_count = attributes["reconsumeCount"]
        if attributes["reconsuming"] and repeats_count == 0:
            repeats_count = 1

        if repeats_count >= 1:
            for _ in range(attributes["reconsumeCount"]):
                instance = model(
                    item=item,
                    user=self.user,
                    score=self._get_rating(attributes["ratingTwenty"]),
                    progress=max_progress or attributes["progress"],
                    status=Status.COMPLETED.value,
                    start_date=attributes["startedAt"],
                    end_date=attributes["finishedAt"],
                    notes=attributes["notes"] or "",
                )
                instance._history_date = updated_at
                self.bulk_media[media_type].append(instance)

        instance = model(
            item=item,
            user=self.user,
            score=self._get_rating(attributes["ratingTwenty"]),
            progress=attributes["progress"],
            status=self._get_status(attributes["status"]),
            start_date=attributes["startedAt"],
            end_date=attributes["finishedAt"],
            notes=attributes["notes"] or "",
        )

        if attributes["reconsuming"]:
            instance.status = Status.IN_PROGRESS.value

        instance._history_date = updated_at
        self.bulk_media[media_type].append(instance)

    def _fetch_media_from_related_url(self, relationship, media_type):
        """Fetch media data from Kitsu related URL when relationship data is null."""
        related_url = relationship["links"]["related"]
        if not related_url:
            msg = (
                f"Could not import unknown item - missing media data from Kitsu. "
                f"Relationship: {relationship}"
            )
            raise MediaImportError(msg)

        params = {
            "include": "mappings",
            f"fields[{media_type}]": "canonicalTitle,posterImage,mappings",
            "fields[mappings]": "externalSite,externalId",
        }

        response = app.providers.services.api_request(
            "KITSU",
            "GET",
            related_url,
            params=params,
        )

        mapping_lookup = {
            item["id"]: item
            for item in response.get("included", [])
            if item["type"] == "mappings"
        }

        return response["data"], mapping_lookup

    def _create_or_get_item(self, media_type, kitsu_metadata, mapping_lookup):
        """Create or get an Item instance."""
        sites = [
            f"myanimelist/{media_type}",
            "mangaupdates",
        ]

        mappings = {
            mapping["attributes"]["externalSite"]: mapping["attributes"]["externalId"]
            for mapping_ref in kitsu_metadata["relationships"]["mappings"]["data"]
            for mapping in [mapping_lookup[mapping_ref["id"]]]
        }

        media_id = None
        for site in sites:
            if site not in mappings:
                continue

            external_id = mappings[site]
            if site == f"myanimelist/{media_type}":
                media_id = external_id
                source = Sources.MAL.value
                break

            if site == "mangaupdates":
                # if its int, its an old MU ID
                if external_id.isdigit():
                    # get the base36 encoded ID
                    try:
                        external_id = self.kitsu_mu_mapping[external_id]
                    except KeyError:  # ID not found in mapping
                        continue

                # decode the base36 encoded ID
                media_id = str(int(external_id, 36))
                source = Sources.MANGAUPDATES.value
                break

        # Farmagia (49333) shows MAL external_id == "anime"
        if not media_id or not media_id.isdigit():
            media_title = kitsu_metadata["attributes"]["canonicalTitle"]
            msg = f"{media_title}: No valid external ID found."
            raise MediaImportError(msg)

        image_url = self._get_image_url(kitsu_metadata)

        item, _ = app.models.Item.objects.get_or_create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            defaults={
                "title": kitsu_metadata["attributes"]["canonicalTitle"],
                "image": image_url,
            },
        )
        return item

    def _get_image_url(self, media):
        """Get the image URL for a media item."""
        try:
            return media["attributes"]["posterImage"]["medium"]
        except KeyError:
            try:
                return media["attributes"]["posterImage"]["original"]
            except KeyError:
                return settings.IMG_NONE

    def _get_rating(self, rating):
        """Convert the rating from Kitsu to a 0-10 scale."""
        if rating:
            return rating / 2
        return None

    def _get_status(self, status):
        """Convert the status from Kitsu to the status used in the app."""
        status_mapping = {
            "completed": Status.COMPLETED.value,
            "current": Status.IN_PROGRESS.value,
            "planned": Status.PLANNING.value,
            "on_hold": Status.PAUSED.value,
            "dropped": Status.DROPPED.value,
        }
        return status_mapping[status]
