from django.core.cache import cache

from decouple import config
import requests

from app.models import Media


TMDB_API = config("TMDB_API", default=None)
MAL_API = config("MAL_API", default=None)


def mal_edit(request, media_type, media_id):
    cache_key = media_type + str(media_id)
    response = cache.get(cache_key)
    if response is None:
        url = f"https://api.myanimelist.net/v2/{media_type}/{media_id}?fields=title,main_picture,start_date,end_date,synopsis,mean,rank,popularity,updated_at,media_type,status,genres,num_episodes,num_chapters,broadcast,source,average_episode_duration,rating,pictures,background,related_anime,related_manga,recommendations,studios,statistics"

        header = {"X-MAL-CLIENT-ID": MAL_API}
        response = requests.get(url, headers=header).json()
        response["original_type"] = response["media_type"].replace("_", " ").title()
        response["media_type"] = media_type

        if "start_date" in response:
            response["year"] = response["start_date"][0:4]

        if "average_episode_duration" in response:
            # convert seconds to hours and minutes
            hours, minutes = divmod(int(response["average_episode_duration"] / 60), 60)
            if hours == 0:
                response["duration"] = f"{minutes}m"
            else:
                response["duration"] = f"{hours}h {minutes}m"

        if "status" in response:
            if response["status"] == "finished_airing":
                response["status"] = "Finished"
            elif response["status"] == "currently_airing":
                response["status"] = "Airing"
            elif response["status"] == "not_yet_aired":
                response["status"] = "Upcoming"
            elif response["status"] == "finished":
                response["status"] = "Finished"
            elif response["status"] == "currently_publishing":
                response["status"] = "Publishing"

        if "main_picture" in response:
            response["image"] = response["main_picture"]["large"]
        else:
            response["image"] = "none.svg"

        if "num_chapters" in response:
            response["num_episodes"] = response["num_chapters"]

        cache.set(cache_key, response, 300)

    try:
        media = Media.objects.get(
            media_id=media_id, media_type=media_type, user=request.user
        )
        data = {"response": response, "database": media}
    except Media.DoesNotExist:
        data = {"response": response}

    return data


def tmdb_edit(request, media_type, media_id):
    cache_key = media_type + str(media_id)
    response = cache.get(cache_key)
    if response is None:
        url = f"https://api.themoviedb.org/3/{media_type}/{media_id}?api_key={TMDB_API}"

        response = requests.get(url).json()

        response["media_type"] = media_type

        if "name" in response:
            response["title"] = response["name"]

        if "release_date" in response:
            response["year"] = response["release_date"][0:4]
        elif "first_air_date" in response:
            response["year"] = response["first_air_date"][0:4]

        # movies have runtime
        if "runtime" in response:
            hours, minutes = divmod(response["runtime"], 60)
            response["runtime"] = f"{hours}h {minutes}m"
        # tv shows episode runtime are shown in last_episode_to_air
        elif response["last_episode_to_air"] is not None and "runtime" in response["last_episode_to_air"]:
            hours, minutes = divmod(response["last_episode_to_air"]["runtime"], 60)
            if hours == 0:
                response["runtime"] = f"{minutes}m"
            else:
                response["runtime"] = f"{hours}h {minutes}m"
        else:
            response["runtime"] = "Unknown"

        if response["poster_path"] is None:
            response["image"] = "none.svg"
        else:
            response["image"] = response["poster_path"]

        if "number_of_episodes" in response:
            response["num_episodes"] = response["number_of_episodes"]

        cache.set(cache_key, response, 300)

    try:
        media = Media.objects.get(
            media_id=media_id, user=request.user, media_type=media_type
        )
        data = {"response": response, "database": media}
    except Media.DoesNotExist:
        data = {"response": response}

    return data
