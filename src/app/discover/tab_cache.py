"""Redis-backed tab cache for Discover page payloads."""

# ruff: noqa: TRY300

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import parse_qsl, urlparse
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from app.discover.registry import ALL_MEDIA_KEY, DISCOVER_MEDIA_TYPES
from app.discover.schemas import CandidateItem, RowResult
from app.log_safety import exception_summary
from app.models import (
    DiscoverApiCache,
    DiscoverRowCache,
    DiscoverTasteProfile,
    MediaTypes,
    Sources,
)

logger = logging.getLogger(__name__)

DISCOVER_TAB_CACHE_VERSION = 3
DISCOVER_TAB_PREFIX = f"discover_tab_v{DISCOVER_TAB_CACHE_VERSION}"
DISCOVER_TAB_REFRESH_LOCK_PREFIX = f"{DISCOVER_TAB_PREFIX}_refresh_lock"
DISCOVER_TAB_ACTIVITY_VERSION_PREFIX = f"{DISCOVER_TAB_PREFIX}_activity_version"
DISCOVER_TAB_REFRESH_SCHEDULED_PREFIX = f"{DISCOVER_TAB_PREFIX}_refresh_scheduled"
DISCOVER_TAB_ACTIVE_PREFIX = f"{DISCOVER_TAB_PREFIX}_active"
DISCOVER_RECENTLY_USED_PREFIX = f"{DISCOVER_TAB_PREFIX}_recently_used"
DISCOVER_ACTION_UNDO_PREFIX = "discover_action_undo_v1"
DISCOVER_TAB_TIMEOUT = 60 * 60 * 24 * 7  # 7 days; staleness is tracked separately
DISCOVER_TAB_STALE_AFTER = timedelta(minutes=15)
DISCOVER_TAB_REFRESH_LOCK_TTL = 60 * 5
DISCOVER_TAB_REFRESH_SCHEDULED_TTL = 60 * 10
DISCOVER_TAB_ACTIVE_TTL = 45
# A rebuild costs 20-30 s of CPU on a large library. After one finishes,
# background invalidations (imports, scrobbles) only mark the tab stale for this
# long; the next Discover view rebuilds it at once (stale rows are served).
DISCOVER_BACKGROUND_REBUILD_COOLDOWN = 60 * 10
DISCOVER_ACTION_UNDO_TTL = 60
DISCOVER_TAB_RECENTLY_BUILT_SECONDS = 60
DISCOVER_REQUEST_WARMUP_PREFIX = f"{DISCOVER_TAB_PREFIX}_request_warm"
DISCOVER_REQUEST_WARMUP_TTL = 60 * 15
DISCOVER_DEFAULT_REFRESH_DEBOUNCE_SECONDS = 30
DISCOVER_DEFAULT_REFRESH_COUNTDOWN = 3
DISCOVER_PRIORITY_REFRESH_DEBOUNCE_SECONDS = 0
DISCOVER_PRIORITY_REFRESH_COUNTDOWN = 0
DISCOVER_WARM_SIBLING_DEBOUNCE_SECONDS = 60
DISCOVER_WARM_SIBLING_COUNTDOWN = 10
DISCOVER_VISIBLE_ITEMS_PER_ROW = 12
DISCOVER_TASK_PRIORITY_INTERACTIVE = getattr(
    settings,
    "CELERY_TASK_PRIORITY_INTERACTIVE",
    9,
)
DISCOVER_TASK_PRIORITY_FOLLOWUP = getattr(settings, "CELERY_TASK_PRIORITY_FOLLOWUP", 3)
DISCOVER_TASK_PRIORITY_BACKGROUND = getattr(
    settings,
    "CELERY_TASK_PRIORITY_BACKGROUND",
    1,
)

DISCOVER_PROVIDER_BY_MEDIA_TYPE = {
    ALL_MEDIA_KEY: {Sources.TMDB.value},
    MediaTypes.MOVIE.value: {"trakt"},
    MediaTypes.TV.value: {"trakt"},
    MediaTypes.ANIME.value: {"trakt"},
    MediaTypes.MUSIC.value: {Sources.MUSICBRAINZ.value},
    MediaTypes.PODCAST.value: {
        Sources.POCKETCASTS.value,
        Sources.GPODDER.value,
        Sources.AUDIOBOOKSHELF.value,
    },
    MediaTypes.BOOK.value: {Sources.OPENLIBRARY.value},
    MediaTypes.COMIC.value: {Sources.COMICVINE.value},
    MediaTypes.MANGA.value: {Sources.MAL.value},
    MediaTypes.GAME.value: {Sources.IGDB.value},
    MediaTypes.BOARDGAME.value: {Sources.BGG.value},
}


@dataclass(slots=True)
class ActiveDiscoverContext:
    """Short-lived context for the currently active Discover tab."""

    media_type: str
    show_more: bool
    activated_at: timezone.datetime | None = None


def _normalize_media_type(media_type: str | None) -> str:
    media_type = (media_type or ALL_MEDIA_KEY).strip().lower()
    if media_type == ALL_MEDIA_KEY:
        return media_type
    if media_type in DISCOVER_MEDIA_TYPES:
        return media_type
    return ALL_MEDIA_KEY


def _parse_show_more(value) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _log_cache_failure(operation: str, key: str, exc: Exception) -> None:
    logger.debug(
        "Discover cache %s failed for %s: %s",
        operation,
        key,
        exception_summary(exc),
    )


def _cache_get(key: str, *, default=None):
    try:
        return cache.get(key)
    except Exception as exc:
        _log_cache_failure("get", key, exc)
        return default


def _cache_set(key: str, value, *, timeout=None) -> bool:
    try:
        cache.set(key, value, timeout=timeout)
        return True
    except Exception as exc:
        _log_cache_failure("set", key, exc)
        return False


def _cache_add(key: str, value, *, timeout=None) -> bool | None:
    try:
        return bool(cache.add(key, value, timeout=timeout))
    except Exception as exc:
        _log_cache_failure("add", key, exc)
        return None


def _cache_delete(key: str) -> bool:
    try:
        return bool(cache.delete(key))
    except Exception as exc:
        _log_cache_failure("delete", key, exc)
        return False


