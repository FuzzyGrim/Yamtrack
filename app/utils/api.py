from decouple import config
import requests
import csv
from aiohttp import ClientSession
from asyncio import ensure_future, gather, run
from django.core.files import File

from app.models import Media
from app.utils import helpers


TMDB_API = config("TMDB_API")
MAL_API = config("MAL_API")


def search(content, query):
    if content == "tmdb":
        url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API}&query={query}&include_adult=true"

        response = requests.get(url).json()
        run(tmdb_get_seasons_list(response["results"]))

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
        return await gather(*task)


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

    bulk_add_media = []

    for anime in animes["data"]:
        if not Media.objects.filter(media_id=anime["node"]["id"], api_origin="mal", user=user).exists():
            if anime["list_status"]["status"] == "plan_to_watch":
                anime["list_status"]["status"] = "Planning"
            elif anime["list_status"]["status"] == "on_hold":
                anime["list_status"]["status"] = "Paused"
            else:
                anime["list_status"]["status"] = anime["list_status"]["status"].capitalize()

            media = Media(
                    media_id=anime["node"]["id"],
                    title=anime["node"]["title"],
                    media_type="anime",
                    score=anime["list_status"]["score"],
                    status=anime["list_status"]["status"],
                    api_origin="mal",
                    user=user,
                )

            img_temp = helpers.get_image_temp(anime["node"]["main_picture"]["medium"])
            if anime["node"]["main_picture"]["medium"] == "":
                media.image.save(f"none.svg", File(img_temp), save=False)
            else:
                media.image.save(f'anime-{anime["node"]["main_picture"]["medium"].rsplit("/", 1)[-1]}', File(img_temp), save=False)

            bulk_add_media.append(
                media
            )

    for manga in mangas["data"]:
        if not Media.objects.filter(edia_id=anime["node"]["id"], api_origin="mal", user=user).exists():
            if anime["list_status"]["status"] == "plan_to_watch":
                anime["list_status"]["status"] = "Planning"
            elif anime["list_status"]["status"] == "on_hold":
                anime["list_status"]["status"] = "Paused"
            elif manga["list_status"]["status"] == "reading":
                manga["list_status"]["status"] = "Watching"
            else:
                manga["list_status"]["status"] = manga["list_status"]["status"].capitalize()

            media = Media(
                    media_id=manga["node"]["id"],
                    title=manga["node"]["title"],
                    media_type="manga",
                    score=manga["list_status"]["score"],
                    status=manga["list_status"]["status"],
                    api_origin="mal",
                    user=user,
                )
            
            img_temp = helpers.get_image_temp(anime["node"]["main_picture"]["medium"])

            if manga["node"]["main_picture"]["medium"] == "":
                media.image.save(f"none.svg", File(img_temp), save=False)
            else:
                media.image.save(f'manga-{manga["node"]["main_picture"]["medium"].rsplit("/", 1)[-1]}', File(img_temp), save=False)

            bulk_add_media.append(
                media
            )

    Media.objects.bulk_create(bulk_add_media)

    return True


def import_tmdb(file, user):

    if "ratings" in file.name:
        status = "Completed"
    else:
        status = "Planning"

    if not file.name.endswith(".csv"):
        return False

    decoded_file = file.read().decode("utf-8").splitlines()
    reader = csv.DictReader(decoded_file)

    reader = run(tmdb_get_extra_list(reader))

    bulk_add_media = []

    for row in reader:
        if not Media.objects.filter(
            media_id=row["TMDb ID"],
            media_type=row["Type"],
            api_origin="tmdb",
            user=user,
        ).exists():
            seasons_score = {}
            if row["Your Rating"] == "":
                score = None
            else:
                score = float(row["Your Rating"])

            if "num_seasons" in row:
                for season in range(1, row["num_seasons"] + 1):
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

            img_temp = helpers.get_image_temp(f"https://image.tmdb.org/t/p/w92{row['image']}")
            if row["image"] == None:
                media.image.save(f"none.svg", File(img_temp), save=False)
            else:
                media.image.save(f"tmdb-{row['image'].rsplit('/', 1)[-1]}", File(img_temp), save=False)

            bulk_add_media.append(
                media
            )

    Media.objects.bulk_create(bulk_add_media)

    return True


