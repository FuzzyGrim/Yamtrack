import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from django.conf import settings
from django.core.cache import cache

from app import helpers
from app.models import MediaTypes, Sources
from app.providers import services

logger = logging.getLogger(__name__)
base_url = "https://api.myanimelist.net/v2"
base_fields = "title,main_picture,media_type,start_date,end_date,synopsis,status,genres,mean,num_scoring_users,recommendations"  # noqa: E501


def handle_error(error):
    """Handle MAL API errors."""
    error_resp = error.response
    status_code = error_resp.status_code

    try:
        error_json = error_resp.json()
    except requests.exceptions.JSONDecodeError as json_error:
        logger.exception("Failed to decode JSON response")
        raise services.ProviderAPIError(Sources.MAL.value, error) from json_error

    if status_code == requests.codes.forbidden:
        details = "API key is missing"
        raise services.ProviderAPIError(Sources.MAL.value, error, details)
    if status_code == requests.codes.bad_request:
        error_message = error_json.get("message")
        if error_message == "Invalid client id":
            details = "Invalid API key"
            raise services.ProviderAPIError(Sources.MAL.value, error, details)
        if error_message == "invalid q":
            return {"data": []}

    raise services.ProviderAPIError(Sources.MAL.value, error)


def search(media_type, query, page):
    """Search for media on MyAnimeList."""
    cache_key = f"search_{Sources.MAL.value}_{media_type}_{query}_{page}"
    data = cache.get(cache_key)

    if data is None:
        url = f"{base_url}/{media_type}"
        params = {
            "q": query,
            "fields": "media_type",
            "limit": settings.PER_PAGE,
        }
        if settings.MAL_NSFW:
            params["nsfw"] = "true"

        try:
            response = services.api_request(
                Sources.MAL.value,
                "GET",
                url,
                params=params,
                headers={"X-MAL-CLIENT-ID": settings.MAL_API},
            )
        except requests.exceptions.HTTPError as error:
            response = handle_error(error)

        response = response["data"]
        results = [
            {
                "media_id": media["node"]["id"],
                "source": Sources.MAL.value,
                "media_type": media_type,
                "title": media["node"]["title"],
                "image": get_image_url(media["node"]),
            }
            for media in response
        ]

        data = helpers.format_search_response(
            page,
            100,
            len(results),
            results,
        )

        cache.set(cache_key, data)

    return data


