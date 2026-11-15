"""External provider candidate fetchers for Discover rows."""

from __future__ import annotations

import contextvars
import logging
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from app.discover import cache_repo
from app.discover.adapters import TRAKT_ADAPTER
from app.discover.schemas import CandidateItem
from app.models import MediaTypes, Sources
from app.providers import (
    bgg,
    comicvine,
    credentials,
    igdb,
    mal,
    musicbrainz,
    openlibrary,
    services,
)

logger = logging.getLogger(__name__)

PROVIDER_DISCOVER_TTL_SECONDS = 60 * 60
PROVIDER_COMING_SOON_WINDOW_DAYS = 180
TRAKT_POPULAR_PAGE_SIZE = 100
ISO_DATE_LENGTH = 10

# A trending "work" without an edition id is resolved to its first edition
# with one /editions.json request each. Open Library allows 20 requests a
# minute (see services.py), so a cold daily + monthly build made 22 such calls
# one after another, about a minute on the single Discover worker (M6).
# The resolution is not user data and changes only when Open Library adds an
# edition, so it is kept for a month; a failure is retried after six hours.
OPENLIBRARY_FIRST_EDITION_TTL_SECONDS = 60 * 60 * 24 * 30
OPENLIBRARY_FIRST_EDITION_FAILURE_TTL_SECONDS = 60 * 60 * 6
# Uncached lookups run a few at a time (the shared per-host limiter still caps
# the rate) and one row waits at most this long for them. A work still
# unresolved is left out of this build; its lookup finishes in the background
# and fills the cache, so the next build shows it without another request.
OPENLIBRARY_EDITION_LOOKUP_WORKERS = 4
OPENLIBRARY_EDITION_LOOKUP_BUDGET_SECONDS = 15


def _api_cached_results(
    provider: str,
    endpoint: str,
    params: dict,
    *,
    ttl_seconds: int,
    fetcher,
) -> list[dict]:
    payload, is_stale = cache_repo.get_api_cache(provider, endpoint, params)
    if payload and not is_stale:
        return list(payload.get("results") or [])

    try:
        results = list(fetcher() or [])
        cache_repo.set_api_cache(
            provider,
            endpoint,
            params,
            {"results": results},
            ttl_seconds=ttl_seconds,
        )
    except Exception as error:
        if payload:
            logger.warning(
                "discover_provider_cache_fallback provider=%s endpoint=%s error=%s",
                provider,
                endpoint,
                error,
            )
            return list(payload.get("results") or [])
        logger.warning(
            "discover_provider_fetch_failed provider=%s endpoint=%s error=%s",
            provider,
            endpoint,
            error,
        )
        return []
    else:
        return results


def _safe_float(value) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _iso_date(raw) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if len(text) >= ISO_DATE_LENGTH and text[4:5] == "-" and text[7:8] == "-":
        return text[:10]
    return None


def _iso_date_from_timestamp(value) -> str | None:
    try:
        if value is None or value == "":
            return None
        timestamp = int(float(value))
        return datetime.fromtimestamp(timestamp, tz=UTC).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _openlibrary_cover_url(entry: dict) -> str:
    cover_id = (
        entry.get("cover_i")
        or entry.get("cover_id")
        or (entry.get("covers") or [None])[0]
    )
    if cover_id:
        return f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg"
    return settings.IMG_NONE


def _openlibrary_first_edition_cache_key(work_key: str) -> str:
    return f"discover:openlibrary:first_edition:{work_key}"


def _openlibrary_first_edition_id(work_key: str) -> str | None:
    """Resolve a work to its first edition id, cached across rebuilds.

    Safe to run in a worker thread: it touches only the cache and
    services.api_request, never the database.
    """
    if not work_key or not str(work_key).startswith("/works/"):
        return None

    cache_key = _openlibrary_first_edition_cache_key(work_key)
    cached_value = cache.get(cache_key)
    if cached_value is not None:
        return str(cached_value) or None

    try:
        payload = services.api_request(
            Sources.OPENLIBRARY.value,
            "GET",
            f"https://openlibrary.org{work_key}/editions.json",
            params={"limit": 1},
        )
    except Exception:
        cache.set(
            cache_key,
            "",
            timeout=OPENLIBRARY_FIRST_EDITION_FAILURE_TTL_SECONDS,
        )
        return None

    entries = payload.get("entries") or []
    edition_id = None
    if entries and isinstance(entries[0], dict):
        edition_key = entries[0].get("key")
        if isinstance(edition_key, str) and "/books/" in edition_key:
            edition_id = edition_key.rstrip("/").split("/books/")[-1]

    cache.set(
        cache_key,
        edition_id or "",
        timeout=OPENLIBRARY_FIRST_EDITION_TTL_SECONDS,
    )
    return edition_id


