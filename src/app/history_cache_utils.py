"""Cache-key constants, key-derivation utilities, and low-level helpers for the History cache."""

import logging
import sys
import time
import uuid
from datetime import datetime, timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import models
from django.utils import timezone

from app.log_safety import stable_hmac
from app.models import MediaTypes, Music
from app.public_errors import PublicMessageError
from app.stats_utils import _coerce_genre_list

logger = logging.getLogger(__name__)


class UnsupportedHistoryMediaTypeError(PublicMessageError, ValueError):
    """Raised when a history API media-type filter is unknown."""

    def __init__(self, token):
        """Build an error containing the rejected token."""
        super().__init__(f"Unsupported history media type: {token}")


def history_entry_sort_key(entry):
    """Order a history row newest first.

    Callers sort with ``reverse=True``. ``played_at_local`` is the real
    order. YouTube only has a calendar day, so every play that day shares
    noon and this key would otherwise tie. ``instance_id`` is the row id.
    The sync posts a batch oldest-first, so the higher id is the later watch.

    @param entry - History entry dict.
    @returns Tuple of played-at, season, episode, and row id.
    """
    return (
        entry["played_at_local"],
        entry.get("season_number") or 0,
        entry.get("episode_number") or 0,
        entry.get("instance_id") or 0,
    )


# ── Constants ────────────────────────────────────────────────────────────────


def _coerce_timedelta(value, default):
    if value is None:
        return default
    if isinstance(value, timedelta):
        return value
    try:
        return timedelta(seconds=int(value))
    except (TypeError, ValueError):
        return default


HISTORY_CACHE_VERSION = 27
HISTORY_INDEX_PREFIX = f"history_index_v{HISTORY_CACHE_VERSION}"
HISTORY_DAY_PREFIX = f"history_day_v{HISTORY_CACHE_VERSION}"
HISTORY_CACHE_PREFIX = HISTORY_INDEX_PREFIX
HISTORY_CACHE_TIMEOUT = 60 * 60 * 6  # 6 hours for the history index
# Era keys must outlive any index published under them: if the era key
# expires while its typed indexes are still alive, those indexes are
# orphaned and readers rebuild — correct, but wasteful. 24h comfortably
# covers the 6h index TTL even when nothing refreshes it.
HISTORY_ERA_TIMEOUT = 60 * 60 * 24
# Finite so day payloads are evictable: Redis runs volatile-lru, which only
# evicts keys that carry a TTL, so payloads without one crowded out sessions
# and page caches instead. Expired days are rebuilt by the coverage repair.
HISTORY_DAY_CACHE_TIMEOUT = getattr(
    settings, "HISTORY_DAY_CACHE_TIMEOUT", 30 * 24 * 60 * 60
)
HISTORY_STALE_AFTER = _coerce_timedelta(
    getattr(settings, "HISTORY_CACHE_STALE_AFTER", None),
    timedelta(hours=1),
)
HISTORY_DAYS_PER_PAGE = 30
HISTORY_ENTRIES_PER_DAY_PAGE = 30

HISTORY_UNREAD_ITEM_FIELDS = (
    "watch_providers",
    "synopsis",
    "provider_keywords",
    "provider_game_lengths",
    "themes",
    "studios",
    "languages",
    "creators",
    "authors",
    "isbn",
    "platforms",
    "manual_metadata",
    "provider_collection_name",
    "source_url",
    "publishers",
    "series_name",
    "source_material",
)


def history_deferred_item_fields(*relations):
    """Return the item columns history never reads, per select_related path.

    History builds cards from a handful of item columns (title, image, genres,
    numbers, runtime). It reads none of these. ``watch_providers`` is the one
    that matters: TMDB's availability for every region it knows, around 146 KiB
    a title. An episode row select_relates three items -- the episode, its
    season and its show -- so a filtered history request decoded it three times
    per play. On a 6,454-play filtered request that cost a web worker ~790 MiB
    of anonymous memory to return a 56 KiB response (#1180 follow-up).

    Deferring rather than ``only()`` is the safe direction: an unforeseen
    reader loads the column late instead of seeing it missing.

    Pass the select_related paths that reach an item ("item",
    "related_season__item", ...); pass "" for the item model itself.
    """
    return tuple(
        f"{relation}__{field}" if relation else field
        for relation in relations
        for field in HISTORY_UNREAD_ITEM_FIELDS
    )


