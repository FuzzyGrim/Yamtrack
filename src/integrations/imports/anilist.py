import logging
from collections import defaultdict
from datetime import UTC

import requests
from django.apps import apps
from django.urls import reverse
from django.utils import timezone

import app
from app import helpers as app_helpers
from app.models import MediaTypes, Sources, Status
from app.providers import credentials, services
from integrations import import_progress
from integrations.imports import helpers
from integrations.imports.helpers import MediaImportError, MediaImportUnexpectedError

logger = logging.getLogger(__name__)


def get_token(request, redirect_uri=None):
    """View for getting the AniList OAuth2 token."""
    code = request.GET["code"]

    url = "https://anilist.co/api/v2/oauth/token"

    params = {
        "client_id": credentials.get("anilist", "client_id"),
        "client_secret": credentials.get("anilist", "client_secret"),
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri
        or app_helpers.build_absolute_app_url(
            request,
            reverse("import_anilist_private"),
        ),
    }

    try:
        token_response = app.providers.services.api_request(
            "ANILIST",
            "POST",
            url,
            params=params,
        )
    except services.ProviderAPIError as error:
        if error.status_code == requests.codes.unauthorized:
            msg = "Invalid Anilist secret key."
            raise MediaImportError(msg) from error
        raise

    return {
        "access_token": token_response["access_token"],
        "username": get_username_from_oauth(token_response["access_token"]),
    }


def get_username_from_oauth(access_token):
    """Get AniList username from access token."""
    query = """
    query {
        Viewer {
            name
        }
    }
    """

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    try:
        response = app.providers.services.api_request(
            "ANILIST",
            "POST",
            "https://graphql.anilist.co",
            headers=headers,
            params={"query": query},
        )
    except services.ProviderAPIError as error:
        if error.status_code == requests.codes.unauthorized:
            msg = "Invalid AniList access token."
            raise MediaImportError(msg) from error
        raise

    return response["data"]["Viewer"]["name"]


def importer(token, user, mode, username):
    """Import anime and manga ratings from Anilist."""
    anilist_importer = AniListImporter(token, user, mode, username)
    return anilist_importer.import_data()


class AniListImporter:
    """Class to handle importing user data from AniList."""

    def __init__(self, token, user, mode, username):
        """Initialize the importer with username, user, and mode.

        Args:
            username (str): AniList username to import from
            token (str): Encrypted access token for private imports (optional)
            user: Django user object to import data for
            mode (str): Import mode ("new" or "overwrite")
        """
        self.username = username
        self.token = token
        self.user = user
        self.mode = mode
        self.warnings = []

        if self.token is not None:
            self.token = helpers.decrypt_or_raise(self.token)

        # Track existing media for "new" mode
        self.existing_media = helpers.get_existing_media(user)
        self.deleted_media = helpers.get_deleted_media(user)

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

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        logger.info("Fetching anime and manga from AniList account")

        try:
            response = app.providers.services.api_request(
                "ANILIST",
                "POST",
                url,
                params={"query": query, "variables": variables},
                headers=headers,
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

        self._progress_total = sum(
            len(status_list["entries"])
            for media_data in (response["data"]["anime"], response["data"]["manga"])
            for status_list in media_data["lists"]
            if not status_list["isCustomList"]
        )
        self._progress_current = 0

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
                    self._progress_current += 1
                    import_progress.report(
                        self._progress_current,
                        self._progress_total,
                        "AniList",
                    )
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
            deleted_media=self.deleted_media,
        ):
            return

        if content["status"] in ("CURRENT", "REPEATING"):
            status = Status.IN_PROGRESS.value
        else:
            status = content["status"].capitalize()

        item, _ = helpers.get_or_create_item_across_buckets(
            user=self.user,
            media_id=str(content["media"]["idMal"]),
            source=Sources.MAL.value,
            media_type=media_type,
            defaults={
                **app.models.Item.title_fields_from_metadata(
                    {
                        "title": content["media"]["title"]["userPreferred"],
                        "localized_title": content["media"]["title"]["userPreferred"],
                        "original_title": None,
                    },
                ),
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
        # AniList uses zero for an unrated entry; Floppy permits a real zero.
        score = content["score"] or None
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
                    score=score,
                    progress=max_progress or 0,
                    status=Status.COMPLETED.value,
                    start_date=self._get_date(content["startedAt"]),
                    end_date=None,
                    notes=content["notes"] or "",
                )
                instance._history_date = updated_at
                self.bulk_media[media_type].append(instance)

        instance = model(
            item=item,
            user=self.user,
            score=score,
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
