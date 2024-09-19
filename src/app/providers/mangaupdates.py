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


def get_image_url(response):
    """Get the image URL for a media item."""
    return response["image"]["url"]["original"]
