import logging
from collections import defaultdict

import requests
from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

import app
from app import helpers as app_helpers
from app.models import MediaTypes, Sources, Status
from app.providers import services
from integrations.imports import helpers
from integrations.imports.helpers import MediaImportError, MediaImportUnexpectedError

logger = logging.getLogger(__name__)


def get_token(request):
    """View for getting the SIMKL OAuth2 token."""
    code = request.GET["code"]
    url = "https://api.simkl.com/oauth/token"

    headers = {
        "Content-Type": "application/json",
    }

    params = {
        "client_id": settings.SIMKL_ID,
        "client_secret": settings.SIMKL_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": app_helpers.build_absolute_app_url(
            request,
            reverse("import_simkl_private"),
        ),
    }

    try:
        token_response = app.providers.services.api_request(
            "SIMKL",
            "POST",
            url,
            headers=headers,
            params=params,
        )
    except services.ProviderAPIError as error:
        if error.status_code == requests.codes.unauthorized:
            msg = "Invalid SIMKL secret key."
            raise MediaImportError(msg) from error
        raise

    return {
        "access_token": token_response["access_token"],
        "username": get_username(token_response["access_token"]),
    }


def get_username(token):
    """Get the username from SIMKL using the provided token."""
    try:
        user_info = app.providers.services.api_request(
            "SIMKL",
            "POST",
            "https://api.simkl.com/users/settings",
            headers={
                "Authorization": f"Bearer {token}",
                "simkl-api-key": settings.SIMKL_ID,
                "Content-Type": "application/json",
            },
        )
    except services.ProviderAPIError as error:
        if error.status_code == requests.codes.unauthorized:
            msg = "Invalid SIMKL secret key."
            raise MediaImportError(msg) from error
        raise

    return user_info["user"]["name"]


def importer(token, user, mode):
    """Import tv shows, movies and anime from SIMKL."""
    simkl_importer = SimklImporter(token, user, mode)
    return simkl_importer.import_data()


