import logging
import time

import requests
from django.conf import settings

from app.providers import igdb, mal, tmdb

logger = logging.getLogger(__name__)


def api_request(provider, method, url, params=None, data=None, headers=None):  # noqa: PLR0913
    """Make a request to the API and return the response as a dictionary."""
    try:
        if method == "GET":
            response = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=settings.REQUEST_TIMEOUT,
            )
        elif method == "POST":
            response = requests.post(
                url,
                data=data,
                json=params,
                headers=headers,
                timeout=settings.REQUEST_TIMEOUT,
            )

        response.raise_for_status()

    except requests.exceptions.HTTPError as error:
        args = (provider, method, url, params, data, headers)
        request_error_handling(error, *args)

    return response.json()


def request_error_handling(error, *args):
    """Handle errors when making a request to the API."""
    # unpack the arguments
    provider, method, url, params, data, headers = args

    # handle rate limiting
    if error.response.status_code == requests.codes.too_many_requests:
        seconds_to_wait = int(error.response.headers["Retry-After"])
        logger.warning("Rate limited, waiting %s seconds", seconds_to_wait)
        time.sleep(seconds_to_wait)
        logger.info("Retrying request")
        return api_request(
            provider,
            method,
            url,
            params=params,
            data=data,
            headers=headers,
        )

    if provider == "IGDB" and error.response.status_code == requests.codes.bad_request:
        message = error.response.json()["message"]
        logger.exception("IGDB bad request: %s", message)

    if provider == "TMDB" and error.response.status_code == requests.codes.unauthorized:
        message = error.response.json()["status_message"]
        logger.exception("TMDB unauthorized: %s", message)

    if provider == "MAL":
        if error.response.status_code == requests.codes.forbidden:
            logger.exception("MAL forbidden: is the API key set?")
        elif (
            error.response.status_code == requests.codes.bad_request
            and error.response.json()["message"] == "Invalid client id"
        ):
            logger.exception("MAL bad request: check the API key")

    raise  # re-raise for caller to handle


def get_media_metadata(media_type, media_id):
    """Return the metadata for the selected media."""
    if media_type == "anime":
        media_metadata = mal.anime(media_id)
    elif media_type == "manga":
        media_metadata = mal.manga(media_id)
    elif media_type == "tv":
        media_metadata = tmdb.tv(media_id)
    elif media_type == "movie":
        media_metadata = tmdb.movie(media_id)
    elif media_type == "game":
        media_metadata = igdb.game(media_id)

    return media_metadata
