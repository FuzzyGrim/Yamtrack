import json
import logging
import re
import zipfile
from collections import defaultdict

import requests
from django.conf import settings
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django_celery_beat.models import PeriodicTask

import app
from app import helpers as app_helpers
from app.models import MediaTypes, Sources, Status
from app.providers import services
from integrations.imports import helpers
from integrations.imports.helpers import MediaImportError, MediaImportUnexpectedError
from lists.models import CustomList, CustomListItem

logger = logging.getLogger(__name__)

TRAKT_API_BASE_URL = "https://api.trakt.tv"
BULK_PAGE_SIZE = 1000

# The archive is expanded in the worker, so cap how much it may hold uncompressed.
MAX_EXPORT_UNCOMPRESSED_BYTES = 100 * 1024 * 1024

# Username used when the archive has no readable user-profile.json.
EXPORT_FALLBACK_USERNAME = "Trakt User"

# Convert Trakt API endpoints to the corresponding export file(s) that contain the data.
ENDPOINTS_TO_FILES = {
    "/history": ("watched-history",),
    "/watchlist": ("lists-watchlist",),
    "/lists": ("lists-lists",),
    "/lists/collaborations": ("lists-collaborations",),
    "/favorites": ("lists-favorites",),
    "/ratings": (
        "ratings-movies",
        "ratings-shows",
        "ratings-seasons",
        "ratings-episodes",
    ),
    "/comments": (
        "comments-movies",
        "comments-shows",
        "comments-seasons",
        "comments-episodes",
    ),
}

# Trakt writes each custom list's items to lists-list-<trakt id>-<slug>.json,
# so those files cannot be mapped from an endpoint alone.
LIST_ITEMS_FILE_PREFIX = "lists-list"

# Trakt exposes favorites as a fixed list, so it has no name of its own.
FAVORITES_LIST_NAME = "Favorites"

# Trakt list entry types that map onto a Yamtrack item. Lists can also hold
# people, which Yamtrack does not track.
LIST_ENTRY_MEDIA_TYPES = {
    "movie": MediaTypes.MOVIE.value,
    "show": MediaTypes.TV.value,
    "season": MediaTypes.SEASON.value,
    "episode": MediaTypes.EPISODE.value,
}

# Responses that mean a list is simply not shared with us, which is expected for
# a public import, rather than an import failure.
MISSING_LIST_STATUSES = frozenset(
    {
        requests.codes.unauthorized,
        requests.codes.forbidden,
        requests.codes.not_found,
    },
)


def handle_oauth_callback(request, redirect_uri=None):
    """View for getting the Trakt OAuth2 token."""
    code = request.GET["code"]

    url = "https://api.trakt.tv/oauth/token"
    redirect_uri = redirect_uri or app_helpers.build_absolute_app_url(
        request,
        reverse("import_trakt_private"),
    )

    params = {
        "client_id": settings.TRAKT_API,
        "client_secret": settings.TRAKT_API_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }

    try:
        token_response = app.providers.services.api_request(
            "TRAKT",
            "POST",
            url,
            params=params,
        )
    except services.ProviderAPIError as error:
        if error.status_code == requests.codes.unauthorized:
            msg = "Invalid Trakt secret key."
            raise MediaImportError(msg) from error
        raise

    return {
        "refresh_token": token_response["refresh_token"],
        "username": get_username_from_oauth(token_response["access_token"]),
    }


def get_username_from_oauth(access_token):
    """View for getting the Trakt OAuth2 username."""
    url = "https://api.trakt.tv/users/me"

    headers = {
        "Content-Type": "application/json",
        "trakt-api-version": "2",
        "trakt-api-key": settings.TRAKT_API,
        "Authorization": f"Bearer {access_token}",
    }

    try:
        request = app.providers.services.api_request(
            "TRAKT",
            "GET",
            url,
            headers=headers,
        )
    except services.ProviderAPIError as error:
        if error.status_code == requests.codes.unauthorized:
            msg = "Invalid Trakt secret key."
            raise MediaImportError(msg) from error
        raise

    return request["username"]


