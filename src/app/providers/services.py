import logging
import time

import requests
from django.conf import settings

from app.providers import mal, tmdb

logger = logging.getLogger(__name__)


def api_request(url, method, headers=None, json=None):
    """Make a request to the API and return the response as a dictionary."""
    if method == "GET":
        response = requests.get(url, headers=headers, timeout=settings.REQUEST_TIMEOUT)
    elif method == "POST":
        response = requests.post(url, json=json, timeout=settings.REQUEST_TIMEOUT)

    # rate limit exceeded
    if response.status_code == 429:
        seconds_to_wait = int(response.headers["Retry-After"])
        time.sleep(seconds_to_wait)
        return api_request(url, method, json)

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

    return media_metadata