def _tab_cache_key(user_id: int, media_type: str, *, show_more: bool) -> str:
    normalized_media_type = _normalize_media_type(media_type)
    return (
        f"{DISCOVER_TAB_PREFIX}_{user_id}_{normalized_media_type}_"
        f"{int(bool(show_more))}"
    )


def _refresh_lock_key(user_id: int, media_type: str, *, show_more: bool) -> str:
    return (
        f"{DISCOVER_TAB_REFRESH_LOCK_PREFIX}_{user_id}_{_normalize_media_type(media_type)}_"
        f"{int(bool(show_more))}"
    )


def _activity_version_key(user_id: int, media_type: str) -> str:
    normalized_media_type = _normalize_media_type(media_type)
    return f"{DISCOVER_TAB_ACTIVITY_VERSION_PREFIX}_{user_id}_{normalized_media_type}"


def _refresh_scheduled_key(
    user_id: int,
    media_type: str,
    *,
    show_more: bool,
    activity_version: str,
) -> str:
    return (
        f"{DISCOVER_TAB_REFRESH_SCHEDULED_PREFIX}_{user_id}_{activity_version}_"
        f"{_normalize_media_type(media_type)}_{int(bool(show_more))}"
    )


def _rebuild_cooldown_key(user_id: int, media_type: str, *, show_more: bool) -> str:
    return (
        f"{DISCOVER_TAB_REFRESH_SCHEDULED_PREFIX}_cooldown_{user_id}_"
        f"{_normalize_media_type(media_type)}_{int(bool(show_more))}"
    )


def _active_key(user_id: int) -> str:
    return f"{DISCOVER_TAB_ACTIVE_PREFIX}_{user_id}"


def _recently_used_key(user_id: int) -> str:
    return f"{DISCOVER_RECENTLY_USED_PREFIX}_{user_id}"


def _request_warmup_key(user_id: int) -> str:
    return f"{DISCOVER_REQUEST_WARMUP_PREFIX}_{user_id}"


def _action_undo_key(user_id: int, token: str) -> str:
    return f"{DISCOVER_ACTION_UNDO_PREFIX}_{user_id}_{token}"


def _parse_cached_datetime(value):
    if not value:
        return None

    parsed = value
    if isinstance(value, str):
        try:
            parsed = timezone.datetime.fromisoformat(value)
        except ValueError:
            return None

    if not hasattr(parsed, "tzinfo"):
        return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _lock_is_stale(value) -> bool:
    if not value:
        return False
    if not isinstance(value, dict):
        return True
    started_at = _parse_cached_datetime(value.get("started_at"))
    if started_at is None:
        return True
    return timezone.now() - started_at > timedelta(
        seconds=DISCOVER_TAB_REFRESH_LOCK_TTL,
    )


def _should_enqueue_refresh_tasks() -> bool:
    """Return whether Discover refreshes should hit Celery in this process."""
    if getattr(settings, "TESTING", False):
        return bool(getattr(settings, "DISCOVER_TASKS_EAGER_REFRESH", False))
    return True


def _serialize_rows(
    rows: list[RowResult],
    *,
    include_reserve: bool = False,
) -> list[dict]:
    return [row.to_dict(include_reserve=include_reserve) for row in rows]


def _deserialize_rows(payload_rows) -> list[RowResult]:
    return [RowResult.from_dict(row_payload) for row_payload in (payload_rows or [])]


def _get_activity_version(user_id: int, media_type: str) -> str:
    key = _activity_version_key(user_id, media_type)
    version = _cache_get(key)
    if version:
        return str(version)
    version = timezone.now().isoformat()
    _cache_set(key, version, timeout=DISCOVER_TAB_TIMEOUT)
    return version


def get_activity_version(user_id: int, media_type: str) -> str:
    """Return the current Discover activity version for a media type."""
    return _get_activity_version(user_id, media_type)


def bump_activity_version(user_id: int, media_type: str) -> str:
    """Advance the activity version for a Discover media type."""
    # The suffix makes every bump distinct: a coarse clock can give a bump the
    # same timestamp as the version it replaces, leaving the tab looking fresh.
    version = f"{timezone.now().isoformat()}-{uuid4().hex[:8]}"
    _cache_set(
        _activity_version_key(user_id, media_type),
        version,
        timeout=DISCOVER_TAB_TIMEOUT,
    )
    return version


def get_tab_cache(user_id: int, media_type: str, *, show_more: bool = False):
    """Return cached tab payload and whether it is stale."""
    media_type = _normalize_media_type(media_type)
    payload = _cache_get(
        _tab_cache_key(user_id, media_type, show_more=show_more),
    )
    if not payload:
        return None, False

    built_at = _parse_cached_datetime(payload.get("built_at"))
    current_version = _get_activity_version(user_id, media_type)
    cached_version = str(payload.get("activity_version") or "")
    is_stale = cached_version != current_version
    if payload.get("optimistic_refreshing"):
        is_stale = True
    if built_at:
        is_stale = is_stale or (timezone.now() - built_at > DISCOVER_TAB_STALE_AFTER)

    return payload, is_stale


def has_fresh_tab_cache(
    user_id: int, media_type: str, *, show_more: bool = False
) -> bool:
    """Return whether a Discover tab has a non-stale cached payload."""
    payload, is_stale = get_tab_cache(user_id, media_type, show_more=show_more)
    return bool(payload and not is_stale)


def get_user_tab_targets(user) -> list[str]:
    """Return the default Discover tabs to keep warm for a user."""
    enabled_media_types = get_enabled_discover_media_types(user)
    return list(dict.fromkeys([ALL_MEDIA_KEY, *enabled_media_types]))


def get_enabled_discover_media_types(
    user,
    *,
    fallback_to_all: bool = False,
) -> list[str]:
    """Return the user's enabled Discover media types."""
    enabled_media_types = []
    if user is not None:
        enabled_media_types = [
            media_type
            for media_type in user.get_enabled_media_types()
            if media_type in DISCOVER_MEDIA_TYPES
        ]
    if enabled_media_types or not fallback_to_all:
        return enabled_media_types
    return list(DISCOVER_MEDIA_TYPES)


def media_type_is_enabled_for_user(user, media_type: str | None) -> bool:
    """Return whether the user can access a Discover media type."""
    normalized_media_type = _normalize_media_type(media_type)
    if normalized_media_type == ALL_MEDIA_KEY:
        return True
    return normalized_media_type in set(
        get_enabled_discover_media_types(user, fallback_to_all=True),
    )


