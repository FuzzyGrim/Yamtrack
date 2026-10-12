import logging

import requests
from django.conf import settings
from django.core.cache import cache

from app import helpers
from app.providers import services

logger = logging.getLogger(__name__)
PROVIDER = "fanart"
base_url = "https://webservice.fanart.tv/v3"

# Fanart.tv artwork types mapped to the image groups of the API
MOVIE_IMAGE_TYPES = {
    "posters": ["movieposter"],
    "backdrops": ["moviebackground"],
    "logos": ["hdmovielogo", "movielogo"],
}
TV_IMAGE_TYPES = {
    "posters": ["tvposter"],
    "backdrops": ["showbackground"],
    "logos": ["hdtvlogo", "clearlogo"],
}


def is_enabled():
    """Return whether a Fanart.tv API key is configured."""
    return bool(settings.FANART_API)


def get_artwork(kind, external_id):
    """Return the raw Fanart.tv artwork of a movie or tv show, empty if none."""
    cache_key = f"{PROVIDER}_{kind}_{external_id}"
    data = cache.get(cache_key)

    if data is None:
        try:
            data = services.api_request(
                PROVIDER,
                "GET",
                f"{base_url}/{kind}/{external_id}",
                params={"api_key": settings.FANART_API},
            )
        except requests.exceptions.HTTPError as error:
            status_code = getattr(error.response, "status_code", None)
            # Fanart.tv returns 404 when it has no artwork for the media
            if status_code != requests.codes.not_found:
                raise services.ProviderAPIError(PROVIDER, error) from error
            data = {}

        cache.set(cache_key, data)

    return data


def process_images(artwork, artwork_types, season_number=None):
    """Convert Fanart.tv artwork into image groups."""
    images = {}
    for group, types in artwork_types.items():
        entries = []
        for artwork_type in types:
            for art in artwork.get(artwork_type, []):
                if season_number is not None and art.get("season") != str(
                    season_number,
                ):
                    continue
                url = art.get("url")
                if not url:
                    continue
                entry = helpers.build_image_entry(
                    url.replace("/fanart/", "/preview/", 1),
                    url,
                )
                # Fanart.tv uses "00" for images without text
                language = art.get("lang")
                entry["language"] = language if language and language != "00" else None
                entry["vote_count"] = int(art.get("likes") or 0)
                entries.append(entry)
        images[group] = sorted(entries, key=lambda e: e["vote_count"], reverse=True)
    return images


def movie_images(tmdb_id):
    """Return the posters, backgrounds and logos of a movie, by TMDB ID."""
    if not is_enabled():
        return {}
    return process_images(get_artwork("movies", tmdb_id), MOVIE_IMAGE_TYPES)


def tv_images(tvdb_id, season_number=None):
    """Return the artwork of a tv show or the posters of one of its seasons."""
    if not is_enabled() or not tvdb_id:
        return {}
    artwork = get_artwork("tv", tvdb_id)
    if season_number is not None:
        return process_images(
            artwork,
            {"posters": ["seasonposter"]},
            season_number=season_number,
        )
    return process_images(artwork, TV_IMAGE_TYPES)
