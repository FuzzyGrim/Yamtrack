import json
import logging
from pathlib import Path

from django.apps import apps
from django.conf import settings

import app
from app.models import Item, Media, MediaTypes, Sources
from integrations import helpers
from integrations.helpers import MediaImportError, MediaImportUnexpectedError

logger = logging.getLogger(__name__)
KITSU_API_BASE_URL = "https://kitsu.io/api/edge"
KITSU_PAGE_LIMIT = 500


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
        raise MediaImportError(msg)
    if len(response["data"]) > 1:
        msg = (
            f"Multiple users found for {username}, please use your user ID. "
            "User IDs can be found in the URL when viewing your Kitsu profile."
        )
        raise MediaImportError(msg)

    return response["data"][0]["id"]


def importer(kitsu_id, user, mode):
    """Import anime and manga ratings from Kitsu by user ID."""
    # Check if given ID is a username
    if not kitsu_id.isdigit():
        kitsu_id = get_kitsu_id(kitsu_id)

    logger.info("Starting Kitsu import for user id %s with mode %s", kitsu_id, mode)

    anime_response = get_media_response(kitsu_id, MediaTypes.ANIME.value)
    num_anime_imported, anime_warnings = import_media(
        anime_response,
        MediaTypes.ANIME.value,
        user,
        mode,
    )

    manga_response = get_media_response(kitsu_id, MediaTypes.MANGA.value)
    num_manga_imported, manga_warning = import_media(
        manga_response,
        MediaTypes.MANGA.value,
        user,
        mode,
    )

    imported_counts = {
        MediaTypes.ANIME.value: num_anime_imported,
        MediaTypes.MANGA.value: num_manga_imported,
    }
    warning_messages = anime_warnings + manga_warning
    return imported_counts, "\n".join(warning_messages)


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


def import_media(response, media_type, user, mode):
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
    warnings = []

    current_file_dir = Path(__file__).resolve().parent
    json_file_path = current_file_dir / "data" / "kitsu-mu-mapping.json"
    with json_file_path.open() as f:
        kitsu_mu_mapping = json.load(f)
        for entry in response["entries"]:
            try:
                instance = process_entry(
                    entry,
                    media_type,
                    media_lookup,
                    mapping_lookup,
                    kitsu_mu_mapping,
                    user,
                )
            except MediaImportError as error:
                warnings.append(str(error))
            except Exception as error:
                kitsu_id = entry["relationships"][media_type]["data"]["id"]
                kitsu_metadata = media_lookup[kitsu_id]
                title = kitsu_metadata["attributes"]["canonicalTitle"]
                msg = f"Error processing entry: {title} ({kitsu_id}) - {entry}"
                raise MediaImportUnexpectedError(msg) from error
            else:
                bulk_data.append(instance)

    num_imported = helpers.bulk_chunk_import(bulk_data, model, user, mode)

    logger.info("Imported %s %s", num_imported, media_type)

    return num_imported, warnings


def process_entry(
    entry,
    media_type,
    media_lookup,
    mapping_lookup,
    kitsu_mu_mapping,
    user,
):
    """Process a single entry and return the model instance."""
    attributes = entry["attributes"]
    relationship = entry["relationships"][media_type]

    if relationship["data"]:
        kitsu_id = relationship["data"]["id"]
        kitsu_metadata = media_lookup[kitsu_id]
    else:
        # NSFW content are hidden, fetch from related URL
        kitsu_metadata, mapping_lookup = fetch_media_from_related_url(
            relationship,
            media_type,
        )

    item = create_or_get_item(
        media_type,
        kitsu_metadata,
        mapping_lookup,
        kitsu_mu_mapping,
    )

    model = apps.get_model(app_label="app", model_name=media_type)

    instance = model(
        item=item,
        user=user,
        score=get_rating(attributes["ratingTwenty"]),
        progress=attributes["progress"],
        status=get_status(attributes["status"]),
        repeats=attributes["reconsumeCount"],
        start_date=attributes["startedAt"],
        end_date=attributes["finishedAt"],
        notes=attributes["notes"] or "",  # sometimes returns None instead of ""
    )

    if attributes["reconsuming"]:
        instance.status = Media.Status.REPEATING.value

    return instance


def fetch_media_from_related_url(relationship, media_type):
    """Fetch media data from Kitsu related URL when relationship data is null."""
    related_url = relationship["links"]["related"]
    if not related_url:
        msg = (
            f"Could not import unknown item - missing media data from Kitsu. "
            f"Relationship: {relationship}"
        )
        raise MediaImportError(msg)

    params = {
        "include": "mappings",
        f"fields[{media_type}]": "canonicalTitle,posterImage,mappings",
        "fields[mappings]": "externalSite,externalId",
    }

    response = app.providers.services.api_request(
        "KITSU",
        "GET",
        related_url,
        params=params,
    )

    mapping_lookup = {
        item["id"]: item
        for item in response.get("included", [])
        if item["type"] == "mappings"
    }

    return response["data"], mapping_lookup


def create_or_get_item(media_type, kitsu_metadata, mapping_lookup, kitsu_mu_mapping):
    """Create or get an Item instance."""
    sites = [
        f"myanimelist/{media_type}",
        "mangaupdates",
    ]

    mappings = {
        mapping["attributes"]["externalSite"]: mapping["attributes"]["externalId"]
        for mapping_ref in kitsu_metadata["relationships"]["mappings"]["data"]
        for mapping in [mapping_lookup[mapping_ref["id"]]]
    }

    media_id = None
    for site in sites:
        if site not in mappings:
            continue

        external_id = mappings[site]
        if site == f"myanimelist/{media_type}":
            media_id = external_id
            source = Sources.MAL.value
            break

        if site == "mangaupdates":
            # if its int, its an old MU ID
            if external_id.isdigit():
                # get the base36 encoded ID
                try:
                    external_id = kitsu_mu_mapping[external_id]
                except KeyError:  # ID not found in mapping
                    continue

            # decode the base36 encoded ID
            media_id = str(int(external_id, 36))
            source = Sources.MANGAUPDATES.value
            break

    # Farmagia (49333) shows MAL external_id == "anime"
    if not media_id or not media_id.isdigit():
        media_title = kitsu_metadata["attributes"]["canonicalTitle"]
        msg = f"{media_title}: No valid external ID found."
        raise MediaImportError(msg)

    image_url = get_image_url(kitsu_metadata)

    return Item.objects.get_or_create(
        media_id=media_id,
        source=source,
        media_type=media_type,
        defaults={
            "title": kitsu_metadata["attributes"]["canonicalTitle"],
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


def get_status(status):
    """Convert the status from Kitsu to the status used in the app."""
    status_mapping = {
        "completed": Media.Status.COMPLETED.value,
        "current": Media.Status.IN_PROGRESS.value,
        "planned": Media.Status.PLANNING.value,
        "on_hold": Media.Status.PAUSED.value,
        "dropped": Media.Status.DROPPED.value,
    }
    return status_mapping[status]
