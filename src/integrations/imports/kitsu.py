import datetime
import logging

import app
from app.models import Item
from django.apps import apps
from django.conf import settings

from integrations import helpers

logger = logging.getLogger(__name__)
KITSU_API_BASE_URL = "https://kitsu.io/api/edge"
KITSU_PAGE_LIMIT = 500


def import_by_user_id(kitsu_id, user):
    """Import anime and manga ratings from Kitsu by user ID."""
    anime_response = get_media_response(kitsu_id, "anime")
    num_anime_imported, anime_warning = importer(anime_response, "anime", user)

    manga_response = get_media_response(kitsu_id, "manga")
    num_manga_imported, manga_warning = importer(manga_response, "manga", user)

    warning_message = anime_warning + manga_warning
    return num_anime_imported, num_manga_imported, warning_message


def import_by_username(kitsu_username, user):
    """Import anime and manga ratings from Kitsu by username."""
    kitsu_id = get_kitsu_id(kitsu_username)
    return import_by_user_id(kitsu_id, user)


def get_kitsu_id(username):
    """Get the user ID from Kitsu."""
    url = f"{KITSU_API_BASE_URL}/users"
    response = app.providers.services.api_request(
        "KITSU",
        "GET",
        url,
        params={"filter[name]": username},
    )

    if not response["data"]:
        msg = f"User {username} not found."
        raise ValueError(msg)
    if len(response["data"]) > 1:
        msg = f"Multiple users found for {username}, please use user ID."
        raise ValueError(msg)

    return response["data"][0]["id"]


def get_media_response(kitsu_id, media_type):
    """Get all media entries for a user from Kitsu."""
    logger.info("Fetching %s from Kitsu", media_type)
    url = f"{KITSU_API_BASE_URL}/library-entries"
    params = {
        "filter[user_id]": kitsu_id,
        "filter[kind]": media_type,
        "include": f"{media_type},{media_type}.mappings",
        f"fields[{media_type}]": "canonicalTitle,posterImage,mappings",
        "fields[mappings]": "externalSite,externalId",
        "page[limit]": KITSU_PAGE_LIMIT,
    }

    all_data = {"entries": [], "included": []}

    while url:
        data = app.providers.services.api_request("KITSU", "GET", url, params=params)
        all_data["entries"].extend(data["data"])
        all_data["included"].extend(data.get("included", []))
        url = data["links"].get("next")
        params = {}  # Clear params for subsequent requests
    return all_data


def importer(response, media_type, user):
    """Import media from Kitsu and return the number of items imported."""
    logger.info("Importing %s from Kitsu", media_type)

    model = apps.get_model(app_label="app", model_name=media_type)
    media_lookup = {
        item["id"]: item for item in response["included"] if item["type"] == media_type
    }
    mapping_lookup = {
        item["id"]: item for item in response["included"] if item["type"] == "mappings"
    }

    bulk_data = []
    warning_message = ""

    for entry in response["entries"]:
        mal_id, instance = process_entry(
            entry,
            media_type,
            media_lookup,
            mapping_lookup,
            user,
        )
        if mal_id:
            bulk_data.append(instance)
        else:
            media_metadata = media_lookup[
                entry["relationships"][media_type]["data"]["id"]
            ]["attributes"]
            warning_message += (
                f"No matching MAL ID for {media_metadata['canonicalTitle']}\n"
            )

    num_before = model.objects.filter(user=user).count()
    helpers.bulk_chunk_import(bulk_data, model, user)
    num_after = model.objects.filter(user=user).count()

    num_imported = num_after - num_before
    logger.info("Imported %s %s", num_imported, media_type)

    return num_imported, warning_message


def process_entry(entry, media_type, media_lookup, mapping_lookup, user):
    """Process a single entry and return the MAL ID and model instance."""
    attributes = entry["attributes"]
    media_id = entry["relationships"][media_type]["data"]["id"]
    media = media_lookup[media_id]

    mal_id = get_mal_id(media, mapping_lookup, media_type)
    if not mal_id:
        return None, None

    item = create_or_get_item(mal_id, media_type, media)
    model = apps.get_model(app_label="app", model_name=media_type)

    instance = model(
        item=item,
        user=user,
        score=get_rating(attributes["ratingTwenty"]),
        progress=attributes["progress"],
        status=get_status(attributes["status"]),
        repeats=attributes["reconsumeCount"],
        start_date=get_date(attributes["startedAt"]),
        end_date=get_date(attributes["finishedAt"]),
        notes=attributes["notes"] or "",  # sometimes returns None instead of ""
    )

    if attributes["reconsuming"]:
        instance.status = app.models.STATUS_REPEATING

    return mal_id, instance


def get_mal_id(media, mapping_lookup, media_type):
    """Get the MAL ID for a given media item."""
    for mapping_ref in media["relationships"]["mappings"]["data"]:
        mapping = mapping_lookup[mapping_ref["id"]]
        if mapping["attributes"]["externalSite"] == f"myanimelist/{media_type}":
            return mapping["attributes"]["externalId"]
    return None


def create_or_get_item(mal_id, media_type, media):
    """Create or get an Item instance."""
    image_url = get_image_url(media)
    return Item.objects.get_or_create(
        media_id=mal_id,
        media_type=media_type,
        defaults={
            "title": media["attributes"]["canonicalTitle"],
            "image": image_url,
        },
    )[0]


def get_image_url(media):
    """Get the image URL for a media item."""
    try:
        return media["attributes"]["posterImage"]["medium"]
    except KeyError:
        try:
            return media["attributes"]["posterImage"]["original"]
        except KeyError:
            return settings.IMG_NONE


def get_rating(rating):
    """Convert the rating from Kitsu to a 0-10 scale."""
    if rating:
        return rating / 2
    return None


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
