from decouple import config

import asyncio
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
    anime_url = f"https://api.myanimelist.net/v2/users/{username}/animelist?fields=list_status&nsfw=true&limit=100"
    animes = requests.get(anime_url, headers=header).json()

    if "error" in animes and animes["error"] == "not_found":
        logger.info(f"User {username} not found in MyAnimeList.")
        return False

    while "next" in animes["paging"]:
        next_url = animes["paging"]["next"]
        # Fetch the data from the next URL
        next_data = requests.get(next_url, headers=header).json()
        # Append the new data to the existing data in the response
        animes["data"].extend(next_data["data"])
        # Update the "paging" key with the new "next" URL (if any)
        animes["paging"] = next_data["paging"]

    bulk_add_media = add_media_list(animes, "anime", user)

    manga_url = f"https://api.myanimelist.net/v2/users/{username}/mangalist?fields=list_status&nsfw=true&limit=100"
    mangas = requests.get(manga_url, headers=header).json()

    while "next" in mangas["paging"]:
        next_url = mangas["paging"]["next"]
        # Fetch the data from the next URL
        next_data = requests.get(next_url, headers=header).json()
        # Append the new data to the existing data in the response
        mangas["data"].extend(next_data["data"])
        # Update the "paging" key with the new "next" URL (if any)
        mangas["paging"] = next_data["paging"]

    bulk_add_media.extend(add_media_list(mangas, "manga", user))

    Media.objects.bulk_create(bulk_add_media)
    logger.info(f"Finished importing {username} from MyAnimeList")

    return True


def add_media_list(response, media_type, user):
    bulk_add_media = []
    images_to_download = []
    for content in response["data"]:
        if Media.objects.filter(
            media_id=content["node"]["id"],
            media_type=media_type,
            user=user,
        ).exists():
            logger.warning(
                f"{media_type.capitalize()}: {content['node']['title']} ({content['node']['id']}) already exists, skipping..."
            )
        else:
            images_to_download, bulk_add_media = process_media(content, media_type, user, images_to_download, bulk_add_media)

            logger.info(
                f"{media_type.capitalize()}: {content['node']['title']} ({content['node']['id']}) added to import list."
            )

    asyncio.run(helpers.images_downloader(images_to_download, media_type))

    return bulk_add_media


def process_media(content, media_type, user, images_to_download, bulk_add_media):
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
        image_url = content['node']['main_picture']['large']
        images_to_download.append(image_url)

        # rsplit is used to split the url at the last / and taking the last element
        # https://api-cdn.myanimelist.net/images/anime/12/76049.jpg -> 76049.jpg
        media.image = f"{media_type}-{image_url.rsplit('/', 1)[-1]}"

    else:
        media.image = "none.svg"

    bulk_add_media.append(media)

    return images_to_download, bulk_add_media
