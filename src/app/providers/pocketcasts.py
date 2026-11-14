"""Pocket Casts provider for podcast search using iTunes API."""

import logging
import re
from concurrent.futures import ThreadPoolExecutor

import requests
from django.conf import settings
from django.core.cache import cache

from app import helpers, request_timing
from app.models import MediaTypes, Sources
from app.providers import services

logger = logging.getLogger(__name__)

ITUNES_API_BASE = "https://itunes.apple.com/search"
ITUNES_LOOKUP_BASE = "https://itunes.apple.com/lookup"
ITUNES_MAX_RESULTS = 200
POCKETCASTS_SEARCH_URL = "https://podcast-api.pocketcasts.com/discover/search"
POCKETCASTS_MAX_CANDIDATES = 12
USER_AGENT = "Floppy/1.0 (https://github.com/dannyvfilms/Floppy)"


def handle_error(error):
    """Handle iTunes API errors."""
    raise services.ProviderAPIError(
        Sources.POCKETCASTS.value,
        error,
    )


def _show_key(title, artist):
    """Identity of a show across duplicate directory listings."""
    return (" ".join((title or "").lower().split()), (artist or "").lower().strip())


def _directory_candidates(query):
    """Return (title, author) pairs from Pocket Casts' own directory search.

    iTunes has no prefix matching and caps results, so shows such as "Blackout"
    can be missing; the Pocket Casts directory ranks them (#1829).
    """
    try:
        response = requests.post(
            POCKETCASTS_SEARCH_URL,
            json={"term": query},
            headers={"User-Agent": USER_AGENT},
            timeout=services.request_timeout(),
        )
        response.raise_for_status()
        shows = response.json()
    except (requests.RequestException, ValueError):
        logger.warning("Pocket Casts directory search failed", exc_info=True)
        return []
    if not isinstance(shows, list):
        return []
    return [
        (show["title"], show.get("author") or "")
        for show in shows[:POCKETCASTS_MAX_CANDIDATES]
        if isinstance(show, dict)
        and isinstance(show.get("title"), str)
        and show["title"]
    ]


