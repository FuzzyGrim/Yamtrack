from decouple import config
import requests

TMDB_API = config("TMDB_API", default=None)
MAL_API = config("MAL_API", default=None)


def mal(media_type, media_id):
    url = f"https://api.myanimelist.net/v2/{media_type}/{media_id}?fields=title,main_picture,start_date,end_date,synopsis,mean,rank,popularity,updated_at,media_type,status,genres,num_episodes,num_chapters,broadcast,source,average_episode_duration,rating,pictures,background,related_anime,related_manga,recommendations,studios,statistics"

    header = {"X-MAL-CLIENT-ID": MAL_API}
    response = requests.get(url, headers=header).json()

    response["original_type"] = response["media_type"].replace("_", " ").title()
    response["media_type"] = media_type

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
        response["image"] = ""

    if "num_chapters" in response:
        response["num_episodes"] = response["num_chapters"]

    response["api"] = "mal"
    return response


def tmdb(media_type, media_id):
    url = f"https://api.themoviedb.org/3/{media_type}/{media_id}?api_key={TMDB_API}"

    response = requests.get(url).json()

    response["media_type"] = media_type

    if "poster_path" in response:
        response["image"] = f"https://image.tmdb.org/t/p/w500{response['poster_path']}"

    # tv shows have name instead of title
    if "name" in response:
        response["title"] = response["name"]

    # movies have release_date
    if "release_date" in response:
        response["start_date"] = response["release_date"]
    # tv shows have first_air_date
    elif "first_air_date" in response:
        response["start_date"] = response["first_air_date"]

    if "last_air_date" in response:
        response["end_date"] = response["last_air_date"]

    if "overview" in response:
        response["synopsis"] = response["overview"]
    else:
        response["synopsis"] = "No synopsis available."

    # movies have runtime
    if "runtime" in response:
        hours, minutes = divmod(response["runtime"], 60)
        response["runtime"] = f"{hours}h {minutes}m"
    # tv shows episode runtime are shown in last_episode_to_air
    elif "runtime" in response["last_episode_to_air"]:
        hours, minutes = divmod(response["last_episode_to_air"]["runtime"], 60)
        if hours == 0:
            response["runtime"] = f"{minutes}m"
        else:
            response["runtime"] = f"{hours}h {minutes}m"

    if "number_of_episodes" in response:
        response["num_episodes"] = response["number_of_episodes"]
    else:
        response["num_episodes"] = 1

    response["api"] = "tmdb"

    return response