def _openlibrary_resolve_first_editions(
    work_keys: list[str],
) -> dict[str, str | None]:
    """Resolve many works at once, bounded in concurrency and total time.

    Returns the edition id (or None after a failed lookup) for each work that
    resolved within the budget. A work missing from the result is still being
    looked up; the caller leaves it out of this build.
    """
    resolved: dict[str, str | None] = {}
    unique_keys = list(dict.fromkeys(key for key in work_keys if key))
    if not unique_keys:
        return resolved

    cached = cache.get_many(
        [_openlibrary_first_edition_cache_key(key) for key in unique_keys],
    )
    missing: list[str] = []
    for key in unique_keys:
        value = cached.get(_openlibrary_first_edition_cache_key(key))
        if value is None:
            missing.append(key)
        else:
            resolved[key] = str(value) or None
    if not missing:
        return resolved

    executor = ThreadPoolExecutor(
        max_workers=min(OPENLIBRARY_EDITION_LOOKUP_WORKERS, len(missing)),
        thread_name_prefix="openlibrary-edition",
    )
    futures = {
        # copy_context keeps services.interactive_request_scope() in force
        # inside the worker thread.
        executor.submit(
            contextvars.copy_context().run,
            _openlibrary_first_edition_id,
            key,
        ): key
        for key in missing
    }
    deadline = time.monotonic() + OPENLIBRARY_EDITION_LOOKUP_BUDGET_SECONDS
    pending = set(futures)
    try:
        while pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            done, pending = wait(
                pending,
                timeout=remaining,
                return_when=FIRST_COMPLETED,
            )
            for future in done:
                try:
                    resolved[futures[future]] = future.result()
                except Exception:
                    logger.debug(
                        "Open Library edition lookup failed for %s",
                        futures[future],
                        exc_info=True,
                    )
                    resolved[futures[future]] = None
    finally:
        # Do not wait for lookups still running: they finish on their own
        # and cache their answer for the next build. Queued ones are dropped.
        executor.shutdown(wait=False, cancel_futures=True)

    if pending:
        logger.info(
            "Open Library edition lookups: %s of %s still pending after %ss; "
            "left out of this Discover build",
            len(pending),
            len(missing),
            OPENLIBRARY_EDITION_LOOKUP_BUDGET_SECONDS,
        )
    return resolved


def _openlibrary_entry_direct_edition_id(entry: dict) -> tuple[str | None, str | None]:
    """Return (edition id, work key still needing a lookup) for one entry."""
    direct_edition = entry.get("cover_edition_key")
    if isinstance(direct_edition, str) and direct_edition.strip():
        return direct_edition.strip(), None

    edition_keys = entry.get("edition_key") or entry.get("edition_keys")
    if isinstance(edition_keys, list):
        for edition_key in edition_keys:
            if isinstance(edition_key, str) and edition_key.strip():
                return edition_key.strip(), None

    editions = entry.get("editions")
    if isinstance(editions, list):
        for edition in editions:
            if not isinstance(edition, dict):
                continue
            key = edition.get("key")
            if isinstance(key, str) and "/books/" in key:
                return key.rstrip("/").split("/books/")[-1], None

    key = entry.get("key")
    if isinstance(key, str):
        if "/books/" in key:
            return key.rstrip("/").split("/books/")[-1], None
        if key.startswith("/works/"):
            return None, key

    work = entry.get("work")
    if isinstance(work, dict):
        work_key = work.get("key")
        if isinstance(work_key, str) and work_key.startswith("/works/"):
            return None, work_key

    return None, None


def _openlibrary_entry_edition_ids(entries: list[dict]) -> list[str | None]:
    """Return each entry's edition id, resolving works in one bounded batch."""
    direct = [_openlibrary_entry_direct_edition_id(entry) for entry in entries]
    resolved = _openlibrary_resolve_first_editions(
        [work_key for _edition_id, work_key in direct if work_key],
    )
    return [
        edition_id or (resolved.get(work_key) if work_key else None)
        for edition_id, work_key in direct
    ]


def _openlibrary_trending_candidates(
    *,
    period: str,
    row_key: str,
    source_reason: str,
    limit: int = 100,
) -> list[CandidateItem]:
    endpoint = f"/trending/{period}.json"
    params = {"limit": min(max(limit, 1), 100), "page": 1}

    def fetcher() -> list[dict]:
        payload = services.api_request(
            Sources.OPENLIBRARY.value,
            "GET",
            f"https://openlibrary.org{endpoint}",
            params=params,
        )
        works = (
            payload.get("works") or payload.get("docs") or payload.get("results") or []
        )
        if isinstance(works, dict):
            works = list(works.values())
        return [entry for entry in works if isinstance(entry, dict)]

    entries = _api_cached_results(
        Sources.OPENLIBRARY.value,
        endpoint,
        params,
        ttl_seconds=PROVIDER_DISCOVER_TTL_SECONDS,
        fetcher=fetcher,
    )

    edition_ids = _openlibrary_entry_edition_ids(entries)
    candidates: list[CandidateItem] = []
    for index, (entry, edition_id) in enumerate(
        zip(entries, edition_ids, strict=True),
        start=1,
    ):
        if not edition_id:
            continue

        title = (entry.get("title") or entry.get("name") or "").strip()
        if not title:
            continue

        publish_year = _safe_int(entry.get("first_publish_year"))
        release_date = f"{publish_year}-01-01" if publish_year else None
        popularity = _safe_float(entry.get("reading_log_count")) or _safe_float(
            entry.get("want_to_read_count"),
        )
        if popularity is None:
            popularity = float(max(len(entries) - index + 1, 1))

        subjects = entry.get("subject") or entry.get("subjects") or []
        genres = [str(subject).strip() for subject in subjects if str(subject).strip()][
            :4
        ]

        candidates.append(
            CandidateItem(
                media_type=MediaTypes.BOOK.value,
                source=Sources.OPENLIBRARY.value,
                media_id=str(edition_id),
                title=title,
                image=_openlibrary_cover_url(entry),
                release_date=release_date,
                genres=genres,
                popularity=popularity,
                row_key=row_key,
                source_reason=source_reason,
            ),
        )

    return candidates[:limit]


