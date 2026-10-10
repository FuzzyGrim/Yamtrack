"""Helper module for Seerr (Overseerr/Jellyseerr) integration."""

import logging
from urllib.parse import urljoin

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class SeerrAPIError(Exception):
    """Exception raised when the Seerr API fails to respond."""


ERROR_CONNECTION = "Connection failed"
ERROR_TIMEOUT = "Request timed out"
ERROR_API_KEY = "Invalid API key"
ERROR_NOT_FOUND = "Media not found"
ERROR_RESPONSE = "Invalid response"


def _api_request(seerr_url, seerr_api_key, method, path, json_data=None):
    """Make an API request to the Seerr instance."""
    url = urljoin(seerr_url.rstrip("/") + "/", path.lstrip("/"))

    headers = {
        "X-Api-Key": seerr_api_key,
        "Content-Type": "application/json",
    }

    try:
        response = requests.request(
            method=method,
            url=url,
            headers=headers,
            json=json_data,
            timeout=settings.REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        if response.content:
            return response.json()
    except requests.exceptions.ConnectionError as error:
        logger.exception("Failed to connect to Seerr instance at %s", seerr_url)
        raise SeerrAPIError(ERROR_CONNECTION) from error
    except requests.exceptions.Timeout as error:
        logger.exception("Request to Seerr instance timed out")
        raise SeerrAPIError(ERROR_TIMEOUT) from error
    except requests.exceptions.HTTPError as error:
        status_code = error.response.status_code if error.response is not None else 0
        if status_code == requests.codes.unauthorized:
            raise SeerrAPIError(ERROR_API_KEY) from error
        if status_code == requests.codes.not_found:
            raise SeerrAPIError(ERROR_NOT_FOUND) from error
        logger.exception("Seerr API HTTP error: %s", status_code)
        msg = f"HTTP {status_code}"
        raise SeerrAPIError(msg) from error
    except requests.exceptions.JSONDecodeError as error:
        logger.exception("Failed to decode Seerr API response")
        raise SeerrAPIError(ERROR_RESPONSE) from error
    else:
        return {}


def check_connection(seerr_url, seerr_api_key):
    """Check if the Seerr instance is accessible.

    Returns True if the connection is successful, False otherwise.
    """
    try:
        _api_request(seerr_url, seerr_api_key, "GET", "api/v1/status")
    except SeerrAPIError:
        return False
    return True


def request_media(seerr_url, seerr_api_key, media_type, tmdb_id):
    """Request media on the Seerr instance.

    Args:
        seerr_url: The base URL of the Seerr instance.
        seerr_api_key: The API key for the Seerr instance.
        media_type: The type of media ('movie' or 'tv').
        tmdb_id: The TMDB ID of the media to request.

    Returns:
        The response from the Seerr API.

    Raises:
        SeerrAPIError: If the request fails.
    """
    data = {
        "mediaType": media_type,
        "mediaId": int(tmdb_id),
    }

    return _api_request(
        seerr_url,
        seerr_api_key,
        "POST",
        "api/v1/request",
        json_data=data,
    )