HISTORY_WARM_DAYS = getattr(settings, "HISTORY_CACHE_WARM_DAYS", 0)
HISTORY_COLD_MISS_WARM_DAYS = getattr(
    settings,
    "HISTORY_CACHE_COLD_MISS_WARM_DAYS",
    HISTORY_DAYS_PER_PAGE,
)
HISTORY_REFRESH_LOCK_PREFIX = f"history_refresh_lock_v{HISTORY_CACHE_VERSION}"
HISTORY_REFRESH_LOCK_MAX_AGE = timedelta(minutes=5)  # safety to clear stuck locks
HISTORY_COVERAGE_REPAIR_PREFIX = f"history_day_coverage_v{HISTORY_CACHE_VERSION}"
HISTORY_COVERAGE_REPAIR_BATCH_SIZE = getattr(
    settings,
    "HISTORY_COVERAGE_REPAIR_BATCH_SIZE",
    120,
)
HISTORY_COVERAGE_REPAIR_LOCK_TTL = getattr(
    settings,
    "HISTORY_COVERAGE_REPAIR_LOCK_TTL",
    60 * 30,
)
HISTORY_COVERAGE_WINDOW_DAYS = getattr(settings, "HISTORY_COVERAGE_WINDOW_DAYS", 62)
HISTORY_COVERAGE_BOTH_STYLES = getattr(
    settings, "HISTORY_COVERAGE_BOTH_STYLES", False
)


def history_coverage_day_keys(index_day_keys):
    """Return the indexed days whose payloads are built ahead of a visit.

    The days of the last ``HISTORY_COVERAGE_WINDOW_DAYS`` (the month view's
    current and previous month), and never fewer than one page, so a user who
    has been away still finds their latest page warm. Everything older is
    built when a page asks for it. ``index_day_keys`` is newest first.
    """
    keys = list(index_day_keys or [])
    if HISTORY_COVERAGE_WINDOW_DAYS <= 0:
        return keys
    cutoff = timezone.localdate() - timedelta(days=HISTORY_COVERAGE_WINDOW_DAYS)
    cutoff_key = cutoff.strftime("%Y%m%d")
    recent = sum(1 for key in keys if str(key) >= cutoff_key)
    return keys[: max(recent, HISTORY_DAYS_PER_PAGE)]


def history_coverage_styles(user):
    """Return the logging styles whose day payloads are built ahead of a visit.

    Only the style the user reads, unless the operator asked for both. The
    other style is still invalidated on every write; it is built on demand.
    """
    if HISTORY_COVERAGE_BOTH_STYLES or user is None:
        return HISTORY_LOGGING_STYLES
    return (_normalize_logging_style(None, user),)

DAY_KEY_LENGTH = 8  # length of a YYYYMMDD day key string


def apply_history_entry_cap(history_days, cap):
    """Truncate each day's entries to `cap`, annotating entry_count/entries_truncated.

    A day's raw entry count is unbounded (imports, binge sessions, frequent
    podcast scrobbles), so every history read path that returns per-day
    entries must cap them before serialization — shared here so the windowed
    API path and the flat/date-filtered bypass path stay in sync (#1004).
    """
    total_entries = 0
    for day_payload in history_days:
        entries = day_payload.get("entries", [])
        entry_count = day_payload.get("entry_count", len(entries))
        total_entries += entry_count
        if entry_count > cap:
            day_payload["entries"] = entries[:cap]
        day_payload["entry_count"] = entry_count
        day_payload["entries_truncated"] = entry_count > len(day_payload["entries"])
    return total_entries


_HISTORY_MEDIA_TYPE_ALIASES = {
    "show": MediaTypes.TV.value,
    "shows": MediaTypes.TV.value,
    "tvs": MediaTypes.TV.value,
    "episodes": MediaTypes.EPISODE.value,
    "movies": MediaTypes.MOVIE.value,
    "animes": MediaTypes.ANIME.value,
    "mangas": MediaTypes.MANGA.value,
    "games": MediaTypes.GAME.value,
    "books": MediaTypes.BOOK.value,
    "comics": MediaTypes.COMIC.value,
    "boardgames": MediaTypes.BOARDGAME.value,
    "board_games": MediaTypes.BOARDGAME.value,
    "musics": MediaTypes.MUSIC.value,
    "podcasts": MediaTypes.PODCAST.value,
    "videos": MediaTypes.VIDEO.value,
}


def normalize_history_media_type_tokens(values):
    """Normalize history media-type values without expanding TV aliases."""
    if values is None:
        return None
    if isinstance(values, str):
        values = [values]

    normalized = set()
    for value in values:
        for raw_token in str(value or "").split(","):
            token = raw_token.strip().lower()
            if not token:
                continue
            canonical = _HISTORY_MEDIA_TYPE_ALIASES.get(token, token)
            if canonical not in {media_type.value for media_type in MediaTypes}:
                raise UnsupportedHistoryMediaTypeError(token)
            normalized.add(canonical)
    return normalized