def get_access_token(encrypted_refresh_token, redirect_uri=None):
    """Get access token from encrypted refresh token."""
    url = "https://api.trakt.tv/oauth/token"

    decrypted_token = helpers.decrypt(encrypted_refresh_token)
    redirect_uri = redirect_uri or app_helpers.build_absolute_app_url(
        None,
        reverse("import_trakt_private"),
    )

    params = {
        "client_id": settings.TRAKT_API,
        "client_secret": settings.TRAKT_API_SECRET,
        "refresh_token": decrypted_token,
        "grant_type": "refresh_token",
    }
    if redirect_uri:
        params["redirect_uri"] = redirect_uri

    try:
        request = app.providers.services.api_request(
            "TRAKT",
            "POST",
            url,
            params=params,
        )
    except services.ProviderAPIError as error:
        if error.status_code == requests.codes.unauthorized:
            msg = "Invalid Trakt secret key."
            raise MediaImportError(msg) from error
        raise

    # refresh tokens are one time use only
    update_refresh_token(encrypted_refresh_token, request["refresh_token"])
    return request["access_token"]


def update_refresh_token(old_token, new_token):
    """Update the refresh token in periodic tasks."""
    periodic_task = PeriodicTask.objects.filter(
        task="Import from Trakt",
        kwargs__contains=f'"token": "{old_token}"',
    ).first()

    if periodic_task:
        task_kwargs = json.loads(periodic_task.kwargs)
        task_kwargs["token"] = helpers.encrypt(new_token)
        periodic_task.kwargs = json.dumps(task_kwargs)
        periodic_task.save()


def importer(token, user, mode, username=None, redirect_uri=None, file=None):
    """Import the user's data from Trakt.

    Can import using OAuth (token provided), public username, or an export archive
    downloaded from the Trakt website.

    Args:
        token (str, optional): Encrypted OAuth2 refresh token if using OAuth else None
        user: Django user object to import data for
        mode (str): Import mode ("new" or "overwrite")
        username (str, optional): Trakt username if using API import else None
        redirect_uri (str, optional): OAuth2 redirect URI if using OAuth else None
        file (File, optional): Uploaded Trakt export archive else None
    """
    if file:
        trakt_importer = TraktExportImporter(file, user, mode)
    else:
        trakt_importer = TraktImporter(
            username,
            user,
            mode,
            refresh_token=token,
            redirect_uri=redirect_uri,
        )

    return trakt_importer.import_data()


