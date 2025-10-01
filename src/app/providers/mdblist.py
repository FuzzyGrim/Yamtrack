import logging

import requests
from django.conf import settings
from django.core.cache import cache

from app.providers import services

logger = logging.getLogger(__name__)
base_url = "https://api.mdblist.com"


def handle_error(error):
    """Handle MDBList API errors."""
    error_resp = error.response
    status_code = error_resp.status_code

    try:
        error_json = error_resp.json()
    except requests.exceptions.JSONDecodeError as json_error:
        logger.exception("Failed to decode JSON response")
        raise services.ProviderAPIError("mdblist", error) from json_error

    # Handle authentication errors
    if status_code == requests.codes.unauthorized:
        details = error_json.get("message", "Invalid API key")
        raise services.ProviderAPIError("mdblist", error, details)

    raise services.ProviderAPIError("mdblist", error)


def get_media_ratings(tmdb_id, media_type):
    """Get ratings for a media item from MDBList using TMDB ID."""
    cache_key = f"mdblist_ratings_tmdb_{media_type}_{tmdb_id}"
    data = cache.get(cache_key)

    if data is None:
        url = f"{base_url}/tmdb/{media_type}/{tmdb_id}"
        params = {
            "apikey": settings.MDBLIST_API,
            "append_to_response": "keyword",
        }

        try:
            response = services.api_request(
                "mdblist",
                "GET",
                url,
                params=params,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)
            return None

        # Extract ratings data
        ratings_data = {}
        if "ratings" in response:
            for rating in response["ratings"]:
                source = rating.get("source")
                if source in ["imdb", "tomatoes", "letterboxd"]:
                    ratings_data[source] = {
                        "value": rating.get("value"),
                        "score": rating.get("score"),
                        "votes": rating.get("votes"),
                        "url": rating.get("url"),
                    }

        data = ratings_data
        cache.set(cache_key, data, timeout=86400)  # Cache for 24 hours

    return data
