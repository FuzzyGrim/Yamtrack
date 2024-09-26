import datetime
import json
import logging
from pathlib import Path

import app
from app.models import Item
from django.apps import apps
from django.conf import settings
from django.core.cache import cache

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
            except ValueError as e:
                warning_message += f"{e}\n"
            else:
                bulk_data.append(instance)

    num_before = model.objects.filter(user=user).count()
    helpers.bulk_chunk_import(bulk_data, model, user)
    num_after = model.objects.filter(user=user).count()

    num_imported = num_after - num_before
    logger.info("Imported %s %s", num_imported, media_type)

    return num_imported, warning_message


def process_entry(  # noqa: PLR0913
    entry,
    media_type,
    media_lookup,
    mapping_lookup,
    kitsu_mu_mapping,
    user,
):
    """Process a single entry and return the model instance."""
    attributes = entry["attributes"]
    kitsu_id = entry["relationships"][media_type]["data"]["id"]
    kitsu_metadata = media_lookup[kitsu_id]

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
        start_date=get_date(attributes["startedAt"]),
        end_date=get_date(attributes["finishedAt"]),
        notes=attributes["notes"] or "",  # sometimes returns None instead of ""
    )

    if attributes["reconsuming"]:
        instance.status = app.models.STATUS_REPEATING

    return instance


def create_or_get_item(media_type, kitsu_metadata, mapping_lookup, kitsu_mu_mapping):
    """Create or get an Item instance."""
    sites = [
        f"myanimelist/{media_type}",
        "mangaupdates",
        "thetvdb/season",
        "thetvdb",
        "thetvdb/series",
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
            season_number = None
            source = "mal"
            break

        if site == "mangaupdates":
            # if its int, its an old MU ID
            if isinstance(external_id, int):
                # get the base36 encoded ID
                external_id = kitsu_mu_mapping[external_id]

            # decode the base36 encoded ID
            media_id = int(external_id, 36)
            media_type = "manga"
            season_number = None
            source = "mangaupdates"
            break

        if "thetvdb" in site:
            try:
                media_id, media_type, season_number = convert_tvdb_to_tmdb(
                    external_id,
                    site,
                )
                source = "tmdb"
                break
            except IndexError:  # id cant be found on TMDB
                continue

    if not media_id:
        media_title = kitsu_metadata["attributes"]["canonicalTitle"]
        msg = f"Couldn't find a matching ID for {media_title}."
        raise ValueError(msg)

    image_url = get_image_url(kitsu_metadata)

    return Item.objects.get_or_create(
        media_id=media_id,
        source=source,
        media_type=media_type,
        season_number=season_number,
        defaults={
            "title": kitsu_metadata["attributes"]["canonicalTitle"],
            "image": image_url,
        },
    )[0]


def convert_tvdb_to_tmdb(tvdb_id, source):
    """Convert a TVDB ID to a TMDB ID."""
    cache_key = f"tvdb_to_tmdb_{tvdb_id}_{source}"
    cached_result = cache.get(cache_key)

    if cached_result:
        return cached_result

    season_number = None
    if "/" in tvdb_id:
        tvdb_id, season_number = tvdb_id.split("/")
        season_number = int(season_number)
        media_type = "season"
    else:
        media_type = "tv"

    url = f"https://api.themoviedb.org/3/find/{tvdb_id}"
    params = {
        "api_key": settings.TMDB_API,
        "language": settings.TMDB_LANG,
        "external_source": "tvdb_id",
    }

    data = app.providers.services.api_request("TMDB", "GET", url, params=params)

    if source == "thetvdb/season":
        tmdb_id = data["tv_season_results"][0]["show_id"]
        media_type = "season"
        season_number = data["tv_season_results"][0]["season_number"]
        result = (tmdb_id, media_type, season_number)
    else:
        tmdb_id = data["tv_results"][0]["id"]
        result = (tmdb_id, media_type, season_number)

    cache.set(cache_key, result)
    return result


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
