import asyncio
import logging
import re

import aiohttp
import requests
from django.conf import settings
from django.core.cache import cache

from app import helpers
from app.models import MediaTypes, Sources
from app.providers import services

logger = logging.getLogger(__name__)

base_url = "https://api.mangaupdates.com/v1"


def handle_error(error):
    """Handle MangaUpdates API errors."""
    error_resp = error.response
    status_code = error_resp.status_code

    try:
        error_json = error_resp.json()
    except requests.exceptions.JSONDecodeError as json_error:
        logger.exception("Failed to decode JSON response")
        raise services.ProviderAPIError(
            Sources.MANGAUPDATES.value,
            error,
        ) from json_error

    if status_code == requests.codes.bad_request:
        search_error = error_json["context"].get("search")
        if search_error:
            message = search_error[0]["errors"][0]
            if message == '"" must have a length between 1 and 400':
                return {"results": [], "total_hits": 0}

    raise services.ProviderAPIError(
        Sources.MANGAUPDATES.value,
        error,
    )


def search(query, page):
    """Search for media on MangaUpdates."""
    cache_key = (
        f"search_{Sources.MANGAUPDATES.value}_{MediaTypes.MANGA.value}_{query}_{page}"
    )
    data = cache.get(cache_key)

    if data is None:
        url = f"{base_url}/series/search"
        per_page = 30
        params = {
            "search": query,
            "stype": "title",
            "perpage": per_page,
            "page": page,
        }

        if not settings.MAL_NSFW:
            params["exclude_genre"] = [
                "Adult",
                "Hentai",
                "Doujinshi",
            ]

        try:
            response = services.api_request(
                Sources.MANGAUPDATES.value,
                "POST",
                url,
                params=params,
            )
        except requests.exceptions.HTTPError as error:
            response = handle_error(error)

        results = [
            {
                "media_id": media["record"]["series_id"],
                "source": Sources.MANGAUPDATES.value,
                "media_type": MediaTypes.MANGA.value,
                "title": media["record"]["title"],
                "image": get_image_url(media["record"]),
            }
            for media in response["results"]
        ]

        total_results = response["total_hits"]
        data = helpers.format_search_response(
            page,
            per_page,
            total_results,
            results,
        )

        cache.set(cache_key, data)

    return data


def manga(media_id):
    """Get metadata for a manga from MangaUpdates."""
    return asyncio.run(async_manga(media_id))


async def async_manga(media_id):
    """Asynchronous implementation of manga metadata retrieval."""
    cache_key = f"{Sources.MANGAUPDATES.value}_{MediaTypes.MANGA.value}_{media_id}"
    data = cache.get(cache_key)

    if data is None:
        url = f"{base_url}/series/{media_id}"

        try:
            response = services.api_request(Sources.MANGAUPDATES.value, "GET", url)
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        # Run related_manga and recommendations concurrently
        related_task = asyncio.create_task(
            get_related_series(response["related_series"]),
        )
        recommendations_task = asyncio.create_task(
            get_recommendations(response["recommendations"]),
        )

        data = {
            "media_id": media_id,
            "source": Sources.MANGAUPDATES.value,
            "source_url": response["url"],
            "media_type": MediaTypes.MANGA.value,
            "title": response["title"],
            "image": get_image_url(response),
            "synopsis": response["description"],
            "max_progress": get_max_progress(response),
            "genres": get_genres(response["genres"]),
            "score": get_score(response["bayesian_rating"]),
            "score_count": response["rating_votes"],
            "details": {
                "format": response["type"],
                "authors": get_authors(response["authors"]),
                "year": response["year"],
                "status_in_country_of_origin": get_status(response["status"]),
                "latest_chapter_translated": response["latest_chapter"],
            },
            "related": {
                "related_manga": await related_task,
                "recommendations": await recommendations_task,
            },
        }

        cache.set(cache_key, data)

    return data


def get_image_url(response):
    """Get the image URL for a media item."""
    # when no image, value from response is null
    url = response["image"]["url"]["original"]
    return url or settings.IMG_NONE


def get_max_progress(response):
    """Get the maximum progress if the media is completed."""
    if response["completed"]:
        return response["latest_chapter"]
    return None


def get_genres(genres):
    """Return the genres for the media."""
    if genres:
        return [item["genre"] for item in genres]
    return None


def get_authors(authors):
    """Get the authors for a media item."""
    if authors:
        return [item["name"] for item in authors]
    return None


def get_status(status):
    """Return the status of the media."""
    # can be null 67117539345
    # e.g berserk 51239621230 needs parsing
    if status:
        pattern = r"(\d+\s+Volumes\s+\([^)]+\))"
        match = re.search(pattern, status)
        if match:
            return match.group(1)
    return status


def get_score(score):
    """Return the score for the media."""
    # can be null 29732162445
    if score:
        return round(score, 1)
    return None


async def get_related_series(related):
    """Return list of related media for the selected media asynchronously."""
    async with aiohttp.ClientSession() as session:
        tasks = [
            fetch_series_data(
                session,
                f"{base_url}/series/{item['related_series_id']}",
                item,
            )
            for item in related
            if item["related_series_name"]
        ]
        results = await asyncio.gather(*tasks)
    return [item for item in results if item is not None]


async def get_recommendations(recommendations):
    """Return list of recommended media for the selected media asynchronously."""
    async with aiohttp.ClientSession() as session:
        tasks = [
            fetch_series_data(session, f"{base_url}/series/{item['series_id']}", item)
            for item in recommendations
            if item["series_name"]
        ]
        results = await asyncio.gather(*tasks)
    return [item for item in results if item is not None]


async def fetch_series_data(session, url, item):
    """Fetch series data asynchronously."""
    async with session.get(url) as response:
        if response.status == requests.codes.ok:
            data = await response.json()
            image = get_image_url(data)
            return {
                "source": Sources.MANGAUPDATES.value,
                "media_id": item.get("related_series_id") or item.get("series_id"),
                "media_type": MediaTypes.MANGA.value,
                "title": item.get("related_series_name") or item.get("series_name"),
                "image": image,
            }
    return None
