from django.core.cache import cache

from decouple import config
from aiohttp import ClientSession
from asyncio import ensure_future, gather, run
import requests

from app.models import Media


TMDB_API = config("TMDB_API", default=None)
MAL_API = config("MAL_API", default=None)


def search(api_type, query):
    if api_type == "tmdb":
        url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API}&query={query}&include_adult=true"
        response = requests.get(url).json()["results"]
        for media in response:
            media['media_id'] = media['id']
            
            # needed for delete button
            media['api'] = 'tmdb'

    elif api_type == "mal":
        animes, mangas = run(mal_search(query))

        # merge anime and manga results alternating between each
        response = [item for pair in zip(animes, mangas) for item in pair]
        
    return response


async def mal_search(query):
    anime_url = f"https://api.myanimelist.net/v2/anime?q={query}&limit=10&nsfw=true"
    manga_url = f"https://api.myanimelist.net/v2/manga?q={query}&limit=10&nsfw=true"
    async with ClientSession() as session:
        task = []
        task.append(ensure_future(mal_search_list(session, anime_url)))
        task.append(ensure_future(mal_search_list(session, manga_url)))
        animes, mangas = await gather(*task)
        return animes, mangas


async def mal_search_list(session, url):
    async with session.get(url, headers={"X-MAL-CLIENT-ID": MAL_API}) as resp:
        response = await resp.json()
        if "data" in response:
            response = response["data"]
            for media in response:
                media["node"]["media_type"] = "manga" if "manga" in url else "anime"
                media["node"]["media_id"] = media["node"]["id"]

                # needed for delete button
                media["node"]["api"] = "mal"
                media.update(media.pop("node"))
        return response
    

def mal_edit(request, media_type, media_id):
    cache_key = media_type + str(media_id)
    response = cache.get(cache_key)
    if response is None:
        url = f"https://api.myanimelist.net/v2/{media_type}/{media_id}?fields=title,main_picture,start_date,synopsis,media_type,num_episodes,num_chapters,average_episode_duration,status,genres"
        header = {"X-MAL-CLIENT-ID": MAL_API}
        response = requests.get(url, headers=header).json()

        response["media_type"] = media_type

        if "start_date" in response:
            response["year"] = response["start_date"][0:4]

        if "average_episode_duration" in response:
            # convert seconds to hours and minutes
            hours, minutes = divmod(int(response["average_episode_duration"]/60), 60)
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

        cache.set(cache_key, response, 300)

    try:
        media = Media.objects.get(media_id=media_id, user=request.user, api="mal", media_type=media_type)
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

        if "runtime" in response:
            hours, minutes = divmod(response["runtime"], 60)
            response["duration"] = f"{hours}h {minutes}m"
        elif "runtime" in response["last_episode_to_air"]:
            hours, minutes = divmod(response["last_episode_to_air"]["runtime"], 60)
            if hours == 0:
                response["duration"] = f"{minutes}m"
            else:
                response["duration"] = f"{hours}h {minutes}m"

        if "poster_path" in response:
            response["image"] = response["poster_path"]
        else:
            response["image"] = ""
        
        if "number_of_episodes" in response:
            response["num_episodes"] = response["number_of_episodes"]

        response["api"] = "tmdb"

        cache.set(cache_key, response, 300)

    try:
        media = Media.objects.get(media_id=media_id, user=request.user, api="tmdb", media_type=media_type)
        data = {"response": response, "database": media}
    except Media.DoesNotExist:
        data = {"response": response}

    return data