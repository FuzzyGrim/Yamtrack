from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING

import requests
import requests_cache
from app.models import Anime, Manga
from app.utils import helpers

if TYPE_CHECKING:
    from users.models import User

logger = logging.getLogger(__name__)


def anilist_data(username: str, user: User) -> str:
    """Import anime and manga ratings from Anilist."""

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

    with requests_cache.disabled():  # don't cache request as it can change frequently
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

    # returns media that couldn't be added
    return add_media_list(query, warning_message="", user=user)


def add_media_list(query: dict, warning_message: str, user: User) -> str:
    """Add media to list for bulk creation."""

    bulk_media = {"anime": [], "manga": []}

    for media_type in query["data"]:
        logger.info("Importing %ss from Anilist", media_type)

        media_mapping = helpers.media_type_mapper(media_type)
        for status_list in query["data"][media_type]["lists"]:
            if not status_list["isCustomList"]:
                for content in status_list["entries"]:
                    if content["media"]["idMal"] is None:
                        warning_message += f"\n {content['media']['title']['userPreferred']} ({media_type.capitalize()}: Couldn't find a matching MyAnimeList ID)"
                    else:
                        if content["status"] == "CURRENT":
                            status = "In progress"
                        else:
                            status = content["status"].capitalize()

                        instance = media_mapping["model"](
                            user=user,
                            title=content["media"]["title"]["userPreferred"],
                            image=content["media"]["coverImage"]["large"],
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
                            warning_message += f"\n {content['media']['title']['userPreferred']} ({media_type.capitalize()}): {form.errors.as_text()}"

    Anime.objects.bulk_create(bulk_media["anime"], ignore_conflicts=True)
    logger.info("Imported %s animes", len(bulk_media["anime"]))

    Manga.objects.bulk_create(bulk_media["manga"], ignore_conflicts=True)
    logger.info("Imported %s mangas", len(bulk_media["manga"]))

    return warning_message


def get_date(date: dict) -> datetime.date | None:
    """Return date object from date dict."""

    if date["year"]:
        return datetime.date(date["year"], date["month"], date["day"])

    return None
