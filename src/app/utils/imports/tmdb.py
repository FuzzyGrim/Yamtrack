from decouple import config
import requests
import logging
import asyncio

from app.models import Media
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


def import_tmdb(user, request_token):
    session_id = get_session_id(request_token)
    tv_images_to_download = []
    movies_images_to_download = []
    bulk_add_media = []

    movies_rated_url = f"https://api.themoviedb.org/3/account/{user}/rated/movies?api_key={TMDB_API}&session_id={session_id}"
    movies_images, bulk_add_media = process_media_list(
        movies_rated_url, "movie", "Completed", user, bulk_add_media
    )
    movies_images_to_download.extend(movies_images)

    movies_watchlist_url = f"https://api.themoviedb.org/3/account/{user}/watchlist/movies?api_key={TMDB_API}&session_id={session_id}"
    movies_images, bulk_add_media = process_media_list(
        movies_watchlist_url, "movie", "Planning", user, bulk_add_media
    )
    movies_images_to_download.extend(movies_images)

    tv_rated_url = f"https://api.themoviedb.org/3/account/{user}/rated/tv?api_key={TMDB_API}&session_id={session_id}"
    tv_images, bulk_add_media = process_media_list(
        tv_rated_url, "tv", "Completed", user, bulk_add_media
    )
    tv_images_to_download.extend(tv_images)

    tv_watchlist_url = f"https://api.themoviedb.org/3/account/{user}/watchlist/tv?api_key={TMDB_API}&session_id={session_id}"
    tv_images, bulk_add_media = process_media_list(
        tv_watchlist_url, "tv", "Planning", user, bulk_add_media
    )
    tv_images_to_download.extend(tv_images)

    asyncio.run(helpers.images_downloader(tv_images_to_download, "tv"))
    asyncio.run(helpers.images_downloader(movies_images_to_download, "movie"))

    Media.objects.bulk_create(bulk_add_media)


def get_session_id(request_token):
    session_url = (
        f"https://api.themoviedb.org/3/authentication/session/new?api_key={TMDB_API}"
    )
    data = {"request_token": request_token}
    session_request = requests.post(session_url, data=data).json()
    if session_request["success"]:
        return session_request["session_id"]


def process_media_list(url, media_type, status, user, bulk_add_media):
    """
    Processes rated and watchlist media lists and adds them to the database.
    Returns a list of images to download.
    """
    images_to_download = []
    data = requests.get(url).json()

    if "success" in data and not data["success"]:
        logger.error(f"Error: {data['status_message']}")
        return images_to_download, bulk_add_media

    next_page = 2

    while next_page <= data["total_pages"]:
        next_url = f"{url}&page={next_page}"
        next_data = requests.get(next_url).json()
        data["results"].extend(next_data["results"])
        next_page += 1

    for media in data["results"]:
        if Media.objects.filter(
            media_id=media["id"], media_type=media_type, user=user
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

            if "name" in media:
                media["title"] = media["name"]

            bulk_add_media.append(
                Media(
                    media_id=media["id"],
                    title=media["title"],
                    media_type=media_type,
                    score=media["rating"] if "rating" in media else 0,
                    progress=0,
                    status=status,
                    user=user,
                    image=image,
                    start_date=None,
                    end_date=None,
                )
            )
            logger.info(
                f"{media_type.capitalize()}: {media['title']} ({media['id']}) added."
            )

    return images_to_download, bulk_add_media
