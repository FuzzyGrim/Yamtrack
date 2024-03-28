import logging
import time

import requests
from django.conf import settings

from app.providers import igdb, mal, tmdb

logger = logging.getLogger(__name__)


def api_request(method, url, params=None, data=None, headers=None):
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
        # handle rate limiting
        if error.response.status_code == requests.codes.too_many_requests:
            seconds_to_wait = int(error.response.headers["Retry-After"])
            logger.warning("Rate limited, waiting %s seconds", seconds_to_wait)
            time.sleep(seconds_to_wait)
            logger.info("Retrying request")
            return api_request(
                method,
                url,
                params=params,
                data=data,
                headers=headers,
            )

        raise  # re-raise for caller to handle

    return response.json()


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
