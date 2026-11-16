"""Cache lifecycle management: invalidation and background-task scheduling."""

import logging
import uuid
from collections.abc import Iterable

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone

from app.history_cache_utils import (
    HISTORY_COVERAGE_REPAIR_LOCK_TTL,
    HISTORY_COVERAGE_REPAIR_PREFIX,
    HISTORY_LOGGING_STYLES,
    HISTORY_REFRESH_LOCK_MAX_AGE,
    _bump_history_era,
    _cache_key,
    _coverage_repair_key,
    _day_cache_key,
    _day_key_from_value,
    _normalize_logging_style,
    _refresh_dedupe_key,
    _refresh_lock_key,
    _refresh_rerun_key,
    _typed_history_index_registry_key,
    history_coverage_styles,
)

logger = logging.getLogger(__name__)


def history_rebuild_styles(user_id: int, logging_styles=None):
    """Split invalidated styles into ``(rebuild, drop)``.

    Only the style the user reads is rebuilt (``history_coverage_styles``).
    The other is dropped, so it is rebuilt on demand from rows and never
    served from before the write.
    """
    styles = tuple(
        dict.fromkeys(
            _normalize_logging_style(style)
            for style in logging_styles or HISTORY_LOGGING_STYLES
        ),
    )
    user = (
        get_user_model()
        .objects.filter(pk=user_id)
        .only("id", "game_logging_style")
        .first()
    )
    keep = history_coverage_styles(user)
    return (
        tuple(style for style in styles if style in keep),
        tuple(style for style in styles if style not in keep),
    )


def _clean_refresh_lock(lock_key: str):
    refresh_lock = cache.get(lock_key)
    if refresh_lock:
        if not isinstance(refresh_lock, dict):
            cache.delete(lock_key)
            return None
        started_at = refresh_lock.get("started_at")
        if started_at and timezone.now() - started_at > HISTORY_REFRESH_LOCK_MAX_AGE:
            cache.delete(lock_key)
            return None
    return refresh_lock


def _clear_marker_key(user_id: int, logging_style: str) -> str:
    return f"{HISTORY_COVERAGE_REPAIR_PREFIX}_cleared_{user_id}_{logging_style}"


def _repair_done_key(user_id: int, logging_style: str) -> str:
    return f"{HISTORY_COVERAGE_REPAIR_PREFIX}_done_{user_id}_{logging_style}"


def record_history_days_cleared(
    user_id: int, logging_style: str, reason: str | None
) -> None:
    """Remember when and why day payloads were deleted on purpose.

    Written without a TTL so ``volatile-lru`` never evicts it: the repair task
    reads it to tell a deliberate clear from a payload Redis dropped.
    """
    cache.set(
        _clear_marker_key(user_id, logging_style),
        {"at": timezone.now(), "reason": reason or "unspecified"},
        timeout=None,
    )


def record_history_repair_complete(
    user_id: int, logging_style: str, days: int
) -> None:
    """Remember when a repair last found every indexed day cached."""
    cache.set(
        _repair_done_key(user_id, logging_style),
        {"at": timezone.now(), "days": days},
        timeout=None,
    )


def classify_missing_history_days(user_id: int, logging_style: str, day_ttl):
    """Say why day payloads are missing: ``(reason, detail)``.

    ``invalidated``: deleted on purpose after the last complete repair;
    ``evicted``: gone before their TTL with no delete recorded (Redis memory
    pressure or a flush); ``expired``: the last complete repair is older than
    the TTL; ``absent``: never built, or nothing recorded.
    """
    cleared = cache.get(_clear_marker_key(user_id, logging_style))
    done = cache.get(_repair_done_key(user_id, logging_style))
    if cleared and (not done or cleared["at"] >= done["at"]):
        return "invalidated", cleared.get("reason", "unspecified")
    if done:
        age = timezone.now() - done["at"]
        return ("expired" if age >= day_ttl else "evicted"), None
    return "absent", None


def _delete_history_cache_entries(
    user_id: int, logging_style: str, day_keys=None, reason: str | None = None
):
    # Retire the era BEFORE deleting anything: a builder that captured the
    # previous token can still write its (possibly stale) index afterwards,
    # but only into a namespace no reader will select again. The deletes
    # below are hygiene on top of that guarantee, not the guarantee itself.
    _bump_history_era(user_id, logging_style)
    if day_keys is None:
        index_entry = cache.get(_cache_key(user_id, logging_style))
        day_keys = index_entry.get("days", []) if index_entry else []

    normalized_keys = []
    for value in day_keys:
        day_key = _day_key_from_value(value)
        if day_key:
            normalized_keys.append(day_key)

    if normalized_keys:
        cache.delete_many(
            [
                _day_cache_key(user_id, logging_style, day_key)
                for day_key in normalized_keys
            ],
        )
        record_history_days_cleared(user_id, logging_style, reason)
    cache.delete(_cache_key(user_id, logging_style))
    registry_key = _typed_history_index_registry_key(user_id, logging_style)
    typed_index_keys = cache.get(registry_key) or []
    if typed_index_keys:
        cache.delete_many(typed_index_keys)
    cache.delete(registry_key)


