"""MusicBrainz API provider for music metadata."""

import logging
import re
import time
from collections import OrderedDict
from http import HTTPStatus
from typing import NoReturn
from urllib.parse import quote

import requests
from django.conf import settings
from django.core.cache import cache

from app import helpers, request_timing
from app.log_safety import exception_summary
from app.models import MediaTypes, Sources
from app.providers import credentials, services

logger = logging.getLogger(__name__)

COVER_ART_BASE = "https://coverartarchive.org"
WIKIPEDIA_API_BASE = "https://en.wikipedia.org/api/rest_v1/page/summary"
LASTFM_API_BASE = "https://ws.audioscrobbler.com/2.0/"
MIN_REQUEST_INTERVAL = 1.0  # MusicBrainz requires 1 req/sec for unauth requests
DISCOGRAPHY_CACHE_VERSION = 2
RELEASE_CACHE_VERSION = 2
RELEASE_GROUP_RELEASES_CACHE_VERSION = 1
_last_request_time = 0

# User-Agent required by MusicBrainz API
USER_AGENT = "Floppy/1.0 (https://github.com/dannyvfilms/Floppy)"
_GENRE_ACRONYMS = frozenset({"ebm", "edm", "idm", "ndw", "nsbm", "nwobhm", "usbm"})
_GENRE_PARENT_SUFFIXES = {
    "rock": "Rock",
    "metal": "Metal",
    "jazz": "Jazz",
    "folk": "Folk",
    "country": "Country",
    "blues": "Blues",
    "punk": "Punk",
    "rap": "Rap",
}
_GENRE_PARENT_TOKENS = {
    "experimental": "Experimental",
}
_GENRE_PARENT_EXACT_FALLBACKS = {
    "plunderphonics": ["Experimental"],
}


def capitalize_genre(name: str) -> str:
    """Return a display-friendly MusicBrainz genre name."""
    if not name:
        return name

    normalized = str(name).strip()
    if not normalized:
        return normalized

    lowered = normalized.lower()
    if lowered in _GENRE_ACRONYMS:
        return lowered.upper()

    if "-" in normalized:
        return "-".join(capitalize_genre(part) for part in normalized.split("-"))

    return normalized.title()


def _normalize_musicbrainz_genre_names(values) -> list[str]:
    """Normalize raw MusicBrainz genre/tag payloads into deduped display names."""
    normalized = OrderedDict()
    for value in values or []:
        if isinstance(value, dict):
            value = value.get("name")  # noqa: PLW2901  # deliberate in-loop normalisation
        name = capitalize_genre(str(value).strip()) if value else ""
        if name:
            normalized.setdefault(name.lower(), name)
    return list(normalized.values())


def _infer_genre_parents(genre_name: str) -> list[str]:
    """Infer broad parent genres when MusicBrainz cannot return genre relations."""
    normalized_name = capitalize_genre(str(genre_name or "").strip())
    if not normalized_name:
        return []

    lowered_name = normalized_name.lower()
    inferred = []
    inferred.extend(_GENRE_PARENT_EXACT_FALLBACKS.get(lowered_name, []))

    for token, parent in _GENRE_PARENT_TOKENS.items():
        if token in lowered_name and lowered_name != parent.lower():
            inferred.append(parent)

    for suffix, parent in _GENRE_PARENT_SUFFIXES.items():
        if lowered_name.endswith(f" {suffix}") and lowered_name != parent.lower():
            inferred.append(parent)

    return _normalize_musicbrainz_genre_names(inferred)


@request_timing.timed_provider_call
def get_wikipedia_data(title):
    """Fetch Wikipedia data for a given title (bio extract and image).

    Uses Wikipedia's REST API to get a summary/extract and image.
    The title can be an artist name or a specific Wikipedia article title
    (e.g., "Queen_(band)" which is more accurate than just "Queen").

    Returns a dict with 'extract' and 'image' keys, or None values if not found.
    """
    if not title:
        return {"extract": None, "image": None}

    # Normalize title for cache key and URL
    normalized_title = title.replace(" ", "_")
    cache_key = f"wikipedia_data_{normalized_title.lower()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    result = {"extract": None, "image": None}

    try:
        # Wikipedia API uses underscores for spaces in titles
        url = f"{WIKIPEDIA_API_BASE}/{normalized_title}"
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=10,
        )

        if response.ok:
            data = response.json()

            # Disambiguation pages have a short "extract" too, but it's not a
            # bio - it's a list of unrelated things sharing the title (e.g.
            # "Tool" the band vs. "Tool" the implement). Treat as a miss so
            # callers fall through to a more specific lookup strategy.
            if data.get("type") != "disambiguation":
                extract = data.get("extract", "")
                result["extract"] = extract or None

                # Get the image - prefer originalimage, fall back to thumbnail
                original = data.get("originalimage", {})
                thumbnail = data.get("thumbnail", {})

                # Use original if available and reasonable size, otherwise thumbnail
                if original.get("source"):
                    result["image"] = original["source"]
                elif thumbnail.get("source"):
                    result["image"] = thumbnail["source"]

            # Cache for 7 days on a real hit, 1 day on a miss/disambiguation
            ttl = 60 * 60 * 24 * 7 if result["extract"] else 60 * 60 * 24
            cache.set(cache_key, result, ttl)
        else:
            # Cache the miss to avoid repeated failed lookups
            cache.set(cache_key, result, 60 * 60 * 24)  # Cache miss for 1 day

    except Exception as e:
        logger.debug("Failed to fetch Wikipedia data for %s: %s", title, e)

    return result


_LASTFM_READ_MORE_RE = re.compile(r"\s*<a[^>]*>Read more on Last\.fm</a>\.?\s*$")
_HTML_TAG_RE = re.compile(r"<[^>]+>")