def resolve_media_type_for_user(user, media_type: str | None) -> str:
    """Clamp a Discover media type to what the user has enabled."""
    normalized_media_type = _normalize_media_type(media_type)
    if media_type_is_enabled_for_user(user, normalized_media_type):
        return normalized_media_type
    return ALL_MEDIA_KEY


def set_tab_cache(
    user_id: int,
    media_type: str,
    rows: list[RowResult],
    *,
    show_more: bool = False,
    activity_version: str | None = None,
    optimistic_refreshing: bool = False,
) -> dict:
    """Persist a serialized Discover tab payload."""
    media_type = _normalize_media_type(media_type)
    payload = {
        "built_at": timezone.now().isoformat(),
        "activity_version": activity_version
        or _get_activity_version(user_id, media_type),
        "media_type": media_type,
        "show_more": bool(show_more),
        "rows": _serialize_rows(rows, include_reserve=True),
        "optimistic_refreshing": bool(optimistic_refreshing),
    }
    _cache_set(
        _tab_cache_key(user_id, media_type, show_more=show_more),
        payload,
        timeout=DISCOVER_TAB_TIMEOUT,
    )
    return payload


def clear_provider_cache_for_media_type(media_type: str) -> int:
    """Delete provider-backed Discover API cache rows relevant to a media type."""
    providers = DISCOVER_PROVIDER_BY_MEDIA_TYPE.get(
        _normalize_media_type(media_type),
        set(),
    )
    if not providers:
        return 0
    deleted, _ = DiscoverApiCache.objects.filter(provider__in=providers).delete()
    return int(deleted)


def clear_lower_level_cache(user_id: int, media_type: str) -> tuple[int, int]:
    """Delete lower-level row and taste-profile caches for a user/media type."""
    deleted_rows = clear_row_cache(user_id, media_type)
    return deleted_rows, clear_taste_profile(user_id, media_type)


def clear_taste_profile(user_id: int, media_type: str) -> int:
    """Delete the cached taste profile for a user/media type."""
    deleted_profiles, _ = DiscoverTasteProfile.objects.filter(
        user_id=user_id,
        media_type=_normalize_media_type(media_type),
    ).delete()
    return int(deleted_profiles)


def clear_row_cache(user_id: int, media_type: str) -> int:
    """Delete lower-level row caches for a user/media type."""
    normalized_media_type = _normalize_media_type(media_type)
    deleted_rows, _ = DiscoverRowCache.objects.filter(
        user_id=user_id,
        media_type=normalized_media_type,
    ).delete()
    return int(deleted_rows)


def mark_active(
    user_id: int,
    media_type: str,
    *,
    show_more: bool = False,
) -> ActiveDiscoverContext:
    """Record the currently active Discover tab for a short period."""
    context = ActiveDiscoverContext(
        media_type=_normalize_media_type(media_type),
        show_more=bool(show_more),
        activated_at=timezone.now(),
    )
    _cache_set(
        _active_key(user_id),
        {
            "media_type": context.media_type,
            "show_more": context.show_more,
            "activated_at": (
                context.activated_at.isoformat() if context.activated_at else ""
            ),
        },
        timeout=DISCOVER_TAB_ACTIVE_TTL,
    )
    mark_recently_used(user_id)
    return context


def _eager_rebuild_days() -> int:
    return max(0, int(getattr(settings, "DISCOVER_EAGER_REBUILD_DAYS", 0) or 0))


def mark_recently_used(user_id: int) -> None:
    """Record that the user opened Discover, for the eager-rebuild window."""
    days = _eager_rebuild_days()
    if days:
        _cache_set(_recently_used_key(user_id), 1, timeout=days * 24 * 60 * 60)


def recently_used(user_id: int) -> bool | None:
    """Return whether the user opened Discover within the eager-rebuild window.

    None means the cache could not answer; callers treat that as "yes", which
    is the behaviour from before the window existed.
    """
    key = _recently_used_key(user_id)
    try:
        present = cache.has_key(key)
    except Exception as exc:
        _log_cache_failure("has_key", key, exc)
        return None
    # django-redis answers None when it swallowed a connection error.
    return None if present is None else bool(present)


def _rebuilds_eagerly(user_id: int) -> bool:
    """Return whether a tracked write should queue this user's tab rebuilds."""
    if not _eager_rebuild_days():
        return True
    return recently_used(user_id) is not False


def get_active_context(user_id: int) -> ActiveDiscoverContext | None:
    """Return the active Discover tab context when one is available."""
    raw = _cache_get(_active_key(user_id))
    if not isinstance(raw, dict):
        return None
    media_type = _normalize_media_type(raw.get("media_type"))
    activated_at = _parse_cached_datetime(raw.get("activated_at"))
    return ActiveDiscoverContext(
        media_type=media_type,
        show_more=bool(raw.get("show_more")),
        activated_at=activated_at,
    )


def clear_active(user_id: int) -> None:
    """Clear any short-lived active Discover context for a user."""
    _cache_delete(_active_key(user_id))


def _discover_context_from_url(
    url: str | None,
    *,
    fallback_media_type: str | None = None,
    fallback_show_more: bool | None = None,
) -> ActiveDiscoverContext | None:
    if not url:
        return None
    if not url_has_allowed_host_and_scheme(url, allowed_hosts=None):
        return None

    parsed = urlparse(url)
    discover_path = reverse("discover")
    discover_rows_path = reverse("discover_rows")
    path = parsed.path or ""
    if not path.startswith((discover_path, discover_rows_path)):
        return None

    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    media_type = query.get("media_type", fallback_media_type)
    show_more = query.get("show_more")
    if show_more is None:
        show_more = fallback_show_more
    return ActiveDiscoverContext(
        media_type=_normalize_media_type(media_type),
        show_more=bool(_parse_show_more(show_more)),
        activated_at=timezone.now(),
    )


def mark_active_from_request(
    request,
    *,
    fallback_media_type: str | None = None,
    fallback_show_more: bool | None = None,
) -> ActiveDiscoverContext | None:
    """Mark Discover as active when a request originated from the Discover page."""
    if not getattr(request.user, "is_authenticated", False) or not request.user.id:
        return None

    next_url = request.GET.get("next") or request.POST.get("next")
    context = _discover_context_from_url(
        next_url,
        fallback_media_type=fallback_media_type,
        fallback_show_more=fallback_show_more,
    )
    if context is None:
        context = _discover_context_from_url(
            request.META.get("HTTP_REFERER"),
            fallback_media_type=fallback_media_type,
            fallback_show_more=fallback_show_more,
        )
    if context is None:
        return None
    return mark_active(
        request.user.id,
        context.media_type,
        show_more=context.show_more,
    )


