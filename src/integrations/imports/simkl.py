import logging

import requests
from django.apps import apps
from django.conf import settings
from django.utils.dateparse import parse_datetime

import app
from app.models import Media, MediaTypes, Sources
from app.providers import services
from integrations import helpers
from integrations.helpers import MediaImportError, MediaImportUnexpectedError

logger = logging.getLogger(__name__)

SIMKL_API_BASE_URL = "https://api.simkl.com"


def get_token(request):
    """View for getting the SIMKL OAuth2 token."""
    domain = request.get_host()
    scheme = request.scheme
    code = request.GET["code"]
    url = f"{SIMKL_API_BASE_URL}/oauth/token"

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
        request = app.providers.services.api_request(
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

    return request["access_token"]


def importer(token, user, mode):
    """Import tv shows, movies and anime from SIMKL."""
    logger.info("Starting SIMKL import with mode %s", mode)

    data = get_user_list(token)

    if not data:
        return 0, 0, 0, ""

    bulk_media = {
        MediaTypes.TV.value: [],
        MediaTypes.SEASON.value: [],
        MediaTypes.EPISODE.value: [],
        MediaTypes.MOVIE.value: [],
        MediaTypes.ANIME.value: [],
    }
    warnings = []

    # Process all media types
    if "shows" in data:
        process_tv_list(data["shows"], user, bulk_media, warnings)
    if "movies" in data:
        process_movie_list(data["movies"], user, bulk_media, warnings)
    if "anime" in data:
        process_anime_list(data["anime"], user, bulk_media, warnings)

    # Import using bulk operations
    imported_counts = {}
    for media_type, bulk_list in bulk_media.items():
        if bulk_list:
            if media_type == MediaTypes.SEASON.value:
                helpers.update_season_references(bulk_list, user)
            elif media_type == MediaTypes.EPISODE.value:
                helpers.update_episode_references(bulk_list, user)

            imported_counts[media_type] = helpers.bulk_chunk_import(
                bulk_list,
                apps.get_model(app_label="app", model_name=media_type),
                user,
                mode,
            )

    return imported_counts, "\n".join(warnings)


def get_user_list(token):
    """Get the user's list from SIMKL."""
    url = f"{SIMKL_API_BASE_URL}/sync/all-items/"
    headers = {
        "Authorization": f"Bearer: {token}",
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


def process_tv_list(tv_list, user, bulk_media, warnings):
    """Process TV list from SIMKL and prepare for bulk creation."""
    logger.info("Processing tv shows")
    existing_tv_ids = set()

    for tv in tv_list:
        try:
            title = tv["show"]["title"]
            logger.debug("Processing %s", title)

            try:
                tmdb_id = tv["show"]["ids"]["tmdb"]
            except KeyError:
                warnings.append(f"{title}: No TMDB ID found")
                continue

            if tmdb_id in existing_tv_ids:
                warnings.append(
                    f"{title} ({tmdb_id}) already present in the import list",
                )
                continue

            tv_status = get_status(tv["status"])

            try:
                season_numbers = [season["number"] for season in tv["seasons"]]
            except KeyError:
                season_numbers = []

            try:
                metadata = app.providers.tmdb.tv_with_seasons(tmdb_id, season_numbers)
            except services.ProviderAPIError as error:
                if error.status_code == requests.codes.not_found:
                    warnings.append(
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
                user=user,
                status=tv_status,
                score=tv["user_rating"],
                notes=tv["memo"]["text"] if tv["memo"] != {} else "",
            )
            bulk_media[MediaTypes.TV.value].append(tv_instance)
            existing_tv_ids.add(tmdb_id)

            if season_numbers:
                # Process seasons and episodes
                process_seasons_and_episodes(
                    tv,
                    tv_instance,
                    metadata,
                    season_numbers,
                    user,
                    bulk_media,
                )

        except Exception as error:
            msg = f"Error processing entry: {tv}"
            raise MediaImportUnexpectedError(msg) from error

    logger.info("Processed %d tv shows", len(tv_list))


def process_seasons_and_episodes(
    tv,
    tv_instance,
    metadata,
    season_numbers,
    user,
    bulk_media,
):
    """Process seasons and episodes for bulk creation."""
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

        # Prepare Season instance for bulk creation
        season_status = (
            Media.Status.COMPLETED.value
            if season_number != season_numbers[-1]
            else tv_instance.status
        )

        season_instance = app.models.Season(
            item=season_item,
            user=user,
            related_tv=tv_instance,
            status=season_status,
        )
        bulk_media[MediaTypes.SEASON.value].append(season_instance)

        # Process episodes
        for episode in episodes:
            ep_img = get_episode_image(episode, season_number, metadata)
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
                end_date=episode["watched_at"],
            )
            bulk_media[MediaTypes.EPISODE.value].append(episode_instance)


def get_episode_image(episode, season_number, metadata):
    """Get the image for the episode."""
    for episode_metadata in metadata[f"season/{season_number}"]["episodes"]:
        if episode_metadata["episode_number"] == episode["number"]:
            return f"https://image.tmdb.org/t/p/w500{episode_metadata['still_path']}"
    return settings.IMG_NONE


def process_movie_list(movie_list, user, bulk_media, warnings):
    """Process movie list from SIMKL and prepare for bulk creation."""
    logger.info("Processing movies")
    existing_movie_ids = set()

    for movie in movie_list:
        try:
            title = movie["movie"]["title"]
            logger.debug("Processing %s", title)

            try:
                tmdb_id = movie["movie"]["ids"]["tmdb"]
            except KeyError:
                warnings.append(f"{title}: No TMDB ID found")
                continue

            if tmdb_id in existing_movie_ids:
                warnings.append(
                    f"{title} ({tmdb_id}) already present in the import list",
                )
                continue

            movie_status = get_status(movie["status"])

            try:
                metadata = app.providers.tmdb.movie(tmdb_id)
            except services.ProviderAPIError as error:
                if error.status_code == requests.codes.not_found:
                    warnings.append(
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
                user=user,
                status=movie_status,
                score=movie["user_rating"],
                start_date=get_date(movie["last_watched_at"]),
                end_date=get_date(movie["last_watched_at"]),
                notes=movie["memo"]["text"] if movie["memo"] != {} else "",
            )
            bulk_media[MediaTypes.MOVIE.value].append(movie_instance)
            existing_movie_ids.add(tmdb_id)

        except Exception as error:
            msg = f"Error processing entry: {movie}"
            raise MediaImportUnexpectedError(msg) from error

    logger.info("Processed %d movies", len(movie_list))


def process_anime_list(anime_list, user, bulk_media, warnings):
    """Process anime list from SIMKL and prepare for bulk creation."""
    logger.info("Processing anime")
    existing_anime_ids = set()

    for anime in anime_list:
        try:
            title = anime["show"]["title"]
            logger.debug("Processing %s", title)

            try:
                mal_id = anime["show"]["ids"]["mal"]
            except KeyError:
                warnings.append(f"{title}: No MyAnimeList ID found")
                continue

            if mal_id in existing_anime_ids:
                warnings.append(
                    f"{title} ({mal_id}) already present in the import list",
                )
                continue

            anime_status = get_status(anime["status"])

            try:
                metadata = app.providers.mal.anime(mal_id)
            except services.ProviderAPIError as error:
                if error.status_code == requests.codes.not_found:
                    warnings.append(
                        f"{title}: not found in {Sources.MAL.label} with ID {mal_id}.",
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
                user=user,
                status=anime_status,
                score=anime["user_rating"],
                progress=anime["watched_episodes_count"],
                start_date=get_start_date(anime),
                end_date=get_end_date(anime_status, anime["last_watched_at"]),
                notes=anime["memo"]["text"] if anime["memo"] != {} else "",
            )
            bulk_media[MediaTypes.ANIME.value].append(anime_instance)
            existing_anime_ids.add(mal_id)

        except Exception as error:
            msg = f"Error processing entry: {anime}"
            raise MediaImportUnexpectedError(msg) from error

    logger.info("Processed %d anime", len(anime_list))


def get_status(status):
    """Map SIMKL status to internal status."""
    status_mapping = {
        "completed": Media.Status.COMPLETED.value,
        "watching": Media.Status.IN_PROGRESS.value,
        "plantowatch": Media.Status.PLANNING.value,
        "hold": Media.Status.PAUSED.value,
        "dropped": Media.Status.DROPPED.value,
    }

    return status_mapping.get(status, Media.Status.IN_PROGRESS.value)


def get_date(date_str):
    """Convert the date from Trakt to a date object."""
    if date_str:
        return parse_datetime(date_str)
    return None


def get_start_date(anime):
    """Get the start date based on earliest watched episode."""
    if "seasons" in anime:
        episodes = anime["seasons"][0]["episodes"]
        dates = [get_date(episode["watched_at"]) for episode in episodes]
        return min(dates) if dates else None

    return None


def get_end_date(anime_status, last_watched_at):
    """Get the end date based on the anime status."""
    if anime_status == Media.Status.COMPLETED.value:
        return get_date(last_watched_at)
    return None
