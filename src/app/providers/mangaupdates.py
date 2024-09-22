import asyncio
import re

import aiohttp
import requests
from django.conf import settings
from django.core.cache import cache

from app.providers import services

base_url = "https://api.mangaupdates.com/v1"


def search(query):
    """Search for media on MangaUpdates."""
    data = cache.get(f"search_mangaupdates_{query}")

    if data is None:
        url = f"{base_url}/series/search"
        params = {
            "search": query,
            "stype": "title",
        }

        if not settings.MAL_NSFW:
            params["exclude_genre"] = [
                "Adult",
                "Hentai",
                "Doujinshi",
            ]

        response = services.api_request(
            "MANGAUPDATES",
            "POST",
            url,
            params=params,
        )

        response = response["results"]
        data = [
            {
                "media_id": media["record"]["series_id"],
                "source": "mangaupdates",
                "media_type": "manga",
                "title": media["record"]["title"],
                "image": get_image_url(media["record"]),
            }
            for media in response
        ]

        cache.set(f"search_mangaupdates_{query}", data)

    return data


def manga(media_id):
    """Get metadata for a manga from MangaUpdates."""
    return asyncio.run(async_manga(media_id))


async def async_manga(media_id):
    """Asynchronous implementation of manga metadata retrieval."""
    data = cache.get(f"mangaupdates_manga_{media_id}")

    if data is None:
        url = f"{base_url}/series/{media_id}"
        response = services.api_request("MANGAUPDATES", "GET", url)

        num_chapters = response["latest_chapter"]

        # Run related_manga and recommendations concurrently
        related_task = asyncio.create_task(
            get_related_series(response["related_series"]),
        )
        recommendations_task = asyncio.create_task(
            get_recommendations(response["recommendations"]),
        )

        data = {
            "media_id": media_id,
            "source": "mangaupdates",
            "media_type": "manga",
            "title": response["title"],
            "image": get_image_url(response),
            "synopsis": response["description"],
            "max_progress": num_chapters,
            "details": {
                "format": response["type"],
                "authors": get_authors(response["authors"]),
                "year": response["year"],
                "status": get_status(response["status"]),
                "number_of_chapters": num_chapters,
                "genres": get_genres(response["genres"]),
            },
            "related": {
                "related_manga": await related_task,
                "recommendations": await recommendations_task,
            },
        }

        cache.set(f"mangaupdates_manga_{media_id}", data)

    return data


def get_image_url(response):
    """Get the image URL for a media item."""
    # when no image, value from response is null
    url = response["image"]["url"]["original"]
    return url if url else settings.IMG_NONE


def get_genres(genres):
    """Return the genres for the media."""
    return ", ".join(item["genre"] for item in genres)


def get_authors(authors):
    """Get the authors for a media item."""
    return ", ".join(item["name"] for item in authors)


def get_status(status):
    """Return the status of the media."""
    # e.g berserk 51239621230 needs parsing
    pattern = r"(\d+\s+Volumes\s+\([^)]+\))"
    match = re.search(pattern, status)
    if match:
        return match.group(1)
    return status


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
                "media_id": item.get("related_series_id") or item.get("series_id"),
                "title": item.get("related_series_name") or item.get("series_name"),
                "image": image,
            }
    return None