def target_media_types_for_change(media_type: str | None) -> list[str]:
    """Return affected Discover tab media types for a model/input change."""
    normalized = (media_type or "").strip().lower()
    mapping = {
        MediaTypes.MOVIE.value: [MediaTypes.MOVIE.value, ALL_MEDIA_KEY],
        MediaTypes.TV.value: [MediaTypes.TV.value, ALL_MEDIA_KEY],
        MediaTypes.SEASON.value: [MediaTypes.TV.value, ALL_MEDIA_KEY],
        MediaTypes.EPISODE.value: [MediaTypes.TV.value, ALL_MEDIA_KEY],
        MediaTypes.ANIME.value: [MediaTypes.ANIME.value, ALL_MEDIA_KEY],
        MediaTypes.MUSIC.value: [MediaTypes.MUSIC.value, ALL_MEDIA_KEY],
        MediaTypes.PODCAST.value: [MediaTypes.PODCAST.value, ALL_MEDIA_KEY],
        MediaTypes.BOOK.value: [MediaTypes.BOOK.value, ALL_MEDIA_KEY],
        MediaTypes.COMIC.value: [MediaTypes.COMIC.value, ALL_MEDIA_KEY],
        MediaTypes.MANGA.value: [MediaTypes.MANGA.value, ALL_MEDIA_KEY],
        MediaTypes.GAME.value: [MediaTypes.GAME.value, ALL_MEDIA_KEY],
        MediaTypes.BOARDGAME.value: [MediaTypes.BOARDGAME.value, ALL_MEDIA_KEY],
    }
    if normalized == ALL_MEDIA_KEY:
        return [ALL_MEDIA_KEY]
    return mapping.get(normalized, [])


def should_prioritize(
    active_context: ActiveDiscoverContext | None,
    changed_media_type: str | None,
    *,
    target_media_types: list[str] | None = None,
) -> bool:
    """Return whether a media change should preemptively refresh Discover."""
    if active_context is None:
        return False
    resolved_targets = target_media_types
    if not isinstance(resolved_targets, (list, tuple, set)):
        resolved_targets = target_media_types_for_change(changed_media_type)
    resolved_targets = set(resolved_targets)
    return active_context.media_type in resolved_targets


def target_media_types_for_user_change(user, media_type: str | None) -> list[str]:
    """Return affected Discover tab media types filtered by sidebar settings."""
    normalized_media_type = (media_type or "").strip().lower()
    targets = target_media_types_for_change(normalized_media_type)
    if not targets or normalized_media_type == ALL_MEDIA_KEY:
        return targets

    enabled_media_types = set(
        get_enabled_discover_media_types(user, fallback_to_all=True),
    )
    enabled_targets = [
        target_media_type
        for target_media_type in targets
        if target_media_type != ALL_MEDIA_KEY
        and target_media_type in enabled_media_types
    ]
    if not enabled_targets:
        return []
    if ALL_MEDIA_KEY in targets:
        enabled_targets.append(ALL_MEDIA_KEY)
    return enabled_targets


def get_user_target_media_types_for_change(
    user_id: int,
    media_type: str | None,
) -> list[str]:
    """Return affected Discover tab media types for a specific user."""
    user_model = get_user_model()
    user = user_model.objects.filter(id=user_id).first()
    if not user:
        return []
    return target_media_types_for_user_change(user, media_type)


def _action_target_media_types(
    active_media_type: str,
    candidate_media_type: str,
) -> list[str]:
    targets = {
        _normalize_media_type(active_media_type),
        _normalize_media_type(candidate_media_type),
        ALL_MEDIA_KEY,
    }
    return [
        media_type
        for media_type in targets
        if media_type == ALL_MEDIA_KEY or media_type in DISCOVER_MEDIA_TYPES
    ]


def _candidate_matches(item: CandidateItem, identity: tuple[str, str, str]) -> bool:
    return item.identity() == identity


def _row_pool_without_identity(
    row: RowResult,
    identity: tuple[str, str, str],
) -> list[CandidateItem]:
    return [
        candidate
        for candidate in [*row.items, *row.reserve_items]
        if not _candidate_matches(candidate, identity)
    ]


def _rebalance_rows(rows: list[RowResult]) -> list[RowResult]:
    seen_visible_identities: set[tuple[str, str, str]] = set()
    for row in rows:
        pool = [*row.items, *row.reserve_items]
        unique_pool: list[CandidateItem] = []
        seen_row_identities: set[tuple[str, str, str]] = set()
        for candidate in pool:
            identity = candidate.identity()
            if identity in seen_row_identities:
                continue
            seen_row_identities.add(identity)
            unique_pool.append(candidate)

        if row.key == "all_time_greats_unseen":
            visible_items = unique_pool[:DISCOVER_VISIBLE_ITEMS_PER_ROW]
            reserve_items = unique_pool[DISCOVER_VISIBLE_ITEMS_PER_ROW:]
        else:
            allowed_pool = [
                candidate
                for candidate in unique_pool
                if candidate.identity() not in seen_visible_identities
            ]
            visible_items = allowed_pool[:DISCOVER_VISIBLE_ITEMS_PER_ROW]
            reserve_items = allowed_pool[DISCOVER_VISIBLE_ITEMS_PER_ROW:]

        row.items = visible_items
        row.reserve_items = reserve_items
        seen_visible_identities.update(item.identity() for item in row.items)
    return rows


def _acquire_refresh_lock(
    lock_key: str,
    lock_payload: dict,
    *,
    debounce_seconds: int,
) -> bool:
    if debounce_seconds:
        added = _cache_add(lock_key, lock_payload, timeout=debounce_seconds)
        if added is None:
            return False
        if not added:
            existing_lock = _cache_get(lock_key)
            if not _lock_is_stale(existing_lock):
                return False
            _cache_delete(lock_key)
            if _cache_add(lock_key, lock_payload, timeout=debounce_seconds) is not True:
                return False

    _cache_set(lock_key, lock_payload, timeout=DISCOVER_TAB_REFRESH_LOCK_TTL)
    return True


