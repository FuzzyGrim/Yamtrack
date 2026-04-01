import json
import logging
import re

from app.models import MediaTypes

from .base import BaseWebhookProcessor

logger = logging.getLogger(__name__)


class PlexWebhookProcessor(BaseWebhookProcessor):
    """Processor for Plex webhook events."""

    def process_payload(self, payload, user):
        """Process the incoming Plex webhook payload."""
        logger.debug("Received Plex webhook payload: %s", json.dumps(payload, indent=2))

        event_type = payload.get("event")
        if not self._is_supported_event(payload.get("event")):
            logger.debug("Ignoring Plex webhook event type: %s", event_type)
            return

        payload_user = payload["Account"]["title"].strip().lower()
        if not self._is_valid_user(payload_user, user):
            logger.debug(
                "Ignoring Plex webhook event for user %s: not a valid user",
                payload_user,
            )
            return

        ids = self._extract_external_ids(payload)
        logger.info("Extracted IDs from payload: %s", ids)

        if not any(ids.values()):
            logger.warning("Ignoring Plex webhook call because no ID was found.")
            return

        if self._is_rating_event(payload):
            self._process_rating(payload, user, ids)
        else:
            self._process_media(payload, user, ids)

    def _is_supported_event(self, event_type):
        return event_type in ("media.scrobble", "media.play", "media.rate")

    def _is_valid_user(self, payload_user, user):
        stored_usernames = [
            u.strip().lower()
            for u in (user.plex_usernames or "").split(",")
            if u.strip()
        ]
        logger.debug(
            "Checking if payload user '%s' is in stored usernames: %s",
            payload_user,
            stored_usernames,
        )
        return payload_user in stored_usernames

    def _is_played(self, payload):
        return payload["event"] == "media.scrobble"

    def _is_rating_event(self, payload):
        """Check if this is a rating event."""
        return payload.get("event") == "media.rate"

    def _process_rating(self, payload, user, ids):
        """Process rating event."""
        media_type = self._get_media_type(payload)
        if not media_type:
            logger.debug("Ignoring unsupported media type for rating")
            return

        rating = self._get_rating_from_payload(payload)
        logger.info("Processing rating event: %s with rating %s", media_type, rating)

        if user.anime_enabled:
            mapping_data = self._fetch_mapping_data()

            if ids.get("anidb_id"):
                matching_entry = mapping_data.get(ids["anidb_id"])
                if matching_entry and "mal_id" in matching_entry:
                    mal_id = self._parse_mal_id(matching_entry["mal_id"])
                    logger.info(
                        "Detected anime via AniDB ID: %s, MAL ID: %s",
                        ids["anidb_id"],
                        mal_id,
                    )
                    self._handle_rating(
                        MediaTypes.ANIME.value, mal_id, "mal", rating, user
                    )
                    return
                else:
                    logger.debug(
                        "AniDB ID %s not found in mapping or has no MAL ID",
                        ids["anidb_id"],
                    )

            tvdb_id = ids.get("tvdb_id")
            if tvdb_id and media_type == MediaTypes.TV.value:
                season_number = payload["Metadata"].get("parentIndex") or 1
                mal_id, _ = self._get_mal_id_from_tvdb(
                    mapping_data, int(tvdb_id), season_number, 1
                )
                if mal_id:
                    logger.info(
                        "Detected anime TV via TVDB ID: %s S%d, MAL ID: %s",
                        tvdb_id,
                        season_number,
                        mal_id,
                    )
                    self._handle_rating(
                        MediaTypes.ANIME.value, mal_id, "mal", rating, user
                    )
                    return
                else:
                    logger.debug(
                        "TVDB ID %s S%d not found in anime mapping",
                        tvdb_id,
                        season_number,
                    )

            tmdb_id = ids.get("tmdb_id")
            imdb_id = ids.get("imdb_id")

            if tmdb_id:
                mal_id = self._get_mal_id_from_tmdb_movie(mapping_data, tmdb_id)
                if mal_id:
                    logger.info(
                        "Detected anime movie via TMDB ID: %s, MAL ID: %s",
                        tmdb_id,
                        mal_id,
                    )
                    self._handle_rating(
                        MediaTypes.ANIME.value, mal_id, "mal", rating, user
                    )
                    return
                else:
                    logger.debug(
                        "TMDB ID %s not found in anime mapping",
                        tmdb_id,
                    )

            if imdb_id:
                mal_id = self._get_mal_id_from_imdb(mapping_data, imdb_id)
                if mal_id:
                    logger.info(
                        "Detected anime movie via IMDB ID: %s, MAL ID: %s",
                        imdb_id,
                        mal_id,
                    )
                    self._handle_rating(
                        MediaTypes.ANIME.value, mal_id, "mal", rating, user
                    )
                    return
                else:
                    logger.debug(
                        "IMDB ID %s not found in anime mapping",
                        imdb_id,
                    )

        media_id = None
        source = None

        if ids.get("tmdb_id"):
            media_id = ids["tmdb_id"]
            source = "tmdb"
        elif ids.get("imdb_id"):
            media_id = ids["imdb_id"]
            source = "imdb"
        else:
            logger.warning("No valid ID found for rating event")
            return

        self._handle_rating(media_type, media_id, source, rating, user)

    def _get_media_type(self, payload):
        media_type = payload["Metadata"].get("type")
        if not media_type:
            return None

        return self.MEDIA_TYPE_MAPPING.get(media_type.title())

    def _get_media_title(self, payload):
        """Get media title from payload."""
        title = None

        if self._get_media_type(payload) == MediaTypes.TV.value:
            series_name = payload["Metadata"].get("grandparentTitle")
            season_number = payload["Metadata"].get("parentIndex")
            episode_number = payload["Metadata"].get("index")
            title = f"{series_name} S{season_number:02d}E{episode_number:02d}"

        elif self._get_media_type(payload) == MediaTypes.MOVIE.value:
            title = payload["Metadata"].get("title")

        return title

    def _extract_external_ids(self, payload):
        guids = payload["Metadata"].get("Guid", [])
        guid = payload["Metadata"].get("guid", None)

        def get_id(prefix):
            return next(
                (
                    guid["id"].replace(f"{prefix}://", "")
                    for guid in guids
                    if guid["id"].startswith(f"{prefix}://")
                ),
                None,
            )

        def extract_hama_anidb_id(guid):
            """Extract the AniDB ID from a Hama agent GUID string.

            e.g., "com.plexapp.agents.hama://anidb-12834/1/2?lang=en" -> "12834"
            """
            if guid and "hama://anidb-" in guid:
                match = re.search(r"anidb-(\d+)", guid)
                if match:
                    return match.group(1)
            return None

        return {
            "tmdb_id": get_id("tmdb"),
            "imdb_id": get_id("imdb"),
            "tvdb_id": get_id("tvdb"),
            "anidb_id": extract_hama_anidb_id(guid),
        }
