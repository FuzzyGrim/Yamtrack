from decouple import config
import requests
from aiohttp import ClientSession
from asyncio import ensure_future, gather, run
from app.models import Media


TMDB_API = config("TMDB_API")
MAL_API = config("MAL_API")


def search(content, query):
    if content == "tmdb":
        url = "https://api.themoviedb.org/3/search/multi?api_key={}&query={}&include_adult=true".format(TMDB_API, query)
        response = requests.get(url).json()
        run(tmdb_list(response["results"]))
        
    else:
        header = { "X-MAL-CLIENT-ID" : MAL_API }
        anime_url = "https://api.myanimelist.net/v2/anime?q={}&limit=5".format(query)
        manga_url = "https://api.myanimelist.net/v2/manga?q={}&limit=5".format(query)
        anime_response = requests.get(anime_url, headers=header).json()
        manga_response = requests.get(manga_url, headers=header).json()

        run(mal_list(anime_response["data"], "anime"))
        run(mal_list(manga_response["data"], "manga"))

        # merge anime and manga results alternating between each
        response = [i for j in zip(anime_response["data"], manga_response["data"]) for i in j]
        
    return response


async def tmdb_list(response):
    async with ClientSession() as session:
        task = []
        for result in response:
            if result["media_type"] == "tv":
                url = "https://api.themoviedb.org/3/tv/{}?api_key={}".format(str(result["id"]), TMDB_API)
                task.append(ensure_future(tmdb_get_seasons(session, url, result)))
        return await gather(*task)

async def tmdb_get_seasons(session, url, result):
    async with session.get(url) as resp:
        response = await resp.json()
        if response["last_episode_to_air"] is None:
            result["seasons"] = 1
        else:
            result["seasons"] = response["last_episode_to_air"]["season_number"]


async def mal_list(response, type):
    async with ClientSession() as session:
        task = []
        for result in response:
            id = result["node"]["id"]
            header = { "X-MAL-CLIENT-ID" : MAL_API }
            url = "https://api.myanimelist.net/v2/{}/{}?fields=start_date,synopsis,media_type".format(type, str(id))
            task.append(ensure_future(mal_get_specific(session, url, header, result)))
        return await gather(*task)


async def mal_get_specific(session, url, header, result):
    async with session.get(url, headers=header) as resp:
        response = await resp.json()
        if "start_date" in response:
            result["node"]["start_date"] = response["start_date"]
        if "synopsis" in response:
            result["node"]["synopsis"] = response["synopsis"]
        if "media_type" in response:
            if response["media_type"] == "tv":
                result["node"]["media_type"] = "anime"
            else:
                result["node"]["media_type"] = response["media_type"]
        return result


def import_myanimelist(username, user):
    header = { "X-MAL-CLIENT-ID" : MAL_API }
    anime_url = "https://api.myanimelist.net/v2/users/{}/animelist?fields=list_status".format(username)
    anime_response = requests.get(anime_url, headers=header).json()

    if "error" in anime_response and anime_response["error"]  == "not_found":
        return False

    manga_url = "https://api.myanimelist.net/v2/users/{}/mangalist?fields=list_status".format(username)
    manga_response = requests.get(manga_url, headers=header).json()

    bulk_add_media = []

    for anime in anime_response["data"]:
        if not Media.objects.filter(media_id=anime["node"]["id"], api_origin="mal", user=user).exists():
            if anime["list_status"]["status"] == "plan_to_watch":
                anime["list_status"]["status"] = "Planning"
            elif anime["list_status"]["status"] == "on_hold":
                anime["list_status"]["status"] = "Paused"
            else:
                anime["list_status"]["status"] = anime["list_status"]["status"].capitalize()

            bulk_add_media.append(Media(media_id=anime["node"]["id"], title=anime["node"]["title"], image=anime["node"]["main_picture"]["medium"], 
                                        media_type="anime", score=anime["list_status"]["score"], status=anime["list_status"]["status"], 
                                        api_origin="mal", user=user))

    for manga in manga_response["data"]:
        if not Media.objects.filter(media_id=anime["node"]["id"], api_origin="mal", user=user).exists():
            if anime["list_status"]["status"] == "plan_to_watch":
                anime["list_status"]["status"] = "Planning"
            elif anime["list_status"]["status"] == "on_hold":
                anime["list_status"]["status"] = "Paused"
            elif manga["list_status"]["status"] == "reading":
                manga["list_status"]["status"] = "Watching"
            else:
                manga["list_status"]["status"] = manga["list_status"]["status"].capitalize()
            bulk_add_media.append(Media(media_id=manga["node"]["id"], title=manga["node"]["title"], image=manga["node"]["main_picture"]["medium"], 
                                        media_type="manga", score=manga["list_status"]["score"], status=manga["list_status"]["status"], 
                                        api_origin="mal", user=user))
    
    Media.objects.bulk_create(bulk_add_media)

    return True