from django.core.cache import cache
from decouple import config
import requests

TMDB_API = config("TMDB_API", default=None)
MAL_API = config("MAL_API", default=None)


def mal(media_type, media_id):
    cache_key = media_type + str(media_id)
    response = cache.get(cache_key)
    if response is None:
        url = f"https://api.myanimelist.net/v2/{media_type}/{media_id}?fields=title,main_picture,start_date,synopsis,status,genres,num_episodes,num_chapters,average_episode_duration,related_anime,related_manga,recommendations"

        response = requests.get(url, headers={"X-MAL-CLIENT-ID": MAL_API}).json()

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
        else:
            response["runtime"] = "Unknown"

        if "genres" not in response:
            response["genres"] = [{"name": "Unknown"}]

        for key in ("related_anime", "related_manga", "recommendations"):
            items = response.get(key)
            for item in response.get(key):
                if "main_picture" in item["node"]:
                    item["node"]["image"] = item["node"]["main_picture"]["large"]
                else:
                    item["node"]["image"] = "none.svg"
                item.update(item["node"])

            response[key] = items

        # cache for 6 hours
        cache.set(cache_key, response, 21600)

    return response


def tmdb(media_type, media_id):
    cache_key = media_type + str(media_id)
    response = cache.get(cache_key)
    if response is None:
        url = f"https://api.themoviedb.org/3/{media_type}/{media_id}?api_key={TMDB_API}&append_to_response=recommendations"

        response = requests.get(url).json()

        response["media_type"] = media_type
        response["media_id"] = response["id"]

        # when specific data is not available
        # tmdb will either not return the key or return an empty value/string

        if response["poster_path"]:
            response["image"] = f"https://image.tmdb.org/t/p/w500{response['poster_path']}"
        else:
            response["image"] = "none.svg"

        # tv shows have name instead of title
        if "name" in response:
            response["title"] = response["name"]

        # movies have release_date
        if "release_date" in response and response["release_date"] != "":
            response["start_date"] = response["release_date"]
        # tv shows have first_air_date
        elif "first_air_date" in response and response["first_air_date"] != "":
            response["start_date"] = response["first_air_date"]
        else:
            response["start_date"] = "Unknown"

        if response["overview"] == "":
            response["synopsis"] = "No synopsis available."
        else:
            response["synopsis"] = response["overview"]

        # movies uses runtime
        # tv shows episode runtime are shown in last_episode_to_air
        duration = response.get("runtime") or response.get("last_episode_to_air", {}).get("runtime")
        if duration:
            hours, minutes = divmod(int(duration), 60)
            response["runtime"] = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"

        response["num_episodes"] = response.get("number_of_episodes", 1)

        if not response["genres"]:
            response["genres"] = [{"name": "Unknown"}]

        response["recommendations"] = response["recommendations"]["results"][:10]
        for recommendation in response["recommendations"]:
            if "name" in recommendation:
                recommendation["title"] = recommendation["name"]
            if "poster_path" in recommendation:
                recommendation[
                    "image"
                ] = f"https://image.tmdb.org/t/p/w500{recommendation['poster_path']}"
            else:
                recommendation["image"] = "none.svg"

        # cache for 6 hours
        cache.set(cache_key, response, 21600)

    return response