def _openlibrary_coming_soon_candidates(
    *,
    row_key: str,
    source_reason: str,
    limit: int = 100,
) -> list[CandidateItem]:
    endpoint = "/search.json"
    today = timezone.localdate()
    current_year = today.year
    next_year = today.year + 1
    params = {
        "q": f"publish_year:{current_year} OR publish_year:{next_year}",
        "sort": "new",
        "limit": min(max(limit, 1), 100),
        "page": 1,
        "fields": "title,key,edition_key,cover_i,first_publish_year,publish_year,subject",
    }

    def fetcher() -> list[dict]:
        payload = services.api_request(
            Sources.OPENLIBRARY.value,
            "GET",
            openlibrary.search_url,
            params=params,
        )
        docs = payload.get("docs") or []
        if isinstance(docs, dict):
            docs = [docs]
        return [entry for entry in docs if isinstance(entry, dict)]

    entries = _api_cached_results(
        Sources.OPENLIBRARY.value,
        f"{endpoint}:coming_soon",
        params,
        ttl_seconds=PROVIDER_DISCOVER_TTL_SECONDS,
        fetcher=fetcher,
    )

    edition_ids = _openlibrary_entry_edition_ids(entries)
    candidates: list[CandidateItem] = []
    for index, (entry, edition_id) in enumerate(
        zip(entries, edition_ids, strict=True),
        start=1,
    ):
        if not edition_id:
            continue

        title = (entry.get("title") or entry.get("name") or "").strip()
        if not title:
            continue

        publish_year = _safe_int(entry.get("first_publish_year"))
        if publish_year is None:
            publish_years = entry.get("publish_year") or []
            if isinstance(publish_years, list):
                filtered_years = [
                    year
                    for year in (_safe_int(year) for year in publish_years)
                    if year is not None
                ]
                if filtered_years:
                    publish_year = min(filtered_years)

        if publish_year and publish_year < current_year:
            continue

        release_date = f"{publish_year}-01-01" if publish_year else None
        popularity = _safe_float(entry.get("edition_count"))
        if popularity is None:
            popularity = float(max(len(entries) - index + 1, 1))

        subjects = entry.get("subject") or entry.get("subjects") or []
        genres = [str(subject).strip() for subject in subjects if str(subject).strip()][
            :4
        ]

        candidates.append(
            CandidateItem(
                media_type=MediaTypes.BOOK.value,
                source=Sources.OPENLIBRARY.value,
                media_id=str(edition_id),
                title=title,
                image=_openlibrary_cover_url(entry),
                release_date=release_date,
                genres=genres,
                popularity=popularity,
                row_key=row_key,
                source_reason=source_reason,
            ),
        )

    return candidates[:limit]


def _comicvine_volume_candidates(
    *,
    sort: str,
    row_key: str,
    source_reason: str,
    limit: int = 100,
) -> list[CandidateItem]:
    endpoint = "/volumes/"
    params = {
        "api_key": credentials.get("comicvine", "api_key"),
        "format": "json",
        "field_list": "id,name,image,start_year,count_of_issues,date_last_updated",
        "sort": sort,
        "limit": min(max(limit, 1), 100),
        "offset": 0,
    }

    def fetcher() -> list[dict]:
        payload = services.api_request(
            Sources.COMICVINE.value,
            "GET",
            f"{comicvine.base_url}{endpoint}",
            params=params,
            headers=comicvine.headers,
        )
        return [
            entry for entry in (payload.get("results") or []) if isinstance(entry, dict)
        ]

    entries = _api_cached_results(
        Sources.COMICVINE.value,
        endpoint,
        params,
        ttl_seconds=PROVIDER_DISCOVER_TTL_SECONDS,
        fetcher=fetcher,
    )
    candidates: list[CandidateItem] = []
    for index, entry in enumerate(entries, start=1):
        media_id = _safe_int(entry.get("id"))
        title = (entry.get("name") or "").strip()
        if not media_id or not title:
            continue

        start_year = _safe_int(entry.get("start_year"))
        release_date = f"{start_year}-01-01" if start_year else None
        popularity = _safe_float(entry.get("count_of_issues"))
        if popularity is None:
            popularity = float(max(len(entries) - index + 1, 1))

        candidates.append(
            CandidateItem(
                media_type=MediaTypes.COMIC.value,
                source=Sources.COMICVINE.value,
                media_id=str(media_id),
                title=title,
                image=comicvine.get_image(entry),
                release_date=release_date,
                popularity=popularity,
                row_key=row_key,
                source_reason=source_reason,
            ),
        )

    return candidates[:limit]


