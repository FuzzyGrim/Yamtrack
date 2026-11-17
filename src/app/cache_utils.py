from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

import redis
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django_redis import get_redis_connection

if TYPE_CHECKING:
    from app.media_list_filters import MediaListFilters

logger = logging.getLogger(__name__)


# v19: payload changed from a pickled entry list to a compact ordered-ids
# structure hydrated per page (see media_list_views time_left handling).
TIME_LEFT_CACHE_PREFIX = "time_left_sorted_v19"
_REGISTRY_TEMPLATE = f"{TIME_LEFT_CACHE_PREFIX}_registry_{{user_id}}"

# v2: payload changed from a pickled full MediaListEntry list to a compact
# {media_pk, model, item_pk} order hydrated per page (issue #865). Bumped so a
# rolling deploy can't hand the new code an old pickled entry (or vice versa).
MEDIA_LIST_CACHE_PREFIX = "media_list_v2"
MEDIA_LIST_CACHE_TTL = 60  # seconds — enough for "navigate away and back" use case
# v2: filter payloads include per-tag media-list usage counts.
MEDIA_LIST_FILTER_CACHE_PREFIX = "media_list_filters_v2"
MEDIA_LIST_FILTER_CACHE_TTL = 300
_MEDIA_LIST_REGISTRY_TEMPLATE = f"{MEDIA_LIST_CACHE_PREFIX}_registry_{{user_id}}"

# The media-list keys are built from ``MediaListFilters.cache_token``, so a
# new filter reaches every key on its own. These are the fields a key leaves
# out on purpose.
#
# The filter menu does not depend on the order, or on these narrowing filters.
FILTER_MENU_IGNORED_FILTERS = frozenset(
    {
        "sort",
        "direction",
        "rating",
        "rating_min",
        "rating_max",
        "collection",
        "origin",
        "release_date_from",
        "release_date_to",
        "date_added_from",
        "date_added_to",
    },
)
# The time-left order is TV only; author and format do not filter TV.
TIME_LEFT_IGNORED_FILTERS = frozenset({"author", "format"})


def _alphabet_part(filters: MediaListFilters, title_display: str) -> str:
    """Return the A to Z part; a letter is matched on the displayed title."""
    return f"initial={filters.initial}:title={title_display}" if filters.initial else ""


def build_time_left_cache_key(
    user_id: int,
    filters: MediaListFilters,
    *,
    title_display: str = "",
) -> str:
    """Create the cache key used for time-left sorted TV lists.

    ``filters`` is the view's effective filter set. The time-left order
    ignores the filters in ``TIME_LEFT_IGNORED_FILTERS`` and the anime
    library mode.
    """
    return "_".join(
        (
            TIME_LEFT_CACHE_PREFIX,
            str(user_id),
            str(filters.media_type or ""),
            filters.cache_token(exclude=TIME_LEFT_IGNORED_FILTERS),
            _alphabet_part(filters, title_display),
        ),
    )


def _registry_client():
    """Return a raw Redis client, or None when the cache isn't Redis-backed."""
    try:
        return get_redis_connection("default")
    except Exception as error:  # registries are best-effort
        logger.debug("No raw Redis connection for cache registry: %s", error)
        return None


def _legacy_registry_add(registry_key: str, cache_key: str) -> None:
    """Read-modify-write fallback for a cache that is not a raw Redis."""
    keys = set(cache.get(registry_key) or ())
    if cache_key not in keys:
        keys.add(cache_key)
        cache.set(registry_key, list(keys), getattr(settings, "CACHE_TIMEOUT", None))


