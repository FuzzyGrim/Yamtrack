from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

import requests
from django.conf import settings

if TYPE_CHECKING:
    from django.http import HttpRequest

logger = logging.getLogger(__name__)


def api_request(
    url: str,
    method: str,
    headers: dict | None = None,
    json: dict | None = None,
) -> dict:
    """Make a request to the API and return the response as a dictionary."""

    if method == "GET":
        response = requests.get(url, headers=headers, timeout=settings.REQUEST_TIMEOUT)
    elif method == "POST":
        response = requests.post(url, json=json, timeout=settings.REQUEST_TIMEOUT)

    if response.status_code != 200:
        if "anilist" in url:
            message = response.json().get("errors")[0].get("message")
        elif "tmdb" in url:
            message = response.json().get("status_message")
        elif (
            "myanimelist" in url and response.json().get("message") == "invalid q"
        ):  # when no results are found
            return []
        else:
            message = (
                f"Request failed with status code {response.status_code} for {url}"
            )

        logger.error(
            "Request failed with status code %s for %s",
            response.status_code,
            url,
        )

        # rate limit exceeded
        if response.status_code == 429:
            seconds_to_wait = int(response.headers["Retry-After"])
            time.sleep(seconds_to_wait)
            return api_request(url, method, json)

        raise ValueError(message)

    return response.json()


def get_client_ip(request: HttpRequest) -> str:
    """Return the client's IP address.

    Used when logging for user registration and login.
    """

    # get the user's IP address
    ip_address = request.META.get("HTTP_X_FORWARDED_FOR")

    # if the IP address is not available in HTTP_X_FORWARDED_FOR
    if not ip_address:
        ip_address = request.META.get("REMOTE_ADDR")

    return ip_address


def media_type_mapper(media_type: str) -> dict:
    """Map the media type to its corresponding model, form and other properties."""

    media_mapping = {
        "manga": {
            "list_layout": "app/media_table.html",
            "sort_choices": [
                ("score", "Score"),
                ("title", "Title"),
                ("progress", "Progress"),
                ("start_date", "Start Date"),
                ("end_date", "End Date"),
            ],
        },
        "anime": {
            "list_layout": "app/media_table.html",
            "sort_choices": [
                ("score", "Score"),
                ("title", "Title"),
                ("progress", "Progress"),
                ("start_date", "Start Date"),
                ("end_date", "End Date"),
            ],
        },
        "movie": {
            "list_layout": "app/media_grid.html",
            "sort_choices": [
                ("score", "Score"),
                ("title", "Title"),
                ("progress", "Progress"),
                ("start_date", "Start Date"),
                ("end_date", "End Date"),
            ],
        },
        "tv": {
            "list_layout": "app/media_grid.html",
            "sort_choices": [
                ("score", "Score"),
                ("title", "Title"),
            ],
        },
        "season": {
            "list_layout": "app/media_grid.html",
            "sort_choices": [
                ("score", "Score"),
                ("title", "Title"),
                ("progress", "Progress"),
                ("start_date", "Start Date"),
                ("end_date", "End Date"),
            ],
        },
    }
    return media_mapping[media_type]
