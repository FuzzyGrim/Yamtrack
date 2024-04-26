import datetime
import logging

from app.providers import services
from django.apps import apps

from integrations import helpers

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
                    repeat
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
                    repeat
                    notes
                }
            }
        }
    }
    """

    variables = {"userName": username}
    url = "https://graphql.anilist.co"

    response = services.api_request(
        "ANILIST",
        "POST",
        url,
        params={"query": query, "variables": variables},
    )

    warning_message = ""
    anime_imported, warning_message = import_media(
        response["data"]["anime"],
        "anime",
        user,
        warning_message,
    )

    manga_imported, warning_message = import_media(
        response["data"]["manga"],
        "manga",
        user,
        warning_message,
    )

    return anime_imported, manga_imported, warning_message


def import_media(media_data, media_type, user, warning_message):
    """Import media of a specific type from Anilist."""
    logger.info("Importing %ss from Anilist", media_type)

    bulk_media = []
    for status_list in media_data["lists"]:
        if not status_list["isCustomList"]:
            for content in status_list["entries"]:
                if content["media"]["idMal"] is None:
                    warning_message += "\n {} ({}): No matching MyAnimeList ID".format(
                        content["media"]["title"]["userPreferred"],
                        media_type.capitalize(),
                    )
                else:
                    if content["status"] == "CURRENT":
                        status = "In progress"
                    else:
                        status = content["status"].capitalize()
                    notes = content["notes"] or ""

                    model_type = apps.get_model(app_label="app", model_name=media_type)
                    instance = model_type(
                        media_id=content["media"]["idMal"],
                        title=content["media"]["title"]["userPreferred"],
                        image=content["media"]["coverImage"]["large"],
                        score=content["score"],
                        progress=content["progress"],
                        status=status,
                        repeats=content["repeat"],
                        start_date=get_date(content["startedAt"]),
                        end_date=get_date(content["completedAt"]),
                        user=user,
                        notes=notes,
                    )
                    bulk_media.append(instance)

    model = apps.get_model(app_label="app", model_name=media_type)
    num_before = model.objects.filter(user=user).count()
    helpers.bulk_chunk_import(bulk_media, model)
    num_after = model.objects.filter(user=user).count()
    num_imported = num_after - num_before

    return num_imported, warning_message


def get_date(date):
    """Return date object from date dict."""
    if date["year"]:
        return datetime.date(date["year"], date["month"], date["day"])
    return None
