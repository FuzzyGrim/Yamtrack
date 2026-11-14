"""MDBList full-account import.

Imports a user's MDBList tracking data (watched history, watchlist, ratings,
dropped shows and collection) via the ``/sync/*`` and ``/watchlist/items``
endpoints, mirroring the Trakt importer's TMDB-based mapping. Lists are
handled separately by the recurring sync in ``lists.imports.mdblist``, which
reuses the shared client helpers defined here.

The MDBList sync endpoints are marked beta upstream, so entry parsing is
deliberately defensive: malformed entries are skipped with a warning instead
of aborting the import.
"""

import logging
from collections import defaultdict
from decimal import Decimal

import requests
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from simple_history.utils import bulk_update_with_history

import app
from app.models import MediaTypes, Sources, Status
from app.providers import services, tmdb
from integrations import import_progress
from integrations.imports import helpers
from integrations.imports.helpers import ConnectionAuthError, MediaImportError
from integrations.imports.trakt import TraktMetadataResolverMixin

logger = logging.getLogger(__name__)

MODE_STAGES = {"watchlist": "watchlist", "update_collection": "collection"}

MDBLIST_API_BASE_URL = "https://api.mdblist.com"
PAGE_SIZE = 500

MEDIATYPE_MAP = {
    "movie": MediaTypes.MOVIE.value,
    "show": MediaTypes.TV.value,
}


def request(api_key, path, params=None):
    """Make an MDBList API request, translating auth failures."""
    params = {**(params or {}), "apikey": api_key}
    url = f"{MDBLIST_API_BASE_URL}{path}"
    try:
        return services.api_request("MDBLIST", "GET", url, params=params)
    except requests.exceptions.HTTPError as error:
        status_code = getattr(error.response, "status_code", None)
        if status_code in (401, 403):
            msg = "MDBList API key is invalid or revoked."
            raise ConnectionAuthError(msg) from error
        raise


def validate_api_key(api_key):
    """Validate an MDBList API key and return the account info."""
    return request(api_key, "/user")


def normalize_items_response(response):
    """Flatten flat-array or {"movies": [], "shows": []} item responses.

    The flat array preserves the list's upstream ordering; list imports ask for
    it with unified=true. The grouped shape carries no cross-bucket ordering,
    so movies-then-shows is the best that can be done for it.
    """
    if isinstance(response, list):
        return response
    if isinstance(response, dict):
        entries = []
        for key in ("movies", "shows"):
            group = response.get(key) or []
            mediatype = "movie" if key == "movies" else "show"
            for entry in group:
                entry.setdefault("mediatype", mediatype)
                entries.append(entry)
        return entries
    return []


def resolve_tmdb_id(entry, media_type):
    """Resolve an MDBList entry to a TMDB id, using /find as fallback."""
    ids = entry.get("ids") or {}
    tmdb_id = ids.get("tmdb") or entry.get("tmdb_id")
    if tmdb_id:
        return tmdb_id

    key = "movie_results" if media_type == MediaTypes.MOVIE.value else "tv_results"
    lookups = [(ids.get("imdb") or entry.get("imdb_id"), "imdb_id")]
    if media_type == MediaTypes.TV.value:
        lookups.append((ids.get("tvdb") or entry.get("tvdb_id"), "tvdb_id"))

    for external_id, external_source in lookups:
        if not external_id:
            continue
        try:
            found = tmdb.find(external_id, external_source)
        except services.ProviderAPIError:
            continue
        results = (found or {}).get(key) or []
        if results:
            return results[0].get("id")
    return None


def importer(api_key, user, mode):
    """Import the user's tracking data from MDBList.

    Args:
        api_key (str): Decrypted MDBList API key
        user: Django user object to import data for
        mode (str): Import mode ("new" or "overwrite")
    """
    return MDBListImporter(api_key, user, mode).import_data()


def _parse_entry_datetime(entry, *keys):
    """Parse the first present timestamp key of an entry, tolerating nulls."""
    for key in keys:
        value = entry.get(key)
        if value:
            parsed = parse_datetime(value)
            if parsed is not None:
                return parsed
    return None


def _show_keys(show_data):
    """Return provider identifiers that can match one show's rows."""
    if not isinstance(show_data, dict):
        return set()

    ids = show_data.get("ids")
    if not isinstance(ids, dict):
        ids = {}
    return {
        (source, str(value))
        for source in ("tmdb", "tvdb", "imdb", "mdblist")
        if (value := ids.get(source) or show_data.get(f"{source}_id"))
    }


