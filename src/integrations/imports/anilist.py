import datetime
import logging

import app
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

    logger.info("Fetching anime and manga from AniList account")
    response = app.providers.services.api_request(
        "ANILIST",
        "POST",
        url,
        params={"query": query, "variables": variables},
    )

    anime_imported, anime_warning = import_media(
        response["data"]["anime"],
        "anime",
        user,
    )

    manga_imported, manga_warning = import_media(
        response["data"]["manga"],
        "manga",
        user,
    )

    warning_message = anime_warning + manga_warning
    return anime_imported, manga_imported, warning_message


def import_media(media_data, media_type, user):
    """Import media of a specific type from Anilist."""
    logger.info("Importing %s from Anilist", media_type)

    bulk_media = []
    warning_message = ""
    for status_list in media_data["lists"]:
        if not status_list["isCustomList"]:
            bulk_media, warning_message = process_status_list(
                bulk_media,
                status_list,
                media_type,
                user,
                warning_message,
            )

    model = apps.get_model(app_label="app", model_name=media_type)
    num_before = model.objects.filter(user=user).count()
    helpers.bulk_chunk_import(bulk_media, model, user)
    num_after = model.objects.filter(user=user).count()
    num_imported = num_after - num_before

    return num_imported, warning_message


def process_status_list(bulk_media, status_list, media_type, user, warning_message):
    """Process each status list."""
    for content in status_list["entries"]:
        if content["media"]["idMal"] is None:
            warning_message += (
                f"No matching MAL ID for {content['media']['title']['userPreferred']}\n"
            )
        else:
            if content["status"] == "CURRENT":
                status = app.models.STATUS_IN_PROGRESS
            else:
                status = content["status"].capitalize()
            notes = content["notes"] or ""

            item, _ = app.models.Item.objects.get_or_create(
                media_id=content["media"]["idMal"],
                media_type=media_type,
                defaults={
                    "title": content["media"]["title"]["userPreferred"],
                    "image": content["media"]["coverImage"]["large"],
                },
            )

            model_type = apps.get_model(app_label="app", model_name=media_type)
            instance = model_type(
                item=item,
                user=user,
                score=content["score"],
                progress=content["progress"],
                status=status,
                repeats=content["repeat"],
                start_date=get_date(content["startedAt"]),
                end_date=get_date(content["completedAt"]),
                notes=notes,
            )
            bulk_media.append(instance)

    return bulk_media, warning_message


def get_date(date):
    """Return date object from date dict."""
    if date["year"]:
        return datetime.date(date["year"], date["month"], date["day"])
    return None
