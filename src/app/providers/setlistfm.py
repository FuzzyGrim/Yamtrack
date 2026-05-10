"""Setlist.fm API provider for concert metadata.

API Documentation: https://api.setlist.fm/docs/1.0/index.html
"""

import logging

import requests
from django.conf import settings
from django.core.cache import cache

from app import helpers
from app.models import MediaTypes, Sources
from app.providers import services

logger = logging.getLogger(__name__)
base_url = "https://api.setlist.fm/rest/1.0"
RESULTS_PER_PAGE = 20


def _headers():
    return {
        "x-api-key": settings.SETLISTFM_API_KEY,
        "Accept": "application/json",
    }


def handle_error(error):
    if error.response.status_code == requests.codes.unauthorized:
        raise services.ProviderAPIError(
            Sources.SETLISTFM.value,
            error,
            "Invalid or missing Setlist.fm API key",
        )
    raise services.ProviderAPIError(Sources.SETLISTFM.value, error)


def search(query, page):
    """Search for concerts (setlists) by artist name on Setlist.fm."""
    cache_key = f"search_{Sources.SETLISTFM.value}_{MediaTypes.CONCERT.value}_{query}_{page}"
    data = cache.get(cache_key)

    if data is None:
        try:
            response = services.api_request(
                Sources.SETLISTFM.value,
                "GET",
                f"{base_url}/search/setlists",
                params={"artistName": query, "p": page},
                headers=_headers(),
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        setlists = response.get("setlist", [])
        total = response.get("total", 0)

        results = [_format_search_result(s) for s in setlists]

        data = helpers.format_search_response(page, RESULTS_PER_PAGE, total, results)
        cache.set(cache_key, data)

    return data


def concert(setlist_id):
    """Return metadata for a single setlist/concert from Setlist.fm."""
    cache_key = f"{Sources.SETLISTFM.value}_{MediaTypes.CONCERT.value}_{setlist_id}"
    data = cache.get(cache_key)

    if data is None:
        try:
            s = services.api_request(
                Sources.SETLISTFM.value,
                "GET",
                f"{base_url}/setlist/{setlist_id}",
                headers=_headers(),
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        data = _format_metadata(s)
        cache.set(cache_key, data)

    return data


def _format_search_result(s):
    artist = s.get("artist", {}).get("name", "Unknown Artist")
    venue = s.get("venue", {})
    venue_name = venue.get("name", "Unknown Venue")
    city = venue.get("city", {})
    city_name = city.get("name", "")
    country = city.get("country", {}).get("name", "")
    location = ", ".join(filter(None, [city_name, country]))

    return {
        "media_id": s["id"],
        "source": Sources.SETLISTFM.value,
        "media_type": MediaTypes.CONCERT.value,
        "title": f"{artist} at {venue_name}",
        "image": settings.IMG_NONE,
        "details": {
            "date": _parse_date(s.get("eventDate", "")),
            "location": location,
        },
    }


def _format_metadata(s):
    artist = s.get("artist", {}).get("name", "Unknown Artist")
    venue = s.get("venue", {})
    venue_name = venue.get("name", "Unknown Venue")
    city = venue.get("city", {})
    city_name = city.get("name", "")
    country = city.get("country", {}).get("name", "")
    location = ", ".join(filter(None, [city_name, country]))
    event_date = _parse_date(s.get("eventDate", ""))

    return {
        "media_id": s["id"],
        "source": Sources.SETLISTFM.value,
        "source_url": s.get("url", ""),
        "media_type": MediaTypes.CONCERT.value,
        "title": f"{artist} at {venue_name}",
        "max_progress": None,
        "image": settings.IMG_NONE,
        "synopsis": f"Concert by {artist} at {venue_name}, {location}.",
        "score": None,
        "score_count": None,
        "details": {
            "date": event_date,
            "venue": venue_name,
            "location": location,
            "artist": artist,
            "tour": s.get("tour", {}).get("name") if s.get("tour") else None,
        },
        "setlist": _parse_setlist(s),
    }


def _parse_setlist(s):
    """Parse sets/songs into a flat list with dividers before each encore."""
    items = []
    position = 1
    in_encore = False
    for set_group in s.get("sets", {}).get("set", []):
        encore = bool(set_group.get("encore"))
        if encore and not in_encore:
            items.append({"type": "divider", "label": "Encore"})
            in_encore = True
        for song in set_group.get("song", []):
            name = song.get("name", "").strip()
            if not name:
                continue
            items.append({"type": "song", "position": position, "song": name})
            position += 1
    return items


def _parse_date(event_date):
    """Convert setlist.fm DD-MM-YYYY date to YYYY-MM-DD."""
    if not event_date:
        return None
    parts = event_date.split("-")
    if len(parts) == 3:
        return f"{parts[2]}-{parts[1]}-{parts[0]}"
    return None
