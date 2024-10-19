import logging
import time
from functools import wraps

import requests
from django.conf import settings
from django.core.cache import cache
from pyrate_limiter import RedisBucket
from redis import ConnectionPool
from requests_ratelimiter import LimiterAdapter, LimiterSession

from app.providers import igdb, mal, mangaupdates, manual, tmdb

logger = logging.getLogger(__name__)


def get_redis_connection():
    """Return a Redis connection pool."""
    if settings.TESTING:
        import fakeredis

        return fakeredis.FakeStrictRedis().connection_pool
    return ConnectionPool.from_url(settings.REDIS_URL)


redis_pool = get_redis_connection()

session = LimiterSession(
    per_second=5,
    bucket_class=RedisBucket,
    bucket_kwargs={"redis_pool": redis_pool, "bucket_name": "api"},
)
session.mount(
    "https://api.myanimelist.net/v2",
    LimiterAdapter(per_minute=30),
)
session.mount(
    "https://graphql.anilist.co",
    LimiterAdapter(per_minute=85),
)
session.mount(
    "https://api.igdb.com/v4",
    LimiterAdapter(per_second=3),
)


def retry_on_error(delay=1):
    """Retry a function if it raises a RequestException."""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except requests.exceptions.RequestException:
                msg = f"Request failed. Retrying in {delay} seconds."
                logger.warning(msg)
                time.sleep(delay)
                try:
                    return func(*args, **kwargs)
                except requests.exceptions.RequestException:
                    msg = "Request failed after retry. Raising error."
                    logger.error(msg)  # noqa: TRY400
                    raise

        return wrapper

    return decorator


@retry_on_error(delay=1)
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
        json_response = response.json()

    except requests.exceptions.HTTPError as error:
        args = (provider, method, url, params, data, headers)
        json_response = request_error_handling(error, *args)

    return json_response


def request_error_handling(error, *args):
    """Handle errors when making a request to the API."""
    # unpack the arguments
    provider, method, url, params, data, headers = args

    error_resp = error.response
    status_code = error_resp.status_code

    # handle rate limiting
    if status_code == requests.codes.too_many_requests:
        seconds_to_wait = int(error_resp.headers["Retry-After"])
        logger.warning("Rate limited, waiting %s seconds", seconds_to_wait)
        time.sleep(seconds_to_wait + 3)
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
            error_json = error_resp.json()
            message = error_json["message"]
            logger.error("IGDB bad request: %s", message)

    if provider == "TMDB" and status_code == requests.codes.unauthorized:
        error_json = error_resp.json()
        message = error_json["status_message"]
        logger.error("TMDB unauthorized: %s", message)

    if provider == "MAL":
        error_json = error_resp.json()
        if status_code == requests.codes.forbidden:
            logger.error("MAL forbidden: is the API key set?")
        elif (
            status_code == requests.codes.bad_request
            and error_json["message"] == "Invalid client id"
        ):
            logger.error("MAL bad request: check the API key")

    raise error  # re-raise the error if it's not handled


def get_media_metadata(media_type, media_id, source, season_number=None):
    """Return the metadata for the selected media."""
    if source == "manual":
        return manual.metadata(media_id)

    metadata_retrievers = {
        "anime": lambda: mal.anime(media_id),
        "manga": lambda: mangaupdates.manga(media_id)
        if source == "mangaupdates"
        else mal.manga(media_id),
        "tv": lambda: tmdb.tv(media_id),
        "season": lambda: tmdb.tv_with_seasons(media_id, season_number)[
            [f"season/{season_number}"]
        ],
        "movie": lambda: tmdb.movie(media_id),
        "game": lambda: igdb.game(media_id),
    }
    return metadata_retrievers[media_type]()
