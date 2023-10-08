import logging

import requests
from decouple import config
from django.conf import settings

logger = logging.getLogger(__name__)

TMDB_API = config("TMDB_API", default=None)
MAL_API = config("MAL_API", default=None)


def tmdb(media_type: str, query: str) -> list:
    """Search for media on TMDB."""

    url = f"https://api.themoviedb.org/3/search/{media_type}?api_key={TMDB_API}&query={query}"
    response = requests.get(url, timeout=5).json()

    # when api key is invalid
    if "success" in response and not response["success"]:
        logger.error("TMDB: %s", response["status_message"])
        return []

    response = response["results"]
    for media in response:
        media["media_id"] = media["id"]
        media["original_type"] = media_type
        media["media_type"] = media_type
        if "name" in media:
            media["title"] = media["name"]
        if media["poster_path"]:
            media["image"] = f"https://image.tmdb.org/t/p/w500{media['poster_path']}"
        else:
            media["image"] = settings.IMG_NONE
    return response


def mal(media_type: str, query: str) -> list:
    """Search for media on MyAnimeList."""

    url = f"https://api.myanimelist.net/v2/{media_type}?q={query}&nsfw=true&fields=media_type"
    response = requests.get(url, headers={"X-MAL-CLIENT-ID": MAL_API}, timeout=5).json()

    if "error" in response:
        if response["error"] == "forbidden":
            logger.error(
                "MAL: %s, probably no API key provided",
                response["error"].title(),
            )
        elif response["error"] == "bad_request":
            if response["message"] == "invalid q":
                logger.error("MAL: Invalid query")
            elif response["message"] == "Invalid client id":
                logger.error("MAL: Wrong API key")
        return []

    if "data" in response:
        response = response["data"]
        for media in response:
            if media["node"]["media_type"] == "tv":
                media["node"]["media_type"] = "anime"
            media["node"]["original_type"] = (
                media["node"]["media_type"].replace("_", " ").title()
            )
            media["node"]["media_type"] = media_type
            media["node"]["media_id"] = media["node"]["id"]

            if "main_picture" in media["node"]:
                media["image"] = media["node"]["main_picture"]["large"]
            else:
                media["image"] = settings.IMG_NONE
            # remove node layer
            media.update(media.pop("node"))
    return response
