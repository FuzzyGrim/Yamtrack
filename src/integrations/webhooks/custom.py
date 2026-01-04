import json
import logging

from app.models import MediaTypes

from .base import BaseWebhookProcessor

logger = logging.getLogger(__name__)


class CustomWebhookProcessor(BaseWebhookProcessor):
    """Processor for Custom webhook events."""

    def process_payload(self, payload, user):
        """Process the incoming Custom webhook payload."""
        logger.debug(
            "Processing Custom webhook payload: %s",
            json.dumps(payload, indent=2),
        )

        event_type = payload.get("event")
        if not self._is_supported_event(event_type):
            logger.debug("Ignoring Custom webhook event type: %s", event_type)
            return None

        ids = self._extract_external_ids(payload)
        logger.info("Extracted IDs from payload: %s", ids)

        return self._process_media(payload, user, ids, payload.get("tv_info"))

    def _is_supported_event(self, event_type):
        return event_type in ("start", "stop")

    def _is_played(self, payload):
        return payload.get("played", False) is True

    def _get_media_type(self, payload):
        return self.MEDIA_TYPE_MAPPING.get(payload.get("type"))

    def _get_media_title(self, payload):
        """Get media title from payload."""
        return payload.get("title", "")

    def _extract_external_ids(self, payload):
        external_ids = payload.get("external_ids", {})
        return {
            "tmdb_id": external_ids.get("tmdb_id"),
            "imdb_id": external_ids.get("imdb_id"),
            "tvdb_id": external_ids.get("tvdb_id"),
        }
