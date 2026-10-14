import logging

import requests
from django.conf import settings
from django.core.cache import cache

from app.providers import services

logger = logging.getLogger(__name__)
base_url = "https://www.omdbapi.com/"

# Critic ratings change slowly, cache them longer than the default to stay well
# within OMDb's free tier (1000 requests per day)
RATINGS_CACHE_TIMEOUT = 60 * 60 * 24 * 7  # 7 days

# OMDb "Source" names mapped to the keys exposed to templates
RATING_SOURCES = {
    "Rotten Tomatoes": "rotten_tomatoes",
    "Internet Movie Database": "imdb",
    "Metacritic": "metacritic",
}


def ratings(imdb_id):
    """Return critic ratings for an IMDb ID from OMDb.

    Returns an empty dict when OMDb is not configured, the ID is missing or
    unknown, or the request fails, so the details page never depends on OMDb.
    """
    if not settings.OMDB_API or not imdb_id:
        return {}

    cache_key = f"omdb_ratings_{imdb_id}"
    data = cache.get(cache_key)

    if data is None:
        params = {"apikey": settings.OMDB_API, "i": imdb_id}

        try:
            response = services.api_request("omdb", "GET", base_url, params=params)
        except requests.exceptions.RequestException as error:
            # don't cache failures, so the next page view retries
            logger.warning("OMDb request failed for %s: %s", imdb_id, error)
            return {}

        # OMDb returns HTTP 200 with Response "False" for unknown IDs
        if response.get("Response") != "True":
            logger.warning(
                "OMDb returned no data for %s: %s",
                imdb_id,
                response.get("Error", "unknown error"),
            )
            data = {}
        else:
            data = get_ratings(response)

        cache.set(cache_key, data, RATINGS_CACHE_TIMEOUT)

    return data


def get_ratings(response):
    """Return the ratings from an OMDb title response keyed by source.

    Each rating is split into score and scale for display, e.g.
    "7.6/10" -> {"value": "7.6", "suffix": "/10"}
    "85%" -> {"value": "85", "suffix": "%"}
    """
    ratings = {}

    for rating in response.get("Ratings", []):
        key = RATING_SOURCES.get(rating.get("Source"))
        value = rating.get("Value")
        if key and value:
            ratings[key] = split_rating(value)

    # OMDb reports "N/A" when there is no vote count
    votes = response.get("imdbVotes")
    if "imdb" in ratings and votes and votes != "N/A":
        ratings["imdb"]["votes"] = votes

    return ratings


def split_rating(value):
    """Split an OMDb rating string into its score and scale."""
    if value.endswith("%"):
        return {"value": value[:-1], "suffix": "%"}

    score, separator, scale = value.partition("/")
    return {"value": score, "suffix": separator + scale}
