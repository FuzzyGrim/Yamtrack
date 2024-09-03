import datetime
import logging

import app
from django.apps import apps
from django.conf import settings

from integrations import helpers

logger = logging.getLogger(__name__)


def import_by_user_id(kitsu_id, user):
    """Import anime and manga ratings from Kitsu by user ID."""
    anime_response = get_anime_response(kitsu_id)
    manga_response = get_manga_response(kitsu_id)

    warning_message = ""

    num_anime_imported, warning_message = importer(
        anime_response,
        "anime",
        user,
        warning_message,
    )
    num_manga_imported, warning_message = importer(
        manga_response,
        "manga",
        user,
        warning_message,
    )

    return num_anime_imported, num_manga_imported, warning_message


def import_by_username(kitsu_username, user):
    """Import anime and manga ratings from Kitsu by username."""
    kitsu_id = get_kitsu_id(kitsu_username)
    return import_by_user_id(kitsu_id, user)


def get_kitsu_id(username):
    """Get the user ID from Kitsu."""
    url = "https://kitsu.io/api/edge/users"
    response = app.providers.services.api_request(
        "KITSU",
        "GET",
        url,
        params={"filter[name]": username},
    )
    if len(response["data"]) == 0:
        raise ValueError(f"User {username} not found.")
    if len(response["data"]) > 1:
        raise ValueError(f"Multiple users found for {username}.")
    return response["data"][0]["id"]


def get_anime_response(kitsu_id):
    """Get all anime entries for a user from Kitsu."""
    url = "https://kitsu.io/api/edge/library-entries"
    params = {
        "filter[user_id]": kitsu_id,
        "filter[kind]": "anime",
        "include": "anime,anime.mappings",
        "fields[anime]": "canonicalTitle,posterImage,mappings",
        "fields[mappings]": "externalSite,externalId",
        "page[limit]": 500,
    }

    all_data = {
        "entries": [],
        "included": [],
    }

    while url:
        data = app.providers.services.api_request(
            "KITSU",
            "GET",
            url,
            params=params,
        )

        all_data["entries"].extend(data["data"])
        all_data["included"].extend(data.get("included", []))

        # Check for next page
        url = data["links"].get("next")
        params = {}  # Clear params for subsequent requests

    return all_data


def get_manga_response(kitsu_id):
    """Get all manga entries for a user from Kitsu."""
    url = "https://kitsu.io/api/edge/library-entries"
    params = {
        "filter[user_id]": kitsu_id,
        "filter[kind]": "manga",
        "include": "manga,manga.mappings",
        "fields[manga]": "canonicalTitle,posterImage,mappings",
        "fields[mappings]": "externalSite,externalId",
        "page[limit]": 500,
    }

    all_data = {
        "entries": [],
        "included": [],
    }

    while url:
        data = app.providers.services.api_request(
            "KITSU",
            "GET",
            url,
            params=params,
        )

        all_data["entries"].extend(data["data"])
        all_data["included"].extend(data.get("included", []))

        # Check for next page
        url = data["links"].get("next")
        params = {}  # Clear params for subsequent requests

    return all_data


def importer(response, media_type, user, warning_message):
    """Import media from Kitsu and return the number of items imported."""
    bulk_data = []
    model = apps.get_model(app_label="app", model_name=media_type)

    media_lookup = {
        item["id"]: item for item in response["included"] if item["type"] == media_type
    }
    mapping_lookup = {
        item["id"]: item for item in response["included"] if item["type"] == "mappings"
    }
    for entry in response["entries"]:
        attributes = entry["attributes"]
        media_id = entry["relationships"][media_type]["data"]["id"]
        media = media_lookup[media_id]

        mal_id = None
        for mapping_ref in media["relationships"]["mappings"]["data"]:
            mapping = mapping_lookup[mapping_ref["id"]]
            if mapping["attributes"]["externalSite"] == "myanimelist/" + media_type:
                mal_id = mapping["attributes"]["externalId"]
                break

        if mal_id:
            try:
                image_url = media["attributes"]["posterImage"]["medium"]
            except KeyError:
                try:
                    image_url = media["attributes"]["posterImage"]["original"]
                except KeyError:
                    image_url = settings.IMG_NONE
            item, _ = app.models.Item.objects.get_or_create(
                media_id=mal_id,
                media_type=media_type,
                defaults={
                    "title": media["attributes"]["canonicalTitle"],
                    "image": image_url,
                },
            )

            instance = model(
                item=item,
                user=user,
                score=attributes["rating"],
                progress=attributes["progress"],
                status=get_status(attributes["status"]),
                repeats=attributes["reconsumeCount"],
                start_date=get_date(attributes["startedAt"]),
                end_date=get_date(attributes["finishedAt"]),
                notes=attributes["notes"],
            )

            if attributes["reconsuming"]:
                instance.status = app.models.STATUS_REPEATING
            bulk_data.append(instance)
        else:
            warning_message += (
                f"No matching MAL ID for {media['attributes']['canonicalTitle']}\n"
            )

    num_before = model.objects.filter(user=user).count()
    helpers.bulk_chunk_import(bulk_data, model, user)
    num_after = model.objects.filter(user=user).count()
    return num_after - num_before, warning_message


def get_date(date):
    """Convert the date from Kitsu to a date object."""
    if date:
        return (
            datetime.datetime.strptime(date, "%Y-%m-%dT%H:%M:%S.%fZ")
            .replace(tzinfo=datetime.UTC)
            .astimezone(settings.TZ)
            .date()
        )

    return None


def get_status(status):
    """Convert the status from Kitsu to the status used in the app."""
    status_mapping = {
        "completed": app.models.STATUS_COMPLETED,
        "current": app.models.STATUS_IN_PROGRESS,
        "planned": app.models.STATUS_PLANNING,
        "on_hold": app.models.STATUS_PAUSED,
        "dropped": app.models.STATUS_DROPPED,
    }
    return status_mapping[status]
