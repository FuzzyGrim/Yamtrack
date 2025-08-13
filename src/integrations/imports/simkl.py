import logging
from collections import defaultdict

import requests
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

import app
from app.models import MediaTypes, Sources, Status
from app.providers import services
from integrations.imports import helpers
from integrations.imports.helpers import MediaImportError, MediaImportUnexpectedError

logger = logging.getLogger(__name__)


def get_token(request):
    """View for getting the SIMKL OAuth2 token."""
    domain = request.get_host()
    scheme = request.scheme
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
        "redirect_uri": f"{scheme}://{domain}",
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

                # Check if we should process this entry based on mode
                if not helpers.should_process_media(
                    self.existing_media,
                    self.to_delete,
                    MediaTypes.TV.value,
                    Sources.TMDB.value,
                    str(tmdb_id),
                    self.mode,
                ):
                    continue

                tv_status = self._get_status(tv["status"])

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

                tv_instance = app.models.TV(
                    item=tv_item,
                    user=self.user,
                    status=tv_status,
                    score=tv["user_rating"],
                    notes=tv["memo"]["text"] if tv["memo"] != {} else "",
                )
                tv_instance._history_date = self._get_history_date(tv)
                self.bulk_media[MediaTypes.TV.value].append(tv_instance)
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

    def _process_seasons_and_episodes(self, tv, tv_instance, metadata):
        """Process seasons and episodes for a TV show."""
        tmdb_id = tv["show"]["ids"]["tmdb"]

        for season in tv["seasons"]:
            season_number = season["number"]
            episodes = season["episodes"]
            season_metadata = metadata[f"season/{season_number}"]

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
                season_status = tv_instance.status

            season_instance = app.models.Season(
                item=season_item,
                user=self.user,
                related_tv=tv_instance,
                status=season_status,
            )
            season_instance._history_date = self._get_history_date(tv)
            self.bulk_media[MediaTypes.SEASON.value].append(season_instance)

            # Process episodes
            for episode in episodes:
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

                # Check if we should process this entry based on mode
                if not helpers.should_process_media(
                    self.existing_media,
                    self.to_delete,
                    MediaTypes.MOVIE.value,
                    Sources.TMDB.value,
                    str(tmdb_id),
                    self.mode,
                ):
                    continue

                movie_status = self._get_status(movie["status"])

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
                    start_date=self._get_date(movie.get("last_watched_at")),
                    end_date=self._get_date(movie.get("last_watched_at")),
                    notes=movie["memo"]["text"] if movie["memo"] != {} else "",
                )
                movie_instance._history_date = self._get_history_date(movie)
                self.bulk_media[MediaTypes.MOVIE.value].append(movie_instance)
                existing_movie_ids.add(tmdb_id)

            except Exception as error:
                msg = f"Error processing entry: {movie}"
                raise MediaImportUnexpectedError(msg) from error

        logger.info("Processed %d movies", len(movie_list))

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

                anime_status = self._get_status(anime["status"])

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
                    notes=anime["memo"]["text"] if anime["memo"] != {} else "",
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

    def _get_date(self, date_str):
        """Convert the date from Simkl to a date object."""
        if date_str:
            return parse_datetime(date_str)
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
