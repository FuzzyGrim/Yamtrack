import logging

from decouple import config
from django.conf import settings

from app.utils import helpers

logger = logging.getLogger(__name__)

TMDB_API = config("YAMTRACK_TMDB_API", default=None)
MAL_API = config("YAMTRACK_MAL_API", default=None)


def tmdb(media_type: str, query: str) -> list:
    """Search for media on TMDB."""

    url = f"https://api.themoviedb.org/3/search/{media_type}?api_key={TMDB_API}&query={query}"
    response = helpers.api_request(url, "GET")

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
    response = helpers.api_request(url, "GET", headers={"X-MAL-CLIENT-ID": MAL_API})

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