def release_refresh_lock(user_id: int, media_type: str, *, show_more: bool) -> None:
    """Drop a tab refresh lock the task did not need to use."""
    _cache_delete(
        _refresh_lock_key(
            user_id, _normalize_media_type(media_type), show_more=show_more
        )
    )


def release_refresh_reservation(
    user_id: int, media_type: str, *, show_more: bool
) -> None:
    """Drop a queued refresh's lock and scheduled marker when it will not run.

    Unlike release_refresh_lock, this also clears the scheduled marker, so a later
    request can queue a real rebuild instead of waiting out the marker's TTL.
    """
    media_type = _normalize_media_type(media_type)
    release_refresh_lock(user_id, media_type, show_more=show_more)
    _cache_delete(
        _refresh_scheduled_key(
            user_id,
            media_type,
            show_more=show_more,
            activity_version=_get_activity_version(user_id, media_type),
        )
    )


def _reserve_scheduled_refresh(
    user_id: int,
    media_type: str,
    *,
    show_more: bool,
    force: bool,
    lock_key: str,
) -> tuple[bool, str | None]:
    if force or not DISCOVER_TAB_REFRESH_SCHEDULED_TTL:
        return True, None

    scheduled_key = _refresh_scheduled_key(
        user_id,
        media_type,
        show_more=show_more,
        activity_version=_get_activity_version(user_id, media_type),
    )
    if (
        _cache_add(
            scheduled_key,
            1,
            timeout=DISCOVER_TAB_REFRESH_SCHEDULED_TTL,
        )
        is not True
    ):
        _cache_delete(lock_key)
        return False, scheduled_key

    return True, scheduled_key


def schedule_tab_refresh(
    user_id: int,
    media_type: str,
    *,
    show_more: bool = False,
    debounce_seconds: int = DISCOVER_DEFAULT_REFRESH_DEBOUNCE_SECONDS,
    countdown: int = DISCOVER_DEFAULT_REFRESH_COUNTDOWN,
    force: bool = False,
    clear_provider_cache: bool = False,
    priority: int | None = None,
) -> bool:
    """Queue a background rebuild of a Discover tab cache entry."""
    media_type = _normalize_media_type(media_type)
    lock_key = _refresh_lock_key(user_id, media_type, show_more=show_more)
    lock_payload = {
        "started_at": timezone.now().isoformat(),
        "media_type": media_type,
        "show_more": bool(show_more),
        "force": bool(force),
        "clear_provider_cache": bool(clear_provider_cache),
    }
    if not _acquire_refresh_lock(
        lock_key,
        lock_payload,
        debounce_seconds=debounce_seconds,
    ):
        return False

    reserved, scheduled_key = _reserve_scheduled_refresh(
        user_id,
        media_type,
        show_more=show_more,
        force=force,
        lock_key=lock_key,
    )
    if not reserved:
        return False

    if not _should_enqueue_refresh_tasks():
        return True

    try:
        from app.tasks import refresh_discover_tab_cache

        refresh_discover_tab_cache.apply_async(
            args=[user_id, media_type],
            kwargs={
                "show_more": bool(show_more),
                "force": bool(force),
                "clear_provider_cache": bool(clear_provider_cache),
            },
            countdown=countdown,
            priority=(
                DISCOVER_TASK_PRIORITY_INTERACTIVE if priority is None else priority
            ),
        )
        return True
    except Exception as error:  # pragma: no cover - Celery unavailable
        _cache_delete(lock_key)
        if scheduled_key:
            _cache_delete(scheduled_key)
        logger.warning(
            "discover_tab_refresh_enqueue_failed user_id=%s media_type=%s "
            "show_more=%s error=%s",
            user_id,
            media_type,
            int(bool(show_more)),
            error,
        )
        return False


def _tab_changed_since_built(
    user_id: int,
    media_type: str,
    activity_version: str,
    *,
    show_more: bool,
) -> bool:
    payload = _cache_get(_tab_cache_key(user_id, media_type, show_more=show_more))
    if not isinstance(payload, dict):
        return True
    return str(payload.get("activity_version") or "") != activity_version


def _clear_rows_built_before(user_id: int, media_type: str, activity_version: str) -> int:
    """Delete cached rows not built for this activity version.

    Keeps a row a row refresh rebuilt moments ago for the current version.
    """
    from app.discover.row_cache_schema import ROW_CACHE_ACTIVITY_VERSION_META_KEY

    deleted_rows, _ = (
        DiscoverRowCache.objects.filter(
            user_id=user_id,
            media_type=_normalize_media_type(media_type),
        )
        .exclude(
            **{f"payload__meta__{ROW_CACHE_ACTIVITY_VERSION_META_KEY}": activity_version},
        )
        .delete()
    )
    return int(deleted_rows)


def refresh_tab_cache(
    user,
    media_type: str,
    *,
    show_more: bool = False,
    force: bool = False,
    clear_provider_cache: bool = False,
) -> list[RowResult]:
    """Rebuild and persist a Discover tab payload."""
    from app.discover.service import get_discover_rows
    from app.discover.service_helpers import credit_maps_scope

    media_type = _normalize_media_type(media_type)
    lock_key = _refresh_lock_key(user.id, media_type, show_more=show_more)
    build_activity_version = _get_activity_version(user.id, media_type)
    version_drifted = False
    try:
        if force:
            clear_row_cache(user.id, media_type)
        elif _tab_changed_since_built(
            user.id, media_type, build_activity_version, show_more=show_more
        ):
            # A write that did not queue a rebuild left its rows in place.
            _clear_rows_built_before(user.id, media_type, build_activity_version)
        if clear_provider_cache:
            clear_provider_cache_for_media_type(media_type)

        with credit_maps_scope():
            rows = get_discover_rows(
                user,
                media_type,
                show_more=show_more,
                include_debug=False,
                defer_artwork=False,
            )
        current_activity_version = _get_activity_version(user.id, media_type)
        if current_activity_version != build_activity_version:
            version_drifted = True
            logger.info(
                "discover_tab_refresh_version_drift user_id=%s media_type=%s show_more=%s "
                "started_version=%s current_version=%s",
                user.id,
                media_type,
                int(bool(show_more)),
                build_activity_version,
                current_activity_version,
            )
            return rows
        set_tab_cache(
            user.id,
            media_type,
            rows,
            show_more=show_more,
            activity_version=build_activity_version,
        )
        return rows
    finally:
        _cache_delete(lock_key)
        _cache_set(
            _rebuild_cooldown_key(user.id, media_type, show_more=show_more),
            1,
            timeout=DISCOVER_BACKGROUND_REBUILD_COOLDOWN,
        )
        active_context = get_active_context(user.id) if version_drifted else None
        if version_drifted and (
            active_context is not None and active_context.media_type == media_type
        ):
            schedule_tab_refresh(
                user.id,
                media_type,
                show_more=show_more,
                debounce_seconds=DISCOVER_PRIORITY_REFRESH_DEBOUNCE_SECONDS,
                countdown=DISCOVER_PRIORITY_REFRESH_COUNTDOWN,
                force=force,
                clear_provider_cache=clear_provider_cache,
                priority=DISCOVER_TASK_PRIORITY_INTERACTIVE,
            )