class TraktImporter:
    """Class to handle importing user data from Trakt."""

    def __init__(self, username, user, mode, refresh_token=None, redirect_uri=None):
        """Initialize the importer with user details and mode.

        Args:
            username (str): Trakt username to import from
            user: Django user object to import data for
            mode (str): Import mode ("new" or "overwrite")
            refresh_token (str, optional): Encrypted OAuth2 refresh token if
                using OAuth, None for public import
        """
        self.username = username
        self.user = user
        self.mode = mode
        self.refresh_token = refresh_token
        self.redirect_uri = redirect_uri
        self.user_base_url = f"{TRAKT_API_BASE_URL}/users/{username}"
        self.warnings = []

        # Track existing media to handle "new" mode correctly
        self.existing_media = helpers.get_existing_media(user)

        # Track media IDs to delete in overwrite mode
        self.to_delete = defaultdict(lambda: defaultdict(set))

        # Track bulk creation lists for each media type
        self.bulk_media = defaultdict(list)

        # Track media instances being created
        self.media_instances = defaultdict(lambda: defaultdict(list))

        # Track custom lists to create, keyed by list name. Each holds the item
        # IDs it contains mapped to the date Trakt listed them at.
        self.custom_lists = {}

        logger.info(
            "Initialized Trakt importer for user %s with mode %s",
            username,
            mode,
        )

    def import_data(self):
        """Import all user data from Trakt."""
        self.process_history()
        self.process_watchlist()
        self.process_ratings()
        self.process_comments()
        self.process_lists()

        helpers.cleanup_existing_media(self.to_delete, self.user)
        helpers.bulk_create_media(self.bulk_media, self.user)

        imported_counts = {
            media_type: len(media_list)
            for media_type, media_list in self.bulk_media.items()
        }

        list_count = self.save_custom_lists()
        if list_count:
            imported_counts[helpers.CUSTOM_LIST_COUNT_KEY] = list_count

        deduplicated_messages = "\n".join(dict.fromkeys(self.warnings))

        return imported_counts, deduplicated_messages

    def _make_api_request(self, url):
        """Make a request to the Trakt API with proper headers."""
        headers = {
            "Content-Type": "application/json",
            "trakt-api-version": "2",
            "trakt-api-key": settings.TRAKT_API,
        }
        if self.refresh_token:
            try:
                # already made api_request before, so access_token is set
                headers["Authorization"] = f"Bearer {self.access_token}"
            except AttributeError:
                self.access_token = get_access_token(
                    self.refresh_token,
                    redirect_uri=self.redirect_uri,
                )
                headers["Authorization"] = f"Bearer {self.access_token}"
        return services.api_request(
            "TRAKT",
            "GET",
            url,
            headers=headers,
        )

    def _get_paginated_data(self, endpoint, item_type="items"):
        """Get paginated data from Trakt API."""
        page = 1
        all_data = []

        while True:
            url = f"{endpoint}?page={page}&limit={BULK_PAGE_SIZE}"

            try:
                page_data = self._make_api_request(url)
            except requests.exceptions.HTTPError as error:
                if error.response.status_code == requests.codes.not_found:
                    msg = (
                        f"User slug {self.username} not found. "
                        "User slug can be found in your Trakt profile URL."
                    )
                    raise MediaImportError(msg) from error

                if error.response.status_code == requests.codes.unauthorized:
                    msg = "This account is set to private, use OAuth import instead."
                    raise MediaImportError(msg) from error
                raise

            if not page_data:
                # We've reached the end of the data
                break

            all_data.extend(page_data)
            page += 1
            logger.info(
                "Retrieved page %s of %s for user %s (%s items)",
                page - 1,
                item_type,
                self.username,
                len(page_data),
            )

        logger.info(
            "Retrieved %s total %s for user %s",
            len(all_data),
            item_type,
            self.username,
        )
        return all_data

    def process_history(self):
        """Process watch history from Trakt."""
        logger.info("Importing watch history for user %s", self.username)
        history_endpoint = f"{self.user_base_url}/history"
        full_history = self._get_paginated_data(history_endpoint, "history entries")

        # Process in chronological order (oldest first)
        for entry in reversed(full_history):
            watched_at = entry["watched_at"]
            try:
                if entry["type"] == "movie":
                    logger.info(
                        "Processing movie %s watched at %s",
                        entry["movie"]["title"],
                        watched_at,
                    )
                    self.process_watched_movie(entry)
                elif entry["type"] == "episode":
                    logger.info(
                        "Processing episode %s S%sE%s watched at %s",
                        entry["show"]["title"],
                        entry["episode"]["season"],
                        entry["episode"]["number"],
                        watched_at,
                    )
                    self.process_watched_episode(entry)
            except Exception as e:
                msg = f"Error processing history entry: {entry}"
                raise MediaImportUnexpectedError(msg) from e

    def _get_date(self, date_str):
        """Parse a Trakt watched_at timestamp and strip seconds/microseconds."""
        return parse_datetime(date_str).replace(second=0, microsecond=0)

    def _get_tmdb_id(self, entry_data):
        """Extract TMDB ID from entry data."""
        if (
            "ids" in entry_data
            and "tmdb" in entry_data["ids"]
            and entry_data["ids"]["tmdb"]
        ):
            return str(entry_data["ids"]["tmdb"])

        self.warnings.append(
            f"{entry_data['title']}: No {Sources.TMDB.label} ID found.",
        )
        return None

    def _get_metadata(self, media_type, tmdb_id, title, season_number=None):
        """Get metadata for a media item."""
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

    def process_watched_movie(self, entry):
        """Process a single movie watch event."""
        movie = entry["movie"]
        tmdb_id = self._get_tmdb_id(movie)
        if not tmdb_id:
            return

        # Check if we should process this movie based on mode
        if not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            MediaTypes.MOVIE.value,
            Sources.TMDB.value,
            tmdb_id,
            self.mode,
        ):
            return

        metadata = self._get_metadata(MediaTypes.MOVIE.value, tmdb_id, movie["title"])
        if not metadata:
            return

        item = self._get_or_create_item(MediaTypes.MOVIE.value, tmdb_id, metadata)
        watched_at = entry["watched_at"]

        key = f"{tmdb_id}"

        movie_obj = app.models.Movie(
            item=item,
            user=self.user,
            end_date=self._get_date(watched_at),
            status=Status.COMPLETED.value,
            progress=1,
        )
        movie_obj._history_date = parse_datetime(watched_at)

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

    def process_watched_episode(self, entry):
        """Process a single episode watch event."""
        show = entry["show"]
        tmdb_id = self._get_tmdb_id(show)
        if not tmdb_id:
            return

        # Check if we should process this episode based on mode
        if not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            MediaTypes.TV.value,
            Sources.TMDB.value,
            tmdb_id,
            self.mode,
        ):
            return

        # Extract episode data
        season_number = entry["episode"]["season"]
        episode_number = entry["episode"]["number"]

        # Get TV metadata
        tv_metadata = self._get_metadata(MediaTypes.TV.value, tmdb_id, show["title"])
        if not tv_metadata:
            return

        # Get Season metadata
        season_metadata = self._get_metadata(
            MediaTypes.SEASON.value,
            tmdb_id,
            show["title"],
            season_number,
        )
        if not season_metadata:
            return

        # Validate episode number exists in TMDB
        episode_exists = any(
            ep["episode_number"] == episode_number for ep in season_metadata["episodes"]
        )

        if not episode_exists:
            item_identifier = f"{show['title']} S{season_number}E{episode_number}"
            self.warnings.append(
                f"{item_identifier}: not found in {Sources.TMDB.label} "
                f"with ID {tmdb_id}.",
            )
            return

        episode_image = self._get_episode_image(episode_number, season_metadata)
        watched_at = entry["watched_at"]

        # Create or get TV show
        tv_item = self._get_or_create_item(MediaTypes.TV.value, tmdb_id, tv_metadata)
        tv_key = f"{tmdb_id}"

        if tv_key not in self.media_instances[MediaTypes.TV.value]:
            tv_obj = app.models.TV(
                item=tv_item,
                user=self.user,
                status=Status.IN_PROGRESS.value,
            )
            tv_obj._history_date = parse_datetime(watched_at)
            self.bulk_media[MediaTypes.TV.value].append(tv_obj)
            self.media_instances[MediaTypes.TV.value][tv_key] = [tv_obj]
        else:
            tv_obj = self.media_instances[MediaTypes.TV.value][tv_key][0]

        # Create or get Season
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
            season_obj._history_date = parse_datetime(watched_at)
            self.bulk_media[MediaTypes.SEASON.value].append(season_obj)
            self.media_instances[MediaTypes.SEASON.value][season_key] = [season_obj]
        else:
            season_obj = self.media_instances[MediaTypes.SEASON.value][season_key][0]

        # Create Episode item and object
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

        ep_key = f"{tmdb_id}:{season_number}:{episode_number}"

        episode_obj = app.models.Episode(
            item=episode_item,
            related_season=season_obj,
            end_date=self._get_date(watched_at),
        )
        episode_obj._history_date = parse_datetime(watched_at)
        self.media_instances[MediaTypes.EPISODE.value][ep_key].append(episode_obj)
        self.bulk_media[MediaTypes.EPISODE.value].append(episode_obj)

        # Update status if this is the last episode
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

    def process_watchlist(self):
        """Process watchlist from Trakt."""
        logger.info("Importing watchlist for user %s", self.username)
        watchlist_endpoint = f"{self.user_base_url}/watchlist"
        watchlist_data = self._make_api_request(watchlist_endpoint)

        for entry in watchlist_data:
            try:
                self._process_generic_entry(
                    entry,
                    "watchlist",
                    {"status": Status.PLANNING.value},
                )
            except Exception as e:
                msg = f"Error processing watchlist entry: {entry}"
                raise MediaImportUnexpectedError(msg) from e

    def process_ratings(self):
        """Process ratings from Trakt."""
        logger.info("Importing ratings for user %s", self.username)
        ratings_endpoint = f"{self.user_base_url}/ratings"
        ratings_data = self._make_api_request(ratings_endpoint)

        for entry in ratings_data:
            try:
                self._process_generic_entry(
                    entry,
                    "rating",
                    {"score": entry["rating"]},
                )
            except Exception as e:
                msg = f"Error processing rating entry: {entry}"
                raise MediaImportUnexpectedError(msg) from e

    def process_comments(self):
        """Process comments from Trakt."""
        logger.info("Importing comments for user %s", self.username)
        comments_endpoint = f"{self.user_base_url}/comments"
        full_comments = self._get_paginated_data(comments_endpoint, "comments")

        for entry in full_comments:
            try:
                self._process_generic_entry(
                    entry,
                    "comment",
                    {"notes": entry["comment"]["comment"]},
                )
            except Exception as e:
                msg = f"Error processing comment entry: {entry}"
                raise MediaImportUnexpectedError(msg) from e

    def process_lists(self):
        """Process the watchlist-independent lists: personal, shared, favorites."""
        logger.info("Importing custom lists for user %s", self.username)

        for list_data in self.get_custom_lists():
            try:
                self.process_custom_list(list_data)
            except Exception as e:
                msg = f"Error processing list: {list_data}"
                raise MediaImportUnexpectedError(msg) from e

    def get_custom_lists(self):
        """Get the metadata of every Trakt list that should be imported."""
        lists = []

        for endpoint in ("/lists", "/lists/collaborations"):
            lists.extend(
                self._get_optional_list_data(
                    f"{self.user_base_url}{endpoint}",
                    endpoint.removeprefix("/"),
                ),
            )

        # Favorites carries no metadata of its own, an empty ids mapping marks it
        # so that the items are read from the favorites endpoint or file.
        lists.append({"name": FAVORITES_LIST_NAME, "description": "", "ids": {}})

        return [entry for entry in lists if isinstance(entry, dict)]

    def get_custom_list_items(self, list_data):
        """Get the entries held by a single Trakt list."""
        list_id = (list_data.get("ids") or {}).get("trakt")

        if list_id is None:
            return self._get_optional_list_data(
                f"{self.user_base_url}/favorites",
                "favorites",
            )

        return self._get_optional_list_data(
            f"{self.user_base_url}/lists/{list_id}/items",
            f"list {list_id}",
        )

    def _get_optional_list_data(self, endpoint, description):
        """Request list data the account may not expose, [] when unavailable."""
        try:
            data = self._make_api_request(endpoint)
        except requests.exceptions.HTTPError as error:
            if error.response.status_code in MISSING_LIST_STATUSES:
                logger.info("Trakt %s is not available, skipping", description)
                self.warnings.append(f"Trakt {description}: not available, skipped.")
                return []
            raise

        return data if isinstance(data, list) else []

    def process_custom_list(self, list_data):
        """Collect the items of a single Trakt list."""
        name = (list_data.get("name") or "").strip()
        if not name:
            self.warnings.append("A Trakt list without a name was skipped.")
            return

        entries = self.get_custom_list_items(list_data)
        if not entries:
            return

        logger.info("Processing Trakt list %s with %s entries", name, len(entries))

        # Two Trakt lists can share a name, in which case they are merged.
        custom_list = self.custom_lists.setdefault(
            name,
            {"description": list_data.get("description") or "", "items": {}},
        )

        for entry in entries:
            item = self._get_list_entry_item(entry, name)
            if item is None:
                continue

            listed_at = entry.get("listed_at")
            # Keep the first date seen for the item, so a duplicate entry in a
            # merged list does not move it to the top of the list.
            custom_list["items"].setdefault(
                item.pk,
                parse_datetime(listed_at) if listed_at else None,
            )

    def _get_list_entry_item(self, entry, list_name):
        """Get or create the item a Trakt list entry points at."""
        entry_type = entry.get("type")
        media_type = LIST_ENTRY_MEDIA_TYPES.get(entry_type)
        if media_type is None:
            self.warnings.append(
                f"{list_name}: list entries of type {entry_type} are not "
                "supported, skipped.",
            )
            return None

        media_data = (
            entry["movie"] if media_type == MediaTypes.MOVIE.value else entry["show"]
        )
        tmdb_id = self._get_tmdb_id(media_data)
        if not tmdb_id:
            return None

        if media_type == MediaTypes.EPISODE.value:
            return self._get_episode_item(
                tmdb_id,
                media_data["title"],
                entry["episode"]["season"],
                entry["episode"]["number"],
            )

        season_number = (
            entry["season"]["number"] if media_type == MediaTypes.SEASON.value else None
        )
        metadata = self._get_metadata(
            media_type,
            tmdb_id,
            media_data["title"],
            season_number,
        )
        if not metadata:
            return None

        return self._get_or_create_item(media_type, tmdb_id, metadata, season_number)

    def _get_episode_item(self, tmdb_id, title, season_number, episode_number):
        """Get or create the item for a single episode of a show."""
        tv_metadata = self._get_metadata(MediaTypes.TV.value, tmdb_id, title)
        if not tv_metadata:
            return None

        season_metadata = self._get_metadata(
            MediaTypes.SEASON.value,
            tmdb_id,
            title,
            season_number,
        )
        if not season_metadata:
            return None

        episode_exists = any(
            episode["episode_number"] == episode_number
            for episode in season_metadata["episodes"]
        )
        if not episode_exists:
            self.warnings.append(
                f"{title} S{season_number}E{episode_number}: not found in "
                f"{Sources.TMDB.label} with ID {tmdb_id}.",
            )
            return None

        episode_metadata = {
            "title": tv_metadata["title"],
            "image": self._get_episode_image(episode_number, season_metadata),
        }

        return self._get_or_create_item(
            MediaTypes.EPISODE.value,
            tmdb_id,
            episode_metadata,
            season_number,
            episode_number,
        )

    def save_custom_lists(self):
        """Create the collected lists and return how many were imported."""
        imported = 0

        for name, list_data in self.custom_lists.items():
            if not list_data["items"]:
                # Every entry was skipped, so there is no list worth creating.
                continue

            custom_list, created = CustomList.objects.get_or_create(
                owner=self.user,
                name=name,
                defaults={"description": list_data["description"]},
            )
            added = self._add_custom_list_items(custom_list, list_data["items"])
            if created or added:
                imported += 1

        return imported

    def _add_custom_list_items(self, custom_list, listed_at_by_item):
        """Add the items missing from a list, keeping the dates Trakt listed."""
        existing_item_ids = set(
            CustomListItem.objects.filter(custom_list=custom_list).values_list(
                "item_id",
                flat=True,
            ),
        )
        new_item_ids = [
            item_id for item_id in listed_at_by_item if item_id not in existing_item_ids
        ]
        if not new_item_ids:
            return 0

        CustomListItem.objects.bulk_create(
            [
                CustomListItem(custom_list=custom_list, item_id=item_id)
                for item_id in new_item_ids
            ],
            batch_size=BULK_PAGE_SIZE,
            ignore_conflicts=True,
        )

        # date_added is auto_now_add, so it holds the time of the import until
        # the date Trakt recorded is written back over it.
        created_items = [
            list_item
            for list_item in CustomListItem.objects.filter(
                custom_list=custom_list,
                item_id__in=new_item_ids,
            )
            if listed_at_by_item.get(list_item.item_id)
        ]
        for list_item in created_items:
            list_item.date_added = listed_at_by_item[list_item.item_id]

        CustomListItem.objects.bulk_update(
            created_items,
            ["date_added"],
            batch_size=BULK_PAGE_SIZE,
        )

        logger.info(
            "Added %s items to custom list %s",
            len(new_item_ids),
            custom_list.name,
        )
        return len(new_item_ids)

    def _process_generic_entry(self, entry, entry_type, attribute_updates):
        """Process a generic entry (watchlist, rating, or comment)."""
        if entry["type"] == "movie":
            logger.info(
                "Processing movie %s for %s",
                entry["movie"]["title"],
                entry_type,
            )
            # Movies with Completed status (from ratings and comments)
            # should have progress=1
            status = attribute_updates.get("status", Status.COMPLETED.value)
            if status == Status.COMPLETED.value:
                attribute_updates["progress"] = 1

            self._process_media_item(
                entry,
                entry["movie"],
                MediaTypes.MOVIE.value,
                app.models.Movie,
                attribute_updates,
            )
        elif entry["type"] == "show":
            logger.info(
                "Processing show %s for %s",
                entry["show"]["title"],
                entry_type,
            )
            self._process_media_item(
                entry,
                entry["show"],
                MediaTypes.TV.value,
                app.models.TV,
                attribute_updates,
            )
        elif entry["type"] == "season":
            logger.info(
                "Processing season %s S%s for %s",
                entry["show"]["title"],
                entry["season"]["number"],
                entry_type,
            )
            self._process_media_item(
                entry,
                entry["show"],
                MediaTypes.SEASON.value,
                app.models.Season,
                attribute_updates,
                entry["season"]["number"],
            )

    def _process_media_item(
        self,
        entry,
        media_data,
        media_type,
        model_class,
        defaults,
        season_number=None,
    ):
        """Process media items for watchlist, ratings, and comments."""
        tmdb_id = self._get_tmdb_id(media_data)
        if not tmdb_id:
            return

        parent_type = (
            MediaTypes.TV.value if media_type == MediaTypes.SEASON.value else media_type
        )
        if not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            parent_type,
            Sources.TMDB.value,
            tmdb_id,
            self.mode,
        ):
            return

        metadata = self._get_metadata(
            media_type,
            tmdb_id,
            media_data["title"],
            season_number,
        )
        if not metadata:
            return

        updated_at = parse_datetime(
            entry.get("listed_at")
            or entry.get("rated_at")
            or entry["comment"].get("updated_at"),
        )

        if media_type == MediaTypes.SEASON.value:
            tv_obj = self._get_tv_obj(tmdb_id, media_data, updated_at)
            if not tv_obj:
                return
            defaults["related_tv"] = tv_obj

        key = f"{tmdb_id}"
        if media_type == MediaTypes.SEASON.value:
            key = f"{key}:{season_number}"

        item = self._get_or_create_item(media_type, tmdb_id, metadata, season_number)

        if key in self.media_instances[media_type]:
            self._update_instance(media_type, key, defaults)
        else:
            media_obj = model_class(
                item=item,
                user=self.user,
                **defaults,
            )
            media_obj._history_date = updated_at
            self.bulk_media[media_type].append(media_obj)
            self.media_instances[media_type][key] = [media_obj]

    def _get_tv_obj(self, tmdb_id, media_data, updated_at):
        """Get or create a TV object for the given season."""
        tv_metadata = self._get_metadata(
            MediaTypes.TV.value,
            tmdb_id,
            media_data["title"],
        )
        if not tv_metadata:
            return None

        tv_item = self._get_or_create_item(
            MediaTypes.TV.value,
            tmdb_id,
            tv_metadata,
        )

        tv_key = f"{tmdb_id}"

        # Create or get the TV object
        if tv_key in self.media_instances[MediaTypes.TV.value]:
            tv_obj = self.media_instances[MediaTypes.TV.value][tv_key][0]
        else:
            tv_obj = app.models.TV(
                item=tv_item,
                user=self.user,
                status=Status.IN_PROGRESS.value,
            )
            tv_obj._history_date = updated_at
            self.bulk_media[MediaTypes.TV.value].append(tv_obj)
            self.media_instances[MediaTypes.TV.value][tv_key] = [tv_obj]
        return tv_obj

    def _update_instance(self, media_type, key, defaults):
        """Update the instance with new attributes."""
        for media_obj in self.media_instances[media_type][key]:
            for attr, value in defaults.items():
                setattr(media_obj, attr, value)


