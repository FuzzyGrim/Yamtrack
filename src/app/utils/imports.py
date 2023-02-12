from aiohttp import ClientSession
from asyncio import ensure_future, gather, run
from csv import DictReader
from decouple import config

import datetime
import requests

from app.models import Media, Season
from app.utils import helpers

TMDB_API = config("TMDB_API", default="")
MAL_API = config("MAL_API", default="")


def import_myanimelist(username, user):
    header = {"X-MAL-CLIENT-ID": MAL_API}
    anime_url = f"https://api.myanimelist.net/v2/users/{username}/animelist?fields=list_status&nsfw=true"
    animes = requests.get(anime_url, headers=header).json()

    if "error" in animes and animes["error"] == "not_found":
        return False

    manga_url = f"https://api.myanimelist.net/v2/users/{username}/mangalist?fields=list_status&nsfw=true"
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
                if not await Media.objects.filter(
                    media_id=content["node"]["id"], api="mal", user=user
                ).aexists():
                    task.append(
                        ensure_future(
                            myanimelist_get_media(session, content, media_type, user)
                        )
                    )

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
        media.start_date = datetime.datetime.strptime(
            content["list_status"]["start_date"], "%Y-%m-%d"
        ).date()
    else:
        media.start_date = None

    if "finish_date" in content["list_status"]:
        media.end_date = datetime.datetime.strptime(
            content["list_status"]["finish_date"], "%Y-%m-%d"
        ).date()
    else:
        media.end_date = None

    if "main_picture" in content["node"]:
        await helpers.download_image(
            session, content["node"]["main_picture"]["medium"], media_type
        )
        # rspilt is used to get the filename from the url by splitting the url at the last / and taking the last element
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
                    task.append(
                        ensure_future(tmdb_get_media(session, url, row, user, status))
                    )

                elif row["Type"] == "movie":
                    url = f"https://api.themoviedb.org/3/movie/{row['TMDb ID']}?api_key={TMDB_API}"
                    task.append(
                        ensure_future(tmdb_get_media(session, url, row, user, status))
                    )

        await gather(*task)


async def tmdb_get_media(session, url, row, user, status):
    async with session.get(url) as resp:
        response = await resp.json()

        if row["Your Rating"] == "":
            score = None
        else:
            score = float(row["Your Rating"])

        await helpers.download_image(
            session, f"https://image.tmdb.org/t/p/w92{response['poster_path']}", "tmdb"
        )

        if response["poster_path"] is None:
            image = "images/none.svg"
        else:
            # rspilt is used to get the filename from the url by splitting the url at the last / and taking the last element
            image = f"images/tmdb-{response['poster_path'].rsplit('/', 1)[-1]}"

        if "number_of_episodes" in response and status == "Completed":
            progress = response["number_of_episodes"]
        else:
            progress = 0

        start_date = datetime.datetime.strptime(
            row["Date Rated"], "%Y-%m-%dT%H:%M:%SZ"
        ).date()

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
            end_date=None,
        )

        if "number_of_seasons" in response:
            seasons_list = []
            for season in range(1, response["number_of_seasons"] + 1):
                if (
                    "episode_count" in response["seasons"][season]
                    and status == "Completed"
                ):
                    seasons_list.append(
                        Season(
                            media=media,
                            title=row["Name"],
                            number=season,
                            score=score,
                            status=status,
                            progress=response["seasons"][season - 1]["episode_count"],
                            start_date=start_date,
                            end_date=None,
                        )
                    )
                else:
                    seasons_list.append(
                        Season(
                            media=media,
                            title=row["Name"],
                            number=season,
                            score=score,
                            status=status,
                            progress=0,
                            start_date=start_date,
                            end_date=None,
                        )
                    )
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
                            ).aexists()
                            and content["media"]["idMal"] is not None
                        ):
                            task.append(
                                ensure_future(
                                    anilist_get_media(
                                        session, content, media_type, user
                                    )
                                )
                            )

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
        start_date = datetime.date(
            start_date["year"], start_date["month"], start_date["day"]
        )
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
        end_date=end_date,
    )

    await helpers.download_image(
        session, content["media"]["coverImage"]["medium"], media_type
    )

    # rspilt is used to get the filename from the url by splitting the url at the last / and taking the last element
    media.image = f"images/{media_type}-{content['media']['coverImage']['medium'].rsplit('/', 1)[-1]}"

    return media