def _collect_cached_action_payloads(
    user_id: int,
    active_media_type: str,
    candidate_media_type: str,
) -> list[dict]:
    payloads: list[dict] = []
    seen_keys: set[str] = set()
    for media_type in _action_target_media_types(
        active_media_type, candidate_media_type
    ):
        for show_more in (False, True):
            cache_key = _tab_cache_key(user_id, media_type, show_more=show_more)
            if cache_key in seen_keys:
                continue
            seen_keys.add(cache_key)
            payload = _cache_get(cache_key)
            if not payload:
                continue
            payloads.append(
                {
                    "media_type": media_type,
                    "show_more": bool(show_more),
                    "payload": payload,
                },
            )
    return payloads


def collect_action_payloads(
    user_id: int,
    active_media_type: str,
    candidate_media_type: str,
) -> list[dict]:
    """Public wrapper around _collect_cached_action_payloads.

    Callers that need to pass the same payloads to both store_undo_snapshot and
    apply_cached_action can collect them once here and pass via preloaded_payloads.
    """
    return _collect_cached_action_payloads(
        user_id, active_media_type, candidate_media_type
    )


def apply_cached_action(
    user_id: int,
    active_media_type: str,
    candidate_media_type: str,
    *,
    media_id: str,
    source: str,
    show_more: bool = False,
    preloaded_payloads: list[dict] | None = None,
) -> list[RowResult] | None:
    """Optimistically patch cached Discover tabs after a card action."""
    from app.discover.artwork import hydrate_visible_row_artwork

    started = time.monotonic()
    normalized_active_media_type = _normalize_media_type(active_media_type)
    identity = (
        _normalize_media_type(candidate_media_type),
        str(source),
        str(media_id),
    )
    active_rows: list[RowResult] | None = None
    patched_tabs = 0
    hydrated_rows = 0

    entries = (
        preloaded_payloads
        if preloaded_payloads is not None
        else _collect_cached_action_payloads(
            user_id,
            active_media_type,
            candidate_media_type,
        )
    )
    for entry in entries:
        rows = _deserialize_rows(entry["payload"].get("rows"))
        was_active_entry = entry["media_type"] == normalized_active_media_type and bool(
            entry["show_more"]
        ) == bool(show_more)
        visible_before = [
            [
                candidate.identity()
                for candidate in row.items[:DISCOVER_VISIBLE_ITEMS_PER_ROW]
            ]
            for row in rows
        ]
        for row in rows:
            row.items = [
                candidate
                for candidate in row.items
                if not _candidate_matches(candidate, identity)
            ]
            row.reserve_items = [
                candidate
                for candidate in row.reserve_items
                if not _candidate_matches(candidate, identity)
            ]

        rows = _rebalance_rows(rows)
        if was_active_entry:
            for index, row in enumerate(rows):
                visible_after = [
                    candidate.identity()
                    for candidate in row.items[:DISCOVER_VISIBLE_ITEMS_PER_ROW]
                ]
                if visible_after == visible_before[index]:
                    continue
                hydrate_visible_row_artwork(row, allow_remote=False)
                hydrated_rows += 1
        set_tab_cache(
            user_id,
            entry["media_type"],
            rows,
            show_more=bool(entry["show_more"]),
            activity_version=_get_activity_version(user_id, entry["media_type"]),
            optimistic_refreshing=True,
        )
        patched_tabs += 1
        if was_active_entry:
            active_rows = rows

    logger.info(
        "discover_cached_action_patch user_id=%s active_media_type=%s candidate_media_type=%s "
        "show_more=%s patched_tabs=%s hydrated_rows=%s duration_ms=%s",
        user_id,
        normalized_active_media_type,
        _normalize_media_type(candidate_media_type),
        int(bool(show_more)),
        patched_tabs,
        hydrated_rows,
        int((time.monotonic() - started) * 1000),
    )
    return active_rows


def store_undo_snapshot(
    user_id: int,
    *,
    action: str,
    active_media_type: str,
    candidate_media_type: str,
    show_more: bool = False,
    side_effect: dict | None = None,
    preloaded_payloads: list[dict] | None = None,
) -> str | None:
    """Persist a short-lived undo snapshot for Discover card actions."""
    tabs = (
        preloaded_payloads
        if preloaded_payloads is not None
        else _collect_cached_action_payloads(
            user_id,
            active_media_type,
            candidate_media_type,
        )
    )
    if not tabs and not side_effect:
        return None

    token = uuid4().hex
    _cache_set(
        _action_undo_key(user_id, token),
        {
            "action": action,
            "active_media_type": _normalize_media_type(active_media_type),
            "candidate_media_type": _normalize_media_type(candidate_media_type),
            "show_more": bool(show_more),
            "side_effect": side_effect or {},
            "tabs": tabs,
        },
        timeout=DISCOVER_ACTION_UNDO_TTL,
    )
    return token


def get_undo_snapshot(user_id: int, token: str) -> dict | None:
    """Return an undo snapshot without restoring cached payloads."""
    snapshot = _cache_get(_action_undo_key(user_id, token))
    return snapshot if isinstance(snapshot, dict) else None