def anime(media_id):
    """Return the metadata for the selected anime or manga from MyAnimeList."""
    cache_key = f"{Sources.MAL.value}_{MediaTypes.ANIME.value}_{media_id}"
    data = cache.get(cache_key)

    if data is None:
        url = f"{base_url}/anime/{media_id}"
        params = {
            "fields": f"{base_fields},num_episodes,average_episode_duration,studios,start_season,broadcast,source,related_anime",  # noqa: E501
        }

        try:
            response = services.api_request(
                Sources.MAL.value,
                "GET",
                url,
                params=params,
                headers={"X-MAL-CLIENT-ID": settings.MAL_API},
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        num_episodes = get_number_of_episodes(response)

        data = {
            "media_id": media_id,
            "source": Sources.MAL.value,
            "source_url": f"https://myanimelist.net/anime/{media_id}",
            "media_type": MediaTypes.ANIME.value,
            "title": response["title"],
            "max_progress": num_episodes,
            "image": get_image_url(response),
            "synopsis": get_synopsis(response),
            "genres": get_genres(response),
            "score": get_score(response),
            "score_count": get_score_count(response),
            "details": {
                "format": get_format(response),
                "start_date": response.get("start_date"),
                "end_date": response.get("end_date"),
                "status": get_readable_status(response),
                "episodes": num_episodes,
                "runtime": get_runtime(response),
                "studios": get_studios(response),
                "season": get_season(response),
                "broadcast": get_broadcast(response),
                "source": get_source(response),
            },
            "related": {
                "related_anime": get_related(
                    response.get("related_anime"),
                    MediaTypes.ANIME.value,
                ),
                "recommendations": get_related(
                    response.get("recommendations"),
                    MediaTypes.ANIME.value,
                ),
            },
        }

        cache.set(cache_key, data)

    return data


def manga(media_id):
    """Return the metadata for the selected anime or manga from MyAnimeList."""
    cache_key = f"{Sources.MAL.value}_{MediaTypes.MANGA.value}_{media_id}"
    data = cache.get(cache_key)

    if data is None:
        url = f"{base_url}/manga/{media_id}"
        params = {
            "fields": f"{base_fields},num_chapters,related_manga,recommendations",
        }

        try:
            response = services.api_request(
                Sources.MAL.value,
                "GET",
                url,
                params=params,
                headers={"X-MAL-CLIENT-ID": settings.MAL_API},
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        num_chapters = get_number_of_episodes(response)

        data = {
            "media_id": media_id,
            "source": Sources.MAL.value,
            "source_url": f"https://myanimelist.net/manga/{media_id}",
            "media_type": MediaTypes.MANGA.value,
            "title": response["title"],
            "image": get_image_url(response),
            "synopsis": get_synopsis(response),
            "max_progress": num_chapters,
            "genres": get_genres(response),
            "score": get_score(response),
            "score_count": get_score_count(response),
            "details": {
                "format": get_format(response),
                "start_date": response.get("start_date"),
                "end_date": response.get("end_date"),
                "status": get_readable_status(response),
                "number_of_chapters": num_chapters,
            },
            "related": {
                "related_manga": get_related(
                    response.get("related_manga"),
                    MediaTypes.MANGA.value,
                ),
                "recommendations": get_related(
                    response.get("recommendations"),
                    MediaTypes.MANGA.value,
                ),
            },
        }

        cache.set(cache_key, data)

    return data


def get_format(response):
    """Return the original type of the media."""
    media_format = response["media_type"]

    # MAL return tv in metadata for anime
    if media_format == "tv":
        return "Anime"
    if media_format in ("ova", "ona"):
        return media_format.upper()
    return media_format.replace("_", " ").title()


def get_image_url(response):
    """Return the image URL for the media."""
    # when no picture, main_picture is not present in the response
    # e.g anime: 38869
    try:
        return response["main_picture"]["large"]
    except KeyError:
        return settings.IMG_NONE


def get_readable_status(response):
    """Return the status in human-readable format."""
    # Map status to human-readable values
    status_map = {
        "finished_airing": "Finished",
        "currently_airing": "Airing",
        "not_yet_aired": "Upcoming",
        "finished": "Finished",
        "currently_publishing": "Publishing",
        "not_yet_published": "Upcoming",
        "on_hiatus": "On Hiatus",
        "discontinued": "Discontinued",
    }
    if response["status"] in status_map:
        return status_map[response["status"]]
    return response["status"].replace("_", " ").title()


def get_synopsis(response):
    """Add the synopsis to the response."""
    # when no synopsis, value from response is empty string
    # e.g manga: 160219
    if response["synopsis"] == "":
        return "No synopsis available."
    return response["synopsis"]


def get_number_of_episodes(response):
    """Return the number of episodes for the media."""
    # when unknown episodes, value from response is 0
    # e.g manga: 160219
    try:
        episodes = response["num_episodes"]
    except KeyError:
        episodes = response["num_chapters"]

    return episodes if episodes != 0 else None


def get_runtime(response):
    """Return the average episode duration."""
    # when unknown duration, value from response is 0
    # e.g anime: 43333
    duration = response["average_episode_duration"]

    # Convert average_episode_duration to hours and minutes
    if duration:
        # duration are in seconds
        hours, minutes = divmod(int(duration / 60), 60)
        return f"{hours}h {minutes}m" if hours > 0 else f"{minutes} min"
    return None


def get_genres(response):
    """Return the genres for the media."""
    if response["genres"]:
        return [genre["name"] for genre in response["genres"]]
    return None


def get_studios(response):
    """Return the studios for the media."""
    # when unknown studio, studios is an empty list
    # e.g anime: 43333

    if response["studios"]:
        return [studio["name"] for studio in response["studios"]]
    return None


def get_season(response):
    """Return the season for the media."""
    # when unknown start season, no start_season key in response
    # e.g anime: 43333
    try:
        season = response["start_season"]
        return f"{season['season'].title()} {season['year']}"
    except KeyError:
        return None


def get_broadcast(response):
    """Return the broadcast day and time for the media."""
    start_date = response.get("start_date")
    if not start_date:
        return None

    # when unknown broadcast, value is not present in the response
    # e.g anime: 38869
    broadcast = response.get("broadcast")
    if not broadcast:
        return None

    # when unknown start time, value is not present in the broadcast dict
    start_time = broadcast.get("start_time") if broadcast else None
    if not start_time:
        return None

    japan_timezone = ZoneInfo("Asia/Tokyo")
    # Try parsing with different date formats
    try:
        date_obj = datetime.strptime(start_date, "%Y-%m-%d").replace(
            tzinfo=japan_timezone,
        )
    except ValueError:
        date_obj = datetime.strptime(start_date, "%Y-%m").replace(tzinfo=japan_timezone)

    broadcast_time_japan = datetime.strptime(
        f"{date_obj.strftime('%Y-%m-%d')} {start_time}",
        "%Y-%m-%d %H:%M",
    ).replace(tzinfo=japan_timezone)

    broadcast_time_local = broadcast_time_japan.astimezone(settings.TZ)
    return broadcast_time_local.strftime("%A %H:%M")


def get_source(response):
    """Return the source for the media."""
    # when unknown source, value from response is empty string
    # e.g anime: 32253
    try:
        return response["source"].replace("_", " ").title()
    except KeyError:
        return None


def get_score(response):
    """Return the score for the media."""
    # when num_scoring_users is small, the response does not include this field.
    try:
        return round(response["mean"], 1)
    except KeyError:
        return None


def get_score_count(response):
    """Return the score count for the media."""
    if get_score(response):
        return response["num_scoring_users"]
    return 0


def get_related(related_medias, media_type):
    """Return list of related media for the selected media."""
    if related_medias:
        return [
            {
                "media_id": media["node"]["id"],
                "source": Sources.MAL.value,
                "title": media["node"]["title"],
                "media_type": media_type,
                "image": get_image_url(media["node"]),
            }
            for media in related_medias
        ]
    return []