def _comicvine_coming_soon_volume_candidates(
    *,
    row_key: str,
    source_reason: str,
    limit: int = 100,
) -> list[CandidateItem]:
    endpoint = "/issues/"
    start_date = timezone.localdate().isoformat()
    end_date = (
        timezone.localdate() + timedelta(days=PROVIDER_COMING_SOON_WINDOW_DAYS)
    ).isoformat()
    params = {
        "api_key": credentials.get("comicvine", "api_key"),
        "format": "json",
        "field_list": "id,name,issue_number,store_date,cover_date,image,volume",
        "filter": f"store_date:{start_date}|{end_date}",
        "sort": "store_date:asc",
        "limit": min(max(limit * 2, 20), 200),
        "offset": 0,
    }

    def fetcher() -> list[dict]:
        payload = services.api_request(
            Sources.COMICVINE.value,
            "GET",
            f"{comicvine.base_url}{endpoint}",
            params=params,
            headers=comicvine.headers,
        )
        return [
            entry for entry in (payload.get("results") or []) if isinstance(entry, dict)
        ]

    entries = _api_cached_results(
        Sources.COMICVINE.value,
        f"{endpoint}:coming_soon",
        params,
        ttl_seconds=PROVIDER_DISCOVER_TTL_SECONDS,
        fetcher=fetcher,
    )

    earliest_issue_by_volume: dict[int, dict] = {}
    for entry in entries:
        volume = entry.get("volume") or {}
        volume_id = _safe_int(volume.get("id"))
        if not volume_id:
            continue
        release_date = _iso_date(entry.get("store_date")) or _iso_date(
            entry.get("cover_date")
        )
        existing = earliest_issue_by_volume.get(volume_id)
        if existing is None:
            earliest_issue_by_volume[volume_id] = {
                "volume_name": str(volume.get("name") or "").strip(),
                "release_date": release_date,
                "image": comicvine.get_image(entry),
            }
            continue

        existing_release = existing.get("release_date")
        if release_date and (not existing_release or release_date < existing_release):
            earliest_issue_by_volume[volume_id] = {
                "volume_name": str(volume.get("name") or "").strip(),
                "release_date": release_date,
                "image": comicvine.get_image(entry),
            }

    candidates: list[CandidateItem] = []
    sorted_volumes = sorted(
        earliest_issue_by_volume.items(),
        key=lambda item: (item[1].get("release_date") or "9999-12-31", item[0]),
    )
    for index, (volume_id, payload) in enumerate(sorted_volumes, start=1):
        title = payload.get("volume_name") or ""
        if not title:
            continue
        candidates.append(
            CandidateItem(
                media_type=MediaTypes.COMIC.value,
                source=Sources.COMICVINE.value,
                media_id=str(volume_id),
                title=title,
                image=payload.get("image") or settings.IMG_NONE,
                release_date=payload.get("release_date"),
                popularity=float(max(len(sorted_volumes) - index + 1, 1)),
                row_key=row_key,
                source_reason=source_reason,
            ),
        )
        if len(candidates) >= limit:
            break

    return candidates[:limit]


def _bgg_hot_candidates(
    *,
    row_key: str,
    source_reason: str,
    limit: int = 100,
) -> list[CandidateItem]:
    endpoint = "/xmlapi2/hot"
    params = {"type": "boardgame"}
    headers = {"Authorization": f"Bearer {credentials.get("bgg", "token")}"}

    def fetcher() -> list[dict]:
        root = services.api_request(
            Sources.BGG.value,
            "GET",
            f"{bgg.base_url}/hot",
            params=params,
            headers=headers,
            response_format="xml",
        )
        entries: list[dict] = []
        for item in root.findall(".//item"):
            name_node = item.find("name")
            year_node = item.find("yearpublished")
            entries.append(
                {
                    "id": item.get("id"),
                    "rank": item.get("rank"),
                    "title": name_node.get("value") if name_node is not None else None,
                    "year": year_node.get("value") if year_node is not None else None,
                },
            )
        return entries

    entries = _api_cached_results(
        Sources.BGG.value,
        endpoint,
        params,
        ttl_seconds=PROVIDER_DISCOVER_TTL_SECONDS,
        fetcher=fetcher,
    )

    ids = [str(entry.get("id")) for entry in entries[:limit] if entry.get("id")]
    thumbnails = bgg._fetch_thumbnails(ids) if ids else {}

    candidates: list[CandidateItem] = []
    for index, entry in enumerate(entries, start=1):
        media_id = str(entry.get("id") or "").strip()
        title = str(entry.get("title") or "").strip()
        if not media_id or not title:
            continue
        release_year = _safe_int(entry.get("year"))
        release_date = f"{release_year}-01-01" if release_year else None
        popularity = _safe_float(entry.get("rank"))
        if popularity is None:
            popularity = float(max(len(entries) - index + 1, 1))
        else:
            popularity = max(1.0, 1000.0 - popularity)

        candidates.append(
            CandidateItem(
                media_type=MediaTypes.BOARDGAME.value,
                source=Sources.BGG.value,
                media_id=media_id,
                title=title,
                image=thumbnails.get(media_id, settings.IMG_NONE),
                release_date=release_date,
                popularity=popularity,
                row_key=row_key,
                source_reason=source_reason,
            ),
        )

    return candidates[:limit]


