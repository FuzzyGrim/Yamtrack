from __future__ import annotations

import logging
import time

import requests
from django.conf import settings

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

    # rate limit exceeded
    if response.status_code == 429:
        seconds_to_wait = int(response.headers["Retry-After"])
        time.sleep(seconds_to_wait)
        return api_request(url, method, json)

    response.raise_for_status()

    return response.json()
