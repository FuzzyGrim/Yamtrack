import logging

import requests
from django.conf import settings
from django.core.cache import cache

from app.providers import services

logger = logging.getLogger(__name__)

PROVIDER = "TVDB"
BASE_URL = "https://api4.thetvdb.com/v4"
ACCESS_TOKEN_CACHE_KEY = "tvdb_access_token"  # noqa: S105
ACCESS_TOKEN_TIMEOUT = 60 * 60 * 24 * 29  # tokens are valid for 1 month
TMDB_REMOTE_ID_TYPE = 12
TMDB_REMOTE_ID_SOURCE_NAME = "TheMovieDB.com"


def handle_error(error):
    """Handle TVDB API errors."""
    error_resp = error.response
    status_code = error_resp.status_code

    if status_code == requests.codes.unauthorized:
        logger.warning("TVDB access token is invalid or expired, refreshing")
        cache.delete(ACCESS_TOKEN_CACHE_KEY)
        return {"retry": True}

    raise services.ProviderAPIError(PROVIDER, error)


def get_access_token():
    """Return a cached TVDB access token."""
    if not settings.TVDB_API:
        logger.debug("Skipping TVDB lookup because TVDB_API is not configured")
        return None

    access_token = cache.get(ACCESS_TOKEN_CACHE_KEY)
    if access_token is None:
        payload = {"apikey": settings.TVDB_API}

        try:
            response = services.api_request(
                PROVIDER,
                "POST",
                f"{BASE_URL}/login",
                params=payload,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        access_token = response.get("data", {}).get("token")
        if not access_token:
            return None

        cache.set(
            ACCESS_TOKEN_CACHE_KEY,
            access_token,
            ACCESS_TOKEN_TIMEOUT,
        )

    return access_token


def episode(episode_id):
    """Return TVDB base metadata for a single episode ID."""
    cache_key = f"tvdb_episode_{episode_id}"
    cached_episode = cache.get(cache_key)
    if cached_episode is not None:
        return cached_episode

    access_token = get_access_token()
    if not access_token:
        return None

    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{BASE_URL}/episodes/{episode_id}"

    try:
        response = services.api_request(
            PROVIDER,
            "GET",
            url,
            headers=headers,
        )
    except requests.exceptions.HTTPError as error:
        error_resp = handle_error(error)
        if error_resp and error_resp.get("retry"):
            headers["Authorization"] = f"Bearer {get_access_token()}"
            response = services.api_request(
                PROVIDER,
                "GET",
                url,
                headers=headers,
            )
        else:
            return None

    episode_data = response.get("data", {})
    data = {
        "episode_id": episode_data.get("id"),
        "series_id": episode_data.get("seriesId"),
        "season_number": episode_data.get("seasonNumber"),
        "episode_number": episode_data.get("number"),
    }

    if not all(
        data[key] is not None
        for key in ("series_id", "season_number", "episode_number")
    ):
        logger.debug("TVDB episode metadata incomplete for episode ID %s", episode_id)
        return None

    cache.set(cache_key, data)
    return data


def series_tmdb_id(series_id):
    """Return the TMDB series ID from a TVDB series extended record."""
    cache_key = f"tvdb_series_tmdb_id_{series_id}"
    cached_tmdb_id = cache.get(cache_key)
    if cached_tmdb_id is not None:
        return cached_tmdb_id

    access_token = get_access_token()
    if not access_token:
        return None

    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{BASE_URL}/series/{series_id}/extended"

    try:
        response = services.api_request(
            PROVIDER,
            "GET",
            url,
            headers=headers,
        )
    except requests.exceptions.HTTPError as error:
        error_resp = handle_error(error)
        if error_resp and error_resp.get("retry"):
            headers["Authorization"] = f"Bearer {get_access_token()}"
            response = services.api_request(
                PROVIDER,
                "GET",
                url,
                headers=headers,
            )
        else:
            return None

    remote_ids = response.get("data", {}).get("remoteIds") or []
    tmdb_id = next(
        (
            remote_id.get("id")
            for remote_id in remote_ids
            if (
                remote_id.get("type") == TMDB_REMOTE_ID_TYPE
                or remote_id.get("sourceName") == TMDB_REMOTE_ID_SOURCE_NAME
            )
        ),
        None,
    )

    if not tmdb_id:
        logger.debug("TVDB series metadata has no TMDB ID for series ID %s", series_id)
        return None

    cache.set(cache_key, tmdb_id)
    return tmdb_id
