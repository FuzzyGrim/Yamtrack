from app.exceptions import ImportSourceError
from app.models import Anime, Manga
from app.utils import helpers

import asyncio
import datetime
import requests
import logging

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
                    notes
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
                    notes
                }
            }
        }
    }
    """

    variables = {"userName": username}
    url = "https://graphql.anilist.co"

    response = requests.post(url, json={"query": query, "variables": variables})
    query = response.json()

    # usually when username not found
    if response.status_code == 404:
        error_message = query.get("errors")[0].get("message")
        raise ImportSourceError(f"Anilist API Error: {error_message}")

    # stores media titles that don't have a corresponding MAL ID
    warning_message = add_media_list(query, warning_message="", user=user)

    logger.info(f"Finished importing {username} from Anilist")
    return warning_message


def add_media_list(query, warning_message, user):
    bulk_media = {"anime": [], "manga": []}

    for media_type in query["data"]:
        bulk_image = []
        media_mapping = helpers.media_type_mapper(media_type)
        for status_list in query["data"][media_type]["lists"]:
            if not status_list["isCustomList"]:
                for content in status_list["entries"]:
                    if content["media"]["idMal"] is None:
                        warning_message += (
                            f"\n {content['media']['title']['userPreferred']}"
                        )
                        logger.warning(
                            f"{media_type.capitalize()}: {content['media']['title']['userPreferred']} has no MAL ID."
                        )
                    else:
                        if content["status"] == "CURRENT":
                            status = "Watching"
                        else:
                            status = content["status"].capitalize()

                        image_url = content["media"]["coverImage"]["large"]
                        bulk_image.append(image_url)

                        image_filename = helpers.get_image_filename(
                            image_url, media_type
                        )

                        instance = media_mapping["model"](
                            user=user,
                            title=content["media"]["title"]["userPreferred"],
                            image=image_filename,
                        )
                        form = media_mapping["form"](
                            data={
                                "media_id": content["media"]["idMal"],
                                "media_type": media_type,
                                "score": content["score"],
                                "progress": content["progress"],
                                "status": status,
                                "start_date": get_date(content["startedAt"]),
                                "end_date": get_date(content["completedAt"]),
                                "notes": content["notes"],
                            },
                            instance=instance,
                            post_processing=False,
                        )
                        if form.is_valid():
                            bulk_media[media_type].append(form.instance)
                        else:
                            error_message = f"Error importing {content['media']['title']['userPreferred']}: {form.errors.as_data()}"
                            logger.error(error_message)

        asyncio.run(helpers.images_downloader(bulk_image, media_type))

    Anime.objects.bulk_create(bulk_media["anime"], ignore_conflicts=True)
    Manga.objects.bulk_create(bulk_media["manga"], ignore_conflicts=True)

    return warning_message


def get_date(date):
    if date["year"]:
        return datetime.date(date["year"], date["month"], date["day"])
    else:
        return None
