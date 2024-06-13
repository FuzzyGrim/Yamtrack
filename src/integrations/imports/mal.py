import logging

import app
from django.apps import apps
from django.conf import settings

from integrations import helpers

logger = logging.getLogger(__name__)

base_url = "https://api.myanimelist.net/v2/users"


def importer(username, user):
    """Import anime and manga from MyAnimeList."""
    anime_imported = import_media(username, user, "anime")
    manga_imported = import_media(username, user, "manga")
    return anime_imported, manga_imported


def import_media(username, user, media_type):
    """Import media of a specific type from MyAnimeList."""
    params = {
        "fields": "list_status{comments,num_times_rewatched,num_times_reread}",
        "nsfw": "true",
        "limit": 1000,
    }
    url = f"{base_url}/{username}/{media_type}list"
    media_data = get_whole_response(url, params)
    bulk_media = add_media_list(media_data, media_type, user)

    model = apps.get_model(app_label="app", model_name=media_type)
    num_before = model.objects.filter(user=user).count()
    helpers.bulk_chunk_import(bulk_media, model, user)
    num_after = model.objects.filter(user=user).count()

    return num_after - num_before


def get_whole_response(url, params):
    """Fetch whole data from user."""
    headers = {"X-MAL-CLIENT-ID": settings.MAL_API}
    data = app.providers.services.api_request(
        "MAL",
        "GET",
        url,
        params=params,
        headers=headers,
    )

    while "next" in data["paging"]:
        next_url = data["paging"]["next"]
        next_data = app.providers.services.api_request(
            "MAL",
            "GET",
            next_url,
            params=params,
            headers=headers,
        )
        data["data"].extend(next_data["data"])
        data["paging"] = next_data["paging"]

    return data


def add_media_list(response, media_type, user):
    """Add media to list for bulk creation."""
    logger.info("Importing %ss from MyAnimeList", media_type)
    bulk_media = []

    for content in response["data"]:
        list_status = content["list_status"]
        status = get_status(list_status["status"])

        if media_type == "anime":
            progress = list_status["num_episodes_watched"]
            repeats = list_status["num_times_rewatched"]
            if list_status["is_rewatching"]:
                status = "Repeating"
        else:
            progress = list_status["num_chapters_read"]
            repeats = list_status["num_times_reread"]
            if list_status["is_rereading"]:
                status = "Repeating"

        try:
            image_url = content["node"]["main_picture"]["large"]
        except KeyError:
            image_url = settings.IMG_NONE

        model = apps.get_model(app_label="app", model_name=media_type)
        instance = model(
            media_id=content["node"]["id"],
            title=content["node"]["title"],
            image=image_url,
            score=list_status["score"],
            progress=progress,
            status=status,
            repeats=repeats,
            start_date=list_status.get("start_date", None),
            end_date=list_status.get("finish_date", None),
            user=user,
            notes=list_status["comments"],
        )
        bulk_media.append(instance)

    return bulk_media


def get_status(status):
    """Convert the status from MyAnimeList to the status used in the app."""
    status_mapping = {
        "completed": app.models.STATUS_COMPLETED,
        "reading": app.models.STATUS_IN_PROGRESS,
        "watching": app.models.STATUS_IN_PROGRESS,
        "plan_to_watch": app.models.STATUS_PLANNING,
        "plan_to_read": app.models.STATUS_PLANNING,
        "on_hold": app.models.STATUS_PAUSED,
        "dropped": app.models.STATUS_DROPPED,
    }
    return status_mapping[status]