class TraktExportImporter(TraktImporter):
    """Run the standard Trakt import against an export archive instead of the API."""

    def __init__(self, file, user, mode):
        """Initialize from a Trakt export archive."""
        # The archive appends to this list while files are read during the import,
        # so it is shared with the importer instead of being merged afterwards.
        warnings = []
        self.export_archive = TraktArchiveManager(file, warnings)

        user_profile = self.export_archive.parse_json("user-profile")
        username = (
            user_profile.get("username")
            if isinstance(user_profile, dict)
            else EXPORT_FALLBACK_USERNAME
        )

        super().__init__(username or EXPORT_FALLBACK_USERNAME, user, mode)
        self.warnings = warnings

    def _make_api_request(self, url):
        """Read from the export archive instead of making API calls."""
        return self._get_paginated_data(url)

    def _get_paginated_data(self, endpoint, item_type="items"):
        """Read the export files matching the endpoint."""
        relative_filepath = endpoint.removeprefix(self.user_base_url)
        prefixes = ENDPOINTS_TO_FILES.get(relative_filepath)

        if prefixes is None:
            logger.warning("No Trakt export file mapped for endpoint %s", endpoint)
            return []

        # Read the JSON files from the export archive and concatenate their contents.
        entries = self.export_archive.load(*prefixes)
        logger.info(
            "Retrieved %s total %s for user %s from export archive",
            len(entries),
            item_type,
            self.username,
        )
        return entries

    def get_custom_list_items(self, list_data):
        """Read the export file holding a custom list's items."""
        ids = list_data.get("ids") or {}
        list_id = ids.get("trakt")

        if list_id is None:
            # Favorites is exported as a single file rather than as a list.
            return self.export_archive.load("lists-favorites")

        return self.export_archive.load_list_items(list_id, ids.get("slug"))


