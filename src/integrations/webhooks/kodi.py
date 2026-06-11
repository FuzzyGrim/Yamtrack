import json
import logging
from enum import StrEnum

from app.models import MediaTypes

from .base import BaseWebhookProcessor

logger = logging.getLogger(__name__)

PERCENT_COMPLETE_THRESHOLD = 80


class KodiEvent(StrEnum):
    """Kodi webhook event names."""

    PLAYBACK_START = "start"
    PLAYBACK_STOP = "stop"
    PLAYBACK_END = "end"


class KodiWebhookProcessor(BaseWebhookProcessor):
    """Processor for Kodi webhook events."""

    def process_payload(self, payload, user):
        """Process the incoming Kodi webhook payload."""
        logger.debug(
            "Processing Kodi webhook payload: %s",
            json.dumps(payload, indent=2),
        )

        event_type = payload.get("event")
        if not self._is_supported_event(event_type):
            logger.debug("Ignoring Kodi webhook event type: %s", event_type)
            return

        ids = self._extract_external_ids(payload)
        logger.info("Extracted IDs from payload: %s", ids)

        if not any(ids.values()):
            logger.warning("Ignoring Kodi webhook call because no ID was found.")
            return

        self._process_media(payload, user, ids)

    def _is_supported_event(self, event_type):
        return event_type in {
            KodiEvent.PLAYBACK_START,
            KodiEvent.PLAYBACK_STOP,
            KodiEvent.PLAYBACK_END,
        }

    def _is_played(self, payload):
        if payload.get("event") == KodiEvent.PLAYBACK_END:
            return True

        if payload.get("event") == KodiEvent.PLAYBACK_STOP:
            percent = payload.get("progress", 0).get("percent", 0)
            if percent and percent > PERCENT_COMPLETE_THRESHOLD:
                return True
        return False

    def _get_media_type(self, payload):
        return self.MEDIA_TYPE_MAPPING.get(payload.get("mediaType").capitalize())

    def _get_media_title(self, payload):
        """Get media title from payload."""
        title = None

        if self._get_media_type(payload) == MediaTypes.TV.value:
            series_name = payload.get("tvShowTitle")
            season_number = payload.get("season")
            episode_number = payload.get("episode")
            title = f"{series_name} S{season_number:02d}E{episode_number:02d}"

        elif self._get_media_type(payload) == MediaTypes.MOVIE.value:
            movie_name = payload.get("title")
            year = payload.get("year")

            title = f"{movie_name} ({year})" if movie_name and year else movie_name

        return title

    def _get_episode_number(self, payload):
        return payload.get("episode")

    def _extract_external_ids(self, payload):
        provider_ids = payload.get("uniqueIds", {})
        return {
            "tmdb_id": provider_ids.get("tmdb"),
            "imdb_id": provider_ids.get("imdb"),
            "tvdb_id": provider_ids.get("tvdb"),
        }
