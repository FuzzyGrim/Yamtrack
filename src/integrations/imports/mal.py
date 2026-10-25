import logging
from collections import defaultdict

import requests
from django.apps import apps
from django.conf import settings
from django.utils.dateparse import parse_datetime

import app
import app.helpers
from app.models import MediaTypes, Sources, Status
from integrations.imports import helpers
from integrations.imports.helpers import MediaImportError, MediaImportUnexpectedError

logger = logging.getLogger(__name__)


def importer(username, user, mode):
    """Import anime and manga from MyAnimeList."""
    mal_importer = MyAnimeListImporter(username, user, mode)
    return mal_importer.import_data()


class MyAnimeListImporter:
    """Class to handle importing user data from MyAnimeList."""

    def __init__(self, username, user, mode):
        """Initialize the importer with username, user, and mode.

        Args:
            username (str): MyAnimeList username to import from
            user: Django user object to import data for
            mode (str): Import mode ("new" or "overwrite")
        """
        self.username = username
        self.user = user
        self.mode = mode
        self.warnings = []
        self.base_url = "https://api.myanimelist.net/v2/users"

        # Track existing media for "new" mode
        self.existing_media = helpers.get_existing_media(user)

        # Track every entry of each media to sync the current one and add the
        # missing repeats in "new" mode
        self.existing_entries = {}
        if mode == "new":
            self.existing_entries = helpers.get_existing_entries(
                user,
                [MediaTypes.ANIME.value, MediaTypes.MANGA.value],
            )

        # Track existing media changed in "new" mode, by primary key
        self.to_update = defaultdict(dict)

        # Track media IDs to delete in overwrite mode
        self.to_delete = defaultdict(lambda: defaultdict(set))

        # Track bulk creation lists for each media type
        self.bulk_media = defaultdict(list)

        logger.info(
            "Initialized MyAnimeList importer for user %s with mode %s",
            username,
            mode,
        )

    def import_data(self):
        """Import all user data from MyAnimeList."""
        self._process_media_type(MediaTypes.ANIME.value)
        self._process_media_type(MediaTypes.MANGA.value)

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

    def _process_media_type(self, media_type):
        """Process all media of a specific type from MyAnimeList."""
        logger.info("Fetching %s from MyAnimeList", media_type)
        params = {
            "fields": (
                "num_episodes,num_chapters,"
                "list_status{comments,num_times_rewatched,num_times_reread}"
            ),
            "nsfw": "true",
            "limit": 1000,
        }
        url = f"{self.base_url}/{self.username}/{media_type}list"

        try:
            response = self._get_whole_response(url, params)
        except requests.exceptions.HTTPError as error:
            if error.response.status_code == requests.codes.not_found:
                msg = f"User {self.username} not found."
                raise MediaImportError(msg) from error
            raise

        for content in response["data"]:
            try:
                self._process_entry(content, media_type)
            except Exception as error:
                msg = f"Error processing entry: {content}"
                raise MediaImportUnexpectedError(msg) from error

    def _get_whole_response(self, url, params):
        """Fetch whole data from user."""
        headers = {"X-MAL-CLIENT-ID": settings.MAL_API}

        data = app.providers.services.api_request(
            "MAL",
            "GET",
            url,
            params=params,
            headers=headers,
        )

        while "next" in data["paging"]:
            next_url = data["paging"]["next"]
            next_data = app.providers.services.api_request(
                "MAL",
                "GET",
                next_url,
                params=params,
                headers=headers,
            )
            data["data"].extend(next_data["data"])
            data["paging"] = next_data["paging"]

        return data

    def _process_entry(self, content, media_type):
        """Process a single entry from MyAnimeList."""
        list_status = content["list_status"]
        media_id = str(content["node"]["id"])

        try:
            image_url = content["node"]["main_picture"]["large"]
        except KeyError:
            image_url = settings.IMG_NONE

        existing = self._get_existing(media_type, media_id)

        # Check if we should process this entry based on mode
        if not existing and not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            media_type,
            Sources.MAL.value,
            media_id,
            self.mode,
        ):
            return

        progress, repeats, status = self._get_progress(list_status, media_type)
        current = {
            # MyAnimeList scores unrated media as 0
            "score": list_status["score"] or None,
            "progress": progress,
            "status": status,
            "start_date": self._parse_mal_date(list_status.get("start_date")),
            "end_date": self._parse_mal_date(list_status.get("finish_date")),
            "notes": list_status["comments"],
        }
        max_progress = content["node"].get("num_episodes") or content["node"].get(
            "num_chapters",
        )

        # Existing media only gets the repeats it is missing
        if existing:
            missing = self._sync_existing(
                media_type,
                existing,
                current,
                max_progress,
                repeats + 1,
            )
            if not missing:
                return
            repeats = missing - 1

        item, _ = app.models.Item.objects.get_or_create(
            media_id=media_id,
            source=Sources.MAL.value,
            media_type=media_type,
            defaults={
                "title": content["node"]["title"],
                "image": image_url,
            },
        )

        model = apps.get_model(app_label="app", model_name=media_type)
        updated_at = parse_datetime(list_status.get("updated_at"))

        # Add completed repeats
        for _ in range(repeats):
            instance = model(
                item=item,
                user=self.user,
                **{
                    **current,
                    "progress": max_progress or 0,
                    "status": Status.COMPLETED.value,
                },
            )
            instance._history_date = updated_at
            self.bulk_media[media_type].append(instance)

        # Add current status entry
        instance = model(item=item, user=self.user, **current)
        instance._history_date = updated_at
        self.bulk_media[media_type].append(instance)

    def _get_progress(self, list_status, media_type):
        """Return the progress, completed repeats and status of an entry."""
        status = self._get_status(list_status["status"])

        if media_type == MediaTypes.ANIME.value:
            progress = list_status["num_episodes_watched"]
            repeats = list_status["num_times_rewatched"]
            repeating = list_status["is_rewatching"]
        else:
            progress = list_status["num_chapters_read"]
            repeats = list_status["num_times_reread"]
            repeating = list_status["is_rereading"]

        if repeating:
            if repeats == 0:
                repeats = 1
            status = Status.IN_PROGRESS.value

        return progress, repeats, status

    def _get_existing(self, media_type, media_id):
        """Get the current entry of media already in Yamtrack, only in "new" mode."""
        if self.mode != "new":
            return None

        entries = self.existing_entries.get((media_type, Sources.MAL.value, media_id))
        return helpers.get_current_entry(entries) if entries else None

    def _sync_existing(self, media_type, media, current, max_progress, entries):
        """Sync existing media and return how many of its entries are missing."""
        changed, missing = helpers.sync_existing_entries(
            media,
            current,
            max_progress,
            entries,
            len(
                self.existing_entries[
                    media_type,
                    Sources.MAL.value,
                    media.item.media_id,
                ],
            ),
        )
        if changed:
            self.to_update[media_type][media.pk] = media

        return missing

    def _parse_mal_date(self, date_str):
        """Parse MAL date string (YYYY-MM-YY) into datetime object."""
        if date_str is None:
            return None

        year_only_len = 4  # YYYY
        year_month_len = 7  # YYYY-MM

        if len(date_str) == year_only_len and date_str.isdigit():
            date_str = f"{date_str}-01-01"  # Default to January 1st

        elif (
            len(date_str) == year_month_len
            and date_str[:4].isdigit()
            and date_str[5:7].isdigit()
        ):
            date_str = f"{date_str}-01"  # Default to first day of the month

        return app.helpers.date_only_instant(date_str)

    def _get_status(self, status):
        """Convert the status from MyAnimeList to the status used in the app."""
        status_mapping = {
            "completed": Status.COMPLETED.value,
            "reading": Status.IN_PROGRESS.value,
            "watching": Status.IN_PROGRESS.value,
            "plan_to_watch": Status.PLANNING.value,
            "plan_to_read": Status.PLANNING.value,
            "on_hold": Status.PAUSED.value,
            "dropped": Status.DROPPED.value,
        }
        return status_mapping[status]
