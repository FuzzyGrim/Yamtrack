from django.core.cache import cache

from decouple import config
from csv import DictReader
from aiohttp import ClientSession
from asyncio import ensure_future, gather, run
import requests
import datetime

from app.models import Media, Season
from app.utils import helpers


TMDB_API = config("TMDB_API")
MAL_API = config("MAL_API")


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
        response = [item for pair in zip(animes["data"], mangas["data"]) for item in pair]
        
    return response


async def mal_search(query):
    anime_url = f"https://api.myanimelist.net/v2/anime?q={query}&limit=10"
    manga_url = f"https://api.myanimelist.net/v2/manga?q={query}&limit=10"
    async with ClientSession() as session:
        task = []
        task.append(ensure_future(mal_search_list(session, anime_url)))
        task.append(ensure_future(mal_search_list(session, manga_url)))
        animes, mangas = await gather(*task)
        return animes, mangas


async def mal_search_list(session, url):
    async with session.get(url, headers={"X-MAL-CLIENT-ID": MAL_API}) as resp:
        response = await resp.json()
        for media in response["data"]:
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


def import_myanimelist(username, user):
    header = {"X-MAL-CLIENT-ID": MAL_API}
    anime_url = f"https://api.myanimelist.net/v2/users/{username}/animelist?fields=list_status"
    animes = requests.get(anime_url, headers=header).json()

    if "error" in animes and animes["error"] == "not_found":
        return False

    manga_url = f"https://api.myanimelist.net/v2/users/{username}/mangalist?fields=list_status"
    mangas = requests.get(manga_url, headers=header).json()
    series = {"anime": animes, "manga": mangas}

    bulk_add_media = run(myanilist_get_media_list(series, user))

    Media.objects.bulk_create(bulk_add_media)

    return True


async def myanilist_get_media_list(series, user):
    async with ClientSession() as session:
        task = []
        for media_type, media_list in series.items():
            for content in media_list["data"]:
                if not await Media.objects.filter(media_id=content["node"]["id"], api="mal", user=user).aexists():
                    task.append(ensure_future(myanimelist_get_media(session, content, media_type, user)))

        return await gather(*task)


async def myanimelist_get_media(session, content, media_type, user):
    if content["list_status"]["status"] == "plan_to_watch":
        content["list_status"]["status"] = "Planning"
    elif content["list_status"]["status"] == "on_hold":
        content["list_status"]["status"] = "Paused"
    elif content["list_status"]["status"] == "reading":
        content["list_status"]["status"] = "Watching"
    else:
        content["list_status"]["status"] = content["list_status"]["status"].capitalize()

    media = Media(
            media_id=content["node"]["id"],
            title=content["node"]["title"],
            media_type=media_type,
            score=content["list_status"]["score"],
            status=content["list_status"]["status"],
            api="mal",
            user=user,
        )

    if media_type == "anime":
        media.progress = content["list_status"]["num_episodes_watched"]
    else:
        media.progress = content["list_status"]["num_chapters_read"]

    if "start_date" in content["list_status"]:
        media.start_date = datetime.datetime.strptime(content["list_status"]["start_date"], "%Y-%m-%d").date()
    else:
        media.start_date = None

    if "finish_date" in content["list_status"]:
        media.end_date = datetime.datetime.strptime(content["list_status"]["finish_date"], "%Y-%m-%d").date()
    else:
        media.end_date = None

    if "main_picture" in content["node"]:
        await helpers.download_image(session, content["node"]["main_picture"]["medium"], media_type)
        media.image = f"images/{media_type}-{content['node']['main_picture']['medium'].rsplit('/', 1)[-1]}"
        media.image = f"images/{media_type}-{content['node']['main_picture']['medium'].rsplit('/', 1)[-1]}"

    else:
        await helpers.download_image(session, "", media_type)
        media.image = "images/none.svg"

    return media


def import_tmdb(file, user):

    if "ratings" in file.name:
        status = "Completed"
    else:
        status = "Planning"

    if not file.name.endswith(".csv"):
        return False

    decoded_file = file.read().decode("utf-8").splitlines()
    reader = DictReader(decoded_file)

    run(tmdb_get_media_list(reader, user, status))

    return True


async def tmdb_get_media_list(reader, user, status):
    async with ClientSession() as session:
        task = []
        for row in reader:
            if not await Media.objects.filter(
                media_id=row["TMDb ID"],
                media_type=row["Type"],
                api="tmdb",
                user=user,
            ).aexists():
                if row["Type"] == "tv":
                    url = f"https://api.themoviedb.org/3/tv/{row['TMDb ID']}?api_key={TMDB_API}"
                    task.append(ensure_future(tmdb_get_media(session, url, row, user, status)))

                elif row["Type"] == "movie":
                    url = f"https://api.themoviedb.org/3/movie/{row['TMDb ID']}?api_key={TMDB_API}"
                    task.append(ensure_future(tmdb_get_media(session, url, row, user, status)))

        await gather(*task)