def invalidate_history_days(
    user_id: int,
    day_keys: Iterable | None,
    logging_styles: Iterable | None = None,
    reason: str | None = None,
    force: bool = False,
    refresh_index: bool = True,
):
    """Invalidate per-day history cache entries for a user.

    Day-scoped invalidations keep the existing payloads readable by default and
    schedule a targeted rebuild. Hard deletes are reserved for explicit force
    operations such as cache-version busts, and for a style that is not
    rebuilt (see ``history_rebuild_styles``): its changed days and its index
    are deleted, so its next read rebuilds them from rows.

    Returns the styles that are rebuilt.
    """
    normalized_keys = []
    for value in day_keys or []:
        day_key = _day_key_from_value(value)
        if day_key:
            normalized_keys.append(day_key)
    logging_styles, dropped_styles = history_rebuild_styles(user_id, logging_styles)

    for logging_style in dropped_styles:
        _delete_history_cache_entries(
            user_id, logging_style, normalized_keys, reason
        )
        logger.info(
            "history_day_invalidate user_id=%s logging_style=%s dates=%s "
            "reason=%s deleted=%s rebuild=false",
            user_id,
            logging_style,
            len(normalized_keys),
            reason or "unspecified",
            bool(normalized_keys),
        )

    for style in logging_styles:
        logging_style = _normalize_logging_style(style)
        # Retire the typed-index era first (see _delete_history_cache_entries):
        # afterwards, stale typed publishes can only land in unreachable
        # namespaces. Registry deletion below then reclaims them eagerly;
        # TTL is the backstop for any racer that re-appends after this point.
        _bump_history_era(user_id, logging_style)
        registry_key = _typed_history_index_registry_key(user_id, logging_style)
        typed_index_keys = cache.get(registry_key) or []
        if typed_index_keys:
            cache.delete_many(typed_index_keys)
        cache.delete(registry_key)
        if force and normalized_keys:
            cache.delete_many(
                [
                    _day_cache_key(user_id, logging_style, day_key)
                    for day_key in normalized_keys
                ],
            )
            record_history_days_cleared(user_id, logging_style, reason)
        logger.info(
            "history_day_invalidate user_id=%s logging_style=%s dates=%s reason=%s deleted=%s",
            user_id,
            logging_style,
            len(normalized_keys),
            reason or "unspecified",
            force and bool(normalized_keys),
        )

    if refresh_index:
        for style in logging_styles:
            logging_style = _normalize_logging_style(style)
            scheduled = schedule_history_refresh(
                user_id,
                logging_style,
                warm_days=0,
                day_keys=normalized_keys or None,
            )
            logger.info(
                "history_index_refresh_scheduled user_id=%s logging_style=%s warm_days=0 day_keys=%s scheduled=%s reason=%s",
                user_id,
                logging_style,
                len(normalized_keys) if normalized_keys else 0,
                scheduled,
                reason or "unspecified",
            )
    return logging_styles


def invalidate_history_cache(
    user_id: int,
    force: bool = False,
    day_keys: Iterable | None = None,
    logging_styles: Iterable | None = None,
    reason: str | None = None,
):
    """Remove cached history for a user, optionally scoped to specific days.

    If a refresh is in progress, keep the old cache so users can see it
    while the refresh completes. Otherwise, delete the cache/index.
    """
    if day_keys is not None:
        invalidate_history_days(
            user_id,
            day_keys=day_keys,
            logging_styles=logging_styles,
            force=force,
            refresh_index=True,
            reason=reason,
        )
        return

    logging_styles = logging_styles or HISTORY_LOGGING_STYLES
    for style in logging_styles:
        logging_style = _normalize_logging_style(style)
        refresh_lock = _clean_refresh_lock(_refresh_lock_key(user_id, logging_style))
        if refresh_lock is None or force:
            _delete_history_cache_entries(user_id, logging_style, None, reason)
            logger.info(
                "history_cache_invalidate_all user_id=%s logging_style=%s reason=%s",
                user_id,
                logging_style,
                reason or "full_clear",
            )

    # Schedule refresh after invalidating all cache
    # This ensures cache is rebuilt and page doesn't get stuck
    if force:
        # Everything was deleted, so a style left unscheduled is simply
        # rebuilt on its next read.
        rebuild_styles, _dropped = history_rebuild_styles(user_id, logging_styles)
        for logging_style in rebuild_styles:
            scheduled = schedule_history_refresh(
                user_id,
                logging_style,
                warm_days=0,  # Index-only refresh, don't warm days
            )
            logger.info(
                "history_index_refresh_scheduled user_id=%s logging_style=%s warm_days=0 scheduled=%s reason=%s",
                user_id,
                logging_style,
                scheduled,
                "full_invalidate",
            )