def _registry_add(registry_key: str, cache_key: str) -> None:
    """Add ``cache_key`` to the registry with one atomic SADD round trip.

    The registry is a Redis set, so concurrent misses cannot overwrite each
    other the way ``get`` + ``set`` of a whole list did. A registry written by
    an older release is a pickled string; it is migrated on first touch.
    """
    client = _registry_client()
    if client is None:
        _legacy_registry_add(registry_key, cache_key)
        return
    raw_key = cache.make_key(registry_key)
    timeout = getattr(settings, "CACHE_TIMEOUT", None)
    try:
        try:
            pipe = client.pipeline()
            pipe.sadd(raw_key, cache_key)
            if timeout:
                pipe.expire(raw_key, timeout)
            pipe.execute()
        except redis.ResponseError:  # WRONGTYPE: a pickled list from an old release
            legacy = cache.get(registry_key)
            members = [*legacy, cache_key] if isinstance(legacy, list) else [cache_key]
            client.delete(raw_key)
            client.sadd(raw_key, *members)
    except redis.RedisError as error:
        logger.debug("Cache registry %s unavailable: %s", registry_key, error)


# Atomically read and drop a set registry. A non-set value is an older release's
# pickled list: leave it untouched (nil) so the caller can read it normally.
_TAKE_REGISTRY_LUA = """
if redis.call('TYPE', KEYS[1]).ok == 'set' then
    local members = redis.call('SMEMBERS', KEYS[1])
    redis.call('DEL', KEYS[1])
    return members
end
return false
"""


def _registry_take(registry_key: str) -> list[str]:
    """Return and remove every key in the registry, atomically.

    Read and delete are one script, so a key registered while we are
    invalidating lands in the next registry instead of being erased unseen.
    """
    client = _registry_client()
    if client is not None:
        try:
            members = client.eval(_TAKE_REGISTRY_LUA, 1, cache.make_key(registry_key))
        except redis.RedisError as error:
            logger.debug("Cache registry %s unavailable: %s", registry_key, error)
            return []
        if members is not False and members is not None:
            return [m.decode() if isinstance(m, bytes) else m for m in members]
    keys = list(cache.get(registry_key) or ())
    cache.delete(registry_key)
    return keys


def _registry_key_for_user(user_id: int) -> str:
    return _REGISTRY_TEMPLATE.format(user_id=user_id)


def register_time_left_cache_key(user_id: int, cache_key: str) -> None:
    """Keep track of active cache keys for a user so we can invalidate them later."""
    _registry_add(_registry_key_for_user(user_id), cache_key)


def clear_time_left_cache_for_user(user_id: int) -> None:
    """Invalidate all cached time-left lists for a user."""
    keys = _registry_take(_registry_key_for_user(user_id))

    if not keys:
        return

    cache.delete_many(keys)

    logger.debug(
        "Cleared %s time_left cache entries for user %s",
        len(keys),
        user_id,
    )


def build_media_list_cache_key(
    user_id: int,
    filters: MediaListFilters,
    *,
    sort: str,
    library_mode: str = "",
    title_display: str = "",
    entry_grouping: bool | None = None,
) -> str:
    """Create the cache key for a fully-processed media list page.

    ``filters`` is the view's effective filter set (saved status, masked
    filters and pinned providers already applied). ``sort`` is the sort the
    user chose, ``library_mode`` the anime library mode where it applies, and
    ``entry_grouping`` whether matching plays show as one card or several
    (``None`` where a media type has no such choice).
    """
    grouping = {None: "", False: "grouped", True: "separate"}[entry_grouping]
    return "_".join(
        (
            MEDIA_LIST_CACHE_PREFIX,
            str(user_id),
            str(filters.media_type or ""),
            sort or "",
            filters.cache_token(),
            library_mode or "",
            _alphabet_part(filters, title_display),
            grouping,
        ),
    )


def build_media_list_filter_cache_key(
    user_id: int,
    filters: MediaListFilters,
    *,
    library_mode: str = "",
    title_display: str = "",
) -> str:
    """Create the cache key for media-list filter summary data.

    The filter menu ignores the filters in ``FILTER_MENU_IGNORED_FILTERS``
    and the entry-grouping mode.
    """
    return "_".join(
        (
            MEDIA_LIST_FILTER_CACHE_PREFIX,
            str(user_id),
            str(filters.media_type or ""),
            filters.cache_token(exclude=FILTER_MENU_IGNORED_FILTERS),
            library_mode or "",
            _alphabet_part(filters, title_display),
        ),
    )


