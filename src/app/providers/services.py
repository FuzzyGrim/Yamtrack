import logging
import time

import requests
from django.conf import settings
from pyrate_limiter import RedisBucket
from redis import ConnectionPool
from requests.adapters import HTTPAdapter
from requests_ratelimiter import LimiterAdapter, LimiterSession

from app.models import MediaTypes, Sources
from app.providers import (
    comicvine,
    hardcover,
    igdb,
    mal,
    mangaupdates,
    manual,
    openlibrary,
    tmdb,
)

logger = logging.getLogger(__name__)


def get_redis_connection():
    """Return a Redis connection pool."""
    if settings.TESTING:
        import fakeredis  # noqa: PLC0415

        return fakeredis.FakeStrictRedis().connection_pool
    return ConnectionPool.from_url(settings.REDIS_URL)


redis_pool = get_redis_connection()

session = LimiterSession(
    per_second=5,
    bucket_class=RedisBucket,
    bucket_kwargs={"redis_pool": redis_pool, "bucket_name": "api"},
)

session.mount("http://", HTTPAdapter(max_retries=3))
session.mount("https://", HTTPAdapter(max_retries=3))

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
session.mount(
    "https://api.tvmaze.com",
    LimiterAdapter(per_second=2),
)
session.mount(
    "https://comicvine.gamespot.com/api",
    LimiterAdapter(per_hour=190),
)
session.mount(
    "https://openlibrary.org",
    LimiterAdapter(per_minute=20),
)
session.mount(
    "https://api.hardcover.app/v1/graphql",
    LimiterAdapter(per_minute=55),
)


class ProviderAPIError(Exception):
    """Exception raised when a provider API fails to respond."""

    def __init__(self, provider, error, details=None):
        """Initialize the exception with the provider name."""
        self.provider = provider
        self.status_code = error.response.status_code
        try:
            provider = Sources(provider).label
        except ValueError:
            provider = provider.title()

        logger.error("%s error: %s", provider, error.response.text)

        message = f"There was an error contacting the {provider} API"
        if details:
            message += f": {details}"
        message += ". Check the logs for more details."
        super().__init__(message)


def api_request(provider, method, url, params=None, data=None, headers=None):
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
        return response.json()

    except requests.exceptions.HTTPError as error:
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

        raise error from None


def get_media_metadata(
    media_type,
    media_id,
    source,
    season_numbers=None,
    episode_number=None,
):
    """Return the metadata for the selected media."""
    if source == Sources.MANUAL.value:
        if media_type == MediaTypes.SEASON.value:
            return manual.season(media_id, season_numbers[0])
        if media_type == MediaTypes.EPISODE.value:
            return manual.episode(media_id, season_numbers[0], episode_number)
        if media_type == "tv_with_seasons":
            media_type = MediaTypes.TV.value
        return manual.metadata(media_id, media_type)

    metadata_retrievers = {
        MediaTypes.ANIME.value: lambda: mal.anime(media_id),
        MediaTypes.MANGA.value: lambda: mangaupdates.manga(media_id)
        if source == Sources.MANGAUPDATES.value
        else mal.manga(media_id),
        MediaTypes.TV.value: lambda: tmdb.tv(media_id),
        "tv_with_seasons": lambda: tmdb.tv_with_seasons(media_id, season_numbers),
        MediaTypes.SEASON.value: lambda: tmdb.tv_with_seasons(media_id, season_numbers)[
            f"season/{season_numbers[0]}"
        ],
        MediaTypes.EPISODE.value: lambda: tmdb.episode(
            media_id,
            season_numbers[0],
            episode_number,
        ),
        MediaTypes.MOVIE.value: lambda: tmdb.movie(media_id),
        MediaTypes.GAME.value: lambda: igdb.game(media_id),
        MediaTypes.BOOK.value: lambda: hardcover.book(media_id)
        if source == Sources.HARDCOVER.value
        else openlibrary.book(media_id),
        MediaTypes.COMIC.value: lambda: comicvine.comic(media_id),
    }
    return metadata_retrievers[media_type]()


def search(media_type, query, page, source=None):
    """Search for media based on the query and return the results."""
    if media_type == MediaTypes.MANGA.value:
        if source == Sources.MANGAUPDATES.value:
            response = mangaupdates.search(query, page)
        else:
            response = mal.search(media_type, query, page)
    elif media_type == MediaTypes.ANIME.value:
        response = mal.search(media_type, query, page)
    elif media_type in (MediaTypes.TV.value, MediaTypes.MOVIE.value):
        response = tmdb.search(media_type, query, page)
    elif media_type == MediaTypes.GAME.value:
        response = igdb.search(query, page)
    elif media_type == MediaTypes.BOOK.value:
        if source == Sources.OPENLIBRARY.value:
            response = openlibrary.search(query, page)
        else:
            response = hardcover.search(query, page)
    elif media_type == MediaTypes.COMIC.value:
        response = comicvine.search(query, page)

    return response
