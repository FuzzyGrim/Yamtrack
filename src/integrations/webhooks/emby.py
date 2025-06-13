import json
import logging

from app.models import MediaTypes

from .base import BaseWebhookProcessor

logger = logging.getLogger(__name__)


class EmbyWebhookProcessor(BaseWebhookProcessor):
    """Processor for Emby webhook events."""

    def process_payload(self, payload, user):
        """Process the incoming Emby webhook payload."""
        logger.debug(
            "Processing Emby webhook payload: %s",
            json.dumps(payload, indent=2),
        )

        event_type = payload.get("Event")
        if not self._is_supported_event(event_type):
            logger.info("Ignoring Emby webhook event type: %s", event_type)
            return

        ids = self._extract_external_ids(payload)
        logger.debug("Extracted IDs from payload: %s", ids)

        if not any(ids.values()):
            logger.info("Ignoring Emby webhook call because no ID was found.")
            return

        self._process_media(payload, user, ids)

    def _is_supported_event(self, event_type):
        return event_type in ("playback.start", "playback.stop")

    def _is_played(self, payload):
        return payload.get("PlaybackInfo", {}).get("PlayedToCompletion", False) is True

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

    def _extract_external_ids(self, payload):
        provider_ids = payload["Item"].get("ProviderIds", {})
        return {
            "tmdb_id": provider_ids.get("Tmdb"),
            "imdb_id": provider_ids.get("Imdb"),
            "tvdb_id": provider_ids.get("Tvdb"),
        }