HOME_ROW_CACHE_PREFIX = "home_row_v2"
HOME_ROW_CACHE_TTL = 60  # seconds — matches the media-list cache horizon
# A Python-ordered shelf's id order is the expensive part. Keep it well past the
# rendered section so a section expiry (or a warm visit minutes later) reloads
# one page instead of re-ranking the shelf. Media writes invalidate it through
# the same registry; only provider/event refreshes can lag.
HOME_ROW_ORDER_CACHE_TTL = 1800
# Up Next's membership scan depends on air dates, which move without a user
# write, so it expires sooner than a shelf's order. Media writes still clear it.
HOME_ROW_RELEASED_CACHE_TTL = 300
_HOME_ROW_REGISTRY_TEMPLATE = f"{HOME_ROW_CACHE_PREFIX}_registry_{{user_id}}"


def build_home_row_cache_key(user_id: int, row_id: int, items_limit: int) -> str:
    """Cache key for one built home-row section."""
    return f"{HOME_ROW_CACHE_PREFIX}_{user_id}_{row_id}_{items_limit}"


def build_home_tv_shows_cache_key(user_id: int) -> str:
    """Cache key for the show records Home's TV shelves share (``SharedShows``).

    The version changes with the record's shape (``tv_show_facts._SEASON_FIELDS``).
    """
    return f"{HOME_ROW_CACHE_PREFIX}_tv_shows_v2_{user_id}"


def _home_row_registry_key(user_id: int) -> str:
    return _HOME_ROW_REGISTRY_TEMPLATE.format(user_id=user_id)


def register_home_row_cache_key(user_id: int, cache_key: str) -> None:
    """Track active home_row cache keys so they can be invalidated on save."""
    _registry_add(_home_row_registry_key(user_id), cache_key)


def clear_home_row_cache_for_user(user_id: int) -> None:
    """Invalidate all cached home_row entries for a user."""
    keys = _registry_take(_home_row_registry_key(user_id))

    if not keys:
        return

    cache.delete_many(keys)

    logger.debug(
        "Cleared %s home_row cache entries for user %s",
        len(keys),
        user_id,
    )


def _media_list_registry_key(user_id: int) -> str:
    return _MEDIA_LIST_REGISTRY_TEMPLATE.format(user_id=user_id)


def register_media_list_cache_key(user_id: int, cache_key: str) -> None:
    """Track active media_list cache keys so they can be invalidated on save."""
    _registry_add(_media_list_registry_key(user_id), cache_key)


MEDIA_LIST_BUILD_LOCK_TTL = 20  # seconds — a crashed builder frees readers fast
MEDIA_LIST_BUILD_WAIT = 5.0  # seconds a follower waits before building anyway
_BUILD_POLL = 0.05


def acquire_build_lock(cache_key: str, ttl: int) -> bool:
    """Claim the right to build ``cache_key``; False if a builder is running.

    A request that loses waits with ``wait_for_build`` instead of computing
    the same result a second time.
    """
    return bool(cache.add(f"{cache_key}_building", 1, ttl))


def release_build_lock(cache_key: str) -> None:
    """Drop the build lock once the result is cached (or was not cacheable)."""
    cache.delete(f"{cache_key}_building")


def wait_for_build(cache_key: str, is_ready, wait: float) -> bool:
    """Poll until ``is_ready()`` or the lock clears; False means build it yourself."""
    deadline = time.monotonic() + wait
    lock_key = f"{cache_key}_building"
    while time.monotonic() < deadline:
        if is_ready():
            return True
        if cache.get(lock_key) is None:
            return is_ready()
        time.sleep(_BUILD_POLL)
    return is_ready()


def acquire_media_list_build_lock(cache_key: str) -> bool:
    """Claim the right to rebuild a media list entry; False if one is running."""
    return acquire_build_lock(cache_key, MEDIA_LIST_BUILD_LOCK_TTL)


def release_media_list_build_lock(cache_key: str) -> None:
    """Drop the build lock once the result is cached."""
    release_build_lock(cache_key)


