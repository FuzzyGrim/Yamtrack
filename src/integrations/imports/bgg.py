import logging
import time
from collections import defaultdict

import requests
from django.conf import settings
from django.utils import timezone

import app
from app.models import MediaTypes, Sources, Status
from integrations.imports import helpers
from integrations.imports.helpers import MediaImportError

logger = logging.getLogger(__name__)
base_url = "https://boardgamegeek.com/xmlapi2"


def importer(username, user, mode):
    """Import boardgames from BoardGameGeek."""
    bgg_importer = BGGImporter(username, user, mode)
    return bgg_importer.import_data()


class BGGImporter:
    """Class to handle importing user data from BoardGameGeek."""

    def __init__(self, username, user, mode):
        """Initialize the importer with username, user, and mode.

        Args:
            username (str): BGG username to import from
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
            "Initialized BGG importer for user %s with mode %s",
            username,
            mode,
        )

    def import_data(self):
        """Import all user data from BGG."""
        logger.info("Fetching boardgames from BGG account")

        max_retries = 5
        base_delay = 15
        response = None
        for attempt in range(max_retries):
            try:
                response = app.providers.services.api_request(
                    Sources.BGG.value,
                    "GET",
                    f"{base_url}/collection",
                    params={
                        "username": self.username,
                        "subtype": "boardgame",
                        "stats": 1,
                    },
                    headers={"Authorization": f"Bearer {settings.BGG_API_TOKEN}"},
                    response_format="xml",
                )

                # BGG may return an accepted/queued XML message before data is ready.
                message_elem = response.find("message")
                if message_elem is not None and message_elem.text:
                    message_text = message_elem.text.lower()
                    if (
                        "Please try again later" in message_text
                        and attempt < max_retries - 1
                    ):
                        delay = base_delay * (2**attempt)
                        time.sleep(delay)
                        continue

                break

            except requests.exceptions.RequestException as error:
                if attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)
                    time.sleep(delay)
                    continue
                msg = (
                    "Hit max retries while fetching BGG collection for user "
                    f"{self.username}"
                )
                raise MediaImportError(msg) from error

        if response is None:
            msg = f"Failed to fetch BGG collection for user {self.username}"
            raise MediaImportError(msg)

        for item in response.findall(".//item"):
            self._process_boardgame(item)

        helpers.cleanup_existing_media(self.to_delete, self.user)
        helpers.bulk_create_media(self.bulk_media, self.user)

        imported_counts = {
            media_type: len(media_list)
            for media_type, media_list in self.bulk_media.items()
        }

        deduplicated_messages = "\n".join(dict.fromkeys(self.warnings))
        return imported_counts, deduplicated_messages

    def _process_boardgame(self, boardgame_data):
        """Process a single boardgame from BGG."""
        game_id = boardgame_data.get("objectid")
        name_elem = boardgame_data.find("name")
        if name_elem is None:
            logger.warning("Boardgame with ID %s has no name element", game_id)
            return

        # Check if we should process this entry based on mode
        if not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            MediaTypes.BOARDGAME.value,
            Sources.BGG.value,
            game_id,
            self.mode,
        ):
            return

        name = name_elem.text
        rating_elem = boardgame_data.find("stats/rating")
        rating = rating_elem.get("value") if rating_elem is not None else None
        if rating == "N/A":
            rating = None

        status_elem = boardgame_data.find("status")
        num_plays_elem = boardgame_data.find("numplays")
        status_elem = boardgame_data.find("status")
        thumbnail_elem = boardgame_data.find("thumbnail")
        image_elem = boardgame_data.find("image")

        if image_elem is not None and image_elem.text:
            image = image_elem.text
        elif thumbnail_elem is not None and thumbnail_elem.text:
            image = thumbnail_elem.text
        else:
            image = None

        status = self._determine_status(status_elem)

        item, _ = app.models.Item.objects.get_or_create(
            media_id=game_id,
            source=Sources.BGG.value,
            media_type=MediaTypes.BOARDGAME.value,
            defaults={
                "title": name,
                "image": image,
            },
        )
        boardgame = app.models.BoardGame(
            item=item,
            user=self.user,
            status=status,
            score=rating,
            progress=num_plays_elem.text if num_plays_elem is not None else None,
            notes="Imported from BGG",
            start_date=None,
            end_date=None,
        )
        updated_at = (
            timezone.now()
            if status_elem is None or status_elem.get("lastmodified") is None
            else timezone.datetime.fromisoformat(status_elem.get("lastmodified"))
        )
        boardgame._history_date = updated_at

        self.bulk_media[MediaTypes.BOARDGAME.value].append(boardgame)

    def _determine_status(self, status_elem):
        """Determine the status of the boardgame based on BGG data."""
        if status_elem is None:
            return Status.PLANNING.value
        if status_elem.get("own") == "1":
            return Status.IN_PROGRESS.value
        if status_elem.get("prevowned") == "1" or status_elem.get("fortrade") == "1":
            return Status.DROPPED.value
        if (
            status_elem.get("want") == "1"
            or status_elem.get("wanttoplay") == "1"
            or status_elem.get("wanttobuy") == "1"
            or status_elem.get("wishlist") == "1"
            or status_elem.get("preordered") == "1"
        ):
            return Status.PLANNING.value
        return Status.PLANNING.value