def expand_history_media_types(values):
    """Return the concrete history entry types represented by a filter."""
    normalized = normalize_history_media_type_tokens(values)
    if normalized is None:
        return None
    expanded = set(normalized)
    if MediaTypes.TV.value in normalized:
        expanded.update(
            {
                MediaTypes.TV.value,
                MediaTypes.SEASON.value,
                MediaTypes.EPISODE.value,
            },
        )
    return expanded


# ── Query helpers ─────────────────────────────────────────────────────────────


def _music_history_user_q(user):
    user_id = getattr(user, "id", user)
    owned_music_ids = Music.objects.filter(user_id=user_id).values("id")
    return models.Q(history_user_id=user_id) | (
        models.Q(history_user__isnull=True) & models.Q(id__in=owned_music_ids)
    )


# ── Cache key functions ───────────────────────────────────────────────────────


def _cache_key(user_id: int, logging_style: str) -> str:
    return f"{HISTORY_CACHE_PREFIX}_{user_id}_{logging_style or 'repeats'}"


def _history_era_key(user_id: int, logging_style: str) -> str:
    """Return the key holding the current index era token for a user/style."""
    return (
        f"history_era_v{HISTORY_CACHE_VERSION}_{user_id}_{logging_style or 'repeats'}"
    )


def _new_history_era_token() -> str:
    """Return a globally unique era token (never reuses a prior identity)."""
    return f"{time.time_ns() // 1_000_000:013d}{uuid.uuid4().hex[:8]}"


def _current_history_era(user_id: int, logging_style: str) -> str:
    """Return the current era token, creating it if absent.

    The token is only ever compared for equality: readers accept an index
    published under the token they read, and invalidation replaces the token
    outright. A missing key (never set, expired, or evicted) is seeded with a
    fresh unique token — it can never alias an older era, so resurrecting an
    orphaned typed index is impossible by construction (no ABA).
    """
    era_key = _history_era_key(user_id, logging_style)
    era = cache.get(era_key)
    if isinstance(era, str) and era:
        return era
    era = _new_history_era_token()
    if cache.add(era_key, era, HISTORY_ERA_TIMEOUT):
        return era
    # Lost the create race or the cache dropped the write: adopt the winner
    # if one exists, otherwise return an unpublished token. Publishing under
    # an unpublished token is always safe — no reader will ever look there.
    era = cache.get(era_key)
    if isinstance(era, str) and era:
        return era
    return _new_history_era_token()


def _bump_history_era(user_id: int, logging_style: str) -> str:
    """Retire the current era and return the new one.

    An unconditional write of a fresh unique token: concurrent invalidations
    simply race to install distinct tokens, and *any* winner retires every
    older namespace, so there is no lost-update window to exploit. Builders
    still holding the previous token can only publish into a namespace no
    reader will select again.
    """
    era = _new_history_era_token()
    cache.set(_history_era_key(user_id, logging_style), era, HISTORY_ERA_TIMEOUT)
    return era


def _touch_history_era(user_id: int, logging_style: str) -> None:
    """Refresh the era key's TTL so it outlives indexes published under it."""
    cache.touch(_history_era_key(user_id, logging_style), HISTORY_ERA_TIMEOUT)


def _typed_history_index_key(
    user_id: int,
    logging_style: str,
    media_types,
    era: str | None = None,
) -> str:
    """Return a cache key for an index narrowed to concrete media types."""
    signature = ",".join(sorted(media_types))
    key = f"{_cache_key(user_id, logging_style)}_types_{signature}"
    if era:
        key = f"{key}_e{era}"
    return key


def _typed_history_index_registry_key(user_id: int, logging_style: str) -> str:
    """Return the registry key used to invalidate typed history indexes."""
    return f"{_cache_key(user_id, logging_style)}_typed_registry"


def _refresh_lock_key(user_id: int, logging_style: str) -> str:
    return f"{HISTORY_REFRESH_LOCK_PREFIX}_{user_id}_{logging_style or 'repeats'}"


def _refresh_dedupe_key(user_id: int, logging_style: str, day_keys=None) -> str:
    """Return the key that dedupes refreshes of one set of days.

    ``day_keys`` must already be normalized; the scheduler and the task derive
    the same key from the same list. No days means an index-only refresh.
    """
    lock_key = _refresh_lock_key(user_id, logging_style)
    if not day_keys:
        return lock_key
    dedupe_hash = stable_hmac(
        ",".join(day_keys),
        namespace="history_refresh_days",
        length=10,
    )
    return f"{lock_key}_days_{dedupe_hash}"


