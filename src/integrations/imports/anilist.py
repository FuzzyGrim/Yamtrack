import logging

import requests
from django.apps import apps
from django.utils import timezone

import app
from app.models import Media, MediaTypes, Sources
from integrations import helpers
from integrations.helpers import MediaImportError, MediaImportUnexpectedError

logger = logging.getLogger(__name__)


def importer(username, user, mode):
    """Import anime and manga ratings from Anilist."""
    logger.info("Starting AniList import for user %s with mode %s", username, mode)

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

    try:
        response = app.providers.services.api_request(
            "ANILIST",
            "POST",
            url,
            params={"query": query, "variables": variables},
        )
    except requests.exceptions.HTTPError as error:
        error_message = error.response.json()["errors"][0].get("message")
        if error_message == "User not found":
            msg = f"User {username} not found."
            raise MediaImportError(msg) from error
        if error_message == "Private User":
            msg = f"User {username} is private."
            raise MediaImportError(msg) from error
        raise

    anime_imported, anime_warnings = import_media(
        response["data"]["anime"],
        MediaTypes.ANIME.value,
        user,
        mode,
    )

    manga_imported, manga_warnings = import_media(
        response["data"]["manga"],
        MediaTypes.MANGA.value,
        user,
        mode,
    )

    imported_counts = {
        MediaTypes.ANIME.value: anime_imported,
        MediaTypes.MANGA.value: manga_imported,
    }
    warning_messages = anime_warnings + manga_warnings
    return imported_counts, "\n".join(warning_messages)


def import_media(media_data, media_type, user, mode):
    """Import media of a specific type from Anilist."""
    logger.info("Importing %s from Anilist", media_type)

    bulk_media = []
    warnings = []
    for status_list in media_data["lists"]:
        if not status_list["isCustomList"]:
            bulk_media, warnings = process_status_list(
                bulk_media,
                status_list,
                media_type,
                user,
                warnings,
            )

    model = apps.get_model(app_label="app", model_name=media_type)
    num_imported = helpers.bulk_chunk_import(bulk_media, model, user, mode)

    logger.info("Imported %s %s", num_imported, media_type)

    return num_imported, warnings


def process_status_list(bulk_media, status_list, media_type, user, warnings):
    """Process each status list."""
    for content in status_list["entries"]:
        try:
            if content["media"]["idMal"] is None:
                title = content["media"]["title"]["userPreferred"]
                warnings.append(
                    f"{title}: No matching MAL ID.",
                )
            else:
                if content["status"] == "CURRENT":
                    status = Media.Status.IN_PROGRESS.value
                else:
                    status = content["status"].capitalize()
                notes = content["notes"] or ""

                item, _ = app.models.Item.objects.get_or_create(
                    media_id=str(content["media"]["idMal"]),
                    source=Sources.MAL.value,
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
        except Exception as e:
            msg = f"Error processing history entry: {content}"
            raise MediaImportUnexpectedError(msg) from e

    return bulk_media, warnings


def get_date(date):
    """Return date object from date dict."""
    if not date["year"]:
        return None

    month = date["month"] or 1
    day = date["day"] or 1

    return timezone.datetime(
        year=date["year"],
        month=month,
        day=day,
        hour=0,
        minute=0,
        second=0,
        tzinfo=timezone.get_current_timezone(),
    )
