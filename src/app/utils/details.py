from decouple import config
import requests

TMDB_API = config("TMDB_API", default=None)
MAL_API = config("MAL_API", default=None)


def mal(media_type, media_id):
    url = f"https://api.myanimelist.net/v2/{media_type}/{media_id}?fields=title,main_picture,start_date,end_date,synopsis,mean,rank,popularity,updated_at,media_type,status,genres,num_episodes,num_chapters,broadcast,source,average_episode_duration,rating,pictures,background,related_anime,related_manga,recommendations,studios,statistics"

    header = {"X-MAL-CLIENT-ID": MAL_API}
    response = requests.get(url, headers=header).json()

    response["media_type"] = media_type

    if "average_episode_duration" in response:
        # convert seconds to hours and minutes
        hours, minutes = divmod(int(response["average_episode_duration"] / 60), 60)
        if hours == 0:
            response["runtime"] = f"{minutes}m"
        else:
            response["runtime"] = f"{hours}h {minutes}m"
    else:
        response["runtime"] = "Unknown"

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
    else:
        response["status"] = "Unknown"

    if "main_picture" in response:
        response["image"] = response["main_picture"]["large"]
    else:
        response["image"] = "none.svg"

    if "num_chapters" in response:
        response["num_episodes"] = response["num_chapters"]

    if "genres" not in response:
        response["genres"] = [{"name": "Unknown"}]

    for related_anime in response["related_anime"]:
        if related_anime["node"]["main_picture"] is not None:
            related_anime["node"]["image"] = related_anime["node"]["main_picture"]["large"]
            print(related_anime["node"]["image"])
        else:
            related_anime["node"]["image"] = "none.svg"

        related_anime.update(related_anime.pop("node"))

    for related_manga in response["related_manga"]:
        if related_manga["node"]["main_picture"] is not None:
            related_manga["node"]["image"] = related_manga["node"]["main_picture"]["large"]
        else:
            related_manga["node"]["image"] = "none.svg"

        related_manga.update(related_manga.pop("node"))

    for recommendation in response["recommendations"]:
        if recommendation["node"]["main_picture"] is not None:
            recommendation["node"]["image"] = recommendation["node"]["main_picture"]["large"]
        else:
            recommendation["node"]["image"] = "none.svg"

        recommendation.update(recommendation.pop("node"))

    response["api"] = "mal"
    return response


def tmdb(media_type, media_id):
    url = f"https://api.themoviedb.org/3/{media_type}/{media_id}?api_key={TMDB_API}&append_to_response=recommendations"

    response = requests.get(url).json()

    response["media_type"] = media_type

    if response["poster_path"] is None:
        response["image"] = "none.svg"
    else:
        response["image"] = f"https://image.tmdb.org/t/p/w500{response['poster_path']}"

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

    if "number_of_episodes" in response:
        response["num_episodes"] = response["number_of_episodes"]
    else:
        response["num_episodes"] = 1

    if not response["genres"]:
        response["genres"] = [{"name": "Unknown"}]

    response["recommendations"] = response["recommendations"]["results"][:10]
    for recommendation in response["recommendations"]:
        if "name" in recommendation:
            recommendation["title"] = recommendation["name"]
        if "poster_path" in recommendation:
            recommendation["image"] = f"https://image.tmdb.org/t/p/w500{recommendation['poster_path']}"

    response["api"] = "tmdb"

    return response