def _refresh_rerun_key(dedupe_key: str) -> str:
    """Return the marker a deduped request leaves for the running refresh."""
    return f"{dedupe_key}_rerun"


def _release_refresh_locks(user_id: int, logging_style: str, dedupe_key: str) -> None:
    """Release a refresh's own dedupe key, and the style lock if it is still its own.

    A later request for other days overwrites the style lock with its own
    payload while it waits; deleting it here would release that request's lock.
    """
    lock_key = _refresh_lock_key(user_id, logging_style)
    if dedupe_key != lock_key:
        cache.delete(dedupe_key)
    refresh_lock = cache.get(lock_key)
    owner = refresh_lock.get("dedupe_key") if isinstance(refresh_lock, dict) else None
    if owner in (None, dedupe_key):
        cache.delete(lock_key)


def _coverage_repair_key(user_id: int, logging_style: str) -> str:
    return f"{HISTORY_COVERAGE_REPAIR_PREFIX}_{user_id}_{logging_style or 'repeats'}"


def _day_cache_key(user_id: int, logging_style: str, day_key: str) -> str:
    return f"{HISTORY_DAY_PREFIX}_{user_id}_{logging_style or 'repeats'}_{day_key}"


# ── Day key functions ─────────────────────────────────────────────────────────


def _day_key_for_date(day_value):
    return day_value.strftime("%Y%m%d")


def _date_from_day_key(day_key: str):
    return datetime.strptime(day_key, "%Y%m%d").date()  # noqa: DTZ007  # date-only value; no timezone applies


def _day_key_from_value(value):
    if value is None:
        return None
    if isinstance(value, (int, bytes)):
        try:
            value = value.decode() if isinstance(value, bytes) else str(value)
        except Exception:
            return None
    if isinstance(value, str):
        value = value.strip().strip("'").strip('"')
        if value.isdigit() and len(value) == DAY_KEY_LENGTH:
            return value
        try:
            return _day_key_for_date(datetime.strptime(value, "%Y-%m-%d").date())  # noqa: DTZ007  # date-only value; no timezone applies
        except ValueError:
            return None
    if isinstance(value, datetime):
        localized = _localize_datetime(value)
        if localized:
            return _day_key_for_date(localized.date())
        return None
    if hasattr(value, "strftime"):
        return _day_key_for_date(value)
    return None


def history_day_key(value):
    """Return the history day key."""
    return _day_key_from_value(value)


def history_day_keys_for_range(start_dt, end_dt):
    """Return the history day keys for range."""
    if not start_dt or not end_dt:
        return []
    start_local = _localize_datetime(start_dt)
    end_local = _localize_datetime(end_dt)
    if not start_local or not end_local:
        return []
    start_date = start_local.date()
    end_date = end_local.date()
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    day_count = (end_date - start_date).days + 1
    return [
        _day_key_for_date(start_date + timedelta(days=offset))
        for offset in range(day_count)
    ]


# ── Logging style ─────────────────────────────────────────────────────────────


HISTORY_LOGGING_STYLES = ("sessions", "repeats")


def _normalize_logging_style(logging_style, user=None):
    if logging_style in HISTORY_LOGGING_STYLES:
        return logging_style
    if user is not None:
        return getattr(user, "game_logging_style", "repeats")
    return "repeats"


# ── Diagnostics ───────────────────────────────────────────────────────────────


def _get_rss_kb():
    try:
        import resource
    except Exception:
        return None
    try:
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return rss // 1024 if sys.platform == "darwin" else rss
    except Exception:
        return None


# ── Timezone / datetime ───────────────────────────────────────────────────────


def _localize_datetime(value):
    """Convert a datetime to the current timezone if possible."""
    if value is None:
        return None
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_current_timezone())
    return timezone.localtime(value)


# ── Genre helpers ─────────────────────────────────────────────────────────────


def _resolve_genres(*items):
    """Pick the first usable genres value from the provided items."""
    for item in items:
        if not item:
            continue
        genres = getattr(item, "genres", None)
        if genres:
            return _coerce_genre_list(genres)
    return []


def _resolve_implied_genres(*items):
    """Pick the first usable implied-genres value from the provided items."""
    for item in items:
        if not item:
            continue
        implied_genres = getattr(item, "implied_genres", None)
        if implied_genres:
            return _coerce_genre_list(implied_genres)
    return []


def _resolve_music_genres(album=None, artist=None, track=None):
    if album and album.genres:
        return _coerce_genre_list(album.genres)
    if artist and artist.genres:
        return _coerce_genre_list(artist.genres)
    if track and track.genres:
        return _coerce_genre_list(track.genres)
    return []