def wait_for_media_list_build(cache_key: str, is_ready) -> bool:
    """Poll until ``is_ready()`` or the lock clears; False means build it yourself."""
    return wait_for_build(cache_key, is_ready, MEDIA_LIST_BUILD_WAIT)


def clear_media_list_cache_for_user(user_id: int) -> None:
    """Invalidate all cached media_list entries for a user."""
    keys = _registry_take(_media_list_registry_key(user_id))

    if not keys:
        return

    cache.delete_many(keys)

    logger.debug(
        "Cleared %s media_list cache entries for user %s",
        len(keys),
        user_id,
    )


# What changed, for clear_library_view_caches. Callers name the change; the
# table below decides which views it can affect. Contract and rationale:
# docs/architecture/view-caches.md.
LIBRARY_EDIT = "library_edit"  # tracked rows written; pass media_types
TAGS = "tags"  # tags applied, removed, created or deleted
LIST_SETTING = "list_setting"  # a media-list display preference
HOME_SETTING = "home_setting"  # which media types Home shows
EPISODE_DETAILS = "episode_details"  # provider runtime / air dates, no user write

_TIME_LEFT = "time_left"
_MEDIA_LIST = "media_list"
_HOME = "home"
_TV_MEDIA_TYPES = frozenset({"tv", "season", "episode"})
_VIEWS_BY_CHANGE = {
    # Time-left only holds TV, so it is added for TV media types below.
    LIBRARY_EDIT: (_MEDIA_LIST, _HOME),
    # Home is added only for a user with a tag-filtered Home row (below).
    TAGS: (_MEDIA_LIST,),
    LIST_SETTING: (_MEDIA_LIST,),
    HOME_SETTING: (_HOME,),
    # Home is left out on purpose: provider refreshes touch every tracker of
    # a show, and Home's order/Up Next caches expire on their own TTLs.
    EPISODE_DETAILS: (_TIME_LEFT, _MEDIA_LIST),
}


def _home_filters_by_tag(user_id: int) -> bool:
    from users.models import HomeScreenRow

    return any(
        (filters or {}).get("tag")
        for filters in HomeScreenRow.objects.filter(
            user_id=user_id,
            enabled=True,
        ).values_list("filters", flat=True)
    )


def _clear_views(user_id: int, views) -> None:
    clear_by_view = {
        _TIME_LEFT: clear_time_left_cache_for_user,
        _MEDIA_LIST: clear_media_list_cache_for_user,
        _HOME: clear_home_row_cache_for_user,
    }
    for view in views:
        clear_by_view[view](user_id)


def clear_library_view_caches(user_id, change=LIBRARY_EDIT, *, media_types=()) -> None:
    """Clear the per-user view caches that ``change`` can make stale.

    The one entry point for the media list, Home rows and the TV time-left
    order. Inside a transaction it clears now and again on commit, because a
    concurrent reader can re-cache pre-write rows before the write commits.
    """
    if not user_id:
        return
    views = list(_VIEWS_BY_CHANGE[change])
    if change == LIBRARY_EDIT and _TV_MEDIA_TYPES.intersection(media_types):
        views.insert(0, _TIME_LEFT)
    if change == TAGS and _home_filters_by_tag(user_id):
        views.append(_HOME)

    _clear_views(user_id, views)
    if transaction.get_connection().in_atomic_block:
        transaction.on_commit(lambda: _clear_views(user_id, views))


def clear_show_view_caches(
    media_id,
    source,
    change=EPISODE_DETAILS,
    *,
    user_ids=None,
) -> None:
    """Clear view caches for every user tracking a show or one of its seasons."""
    if not media_id or not source:
        return
    if user_ids is None:
        from app.models import TV, Season

        user_ids = set(
            TV.objects.filter(
                item__media_id=media_id,
                item__source=source,
            ).values_list("user_id", flat=True),
        )
        user_ids.update(
            Season.objects.filter(
                item__media_id=media_id,
                item__source=source,
            ).values_list("user_id", flat=True),
        )
    for user_id in sorted(set(user_ids)):
        clear_library_view_caches(user_id, change, media_types=("tv",))
