import logging
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone as django_timezone

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
            "offset": (page - 1) * settings.PER_PAGE,
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
            settings.PER_PAGE,
            settings.PER_PAGE * 3,  # MAL has no limit, we set max of 3 pages
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
    # when unknown genres, genres key is not present in the response
    # e.g manga: 151971
    if response.get("genres"):
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


# MAL's v2 broadcast object is only {day_of_the_week, start_time} -- it carries
# no timezone, so one has to be assumed. It is the Japanese TV slot: Jikan,
# reading the same source, labels it "Asia/Tokyo" on every currently-airing
# title that has a slot at all (titles without one, such as films, simply omit
# it and are skipped below). If MAL ever reports a zone, read it here.
BROADCAST_TIMEZONE = ZoneInfo("Asia/Tokyo")
WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def get_broadcast(response):
    """Return the next broadcast slot as a UTC instant, or None.

    An instant rather than a display string: this is shared cached metadata and
    the weekday itself moves between timezones, so only the browser can render
    it for its viewer.
    """
    # when unknown broadcast, value is not present in the response
    # e.g anime: 38869
    broadcast = response.get("broadcast") or {}

    # when unknown start time, value is not present in the broadcast dict
    start_time = broadcast.get("start_time")
    # MAL gives the airing weekday outright. It is not the weekday of
    # start_date: One Piece premiered on a Wednesday but airs on Sundays.
    weekday = WEEKDAYS.get((broadcast.get("day_of_the_week") or "").lower())
    if not start_time or weekday is None:
        return None

    try:
        slot = time.fromisoformat(start_time)
    except ValueError:
        logger.warning("Unparseable MAL broadcast time %r", start_time)
        return None

    # The slot is a Japanese weekday and wall clock, so it can only be resolved
    # to an instant in Japan's frame -- UTC's current weekday is not always
    # Japan's. Everything downstream of this function is UTC again.
    now = django_timezone.now().astimezone(BROADCAST_TIMEZONE)
    ahead = (weekday - now.weekday()) % 7
    # Anchored on the next occurrence rather than a fixed week, so the viewer's
    # own DST state is right when the browser renders it.
    occurrence = datetime.combine(
        now.date() + timedelta(days=ahead),
        slot,
        tzinfo=BROADCAST_TIMEZONE,
    )
    if occurrence < now:
        occurrence += timedelta(days=7)

    return occurrence.astimezone(UTC)


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