def _musicbrainz_coming_soon_recording_candidates(
    *,
    row_key: str,
    source_reason: str,
    limit: int = 100,
) -> list[CandidateItem]:
    endpoint = "/recording/"
    start_date = timezone.localdate().isoformat()
    end_date = (
        timezone.localdate() + timedelta(days=PROVIDER_COMING_SOON_WINDOW_DAYS)
    ).isoformat()
    params = {
        "query": f"firstreleasedate:[{start_date} TO {end_date}]",
        "limit": min(max(limit, 1), 100),
        "offset": 0,
        "fmt": "json",
        "inc": "artist-credits+releases+release-groups",
    }

    def fetcher() -> list[dict]:
        payload = services.api_request(
            Sources.MUSICBRAINZ.value,
            "GET",
            f"{musicbrainz.BASE_URL}/recording/",
            params=params,
            headers={
                "User-Agent": musicbrainz.USER_AGENT,
                "Accept": "application/json",
            },
        )
        recordings = payload.get("recordings") or []
        if isinstance(recordings, dict):
            recordings = [recordings]
        return [entry for entry in recordings if isinstance(entry, dict)]

    entries = _api_cached_results(
        Sources.MUSICBRAINZ.value,
        f"{endpoint}:coming_soon",
        params,
        ttl_seconds=PROVIDER_DISCOVER_TTL_SECONDS,
        fetcher=fetcher,
    )

    candidates: list[CandidateItem] = []
    for index, entry in enumerate(entries, start=1):
        media_id = str(entry.get("id") or "").strip()
        title = str(entry.get("title") or "").strip()
        if not media_id or not title:
            continue

        artist_credits = entry.get("artist-credit") or []
        artist_name_parts: list[str] = []
        if isinstance(artist_credits, list):
            for credit in artist_credits:
                if not isinstance(credit, dict):
                    continue
                artist = credit.get("artist") or {}
                artist_name_parts.append(
                    str(credit.get("name") or artist.get("name") or "").strip(),
                )
                artist_name_parts.append(str(credit.get("joinphrase") or ""))
        artist_name = "".join(part for part in artist_name_parts if part).strip()

        release_date = _iso_date(entry.get("first-release-date"))
        image = settings.IMG_NONE
        releases = entry.get("releases") or []
        if isinstance(releases, list):
            selected_release = None
            for release in releases:
                if not isinstance(release, dict):
                    continue
                if release.get("date"):
                    selected_release = release
                    break
            if selected_release is None and releases:
                selected_release = releases[0]
            if isinstance(selected_release, dict):
                release_date = release_date or _iso_date(selected_release.get("date"))

        display_title = title if not artist_name else f"{title} - {artist_name}"
        popularity = _safe_float(entry.get("score")) or float(
            max(len(entries) - index + 1, 1)
        )
        candidates.append(
            CandidateItem(
                media_type=MediaTypes.MUSIC.value,
                source=Sources.MUSICBRAINZ.value,
                media_id=media_id,
                title=display_title,
                image=image,
                release_date=release_date,
                popularity=popularity,
                row_key=row_key,
                source_reason=source_reason,
            ),
        )

    return candidates[:limit]


def _itunes_top_podcasts_candidates(
    *,
    row_key: str,
    source_reason: str,
    limit: int = 100,
) -> list[CandidateItem]:
    endpoint = "/itunes/top-podcasts"
    params = {"country": "us", "limit": min(max(limit, 1), 100)}

    def fetcher() -> list[dict]:
        payload = services.api_request(
            Sources.POCKETCASTS.value,
            "GET",
            f"https://itunes.apple.com/us/rss/toppodcasts/limit={params['limit']}/json",
        )
        entries = (payload.get("feed") or {}).get("entry") or []
        if isinstance(entries, dict):
            entries = [entries]
        return [entry for entry in entries if isinstance(entry, dict)]

    entries = _api_cached_results(
        Sources.POCKETCASTS.value,
        endpoint,
        params,
        ttl_seconds=PROVIDER_DISCOVER_TTL_SECONDS,
        fetcher=fetcher,
    )

    candidates: list[CandidateItem] = []
    for index, entry in enumerate(entries, start=1):
        media_id = ((entry.get("id") or {}).get("attributes") or {}).get("im:id") or (
            (entry.get("id") or {}).get("label") or ""
        ).strip().rsplit("/", 1)[-1]
        title = ((entry.get("im:name") or {}).get("label") or "").strip()
        if not media_id or not title:
            continue

        image = settings.IMG_NONE
        images = entry.get("im:image") or []
        if isinstance(images, list) and images:
            image = ((images[-1] or {}).get("label") or "").strip() or settings.IMG_NONE
        release_text = ((entry.get("im:releaseDate") or {}).get("label")) or (
            (entry.get("im:releaseDate") or {}).get("attributes") or {}
        ).get("label")
        release_date = _iso_date(release_text)
        popularity = float(max(len(entries) - index + 1, 1))

        candidates.append(
            CandidateItem(
                media_type=MediaTypes.PODCAST.value,
                source=Sources.POCKETCASTS.value,
                media_id=str(media_id),
                title=title,
                image=image,
                release_date=release_date,
                popularity=popularity,
                row_key=row_key,
                source_reason=source_reason,
            ),
        )

    return candidates[:limit]


