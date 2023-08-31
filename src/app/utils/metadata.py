import requests
from decouple import config
from django.core.cache import cache

TMDB_API = config("TMDB_API", default=None)
MAL_API = config("MAL_API", default=None)


def get_media_metadata(media_type: str, media_id: str) -> dict:
    """Return the metadata for the selected media."""

    if media_type in ("anime", "manga"):
        media_metadata = anime_manga(media_type, media_id)
    elif media_type == "tv":
        media_metadata = tv(media_id)
    elif media_type == "movie":
        media_metadata = movie(media_id)

    return media_metadata


def anime_manga(media_type: str, media_id: str) -> dict:
    """Return the metadata for the selected anime or manga from MyAnimeList."""

    cache_key = f"{media_type}_{media_id}"
    response = cache.get(cache_key)
    if response is None:
        url = f"https://api.myanimelist.net/v2/{media_type}/{media_id}?fields=title,main_picture,media_type,start_date,synopsis,status,genres,num_episodes,num_chapters,average_episode_duration,related_anime,related_manga,recommendations"
        response = requests.get(
            url, headers={"X-MAL-CLIENT-ID": MAL_API}, timeout=5
        ).json()

        if response["media_type"] == "tv":
            response["media_type"] = "anime"
        response["original_type"] = response["media_type"].replace("_", " ").title()
        response["media_type"] = media_type
        response["media_id"] = response["id"]

        if "main_picture" in response:
            response["image"] = response["main_picture"]["large"]
        else:
            response["image"] = "none.svg"

        # Map status to human-readable values
        status_map = {
            "finished_airing": "Finished",
            "currently_airing": "Airing",
            "not_yet_aired": "Upcoming",
            "finished": "Finished",
            "currently_publishing": "Publishing",
            "not_yet_published": "Upcoming",
            "on_hiatus": "On Hiatus",
        }
        response["status"] = status_map.get(response.get("status"), "Unknown")

        if response["synopsis"] == "":
            response["synopsis"] = "No synopsis available."

        if "num_chapters" in response:
            response["num_episodes"] = response["num_chapters"]
        elif "num_episodes" not in response:
            response["num_episodes"] = "Unknown"

        # Convert average_episode_duration to hours and minutes
        if (
            "average_episode_duration" in response
            and response["average_episode_duration"] != 0
        ):
            duration = response["average_episode_duration"]
            # duration are in seconds
            hours, minutes = divmod(int(duration / 60), 60)
            response["runtime"] = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
        # if no duration is available, set it to unknown only for anime
        elif media_type == "anime":
            response["runtime"] = "Unknown"

        if "genres" not in response:
            response["genres"] = [{"name": "Unknown"}]

        for key in ("related_anime", "related_manga", "recommendations"):
            items = response.get(key)
            for item in items:
                if "main_picture" in item["node"]:
                    item["node"]["image"] = item["node"]["main_picture"]["large"]
                else:
                    item["node"]["image"] = "none.svg"
                item.update(item["node"])

            response[key] = items

        # cache for 6 hours
        cache.set(cache_key, response, 21600)

    return response


