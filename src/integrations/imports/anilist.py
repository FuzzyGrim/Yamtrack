import logging
from collections import defaultdict
from datetime import UTC

import requests
from django.apps import apps
from django.utils import timezone

import app
from app.models import MediaTypes, Sources, Status
from integrations.imports import helpers
from integrations.imports.helpers import MediaImportError, MediaImportUnexpectedError

logger = logging.getLogger(__name__)


def importer(username, user, mode):
    """Import anime and manga ratings from Anilist."""
    anilist_importer = AniListImporter(username, user, mode)
    return anilist_importer.import_data()


class AniListImporter:
    """Class to handle importing user data from AniList."""

    def __init__(self, username, user, mode):
        """Initialize the importer with username, user, and mode.

        Args:
            username (str): AniList username to import from
            user: Django user object to import data for
            mode (str): Import mode ("new" or "overwrite")
        """
        self.username = username
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
            "Initialized AniList importer for user %s with mode %s",
            username,
            mode,
        )

    def import_data(self):
        """Import all user data from AniList."""
        query = """
        query ($userName: String){
            anime: MediaListCollection(userName: $userName, type: ANIME) {
                lists {
                    isCustomList
                    entries {
                        media{
                            title {
                                userPreferred
                            }
                            coverImage {
                                large
                            }
                            idMal
                            chapters
                            episodes
                        }
                        status
                        score(format: POINT_10_DECIMAL)
                        progress
                        startedAt {
                            year
                            month
                            day
                        }
                        completedAt {
                            year
                            month
                            day
                        }
                        updatedAt
                        repeat
                        notes
                    }
                }
            }
            manga: MediaListCollection(userName: $userName, type: MANGA) {
                lists {
                    isCustomList
                    entries {
                        media{
                            title {
                                userPreferred
                            }
                            coverImage {
                                large
                            }
                            idMal
                        }
                        status
                        score(format: POINT_10_DECIMAL)
                        progress
                        startedAt {
                            year
                            month
                            day
                        }
                        completedAt {
                            year
                            month
                            day
                        }
                        updatedAt
                        repeat
                        notes
                    }
                }
            }
        }
        """
        variables = {"userName": self.username}
        url = "https://graphql.anilist.co"

        logger.info("Fetching anime and manga from AniList account")

        try:
            response = app.providers.services.api_request(
                "ANILIST",
                "POST",
                url,
                params={"query": query, "variables": variables},
            )
        except requests.exceptions.HTTPError as error:
            error_message = error.response.json()["errors"][0].get("message")
            if error_message == "User not found":
                msg = f"User {self.username} not found."
                raise MediaImportError(msg) from error
            if error_message == "Private User":
                msg = f"User {self.username} is private."
                raise MediaImportError(msg) from error
            raise

        self._process_media_data(response["data"]["anime"], MediaTypes.ANIME.value)
        self._process_media_data(response["data"]["manga"], MediaTypes.MANGA.value)

        helpers.cleanup_existing_media(self.to_delete, self.user)
        helpers.bulk_create_media(self.bulk_media, self.user)

        imported_counts = {
            media_type: len(media_list)
            for media_type, media_list in self.bulk_media.items()
        }

        deduplicated_messages = "\n".join(dict.fromkeys(self.warnings))
        return imported_counts, deduplicated_messages

    def _process_media_data(self, media_data, media_type):
        """Process media data for a specific type (anime/manga)."""
        logger.info("Processing %s from AniList", media_type)

        for status_list in media_data["lists"]:
            if not status_list["isCustomList"]:
                for content in status_list["entries"]:
                    try:
                        self._process_entry(content, media_type)
                    except Exception as e:
                        msg = f"Error processing history entry: {content}"
                        raise MediaImportUnexpectedError(msg) from e

    def _process_entry(self, content, media_type):
        """Process a single entry from AniList."""
        if content["media"]["idMal"] is None:
            title = content["media"]["title"]["userPreferred"]
            self.warnings.append(f"{title}: No matching MAL ID.")
            return

        # Check if we should process this entry based on mode
        if not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            media_type,
            Sources.MAL.value,
            str(content["media"]["idMal"]),
            self.mode,
        ):
            return

        if content["status"] in ("CURRENT", "REPEATING"):
            status = Status.IN_PROGRESS.value
        else:
            status = content["status"].capitalize()

        item, _ = app.models.Item.objects.get_or_create(
            media_id=str(content["media"]["idMal"]),
            source=Sources.MAL.value,
            media_type=media_type,
            defaults={
                "title": content["media"]["title"]["userPreferred"],
                "image": content["media"]["coverImage"]["large"],
            },
        )
        model = apps.get_model(app_label="app", model_name=media_type)
        updated_at = (
            timezone.now()
            if content["updatedAt"] == 0
            else timezone.datetime.fromtimestamp(content["updatedAt"], tz=UTC)
        )

        repeats_count = content["repeat"]
        if content["status"] == "REPEATING" and repeats_count == 0:
            repeats_count = 1

        if repeats_count >= 1:
            for _ in range(repeats_count):
                max_progress = content["media"].get("episodes") or content["media"].get(
                    "chapters",
                )

                instance = model(
                    item=item,
                    user=self.user,
                    score=content["score"],
                    progress=max_progress or 0,
                    status=Status.IN_PROGRESS.value,
                    start_date=self._get_date(content["startedAt"]),
                    end_date=None,
                    notes=content["notes"] or "",
                )
                instance._history_date = updated_at
                self.bulk_media[media_type].append(instance)

        instance = model(
            item=item,
            user=self.user,
            score=content["score"],
            progress=content["progress"] or 0,
            status=status,
            start_date=self._get_date(content["startedAt"]),
            end_date=self._get_date(content["completedAt"]),
            notes=content["notes"] or "",
        )
        instance._history_date = updated_at

        self.bulk_media[media_type].append(instance)

    def _get_date(self, date_dict):
        """Return date object from date dict."""
        if not date_dict["year"]:
            return None

        month = date_dict["month"] or 1
        day = date_dict["day"] or 1

        return timezone.datetime(
            year=date_dict["year"],
            month=month,
            day=day,
            hour=0,
            minute=0,
            second=0,
            tzinfo=timezone.get_current_timezone(),
        )
