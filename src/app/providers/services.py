import logging
import time

import requests
from django.conf import settings

from app.providers import igdb, mal, tmdb

logger = logging.getLogger(__name__)


def api_request(method, url, params=None, data=None, headers=None):
    """Make a request to the API and return the response as a dictionary."""
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

    rate_limit_code = 429
    if response.status_code == rate_limit_code:
        seconds_to_wait = int(response.headers["Retry-After"])
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

    response.raise_for_status()

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
