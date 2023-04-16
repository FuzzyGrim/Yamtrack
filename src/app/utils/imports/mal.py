from aiohttp import ClientSession
from asyncio import ensure_future, gather, run
from decouple import config

import datetime
import requests
import logging

from app.models import Media
from app.utils import helpers

MAL_API = config("MAL_API", default="")
logger = logging.getLogger(__name__)


def import_myanimelist(username, user):

    logger.info(f"Importing {username} from MyAnimeList to {user}")

    header = {"X-MAL-CLIENT-ID": MAL_API}
    anime_url = f"https://api.myanimelist.net/v2/users/{username}/animelist?fields=list_status&nsfw=true"
    animes = requests.get(anime_url, headers=header).json()

    if "error" in animes and animes["error"] == "not_found":
        logger.info(f"User {username} not found in MyAnimeList.")
        return False

    manga_url = f"https://api.myanimelist.net/v2/users/{username}/mangalist?fields=list_status&nsfw=true"
    mangas = requests.get(manga_url, headers=header).json()
    series = {"anime": animes, "manga": mangas}

    bulk_add_media = run(myanilist_get_media_list(series, user))
    Media.objects.bulk_create(bulk_add_media)

    logger.info(f"Finished importing {username} from MyAnimeList")

    return True


async def myanilist_get_media_list(series, user):
    async with ClientSession() as session:
        task = []
        for media_type, media_list in series.items():
            for content in media_list["data"]:
                if await Media.objects.filter(
                    media_id=content["node"]["id"],
                    media_type=media_type,
                    user=user,
                ).aexists():
                    logger.warning(
                        f"{media_type.capitalize()}: {content['node']['title']} ({content['node']['id']}) already exists in database. Skipping..."
                    )
                else:
                    task.append(
                        ensure_future(
                            myanimelist_get_media(session, content, media_type, user)
                        )
                    )
                    logger.info(
                        f"{media_type.capitalize()}: {content['node']['title']} ({content['node']['id']}) added to import list."
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
        filename = await helpers.download_image_async(
            session, content["node"]["main_picture"]["large"], media_type
        )
        media.image = f"{filename}"

    else:
        media.image = "none.svg"

    return media