def schedule_history_refresh(
    user_id: int,
    logging_style: str = "repeats",
    debounce_seconds: int = 30,
    countdown: int = 3,
    warm_days: int | None = None,
    day_keys: Iterable | None = None,
    allow_inline: bool = True,
    priority: int | None = None,
):
    """Queue a background refresh for a user's history cache.

    Args:
        user_id: User ID
        logging_style: Logging style for history
        debounce_seconds: Seconds to debounce refresh requests
        countdown: Seconds to delay task execution (default 3)
        warm_days: Optional warm window for day payloads
        day_keys: Optional list of day keys to warm
    """
    logging_style = _normalize_logging_style(logging_style)
    lock_key = _refresh_lock_key(user_id, logging_style)
    normalized_day_keys = []
    for value in day_keys or []:
        day_key = _day_key_from_value(value)
        if day_key:
            normalized_day_keys.append(day_key)
    dedupe_key = _refresh_dedupe_key(user_id, logging_style, normalized_day_keys)
    # Keep TTL close to the frontend polling timeout so locks don't appear "stuck"
    # while still covering normal task execution time.
    lock_ttl = 120  # Matches CacheUpdater timeout window
    lock_payload = {"started_at": timezone.now()}
    if normalized_day_keys:
        lock_payload["day_keys"] = normalized_day_keys
        # Store dedupe_key in payload so we can delete it when task completes
        lock_payload["dedupe_key"] = dedupe_key
    if debounce_seconds and not cache.add(dedupe_key, lock_payload, debounce_seconds):
        # A refresh of these days is queued or running. If it has already
        # read its rows, this change is not in them: leave a marker the
        # refresh checks when it finishes, so it queues one more pass.
        cache.set(_refresh_rerun_key(dedupe_key), uuid.uuid4().hex, lock_ttl)
        return False

    # Extend the lock TTL to cover the full task duration
    # This ensures the lock exists even if the task takes longer than debounce_seconds
    cache.set(dedupe_key, lock_payload, lock_ttl)
    if dedupe_key != lock_key:
        cache.set(lock_key, lock_payload, lock_ttl)

    try:
        from app.tasks import refresh_history_cache_task

        task_args = [user_id, logging_style]
        task_kwargs = {}
        if warm_days is not None:
            task_kwargs["warm_days"] = warm_days
        if normalized_day_keys:
            task_kwargs["day_keys"] = normalized_day_keys
        refresh_history_cache_task.apply_async(
            args=task_args,
            kwargs=task_kwargs,
            countdown=countdown,
            priority=(
                getattr(settings, "CELERY_TASK_PRIORITY_INTERACTIVE", 0)
                if priority is None
                else priority
            ),
        )
    except Exception as exc:  # pragma: no cover - Celery not available
        if not allow_inline:
            cache.delete(dedupe_key)
            if dedupe_key != lock_key:
                cache.delete(lock_key)
            logger.warning(
                "Failed to schedule history cache refresh for user %s: %s",
                user_id,
                exc,
            )
            return False
        logger.debug(
            "Falling back to inline history cache rebuild for user %s: %s",
            user_id,
            exc,
        )
        from app.history_cache import (
            refresh_history_cache,  # deferred to avoid circular import
        )

        refresh_history_cache(user_id, logging_style=logging_style, warm_days=warm_days)
        return False
    else:
        return True


def schedule_history_day_cache_coverage(
    user_id: int,
    logging_style: str = "repeats",
    *,
    debounce_seconds: int = 60 * 10,
    countdown: int = 30,
    batch_size: int | None = None,
    priority: int | None = None,
):
    """Queue low-priority repair work for missing persisted day payloads."""
    logging_style = _normalize_logging_style(logging_style)
    repair_key = _coverage_repair_key(user_id, logging_style)
    lock_payload = {
        "started_at": timezone.now().isoformat(),
        "batch_size": batch_size,
    }
    lock_ttl = max(int(debounce_seconds or 0), HISTORY_COVERAGE_REPAIR_LOCK_TTL)
    if debounce_seconds and not cache.add(repair_key, lock_payload, debounce_seconds):
        return False

    cache.set(repair_key, lock_payload, lock_ttl)

    try:
        from app.tasks import repair_history_day_cache_coverage_task

        task_kwargs = {
            "user_id": user_id,
            "logging_style": logging_style,
        }
        if batch_size is not None:
            task_kwargs["batch_size"] = batch_size
        repair_history_day_cache_coverage_task.apply_async(
            kwargs=task_kwargs,
            countdown=countdown,
            priority=(
                getattr(settings, "CELERY_TASK_PRIORITY_BACKGROUND", 9)
                if priority is None
                else priority
            ),
        )
    except Exception as exc:  # pragma: no cover - Celery not available
        cache.delete(repair_key)
        logger.warning(
            "Failed to schedule history day coverage repair for user %s: %s",
            user_id,
            exc,
        )
        return False
    else:
        return True