class SimklImporter:
    """Class to handle importing user data from Simkl."""

    SIMKL_API_BASE_URL = "https://api.simkl.com"

    def __init__(self, token, user, mode):
        """Initialize the importer with token, user, and mode.

        Args:
            token (str): Simkl OAuth token
            user: Django user object to import data for
            mode (str): Import mode ("new" or "overwrite")
        """
        self.token = helpers.decrypt(token)
        self.user = user
        self.mode = mode
        self.warnings = []

        # Track existing media for "new" mode
        self.existing_media = helpers.get_existing_media(user)

        # Track existing seasons and watches to only add what is missing in "new" mode
        self.existing_seasons = {}
        self.existing_watches = {}
        if mode == "new":
            self.existing_seasons = helpers.get_existing_seasons(user)
            self.existing_watches = helpers.get_existing_watches(user)

        # Track existing media changed in "new" mode, by primary key
        self.to_update = defaultdict(dict)

        # Track media IDs to delete in overwrite mode
        self.to_delete = defaultdict(lambda: defaultdict(set))

        # Track bulk creation lists for each media type
        self.bulk_media = defaultdict(list)

        logger.info(
            "Initialized Simkl importer for user %s with mode %s",
            user.username,
            mode,
        )

    def import_data(self):
        """Import all user data from Simkl."""
        data = self._get_user_list()

        if not data:
            return {}, ""

        self._process_media_lists(data)

        helpers.cleanup_existing_media(self.to_delete, self.user)
        helpers.bulk_create_media(self.bulk_media, self.user)
        helpers.bulk_update_media(
            self.to_update,
            helpers.EXISTING_MEDIA_UPDATE_FIELDS,
            self.user,
        )

        imported_counts = {
            media_type: len(media_list)
            for media_type, media_list in self.bulk_media.items()
        }

        deduplicated_messages = "\n".join(dict.fromkeys(self.warnings))
        return imported_counts, deduplicated_messages

    def _get_user_list(self):
        """Get the user's list from Simkl."""
        url = f"{self.SIMKL_API_BASE_URL}/sync/all-items/"
        headers = {
            "Authorization": f"Bearer: {self.token}",
            "simkl-api-key": settings.SIMKL_ID,
        }
        params = {
            "extended": "full",
            "episode_watched_at": "yes",
            "memos": "yes",
        }

        return app.providers.services.api_request(
            "SIMKL",
            "GET",
            url,
            headers=headers,
            params=params,
        )

    def _process_media_lists(self, data):
        """Process all media types from Simkl."""
        if "shows" in data:
            self._process_tv_list(data["shows"])
        if "movies" in data:
            self._process_movie_list(data["movies"])
        if "anime" in data:
            self._process_anime_list(data["anime"])

    def _get_existing(self, media_type, source, media_id, season_number=None):
        """Get the media already in Yamtrack, only tracked in "new" mode."""
        if self.mode != "new":
            return None

        if media_type == MediaTypes.SEASON.value:
            return self.existing_seasons.get((source, str(media_id), season_number))

        return self.existing_media[media_type][source].get(str(media_id))

    def _sync_existing(self, media_type, media, entry, status=None):
        """Fill the missing score and notes of existing media and advance its status."""
        changed = helpers.fill_empty_fields(
            media,
            {"score": entry["user_rating"], "notes": self._get_notes(entry)},
        )
        if status and helpers.advance_status(media, status):
            changed = True

        if changed:
            self.to_update[media_type][media.pk] = media

    def _get_episode_watch_key(self, tmdb_id, season_number, episode):
        """Return the key that identifies an episode in the existing watches."""
        return helpers.get_watch_key(
            MediaTypes.EPISODE.value,
            Sources.TMDB.value,
            tmdb_id,
            season_number,
            episode["number"],
        )

    def _is_new_episode_watch(self, tmdb_id, season_number, episode):
        """Return whether the watch of an episode is missing from Yamtrack."""
        return not helpers.is_existing_watch(
            self.existing_watches,
            self._get_episode_watch_key(tmdb_id, season_number, episode),
            self._get_date(episode.get("watched_at")),
        )

    def _has_new_episodes(self, tv):
        """Return whether the show has episode watches missing from Yamtrack."""
        tmdb_id = tv["show"]["ids"]["tmdb"]
        return any(
            self._is_new_episode_watch(tmdb_id, season["number"], episode)
            for season in tv.get("seasons", [])
            for episode in season["episodes"]
        )

    def _reopen_completed(self, tmdb_id, season_number, episodes, medias):
        """Reopen completed existing media that got episodes never watched before."""
        first_watch = any(
            self._get_episode_watch_key(tmdb_id, season_number, episode)
            not in self.existing_watches
            for episode in episodes
        )
        if not first_watch:
            return

        for media_type, media in medias:
            if media.pk is not None and helpers.reopen_status(media):
                self.to_update[media_type][media.pk] = media

    def _should_process_tv(self, tv, existing_tv, tv_status):
        """Determine if a show should be processed based on mode."""
        # Existing shows only get the episodes they are missing
        if existing_tv:
            self._sync_existing(MediaTypes.TV.value, existing_tv, tv, tv_status)
            return self._has_new_episodes(tv)

        return helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            MediaTypes.TV.value,
            Sources.TMDB.value,
            str(tv["show"]["ids"]["tmdb"]),
            self.mode,
        )

    def _process_tv_list(self, tv_list):
        """Process TV list from Simkl."""
        logger.info("Processing tv shows")
        existing_tv_ids = set()

        for tv in tv_list:
            try:
                title = tv["show"]["title"]
                logger.debug("Processing %s", title)

                try:
                    tmdb_id = tv["show"]["ids"]["tmdb"]
                except KeyError:
                    self.warnings.append(f"{title}: No TMDB ID found")
                    continue

                if tmdb_id in existing_tv_ids:
                    self.warnings.append(
                        f"{title} ({tmdb_id}) already present in the import list",
                    )
                    continue

                tv_status = self._get_status(tv["status"])

                existing_tv = self._get_existing(
                    MediaTypes.TV.value,
                    Sources.TMDB.value,
                    tmdb_id,
                )

                # Check if we should process this entry based on mode
                if not self._should_process_tv(tv, existing_tv, tv_status):
                    continue

                try:
                    season_numbers = [season["number"] for season in tv["seasons"]]
                except KeyError:
                    season_numbers = []

                try:
                    metadata = app.providers.tmdb.tv_with_seasons(
                        tmdb_id,
                        season_numbers,
                    )
                except services.ProviderAPIError as error:
                    if error.status_code == requests.codes.not_found:
                        self.warnings.append(
                            f"{title}: not found in {Sources.TMDB.label} "
                            f"with ID {tmdb_id}.",
                        )
                        continue
                    raise

                tv_item, _ = app.models.Item.objects.get_or_create(
                    media_id=tmdb_id,
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.TV.value,
                    defaults={
                        "title": metadata["title"],
                        "image": metadata["image"],
                    },
                )

                tv_instance = existing_tv or self._create_tv(tv, tv_item, tv_status)
                existing_tv_ids.add(tmdb_id)

                if season_numbers:
                    self._process_seasons_and_episodes(
                        tv,
                        tv_instance,
                        metadata,
                    )

            except Exception as error:
                msg = f"Error processing entry: {tv}"
                raise MediaImportUnexpectedError(msg) from error

        logger.info("Processed %d tv shows", len(tv_list))

    def _create_tv(self, tv, tv_item, tv_status):
        """Create the TV instance of a show that is not in Yamtrack."""
        tv_instance = app.models.TV(
            item=tv_item,
            user=self.user,
            status=tv_status,
            score=tv["user_rating"],
            notes=self._get_notes(tv),
        )
        tv_instance._history_date = self._get_history_date(tv)
        self.bulk_media[MediaTypes.TV.value].append(tv_instance)
        return tv_instance

    def _process_seasons_and_episodes(self, tv, tv_instance, metadata):
        """Process seasons and episodes for a TV show."""
        tmdb_id = tv["show"]["ids"]["tmdb"]

        for season in tv["seasons"]:
            season_number = season["number"]
            episodes = season["episodes"]
            season_metadata = metadata[f"season/{season_number}"]

            existing_season = self._get_existing(
                MediaTypes.SEASON.value,
                Sources.TMDB.value,
                tmdb_id,
                season_number,
            )
            new_episodes = [
                episode
                for episode in episodes
                if self._is_new_episode_watch(tmdb_id, season_number, episode)
            ]
            if existing_season and not new_episodes:
                continue

            season_item, _ = app.models.Item.objects.get_or_create(
                media_id=tmdb_id,
                source=Sources.TMDB.value,
                media_type=MediaTypes.SEASON.value,
                season_number=season_number,
                defaults={
                    "title": metadata["title"],
                    "image": season_metadata["image"],
                },
            )

            if episodes[-1]["number"] == season_metadata["max_progress"]:
                season_status = Status.COMPLETED.value
            else:
                season_status = self._get_status(tv["status"])

            # Unfinished shows and seasons are not left as completed
            unfinished = []
            if self._get_status(tv["status"]) != Status.COMPLETED.value:
                unfinished.append((MediaTypes.TV.value, tv_instance))
            if existing_season and season_status != Status.COMPLETED.value:
                unfinished.append((MediaTypes.SEASON.value, existing_season))
            self._reopen_completed(tmdb_id, season_number, new_episodes, unfinished)

            if existing_season:
                season_instance = existing_season
                if helpers.advance_status(season_instance, season_status):
                    self.to_update[MediaTypes.SEASON.value][season_instance.pk] = (
                        season_instance
                    )
            else:
                season_instance = app.models.Season(
                    item=season_item,
                    user=self.user,
                    related_tv=tv_instance,
                    status=season_status,
                )
                season_instance._history_date = self._get_history_date(tv)
                self.bulk_media[MediaTypes.SEASON.value].append(season_instance)

            # Process episodes
            for episode in new_episodes:
                ep_img = self._get_episode_image(episode, season_number, metadata)
                episode_item, _ = app.models.Item.objects.get_or_create(
                    media_id=tmdb_id,
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.EPISODE.value,
                    season_number=season_number,
                    episode_number=episode["number"],
                    defaults={
                        "title": metadata["title"],
                        "image": ep_img,
                    },
                )

                episode_instance = app.models.Episode(
                    item=episode_item,
                    related_season=season_instance,
                    end_date=self._get_date(episode.get("watched_at")),
                )
                episode_instance._history_date = (
                    self._get_date(
                        episode.get("watched_at"),
                    )
                    or timezone.now()
                )
                self.bulk_media[MediaTypes.EPISODE.value].append(episode_instance)

    def _get_episode_image(self, episode, season_number, metadata):
        """Get the image for the episode."""
        for episode_metadata in metadata[f"season/{season_number}"]["episodes"]:
            if episode_metadata["episode_number"] == episode["number"]:
                return (
                    f"https://image.tmdb.org/t/p/w500{episode_metadata['still_path']}"
                )
        return settings.IMG_NONE

    def _process_movie_list(self, movie_list):
        """Process movie list from Simkl."""
        logger.info("Processing movies")
        existing_movie_ids = set()

        for movie in movie_list:
            try:
                title = movie["movie"]["title"]
                logger.debug("Processing %s", title)

                try:
                    tmdb_id = movie["movie"]["ids"]["tmdb"]
                except KeyError:
                    self.warnings.append(f"{title}: No TMDB ID found")
                    continue

                if tmdb_id in existing_movie_ids:
                    self.warnings.append(
                        f"{title} ({tmdb_id}) already present in the import list",
                    )
                    continue

                movie_status = self._get_status(movie["status"])
                watched_at = self._get_date(movie.get("last_watched_at"))

                existing_movie = self._get_existing(
                    MediaTypes.MOVIE.value,
                    Sources.TMDB.value,
                    tmdb_id,
                )
                if existing_movie:
                    self._sync_existing(MediaTypes.MOVIE.value, existing_movie, movie)
                    if not self._is_new_movie_watch(
                        existing_movie,
                        tmdb_id,
                        movie_status,
                        watched_at,
                    ):
                        continue
                # Check if we should process this entry based on mode
                elif not helpers.should_process_media(
                    self.existing_media,
                    self.to_delete,
                    MediaTypes.MOVIE.value,
                    Sources.TMDB.value,
                    str(tmdb_id),
                    self.mode,
                ):
                    continue

                try:
                    metadata = app.providers.tmdb.movie(tmdb_id)
                except services.ProviderAPIError as error:
                    if error.status_code == requests.codes.not_found:
                        self.warnings.append(
                            f"{title}: not found in {Sources.TMDB.label} "
                            f"with ID {tmdb_id}.",
                        )
                        continue
                    raise

                movie_item, _ = app.models.Item.objects.get_or_create(
                    media_id=tmdb_id,
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.MOVIE.value,
                    defaults={
                        "title": metadata["title"],
                        "image": metadata["image"],
                    },
                )

                movie_instance = app.models.Movie(
                    item=movie_item,
                    user=self.user,
                    status=movie_status,
                    score=movie["user_rating"],
                    progress=1 if movie_status == Status.COMPLETED.value else 0,
                    start_date=watched_at,
                    end_date=watched_at,
                    notes=self._get_notes(movie),
                )
                movie_instance._history_date = self._get_history_date(movie)
                self.bulk_media[MediaTypes.MOVIE.value].append(movie_instance)
                existing_movie_ids.add(tmdb_id)

            except Exception as error:
                msg = f"Error processing entry: {movie}"
                raise MediaImportUnexpectedError(msg) from error

        logger.info("Processed %d movies", len(movie_list))

    def _is_new_movie_watch(self, existing_movie, tmdb_id, movie_status, watched_at):
        """Return whether a watch of an existing movie should be added as a new one."""
        if movie_status != Status.COMPLETED.value or watched_at is None:
            return False

        watch_key = helpers.get_watch_key(
            MediaTypes.MOVIE.value,
            Sources.TMDB.value,
            tmdb_id,
        )
        if helpers.is_existing_watch(self.existing_watches, watch_key, watched_at):
            return False

        # Complete the unwatched movie instead of adding another one
        if existing_movie.end_date is None and helpers.advance_status(
            existing_movie,
            Status.COMPLETED.value,
        ):
            existing_movie.start_date = watched_at
            existing_movie.end_date = watched_at
            existing_movie.progress = 1
            existing_movie.progressed_at = timezone.now()
            self.to_update[MediaTypes.MOVIE.value][existing_movie.pk] = existing_movie
            return False

        return True

    def _sync_existing_anime(self, existing_anime, anime, anime_status):
        """Raise the progress of an existing anime and fill what it is missing."""
        self._sync_existing(
            MediaTypes.ANIME.value,
            existing_anime,
            anime,
            anime_status,
        )

        progress = anime["watched_episodes_count"] or 0
        if progress > existing_anime.progress:
            existing_anime.progress = progress
            existing_anime.progressed_at = timezone.now()
            self.to_update[MediaTypes.ANIME.value][existing_anime.pk] = existing_anime

        if existing_anime.pk not in self.to_update[MediaTypes.ANIME.value]:
            return

        if existing_anime.end_date is None:
            existing_anime.end_date = self._get_end_date(
                existing_anime.status,
                anime.get("last_watched_at"),
            )

    def _process_anime_list(self, anime_list):
        """Process anime list from Simkl."""
        logger.info("Processing anime")
        existing_anime_ids = set()

        for anime in anime_list:
            try:
                title = anime["show"]["title"]
                logger.debug("Processing %s", title)

                try:
                    mal_id = anime["show"]["ids"]["mal"]
                except KeyError:
                    self.warnings.append(f"{title}: No MyAnimeList ID found")
                    continue

                if mal_id in existing_anime_ids:
                    self.warnings.append(
                        f"{title} ({mal_id}) already present in the import list",
                    )
                    continue

                anime_status = self._get_status(anime["status"])

                existing_anime = self._get_existing(
                    MediaTypes.ANIME.value,
                    Sources.MAL.value,
                    mal_id,
                )
                if existing_anime:
                    self._sync_existing_anime(existing_anime, anime, anime_status)
                    continue

                # Check if we should process this entry based on mode
                if not helpers.should_process_media(
                    self.existing_media,
                    self.to_delete,
                    MediaTypes.ANIME.value,
                    Sources.MAL.value,
                    str(mal_id),
                    self.mode,
                ):
                    continue

                try:
                    metadata = app.providers.mal.anime(mal_id)
                except services.ProviderAPIError as error:
                    if error.status_code == requests.codes.not_found:
                        self.warnings.append(
                            f"{title}: not found in {Sources.MAL.label} "
                            f"with ID {mal_id}.",
                        )
                        continue
                    raise

                anime_item, _ = app.models.Item.objects.get_or_create(
                    media_id=mal_id,
                    source=Sources.MAL.value,
                    media_type=MediaTypes.ANIME.value,
                    defaults={
                        "title": metadata["title"],
                        "image": metadata["image"],
                    },
                )

                anime_instance = app.models.Anime(
                    item=anime_item,
                    user=self.user,
                    status=anime_status,
                    score=anime["user_rating"],
                    progress=anime["watched_episodes_count"],
                    start_date=self._get_start_date(anime),
                    end_date=self._get_end_date(
                        anime_status,
                        anime.get("last_watched_at"),
                    ),
                    notes=self._get_notes(anime),
                )
                anime_instance._history_date = self._get_history_date(anime)

                self.bulk_media[MediaTypes.ANIME.value].append(anime_instance)
                existing_anime_ids.add(mal_id)

            except Exception as error:
                msg = f"Error processing entry: {anime}"
                raise MediaImportUnexpectedError(msg) from error

        logger.info("Processed %d anime", len(anime_list))

    def _get_status(self, status):
        """Map Simkl status to internal status."""
        status_mapping = {
            "completed": Status.COMPLETED.value,
            "watching": Status.IN_PROGRESS.value,
            "plantowatch": Status.PLANNING.value,
            "hold": Status.PAUSED.value,
            "dropped": Status.DROPPED.value,
        }

        return status_mapping.get(status, Status.IN_PROGRESS.value)

    def _get_notes(self, entry):
        """Get the notes from the memo of the entry."""
        return entry["memo"]["text"] if entry["memo"] != {} else ""

    def _get_date(self, date_str):
        """Convert the date from Simkl to a date object, stripping seconds."""
        if date_str:
            return parse_datetime(date_str).replace(second=0, microsecond=0)
        return None

    def _get_start_date(self, anime):
        """Get the start date based on earliest watched episode."""
        if "seasons" in anime:
            episodes = anime["seasons"][0]["episodes"]
            current_min_date = None

            for episode in episodes:
                date = self._get_date(episode.get("watched_at"))
                if date is not None and (
                    current_min_date is None or date < current_min_date
                ):
                    current_min_date = date

            return current_min_date

        return None

    def _get_end_date(self, anime_status, last_watched_at):
        """Get the end date based on the anime status."""
        if anime_status == Status.COMPLETED.value:
            return self._get_date(last_watched_at)
        return None

    def _get_history_date(self, entry):
        """Get the history date from the entry."""
        if entry.get("last_watched_at"):
            return parse_datetime(entry.get("last_watched_at"))

        if entry.get("added_to_watchlist_at"):
            return parse_datetime(entry.get("added_to_watchlist_at"))

        return timezone.now()