def _show_season_keys(show_data, season_number):
    """Return provider identifiers that can match one show's season rows."""
    if season_number is None:
        return set()
    return {(*show_key, str(season_number)) for show_key in _show_keys(show_data)}


def _next_page_params(params, pagination, page_size):
    """Return advancing cursor or offset parameters for another provider page."""
    if page_size <= 0 or not isinstance(pagination, dict):
        return None

    next_params = dict(params)
    cursor = pagination.get("next_cursor")
    if cursor:
        next_params["cursor"] = cursor
    elif pagination.get("has_more"):
        current_offset = params.get("offset", pagination.get("offset", 0))
        try:
            current_offset = int(current_offset or 0)
        except (TypeError, ValueError):
            current_offset = 0
        next_params.pop("cursor", None)
        next_params["offset"] = current_offset + page_size
    else:
        return None

    return next_params if next_params != params else None


class MDBListImporter(TraktMetadataResolverMixin):
    """Import watched/watchlist/ratings/dropped/collection data from MDBList."""

    def __init__(self, api_key, user, mode):
        """Initialize the importer with the API key, user and mode."""
        self.api_key = api_key
        self.user = user
        # Watchlist only and Collection only run one stage; the watchlist stage
        # then behaves like "new" so it never touches an existing entry.
        self.stage = MODE_STAGES.get(mode)
        self.mode = "new" if mode == "watchlist" else mode
        self.warnings = []

        # Track existing media to handle "new" mode correctly
        self.existing_media = helpers.get_existing_media(user)
        self.deleted_media = helpers.get_deleted_media(user)

        # Track existing plays for exact dedupe and distinct-episode roll-up.
        (
            self.existing_episode_watch_keys,
            self.watched_episode_numbers,
        ) = self._get_existing_episode_state()

        # Track media IDs to delete in overwrite mode
        self.to_delete = defaultdict(lambda: defaultdict(set))

        # Track bulk creation lists for each media type
        self.bulk_media = defaultdict(list)

        # Track media instances being created
        self.media_instances = defaultdict(lambda: defaultdict(list))

        # Track existing DB objects promoted to COMPLETED during import
        self.completed_seasons = []

        # TMDB IDs of shows the user has dropped on MDBList
        self.dropped_tmdb_ids: set = set()
        self.dropped_tvs: list = []

        logger.info(
            "Initialized MDBList importer for user %s with mode %s",
            user.username,
            mode,
        )

    def _get_existing_episode_state(self):
        """Return exact play keys and completed coordinates for new-mode import."""
        watch_keys = set()
        watched = defaultdict(set)
        if self.mode != "new":
            return watch_keys, watched

        rows = app.models.Episode.objects.filter(
            related_season__user=self.user,
        ).values_list(
            "item__media_id",
            "item__season_number",
            "item__episode_number",
            "end_date",
            "status",
        )
        for media_id, season_number, episode_number, end_date, status in rows:
            if end_date is not None:
                watch_keys.add(
                    (media_id, season_number, episode_number, end_date),
                )
            if (
                status == Status.COMPLETED.value
                and season_number is not None
                and episode_number is not None
            ):
                watched[(str(media_id), season_number)].add(episode_number)
        return watch_keys, watched

    def import_data(self):
        """Import all user data from MDBList."""
        if self.stage is None:
            self.process_dropped()
            self.process_watched()
            self.process_watchlist()
            self.process_ratings()
            self.process_collection()
        else:
            getattr(self, f"process_{self.stage}")()

        helpers.cleanup_existing_media(self.to_delete, self.user)
        self.warnings.extend(helpers.bulk_create_media(self.bulk_media, self.user))

        if self.completed_seasons:
            bulk_update_with_history(
                self.completed_seasons,
                app.models.Season,
                fields=["status"],
            )
            helpers.note_bulk_rows(self.completed_seasons)
        if self.dropped_tvs:
            bulk_update_with_history(
                self.dropped_tvs,
                app.models.TV,
                fields=["status"],
            )
            helpers.note_bulk_rows(self.dropped_tvs)

        imported_counts = {
            media_type: len(media_list)
            for media_type, media_list in self.bulk_media.items()
        }
        deduplicated_messages = "\n".join(dict.fromkeys(self.warnings))

        return imported_counts, deduplicated_messages

    def _get_grouped_sync_data(self, path, item_type):
        """Fetch a grouped ``/sync/*`` payload across cursor or offset pages.

        Returns a dict of entry lists keyed by group name (``movies``,
        ``shows``, ``seasons``, ``episodes``).
        """
        grouped = defaultdict(list)
        params = {"limit": PAGE_SIZE}
        seen_pages = set()
        while True:
            page_token = tuple(sorted(params.items()))
            if page_token in seen_pages:
                break
            seen_pages.add(page_token)

            response = request(self.api_key, path, params=params)
            if not isinstance(response, dict):
                break
            page_size = 0
            for key, group in response.items():
                if key == "pagination" or not isinstance(group, list):
                    continue
                grouped[key].extend(group)
                page_size += len(group)
            import_progress.report(
                sum(len(group) for group in grouped.values()),
                total=None,
                label=f"MDBList: gathering {item_type}…",
            )
            pagination = response.get("pagination") or response
            next_params = _next_page_params(params, pagination, page_size)
            if next_params is None:
                break
            params = next_params

        logger.info(
            "Retrieved %s %s for user %s",
            sum(len(group) for group in grouped.values()),
            item_type,
            self.user.username,
        )
        return grouped

    def _resolve_entry_tmdb_id(self, media_data, media_type):
        """Resolve a nested media dict to a TMDB id string, warning on failure."""
        tmdb_id = resolve_tmdb_id(media_data, media_type)
        if tmdb_id:
            return str(tmdb_id)

        title = media_data.get("title") or "Unknown title"
        self.warnings.append(f"{title}: No {Sources.TMDB.label} ID found.")
        return None

    def _entry_error_warning(self, entry, context):
        """Record a warning for an entry that raised unexpectedly."""
        logger.exception("Skipping MDBList %s entry", context)
        media_data = (
            entry.get("movie")
            or entry.get("show")
            or (entry.get("season") or {}).get("show")
            or (entry.get("episode") or {}).get("show")
            or {}
        )
        title = media_data.get("title") or entry.get("title") or "Unknown title"
        self.warnings.append(f"{title}: skipped a {context} entry.")

    def process_dropped(self):
        """Collect TMDB IDs of shows the user has dropped on MDBList."""
        data = self._get_grouped_sync_data("/sync/dropped", "dropped shows")
        for entry in data.get("shows", []):
            show = entry.get("show") or {}
            tmdb_id = resolve_tmdb_id(show, MediaTypes.TV.value)
            if tmdb_id:
                self.dropped_tmdb_ids.add(str(tmdb_id))

    def process_watched(self):
        """Process watched history from MDBList.

        MDBList stores watched state at whatever level the user marked it:
        movie, whole show, whole season, or individual episode. Season entries
        are expanded into their episodes via TMDB metadata unless explicit
        episode rows exist for that season; show entries are recorded as a
        COMPLETED show without per-episode fan-out.
        """
        data = self._get_grouped_sync_data("/sync/watched", "watched entries")
        explicit_episode_seasons = set()
        detailed_shows = set()
        for entry in data.get("seasons", []):
            if not isinstance(entry, dict):
                continue
            season_data = entry.get("season") or {}
            if not isinstance(season_data, dict):
                continue
            season_number = season_data.get("number")
            if season_number is None:
                continue
            show_data = season_data.get("show") or entry.get("show") or {}
            detailed_shows.update(_show_keys(show_data))

        for entry in data.get("episodes", []):
            if not isinstance(entry, dict):
                continue
            episode_data = entry.get("episode") or {}
            if not isinstance(episode_data, dict):
                continue
            season_number = episode_data.get("season")
            if season_number is None or episode_data.get("number") is None:
                continue
            show_data = episode_data.get("show") or entry.get("show") or {}
            detailed_shows.update(_show_keys(show_data))
            explicit_episode_seasons.update(
                _show_season_keys(show_data, season_number),
            )

        total = sum(
            len(data.get(group, []))
            for group in ("movies", "shows", "seasons", "episodes")
        )
        current = 0

        for entry in data.get("movies", []):
            current += 1
            import_progress.report(current, total, "MDBList: watched history")
            try:
                self.process_watched_movie(entry)
            except MediaImportError:
                raise
            except Exception:
                self._entry_error_warning(entry, "watched movie")

        for entry in data.get("shows", []):
            current += 1
            import_progress.report(current, total, "MDBList: watched history")
            try:
                show_data = entry.get("show") or {}
                if _show_keys(show_data) & detailed_shows:
                    continue
                self.process_watched_show(entry)
            except MediaImportError:
                raise
            except Exception:
                self._entry_error_warning(entry, "watched show")

        for entry in data.get("seasons", []):
            current += 1
            import_progress.report(current, total, "MDBList: watched history")
            try:
                season_data = entry.get("season") or {}
                show_data = season_data.get("show") or entry.get("show") or {}
                if (
                    _show_season_keys(
                        show_data,
                        season_data.get("number"),
                    )
                    & explicit_episode_seasons
                ):
                    continue
                self.process_watched_season(entry)
            except MediaImportError:
                raise
            except Exception:
                self._entry_error_warning(entry, "watched season")

        for entry in data.get("episodes", []):
            current += 1
            import_progress.report(current, total, "MDBList: watched history")
            try:
                episode_data = entry.get("episode") or {}
                show_data = episode_data.get("show") or entry.get("show") or {}
                self.process_watched_episode(
                    show_data,
                    episode_data.get("season"),
                    episode_data.get("number"),
                    _parse_entry_datetime(entry, "watched_at", "last_watched_at"),
                )
            except MediaImportError:
                raise
            except Exception:
                self._entry_error_warning(entry, "watched episode")

    def process_watched_movie(self, entry):
        """Process a watched movie entry."""
        movie = entry.get("movie") or {}
        tmdb_id = self._resolve_entry_tmdb_id(movie, MediaTypes.MOVIE.value)
        if not tmdb_id:
            return

        if not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            MediaTypes.MOVIE.value,
            Sources.TMDB.value,
            tmdb_id,
            self.mode,
            deleted_media=self.deleted_media,
        ):
            return

        metadata = self._get_metadata(
            MediaTypes.MOVIE.value,
            tmdb_id,
            movie.get("title") or tmdb_id,
        )
        if not metadata:
            return

        item = self._get_or_create_item(MediaTypes.MOVIE.value, tmdb_id, metadata)
        watched_at_dt = _parse_entry_datetime(entry, "watched_at", "last_watched_at")

        movie_obj = app.models.Movie(
            item=item,
            user=self.user,
            end_date=watched_at_dt,
            status=Status.COMPLETED.value,
            progress=1,
        )
        if watched_at_dt is not None:
            movie_obj._history_date = watched_at_dt

        self.media_instances[MediaTypes.MOVIE.value][tmdb_id].append(movie_obj)
        self.bulk_media[MediaTypes.MOVIE.value].append(movie_obj)

    def process_watched_show(self, entry):
        """Process a show marked fully watched on MDBList."""
        show = entry.get("show") or {}
        tmdb_id = self._resolve_entry_tmdb_id(show, MediaTypes.TV.value)
        if not tmdb_id:
            return

        if not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            MediaTypes.TV.value,
            Sources.TMDB.value,
            tmdb_id,
            self.mode,
            deleted_media=self.deleted_media,
        ):
            return

        tv_metadata = self._get_metadata(
            MediaTypes.TV.value,
            tmdb_id,
            show.get("title") or tmdb_id,
        )
        if not tv_metadata:
            return

        watched_at_dt = _parse_entry_datetime(entry, "watched_at", "last_watched_at")
        status = (
            Status.DROPPED.value
            if tmdb_id in self.dropped_tmdb_ids
            else Status.COMPLETED.value
        )
        self._get_or_create_tv_instance(tmdb_id, tv_metadata, watched_at_dt, status)

    def process_watched_season(self, entry):
        """Expand a season marked watched into its episodes via TMDB metadata."""
        season_data = entry.get("season") or {}
        show_data = season_data.get("show") or entry.get("show") or {}
        season_number = season_data.get("number")
        if season_number is None:
            self._entry_error_warning(entry, "watched season")
            return

        tmdb_id = self._resolve_entry_tmdb_id(show_data, MediaTypes.TV.value)
        if not tmdb_id:
            return

        season_metadata = self._get_metadata(
            MediaTypes.SEASON.value,
            tmdb_id,
            show_data.get("title") or tmdb_id,
            season_number,
        )
        if not season_metadata:
            return

        watched_at_dt = _parse_entry_datetime(entry, "watched_at", "last_watched_at")
        for episode in season_metadata["episodes"]:
            self.process_watched_episode(
                show_data,
                season_number,
                episode["episode_number"],
                watched_at_dt,
            )

    def process_watched_episode(
        self,
        show_data,
        season_number,
        episode_number,
        watched_at_dt,
    ):
        """Process a single watched episode."""
        if season_number is None or episode_number is None:
            msg = "Episode entry is missing season or episode number"
            raise ValueError(msg)

        tmdb_id = self._resolve_entry_tmdb_id(show_data, MediaTypes.TV.value)
        if not tmdb_id:
            return

        if tmdb_id in self.deleted_media[MediaTypes.TV.value][Sources.TMDB.value]:
            return

        episode_watch_key = (tmdb_id, season_number, episode_number, watched_at_dt)
        if episode_watch_key in self.existing_episode_watch_keys:
            return

        tv_exists = (
            tmdb_id in self.existing_media[MediaTypes.TV.value][Sources.TMDB.value]
        )
        if self.mode == "overwrite" and tv_exists:
            self.to_delete[MediaTypes.TV.value][Sources.TMDB.value].add(tmdb_id)

        title = show_data.get("title") or tmdb_id
        tv_metadata = self._get_metadata(MediaTypes.TV.value, tmdb_id, title)
        if not tv_metadata:
            return

        season_metadata = self._get_metadata(
            MediaTypes.SEASON.value,
            tmdb_id,
            title,
            season_number,
        )
        if not season_metadata:
            return

        episode_exists = any(
            ep["episode_number"] == episode_number for ep in season_metadata["episodes"]
        )
        if not episode_exists:
            self.warnings.append(
                f"{title} S{int(season_number):02d}E{int(episode_number):02d}: not found in "
                f"{Sources.TMDB.label} with ID {tmdb_id}.",
            )
            return

        tv_status = (
            Status.DROPPED.value
            if tmdb_id in self.dropped_tmdb_ids
            else Status.IN_PROGRESS.value
        )
        tv_obj = self._get_or_create_tv_instance(
            tmdb_id,
            tv_metadata,
            watched_at_dt,
            tv_status,
        )

        # Create or get Season
        season_item = self._get_or_create_item(
            MediaTypes.SEASON.value,
            tmdb_id,
            season_metadata,
            season_number,
        )
        season_key = f"{tmdb_id}:{season_number}"
        if season_key not in self.media_instances[MediaTypes.SEASON.value]:
            replacing_tv = (
                tmdb_id in self.to_delete[MediaTypes.TV.value][Sources.TMDB.value]
            )
            season_obj = None
            if not replacing_tv:
                season_obj = app.models.Season.objects.filter(
                    user=self.user,
                    item=season_item,
                ).first()
            if season_obj is None:
                season_obj = app.models.Season(
                    item=season_item,
                    user=self.user,
                    related_tv=tv_obj,
                    # History for a new season of a show the user dropped or
                    # paused is recorded without reopening the show.
                    status=(
                        tv_obj.status
                        if tv_obj.status in app.models.USER_HELD_STATUSES
                        else Status.IN_PROGRESS.value
                    ),
                )
                if watched_at_dt is not None:
                    season_obj._history_date = watched_at_dt
                self.bulk_media[MediaTypes.SEASON.value].append(season_obj)
            self.media_instances[MediaTypes.SEASON.value][season_key] = [season_obj]
        else:
            season_obj = self.media_instances[MediaTypes.SEASON.value][season_key][0]

        episode_metadata = {
            "title": tv_metadata["title"],
            "original_title": tv_metadata.get("original_title"),
            "localized_title": tv_metadata.get("localized_title"),
            "image": self._get_episode_image(episode_number, season_metadata),
        }
        episode_item = self._get_or_create_item(
            MediaTypes.EPISODE.value,
            tmdb_id,
            episode_metadata,
            season_number,
            episode_number,
        )

        ep_key = f"{tmdb_id}:{season_number}:{episode_number}"
        episode_obj = app.models.Episode(
            item=episode_item,
            related_season=season_obj,
            end_date=watched_at_dt,
        )
        if watched_at_dt is not None:
            episode_obj._history_date = watched_at_dt
        self.media_instances[MediaTypes.EPISODE.value][ep_key].append(episode_obj)
        self.bulk_media[MediaTypes.EPISODE.value].append(episode_obj)
        self.existing_episode_watch_keys.add(episode_watch_key)
        watched_episode_numbers = self.watched_episode_numbers[(tmdb_id, season_number)]
        watched_episode_numbers.add(episode_number)

        self._update_completion_status(
            season_obj,
            season_metadata,
            watched_episode_numbers,
        )

    def _get_or_create_tv_instance(self, tmdb_id, tv_metadata, history_date, status):
        """Get or create the TV object for a show, tracking status promotions."""
        tv_item = self._get_or_create_item(MediaTypes.TV.value, tmdb_id, tv_metadata)
        tv_key = f"{tmdb_id}"

        if tv_key in self.media_instances[MediaTypes.TV.value]:
            return self.media_instances[MediaTypes.TV.value][tv_key][0]

        replacing_tv = (
            tmdb_id in self.to_delete[MediaTypes.TV.value][Sources.TMDB.value]
        )
        tv_obj = None
        if not replacing_tv:
            tv_obj = self.existing_media[MediaTypes.TV.value][Sources.TMDB.value].get(
                tmdb_id,
            )
        if tv_obj is None:
            tv_obj = app.models.TV(
                item=tv_item,
                user=self.user,
                status=status,
            )
            if history_date is not None:
                tv_obj._history_date = history_date
            self.bulk_media[MediaTypes.TV.value].append(tv_obj)
        elif tmdb_id in self.dropped_tmdb_ids and tv_obj.status != Status.DROPPED.value:
            tv_obj.status = Status.DROPPED.value
            self.dropped_tvs.append(tv_obj)
        self.media_instances[MediaTypes.TV.value][tv_key] = [tv_obj]
        return tv_obj

    def _update_completion_status(
        self,
        season_obj,
        season_metadata,
        watched_episode_numbers,
    ):
        """Complete a season only when distinct episode evidence covers it."""
        required_episode_numbers = {
            episode.get("episode_number")
            for episode in season_metadata.get("episodes", [])
            if episode.get("episode_number") is not None
        }
        if not required_episode_numbers or not required_episode_numbers.issubset(
            watched_episode_numbers,
        ):
            return
        if season_obj.status in {Status.COMPLETED.value, Status.DROPPED.value}:
            return

        season_obj.status = Status.COMPLETED.value
        if season_obj.pk:
            self.completed_seasons.append(season_obj)

    def _get_watchlist_entries(self):
        """Fetch the user's MDBList watchlist across cursor or offset pages."""
        entries = []
        params = {"limit": PAGE_SIZE}
        seen_pages = set()
        while True:
            page_token = tuple(sorted(params.items()))
            if page_token in seen_pages:
                break
            seen_pages.add(page_token)

            response = request(self.api_key, "/watchlist/items", params=params)
            page = normalize_items_response(response)
            entries.extend(page)
            import_progress.report(
                len(entries),
                total=None,
                label="MDBList: gathering watchlist…",
            )
            if isinstance(response, dict):
                pagination = response.get("pagination") or response
            else:
                pagination = {"has_more": len(page) >= PAGE_SIZE}
            next_params = _next_page_params(params, pagination, len(page))
            if next_params is None:
                break
            params = next_params
        return entries

    def process_watchlist(self):
        """Import the MDBList watchlist as PLANNING entries."""
        entries = self._get_watchlist_entries()
        total = len(entries)
        for i, entry in enumerate(entries, start=1):
            import_progress.report(i, total, "MDBList: watchlist")
            try:
                self._process_watchlist_entry(entry)
            except MediaImportError:
                raise
            except Exception:
                self._entry_error_warning(entry, "watchlist")

    def _process_watchlist_entry(self, entry):
        """Process a single watchlist entry into a PLANNING movie/show."""
        media_type = MEDIATYPE_MAP.get(entry.get("mediatype"))
        if not media_type:
            return

        tmdb_id = self._resolve_entry_tmdb_id(entry, media_type)
        if not tmdb_id:
            return

        # Don't downgrade something already imported as watched in this run
        if tmdb_id in self.media_instances[media_type]:
            return

        if not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            media_type,
            Sources.TMDB.value,
            tmdb_id,
            self.mode,
            deleted_media=self.deleted_media,
        ):
            return

        metadata = self._get_metadata(
            media_type,
            tmdb_id,
            entry.get("title") or tmdb_id,
        )
        if not metadata:
            return

        item = self._get_or_create_item(media_type, tmdb_id, metadata)
        model_class = (
            app.models.Movie if media_type == MediaTypes.MOVIE.value else app.models.TV
        )
        media_obj = model_class(
            item=item,
            user=self.user,
            status=Status.PLANNING.value,
        )
        self.media_instances[media_type][tmdb_id] = [media_obj]
        self.bulk_media[media_type].append(media_obj)

    def process_ratings(self):
        """Import ratings for movies, shows, seasons and episodes."""
        data = self._get_grouped_sync_data("/sync/ratings", "ratings")
        total = sum(
            len(data.get(group, []))
            for group in ("movies", "shows", "seasons", "episodes")
        )
        current = 0

        for group, handler in (
            ("movies", self._process_movie_rating),
            ("shows", self._process_show_rating),
            ("seasons", self._process_season_rating),
            ("episodes", self._process_episode_rating),
        ):
            for entry in data.get(group, []):
                current += 1
                import_progress.report(current, total, "MDBList: ratings")
                try:
                    if entry.get("rating") is None:
                        continue
                    handler(entry)
                except MediaImportError:
                    raise
                except Exception:
                    self._entry_error_warning(entry, "rating")

    def _process_movie_rating(self, entry):
        """Apply a movie rating, storing the score without tracking the movie."""
        self._process_rated_media_item(
            entry,
            entry.get("movie") or {},
            MediaTypes.MOVIE.value,
            app.models.Movie,
            {
                "score": entry["rating"],
                # A rating says nothing about whether the user watched it.
                # Leave the row statusless so it stays out of the lists.
                "status": None,
            },
        )

    def _process_show_rating(self, entry):
        """Apply a show rating, storing the score without tracking the show."""
        self._process_rated_media_item(
            entry,
            entry.get("show") or {},
            MediaTypes.TV.value,
            app.models.TV,
            {"score": entry["rating"], "status": None},
        )

    def _process_season_rating(self, entry):
        """Apply a season rating."""
        season_data = entry.get("season") or {}
        show_data = season_data.get("show") or entry.get("show") or {}
        season_number = season_data.get("number")
        if season_number is None:
            return

        tmdb_id = self._resolve_entry_tmdb_id(show_data, MediaTypes.TV.value)
        if not tmdb_id:
            return

        season_key = f"{tmdb_id}:{season_number}"
        if season_key in self.media_instances[MediaTypes.SEASON.value]:
            for season_obj in self.media_instances[MediaTypes.SEASON.value][season_key]:
                season_obj.score = entry["rating"]
            return

        if not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            MediaTypes.TV.value,
            Sources.TMDB.value,
            tmdb_id,
            self.mode,
            deleted_media=self.deleted_media,
        ):
            return

        title = show_data.get("title") or tmdb_id
        season_metadata = self._get_metadata(
            MediaTypes.SEASON.value,
            tmdb_id,
            title,
            season_number,
        )
        if not season_metadata:
            return

        tv_metadata = self._get_metadata(MediaTypes.TV.value, tmdb_id, title)
        if not tv_metadata:
            return
        tv_obj = self._get_or_create_tv_instance(
            tmdb_id,
            tv_metadata,
            _parse_entry_datetime(entry, "rated_at"),
            None,
        )

        season_item = self._get_or_create_item(
            MediaTypes.SEASON.value,
            tmdb_id,
            season_metadata,
            season_number,
        )
        season_obj = app.models.Season(
            item=season_item,
            user=self.user,
            related_tv=tv_obj,
            # A rating is not a watch: the season keeps no status, so it never
            # triggers the completed-season episode backfill.
            status=None,
            score=entry["rating"],
        )
        rated_at = _parse_entry_datetime(entry, "rated_at")
        if rated_at is not None:
            season_obj._history_date = rated_at
        self.media_instances[MediaTypes.SEASON.value][season_key] = [season_obj]
        self.bulk_media[MediaTypes.SEASON.value].append(season_obj)

    def _process_episode_rating(self, entry):
        """Apply an episode rating to persisted or in-memory episodes."""
        episode_data = entry.get("episode") or {}
        show_data = episode_data.get("show") or entry.get("show") or {}
        season_number = episode_data.get("season")
        episode_number = episode_data.get("number")
        if season_number is None or episode_number is None:
            return

        tmdb_id = self._resolve_entry_tmdb_id(show_data, MediaTypes.TV.value)
        if not tmdb_id:
            return

        season_obj = app.models.Season.objects.filter(
            item__media_id=tmdb_id,
            item__source=Sources.TMDB.value,
            item__season_number=season_number,
            user=self.user,
        ).first()

        if not season_obj:
            season_key = f"{tmdb_id}:{season_number}"
            season_list = self.media_instances[MediaTypes.SEASON.value].get(season_key)
            if season_list:
                season_obj = season_list[0]

        if not season_obj:
            return

        scaled_score = self.user.scale_score_for_storage(
            Decimal(str(entry["rating"])),
        )

        if season_obj.pk:
            episodes = app.models.Episode.objects.filter(
                related_season=season_obj,
                item__episode_number=episode_number,
            )
            if episodes.exists():
                # A queryset update sends no save signal; publish these days.
                changed = list(episodes.exclude(score=scaled_score))
                app.models.Episode.objects.filter(
                    pk__in=[episode.pk for episode in changed],
                ).update(score=scaled_score, scored_at=timezone.now())
                helpers.note_bulk_rows(changed)
                return

        ep_key = f"{tmdb_id}:{season_number}:{episode_number}"
        for ep_obj in self.media_instances[MediaTypes.EPISODE.value].get(ep_key, []):
            ep_obj.score = scaled_score

    def _process_rated_media_item(
        self,
        entry,
        media_data,
        media_type,
        model_class,
        defaults,
    ):
        """Apply a rating to an in-memory instance or create a new one."""
        tmdb_id = self._resolve_entry_tmdb_id(media_data, media_type)
        if not tmdb_id:
            return

        if tmdb_id in self.media_instances[media_type]:
            for media_obj in self.media_instances[media_type][tmdb_id]:
                media_obj.score = defaults["score"]
            return

        if not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            media_type,
            Sources.TMDB.value,
            tmdb_id,
            self.mode,
            deleted_media=self.deleted_media,
        ):
            return

        metadata = self._get_metadata(
            media_type,
            tmdb_id,
            media_data.get("title") or tmdb_id,
        )
        if not metadata:
            return

        item = self._get_or_create_item(media_type, tmdb_id, metadata)
        media_obj = model_class(
            item=item,
            user=self.user,
            **defaults,
        )
        rated_at = _parse_entry_datetime(entry, "rated_at")
        if rated_at is not None:
            media_obj._history_date = rated_at
        self.media_instances[media_type][tmdb_id] = [media_obj]
        self.bulk_media[media_type].append(media_obj)

    def process_collection(self):
        """Import collected movies/shows as CollectionEntry rows.

        MDBList tracks collection at title level, so no episode fan-out or
        season/show rollup is needed. The endpoint is beta upstream; failures
        are downgraded to a warning.
        """
        # Imported lazily to match trakt.py and avoid a circular import.
        from integrations.imports import trakt_collection

        try:
            data = self._get_grouped_sync_data("/sync/collection", "collected items")
        except MediaImportError:
            raise
        except Exception:
            logger.exception("MDBList collection fetch failed")
            self.warnings.append(
                "Collection: could not be fetched from MDBList, skipped.",
            )
            return

        total = len(data.get("movies", [])) + len(data.get("shows", []))
        current = 0

        for group, media_type in (
            ("movies", MediaTypes.MOVIE.value),
            ("shows", MediaTypes.TV.value),
        ):
            data_key = "movie" if media_type == MediaTypes.MOVIE.value else "show"
            for entry in data.get(group, []):
                current += 1
                import_progress.report(current, total, "MDBList: collection")
                try:
                    media_data = entry.get(data_key) or {}
                    tmdb_id = self._resolve_entry_tmdb_id(media_data, media_type)
                    if not tmdb_id:
                        continue
                    metadata = self._get_metadata(
                        media_type,
                        tmdb_id,
                        media_data.get("title") or tmdb_id,
                    )
                    if not metadata:
                        continue
                    item = self._get_or_create_item(media_type, tmdb_id, metadata)
                    trakt_collection.upsert_collection_entry(
                        self.user,
                        item,
                        mode=self.mode,
                        collected_at=_parse_entry_datetime(
                            entry,
                            "collected_at",
                            "listed_at",
                        ),
                        metadata=None,
                    )
                except Exception:
                    self._entry_error_warning(entry, "collection")
