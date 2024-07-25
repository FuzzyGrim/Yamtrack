import logging
import time

import requests
from django.conf import settings
from django.core.cache import cache

from app.providers import igdb, mal, tmdb

logger = logging.getLogger(__name__)
session = requests.Session()


def api_request(provider, method, url, params=None, data=None, headers=None):  # noqa: PLR0913
    """Make a request to the API and return the response as a dictionary."""
    try:
        request_kwargs = {
            "url": url,
            "headers": headers,
            "timeout": settings.REQUEST_TIMEOUT,
        }

        if method == "GET":
            request_kwargs["params"] = params
            request_func = session.get
        elif method == "POST":
            request_kwargs["data"] = data
            request_kwargs["json"] = params
            request_func = session.post

        response = request_func(**request_kwargs)
        response.raise_for_status()

    except requests.exceptions.HTTPError as error:
        args = (provider, method, url, params, data, headers)
        request_error_handling(error, *args)

    return response.json()


def request_error_handling(error, *args):
    """Handle errors when making a request to the API."""
    # unpack the arguments
    provider, method, url, params, data, headers = args

    error_resp = error.response
    error_json = error_resp.json()
    status_code = error_resp.status_code

    # handle rate limiting
    if status_code == requests.codes.too_many_requests:
        seconds_to_wait = int(error_resp.headers["Retry-After"])
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

    if provider == "IGDB":
        # invalid access token, expired or revoked
        if status_code == requests.codes.unauthorized:
            logger.warning("Invalid IGDB access token, refreshing")
            cache.delete("igdb_access_token")
            igdb.get_access_token()

            # retry the request with the new access token
            return api_request(
                provider,
                method,
                url,
                params=params,
                data=data,
                headers=headers,
            )

        # invalid keys
        if status_code == requests.codes.bad_request:
            message = error_json["message"]
            logger.error("IGDB bad request: %s", message)

    if provider == "TMDB" and status_code == requests.codes.unauthorized:
        message = error_json["status_message"]
        logger.error("TMDB unauthorized: %s", message)

    if provider == "MAL":
        if status_code == requests.codes.forbidden:
            logger.error("MAL forbidden: is the API key set?")
        elif (
            status_code == requests.codes.bad_request
            and error_json["message"] == "Invalid client id"
        ):
            logger.error("MAL bad request: check the API key")

    raise error  # re-raise the error if it's not handled


def get_media_metadata(media_type, media_id, season_number=None):
    """Return the metadata for the selected media."""
    if media_type == "anime":
        media_metadata = mal.anime(media_id)
    elif media_type == "manga":
        media_metadata = mal.manga(media_id)
    elif media_type == "tv":
        media_metadata = tmdb.tv(media_id)
    elif media_type == "season":
        tv_metadata = tmdb.tv_with_seasons(media_id, [season_number])
        media_metadata = tv_metadata[f"season/{season_number}"]
        media_metadata["tv_title"] = tv_metadata["title"]
    elif media_type == "movie":
        media_metadata = tmdb.movie(media_id)
    elif media_type == "game":
        media_metadata = igdb.game(media_id)

    return media_metadata
