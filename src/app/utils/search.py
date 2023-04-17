from decouple import config
import requests


TMDB_API = config("TMDB_API", default=None)
MAL_API = config("MAL_API", default=None)


def tmdb(media_type, query):
    url = f"https://api.themoviedb.org/3/search/{media_type}?api_key={TMDB_API}&query={query}"
    response = requests.get(url).json()["results"]
    for media in response:
        media["media_id"] = media["id"]
        media["original_type"] = media_type
        media["media_type"] = media_type
        if "name" in media:
            media["title"] = media["name"]
        if media["poster_path"]:
            media["image"] = f"https://image.tmdb.org/t/p/w500{media['poster_path']}"
        else:
            media["image"] = "none.svg"
    return response


def mal(media_type, query):
    url = f"https://api.myanimelist.net/v2/{media_type}?q={query}&nsfw=true&fields=media_type"
    response = requests.get(url, headers={"X-MAL-CLIENT-ID": MAL_API}).json()
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
                media["image"] = "none.svg"
            # remove node layer
            media.update(media.pop("node"))
    return response