class TraktArchiveManager:
    """Class to manage a Trakt export archive."""

    def __init__(self, file, warnings=None):
        """Open the archive and index its JSON files.

        Args:
            file: Uploaded export archive, or any file-like object
            warnings (list, optional): List to append read warnings to
        """
        try:
            self.zipfile = zipfile.ZipFile(file)
        except zipfile.BadZipFile as e:
            msg = "The uploaded file is not a valid Trakt export archive."
            raise MediaImportError(msg) from e

        self.warnings = warnings if warnings is not None else []

        # Map each JSON base name to its full path inside the archive, so archives
        # that keep the export in a subdirectory can still be read.
        self._files = {}

        uncompressed_size = 0

        # Check the uncompressed size of the archive to avoid memory issues.
        # file_size is what the archive declares, so this is a guard against
        # oversized exports rather than against deliberately crafted archives.
        for info in self.zipfile.infolist():
            if info.is_dir():
                continue
            uncompressed_size += info.file_size
            if uncompressed_size > MAX_EXPORT_UNCOMPRESSED_BYTES:
                msg = "The uncompressed Trakt export archive is too large to import."
                raise MediaImportError(msg)

            name = info.filename.rsplit("/", 1)[-1]
            if not name.lower().endswith(".json"):
                continue
            # Store the base name without the .json suffix for easier matching.
            self._files.setdefault(name[: -len(".json")], info.filename)

        if not self._recognized_export():
            msg = (
                "The uploaded archive does not contain any Trakt export data. "
                "Upload the ZIP file downloaded from the Trakt website."
            )
            raise MediaImportError(msg)

    def _recognized_export(self):
        """Check whether the archive holds at least one known export file."""
        if "user-profile" in self._files:
            return True

        return any(
            self._match_names(prefix)
            for prefixes in ENDPOINTS_TO_FILES.values()
            for prefix in prefixes
        )

    def parse_json(self, base_name):
        """Read a JSON file from the archive, returning None when unavailable."""
        filename = self._files.get(base_name)
        if filename is None:
            return None

        try:
            return json.loads(self.zipfile.read(filename))
        except (KeyError, OSError, json.JSONDecodeError, UnicodeDecodeError):
            logger.exception("Trakt export file %s could not be read", filename)
            self.warnings.append(f"{base_name}.json: could not be read, skipped.")
            return None

    def load(self, *prefixes):
        """Concatenate every file into a single list, sorted by page number."""
        return self._load_names(
            [name for prefix in prefixes for name in self._match_names(prefix)],
        )

    def load_list_items(self, list_id, slug=None):
        """Concatenate the files holding a single custom list's items."""
        base_names = self._list_items_names(list_id, slug)
        if not base_names:
            self.warnings.append(
                f"{LIST_ITEMS_FILE_PREFIX}-{list_id}.json: missing from the "
                "export, skipped.",
            )
            return []

        return self._load_names(base_names)

    def _list_items_names(self, list_id, slug):
        """Return the base names of the files holding a list's items."""
        prefix = f"{LIST_ITEMS_FILE_PREFIX}-{list_id}"

        # Trakt names the file after the list slug, and pages long lists.
        if slug:
            names = self._match_names(f"{prefix}-{slug}")
            if names:
                return names

        # The list may have been renamed since the export was written, so fall
        # back to every file exported for this list id, whatever slug it used.
        return sorted(
            base_name
            for base_name in self._files
            if base_name == prefix or base_name.startswith(f"{prefix}-")
        )

    def _load_names(self, base_names):
        """Concatenate the contents of the named JSON files into one list."""
        entries = []
        for base_name in base_names:
            page = self.parse_json(base_name)
            if page is None:
                # parse_json already recorded why the file was skipped.
                continue
            if isinstance(page, list):
                entries.extend(page)
            else:
                self.warnings.append(f"{base_name}.json: unexpected contents, skipped.")
        return entries

    def _match_names(self, prefix):
        """Return base names for prefixes by page."""
        names = []
        if prefix in self._files:
            names.append(prefix)

        pages = {}
        page_pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
        for base_name in self._files:
            match = page_pattern.match(base_name)
            if match:
                pages[int(match.group(1))] = base_name

        # Trakt splits large sections into pages numbered contiguously from 1, e.g.
        # watched-history-1.json. Only following that run keeps a custom list such as
        # lists-watchlist-2025 from being read as a page of lists-watchlist.
        page = 1
        while page in pages:
            names.append(pages[page])
            page += 1

        return names
