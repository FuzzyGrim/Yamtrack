import asyncio
import datetime
import requests
import logging

from app.models import Media
from app.utils import helpers

logger = logging.getLogger(__name__)


def import_anilist(username, user):
    logger.info(f"Importing {username} from Anilist to {user}")

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
                            large
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
                            large
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

    if "errors" in query:
        if query["errors"][0]["message"] == "User not found":
            logger.error(f"User {username} not found in Anilist.")
            return "User not found"

    # error stores media titles that don't have a corresponding MAL ID
    error = add_media_list(query, error="", user=user)

    logger.info(
        f"Finished importing {username} from Anilist"
    )
    return error


def add_media_list(query, error, user):
    bulk_add_media = []

    for media_type in query["data"]:
        images_to_download = []
        for status_list in query["data"][media_type]["lists"]:
            if not status_list["isCustomList"]:
                for content in status_list["entries"]:
                    if content["media"]["idMal"] is None:
                        error += f"\n {content['media']['title']['userPreferred']}"
                        logger.warning(
                            f"{media_type.capitalize()}: {content['media']['title']['userPreferred']} has no MAL ID."
                        )
                    elif Media.objects.filter(
                        media_id=content["media"]["idMal"],
                        media_type=media_type,
                        user=user,
                    ).exists():
                        logger.warning(
                            f"{media_type.capitalize()}: {content['media']['title']['userPreferred']} ({content['media']['idMal']}) already exists, skipping..."
                        )
                    else:
                        images_to_download, bulk_add_media = process_media(
                            content, media_type, user, images_to_download, bulk_add_media
                        )

                        logger.info(
                            f"{media_type.capitalize()}: {content['media']['title']['userPreferred']} ({content['media']['idMal']}) added to import list."
                        )
        asyncio.run(helpers.images_downloader(images_to_download, media_type))

    Media.objects.bulk_create(bulk_add_media)

    return error


def process_media(content, media_type, user, images_to_download, bulk_add_media):
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
        user=user,
        start_date=start_date,
        end_date=end_date,
    )

    bulk_add_media.append(media)

    image_url = content["media"]["coverImage"]["large"]
    images_to_download.append(image_url)

    # rsplit is used to split the url at the last / and taking the last element
    # https://api-cdn.myanimelist.net/images/anime/12/76049.jpg -> 76049.jpg
    media.image = f"{media_type}-{image_url.rsplit('/', 1)[-1]}"

    return images_to_download, bulk_add_media