def update_undo_snapshot(
    user_id: int,
    token: str,
    *,
    side_effect: dict,
) -> bool:
    """Attach side-effect metadata to an existing undo snapshot."""
    cache_key = _action_undo_key(user_id, token)
    snapshot = _cache_get(cache_key)
    if not isinstance(snapshot, dict):
        return False
    snapshot["side_effect"] = side_effect
    return _cache_set(cache_key, snapshot, timeout=DISCOVER_ACTION_UNDO_TTL)


def restore_undo_snapshot(
    user_id: int,
    token: str,
) -> dict | None:
    """Restore cached Discover payloads from an undo snapshot."""
    cache_key = _action_undo_key(user_id, token)
    snapshot = _cache_get(cache_key)
    if not isinstance(snapshot, dict):
        return None

    for entry in snapshot.get("tabs") or []:
        media_type = _normalize_media_type(entry.get("media_type"))
        show_more = bool(entry.get("show_more"))
        payload = entry.get("payload")
        if not payload:
            continue
        _cache_set(
            _tab_cache_key(user_id, media_type, show_more=show_more),
            payload,
            timeout=DISCOVER_TAB_TIMEOUT,
        )

    _cache_delete(cache_key)
    active_payload = _cache_get(
        _tab_cache_key(
            user_id,
            snapshot.get("active_media_type"),
            show_more=bool(snapshot.get("show_more")),
        ),
    )
    snapshot["rows"] = _deserialize_rows((active_payload or {}).get("rows"))
    return snapshot


def get_tab_rows(
    user,
    media_type: str,
    *,
    show_more: bool = False,
    include_debug: bool = False,
    defer_artwork: bool = True,
    allow_inline_bootstrap: bool = False,
) -> list[RowResult]:
    """Return tab rows from cache, scheduling refreshes as needed."""
    from app.discover.service import get_discover_rows

    media_type = _normalize_media_type(media_type)
    mark_active(user.id, media_type, show_more=show_more)

    if include_debug:
        return get_discover_rows(
            user,
            media_type,
            show_more=show_more,
            include_debug=True,
            defer_artwork=defer_artwork,
        )

    payload, is_stale = get_tab_cache(user.id, media_type, show_more=show_more)
    if payload:
        rows = _deserialize_rows(payload.get("rows"))
        if is_stale:
            for row in rows:
                row.is_stale = True
                row.source_state = "stale"
            refresh_scheduled = schedule_tab_refresh(
                user.id,
                media_type,
                show_more=show_more,
                debounce_seconds=DISCOVER_PRIORITY_REFRESH_DEBOUNCE_SECONDS,
                countdown=DISCOVER_PRIORITY_REFRESH_COUNTDOWN,
                priority=DISCOVER_TASK_PRIORITY_INTERACTIVE,
            )
            if allow_inline_bootstrap and not refresh_scheduled:
                rows = get_discover_rows(
                    user,
                    media_type,
                    show_more=show_more,
                    include_debug=False,
                    defer_artwork=defer_artwork,
                )
                set_tab_cache(
                    user.id,
                    media_type,
                    rows,
                    show_more=show_more,
                    activity_version=_get_activity_version(user.id, media_type),
                )
        return rows

    refresh_scheduled = schedule_tab_refresh(
        user.id,
        media_type,
        show_more=show_more,
        debounce_seconds=DISCOVER_PRIORITY_REFRESH_DEBOUNCE_SECONDS,
        countdown=DISCOVER_PRIORITY_REFRESH_COUNTDOWN,
        priority=DISCOVER_TASK_PRIORITY_INTERACTIVE,
    )
    # Mirror the stale path: when a refresh task is queued (or one is
    # already in flight holding the lock), let the page render its
    # "Building Discover rows" state and fill in via polling instead of
    # paying the multi-second build inline (and doubly, since the task
    # will rebuild anyway).
    if not refresh_scheduled:
        lock_key = _refresh_lock_key(user.id, media_type, show_more=show_more)
        refresh_lock = _cache_get(lock_key)
        refresh_scheduled = bool(refresh_lock) and not _lock_is_stale(refresh_lock)
    if not allow_inline_bootstrap or refresh_scheduled:
        return []

    rows = get_discover_rows(
        user,
        media_type,
        show_more=show_more,
        include_debug=False,
        defer_artwork=defer_artwork,
    )
    set_tab_cache(
        user.id,
        media_type,
        rows,
        show_more=show_more,
        activity_version=_get_activity_version(user.id, media_type),
    )
    return rows


def get_tab_status(user_id: int, media_type: str, *, show_more: bool = False) -> dict:
    """Return cache-status metadata for a Discover tab."""
    media_type = _normalize_media_type(media_type)
    payload, is_stale = get_tab_cache(user_id, media_type, show_more=show_more)
    lock_key = _refresh_lock_key(user_id, media_type, show_more=show_more)
    refresh_lock = _cache_get(lock_key)
    if refresh_lock and _lock_is_stale(refresh_lock):
        _cache_delete(lock_key)
        refresh_lock = None

    built_at = _parse_cached_datetime((payload or {}).get("built_at"))
    recently_built = False
    if built_at:
        recently_built = (timezone.now() - built_at) < timedelta(
            seconds=DISCOVER_TAB_RECENTLY_BUILT_SECONDS
        )

    refresh_scheduled = False
    if payload and not is_stale and refresh_lock:
        _cache_delete(lock_key)
        refresh_lock = None
    elif payload and is_stale and refresh_lock is None:
        refresh_scheduled = schedule_tab_refresh(
            user_id,
            media_type,
            show_more=show_more,
            debounce_seconds=DISCOVER_PRIORITY_REFRESH_DEBOUNCE_SECONDS,
            countdown=DISCOVER_PRIORITY_REFRESH_COUNTDOWN,
            priority=DISCOVER_TASK_PRIORITY_INTERACTIVE,
        )
        refresh_lock = _cache_get(lock_key) if refresh_scheduled else refresh_lock

    return {
        "exists": bool(payload),
        "built_at": built_at.isoformat() if built_at else None,
        "is_stale": bool(is_stale),
        "is_refreshing": refresh_lock is not None or refresh_scheduled,
        "recently_built": recently_built,
        "refresh_scheduled": refresh_scheduled,
    }


