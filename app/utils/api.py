from decouple import config
import requests
import csv
from aiohttp import ClientSession
from asyncio import ensure_future, gather, run


from app.models import Media
from app.utils import helpers


TMDB_API = config("TMDB_API")
MAL_API = config("MAL_API")


def search(content, query):
    if content == "tmdb":
        url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API}&query={query}&include_adult=true"

        response = requests.get(url).json()
        response = run(tmdb_get_seasons_list(response["results"]))

    else:
        
        header = {"X-MAL-CLIENT-ID": MAL_API}
        anime_url = f"https://api.myanimelist.net/v2/anime?q={query}&limit=5"
        manga_url = f"https://api.myanimelist.net/v2/manga?q={query}&limit=5"
        
        animes = requests.get(anime_url, headers=header).json()
        mangas = requests.get(manga_url, headers=header).json()

        run(mal_get_extra_list(animes["data"], "anime"))
        run(mal_get_extra_list(mangas["data"], "manga"))

        # merge anime and manga results alternating between each
        response = [i for j in zip(animes["data"], mangas["data"]) for i in j]

    return response


async def tmdb_get_seasons_list(response):
    async with ClientSession() as session:
        task = []
        for result in response:
            if result["media_type"] == "tv":
                url = f"https://api.themoviedb.org/3/tv/{result['id']}?api_key={TMDB_API}"
                task.append(ensure_future(tmdb_get_seasons(session, url, result)))

        await gather(*task)
        return response


async def tmdb_get_seasons(session, url, result):
    async with session.get(url) as resp:
        response = await resp.json()
        if response["last_episode_to_air"] is None:
            result["num_seasons"] = 1
        else:
            result["num_seasons"] = response["last_episode_to_air"]["season_number"]


async def mal_get_extra_list(response, type):
    async with ClientSession() as session:

        task = []
        for result in response:
            id = result["node"]["id"]
            url = f"https://api.myanimelist.net/v2/{type}/{id}?fields=start_date,synopsis,media_type"
            task.append(ensure_future(mal_get_extra(session, url, result)))
        return await gather(*task)


async def mal_get_extra(session, url, result):

    async with session.get(url, headers={"X-MAL-CLIENT-ID": MAL_API}) as resp:
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
                if not await Media.objects.filter(media_id=content["node"]["id"], api_origin="mal", user=user).aexists():

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
            api_origin="mal",
            user=user,
        )

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
    reader = csv.DictReader(decoded_file)

    bulk_add_media = run(tmdb_get_media_list(reader, user, status))

    Media.objects.bulk_create(bulk_add_media)

    return True


async def tmdb_get_media_list(reader, user, status):
    async with ClientSession() as session:
        task = []
        for row in reader:
            if not await Media.objects.filter(
                media_id=row["TMDb ID"],
                media_type=row["Type"],
                api_origin="tmdb",
                user=user,
            ).aexists():
                if row["Type"] == "tv":
                    url = f"https://api.themoviedb.org/3/tv/{row['TMDb ID']}?api_key={TMDB_API}"
                    task.append(ensure_future(tmdb_get_media(session, url, row, user, status)))

                else:
                    url = f"https://api.themoviedb.org/3/movie/{row['TMDb ID']}?api_key={TMDB_API}"
                    task.append(ensure_future(tmdb_get_media(session, url, row, user, status)))

        return await gather(*task)


async def tmdb_get_media(session, url, row, user, status):

    async with session.get(url) as resp:
        response = await resp.json()
            
        seasons_score = {}
        if row["Your Rating"] == "":
            score = None
        else:
            score = float(row["Your Rating"])

        if "last_episode_to_air" in response:
            for season in range(1, response["last_episode_to_air"]["season_number"] + 1):
                seasons_score[season] = score

        media = Media(
                media_id=row["TMDb ID"],
                title=row["Name"],
                media_type=row["Type"],
                seasons_score=seasons_score,
                score=score,
                status=status,
                api_origin="tmdb",
                user=user,
                num_seasons=row.get("num_seasons"),
                image=row.get("image"),
            )

        await helpers.download_image(session, f"https://image.tmdb.org/t/p/w92{response['poster_path']}", "tmdb")
        
        if response["poster_path"] == None:
            media.image = "images/none.svg"
        else:
            media.image = f"images/tmdb-{response['poster_path'].rsplit('/', 1)[-1]}"

        return media


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
                }
            }
        }
    }
    """

    variables = {"userName": username}

    url = "https://graphql.anilist.co"

    query = requests.post(url, json={"query": query, "variables": variables}).json()

    errors = []

    if "errors" in query:
        if query["errors"][0]["message"] == "User not found":
            errors.append("User not found")
            return errors

    bulk_add_media = run(anilist_get_media_list(query, errors, user))

    Media.objects.bulk_create(bulk_add_media)

    return errors


async def anilist_get_media_list(query, errors, user):
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
                                api_origin="mal",
                                user=user,
                            ).aexists() and content["media"]["idMal"] is not None):
                            task.append(ensure_future(anilist_get_media(session, content, media_type, user)))

                        else:
                            errors.append(content["media"]["title"]["userPreferred"])

        return await gather(*task)


async def anilist_get_media(session, content, media_type, user):
    if content["status"] == "CURRENT":
        status = "Watching"
    else:
        status = content["status"].capitalize()

    media = Media(
            media_id=content["media"]["idMal"],
            title=content["media"]["title"]["userPreferred"],
            media_type=media_type,
            score=content["score"],
            status=status,
            api_origin="mal",
            user=user,
        )

    await helpers.download_image(session, content["media"]["coverImage"]["medium"], media_type)
        
    media.image = f"images/{media_type}-{content['media']['coverImage']['medium'].rsplit('/', 1)[-1]}"

    return media