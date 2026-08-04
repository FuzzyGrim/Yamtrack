import logging
import time
from collections import defaultdict

import requests
from django.conf import settings
from django.utils.dateparse import parse_datetime

import app
from app.models import MediaTypes, Sources, Status
from app.providers import services
from integrations.imports import helpers
from integrations.imports.helpers import MediaImportError, MediaImportUnexpectedError

logger = logging.getLogger(__name__)

WUTCH_API_BASE_URL = "https://wutch.tv/be/api/v1"
BULK_PAGE_SIZE = 1000

# Throttling for the per-show/movie detail lookups used to resolve TMDB ids
# (there's no bulk endpoint for this, so a large history can mean many
# distinct titles -> many detail requests). A short pause between calls,
# plus a longer pause every DETAIL_BATCH_SIZE calls, keeps us polite to
# Wutch's API without slowing small imports down noticeably.
DETAIL_REQUEST_DELAY = 0.3
DETAIL_BATCH_SIZE = 50
DETAIL_BATCH_PAUSE = 3

# Wutch media types (as returned by the API) mapped to Yamtrack media types.
WUTCH_TYPE_TO_MEDIA_TYPE = {
    "show": MediaTypes.TV.value,
    "movie": MediaTypes.MOVIE.value,
}

# Detail endpoint segment per Yamtrack media type, used to resolve a TMDB id
# from a Wutch slug (e.g. /tv-show/{slug} or /movie/{slug}).
DETAIL_ENDPOINT_SEGMENT = {
    MediaTypes.TV.value: "tv-show",
    MediaTypes.MOVIE.value: "movie",
}


def importer(username, user, mode):
    """Import the user's data from Wutch.

    Wutch has no OAuth: the API key is a shared app-level secret read
    from settings.WUTCH_API, and username is the public Wutch username.

    Args:
        username (str): Wutch username to import from
        user: Django user object to import data for
        mode (str): Import mode ("new" or "overwrite")
    """
    wutch_importer = WutchImporter(username, user, mode)
    return wutch_importer.import_data()


