import json
import logging
from enum import StrEnum

from app.models import MediaTypes

from .base import BaseWebhookProcessor

logger = logging.getLogger(__name__)


class JellyfinEvent(StrEnum):
    """Jellyfin webhook event names."""

    PLAY = "Play"
    STOP = "Stop"
    MARK_PLAYED = "MarkPlayed"
    MARK_UNPLAYED = "MarkUnplayed"


class JellyfinWebhookProcessor(BaseWebhookProcessor):
    """Processor for Jellyfin webhook events."""

    def process_payload(self, payload, user):
        """Process the incoming Jellyfin webhook payload."""
        logger.debug(
            "Processing Jellyfin webhook payload: %s",
            json.dumps(payload, indent=2),
        )

        event_type = payload.get("Event")
        if not self._is_supported_event(event_type, user):
            logger.debug("Ignoring Jellyfin webhook event type: %s", event_type)
            return

        ids = self._extract_external_ids(payload)
        logger.info("Extracted IDs from payload: %s", ids)

        if not any(ids.values()):
            logger.warning("Ignoring Jellyfin webhook call because no ID was found.")
            return

        self._process_media(payload, user, ids)

    def _is_supported_event(self, event_type, user=None):
        if event_type in {JellyfinEvent.PLAY, JellyfinEvent.STOP}:
            return True

        if user is None:
            return False

        if event_type == JellyfinEvent.MARK_PLAYED:
            return user.jellyfin_mark_played_enabled

        if event_type == JellyfinEvent.MARK_UNPLAYED:
            return user.jellyfin_mark_unplayed_enabled

        return False

    def _is_played(self, payload):
        if payload["Event"] == JellyfinEvent.MARK_PLAYED:
            return True

        if payload["Event"] == JellyfinEvent.MARK_UNPLAYED:
            return False

        return payload["Item"]["UserData"]["Played"]

    def _is_unplayed(self, payload):
        return payload["Event"] == JellyfinEvent.MARK_UNPLAYED

    def _get_media_type(self, payload):
        return self.MEDIA_TYPE_MAPPING.get(payload["Item"].get("Type"))

    def _get_media_title(self, payload):
        """Get media title from payload."""
        title = None

        if self._get_media_type(payload) == MediaTypes.TV.value:
            series_name = payload["Item"].get("SeriesName")
            season_number = payload["Item"].get("ParentIndexNumber")
            episode_number = payload["Item"].get("IndexNumber")
            title = f"{series_name} S{season_number:02d}E{episode_number:02d}"

        elif self._get_media_type(payload) == MediaTypes.MOVIE.value:
            movie_name = payload["Item"].get("Name")
            year = payload["Item"].get("ProductionYear")

            title = f"{movie_name} ({year})" if movie_name and year else movie_name

        return title

    def _get_episode_number(self, payload):
        return payload["Item"].get("IndexNumber")

    def _extract_external_ids(self, payload):
        provider_ids = payload["Item"].get("ProviderIds", {})
        return {
            "tmdb_id": provider_ids.get("Tmdb"),
            "imdb_id": provider_ids.get("Imdb"),
            "tvdb_id": provider_ids.get("Tvdb"),
        }