@request_timing.timed_provider_call
def get_lastfm_bio(mbid):
    """Fetch an artist bio from Last.fm's artist.getInfo, keyed by MBID.

    Last.fm resolves the artist deterministically by MusicBrainz ID, so
    unlike guessing a Wikipedia article title from the artist name, this
    can't land on the wrong, same-named entity (see issue #979).

    Returns the bio text, or None if unavailable (no API key configured,
    artist not found, empty bio, or a request error).
    """
    api_key = credentials.get("lastfm", "api_key")
    if not api_key or not mbid:
        return None

    cache_key = f"lastfm_bio_{mbid}"
    cached = cache.get(cache_key, "unset")
    if cached != "unset":
        return cached

    bio = None
    try:
        response = requests.get(
            LASTFM_API_BASE,
            params={
                "method": "artist.getinfo",
                "mbid": mbid,
                "api_key": api_key,
                "format": "json",
            },
            headers={"User-Agent": USER_AGENT},
            timeout=10,
        )

        if response.ok:
            data = response.json()
            summary = data.get("artist", {}).get("bio", {}).get("summary", "")
            summary = _LASTFM_READ_MORE_RE.sub("", summary)
            summary = _HTML_TAG_RE.sub("", summary).strip()
            bio = summary or None

    except Exception as e:
        logger.debug("Failed to fetch Last.fm bio for mbid %s: %s", mbid, e)

    # Cache hits for 7 days, misses for 1 day (mirrors get_wikipedia_data)
    ttl = 60 * 60 * 24 * 7 if bio else 60 * 60 * 24
    cache.set(cache_key, bio, ttl)
    return bio


def get_wikipedia_extract(artist_name):
    """Fetch the Wikipedia extract for an artist (legacy wrapper).

    Returns just the extract string for backwards compatibility.
    """
    data = get_wikipedia_data(artist_name)
    return data.get("extract")


def _rate_limit():
    """Ensure minimum time between MusicBrainz API requests."""
    global _last_request_time  # noqa: PLW0603  # module-level rate limiter state
    current_time = time.time()
    elapsed = current_time - _last_request_time
    if elapsed < MIN_REQUEST_INTERVAL:
        time.sleep(MIN_REQUEST_INTERVAL - elapsed)
    _last_request_time = time.time()


def handle_error(error) -> NoReturn:
    """Wrap a MusicBrainz HTTP failure in the shared provider error type.

    ``services.api_request`` re-raises a bare ``requests.exceptions.HTTPError``
    for any 4xx it does not retry and leaves wrapping to each provider. Every
    other large provider module does this; musicbrainz did not, so callers that
    classify by exception type - notably the metadata backfill's
    terminal-vs-transient check - saw an unrecognised error and retried dead
    recording ids forever.
    """
    raise services.ProviderAPIError(Sources.MUSICBRAINZ.value, error) from None


def _mb_request(endpoint, params=None):
    """Make a rate-limited request to the MusicBrainz API."""
    _rate_limit()
    url = f"{settings.MUSICBRAINZ_URL.rstrip('/')}/{endpoint}"

    if params is None:
        params = {}
    params["fmt"] = "json"

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }

    try:
        return services.api_request(
            Sources.MUSICBRAINZ.value,
            "GET",
            url,
            params=params,
            headers=headers,
        )
    except requests.exceptions.HTTPError as error:
        # Downgrade noise for missing/invalid IDs
        if (
            error.response is not None
            and error.response.status_code == HTTPStatus.NOT_FOUND
        ):
            logger.debug("MusicBrainz API request 404 for %s: %s", url, error)
        else:
            logger.warning("MusicBrainz API request failed: %s", error)
        handle_error(error)
    except Exception as error:  # pragma: no cover - defensive
        logger.warning("MusicBrainz API request failed: %s", error)
        raise


def _try_fetch_cover_from_url(url):
    """Fetch a cover from a specific Cover Art Archive URL.

    Returns the best quality image URL or None.
    """
    try:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
        response = services.api_request(
            Sources.MUSICBRAINZ.value,
            "GET",
            url,
            headers=headers,
        )

        if response and "images" in response:
            # Prioritize front cover
            for image in response["images"]:
                if image.get("front"):
                    # Try different thumbnail sizes (prefer medium quality for performance)
                    thumbnails = image.get("thumbnails", {})
                    return (
                        thumbnails.get("500")
                        or thumbnails.get("large")
                        or thumbnails.get("250")
                        or image.get("image")
                    )
            # No front cover, use first available image
            if response["images"]:
                first_image = response["images"][0]
                thumbnails = first_image.get("thumbnails", {})
                return (
                    thumbnails.get("500")
                    or thumbnails.get("large")
                    or thumbnails.get("250")
                    or first_image.get("image")
                )
    except Exception as e:
        logger.debug("Cover art fetch failed for %s: %s", url, e)
    return None


def get_cover_art(release_id=None, release_group_id=None):
    """Fetch cover art from Cover Art Archive.

    This is the centralized function for all cover art fetching.
    Tries release first, then release-group as fallback.

    Args:
        release_id: MusicBrainz release ID (specific album edition)
        release_group_id: MusicBrainz release group ID (canonical album)

    Returns:
        Image URL or IMG_NONE placeholder
    """
    if not release_id and not release_group_id:
        return settings.IMG_NONE

    # Build cache key from both IDs
    cache_key = f"musicbrainz_cover_{release_id or 'none'}_{release_group_id or 'none'}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    image_url = None

    # Try release-specific cover art first (more specific)
    if release_id:
        image_url = _try_fetch_cover_from_url(f"{COVER_ART_BASE}/release/{release_id}")

    # If no cover for release, try release group as fallback
    if not image_url and release_group_id:
        image_url = _try_fetch_cover_from_url(
            f"{COVER_ART_BASE}/release-group/{release_group_id}"
        )

    result = image_url or settings.IMG_NONE

    # Cache the result (even if it's IMG_NONE) to avoid repeated lookups
    cache.set(cache_key, result, 60 * 60 * 24 * 7)  # Cache for 7 days

    return result


def _get_cover_art(release_id, release_group_id=None):
    """Legacy wrapper for get_cover_art. Use get_cover_art instead."""
    return get_cover_art(release_id=release_id, release_group_id=release_group_id)