def _lastfm_top_tracks_candidates(
    *,
    row_key: str,
    source_reason: str,
    limit: int = 100,
) -> list[CandidateItem]:
    if not credentials.get("lastfm", "api_key"):
        return []

    endpoint = "/2.0/chart.gettoptracks"
    params = {
        "method": "chart.gettoptracks",
        "api_key": credentials.get("lastfm", "api_key"),
        "format": "json",
        "limit": min(max(limit, 1), 200),
    }

    def fetcher() -> list[dict]:
        payload = services.api_request(
            "LASTFM",
            "GET",
            "https://ws.audioscrobbler.com/2.0/",
            params=params,
        )
        tracks = (payload.get("tracks") or {}).get("track") or []
        if isinstance(tracks, dict):
            tracks = [tracks]
        return [track for track in tracks if isinstance(track, dict)]

    tracks = _api_cached_results(
        Sources.MUSICBRAINZ.value,
        endpoint,
        params,
        ttl_seconds=PROVIDER_DISCOVER_TTL_SECONDS,
        fetcher=fetcher,
    )

    candidates: list[CandidateItem] = []
    for track in tracks:
        mbid = str(track.get("mbid") or "").strip()
        title = str(track.get("name") or "").strip()
        if not mbid or not title:
            continue
        artist_info = track.get("artist") or {}
        artist_name = (
            artist_info.get("name")
            if isinstance(artist_info, dict)
            else str(artist_info)
        )
        images = track.get("image") or []
        image = settings.IMG_NONE
        if isinstance(images, list):
            for img in reversed(images):
                if not isinstance(img, dict):
                    continue
                image_value = str(img.get("#text") or "").strip()
                if image_value:
                    image = image_value
                    break

        listeners = _safe_float(track.get("listeners"))
        playcount = _safe_float(track.get("playcount"))
        popularity = playcount if playcount is not None else listeners

        display_title = title if not artist_name else f"{title} - {artist_name}"
        candidates.append(
            CandidateItem(
                media_type=MediaTypes.MUSIC.value,
                source=Sources.MUSICBRAINZ.value,
                media_id=mbid,
                title=display_title,
                image=image,
                popularity=popularity,
                row_key=row_key,
                source_reason=source_reason,
            ),
        )
        if len(candidates) >= limit:
            break

    return candidates[:limit]


def _mal_ranking_candidates(
    *,
    media_type: str,
    ranking_type: str,
    row_key: str,
    source_reason: str,
    limit: int = 100,
) -> list[CandidateItem]:
    endpoint = f"/{media_type}/ranking"
    params = {
        "ranking_type": ranking_type,
        "limit": min(max(limit, 1), 100),
        "fields": "media_type,start_date,genres,mean,num_scoring_users,main_picture,alternative_titles",
    }
    if settings.MAL_NSFW:
        params["nsfw"] = "true"

    def fetcher() -> list[dict]:
        payload = services.api_request(
            Sources.MAL.value,
            "GET",
            f"{mal.base_url}{endpoint}",
            params=params,
            headers={"X-MAL-CLIENT-ID": credentials.get("mal", "client_id")},
        )
        return [
            entry for entry in (payload.get("data") or []) if isinstance(entry, dict)
        ]

    entries = _api_cached_results(
        Sources.MAL.value,
        f"{endpoint}:{ranking_type}",
        params,
        ttl_seconds=PROVIDER_DISCOVER_TTL_SECONDS,
        fetcher=fetcher,
    )
    candidates: list[CandidateItem] = []
    for index, entry in enumerate(entries, start=1):
        node = entry.get("node") or {}
        media_id = _safe_int(node.get("id"))
        title = (mal.get_localized_title(node) or node.get("title") or "").strip()
        if not media_id or not title:
            continue
        image = mal.get_image_url(node)
        genres = [
            str(genre.get("name")).strip()
            for genre in (node.get("genres") or [])
            if isinstance(genre, dict) and str(genre.get("name") or "").strip()
        ]
        ranking = entry.get("ranking") or {}
        popularity = _safe_float(ranking.get("rank"))
        if popularity is None:
            popularity = float(max(len(entries) - index + 1, 1))
        else:
            popularity = max(1.0, 1000.0 - popularity)

        candidates.append(
            CandidateItem(
                media_type=media_type,
                source=Sources.MAL.value,
                media_id=str(media_id),
                title=title,
                original_title=node.get("title") or title,
                localized_title=title,
                image=image,
                release_date=_iso_date(node.get("start_date")),
                genres=genres,
                popularity=popularity,
                rating=_safe_float(node.get("mean")),
                rating_count=_safe_int(node.get("num_scoring_users")),
                row_key=row_key,
                source_reason=source_reason,
            ),
        )

    return candidates[:limit]


def _mal_manga_ranking_candidates(
    *,
    ranking_type: str,
    row_key: str,
    source_reason: str,
    limit: int = 100,
) -> list[CandidateItem]:
    return _mal_ranking_candidates(
        media_type=MediaTypes.MANGA.value,
        ranking_type=ranking_type,
        row_key=row_key,
        source_reason=source_reason,
        limit=limit,
    )


def _mal_anime_ranking_candidates(
    *,
    ranking_type: str,
    row_key: str,
    source_reason: str,
    limit: int = 100,
) -> list[CandidateItem]:
    return _mal_ranking_candidates(
        media_type=MediaTypes.ANIME.value,
        ranking_type=ranking_type,
        row_key=row_key,
        source_reason=source_reason,
        limit=limit,
    )