def warm_sibling_tabs(user, active_media_type: str, *, show_more: bool = False) -> None:
    """Warm missing sibling tabs after loading Discover."""
    active_media_type = _normalize_media_type(active_media_type)
    if active_media_type == ALL_MEDIA_KEY:
        return
    for media_type in get_user_tab_targets(user):
        if media_type == active_media_type:
            continue
        if has_fresh_tab_cache(user.id, media_type, show_more=show_more):
            continue
        schedule_tab_refresh(
            user.id,
            media_type,
            show_more=show_more,
            debounce_seconds=DISCOVER_WARM_SIBLING_DEBOUNCE_SECONDS,
            countdown=DISCOVER_WARM_SIBLING_COUNTDOWN,
            priority=DISCOVER_TASK_PRIORITY_BACKGROUND,
        )


def schedule_user_tab_warmup(
    user,
    *,
    media_types: list[str] | None = None,
    prioritize_media_type: str | None = None,
    show_more: bool = False,
) -> int:
    """Schedule missing or stale Discover tabs for a user in the background."""
    if not user.show_discover:
        return 0
    priority_media_type = _normalize_media_type(prioritize_media_type or ALL_MEDIA_KEY)
    targets = media_types or get_user_tab_targets(user)
    normalized_targets = list(
        dict.fromkeys(_normalize_media_type(target) for target in targets),
    )

    scheduled = 0
    for index, media_type in enumerate(normalized_targets):
        if has_fresh_tab_cache(user.id, media_type, show_more=show_more):
            continue

        is_priority_target = media_type == priority_media_type
        debounce_seconds = (
            DISCOVER_PRIORITY_REFRESH_DEBOUNCE_SECONDS
            if is_priority_target
            else DISCOVER_WARM_SIBLING_DEBOUNCE_SECONDS
        )
        countdown = (
            DISCOVER_PRIORITY_REFRESH_COUNTDOWN
            if is_priority_target
            else DISCOVER_WARM_SIBLING_COUNTDOWN + index
        )
        if schedule_tab_refresh(
            user.id,
            media_type,
            show_more=show_more,
            debounce_seconds=debounce_seconds,
            countdown=countdown,
            priority=(
                DISCOVER_TASK_PRIORITY_FOLLOWUP
                if is_priority_target
                else DISCOVER_TASK_PRIORITY_BACKGROUND
            ),
        ):
            scheduled += 1

    return scheduled


def maybe_schedule_user_warmup(
    user,
    *,
    throttle_seconds: int = DISCOVER_REQUEST_WARMUP_TTL,
) -> int:
    """Throttle and schedule a lightweight request-time Discover warmup."""
    user_id = getattr(user, "id", None)
    if not user_id:
        return 0

    if throttle_seconds:
        warmed = _cache_add(
            _request_warmup_key(user_id),
            1,
            timeout=throttle_seconds,
        )
        if warmed is not True:
            return 0

    return schedule_user_tab_warmup(
        user,
        media_types=[ALL_MEDIA_KEY],
        prioritize_media_type=ALL_MEDIA_KEY,
        show_more=False,
    )


def _invalidate_targets(
    user_id: int,
    media_type: str,
    *,
    debounce_seconds: int,
    countdown: int,
    gate_on_recent_use: bool,
) -> list[str]:
    active_context = get_active_context(user_id)
    targets = get_user_target_media_types_for_change(user_id, media_type)
    # Someone who has not opened Discover lately gets the tab marked stale
    # (the version bump below) instead of a rebuild: the next visit serves the
    # stale rows and rebuilds at once, as it does for any stale tab.
    rebuild_in_background = not gate_on_recent_use or _rebuilds_eagerly(user_id)
    for target_media_type in targets:
        bump_activity_version(user_id, target_media_type)

        prioritized = (
            active_context is not None
            and active_context.media_type == target_media_type
        )
        prioritized_show_more = active_context.show_more if prioritized else False
        if not prioritized and not rebuild_in_background:
            # The cached rows stay: every read checks them against the version
            # bumped above, and the tab's next rebuild deletes them first
            # (refresh_tab_cache). The taste profile has readers outside a tab
            # rebuild (feeds, row refreshes) that check no version, so it goes.
            clear_taste_profile(user_id, target_media_type)
            logger.debug(
                "discover_tab_refresh_deferred user_id=%s media_type=%s "
                "reason=not_recently_used",
                user_id,
                target_media_type,
            )
            continue
        clear_lower_level_cache(user_id, target_media_type)
        if not prioritized and _cache_get(
            _rebuild_cooldown_key(user_id, target_media_type, show_more=False)
        ):
            # Rebuilt moments ago and nobody is looking: the version bump
            # above already marks it stale for the next view.
            continue
        schedule_tab_refresh(
            user_id,
            target_media_type,
            show_more=prioritized_show_more,
            debounce_seconds=(
                DISCOVER_PRIORITY_REFRESH_DEBOUNCE_SECONDS
                if prioritized
                else debounce_seconds
            ),
            countdown=(
                DISCOVER_PRIORITY_REFRESH_COUNTDOWN if prioritized else countdown
            ),
            priority=(
                DISCOVER_TASK_PRIORITY_INTERACTIVE
                if prioritized
                else DISCOVER_TASK_PRIORITY_FOLLOWUP
            ),
        )
        if prioritized_show_more:
            schedule_tab_refresh(
                user_id,
                target_media_type,
                show_more=False,
                debounce_seconds=debounce_seconds,
                countdown=countdown,
                priority=DISCOVER_TASK_PRIORITY_FOLLOWUP,
            )
    return targets


def invalidate_for_media_change(user_id: int, media_type: str) -> list[str]:
    """Invalidate affected Discover tabs for a tracked media change."""
    return _invalidate_targets(
        user_id,
        media_type,
        debounce_seconds=DISCOVER_DEFAULT_REFRESH_DEBOUNCE_SECONDS,
        countdown=DISCOVER_DEFAULT_REFRESH_COUNTDOWN,
        gate_on_recent_use=True,
    )


def invalidate_for_feedback_change(user_id: int, media_type: str) -> list[str]:
    """Invalidate Discover immediately after hidden feedback changes."""
    # Feedback is a Discover action itself, so it always rebuilds.
    return _invalidate_targets(
        user_id,
        media_type,
        debounce_seconds=DISCOVER_PRIORITY_REFRESH_DEBOUNCE_SECONDS,
        countdown=DISCOVER_PRIORITY_REFRESH_COUNTDOWN,
        gate_on_recent_use=False,
    )
