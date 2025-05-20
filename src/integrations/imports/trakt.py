import logging
from collections import defaultdict

import requests
from django.apps import apps
from django.conf import settings

import app
from app.models import Media, MediaTypes, Sources
from app.providers import services
from integrations import helpers
from integrations.helpers import MediaImportError

logger = logging.getLogger(__name__)

TRAKT_API_BASE_URL = "https://api.trakt.tv"
BULK_PAGE_SIZE = 1000


def importer(username, user, mode):
    """Import the user's data from Trakt."""
    trakt_importer = TraktImporter(username, user, mode)
    return trakt_importer.import_data()


class TraktImporter:
    """Class to handle importing user data from Trakt."""

    def __init__(self, username, user, mode):
        """Initialize the importer with user details and mode.

        Args:
            username (str): Trakt username to import from
            user: Django user object to import data for
            mode (str): Import mode ("new" or "overwrite")
        """
        self.username = username
        self.user = user
        self.mode = mode
        self.user_base_url = f"{TRAKT_API_BASE_URL}/users/{username}"
        self.warnings = set()

        # Track existing media to handle "new" mode correctly
        self.existing_media = self._get_existing_media()

        # Track media IDs to delete in overwrite mode
        self.to_delete = defaultdict(set)

        # Track bulk creation lists for each media type
        self.bulk_media = defaultdict(lambda: [[]])

        # Track media instances being created
        self.media_instances = defaultdict(dict)

        logger.info(
            "Initialized Trakt importer for user %s with mode %s",
            username,
            mode,
        )

    def _get_existing_media(self):
        """Get all existing media for the user to check against during import."""
        existing = {
            MediaTypes.TV.value: {},
            MediaTypes.MOVIE.value: {},
        }

        # Get existing TV shows
        for tv in app.models.TV.objects.filter(user=self.user).select_related("item"):
            existing[MediaTypes.TV.value][tv.item.media_id] = tv

        # Get existing movies
        for movie in app.models.Movie.objects.filter(user=self.user).select_related(
            "item",
        ):
            existing[MediaTypes.MOVIE.value][movie.item.media_id] = movie

        logger.info(
            "Found existing: %s TV shows, %s movies",
            len(existing[MediaTypes.TV.value]),
            len(existing[MediaTypes.MOVIE.value]),
        )

        return existing

    def import_data(self):
        """Import all user data from Trakt."""
        self.process_history()
        self.process_watchlist()
        self.process_ratings()
        self.process_comments()

        self.cleanup_existing_media()

        # Bulk create all media
        self._bulk_create_media()

        return (
            len(self.media_instances[MediaTypes.TV.value]),
            len(self.media_instances[MediaTypes.SEASON.value]),
            len(self.media_instances[MediaTypes.EPISODE.value]),
            len(self.media_instances[MediaTypes.MOVIE.value]),
            "\n".join(self.warnings),
        )

    def _bulk_create_media(self):
        """Bulk create all media objects."""
        for media_type, bulk_list in self.bulk_media.items():
            if not bulk_list or not bulk_list[0]:
                continue

            model = apps.get_model(app_label="app", model_name=media_type)

            logger.info("Bulk importing %s", media_type)

            for i, bulk_media in enumerate(bulk_list):
                if not bulk_media:
                    continue

                # Update references for seasons and episodes
                if media_type == MediaTypes.SEASON.value:
                    logger.info("Updating references for season to existing TV shows")
                    helpers.update_season_references(bulk_media, self.user)
                elif media_type == MediaTypes.EPISODE.value:
                    logger.info(
                        "Updating references for episodes to existing TV seasons",
                    )
                    helpers.update_episode_references(bulk_media, self.user)

                logger.info(
                    "Bulk importing %s list %s of %s",
                    media_type,
                    i + 1,
                    len(bulk_list),
                )

                helpers.bulk_create_update_with_history(
                    bulk_media,
                    model,
                    user=self.user,
                )

    def cleanup_existing_media(self):
        """Delete existing media if in overwrite mode."""
        for media_type, obj_ids in self.to_delete.items():
            if obj_ids:
                logger.info(
                    "Deleting %s objects for user %s in overwrite mode",
                    media_type,
                    self.user,
                )
                model = apps.get_model(app_label="app", model_name=media_type)
                model.objects.filter(
                    id__in=obj_ids,
                    user=self.user,
                ).delete()

    def _make_api_request(self, url):
        """Make a request to the Trakt API with proper headers."""
        headers = {
            "Content-Type": "application/json",
            "trakt-api-version": "2",
            "trakt-api-key": settings.TRAKT_API,
        }
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
                raise MediaImportError(msg) from e

    def _get_tmdb_id(self, entry_data):
        """Extract TMDB ID from entry data."""
        if (
            "ids" in entry_data
            and "tmdb" in entry_data["ids"]
            and entry_data["ids"]["tmdb"]
        ):
            return str(entry_data["ids"]["tmdb"])

        self.warnings.add(f"{entry_data['title']}: No {Sources.TMDB.label} ID found.")
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
                self.warnings.add(
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

    def _should_process_media(self, media_type, tmdb_id):
        """Determine if a media item should be processed based on mode."""
        key = f"{tmdb_id}"
        exists = key in self.existing_media[media_type]

        if self.mode == "new" and exists:
            # In "new" mode, skip if media already exists
            logger.info(
                "Skipping existing %s: %s (mode: new)",
                media_type,
                key,
            )
            return False

        if self.mode == "overwrite" and exists:
            # In "overwrite" mode, add to the deletion list
            logger.info(
                "Adding existing %s to deletion list: %s (mode: overwrite)",
                media_type,
                key,
            )
            obj_id = self.existing_media[media_type][key].id
            self.to_delete[media_type].add(obj_id)

        return True

    def _add_to_bulk_media(self, media_type, obj, n_watches):
        """Add a media object to the bulk creation list."""
        try:
            self.bulk_media[media_type][n_watches].append(obj)
        except IndexError:
            # If the list index is out of range, create a new list
            self.bulk_media[media_type].append([obj])

        return obj

    def process_watched_movie(self, entry):
        """Process a single movie watch event."""
        movie = entry["movie"]
        tmdb_id = self._get_tmdb_id(movie)
        if not tmdb_id:
            return

        # Check if we should process this movie based on mode
        if not self._should_process_media(MediaTypes.MOVIE.value, tmdb_id):
            return

        metadata = self._get_metadata(MediaTypes.MOVIE.value, tmdb_id, movie["title"])
        if not metadata:
            return

        item = self._get_or_create_item(MediaTypes.MOVIE.value, tmdb_id, metadata)
        watched_at = entry["watched_at"]

        key = f"{tmdb_id}"

        if key in self.media_instances[MediaTypes.MOVIE.value]:
            logger.info(
                "Existing movie found: %s, adding another watch entry",
                movie["title"],
            )

            n_watches = len(self.media_instances[MediaTypes.MOVIE.value][key])

            extra_obj = app.models.Movie(
                item=item,
                user=self.user,
                repeats=n_watches,
                end_date=watched_at,
                status=Media.Status.COMPLETED.value,
            )

            self._add_to_bulk_media(MediaTypes.MOVIE.value, extra_obj, n_watches)

            # Update the watch count
            self.media_instances[MediaTypes.MOVIE.value][key].append(extra_obj)
        else:
            original_obj = app.models.Movie(
                item=item,
                user=self.user,
                end_date=watched_at,
                status=Media.Status.COMPLETED.value,
            )

            self._add_to_bulk_media(MediaTypes.MOVIE.value, original_obj, n_watches=0)
            self.media_instances[MediaTypes.MOVIE.value][key] = [original_obj]

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
        if not self._should_process_media(MediaTypes.TV.value, tmdb_id):
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
            self.warnings.add(
                f"{item_identifier}: not found in TMDB with ID {tmdb_id}.",
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
                status=Media.Status.IN_PROGRESS.value,
            )
            self._add_to_bulk_media(MediaTypes.TV.value, tv_obj, n_watches=0)
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
                status=Media.Status.IN_PROGRESS.value,
            )
            self._add_to_bulk_media(MediaTypes.SEASON.value, season_obj, n_watches=0)
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
        if ep_key in self.media_instances[MediaTypes.EPISODE.value]:
            logger.info(
                "Existing episode found: %s S%sE%s, adding another watch entry",
                show["title"],
                season_number,
                episode_number,
            )

            n_watches = len(self.media_instances[MediaTypes.EPISODE.value][ep_key])
            extra_obj = app.models.Episode(
                item=episode_item,
                related_season=season_obj,
                repeats=n_watches,
                end_date=watched_at,
            )

            self._add_to_bulk_media(
                MediaTypes.EPISODE.value,
                extra_obj,
                n_watches,
            )

            # Update the watch count
            self.media_instances[MediaTypes.EPISODE.value][ep_key].append(extra_obj)
        else:
            original_obj = app.models.Episode(
                item=episode_item,
                related_season=season_obj,
                end_date=watched_at,
            )

            self._add_to_bulk_media(MediaTypes.EPISODE.value, original_obj, n_watches=0)
            self.media_instances[MediaTypes.EPISODE.value][ep_key] = [original_obj]

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
            # If this is the last episode of the season, mark the season as completed
            season_obj.status = Media.Status.COMPLETED.value

            # If this is the last episode of the show, mark the show as completed
            last_season = tv_metadata.get("last_episode_season")
            if last_season and last_season == season_number:
                tv_obj.status = Media.Status.COMPLETED.value

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
                    {"status": Media.Status.PLANNING.value},
                )
            except Exception as e:
                msg = f"Error processing watchlist entry: {entry}"
                raise MediaImportError(msg) from e

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
                raise MediaImportError(msg) from e

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
                raise MediaImportError(msg) from e

    def _process_generic_entry(self, entry, entry_type, attribute_updates=None):
        """Process a generic entry (watchlist, rating, or comment)."""
        if entry["type"] == "movie":
            logger.info(
                "Processing movie %s for %s",
                entry["movie"]["title"],
                entry_type,
            )
            self._process_media_item(
                entry["movie"],
                MediaTypes.MOVIE.value,
                app.models.Movie,
                attribute_updates or {},
            )
        elif entry["type"] == "show":
            logger.info(
                "Processing show %s for %s",
                entry["show"]["title"],
                entry_type,
            )
            self._process_media_item(
                entry["show"],
                MediaTypes.TV.value,
                app.models.TV,
                attribute_updates or {},
            )
        elif entry["type"] == "season":
            logger.info(
                "Processing season %s S%s for %s",
                entry["show"]["title"],
                entry["season"]["number"],
                entry_type,
            )
            self._process_media_item(
                entry["show"],
                MediaTypes.SEASON.value,
                app.models.Season,
                attribute_updates or {},
                entry["season"]["number"],
            )

    def _process_media_item(
        self,
        media_data,
        media_type,
        model_class,
        defaults=None,
        season_number=None,
    ):
        """Process media items for watchlist, ratings, and comments."""
        tmdb_id = self._get_tmdb_id(media_data)
        if not tmdb_id:
            return

        parent_type = (
            MediaTypes.TV.value if media_type == MediaTypes.SEASON.value else media_type
        )
        if not self._should_process_media(parent_type, tmdb_id):
            return

        metadata = self._get_metadata(
            media_type,
            tmdb_id,
            media_data["title"],
            season_number,
        )
        if not metadata:
            return

        if media_type == MediaTypes.SEASON.value:
            tv_obj = self._get_tv_obj(tmdb_id, media_data)
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

            self._add_to_bulk_media(media_type, media_obj, n_watches=0)
            self.media_instances[media_type][key] = [media_obj]

    def _get_tv_obj(self, tmdb_id, media_data):
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
                status=Media.Status.IN_PROGRESS.value,
            )
            self._add_to_bulk_media(MediaTypes.TV.value, tv_obj, n_watches=0)
            self.media_instances[MediaTypes.TV.value][tv_key] = [tv_obj]
        return tv_obj

    def _update_instance(self, media_type, key, defaults):
        """Update the instance with new attributes."""
        for media_obj in self.media_instances[media_type][key]:
            for attr, value in defaults.items():
                setattr(media_obj, attr, value)
