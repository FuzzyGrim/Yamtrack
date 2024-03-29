import datetime
import logging

from app.models import Anime, Manga
from app.providers import services
from django.apps import apps

logger = logging.getLogger(__name__)


def importer(username, user):
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

    query = services.api_request(
        "ANILIST",
        "POST",
        url,
        params={"query": query, "variables": variables},
    )

    # returns media that couldn't be added
    return add_media_list(query, warning_message="", user=user)


def add_media_list(query, warning_message, user):
    """Add media to list for bulk creation."""
    bulk_media = {"anime": [], "manga": []}

    for media_type in query["data"]:
        logger.info("Importing %ss from Anilist", media_type)

        for status_list in query["data"][media_type]["lists"]:
            if not status_list["isCustomList"]:
                for content in status_list["entries"]:
                    if content["media"]["idMal"] is None:
                        warning_message += (
                            "\n {} ({}): No matching MyAnimeList ID".format(
                                content["media"]["title"]["userPreferred"],
                                media_type.capitalize(),
                            )
                        )
                    else:
                        if content["status"] == "CURRENT":
                            status = "In progress"
                        else:
                            status = content["status"].capitalize()

                        if content["notes"] is None:
                            content["notes"] = ""

                        model_type = apps.get_model(
                            app_label="app",
                            model_name=media_type,
                        )

                        instance = model_type(
                            media_id=content["media"]["idMal"],
                            title=content["media"]["title"]["userPreferred"],
                            image=content["media"]["coverImage"]["large"],
                            score=content["score"],
                            progress=content["progress"],
                            status=status,
                            start_date=get_date(content["startedAt"]),
                            end_date=get_date(content["completedAt"]),
                            user=user,
                            notes=content["notes"],
                        )

                        bulk_media[media_type].append(instance)

    Anime.objects.bulk_create(bulk_media["anime"], ignore_conflicts=True)
    Manga.objects.bulk_create(bulk_media["manga"], ignore_conflicts=True)

    return warning_message


def get_date(date):
    """Return date object from date dict."""
    if date["year"]:
        return datetime.date(date["year"], date["month"], date["day"])

    return None