async def tmdb_get_extra_list(reader):
    async with ClientSession() as session:
        task = []
        for row in reader:
            if row["Type"] == "tv":
                url = f"https://api.themoviedb.org/3/tv/{row['TMDb ID']}?api_key={TMDB_API}"
                task.append(ensure_future(tmdb_get_extra(session, url, row)))

            else:
                url = f"https://api.themoviedb.org/3/movie/{row['TMDb ID']}?api_key={TMDB_API}"
                task.append(ensure_future(tmdb_get_extra(session, url, row)))

        return await gather(*task)


async def tmdb_get_extra(session, url, result):
    async with session.get(url) as resp:
        response = await resp.json()
        if "poster_path" in response:
            result["image"] = response["poster_path"]

        if "last_episode_to_air" in response:
            result["num_seasons"] = response["last_episode_to_air"]["season_number"]
        return result


def import_anilist(username, user):
    anime_query = """
    query ($userName: String){
        MediaListCollection(userName: $userName, type: ANIME) {
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
    manga_query = """
    query ($userName: String) {
        MediaListCollection(userName: $userName, type: MANGA) {
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

    animes = requests.post(url, json={"query": anime_query, "variables": variables}).json()
    mangas = requests.post(url, json={"query": manga_query, "variables": variables}).json()

    errors = []

    if "errors" in animes:
        if animes["errors"][0]["message"] == "User not found":
            errors.append("User not found")
            return errors

    bulk_add_media = []
    for list in animes["data"]["MediaListCollection"]["lists"]:
        if not list["isCustomList"]:
            for anime in list["entries"]:
                if anime["status"] == "CURRENT":
                    status = "Watching"
                else:
                    status = anime["status"].capitalize()

                if (
                    not Media.objects.filter(
                        media_id=anime["media"]["idMal"],
                        media_type="anime",
                        api_origin="mal",
                        user=user,
                    ).exists() and anime["media"]["idMal"] is not None):

                    media = Media(
                            media_id=anime["media"]["idMal"],
                            title=anime["media"]["title"]["userPreferred"],
                            media_type="anime",
                            score=anime["score"],
                            status=status,
                            api_origin="mal",
                            user=user,
                        )
                    
                    img_temp = helpers.get_image_temp(anime["media"]["coverImage"]["medium"])
                    media.image.save(f'anime-{anime["media"]["coverImage"]["medium"].rsplit("/", 1)[-1]}', File(img_temp), save=False)

                    bulk_add_media.append(
                        media
                    )

                else:
                    errors.append(anime["media"]["title"]["userPreferred"])

    for list in mangas["data"]["MediaListCollection"]["lists"]:
        if not list["isCustomList"]:
            for manga in list["entries"]:
                if manga["status"] == "CURRENT":
                    status = "Watching"
                else:
                    status = manga["status"].capitalize()

                if (
                    not Media.objects.filter(
                        media_id=manga["media"]["idMal"],
                        media_type="manga",
                        api_origin="mal",
                        user=user,
                    ).exists() and manga["media"]["idMal"] is not None):

                    media = Media(
                            media_id=manga["media"]["idMal"],
                            title=manga["media"]["title"]["userPreferred"],
                            media_type="manga",
                            score=manga["score"],
                            status=status,
                            api_origin="mal",
                            user=user,
                        )
                    
                    img_temp = helpers.get_image_temp(manga["media"]["coverImage"]["medium"])
                    media.image.save(f'manga-{manga["media"]["coverImage"]["medium"].rsplit("/", 1)[-1]}', File(img_temp), save=False)

                    bulk_add_media.append(
                        media
                    )
                else:
                    errors.append(manga["media"]["title"]["userPreferred"])
    Media.objects.bulk_create(bulk_add_media)
    return errors