class WutchImporter:
    """Class to handle importing user data from Wutch.tv."""

    def __init__(self, username, user, mode):
        """Initialize the importer with user details and mode.

        Args:
            username (str): Wutch username to import from
            user: Django user object to import data for
            mode (str): Import mode ("new" or "overwrite")
        """
        self.username = username
        self.user = user
        self.mode = mode
        self.api_key = settings.WUTCH_API
        self.user_base_url = f"{WUTCH_API_BASE_URL}/user/{username}"
        self.warnings = []

        # Track existing media to handle "new" mode correctly
        self.existing_media = helpers.get_existing_media(user)

        # Track media IDs to delete in overwrite mode
        self.to_delete = defaultdict(lambda: defaultdict(set))

        # Track bulk creation lists for each media type
        self.bulk_media = defaultdict(list)

        # Track media instances being created
        self.media_instances = defaultdict(lambda: defaultdict(list))

        # Cache slug -> tmdb_id (or None) resolutions to avoid repeat lookups
        # for the same show/movie across many history entries.
        self.tmdb_id_cache = {}

        # Count of actual (non-cached) detail lookups made this run, used
        # to pace requests to the Wutch API (see DETAIL_BATCH_SIZE below).
        self.detail_requests_made = 0

        logger.info(
            "Initialized Wutch importer for user %s with mode %s",
            username,
            mode,
        )

    def import_data(self):
        """Import all user data from Wutch."""
        self.process_history()
        self.process_watchlist()

        helpers.cleanup_existing_media(self.to_delete, self.user)
        helpers.bulk_create_media(self.bulk_media, self.user)

        imported_counts = {
            media_type: len(media_list)
            for media_type, media_list in self.bulk_media.items()
        }
        deduplicated_messages = "\n".join(dict.fromkeys(self.warnings))

        return imported_counts, deduplicated_messages

    def _make_api_request(self, url, params=None):
        """Make a request to the Wutch API with the api key attached."""
        request_params = dict(params or {})
        request_params["api-key"] = self.api_key

        try:
            return services.api_request(
                "WUTCH",
                "GET",
                url,
                params=request_params,
            )
        except services.ProviderAPIError as error:
            if error.status_code == requests.codes.unauthorized:
                msg = "Invalid Wutch API key."
                raise MediaImportError(msg) from error
            if error.status_code == requests.codes.not_found:
                msg = f"Wutch user '{self.username}' not found."
                raise MediaImportError(msg) from error
            raise

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def _get_paginated_history(self):
        """Fetch and flatten the full watch history from Wutch.

        The history endpoint groups entries by date (most recent first),
        so we flatten across all pages and return entries oldest-first so
        they can be processed chronologically, matching Trakt's behavior.
        """
        page = 1
        all_entries = []
        total_pages = None

        while total_pages is None or page <= total_pages:
            url = f"{self.user_base_url}/watch-history"
            params = {
                "page": page,
                "limit": BULK_PAGE_SIZE,
                "type": "all",
                "rated": "all",
            }
            response = self._make_api_request(url, params=params)

            for day_entries in response.get("data", {}).values():
                all_entries.extend(day_entries)

            pagination = response.get("pagination", {})
            total_pages = pagination.get("total_pages", page)

            logger.info(
                "Retrieved page %s/%s of history for user %s",
                page,
                total_pages,
                self.username,
            )
            page += 1

        # Oldest first, matching Trakt's chronological processing order.
        all_entries.sort(key=lambda entry: entry["watched_at"]["date"])
        return all_entries

    def process_history(self):
        """Process watch history from Wutch."""
        logger.info("Importing watch history for user %s", self.username)
        full_history = self._get_paginated_history()

        for entry in full_history:
            data = entry["data"]
            watched_at = self._parse_wutch_datetime(entry["watched_at"])
            try:
                if data["type"] == "movie":
                    logger.info(
                        "Processing movie %s watched at %s",
                        data["name"],
                        watched_at,
                    )
                    self.process_watched_movie(data, watched_at)
                elif data["type"] == "tv_show_episode":
                    logger.info(
                        "Processing episode %s S%sE%s watched at %s",
                        data.get("tv_show_name"),
                        data.get("season_number"),
                        data.get("episode_number"),
                        watched_at,
                    )
                    self.process_watched_episode(data, watched_at)
            except Exception as e:
                msg = f"Error processing history entry: {entry}"
                raise MediaImportUnexpectedError(msg) from e

    def _parse_wutch_datetime(self, watched_at):
        """Parse Wutch's {date, timezone_type, timezone} structure to an aware datetime."""
        # Wutch always reports UTC; the date string has no offset of its own.
        dt = parse_datetime(watched_at["date"].replace(" ", "T") + "+00:00")
        return dt

    def _get_date(self, dt):
        """Strip seconds/microseconds, mirroring Trakt's history date handling."""
        return dt.replace(second=0, microsecond=0)

    # ------------------------------------------------------------------
    # TMDB id resolution (Wutch doesn't expose a tmdb id directly on
    # history/watchlist entries, only a slug - so we look up the detail
    # page for the show/movie and cache the result).
    # ------------------------------------------------------------------

    def _throttle_detail_request(self):
        """Pace per-title detail lookups: a short delay after each one,
        plus a longer pause every DETAIL_BATCH_SIZE requests.
        """
        self.detail_requests_made += 1
        if self.detail_requests_made % DETAIL_BATCH_SIZE == 0:
            time.sleep(DETAIL_BATCH_PAUSE)
        else:
            time.sleep(DETAIL_REQUEST_DELAY)

    def _resolve_tmdb_id(self, media_type, slug, title=None):
        """Resolve a TMDB id from a Wutch slug via its detail endpoint."""
        cache_key = (media_type, slug)
        if cache_key in self.tmdb_id_cache:
            return self.tmdb_id_cache[cache_key]

        segment = DETAIL_ENDPOINT_SEGMENT.get(media_type)
        if not segment:
            self.tmdb_id_cache[cache_key] = None
            return None

        url = f"{WUTCH_API_BASE_URL}/{segment}/{slug}"
        try:
            detail = self._make_api_request(url)
        except services.ProviderAPIError as error:
            if error.status_code == requests.codes.not_found:
                self.warnings.append(
                    f"{title or slug}: not found on Wutch ({slug}).",
                )
                self.tmdb_id_cache[cache_key] = None
                return None
            raise
        finally:
            self._throttle_detail_request()

        tmdb_id = (detail.get("external_links") or {}).get("tmdb_id")
        if not tmdb_id:
            self.warnings.append(
                f"{title or slug}: no {Sources.TMDB.label} ID found on Wutch.",
            )
            tmdb_id = None
        else:
            tmdb_id = str(tmdb_id)

        self.tmdb_id_cache[cache_key] = tmdb_id
        return tmdb_id

    def _get_metadata(self, media_type, tmdb_id, title, season_number=None):
        """Get metadata for a media item from TMDB."""
        try:
            kwargs = {}
            if season_number is not None:
                kwargs["season_numbers"] = [season_number]

            return services.get_media_metadata(
                media_type,
                tmdb_id,
                Sources.TMDB.value,
                **kwargs,
            )
        except services.ProviderAPIError as error:
            if error.status_code == requests.codes.not_found:
                if media_type == MediaTypes.SEASON.value:
                    title = f"{title} S{season_number}"
                self.warnings.append(
                    f"{title}: not found in {Sources.TMDB.label} with ID {tmdb_id}.",
                )
                return None
            raise

    def _get_or_create_item(
        self,
        media_type,
        tmdb_id,
        metadata,
        season_number=None,
        episode_number=None,
    ):
        """Get or create an item in the database."""
        item_kwargs = {
            "media_id": tmdb_id,
            "source": Sources.TMDB.value,
            "media_type": media_type,
        }

        if season_number is not None:
            item_kwargs["season_number"] = season_number

        if episode_number is not None:
            item_kwargs["episode_number"] = episode_number

        defaults = {
            "title": metadata["title"],
            "image": metadata["image"],
        }

        item, _ = app.models.Item.objects.get_or_create(
            **item_kwargs,
            defaults=defaults,
        )

        return item

    # ------------------------------------------------------------------
    # Movies / episodes (history)
    # ------------------------------------------------------------------

    def process_watched_movie(self, data, watched_at):
        """Process a single movie watch event."""
        tmdb_id = self._resolve_tmdb_id(MediaTypes.MOVIE.value, data["slug"], data["name"])
        if not tmdb_id:
            return

        if not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            MediaTypes.MOVIE.value,
            Sources.TMDB.value,
            tmdb_id,
            self.mode,
        ):
            return

        metadata = self._get_metadata(MediaTypes.MOVIE.value, tmdb_id, data["name"])
        if not metadata:
            return

        item = self._get_or_create_item(MediaTypes.MOVIE.value, tmdb_id, metadata)
        key = f"{tmdb_id}"

        movie_obj = app.models.Movie(
            item=item,
            user=self.user,
            end_date=self._get_date(watched_at),
            status=Status.COMPLETED.value,
            progress=1,
        )
        movie_obj._history_date = watched_at

        self.media_instances[MediaTypes.MOVIE.value][key].append(movie_obj)
        self.bulk_media[MediaTypes.MOVIE.value].append(movie_obj)

    def _get_episode_image(self, episode_number, season_metadata):
        """Extract episode image URL from season metadata."""
        for episode in season_metadata["episodes"]:
            if episode["episode_number"] == episode_number:
                if episode.get("still_path"):
                    return f"https://image.tmdb.org/t/p/w500{episode['still_path']}"
                break
        return settings.IMG_NONE

    def process_watched_episode(self, data, watched_at):
        """Process a single episode watch event."""
        show_slug = data["tv_show_slug"]
        show_title = data.get("tv_show_name") or show_slug

        tmdb_id = self._resolve_tmdb_id(MediaTypes.TV.value, show_slug, show_title)
        if not tmdb_id:
            return

        if not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            MediaTypes.TV.value,
            Sources.TMDB.value,
            tmdb_id,
            self.mode,
        ):
            return

        season_number = data["season_number"]
        episode_number = data["episode_number"]

        tv_metadata = self._get_metadata(MediaTypes.TV.value, tmdb_id, show_title)
        if not tv_metadata:
            return

        season_metadata = self._get_metadata(
            MediaTypes.SEASON.value,
            tmdb_id,
            show_title,
            season_number,
        )
        if not season_metadata:
            return

        episode_exists = any(
            ep["episode_number"] == episode_number for ep in season_metadata["episodes"]
        )
        if not episode_exists:
            item_identifier = f"{show_title} S{season_number}E{episode_number}"
            self.warnings.append(
                f"{item_identifier}: not found in {Sources.TMDB.label} "
                f"with ID {tmdb_id}.",
            )
            return

        episode_image = self._get_episode_image(episode_number, season_metadata)

        tv_item = self._get_or_create_item(MediaTypes.TV.value, tmdb_id, tv_metadata)
        tv_key = f"{tmdb_id}"

        if tv_key not in self.media_instances[MediaTypes.TV.value]:
            tv_obj = app.models.TV(
                item=tv_item,
                user=self.user,
                status=Status.IN_PROGRESS.value,
            )
            tv_obj._history_date = watched_at
            self.bulk_media[MediaTypes.TV.value].append(tv_obj)
            self.media_instances[MediaTypes.TV.value][tv_key] = [tv_obj]
        else:
            tv_obj = self.media_instances[MediaTypes.TV.value][tv_key][0]

        season_item = self._get_or_create_item(
            MediaTypes.SEASON.value,
            tmdb_id,
            season_metadata,
            season_number,
        )

        season_key = f"{tmdb_id}:{season_number}"
        if season_key not in self.media_instances[MediaTypes.SEASON.value]:
            season_obj = app.models.Season(
                item=season_item,
                user=self.user,
                related_tv=tv_obj,
                status=Status.IN_PROGRESS.value,
            )
            season_obj._history_date = watched_at
            self.bulk_media[MediaTypes.SEASON.value].append(season_obj)
            self.media_instances[MediaTypes.SEASON.value][season_key] = [season_obj]
        else:
            season_obj = self.media_instances[MediaTypes.SEASON.value][season_key][0]

        episode_metadata = {
            "title": tv_metadata["title"],
            "image": episode_image,
        }
        episode_item = self._get_or_create_item(
            MediaTypes.EPISODE.value,
            tmdb_id,
            episode_metadata,
            season_number,
            episode_number,
        )

        episode_obj = app.models.Episode(
            item=episode_item,
            related_season=season_obj,
            end_date=self._get_date(watched_at),
        )
        episode_obj._history_date = watched_at
        ep_key = f"{tmdb_id}:{season_number}:{episode_number}"
        self.media_instances[MediaTypes.EPISODE.value][ep_key].append(episode_obj)
        self.bulk_media[MediaTypes.EPISODE.value].append(episode_obj)

        self._update_completion_status(
            season_obj,
            tv_obj,
            season_number,
            episode_number,
            season_metadata,
            tv_metadata,
        )

    def _update_completion_status(
        self,
        season_obj,
        tv_obj,
        season_number,
        episode_number,
        season_metadata,
        tv_metadata,
    ):
        """Update completion status for season and TV show if applicable."""
        if episode_number == season_metadata["max_progress"]:
            season_obj.status = Status.COMPLETED.value

            last_season = tv_metadata.get("last_episode_season")
            if last_season and last_season == season_number:
                tv_obj.status = Status.COMPLETED.value

    # ------------------------------------------------------------------
    # Watchlist
    # ------------------------------------------------------------------

    def process_watchlist(self):
        """Process watchlist from Wutch."""
        logger.info("Importing watchlist for user %s", self.username)
        watchlist_endpoint = f"{self.user_base_url}/watchlist"
        watchlist_data = self._make_api_request(watchlist_endpoint)

        # The watchlist endpoint returns either a bare list or a
        # {"data": [...]} envelope depending on the Wutch deployment.
        entries = (
            watchlist_data.get("data", [])
            if isinstance(watchlist_data, dict)
            else watchlist_data
        )

        for entry in entries:
            try:
                self._process_watchlist_entry(entry)
            except Exception as e:
                msg = f"Error processing watchlist entry: {entry}"
                raise MediaImportUnexpectedError(msg) from e

    def _process_watchlist_entry(self, entry):
        """Process a single watchlist entry (show or movie)."""
        media_type = WUTCH_TYPE_TO_MEDIA_TYPE.get(entry.get("type"))
        if not media_type:
            return

        tmdb_id = self._resolve_tmdb_id(media_type, entry["slug"], entry.get("name"))
        if not tmdb_id:
            return

        if not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            media_type,
            Sources.TMDB.value,
            tmdb_id,
            self.mode,
        ):
            return

        metadata = self._get_metadata(media_type, tmdb_id, entry.get("name"))
        if not metadata:
            return

        item = self._get_or_create_item(media_type, tmdb_id, metadata)
        defaults = {"status": Status.PLANNING.value}

        key = f"{tmdb_id}"
        model_class = app.models.TV if media_type == MediaTypes.TV.value else app.models.Movie

        if key in self.media_instances[media_type]:
            for media_obj in self.media_instances[media_type][key]:
                for attr, value in defaults.items():
                    setattr(media_obj, attr, value)
        else:
            media_obj = model_class(
                item=item,
                user=self.user,
                **defaults,
            )
            # Wutch's watchlist doesn't return an "added at" timestamp, so
            # _history_date is intentionally left unset here; confirmed
            # acceptable to fall back to today's date via model defaults.
            self.bulk_media[media_type].append(media_obj)
            self.media_instances[media_type][key] = [media_obj]
