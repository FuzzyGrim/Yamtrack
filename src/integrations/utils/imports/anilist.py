from __future__ import annotations

import asyncio
import datetime
import logging

import requests
from app.models import Anime, Manga, User
from app.utils import helpers

logger = logging.getLogger(__name__)


def anilist_data(username: str, user: User) -> str:
    """Import anime and manga ratings from Anilist."""

    logger.info("Importing %s from Anilist", username)

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

    response = requests.post(
        url,
        json={"query": query, "variables": variables},
        timeout=5,
    )
    query = response.json()

    # usually when username not found
    if response.status_code == 404:  # noqa: PLR2004
        error_message = query.get("errors")[0].get("message")
        raise ValueError(error_message)

    # stores media titles that don't have a corresponding MAL ID
    warning_message = add_media_list(query, warning_message="", user=user)

    logger.info("Finished importing %s from Anilist", username)
    return warning_message


def add_media_list(query: dict, warning_message: str, user: User) -> str:
    """Add media to list for bulk creation."""

    bulk_media = {"anime": [], "manga": []}

    for media_type in query["data"]:
        bulk_image = []
        media_mapping = helpers.media_type_mapper(media_type)
        for status_list in query["data"][media_type]["lists"]:
            if not status_list["isCustomList"]:
                for content in status_list["entries"]:
                    if content["media"]["idMal"] is None:
                        warning_message += f"\n {content['media']['title']['userPreferred']} ({media_type.capitalize()})"
                        logger.warning(
                            "%s: %s, couldn't find a matching MAL ID.",
                            content["media"]["title"]["userPreferred"],
                            media_type.capitalize(),
                        )
                    else:
                        if content["status"] == "CURRENT":
                            status = "Watching"
                        else:
                            status = content["status"].capitalize()

                        image_url = content["media"]["coverImage"]["large"]
                        bulk_image.append(image_url)

                        image_filename = helpers.get_image_filename(
                            image_url,
                            media_type,
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
                            logger.warning(
                                "%s (%s), %s.",
                                content["media"]["title"]["userPreferred"],
                                media_type.capitalize(),
                                form.errors.as_data(),
                            )

        asyncio.run(helpers.images_downloader(bulk_image, media_type))

    Anime.objects.bulk_create(bulk_media["anime"], ignore_conflicts=True)
    Manga.objects.bulk_create(bulk_media["manga"], ignore_conflicts=True)

    return warning_message


def get_date(date: dict) -> datetime.date | None:
    """Return date object from date dict."""

    if date["year"]:
        return datetime.date(date["year"], date["month"], date["day"])

    return None