def _mal_anime_fallback_candidates(row_key: str) -> list[CandidateItem]:
    ranking_by_row = {
        "trending_right_now": ("airing", "MAL airing ranking"),
        "all_time_greats_unseen": ("bypopularity", "MAL popular ranking"),
        "coming_soon": ("upcoming", "MAL upcoming ranking"),
    }
    ranking = ranking_by_row.get(row_key)
    if ranking is None:
        return []

    ranking_type, source_reason = ranking
    candidates = _mal_anime_ranking_candidates(
        ranking_type=ranking_type,
        row_key=row_key,
        source_reason=source_reason,
        limit=100,
    )
    if candidates or row_key != "coming_soon":
        return candidates

    return _mal_anime_ranking_candidates(
        ranking_type="airing",
        row_key=row_key,
        source_reason="MAL airing fallback",
        limit=100,
    )


def _igdb_games_candidates(
    *,
    query: str,
    endpoint_key: str,
    row_key: str,
    source_reason: str,
    limit: int = 100,
) -> list[CandidateItem]:
    params = {"query": query, "limit": min(max(limit, 1), 100)}

    def fetcher() -> list[dict]:
        access_token = igdb.get_access_token()
        payload = services.api_request(
            Sources.IGDB.value,
            "POST",
            f"{igdb.base_url}/games",
            data=query,
            headers={
                "Client-ID": credentials.get("igdb", "client_id"),
                "Authorization": f"Bearer {access_token}",
            },
        )
        return [entry for entry in payload if isinstance(entry, dict)]

    entries = _api_cached_results(
        Sources.IGDB.value,
        endpoint_key,
        params,
        ttl_seconds=PROVIDER_DISCOVER_TTL_SECONDS,
        fetcher=fetcher,
    )

    candidates: list[CandidateItem] = []
    for index, entry in enumerate(entries, start=1):
        media_id = _safe_int(entry.get("id"))
        title = (entry.get("name") or "").strip()
        if not media_id or not title:
            continue
        genres = [
            str(genre.get("name")).strip()
            for genre in (entry.get("genres") or [])
            if isinstance(genre, dict) and str(genre.get("name") or "").strip()
        ]
        popularity = _safe_float(entry.get("total_rating_count"))
        if popularity is None:
            popularity = float(max(len(entries) - index + 1, 1))
        candidates.append(
            CandidateItem(
                media_type=MediaTypes.GAME.value,
                source=Sources.IGDB.value,
                media_id=str(media_id),
                title=title,
                image=igdb.get_image_url(entry),
                release_date=_iso_date_from_timestamp(entry.get("first_release_date")),
                genres=genres,
                popularity=popularity,
                rating=_safe_float(entry.get("total_rating")),
                rating_count=_safe_int(entry.get("total_rating_count")),
                row_key=row_key,
                source_reason=source_reason,
            ),
        )

    return candidates[:limit]


