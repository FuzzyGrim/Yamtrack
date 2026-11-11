import logging
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from app.providers import services

logger = logging.getLogger(__name__)

OMDB_URL = "https://www.omdbapi.com/"
OMDB_CACHE_SECONDS = 60 * 60 * 24 * 7
OMDB_BUDGET_SECONDS = 60 * 60 * 48


class OmdbBudgetError(Exception):
    """Raised when today's OMDb request budget is already spent."""


class OmdbRequestError(Exception):
    """Raised when OMDb responds with an error that should be retried later."""


def fetch_ratings(imdb_id):
    """Return IMDb, Rotten Tomatoes, and Metacritic scores for an IMDb id.

    Returns None when no API key is configured, without contacting OMDb.
    Scores are Decimal values, or None when OMDb has no number for that source.
    """
    if not settings.OMDB_API_KEY or not imdb_id:
        return None

    cache_key = f"omdb_ratings_{imdb_id}"
    cached = cache.get(cache_key)
    if isinstance(cached, dict) and "imdb_votes" in cached:
        return cached

    if not _consume_budget():
        raise OmdbBudgetError

    try:
        payload = services.api_request(
            "omdb",
            "GET",
            OMDB_URL,
            params={"apikey": settings.OMDB_API_KEY, "i": imdb_id},
        )
    except requests.RequestException as error:
        logger.exception("OMDb request failed for %s", imdb_id)
        raise OmdbRequestError from error

    parsed = _parse_payload(payload, imdb_id)
    cache.set(cache_key, parsed, timeout=OMDB_CACHE_SECONDS)
    return parsed


def _consume_budget():
    """Count one OMDb request against the daily cap."""
    limit = settings.OMDB_DAILY_LIMIT
    if limit <= 0:
        return False

    key = f"omdb_daily_{timezone.localdate().isoformat()}"
    cache.add(key, 0, timeout=OMDB_BUDGET_SECONDS)
    try:
        used = cache.incr(key)
    except ValueError:
        logger.exception("Could not increment the OMDb daily counter")
        return False
    return used <= limit


def _parse_payload(payload, imdb_id):
    """Turn an OMDb JSON body into raw scores."""
    if str(payload.get("Response")) == "False":
        error = payload.get("Error") or ""
        lowered = error.lower()
        if "limit" in lowered:
            raise OmdbBudgetError
        if "not found" in lowered:
            return _empty_scores()
        logger.warning("OMDb error for %s: %s", imdb_id, error)
        raise OmdbRequestError

    return {
        "imdb": _number(payload.get("imdbRating")),
        "imdb_votes": _votes(payload.get("imdbVotes")),
        "rotten_tomatoes": _rotten_tomatoes(payload),
        "metacritic": _number(payload.get("Metascore")),
    }


def _rotten_tomatoes(payload):
    """Return the critics score from OMDb's Ratings list."""
    for entry in payload.get("Ratings") or []:
        if entry.get("Source") == "Rotten Tomatoes":
            return _number(str(entry.get("Value", "")).rstrip("%"))
    return None


def _votes(text):
    """Return an integer vote count, or None for missing OMDb values."""
    if text is None:
        return None
    cleaned = str(text).strip().replace(",", "")
    if not cleaned or cleaned == "N/A":
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def _number(text):
    """Return a Decimal, or None for missing OMDb values such as ``N/A``."""
    if text is None:
        return None
    cleaned = str(text).strip()
    if not cleaned or cleaned == "N/A":
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _empty_scores():
    """Return a ratings dict with every OMDb source missing."""
    return {
        "imdb": None,
        "imdb_votes": None,
        "rotten_tomatoes": None,
        "metacritic": None,
    }
