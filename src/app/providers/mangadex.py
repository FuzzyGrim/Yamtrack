import logging

from django.core.cache import cache

from app import helpers
from app.models import MediaTypes, Sources
from app.providers import services

logger = logging.getLogger(__name__)
PROVIDER = "mangadex"
base_url = "https://api.mangadex.org"
covers_url = "https://uploads.mangadex.org/covers"

# MangaDex returns at most 100 covers per request
max_covers = 100


def find_manga_id(title, source, media_id):
    """Return the MangaDex ID of a manga, matched by its MAL or MangaUpdates ID."""
    response = services.api_request(
        PROVIDER,
        "GET",
        f"{base_url}/manga",
        params={
            "title": title,
            "limit": 20,
            "contentRating[]": ["safe", "suggestive", "erotica", "pornographic"],
        },
    )

    for manga in response.get("data", []):
        links = manga.get("attributes", {}).get("links") or {}
        if source == Sources.MAL.value and links.get("mal") == str(media_id):
            return manga["id"]
        if source == Sources.MANGAUPDATES.value and is_same_mangaupdates_id(
            links.get("mu"),
            media_id,
        ):
            return manga["id"]
    return None


def is_same_mangaupdates_id(link, media_id):
    """Return whether a MangaDex MangaUpdates link matches the MangaUpdates ID."""
    if not link:
        return False
    if link == str(media_id):
        return True
    # MangaDex stores new MangaUpdates IDs in base 36, as in their URLs
    try:
        return int(link, 36) == int(media_id)
    except ValueError:
        return False


def manga_covers(title, source, media_id):
    """Return the volume covers of a manga from MangaDex, as posters."""
    cache_key = f"{PROVIDER}_{MediaTypes.MANGA.value}_{source}_{media_id}_covers"
    data = cache.get(cache_key)

    if data is None:
        data = {"posters": []}
        manga_id = find_manga_id(title, source, media_id)
        if manga_id:
            response = services.api_request(
                PROVIDER,
                "GET",
                f"{base_url}/cover",
                params={
                    "manga[]": [manga_id],
                    "limit": max_covers,
                    "order[volume]": "asc",
                },
            )
            for cover in response.get("data", []):
                file_name = cover.get("attributes", {}).get("fileName")
                if not file_name:
                    continue
                url = f"{covers_url}/{manga_id}/{file_name}"
                entry = helpers.build_image_entry(f"{url}.512.jpg", url)
                entry["language"] = cover["attributes"].get("locale")
                data["posters"].append(entry)

        cache.set(cache_key, data)

    return data