def _cover_art_async_url(release_id):
    """Return a direct Cover Art Archive image URL for client-side loading."""
    if not release_id:
        return settings.IMG_NONE
    return f"{COVER_ART_BASE}/release/{release_id}/front-250"


def _unique_nonempty(values):
    """Return non-empty values in their original order without duplicates."""
    result = []
    for raw_value in values:
        value = str(raw_value or "").strip()
        if value and value not in result:
            result.append(value)
    return result


def _release_summary(release, *, include_image=True):
    """Normalize the release fields needed by the release picker."""
    release_id = release.get("id") or release.get("release_id")
    media = release.get("media") or []
    formats = _unique_nonempty(medium.get("format") for medium in media)
    labels = []
    catalog_numbers = []
    for label_info in release.get("label-info") or []:
        label = label_info.get("label") or {}
        labels.extend([label.get("name")])
        catalog_numbers.extend([label_info.get("catalog-number")])

    track_count = release.get("track-count")
    if track_count is None:
        track_count = sum(
            medium.get("track-count") or len(medium.get("tracks") or [])
            for medium in media
        )

    return {
        "release_id": release_id,
        "title": release.get("title") or "Unknown release",
        "release_date": release.get("date") or "",
        "country": release.get("country") or "",
        "status": release.get("status") or "",
        "packaging": release.get("packaging") or "",
        "formats": formats,
        "format": ", ".join(formats),
        "labels": _unique_nonempty(labels),
        "label": ", ".join(_unique_nonempty(labels)),
        "catalog_numbers": _unique_nonempty(catalog_numbers),
        "barcode": release.get("barcode") or "",
        "track_count": track_count or 0,
        "image": (
            _cover_art_async_url(release_id) if include_image and release_id else ""
        ),
    }


def get_release_group_releases(release_group_id):
    """Return all known MusicBrainz releases in a release group."""
    if not release_group_id:
        return []

    cache_key = (
        f"musicbrainz_release_group_releases_v{RELEASE_GROUP_RELEASES_CACHE_VERSION}_"
        f"{release_group_id}"
    )
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    releases = []
    offset = 0
    page_size = 100
    while True:
        response = _mb_request(
            "release",
            {
                "release-group": release_group_id,
                "inc": "labels+media+release-groups",
                "limit": page_size,
                "offset": offset,
            },
        )
        batch = response.get("releases") or []
        releases.extend(_release_summary(release) for release in batch)
        if not batch:
            break

        total = response.get("release-count") or response.get("count")
        offset += len(batch)
        if (total and offset >= total) or len(batch) < page_size:
            break

    status_order = {
        "official": 0,
        "promotion": 1,
        "pseudo-release": 2,
        "bootleg": 3,
        "withdrawn": 4,
        "cancelled": 5,
    }

    def sort_key(release):
        """Sort official releases first, then newest within each status."""
        year = release["release_date"][:4]
        return (
            status_order.get(release["status"], 6),
            -(int(year) if year.isdigit() else 0),
            release["title"].casefold(),
        )

    releases.sort(
        key=sort_key,
    )
    cache.set(cache_key, releases, 60 * 60 * 24 * 7)
    return releases


def _search_cache_key(query, page, skip_cover_art):
    cache_key = f"musicbrainz_search_{query.lower()}_p{page}"
    if skip_cover_art:
        cache_key += "_no_art"
    return cache_key


def cached_search(query, page=1, skip_cover_art=False):
    """Return a stored ``search`` result without calling MusicBrainz, else None."""
    return cache.get(_search_cache_key(query, page, skip_cover_art)) or None


def search(query, page=1, skip_cover_art=False):
    """Search for music recordings on MusicBrainz.

    Args:
        query: Search query string
        page: Page number for pagination
        skip_cover_art: If True, skip fetching cover art (faster)
    """
    cache_key = _search_cache_key(query, page, skip_cover_art)
    cached = cache.get(cache_key)
    if cached:
        return cached

    per_page = 20
    offset = (page - 1) * per_page

    # Search for recordings (tracks)
    params = {
        "query": query,
        "limit": per_page,
        "offset": offset,
    }

    response = _mb_request("recording", params)

    recordings = response.get("recordings", [])
    total_results = response.get("count", 0)

    results = []
    for recording in recordings:
        recording_id = recording.get("id")
        title = recording.get("title", "Unknown")

        # Get artist info
        artist_credits = recording.get("artist-credit", [])
        artist_name = ""
        artist_id = None
        if artist_credits:
            # Combine all artist credits into a single string
            artist_parts = []
            for credit in artist_credits:
                if isinstance(credit, dict):
                    if not artist_id:
                        artist_id = credit.get("artist", {}).get("id")
                    artist_parts.append(
                        credit.get("name", credit.get("artist", {}).get("name", ""))
                    )
                    artist_parts.append(credit.get("joinphrase", ""))
            artist_name = "".join(artist_parts).strip()

        # Get release info (album) and image
        releases = recording.get("releases", [])
        image = settings.IMG_NONE
        release_date = None
        album_title = ""
        release_id = None
        release_group_id = None

        if releases:
            first_release = releases[0]
            album_title = first_release.get("title", "")
            release_date = first_release.get("date", "")
            release_id = first_release.get("id")
            release_group = (
                first_release.get("release-group")
                or first_release.get("release_group")
                or {}
            )
            release_group_id = release_group.get("id")
            # Try to get cover art from the first release (skip if requested for faster search)
            if not skip_cover_art and release_id:
                image = _get_cover_art(release_id)

        # Get duration in milliseconds, convert to minutes
        duration_ms = recording.get("length")
        duration_minutes = None
        if duration_ms:
            duration_minutes = round(duration_ms / 60000, 1)

        # Build display title with artist
        display_title = title
        if artist_name:
            display_title = f"{title} - {artist_name}"

        results.append(
            {
                "media_id": recording_id,
                "source": Sources.MUSICBRAINZ.value,
                "media_type": MediaTypes.MUSIC.value,
                "title": display_title,
                "image": image,
                # Store additional data for later use
                "artist_name": artist_name,
                "album_title": album_title,
                "artist_id": artist_id,
                "release_id": release_id,
                "release_group_id": release_group_id,
                "duration_minutes": duration_minutes,
                "release_date": release_date,
            }
        )

    data = helpers.format_search_response(
        page=page,
        per_page=per_page,
        total_results=total_results,
        results=results,
    )

    cache.set(cache_key, data, 60 * 60 * 24)  # Cache for 24 hours
    return data