def _resolve_in_itunes(candidate):
    """Find the iTunes listing for a directory show by exact title and author."""
    title, author = candidate
    try:
        response = requests.get(
            ITUNES_API_BASE,
            params={
                "term": f"{title} {author}".strip(),
                "media": "podcast",
                "limit": 5,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=services.request_timeout(),
        )
        response.raise_for_status()
        found = response.json().get("results", [])
    except (requests.RequestException, ValueError):
        return None
    wanted = _show_key(title, author)
    for item in found:
        if _show_key(item.get("collectionName"), item.get("artistName")) == wanted:
            return item
    return None


def _merge_directory_matches(query, itunes_items):
    """Put Pocket Casts' ranked matches first, then the remaining iTunes results."""
    candidates = _directory_candidates(query)
    if not candidates:
        return itunes_items

    # A directory title can complete a partial word even when its search ranks
    # the desired show outside its result limit. Avoid expanding whole words.
    completed_matches = []
    if re.fullmatch(r"[^\W\d_]{3,}", query, re.UNICODE):
        words = [
            word
            for title, _ in candidates
            for word in re.findall(r"[^\W\d_]+", title, re.UNICODE)
            if word.casefold().startswith(query.casefold())
        ]
        if words and not any(word.casefold() == query.casefold() for word in words):
            completion = words[0]
            try:
                response = requests.get(
                    ITUNES_API_BASE,
                    params={
                        "term": completion,
                        "media": "podcast",
                        "limit": ITUNES_MAX_RESULTS,
                    },
                    headers={"User-Agent": USER_AGENT},
                    timeout=services.request_timeout(),
                )
                response.raise_for_status()
                completed = response.json().get("results", [])
                if not isinstance(completed, list):
                    completed = []
                completed_matches = [
                    item
                    for item in completed
                    if isinstance(item, dict)
                    and isinstance(item.get("collectionName"), str)
                    and item["collectionName"].casefold() == completion.casefold()
                ]
            except (requests.RequestException, ValueError, AttributeError):
                logger.warning("Podcast title completion search failed", exc_info=True)

    by_key = {
        _show_key(item.get("collectionName"), item.get("artistName")): item
        for item in reversed(itunes_items)
    }
    missing = [c for c in candidates if _show_key(*c) not in by_key]
    with ThreadPoolExecutor(max_workers=4) as pool:
        resolved = dict(
            zip(missing, pool.map(_resolve_in_itunes, missing), strict=True)
        )

    merged = []
    for candidate in candidates:
        item = by_key.get(_show_key(*candidate)) or resolved.get(candidate)
        if item:
            merged.append(item)
    return completed_matches + merged + itunes_items


@request_timing.timed_provider_call
def search(query, page):
    """Search for podcasts using iTunes API."""
    cache_key = f"search_v2_{Sources.POCKETCASTS.value}_{MediaTypes.PODCAST.value}_{query}_{page}"
    data = cache.get(cache_key)

    if data is None:
        # iTunes ignores `offset` for podcasts (every page came back identical,
        # #1817), so fetch the maximum once and paginate locally.
        per_page = settings.PER_PAGE
        offset = (page - 1) * per_page

        params = {
            "term": query,
            "media": "podcast",
            "limit": ITUNES_MAX_RESULTS,
        }

        try:
            response = requests.get(
                ITUNES_API_BASE,
                params=params,
                headers={"User-Agent": USER_AGENT},
                timeout=services.request_timeout(),
            )
            response.raise_for_status()
            response_data = response.json()
        except requests.RequestException as e:
            handle_error(e)

        items = _merge_directory_matches(query, response_data.get("results", []))

        results = []
        seen = set()
        for item in items:
            # Use collectionId as media_id (preferred) or trackId as fallback
            media_id = str(item.get("collectionId") or item.get("trackId", ""))
            if not media_id:
                continue

            # Get title from collectionName (preferred) or trackName
            title = item.get("collectionName") or item.get(
                "trackName", "Unknown Podcast"
            )

            # iTunes lists one show under several collection ids (#1829)
            show_key = _show_key(title, item.get("artistName"))
            if show_key in seen:
                continue
            seen.add(show_key)

            # Get image URL - prefer artworkUrl600, fallback to artworkUrl100
            image = (
                item.get("artworkUrl600")
                or item.get("artworkUrl100")
                or settings.IMG_NONE
            )

            results.append(
                {
                    "media_id": media_id,
                    "source": Sources.POCKETCASTS.value,
                    "media_type": MediaTypes.PODCAST.value,
                    "title": title,
                    "image": image,
                },
            )

        total_results = len(results)
        data = helpers.format_search_response(
            page,
            per_page,
            total_results,
            results[offset : offset + per_page],
        )

        cache.set(cache_key, data)
    return data


@request_timing.timed_provider_call
def lookup_by_itunes_id(itunes_collection_id):
    """Look up podcast metadata by iTunes collection ID.

    Args:
        itunes_collection_id: iTunes collection ID (string)

    Returns:
        Dict with podcast metadata:
        - feed_url: RSS feed URL
        - title: Podcast title
        - author: Podcast author
        - artwork_url: Artwork image URL
        - description: Podcast description
        - genres: List of genres
    """
    cache_key = f"itunes_lookup_{itunes_collection_id}"
    data = cache.get(cache_key)

    if data is None:
        params = {
            "id": itunes_collection_id,
        }

        try:
            response = requests.get(
                ITUNES_LOOKUP_BASE,
                params=params,
                headers={"User-Agent": USER_AGENT},
                timeout=services.request_timeout(),
            )
            response.raise_for_status()
            response_data = response.json()
        except requests.RequestException as e:
            handle_error(e)

        results = response_data.get("results", [])
        if not results:
            raise services.ProviderAPIError(
                Sources.POCKETCASTS.value,
                type(
                    "obj",
                    (object,),
                    {
                        "response": type(
                            "obj",
                            (object,),
                            {"status_code": 404, "text": "Podcast not found"},
                        )()
                    },
                ),
                "Podcast not found in iTunes",
            )

        # Get the first result (should be the collection/podcast)
        item = results[0]

        # Extract metadata
        # iTunes description might be HTML, we'll clean it if needed
        description = item.get("description", "") or item.get("longDescription", "")

        data = {
            "feed_url": item.get("feedUrl", ""),
            "title": item.get("collectionName")
            or item.get("trackName", "Unknown Podcast"),
            "author": item.get("artistName", ""),
            "artwork_url": item.get("artworkUrl600") or item.get("artworkUrl100") or "",
            "description": description,
            "genres": item.get("genres", []),
            "language": item.get("primaryLanguageName", ""),
        }

        # Cache for 7 days
        cache.set(cache_key, data, 60 * 60 * 24 * 7)

    return data
