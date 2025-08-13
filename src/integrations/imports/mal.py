import logging
import re
from collections import defaultdict
from datetime import datetime

import requests
from django.apps import apps
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

import app
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
        status = self._get_status(list_status["status"])

        try:
            image_url = content["node"]["main_picture"]["large"]
        except KeyError:
            image_url = settings.IMG_NONE

        # Check if we should process this entry based on mode
        if not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            media_type,
            Sources.MAL.value,
            str(content["node"]["id"]),
            self.mode,
        ):
            return

        item, _ = app.models.Item.objects.get_or_create(
            media_id=str(content["node"]["id"]),
            source=Sources.MAL.value,
            media_type=media_type,
            defaults={
                "title": content["node"]["title"],
                "image": image_url,
            },
        )

        model = apps.get_model(app_label="app", model_name=media_type)
        updated_at = parse_datetime(list_status.get("updated_at"))

        # Handle completed repeats
        if media_type == MediaTypes.ANIME.value:
            progress = list_status["num_episodes_watched"]
            repeats = list_status["num_times_rewatched"]
            if list_status["is_rewatching"]:
                if repeats == 0:
                    repeats = 1
                status = Status.IN_PROGRESS.value
        else:
            progress = list_status["num_chapters_read"]
            repeats = list_status["num_times_reread"]
            if list_status["is_rereading"]:
                if repeats == 0:
                    repeats = 1
                status = Status.IN_PROGRESS.value

        if repeats >= 1:
            for _ in range(repeats):
                max_progress = content["node"].get("num_episodes") or content[
                    "node"
                ].get(
                    "num_chapters",
                )
                instance = model(
                    item=item,
                    user=self.user,
                    score=list_status["score"],
                    progress=max_progress or 0,
                    status=Status.COMPLETED.value,
                    start_date=self._parse_mal_date(list_status.get("start_date")),
                    end_date=self._parse_mal_date(list_status.get("finish_date")),
                    notes=list_status["comments"],
                )

                instance._history_date = updated_at
                self.bulk_media[media_type].append(instance)

        # Add current status entry
        instance = model(
            item=item,
            user=self.user,
            score=list_status["score"],
            progress=progress,
            status=status,
            start_date=self._parse_mal_date(list_status.get("start_date")),
            end_date=self._parse_mal_date(list_status.get("finish_date")),
            notes=list_status["comments"],
        )
        instance._history_date = updated_at
        self.bulk_media[media_type].append(instance)

    def _parse_mal_date(self, date_str):
        """Parse MAL date string (YYYY-MM-YY) into datetime object."""
        if date_str is None:
            return None

        try:
            # turn year-only date into YYYY-MM-YY; assume Jan. 1st
            if re.fullmatch(r"\d{4}", date_str):
                return datetime(int(date_str), 1, 1).replace(
                    hour=0,
                    minute=0,
                    second=0,
                    tzinfo=timezone.get_current_timezone(),
                )

            return datetime.strptime(date_str, "%Y-%m-%d").replace(
                hour=0,
                minute=0,
                second=0,
                tzinfo=timezone.get_current_timezone(),
            )
        except ValueError:
            logger.warning("Unexpected MAL date format: %s", date_str)
            return None

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