async def tmdb_get_media(session, url, row, user, status):

    async with session.get(url) as resp:
        response = await resp.json()
            
        if row["Your Rating"] == "":
            score = None
        else:
            score = float(row["Your Rating"])

        await helpers.download_image(session, f"https://image.tmdb.org/t/p/w92{response['poster_path']}", "tmdb")
        
        if response["poster_path"] == None:
            image = "images/none.svg"
        else:
            image = f"images/tmdb-{response['poster_path'].rsplit('/', 1)[-1]}"
        
        if "number_of_episodes" in response and status == "Completed":
            progress = response["number_of_episodes"]
        else:
            progress = 0

        start_date = datetime.datetime.strptime(row["Date Rated"], "%Y-%m-%dT%H:%M:%SZ").date()

        media = await Media.objects.acreate(
            media_id=row["TMDb ID"],
            title=row["Name"],
            media_type=row["Type"],
            score=score,
            progress=progress,
            status=status,
            api="tmdb",
            user=user,
            image=image,
            start_date=start_date,
            end_date=None
        )

        if "number_of_seasons" in response:
            seasons_list = []
            for season in range(1, response["number_of_seasons"] + 1):
                if "episode_count" in response["seasons"][season] and status == "Completed":
                    seasons_list.append(Season(media=media, title=row["Name"], number=season, score=score, status=status, 
                                               progress=response["seasons"][season - 1]["episode_count"], 
                                               start_date=start_date,
                                               end_date=None))
                else:
                    seasons_list.append(Season(media=media, title=row["Name"], number=season, score=score, status=status, 
                                               progress=0, start_date=start_date, end_date=None))
            await Season.objects.abulk_create(seasons_list)


def import_anilist(username, user):
    query = """
    query ($userName: String){
        anime: MediaListCollection(userName: $userName, type: ANIME) {
            lists {
                isCustomList
                entries {
                    media{
                        title {
                            userPreferred
                        }
                        coverImage {
                            medium
                        }
                        idMal
                    }
                    status
                    score(format: POINT_10_DECIMAL)
                    progress
                  	startedAt {
                        year
                        month
                        day
                  	}
                    completedAt {
                        year
                        month
                        day
                    }
                }
            }
        }
        manga: MediaListCollection(userName: $userName, type: MANGA) {
            lists {
                isCustomList
                entries {
                    media{
                        title {
                            userPreferred
                        }
                        coverImage {
                            medium
                        }
                        idMal
                    }
                    status
                    score(format: POINT_10_DECIMAL)
                    progress
                  	startedAt {
                        year
                        month
                        day
                  	}
                    completedAt {
                        year
                        month
                        day
                    }
                }
            }
        }
    }
    """

    variables = {"userName": username}

    url = "https://graphql.anilist.co"

    query = requests.post(url, json={"query": query, "variables": variables}).json()

    error = ""

    if "errors" in query:
        if query["errors"][0]["message"] == "User not found":
            error = "User not found"
    else:

        bulk_add_media, error = run(anilist_get_media_list(query, error, user))
        Media.objects.bulk_create(bulk_add_media)

    return error


async def anilist_get_media_list(query, error, user):
    async with ClientSession() as session:
        task = []
        for media_type in query["data"]:
            for list in query["data"][media_type]["lists"]:
                if not list["isCustomList"]:
                    for content in list["entries"]:
                        if (
                            not await Media.objects.filter(
                                media_id=content["media"]["idMal"],
                                media_type=media_type,
                                api="mal",
                                user=user,
                            ).aexists() and content["media"]["idMal"] is not None):
                            task.append(ensure_future(anilist_get_media(session, content, media_type, user)))

                        else:
                            error += f"\n {content['media']['title']['userPreferred']}"

        return await gather(*task), error


async def anilist_get_media(session, content, media_type, user):
    if content["status"] == "CURRENT":
        status = "Watching"
    else:
        status = content["status"].capitalize()

    start_date = content["startedAt"]
    end_date = content["completedAt"]

    if start_date["year"]:
        start_date = datetime.date(start_date["year"], start_date["month"], start_date["day"])
    else:
        start_date = None

    if end_date["year"]:
        end_date = datetime.date(end_date["year"], end_date["month"], end_date["day"])
    else:
        end_date = None

    media = Media(
            media_id=content["media"]["idMal"],
            title=content["media"]["title"]["userPreferred"],
            media_type=media_type,
            score=content["score"],
            progress=content["progress"],
            status=status,
            api="mal",
            user=user,
            start_date=start_date,
            end_date=end_date
        )

    await helpers.download_image(session, content["media"]["coverImage"]["medium"], media_type)
        
    media.image = f"images/{media_type}-{content['media']['coverImage']['medium'].rsplit('/', 1)[-1]}"

    return media