def _provider_row_candidates(media_type: str, row_key: str) -> list[CandidateItem]:
    if row_key == "trending_right_now":
        if media_type == MediaTypes.MOVIE.value:
            return TRAKT_ADAPTER.movie_watched_weekly(limit=100)
        if media_type == MediaTypes.TV.value:
            return TRAKT_ADAPTER.show_watched_weekly(
                limit=100,
                media_type=MediaTypes.TV.value,
            )
        if media_type == MediaTypes.ANIME.value:
            return TRAKT_ADAPTER.show_watched_weekly(
                limit=100,
                media_type=MediaTypes.ANIME.value,
                trakt_genres=["anime"],
            )
        if media_type == MediaTypes.MANGA.value:
            return _mal_manga_ranking_candidates(
                ranking_type="manga",
                row_key=row_key,
                source_reason="MAL ranking",
                limit=100,
            )
        if media_type == MediaTypes.GAME.value:
            recent_cutoff = int((timezone.now() - timedelta(days=90)).timestamp())
            return _igdb_games_candidates(
                query=(
                    "fields name,cover.image_id,first_release_date,total_rating,total_rating_count,genres.name;"
                    f" where first_release_date != null & first_release_date > {recent_cutoff};"
                    " sort total_rating_count desc;"
                    " limit 100;"
                ),
                endpoint_key="/games/trending_right_now",
                row_key=row_key,
                source_reason="IGDB recent popular",
                limit=100,
            )
        if media_type == MediaTypes.BOOK.value:
            return _openlibrary_trending_candidates(
                period="daily",
                row_key=row_key,
                source_reason="Open Library trending",
                limit=100,
            )
        if media_type == MediaTypes.COMIC.value:
            return _comicvine_volume_candidates(
                sort="date_last_updated:desc",
                row_key=row_key,
                source_reason="Comic Vine recently active",
                limit=100,
            )
        if media_type == MediaTypes.BOARDGAME.value:
            return _bgg_hot_candidates(
                row_key=row_key,
                source_reason="BGG hotness",
                limit=100,
            )
        if media_type == MediaTypes.PODCAST.value:
            return _itunes_top_podcasts_candidates(
                row_key=row_key,
                source_reason="iTunes top podcasts",
                limit=100,
            )
        if media_type == MediaTypes.MUSIC.value:
            return _lastfm_top_tracks_candidates(
                row_key=row_key,
                source_reason="Last.fm chart top tracks",
                limit=100,
            )

    if row_key == "all_time_greats_unseen":
        if media_type == MediaTypes.MOVIE.value:
            return TRAKT_ADAPTER.movie_popular(page=1, limit=TRAKT_POPULAR_PAGE_SIZE)
        if media_type == MediaTypes.TV.value:
            return TRAKT_ADAPTER.show_popular(
                page=1,
                limit=TRAKT_POPULAR_PAGE_SIZE,
                media_type=MediaTypes.TV.value,
            )
        if media_type == MediaTypes.ANIME.value:
            return TRAKT_ADAPTER.show_popular(
                page=1,
                limit=TRAKT_POPULAR_PAGE_SIZE,
                media_type=MediaTypes.ANIME.value,
                trakt_genres=["anime"],
            )
        if media_type == MediaTypes.MANGA.value:
            return _mal_manga_ranking_candidates(
                ranking_type="bypopularity",
                row_key=row_key,
                source_reason="MAL popular ranking",
                limit=100,
            )
        if media_type == MediaTypes.GAME.value:
            return _igdb_games_candidates(
                query=(
                    "fields name,cover.image_id,first_release_date,total_rating,total_rating_count,genres.name;"
                    " where first_release_date != null;"
                    " sort total_rating_count desc;"
                    " limit 100;"
                ),
                endpoint_key="/games/all_time_greats_unseen",
                row_key=row_key,
                source_reason="IGDB all-time popular",
                limit=100,
            )
        if media_type == MediaTypes.BOOK.value:
            return _openlibrary_trending_candidates(
                period="monthly",
                row_key=row_key,
                source_reason="Open Library monthly popular",
                limit=100,
            )
        if media_type == MediaTypes.COMIC.value:
            return _comicvine_volume_candidates(
                sort="count_of_issues:desc",
                row_key=row_key,
                source_reason="Comic Vine long-running volumes",
                limit=100,
            )
        if media_type == MediaTypes.BOARDGAME.value:
            return _bgg_hot_candidates(
                row_key=row_key,
                source_reason="BGG top hotness",
                limit=100,
            )
        if media_type == MediaTypes.PODCAST.value:
            return _itunes_top_podcasts_candidates(
                row_key=row_key,
                source_reason="iTunes top podcasts",
                limit=100,
            )
        if media_type == MediaTypes.MUSIC.value:
            return _lastfm_top_tracks_candidates(
                row_key=row_key,
                source_reason="Last.fm chart top tracks",
                limit=100,
            )

    if row_key == "coming_soon":
        if media_type == MediaTypes.MOVIE.value:
            return TRAKT_ADAPTER.movie_anticipated(
                page=1, limit=TRAKT_POPULAR_PAGE_SIZE
            )
        if media_type == MediaTypes.TV.value:
            return TRAKT_ADAPTER.show_anticipated(
                page=1,
                limit=TRAKT_POPULAR_PAGE_SIZE,
                media_type=MediaTypes.TV.value,
            )
        if media_type == MediaTypes.ANIME.value:
            return TRAKT_ADAPTER.show_anticipated(
                page=1,
                limit=TRAKT_POPULAR_PAGE_SIZE,
                media_type=MediaTypes.ANIME.value,
                trakt_genres=["anime"],
            )
        if media_type == MediaTypes.MANGA.value:
            candidates = _mal_manga_ranking_candidates(
                ranking_type="upcoming",
                row_key=row_key,
                source_reason="MAL upcoming ranking",
                limit=100,
            )
            if candidates:
                return candidates
            return _mal_manga_ranking_candidates(
                ranking_type="manga",
                row_key=row_key,
                source_reason="MAL upcoming fallback",
                limit=100,
            )
        if media_type == MediaTypes.GAME.value:
            now_ts = int(timezone.now().timestamp())
            return _igdb_games_candidates(
                query=(
                    "fields name,cover.image_id,first_release_date,total_rating,total_rating_count,genres.name;"
                    f" where first_release_date != null & first_release_date > {now_ts};"
                    " sort first_release_date asc;"
                    " limit 100;"
                ),
                endpoint_key="/games/coming_soon",
                row_key=row_key,
                source_reason="IGDB upcoming releases",
                limit=100,
            )
        if media_type == MediaTypes.BOOK.value:
            candidates = _openlibrary_coming_soon_candidates(
                row_key=row_key,
                source_reason="Open Library upcoming releases",
                limit=100,
            )
            if candidates:
                return candidates
            return _openlibrary_trending_candidates(
                period="daily",
                row_key=row_key,
                source_reason="Open Library upcoming fallback",
                limit=100,
            )
        if media_type == MediaTypes.COMIC.value:
            candidates = _comicvine_coming_soon_volume_candidates(
                row_key=row_key,
                source_reason="Comic Vine upcoming issues",
                limit=100,
            )
            if candidates:
                return candidates
            return _comicvine_volume_candidates(
                sort="date_last_updated:desc",
                row_key=row_key,
                source_reason="Comic Vine upcoming fallback",
                limit=100,
            )
        if media_type == MediaTypes.BOARDGAME.value:
            return _bgg_hot_candidates(
                row_key=row_key,
                source_reason="BGG upcoming fallback",
                limit=100,
            )
        if media_type == MediaTypes.PODCAST.value:
            return _itunes_top_podcasts_candidates(
                row_key=row_key,
                source_reason="iTunes upcoming fallback",
                limit=100,
            )
        if media_type == MediaTypes.MUSIC.value:
            candidates = _musicbrainz_coming_soon_recording_candidates(
                row_key=row_key,
                source_reason="MusicBrainz upcoming releases",
                limit=100,
            )
            if candidates:
                return candidates
            return _lastfm_top_tracks_candidates(
                row_key=row_key,
                source_reason="Last.fm upcoming fallback",
                limit=100,
            )

    return []