def tv(media_id: str) -> dict:
    """Return the metadata for the selected tv show from The Movie Database."""

    cache_key = f"tv_{media_id}"
    response = cache.get(cache_key)
    if response is None:
        url = f"https://api.themoviedb.org/3/tv/{media_id}?api_key={TMDB_API}&append_to_response=recommendations"

        response = requests.get(url, timeout=5).json()

        response["original_type"] = "TV"
        response["media_type"] = "tv"
        response["media_id"] = response["id"]

        # when specific data is not available
        # tmdb will either not return the key or return an empty value/string

        if response["poster_path"]:
            response[
                "image"
            ] = f"https://image.tmdb.org/t/p/w500{response['poster_path']}"
        else:
            response["image"] = "none.svg"

        # tv shows have name instead of title
        response["title"] = response["name"]

        # tv shows have start_date inside first_air_date
        if response["first_air_date"] != "":
            response["start_date"] = response["first_air_date"]
        else:
            response["start_date"] = "Unknown"

        if response["overview"] == "":
            response["synopsis"] = "No synopsis available."
        else:
            response["synopsis"] = response["overview"]

        # tv shows episode runtime are shown in last_episode_to_air
        duration = response.get("last_episode_to_air", {}).get("runtime")
        if duration:
            hours, minutes = divmod(int(duration), 60)
            response["runtime"] = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
        else:
            response["runtime"] = "Unknown"

        response["num_episodes"] = response.get("number_of_episodes", 1)

        if not response["genres"]:
            response["genres"] = [{"name": "Unknown"}]

        response["recommendations"] = response["recommendations"]["results"][:15]

        for key in ("seasons", "recommendations"):
            items = response.get(key)
            for item in items:
                if item["poster_path"]:
                    item[
                        "image"
                    ] = f"https://image.tmdb.org/t/p/w500{item['poster_path']}"
                else:
                    item["image"] = "none.svg"

                item["title"] = item["name"]

                if key == "seasons":
                    item["id"] = response["id"]

        # cache for 6 hours
        cache.set(cache_key, response, 21600)

    return response


def movie(media_id: str) -> dict:
    """Return the metadata for the selected movie from The Movie Database."""

    cache_key = f"movie_{media_id}"
    response = cache.get(cache_key)
    if response is None:
        url = f"https://api.themoviedb.org/3/movie/{media_id}?api_key={TMDB_API}&append_to_response=recommendations"

        response = requests.get(url, timeout=5).json()

        response["original_type"] = "Movie"
        response["media_type"] = "movie"
        response["media_id"] = response["id"]

        # when specific data is not available
        # tmdb will either not return the key or return an empty value/string

        if response["poster_path"]:
            response[
                "image"
            ] = f"https://image.tmdb.org/t/p/w500{response['poster_path']}"
        else:
            response["image"] = "none.svg"

        if "release_date" in response and response["release_date"] != "":
            response["start_date"] = response["release_date"]
        else:
            response["start_date"] = "Unknown"

        if response["overview"] == "":
            response["synopsis"] = "No synopsis available."
        else:
            response["synopsis"] = response["overview"]

        # movies uses runtime in minutes, convert to hours and minutes
        duration = response.get("runtime")
        if duration:
            hours, minutes = divmod(int(duration), 60)
            response["runtime"] = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
        else:
            response["runtime"] = "Unknown"

        if not response["genres"]:
            response["genres"] = [{"name": "Unknown"}]

        response["recommendations"] = response["recommendations"]["results"][:15]

        for recommendation in response["recommendations"]:
            if recommendation["poster_path"]:
                recommendation[
                    "image"
                ] = f"https://image.tmdb.org/t/p/w500{recommendation['poster_path']}"
            else:
                recommendation["image"] = "none.svg"

        # cache for 6 hours
        cache.set(cache_key, response, 21600)

    return response


def season(tv_id: str, season_number: int) -> dict:
    """Return the metadata for the selected season from The Movie Database."""

    cache_key = f"season_{tv_id}_{season_number}"
    response = cache.get(cache_key)
    if response is None:
        url = f"https://api.themoviedb.org/3/tv/{tv_id}/season/{season_number}?api_key={TMDB_API}"
        response = requests.get(url, timeout=5).json()

        if response["poster_path"]:
            response[
                "image"
            ] = f"https://image.tmdb.org/t/p/w500{response['poster_path']}"
        else:
            response["image"] = "none.svg"

        if response["air_date"] != "":
            response["start_date"] = response["air_date"]
        else:
            response["start_date"] = "Unknown"

        if response["overview"] == "":
            response["synopsis"] = "No synopsis available."
        else:
            response["synopsis"] = response["overview"]

        cache.set(cache_key, response, 21600)

    return response
