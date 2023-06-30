from decouple import config
import requests
import logging
import asyncio

from app.models import TV, Movie
from app.utils import helpers

TMDB_API = config("TMDB_API", default="")
logger = logging.getLogger(__name__)


def auth_url():
    """
    Returns the URL to authenticate with TMDB.
    """
    auth_url = (
        f"https://api.themoviedb.org/3/authentication/token/new?api_key={TMDB_API}"
    )
    auth_request = requests.get(auth_url).json()
    logger.info(
        f"Authentication URL: https://www.themoviedb.org/authenticate/{auth_request['request_token']}"
    )
    return f"https://www.themoviedb.org/authenticate/{auth_request['request_token']}"


def get_session_id(request_token):
    session_url = (
        f"https://api.themoviedb.org/3/authentication/session/new?api_key={TMDB_API}"
    )
    data = {"request_token": request_token}
    session_request = requests.post(session_url, data=data).json()
    if session_request["success"]:
        return session_request["session_id"]


def import_tmdb(user, request_token):
    """
    Imports:
        - Rated movies
        - Watchlist movies
        - Rated TV shows
    It currently doesn't import watchlist TV shows because current tv models
    doesn't have a status field, shows status are tracked per season.

    It doesn't track dates because TMDB API doesn't provide that information.
    """
    session_id = get_session_id(request_token)
    print(session_id)
    tv_images_to_download = []
    movies_images_to_download = []

    # MOVIES
    movies_rated_url = f"https://api.themoviedb.org/3/account/{user}/rated/movies?api_key={TMDB_API}&session_id={session_id}"
    movies_images, bulk_movies = process_media_list(
        movies_rated_url, "movie", "Completed", user
    )
    movies_images_to_download.extend(movies_images)

    movies_watchlist_url = f"https://api.themoviedb.org/3/account/{user}/watchlist/movies?api_key={TMDB_API}&session_id={session_id}"
    movies_images, bulk_watchlist_movies = process_media_list(
        movies_watchlist_url, "movie", "Planning", user
    )
    movies_images_to_download.extend(movies_images)
    bulk_movies.extend(bulk_watchlist_movies)

    # TVs
    tv_rated_url = f"https://api.themoviedb.org/3/account/{user}/rated/tv?api_key={TMDB_API}&session_id={session_id}"
    tv_images, bulk_tv = process_media_list(
        tv_rated_url, "tv", "Completed", user
    )
    tv_images_to_download.extend(tv_images)

    asyncio.run(helpers.images_downloader(tv_images_to_download, "tv"))
    asyncio.run(helpers.images_downloader(movies_images_to_download, "movie"))

    TV.objects.bulk_create(bulk_tv)
    Movie.objects.bulk_create(bulk_movies)


def process_media_list(url, media_type, status, user):
    """
    Processes rated and watchlist media lists and adds them to the database.
    Returns a list of images to download.
    """
    images_to_download = []
    media_mapping = helpers.media_type_mapper(media_type)
    data = requests.get(url).json()
    bulk_media = []

    if "success" in data and not data["success"]:
        logger.error(f"Error: {data['status_message']}")
        return images_to_download, bulk_media

    next_page = 2

    while next_page <= data["total_pages"]:
        next_url = f"{url}&page={next_page}"
        next_data = requests.get(next_url).json()
        data["results"].extend(next_data["results"])
        next_page += 1

    for media in data["results"]:
        if media_type == "tv":
            media["title"] = media["name"]

        if media_mapping["model"].objects.filter(
            media_id=media["id"], user=user
        ).exists():
            logger.info(
                f"{media_type.capitalize()}: {media['title']} ({media['id']}) already exists, skipping..."
            )
        else:
            if media["poster_path"]:
                # poster_path e.g: "/aFmqXViWzIKm.jpg", remove the first slash
                image = media["poster_path"][1:]
                # format: movie-aFmqXViWzIKm.jpg
                image = media_type + "-" + image
                images_to_download.append(
                    f"https://image.tmdb.org/t/p/w500{media['poster_path']}"
                )
            else:
                image = "none.svg"

            media_params = {
                "media_id": media["id"],
                "title": media["title"],
                "image": image,
                "score": media["rating"] if "rating" in media else None,
                "user": user,
                "notes": "",
            }
            if media_type == "movie":
                media_params["status"] = status
                media_params["end_date"] = None

            bulk_media.append(
                media_mapping["model"](**media_params)
            )

            logger.info(
                f"{media_type.capitalize()}: {media['title']} ({media['id']}) added to import list."
            )

    return images_to_download, bulk_media
