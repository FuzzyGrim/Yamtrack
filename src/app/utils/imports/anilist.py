from aiohttp import ClientSession
from asyncio import ensure_future, gather, run

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
    bulk_add_media, error = run(anilist_get_media_list(query, error="", user=user))
    Media.objects.bulk_create(bulk_add_media)

    logger.info(
        f"Finished importing {username} from Anilist"
    )
    return error


async def anilist_get_media_list(query, error, user):
    async with ClientSession() as session:
        task = []
        for media_type in query["data"]:
            for list in query["data"][media_type]["lists"]:
                if not list["isCustomList"]:
                    for content in list["entries"]:
                        if content["media"]["idMal"] is None:
                            error += f"\n {content['media']['title']['userPreferred']}"
                            logger.warning(
                                f"{media_type.capitalize()}: {content['media']['title']['userPreferred']} has no MAL ID."
                            )
                        elif await Media.objects.filter(
                            media_id=content["media"]["idMal"],
                            media_type=media_type,
                            user=user,
                        ).aexists():
                            logger.warning(
                                f"{media_type.capitalize()}: {content['media']['title']['userPreferred']} ({content['media']['idMal']}) already exists in database. Skipping..."
                            )
                        else:
                            task.append(
                                ensure_future(
                                    anilist_get_media(
                                        session, content, media_type, user
                                    )
                                )
                            )
                            logger.info(
                                f"{media_type.capitalize()}: {content['media']['title']['userPreferred']} ({content['media']['idMal']}) added to import list."
                            )

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
        user=user,
        start_date=start_date,
        end_date=end_date,
    )

    filename = await helpers.download_image_async(
        session, content["media"]["coverImage"]["large"], media_type
    )

    media.image = f"{filename}"

    return media