def cached_recording(media_id):
    """Return a stored ``recording`` result without calling MusicBrainz, else None."""
    return cache.get(f"musicbrainz_recording_{media_id}") or None


def recording(media_id):
    """Get detailed metadata for a music recording (track)."""
    cache_key = f"musicbrainz_recording_{media_id}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    # Fetch recording with releases and artist-credits included
    params = {
        "inc": "artists+releases+release-groups+genres+tags",
    }

    response = _mb_request(f"recording/{media_id}", params)

    title = response.get("title", "Unknown")
    recording_id = response.get("id", media_id)

    # Get artist info
    artist_credits = response.get("artist-credit", [])
    artist_name = ""
    artist_id = None
    if artist_credits:
        artist_parts = []
        for credit in artist_credits:
            if isinstance(credit, dict):
                artist_data = credit.get("artist", {})
                if not artist_id:
                    artist_id = artist_data.get("id")
                artist_parts.append(credit.get("name", artist_data.get("name", "")))
                artist_parts.append(credit.get("joinphrase", ""))
        artist_name = "".join(artist_parts).strip()

    # Get release info
    releases = response.get("releases", [])
    release_date = None
    album_title = ""
    album_id = None
    release_group_id = None
    image = settings.IMG_NONE

    if releases:
        # Use the first release with a date, or just the first release
        selected_release = None
        for release in releases:
            if release.get("date"):
                selected_release = release
                break

        if not selected_release and releases:
            selected_release = releases[0]

        if selected_release:
            album_title = selected_release.get("title", "")
            album_id = selected_release.get("id")
            release_date = selected_release.get("date")
            release_group_id = selected_release.get("release-group", {}).get("id")
            image = _get_cover_art(album_id, release_group_id)

    # Get duration
    duration_ms = response.get("length")
    duration_minutes = None
    runtime_str = None
    if duration_ms:
        duration_minutes = round(duration_ms / 60000, 1)
        minutes = int(duration_ms // 60000)
        seconds = int((duration_ms % 60000) // 1000)
        runtime_str = f"{minutes}:{seconds:02d}"

    # Genres/tags (prefer official genres)
    genres = []
    for g in response.get("genres", []):
        name = g.get("name")
        if name:
            genres.append(name)
    if not genres:
        for t in response.get("tags", []):
            name = t.get("name")
            if name:
                genres.append(name)

    # Build display title with artist
    display_title = title
    if artist_name:
        display_title = f"{title} - {artist_name}"

    result = {
        "media_id": recording_id,
        "source": Sources.MUSICBRAINZ.value,
        "media_type": MediaTypes.MUSIC.value,
        "title": display_title,
        "image": image,
        "max_progress": None,  # Music tracks don't have progress in the traditional sense
        "synopsis": "",  # MusicBrainz doesn't have track descriptions
        "related": {},
        "details": {
            "artist": artist_name,
            "artist_id": artist_id,
            "album": album_title,
            "album_id": album_id,
            "release_date": release_date,
            "runtime": runtime_str,
            "duration_minutes": duration_minutes,
        },
        "genres": genres,
        # Additional fields for creating Artist/Album models
        "_artist_name": artist_name,
        "_artist_id": artist_id,
        "_album_title": album_title,
        "_album_id": album_id,
    }

    cache.set(cache_key, result, 60 * 60 * 24 * 7)  # Cache for 7 days
    return result


def get_release_group_genres(release_group_id: str) -> list[str]:
    """Return normalized release-group genres/tags for a MusicBrainz release group."""
    if not release_group_id:
        return []

    cache_key = f"musicbrainz_release_group_genres_{release_group_id}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        response = _mb_request(
            f"release-group/{release_group_id}",
            {"inc": "genres+tags"},
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(
            "Failed to fetch release-group genres for %s: %s",
            release_group_id,
            exception_summary(exc),
        )
        return []

    genres = _normalize_musicbrainz_genre_names(response.get("genres"))
    if not genres:
        genres = _normalize_musicbrainz_genre_names(response.get("tags"))

    cache.set(cache_key, genres, 60 * 60 * 24 * 7)
    return genres


def get_genre_mbid(genre_name: str) -> str:
    """Return the MusicBrainz genre MBID for a display name, or an empty string."""
    normalized_name = str(genre_name or "").strip()
    if not normalized_name:
        return ""

    cache_key = f"musicbrainz_genre_mbid_v3_{normalized_name.lower()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    query = f'name:"{normalized_name}"'
    try:
        response = _mb_request(
            "genre",
            {"query": query},
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(
            "Failed to resolve MusicBrainz genre MBID for %s: %s",
            normalized_name,
            exception_summary(exc),
        )
        return ""

    mbid = ""
    for genre in response.get("genres", []):
        candidate_name = str(genre.get("name") or "").strip()
        if candidate_name.lower() == normalized_name.lower():
            mbid = genre.get("id") or ""
            break
    if not mbid and response.get("genres"):
        mbid = response["genres"][0].get("id") or ""

    cache.set(cache_key, mbid, 60 * 60 * 24 * 30)
    return mbid


def get_genre_parents(genre_name: str, *, max_depth: int = 3) -> list[str]:
    """Return deduped implied parent genres for a MusicBrainz genre name."""
    normalized_name = capitalize_genre(str(genre_name or "").strip())
    if not normalized_name or max_depth < 1:
        return []

    cache_key = f"musicbrainz_genre_parents_v3_{normalized_name.lower()}_{max_depth}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    mbid = get_genre_mbid(normalized_name)
    if not mbid:
        inferred = _infer_genre_parents(normalized_name)
        cache.set(cache_key, inferred, 60 * 60 * 24 * 30)
        return inferred

    seen = set()

    def _walk(current_mbid: str, depth: int) -> list[str]:
        if depth >= max_depth or not current_mbid or current_mbid in seen:
            return []

        seen.add(current_mbid)
        try:
            response = _mb_request(
                f"genre/{quote(current_mbid)}",
                {"inc": "genre-rels"},
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(
                "Failed to fetch MusicBrainz genre parents for %s: %s",
                current_mbid,
                exception_summary(exc),
            )
            return []

        matching_relations = [
            relation
            for relation in response.get("relations", [])
            if relation.get("type") == "subgenre of"
        ]
        forward_relations = [
            relation
            for relation in matching_relations
            if relation.get("direction") == "forward"
        ]
        parent_relations = forward_relations or matching_relations

        parents = []
        for relation in parent_relations:
            parent_payload = relation.get("genre") or {}
            parent_name = capitalize_genre(
                str(parent_payload.get("name") or "").strip()
            )
            parent_mbid = parent_payload.get("id") or ""
            if parent_name:
                parents.append(parent_name)
            if parent_mbid:
                parents.extend(_walk(parent_mbid, depth + 1))
        return parents

    normalized = _normalize_musicbrainz_genre_names(_walk(mbid, 0))
    normalized = [
        value for value in normalized if value.lower() != normalized_name.lower()
    ]
    if not normalized:
        normalized = _infer_genre_parents(normalized_name)
    cache.set(cache_key, normalized, 60 * 60 * 24 * 30)
    return normalized


def search_artists(query, page=1):
    """Search for artists on MusicBrainz."""
    cache_key = f"musicbrainz_artist_search_{query.lower()}_p{page}"
    cached = cache.get(cache_key)
    # Reuse cached hits, but allow a re-query when the previous result was empty
    # so we can benefit from new fallback search logic.
    if cached and cached.get("results"):
        return cached

    per_page = 10
    offset = (page - 1) * per_page

    def _query_musicbrainz(q):
        params = {
            "query": q,
            "limit": per_page,
            "offset": offset,
        }
        return _mb_request("artist", params)

    # Try the primary query first
    response = _query_musicbrainz(query)
    artists = response.get("artists", [])
    total_results = response.get("count", 0)

    # If nothing comes back (commonly for names with special chars like AC/DC),
    # try a few normalized variants before giving up.
    if not artists:
        variants = {query}
        if "/" in query:
            variants.update(
                {
                    query.replace("/", " "),
                    query.replace("/", ""),
                    query.replace("/", "-"),
                }
            )
        # MusicBrainz sometimes matches better with quoted exact queries
        variants.add(f'"{query}"')
        # Run fallback queries until we get a hit
        for variant in variants:
            if variant == query:
                continue
            try:
                resp = _query_musicbrainz(variant)
                if resp.get("artists"):
                    logger.debug(
                        "musicbrainz.search_artists fallback query '%s' returned %s results",
                        variant,
                        len(resp.get("artists", [])),
                    )
                    artists = resp.get("artists", [])
                    total_results = resp.get("count", total_results)
                    break
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug(
                    "musicbrainz.search_artists fallback failed for %s: %s",
                    variant,
                    exception_summary(exc),
                )

    data = {
        "page": page,
        "per_page": per_page,
        "total_results": total_results,
        "total_pages": (total_results + per_page - 1) // per_page
        if total_results > 0
        else 0,
        "results": [],
    }

    for artist in artists:
        artist_id = artist.get("id")
        name = artist.get("name", "Unknown")
        sort_name = artist.get("sort-name", name)
        disambiguation = artist.get("disambiguation", "")

        # Get life-span for display
        life_span = artist.get("life-span", {})
        begin = life_span.get("begin", "")

        # Keep both artist_id and id for callers that expect the generic key
        data["results"].append(
            {
                "artist_id": artist_id,
                "id": artist_id,
                "name": name,
                "sort_name": sort_name,
                "disambiguation": disambiguation,
                "begin_year": begin[:4] if begin else None,
                "type": artist.get("type", ""),
            }
        )

    cache.set(cache_key, data, 60 * 60 * 24)
    return data


def search_releases(query, page=1, skip_cover_art=False):
    """Search for releases (albums) on MusicBrainz.

    Args:
        query: Search query string
        page: Page number for pagination
        skip_cover_art: If True, skip fetching cover art (faster)
    """
    cache_key = f"musicbrainz_release_search_{query.lower()}_p{page}"
    if skip_cover_art:
        cache_key += "_no_art"
    cached = cache.get(cache_key)
    if cached:
        return cached

    per_page = 10
    offset = (page - 1) * per_page

    params = {
        "query": query,
        "limit": per_page,
        "offset": offset,
    }

    response = _mb_request("release", params)

    releases = response.get("releases", [])
    total_results = response.get("count", 0)

    results = []
    for release in releases:
        release_id = release.get("id")
        title = release.get("title", "Unknown")
        date = release.get("date", "")

        # Get artist info
        artist_credits = release.get("artist-credit", [])
        artist_name = ""
        artist_id = None
        if artist_credits:
            artist_parts = []
            for credit in artist_credits:
                if isinstance(credit, dict):
                    artist_data = credit.get("artist", {})
                    if not artist_id:
                        artist_id = artist_data.get("id")
                    artist_parts.append(credit.get("name", artist_data.get("name", "")))
                    artist_parts.append(credit.get("joinphrase", ""))
            artist_name = "".join(artist_parts).strip()

        # Try to get cover art (skip if requested for faster search)
        image = settings.IMG_NONE
        if not skip_cover_art:
            image = _get_cover_art(release_id)

        results.append(
            {
                "release_id": release_id,
                "title": title,
                "artist_name": artist_name,
                "artist_id": artist_id,
                "release_date": date,
                "image": image,
            }
        )

    data = {
        "page": page,
        "per_page": per_page,
        "total_results": total_results,
        "total_pages": (total_results + per_page - 1) // per_page
        if total_results > 0
        else 0,
        "results": results,
    }

    cache.set(cache_key, data, 60 * 60 * 24)
    return data


def cached_artist(artist_id):
    """Return a stored ``get_artist`` result without calling MusicBrainz, else None.

    Pages read this; the repair task calls ``get_artist`` (docs/agents/music_integration.md).
    """
    return cache.get(f"musicbrainz_artist_{artist_id}") or None


def get_artist(artist_id):
    """Get detailed metadata for an artist."""
    cache_key = f"musicbrainz_artist_{artist_id}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    params = {
        "inc": "releases+release-groups+tags+ratings+annotation+genres+url-rels+artist-rels",
    }

    response = _mb_request(f"artist/{artist_id}", params)

    name = response.get("name", "Unknown")
    sort_name = response.get("sort-name", name)
    disambiguation = response.get("disambiguation", "")
    artist_type = response.get("type", "")
    country = response.get("country", "")

    # Life span
    life_span = response.get("life-span", {})
    begin_date = life_span.get("begin", "")
    end_date = life_span.get("end", "")
    ended = life_span.get("ended", False)

    # Area gives the artist's country or location.
    area = response.get("area") or {}
    area_name = area.get("name", "") if isinstance(area, dict) else ""

    # Annotation from MusicBrainz (often just editor notes, not a bio)
    mb_annotation = response.get("annotation", "")

    # Primary bio source: Last.fm's artist.getInfo, resolved by MusicBrainz ID.
    # Unlike guessing a Wikipedia title from the artist name, this can't land
    # on an unrelated same-named entity (e.g. "Tool" the band vs. the tool).
    artist_bio = get_lastfm_bio(artist_id)
    bio_image = None

    # Try to get the Wikipedia article title from MusicBrainz relations
    # This is more reliable than guessing (e.g., "Queen" -> "Queen_(band)")
    wikipedia_title = None
    relations = response.get("relations", [])
    for rel in relations:
        if rel.get("type") == "wikipedia":
            url = rel.get("url", {}).get("resource", "")
            # Extract article title from Wikipedia URL
            # e.g., "https://en.wikipedia.org/wiki/Queen_(band)" -> "Queen_(band)"
            if "wikipedia.org/wiki/" in url:
                wikipedia_title = url.split("/wiki/")[-1]
                # Prefer English Wikipedia
                if "en.wikipedia.org" in url:
                    break

    if not artist_bio and wikipedia_title:
        # Next best: MusicBrainz has the exact Wikipedia URL
        wiki_data = get_wikipedia_data(wikipedia_title)
        artist_bio = wiki_data.get("extract")
        bio_image = wiki_data.get("image")

    if not artist_bio and artist_type == "Group":
        # Try the standard Wikipedia disambiguation suffix for bands
        wiki_data = get_wikipedia_data(f"{name}_(band)")
        artist_bio = wiki_data.get("extract")
        bio_image = wiki_data.get("image")

    if not artist_bio:
        # Try artist name directly (works for "Kenny G", etc.)
        wiki_data = get_wikipedia_data(name)
        artist_bio = wiki_data.get("extract")
        bio_image = wiki_data.get("image")

    if not artist_bio and disambiguation:
        # Last resort: try name with disambiguation (e.g., "Queen_(band)")
        wiki_title_with_disambig = f"{name}_({disambiguation.replace(' ', '_')})"
        wiki_data = get_wikipedia_data(wiki_title_with_disambig)
        artist_bio = wiki_data.get("extract")
        bio_image = wiki_data.get("image")

    # Genres from MusicBrainz (new genre system)
    genres = []
    genre_data = response.get("genres", [])
    for g in genre_data:
        genre_name = g.get("name", "")
        genre_count = g.get("count", 0)
        if genre_name:
            genres.append(
                {
                    "name": genre_name,
                    "count": genre_count,
                }
            )
    # Sort by count (most relevant first)
    genres.sort(key=lambda x: x.get("count", 0), reverse=True)

    # Tags (user-submitted tags, often more available than genres)
    tags = []
    tag_data = response.get("tags", [])
    for t in tag_data:
        tag_name = t.get("name", "")
        tag_count = t.get("count", 0)
        if tag_name and tag_count > 0:
            tags.append(
                {
                    "name": tag_name,
                    "count": tag_count,
                }
            )
    # Sort by count (most relevant first)
    tags.sort(key=lambda x: x.get("count", 0), reverse=True)

    # Rating
    rating_data = response.get("rating", {})
    rating = rating_data.get("value")  # 0-5 scale
    rating_count = rating_data.get("votes-count", 0)

    # Get releases (albums) for this artist
    release_groups = response.get("release-groups", [])
    albums = [
        {
            "release_group_id": rg.get("id"),
            "title": rg.get("title", ""),
            "type": rg.get("primary-type", ""),
            "first_release_date": rg.get("first-release-date", ""),
        }
        for rg in release_groups[:20]
    ]

    # Band members: "member of band" relations where this artist is the band
    # (the member is the source of the relation, so it appears with direction
    # "backward" when fetched from the band's own artist page).
    members = []
    for rel in relations:
        if rel.get("type") != "member of band" or rel.get("direction") != "backward":
            continue
        member_payload = rel.get("artist") or {}
        member_id = member_payload.get("id")
        member_name = member_payload.get("name", "")
        if not member_id or not member_name:
            continue
        attributes = rel.get("attributes", [])
        members.append(
            {
                "artist_id": member_id,
                "name": member_name,
                "role": ", ".join(attributes) if attributes else "",
                "begin_date": rel.get("begin", ""),
                "end_date": rel.get("end", ""),
                "ended": rel.get("ended", False),
            }
        )

    result = {
        "artist_id": artist_id,
        "name": name,
        "sort_name": sort_name,
        "disambiguation": disambiguation,
        "type": artist_type,
        "country": country,
        "area": area_name,
        "begin_date": begin_date,
        "end_date": end_date,
        "ended": ended,
        "bio": artist_bio,  # Last.fm or Wikipedia extract, in that priority order
        "image": bio_image,  # Image from whichever bio source was used
        "annotation": mb_annotation,  # MusicBrainz annotation (usually editor notes)
        "genres": genres[:10],  # Top 10 genres
        "tags": tags[:15],  # Top 15 tags
        "rating": rating,
        "rating_count": rating_count,
        "albums": albums,
        "members": members,
    }

    cache.set(cache_key, result, 60 * 60 * 24 * 7)
    return result


def get_artist_discography(artist_id, skip_cover_art=False):
    """Get the full discography for an artist from MusicBrainz.

    This fetches release-groups (which represent unique album releases)
    and finds a representative release for each to get cover art.

    Args:
        artist_id: MusicBrainz artist ID
        skip_cover_art: If True, skip fetching cover art (faster initial load)

    Returns a normalized list of albums with:
    - title, release_group_id, release_id, release_date, image, release_type
    """
    cache_key = (
        f"musicbrainz_artist_discography_v{DISCOGRAPHY_CACHE_VERSION}_{artist_id}"
    )
    if skip_cover_art:
        cache_key += "_no_art"
    cached = cache.get(cache_key)
    if cached:
        return cached

    # Fetch ALL release-groups for the artist
    # Don't filter by type to get everything, we'll sort later
    params = {
        "artist": artist_id,
        "limit": 100,
    }

    response = _mb_request("release-group", params)
    release_groups = response.get("release-groups", [])

    # Include all MusicBrainz primary types so discography matches their page.
    allowed_types = {"Album", "EP", "Single", "Broadcast", "Other", "Compilation"}
    release_groups = [
        rg for rg in release_groups if rg.get("primary-type") in allowed_types
    ]

    albums = []
    for rg in release_groups:
        rg_id = rg.get("id")
        title = rg.get("title", "")
        primary_type = rg.get("primary-type", "")
        secondary_types = rg.get("secondary-types", [])
        first_release_date = rg.get("first-release-date", "")

        # Build a type string (e.g., "Album", "EP", "Album + Live")
        release_type = primary_type
        if secondary_types:
            release_type = f"{primary_type} + {', '.join(secondary_types)}"

        albums.append(
            {
                "release_group_id": rg_id,
                "title": title,
                "release_date": first_release_date,
                "release_type": release_type,
                "primary_type": primary_type,
                "secondary_types": secondary_types,
                "release_id": None,  # Will be filled if we fetch releases
                "image": "",  # Will be filled later
            }
        )

    # Now fetch actual releases to get release IDs for cover art
    # We'll batch this to avoid too many API calls
    # Get releases for each release-group to find cover art
    release_params = {
        "artist": artist_id,
        "status": "official",
        "limit": 100,
    }

    release_response = _mb_request("release", release_params)
    releases = release_response.get("releases", [])

    # Map release-group-id to best release (prefer ones with cover art)
    rg_to_release = {}
    for release in releases:
        rg_id = release.get("release-group", {}).get("id")
        # Keep first release per release-group (API returns most relevant first)
        if rg_id and rg_id not in rg_to_release:
            rg_to_release[rg_id] = release

    # Update albums with release IDs and optionally fetch cover art
    for album in albums:
        rg_id = album["release_group_id"]
        release_id = None

        if rg_id in rg_to_release:
            release = rg_to_release[rg_id]
            release_id = release.get("id")
            album["release_id"] = release_id

        if not skip_cover_art:
            # Try to get cover art - use both release_id and release_group_id
            # This ensures we try the release-group fallback even if we have a release_id
            album["image"] = get_cover_art(
                release_id=release_id, release_group_id=rg_id
            )

    # Sort by date (newest first), with albums without dates at the end
    albums.sort(key=lambda x: x.get("release_date", "") or "0000", reverse=True)

    cache.set(cache_key, albums, 60 * 60 * 24 * 7)  # Cache for 7 days
    return albums


def get_release_for_group(release_group_id):
    """Get a representative release for a release group.

    This is useful when we only have a release_group_id and need to find
    a specific release to fetch tracks from.

    Args:
        release_group_id: The MusicBrainz release group ID

    Returns:
        A release ID string, or None if not found
    """
    cache_key = f"musicbrainz_release_for_group_{release_group_id}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    try:
        # Query releases for this release group - try official first
        params = {
            "release-group": release_group_id,
            "status": "official",
            "inc": "media",
            "limit": 25,
        }

        response = _mb_request("release", params)
        releases = response.get("releases", [])

        if releases:
            # A release group can contain many pressings with differing
            # tracklists (e.g. a partial vinyl reissue alongside the full
            # digital release) - prefer the one with the most tracks rather
            # than an arbitrary browse-order pick.
            best = max(
                releases,
                key=lambda r: _release_summary(r, include_image=False)[
                    "track_count"
                ],
            )
            release_id = best.get("id")
            cache.set(cache_key, release_id, 60 * 60 * 24 * 7)
            return release_id

        # If no official releases, try without status filter (any release type)
        logger.debug(
            "No official releases found for release_group %s, trying any release",
            release_group_id,
        )
        params = {
            "release-group": release_group_id,
            "inc": "media",
            "limit": 25,
        }

        response = _mb_request("release", params)
        releases = response.get("releases", [])

        if releases:
            best = max(
                releases,
                key=lambda r: _release_summary(r, include_image=False)[
                    "track_count"
                ],
            )
            release_id = best.get("id")
            logger.info(
                "Found non-official release %s for release_group %s",
                release_id,
                release_group_id,
            )
            cache.set(cache_key, release_id, 60 * 60 * 24 * 7)
            return release_id

    except Exception as e:
        logger.warning("Failed to get release for group %s: %s", release_group_id, e)

    return None


def cached_release(release_id):
    """Return a stored ``get_release`` result without calling MusicBrainz, else None."""
    return (
        cache.get(f"musicbrainz_release_v{RELEASE_CACHE_VERSION}_{release_id}") or None
    )


def get_release(release_id, skip_cover_art: bool = False):
    """Get detailed metadata for a release (album).

    Args:
        release_id: MusicBrainz release UUID
        skip_cover_art: If True, do not fetch cover art (use placeholder)
    """
    cache_key = f"musicbrainz_release_v{RELEASE_CACHE_VERSION}_{release_id}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    params = {
        "inc": "artist-credits+recordings+release-groups+genres+tags+labels+media",
    }

    response = _mb_request(f"release/{release_id}", params)

    title = response.get("title", "Unknown")
    date = response.get("date", "")
    summary = _release_summary(response, include_image=False)

    # Get release group info for cover art fallback and type classification
    release_group = response.get("release-group", {})
    release_group_id = release_group.get("id")
    primary_type = release_group.get("primary-type", "")
    secondary_types = release_group.get("secondary-types", [])
    release_type = primary_type
    if secondary_types:
        release_type = f"{primary_type} + {', '.join(secondary_types)}"

    # Get artist info
    artist_credits = response.get("artist-credit", [])
    artist_name = ""
    artist_id = None
    if artist_credits:
        artist_parts = []
        for credit in artist_credits:
            if isinstance(credit, dict):
                artist_data = credit.get("artist", {})
                if not artist_id:
                    artist_id = artist_data.get("id")
                artist_parts.append(credit.get("name", artist_data.get("name", "")))
                artist_parts.append(credit.get("joinphrase", ""))
        artist_name = "".join(artist_parts).strip()

    structured_credits = []
    for credit in artist_credits:
        if isinstance(credit, dict):
            artist_data = credit.get("artist", {})
            structured_credits.append(
                {
                    "artist_id": artist_data.get("id"),
                    "name": credit.get("name") or artist_data.get("name", ""),
                    "sort_name": artist_data.get("sort-name", ""),
                    "join_phrase": credit.get("joinphrase", ""),
                }
            )

    # Get cover art with release group fallback
    image = (
        settings.IMG_NONE
        if skip_cover_art
        else _get_cover_art(release_id, release_group_id)
    )

    # Genres/tags (prefer official genres)
    genres = _normalize_musicbrainz_genre_names(response.get("genres"))
    if not genres:
        genres = _normalize_musicbrainz_genre_names(response.get("tags"))

    # Get tracks
    media_list = response.get("media", [])
    tracks = []
    for medium in media_list:
        disc_number = medium.get("position", 1)
        for track in medium.get("tracks", []):
            recording = track.get("recording", {})
            track_length = recording.get("length")
            duration_str = None
            if track_length:
                minutes = int(track_length // 60000)
                seconds = int((track_length % 60000) // 1000)
                duration_str = f"{minutes}:{seconds:02d}"

            tracks.append(
                {
                    "recording_id": recording.get("id"),
                    "title": recording.get("title", track.get("title", "")),
                    "track_number": track.get("position"),
                    "disc_number": disc_number,
                    "duration": duration_str,
                    "duration_ms": track_length,
                    "genres": genres,
                }
            )

    result = {
        "release_id": release_id,
        "release_group_id": release_group_id,
        "release_type": release_type,
        "title": title,
        "artist_name": artist_name,
        "artist_id": artist_id,
        "artist_credits": structured_credits,
        "release_date": date,
        "image": image,
        "genres": genres,
        "tracks": tracks,
        "country": summary["country"],
        "status": summary["status"],
        "packaging": summary["packaging"],
        "formats": summary["formats"],
        "format": summary["format"],
        "labels": summary["labels"],
        "label": summary["label"],
        "catalog_numbers": summary["catalog_numbers"],
        "barcode": summary["barcode"],
        "track_count": summary["track_count"],
    }

    cache.set(cache_key, result, 60 * 60 * 24 * 7)
    return result


def search_combined(query, page=1):
    """Search artists, albums and tracks in one call.

    First page returns artists/albums while image files load client-side.
    """
    cache_key = f"musicbrainz_combined_search_{query.lower()}_p{page}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    # For first page, fetch artists, releases, and recordings.
    # For subsequent pages, only fetch recordings (tracks).
    if page == 1:
        artist_results = search_artists(query, page=1)
        release_results = search_releases(query, page=1, skip_cover_art=True)
        track_results = search(query, page=1, skip_cover_art=True)

        top_releases = []
        release_art_by_artist_id = {}
        release_art_by_artist_name = {}
        for release in release_results.get("results", [])[:5]:
            release_entry = dict(release)
            release_image = release_entry.get("image")
            if (
                not release_image or release_image == settings.IMG_NONE
            ) and release_entry.get("release_id"):
                release_image = _cover_art_async_url(release_entry["release_id"])
                release_entry["image"] = release_image

            top_releases.append(release_entry)
            if not release_image or release_image == settings.IMG_NONE:
                continue

            artist_id = release_entry.get("artist_id")
            if artist_id and artist_id not in release_art_by_artist_id:
                release_art_by_artist_id[artist_id] = release_image

            artist_name = str(release_entry.get("artist_name") or "").strip().casefold()
            if artist_name and artist_name not in release_art_by_artist_name:
                release_art_by_artist_name[artist_name] = release_image

        top_artists = []
        for artist in artist_results.get("results", [])[:5]:
            artist_entry = dict(artist)
            artist_image = release_art_by_artist_id.get(artist_entry.get("artist_id"))
            if not artist_image:
                artist_name_key = str(artist_entry.get("name") or "").strip().casefold()
                artist_image = release_art_by_artist_name.get(
                    artist_name_key, settings.IMG_NONE
                )
            artist_entry["image"] = artist_image
            top_artists.append(artist_entry)

        data = {
            "artists": top_artists,  # Top 5 artists
            "releases": top_releases,  # Top 5 albums
            "tracks": track_results,  # Full track results with pagination
        }
    else:
        # For page > 1, only return tracks (skip cover art for speed)
        track_results = search(query, page=page, skip_cover_art=True)
        data = {
            "artists": [],
            "releases": [],
            "tracks": track_results,
        }

    cache.set(cache_key, data, 60 * 60 * 24)
    return data
