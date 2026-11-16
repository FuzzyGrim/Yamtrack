"""History cache reader, paginator, and refresh/repair workers."""

import logging
import time
from collections.abc import Iterable
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import formats, timezone
from django.utils.dateparse import parse_date

from app import helpers
from app.history_cache_day_builder import (
    _build_and_cache_history_day,
    build_history_day,
    history_media_types_by_day,
)
from app.history_cache_index import (
    _missing_history_day_keys,
    build_history_index,
    cache_history_index,
)
from app.history_cache_lifecycle import (
    _clean_refresh_lock,
    classify_missing_history_days,
    record_history_repair_complete,
    schedule_history_day_cache_coverage,
    schedule_history_refresh,
)
from app.history_cache_range_prefetch import HistoryRangePrefetch
from app.history_cache_serialization import (
    _deserialize_history_day,
    _serialize_history_day,
)
from app.history_cache_utils import (
    HISTORY_COLD_MISS_WARM_DAYS,
    HISTORY_COVERAGE_REPAIR_BATCH_SIZE,
    HISTORY_DAY_CACHE_TIMEOUT,
    HISTORY_DAYS_PER_PAGE,
    HISTORY_ENTRIES_PER_DAY_PAGE,
    HISTORY_STALE_AFTER,
    HISTORY_WARM_DAYS,
    _cache_key,
    _coverage_repair_key,
    _current_history_era,
    _date_from_day_key,
    _day_cache_key,
    _day_key_from_value,
    _normalize_logging_style,
    _refresh_dedupe_key,
    _refresh_lock_key,
    _refresh_rerun_key,
    _release_refresh_locks,
    _typed_history_index_key,
    apply_history_entry_cap,
    expand_history_media_types,
    history_coverage_day_keys,
    history_coverage_styles,
)
from app.task_cooperation import CooperativeRun

logger = logging.getLogger(__name__)


def get_month_history(
    user,
    year: int,
    month: int,
    logging_style_override=None,
    *,
    entry_filter=None,
):
    """Get history days for a specific calendar month.

    Reads the month from the cached history index plus indexed per-day payloads
    that are kept warm by media events. Any missing indexed day payloads are
    repaired inline so the page can render final content in a single response.
    With no index cached, the month's days come from a month-bounded build and
    the full index is rebuilt in the background.

    Args:
        user: User instance
        year: Calendar year (e.g., 2026)
        month: Calendar month (1-12)
        logging_style_override: Optional logging style override

    Returns:
        Tuple of (history_days, cache_meta) where cache_meta contains:
        - refreshing: bool - Whether a background refresh is in progress
        - refresh_reason: str or None - Why refresh was triggered
    """
    start = time.perf_counter()
    logging_style = _normalize_logging_style(logging_style_override, user)
    cache_meta = {"refreshing": False, "refresh_reason": None}

    cache_key = _cache_key(user.id, logging_style)
    lock_key = _refresh_lock_key(user.id, logging_style)
    refresh_lock = _clean_refresh_lock(lock_key)

    cache_entry = cache.get(cache_key)
    cache_age_s = None
    if cache_entry:
        built_at = cache_entry.get("built_at")
        if built_at:
            cache_age_s = (timezone.now() - built_at).total_seconds()
        if (
            (built_at and timezone.now() - built_at > HISTORY_STALE_AFTER)
            or cache_entry.get("era") != _current_history_era(user.id, logging_style)
        ) and refresh_lock is None:
            scheduled = schedule_history_refresh(user.id, logging_style, warm_days=0)
            logger.info(
                "history_index_stale_refresh user_id=%s logging_style=%s scheduled=%s cache_age_s=%s",
                user.id,
                logging_style,
                scheduled,
                cache_age_s,
            )
    else:
        # A month needs only its own days (the month links are calendar math),
        # so answer from a month-bounded build and leave the whole-history
        # index to the background. The month list is never published as the
        # index: readers treat that key as every day that exists.
        first_day = date(year, month, 1)
        last_day = (first_day + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        index_day_keys = build_history_index(
            user, logging_style, window=(first_day, last_day)
        )
        cache_entry = {"days": index_day_keys}
        scheduled = (
            refresh_lock is None
            and schedule_history_refresh(
                user.id, logging_style, warm_days=0, allow_inline=False
            )
        )
        logger.info(
            "history_index_month_window user_id=%s logging_style=%s year=%s "
            "month=%s days=%s scheduled=%s lock=%s",
            user.id,
            logging_style,
            year,
            month,
            len(index_day_keys),
            scheduled,
            refresh_lock is not None,
        )

    index_days = cache_entry.get("days", [])
    month_prefix = f"{year}{month:02d}"
    month_day_keys = [
        day_key for day_key in index_days if str(day_key).startswith(month_prefix)
    ]

    day_cache_keys = [
        _day_cache_key(user.id, logging_style, day_key) for day_key in month_day_keys
    ]
    day_payloads = cache.get_many(day_cache_keys)
    cache_hits = len(day_payloads)
    logger.info(
        "history_month_cache_lookup user_id=%s year=%s month=%s indexed_days=%s "
        "cache_hits=%s lock=%s cache_age_s=%s",
        user.id,
        year,
        month,
        len(month_day_keys),
        cache_hits,
        refresh_lock is not None,
        cache_age_s,
    )

    if not month_day_keys:
        logger.info(
            "history_month_result user_id=%s year=%s month=%s days=0 elapsed_ms=%.2f",
            user.id,
            year,
            month,
            (time.perf_counter() - start) * 1000,
        )
        return [], cache_meta

    history_days = []
    missing_days = []
    cached_days = {}
    for day_key in month_day_keys:
        payload_key = _day_cache_key(user.id, logging_style, day_key)
        payload = day_payloads.pop(payload_key, None)
        if payload is None:
            missing_days.append(day_key)
        else:
            cached_days[day_key] = _deserialize_history_day(
                payload,
                max_entries=HISTORY_ENTRIES_PER_DAY_PAGE,
                entry_filter=entry_filter,
            )
            history_days.append(cached_days[day_key])

    if missing_days:
        logger.warning(
            "history_month_cache_repair user_id=%s year=%s month=%s indexed_days=%s "
            "cached=%s missing=%s lock=%s",
            user.id,
            year,
            month,
            len(month_day_keys),
            len(history_days),
            len(missing_days),
            refresh_lock is not None,
        )
        day_media_types = history_media_types_by_day(
            user,
            missing_days,
            logging_style,
        )
        prefetch = HistoryRangePrefetch(
            user, missing_days, logging_style, day_media_types,
        )
        history_days = []
        for day_key in month_day_keys:
            if day_key in cached_days:
                history_days.append(cached_days[day_key])
                continue
            history_days.append(
                _window_history_day(
                    _build_and_cache_history_day(
                        user,
                        day_key,
                        logging_style,
                        media_types=day_media_types.get(day_key),
                        prefetch=prefetch,
                    ),
                    max_entries=HISTORY_ENTRIES_PER_DAY_PAGE,
                    entry_filter=entry_filter,
                )
            )
        schedule_history_day_cache_coverage(
            user.id,
            logging_style,
            countdown=15,
        )

    logger.info(
        "history_month_result user_id=%s year=%s month=%s days=%s "
        "source=cache elapsed_ms=%.2f",
        user.id,
        year,
        month,
        len(history_days),
        (time.perf_counter() - start) * 1000,
    )

    return history_days, cache_meta


def get_cached_history_day(
    user,
    day_key,
    logging_style_override=None,
    *,
    entry_offset=0,
    max_entries=None,
    entry_filter=None,
):
    """Read one cached history day, repairing only that day on a miss."""
    normalized_day_key = _day_key_from_value(day_key)
    if not normalized_day_key:
        return None

    logging_style = _normalize_logging_style(logging_style_override, user)
    cache_key = _day_cache_key(user.id, logging_style, normalized_day_key)
    payload = cache.get(cache_key)
    if payload is not None:
        return _deserialize_history_day(
            payload,
            entry_offset=entry_offset,
            max_entries=max_entries,
            entry_filter=entry_filter,
        )

    logger.warning(
        "history_day_fragment_cache_miss user_id=%s logging_style=%s day_key=%s",
        user.id,
        logging_style,
        normalized_day_key,
    )
    day_payload = _build_and_cache_history_day(
        user,
        normalized_day_key,
        logging_style_override=logging_style,
    )
    if day_payload is None:
        return None
    if max_entries is None and not entry_offset:
        return day_payload
    return _window_history_day(
        day_payload,
        entry_offset,
        max_entries,
        entry_filter=entry_filter,
    )


PREFETCH_MIN_DAYS = 2
PREFETCH_MAX_SPAN_DAYS = 62


def _span_prefetch(user, day_keys, logging_style):
    """One span-wide load for several missing days, unless they are far apart.

    The prefetch reads every row between the first and last day, so days spread
    over years (an anniversary, a sparse history) cost more together than apart.
    """
    dates = sorted(
        d for d in (_date_from_day_key(key) for key in day_keys) if d
    )
    if len(dates) < PREFETCH_MIN_DAYS or (dates[-1] - dates[0]).days > PREFETCH_MAX_SPAN_DAYS:
        return {}, None
    day_media_types = history_media_types_by_day(user, day_keys, logging_style)
    return day_media_types, HistoryRangePrefetch(
        user, day_keys, logging_style, day_media_types
    )


def get_history_index_day_keys(user, logging_style_override=None):
    """Return every indexed day key, building and caching the index on a miss."""
    logging_style = _normalize_logging_style(logging_style_override, user)
    era = _current_history_era(user.id, logging_style)
    entry = cache.get(_cache_key(user.id, logging_style))
    built_at = entry.get("built_at") if entry else None
    if (
        entry
        and entry.get("era") == era
        and built_at
        and timezone.now() - built_at <= HISTORY_STALE_AFTER
    ):
        index_days = entry.get("days", [])
    else:
        index_days = build_history_index(user, logging_style_override=logging_style)
        cache_history_index(user.id, logging_style, index_days, era=era)
    return [key for key in (_day_key_from_value(v) for v in index_days) if key]


def get_cached_history_days(user, day_keys, logging_style_override=None):
    """Return full day payloads for ``day_keys`` (in order), repairing misses together."""
    logging_style = _normalize_logging_style(logging_style_override, user)
    keys = [key for key in (_day_key_from_value(v) for v in day_keys) if key]
    payloads = cache.get_many([_day_cache_key(user.id, logging_style, k) for k in keys])
    days, missing = {}, []
    for key in keys:
        payload = payloads.get(_day_cache_key(user.id, logging_style, key))
        if payload is None:
            missing.append(key)
        else:
            days[key] = _deserialize_history_day(payload, max_entries=None)
    day_media_types, prefetch = _span_prefetch(user, missing, logging_style)
    for key in missing:
        days[key] = _build_and_cache_history_day(
            user,
            key,
            logging_style,
            media_types=day_media_types.get(key),
            prefetch=prefetch,
        )
    return [days[key] for key in keys if days.get(key)]


def get_history_days(
    user,
    filters=None,
    date_filters=None,
    logging_style_override=None,
    *,
    cap_entries_per_day=True,
    max_entries_per_day=None,
):
    """Build history days directly (used for filtered requests).

    `cap_entries_per_day` must be False for callers that flatten the result
    into a per-entry list (`flat=1`), which paginate over individual entries
    via their own `limit`/`offset` — capping entries per day here would
    silently make entries beyond the cap unreachable no matter how far such
    a caller paginates, defeating flat mode's per-entry pagination model.
    Day-grouped callers should leave it True (the default) — a single busy
    day could otherwise blow up the response/page render (#1004); pass
    `max_entries_per_day` to override the `HISTORY_ENTRIES_PER_DAY_PAGE`
    default cap.
    """
    start = time.perf_counter()
    logger.info(
        "history_cache_bypass user_id=%s filters=%s date_filters=%s logging_style_override=%s",
        user.id,
        filters or {},
        date_filters or {},
        logging_style_override,
    )
    # Deferred import: build_history_days still lives in history_cache.py
    from app.history_cache import build_history_days

    history_days = build_history_days(
        user,
        filters=filters,
        date_filters=date_filters,
        logging_style_override=logging_style_override,
    )
    if cap_entries_per_day:
        entry_cap = (
            max_entries_per_day
            if max_entries_per_day is not None
            else HISTORY_ENTRIES_PER_DAY_PAGE
        )
        apply_history_entry_cap(history_days, entry_cap)
    logger.info(
        "history_cache_bypass_done user_id=%s days=%s elapsed_ms=%.2f",
        user.id,
        len(history_days),
        (time.perf_counter() - start) * 1000,
    )
    return history_days


def _window_history_day(
    day,
    entry_offset=0,
    max_entries=None,
    media_types=None,
    entry_filter=None,
):
    """Copy only one entry window while retaining full-day summary metadata."""
    if not day:
        return day
    entry_offset = max(int(entry_offset or 0), 0)
    stop = None if max_entries is None else entry_offset + max(int(max_entries), 0)
    entries = []
    entry_count = 0
    total_minutes = 0
    filtering = media_types is not None or entry_filter is not None
    for entry in day.get("entries", []):
        if media_types is not None and entry.get("media_type") not in media_types:
            continue
        if entry_filter is not None and not entry_filter(entry):
            continue
        if filtering:
            total_minutes += entry.get("runtime_minutes") or 0
        if entry_count >= entry_offset and (stop is None or entry_count < stop):
            entries.append(entry)
        entry_count += 1
    result = dict(day)
    result.update(
        {
            "entries": entries,
            "entry_count": entry_count,
            "entries_truncated": len(entries) < entry_count,
            "_entry_window_offset": entry_offset,
            "_entries_filtered": filtering,
        }
    )
    if filtering:
        result["total_minutes"] = total_minutes
        result["total_runtime_display"] = (
            helpers.minutes_to_hhmm(total_minutes) if total_minutes else "0min"
        )
    return result


def _day_keys_within(day_keys, date_filters):
    """Drop the day keys outside an inclusive start_date/end_date range."""
    if not date_filters:
        return day_keys
    start = parse_date(date_filters.get("start_date") or "")
    end = parse_date(date_filters.get("end_date") or "")
    if start is None and end is None:
        return day_keys
    kept = []
    for day_key in day_keys:
        try:
            day = _date_from_day_key(day_key)
        except (TypeError, ValueError):
            continue
        if (start is None or day >= start) and (end is None or day <= end):
            kept.append(day_key)
    return kept


def get_cached_history_window(
    user,
    limit,
    offset,
    filters=None,
    logging_style_override=None,
    max_entries_per_day=None,
    date_filters=None,
    allowed_media_types=None,
):
    """Read one page of days from indexed/day-cached history payloads.

    `allowed_media_types` narrows the entry types further (the viewer's
    enabled media types), intersected with any `media_type` filter.

    `date_filters` are applied to the day index rather than to the builders.
    start_date/end_date are whole-day bounds, so a date range only ever drops
    whole days -- it never changes what a day contains -- which is what lets a
    date-filtered request page the index instead of rebuilding every matching
    entry to throw almost all of them away.
    """
    filters = filters or {}
    entry_cap = (
        max_entries_per_day
        if max_entries_per_day is not None
        else HISTORY_ENTRIES_PER_DAY_PAGE
    )
    unsupported_filters = set(filters) - {"media_type"}
    if unsupported_filters:
        raise ValueError(
            "Cached history only supports media_type filters: "
            + ", ".join(sorted(unsupported_filters)),
        )

    logging_style = _normalize_logging_style(logging_style_override, user)
    requested_media_types = expand_history_media_types(filters.get("media_type"))
    if allowed_media_types is not None:
        requested_media_types = (
            set(allowed_media_types)
            if requested_media_types is None
            else requested_media_types & set(allowed_media_types)
        )
    cache_entry = cache.get(_cache_key(user.id, logging_style))
    if requested_media_types is not None:
        era = _current_history_era(user.id, logging_style)
        typed_cache_key = _typed_history_index_key(
            user.id,
            logging_style,
            requested_media_types,
            era,
        )
        typed_cache_entry = cache.get(typed_cache_key)
        if (
            typed_cache_entry
            and typed_cache_entry.get("era") == era
            and typed_cache_entry.get("built_at")
            and timezone.now() - typed_cache_entry["built_at"] <= HISTORY_STALE_AFTER
        ):
            index_days = typed_cache_entry.get("days", [])
        else:
            # Capture the era before reading rows: if invalidation retires
            # it mid-build, this publish lands in an unreachable namespace
            # and the next reader rebuilds — a stale index can never become
            # the authoritative current one.
            era = _current_history_era(user.id, logging_style)
            index_days = build_history_index(
                user,
                logging_style_override=logging_style,
                media_types=requested_media_types,
            )
            cache_history_index(
                user.id,
                logging_style,
                index_days,
                media_types=requested_media_types,
                era=era,
            )
    elif cache_entry:
        index_days = cache_entry.get("days", [])
        built_at = cache_entry.get("built_at")
        if (
            (built_at and timezone.now() - built_at > HISTORY_STALE_AFTER)
            or cache_entry.get("era") != _current_history_era(user.id, logging_style)
        ) and _clean_refresh_lock(_refresh_lock_key(user.id, logging_style)) is None:
            schedule_history_refresh(user.id, logging_style, warm_days=0)
    else:
        era = _current_history_era(user.id, logging_style)
        index_days = build_history_index(
            user,
            logging_style_override=logging_style,
        )
        cache_history_index(user.id, logging_style, index_days, era=era)

    normalized_day_keys = [
        day_key
        for day_key in (_day_key_from_value(value) for value in index_days)
        if day_key
    ]
    normalized_day_keys = _day_keys_within(normalized_day_keys, date_filters)
    total_days = len(normalized_day_keys)
    page_day_keys = normalized_day_keys[offset : offset + limit]
    payload_keys = [
        _day_cache_key(user.id, logging_style, day_key)
        for day_key in page_day_keys
    ]
    payloads = cache.get_many(payload_keys) if payload_keys else {}
    cache_hits = len(payloads)
    history_days = []
    missing_day_keys = []
    for day_key in page_day_keys:
        payload = payloads.pop(_day_cache_key(user.id, logging_style, day_key), None)
        if payload is None:
            missing_day_keys.append(day_key)
            continue
        day_payload = _deserialize_history_day(
            payload,
            max_entries=entry_cap,
            media_types=requested_media_types,
        )
        if requested_media_types is not None:
            if not day_payload["entry_count"]:
                missing_day_keys.append(day_key)
                continue
            total_minutes = day_payload["total_minutes"]
            day_payload["total_runtime_display"] = (
                helpers.minutes_to_hhmm(total_minutes) if total_minutes else "0min"
            )
        history_days.append(day_payload)

    # The viewer's enabled types alone only narrow what is shown, so build the
    # complete day (and cache it for the next request) and window it below.
    # Only an explicit media_type filter is worth filtering at the query.
    build_media_types = (
        requested_media_types if filters.get("media_type") else None
    )
    # Same span-wide load the month repair uses: one query per media family
    # instead of a query bundle per missing day. A type-filtered request needs
    # it as much as an unfiltered one, or every page of a typed scan rebuilds
    # its days one by one (#1873).
    day_media_types, prefetch = _span_prefetch(user, missing_day_keys, logging_style)
    if build_media_types is not None:
        day_media_types = {}
    for day_key in missing_day_keys:
        # Filter cold builds at the query boundary; only complete days may
        # populate the shared day cache.
        day_payload = build_history_day(
            user,
            day_key,
            media_types=day_media_types.get(day_key, build_media_types),
            logging_style_override=logging_style,
            prefetch=prefetch,
        )
        if day_payload is None:
            continue
        if build_media_types is None:
            cache.set(
                _day_cache_key(user.id, logging_style, day_key),
                _serialize_history_day(day_payload),
                timeout=HISTORY_DAY_CACHE_TIMEOUT,
            )
        windowed = _window_history_day(
            day_payload,
            max_entries=entry_cap,
            media_types=requested_media_types,
        )
        if requested_media_types is not None and not windowed["entry_count"]:
            continue
        history_days.append(windowed)

    history_days.sort(key=lambda day: day.get("date") or date.min, reverse=True)

    # `limit`/`offset` here only bound the number of DAYS returned, not the
    # entries within a day — cap those separately or a single busy day could
    # blow up the response to megabytes even for `limit=1`.
    total_entries = apply_history_entry_cap(history_days, entry_cap)
    for day_payload in history_days:
        day_payload.pop("_entry_window_offset", None)
        day_payload.pop("_entries_filtered", None)

    logger.info(
        "history_cached_window user_id=%s logging_style=%s filters=%s indexed=%s offset=%s limit=%s cached=%s missing=%s returned=%s entries=%s",
        user.id,
        logging_style,
        filters,
        total_days,
        offset,
        limit,
        cache_hits,
        len(missing_day_keys),
        len(history_days),
        total_entries,
    )
    return history_days, total_days


def get_cached_history_page(user, page_number: int = 1, logging_style_override=None):
    """Return a cached history page, total day count, and refresh metadata."""
    start = time.perf_counter()
    logging_style = _normalize_logging_style(logging_style_override, user)
    cache_key = _cache_key(user.id, logging_style)
    lock_key = _refresh_lock_key(user.id, logging_style)
    meta = {"refreshing": False, "refresh_reason": None}

    refresh_lock = _clean_refresh_lock(lock_key)
    lock_age_s = None
    if isinstance(refresh_lock, dict):
        started_at = refresh_lock.get("started_at")
        if started_at:
            lock_age_s = (timezone.now() - started_at).total_seconds()

    cache_entry = cache.get(cache_key)
    logger.info(
        "history_index_lookup user_id=%s cache_key=%s hit=%s lock=%s lock_age_s=%s",
        user.id,
        cache_key,
        cache_entry is not None,
        refresh_lock is not None,
        lock_age_s,
    )

    if not cache_entry:
        if refresh_lock is not None:
            logger.info(
                "history_index_miss_refreshing user_id=%s logging_style=%s returning_empty=true",
                user.id,
                logging_style,
            )
            meta.update({"refreshing": True, "refresh_reason": "index_refreshing"})
            return [], 0, meta
        scheduled = schedule_history_refresh(
            user.id,
            logging_style,
            warm_days=HISTORY_COLD_MISS_WARM_DAYS,
            allow_inline=False,
        )
        logger.info(
            "history_index_miss user_id=%s logging_style=%s scheduled=%s returning_empty=true",
            user.id,
            logging_style,
            scheduled,
        )
        meta.update({"refreshing": True, "refresh_reason": "index_miss"})
        return [], 0, meta

    index_days = cache_entry.get("days", [])
    built_at = cache_entry.get("built_at")
    cache_age_s = None
    if built_at:
        cache_age_s = (timezone.now() - built_at).total_seconds()
    if (
        built_at and timezone.now() - built_at > HISTORY_STALE_AFTER
    ) or cache_entry.get("era") != _current_history_era(user.id, logging_style):
        refresh_lock = _clean_refresh_lock(lock_key)
        if refresh_lock is None:
            scheduled = schedule_history_refresh(user.id, logging_style, warm_days=0)
            logger.info(
                "history_index_stale_refresh user_id=%s logging_style=%s scheduled=%s cache_age_s=%s",
                user.id,
                logging_style,
                scheduled,
                cache_age_s,
            )

    total_days = len(index_days)
    if total_days == 0:
        logger.info(
            "history_index_hit user_id=%s logging_style=%s days=0 cache_age_s=%s",
            user.id,
            logging_style,
            cache_age_s,
        )
        return [], 0, meta

    try:
        page_number = int(page_number)
    except (TypeError, ValueError):
        page_number = 1
    page_number = max(page_number, 1)

    start_index = (page_number - 1) * HISTORY_DAYS_PER_PAGE
    end_index = start_index + HISTORY_DAYS_PER_PAGE
    page_day_keys = index_days[start_index:end_index]
    logger.info(
        "history_page_days user_id=%s logging_style=%s page=%s days_per_page=%s needed=%s",
        user.id,
        logging_style,
        page_number,
        HISTORY_DAYS_PER_PAGE,
        len(page_day_keys),
    )

    day_cache_keys = [
        _day_cache_key(user.id, logging_style, day_key) for day_key in page_day_keys
    ]
    day_payloads = cache.get_many(day_cache_keys)
    logger.info(
        "history_day_cache_get_many user_id=%s logging_style=%s requested=%s hit=%s miss=%s",
        user.id,
        logging_style,
        len(page_day_keys),
        len(day_payloads),
        max(len(page_day_keys) - len(day_payloads), 0),
    )
    history_days = []
    missing_days = []
    cached_days = {}
    cache_hits = len(day_payloads)
    for day_key in page_day_keys:
        payload_key = _day_cache_key(user.id, logging_style, day_key)
        payload = day_payloads.pop(payload_key, None)
        if payload is None:
            missing_days.append(day_key)
            continue
        cached_days[day_key] = _deserialize_history_day(
            payload,
            max_entries=HISTORY_ENTRIES_PER_DAY_PAGE,
        )
        history_days.append(cached_days[day_key])

    if missing_days and cache_hits == 0:
        refresh_lock = _clean_refresh_lock(lock_key)
        scheduled = False
        if refresh_lock is None:
            scheduled = schedule_history_refresh(
                user.id,
                logging_style,
                day_keys=missing_days,
                allow_inline=False,
            )
            logger.info(
                "history_day_cache_cold_miss user_id=%s logging_style=%s missing=%s scheduled=%s returning_empty=true",
                user.id,
                logging_style,
                len(missing_days),
                scheduled,
            )
        else:
            logger.info(
                "history_day_cache_cold_miss_refreshing user_id=%s logging_style=%s missing=%s",
                user.id,
                logging_style,
                len(missing_days),
            )
        refreshing = refresh_lock is not None or scheduled
        meta.update({"refreshing": refreshing, "refresh_reason": "day_cache_cold_miss"})
        return [], total_days, meta

    built_days = {}
    if missing_days:
        build_start = time.perf_counter()
        for day_key in missing_days:
            day_payload = build_history_day(
                user, day_key, logging_style_override=logging_style
            )
            if day_payload:
                built_days[day_key] = _window_history_day(
                    day_payload,
                    max_entries=HISTORY_ENTRIES_PER_DAY_PAGE,
                )
                cache.set(
                    _day_cache_key(user.id, logging_style, day_key),
                    _serialize_history_day(day_payload),
                    timeout=HISTORY_DAY_CACHE_TIMEOUT,
                )
            else:
                day_date = _date_from_day_key(day_key)
                if day_date:
                    empty_day = {
                        "date": day_date,
                        "weekday": formats.date_format(day_date, "l"),
                        "date_display": formats.date_format(day_date, "F j, Y"),
                        "entries": [],
                        "total_minutes": 0,
                        "total_runtime_display": "0min",
                    }
                    cache.set(
                        _day_cache_key(user.id, logging_style, day_key),
                        _serialize_history_day(empty_day),
                        timeout=HISTORY_DAY_CACHE_TIMEOUT,
                    )

        if built_days:
            history_days = []
            for day_key in page_day_keys:
                if day_key in cached_days:
                    history_days.append(cached_days[day_key])
                    continue
                day_payload = built_days.get(day_key)
                if day_payload:
                    history_days.append(day_payload)

        if len(built_days) != len(missing_days):
            refresh_lock = _clean_refresh_lock(lock_key)
            if refresh_lock is None:
                scheduled = schedule_history_refresh(
                    user.id, logging_style, warm_days=0
                )
                logger.info(
                    "history_day_cache_miss user_id=%s logging_style=%s missing=%s built=%s scheduled=%s",
                    user.id,
                    logging_style,
                    len(missing_days),
                    len(built_days),
                    scheduled,
                )
            else:
                logger.info(
                    "history_day_cache_miss_refreshing user_id=%s logging_style=%s missing=%s built=%s",
                    user.id,
                    logging_style,
                    len(missing_days),
                    len(built_days),
                )
        else:
            logger.info(
                "history_day_cache_inline_build user_id=%s logging_style=%s built=%s elapsed_ms=%.2f",
                user.id,
                logging_style,
                len(built_days),
                (time.perf_counter() - build_start) * 1000,
            )

    logger.info(
        "history_index_hit user_id=%s logging_style=%s days=%s page_days=%s cache_age_s=%s elapsed_ms=%.2f",
        user.id,
        logging_style,
        total_days,
        len(history_days),
        cache_age_s,
        (time.perf_counter() - start) * 1000,
    )
    return history_days, total_days, meta


def refresh_history_cache(
    user_id: int,
    logging_style: str | None = None,
    warm_days: int | None = None,
    day_keys: Iterable | None = None,
):
    """Rebuild and store history index for a user."""
    user_model = get_user_model()
    try:
        user = user_model.objects.get(id=user_id)
        logging_style = _normalize_logging_style(logging_style, user)
    except user_model.DoesNotExist:
        cache.delete(_refresh_lock_key(user_id, logging_style or "repeats"))
        return None

    try:
        normalized_day_keys = []
        for value in day_keys or []:
            day_key = _day_key_from_value(value)
            if day_key:
                normalized_day_keys.append(day_key)
        use_specific_days = bool(normalized_day_keys)
        if use_specific_days:
            seen = set()
            requested_day_keys = []
            for key in normalized_day_keys:
                if key in seen:
                    continue
                seen.add(key)
                requested_day_keys.append(key)
        else:
            requested_day_keys = None

        requested_warm_days = warm_days
        if warm_days is None:
            warm_days = HISTORY_WARM_DAYS
        dedupe_key = _refresh_dedupe_key(user_id, logging_style, normalized_day_keys)
        rerun_key = _refresh_rerun_key(dedupe_key)
        # A request deduped while this refresh runs leaves a fresh marker.
        # One already here was left before any row was read, so this pass
        # covers it.
        rerun_before = cache.get(rerun_key)
        logger.info(
            "history_cache_refresh_start user_id=%s logging_style=%s day_keys=%s mode=%s",
            user_id,
            logging_style,
            len(requested_day_keys or []),
            "page_days" if use_specific_days else "index",
        )
        # Capture the era before reading rows: if invalidation retires it
        # mid-build, this publish embeds a retired token and readers treat
        # the index as stale (serve it, schedule a rebuild) rather than
        # accepting pre-invalidation rows as fresh.
        era = _current_history_era(user_id, logging_style)
        index_day_keys = build_history_index(
            user,
            logging_style_override=logging_style,
            memo_eras=(
                _current_history_era(user_id, "sessions"),
                _current_history_era(user_id, "repeats"),
            ),
        )
        cache_history_index(user_id, logging_style, index_day_keys, era=era)

        warm_targets = []
        if use_specific_days:
            warm_targets = requested_day_keys or []
        elif index_day_keys:
            missing_day_keys = _missing_history_day_keys(
                user_id, logging_style, index_day_keys
            )
            if missing_day_keys:
                # Bound inline warming so a cold/evicted day-cache can't turn a
                # "cheap" refresh (e.g. warm_days=0) into a full history rebuild.
                # Index order is most-recent-first, so the cap keeps the pages a
                # user is about to view warm; any remainder is backfilled by the
                # existing low-priority coverage-repair task.
                cap = warm_days or 0
                warm_targets = missing_day_keys[:cap]
                covered = set(history_coverage_day_keys(index_day_keys))
                if any(key in covered for key in missing_day_keys[cap:]):
                    schedule_history_day_cache_coverage(user_id, logging_style)
            elif warm_days:
                warm_targets = index_day_keys[:warm_days]
        rebuilt = 0
        populated = 0
        for day_key in warm_targets:
            day_payload = _build_and_cache_history_day(
                user,
                day_key,
                logging_style,
            )
            rebuilt += 1
            if day_payload and day_payload.get("entries"):
                populated += 1
        logger.info(
            "history_cache_refresh_done user_id=%s logging_style=%s days=%s rebuilt=%s populated=%s",
            user_id,
            logging_style,
            len(index_day_keys),
            rebuilt,
            populated,
        )
        _release_refresh_locks(user_id, logging_style, dedupe_key)
        rerun_after = cache.get(rerun_key)
        if rerun_after is not None:
            cache.delete(rerun_key)
            if rerun_after != rerun_before:
                # A change arrived after this pass read its rows.
                rerun = schedule_history_refresh(
                    user_id,
                    logging_style,
                    warm_days=requested_warm_days,
                    day_keys=normalized_day_keys or None,
                    allow_inline=False,
                )
                logger.info(
                    "history_cache_refresh_rerun user_id=%s logging_style=%s "
                    "day_keys=%s scheduled=%s",
                    user_id,
                    logging_style,
                    len(normalized_day_keys),
                    rerun,
                )
    except Exception:
        logger.exception("Error refreshing history cache for user %s", user_id)
        _release_refresh_locks(
            user_id,
            logging_style,
            _refresh_dedupe_key(user_id, logging_style, normalized_day_keys),
        )
        raise
    else:
        return index_day_keys


def repair_history_day_cache_coverage(
    user_id: int,
    logging_style: str = "repeats",
    batch_size: int | None = None,
):
    """Repair missing persisted day payloads for a user's history cache in batches.

    Only the recent window (``history_coverage_day_keys``) of the style the
    user reads is built ahead of a visit; older days and the other style are
    built when a page asks for them.
    """
    user_model = get_user_model()
    try:
        user = user_model.objects.get(id=user_id)
    except user_model.DoesNotExist:
        cache.delete(_coverage_repair_key(user_id, logging_style))
        return {"rebuilt": 0, "remaining": 0, "days": 0}

    logging_style = _normalize_logging_style(logging_style, user)
    if logging_style not in history_coverage_styles(user):
        logger.info(
            "history_day_coverage_repair user_id=%s logging_style=%s rebuilt=0 "
            "remaining=0 skipped=inactive_style",
            user_id,
            logging_style,
        )
        return {"rebuilt": 0, "remaining": 0, "days": 0}
    if batch_size is None:
        batch_size = HISTORY_COVERAGE_REPAIR_BATCH_SIZE

    cache_entry = cache.get(_cache_key(user_id, logging_style))
    if cache_entry:
        index_day_keys = cache_entry.get("days", [])
    else:
        # Capture the era before reading rows: if invalidation retires it
        # mid-build, this publish embeds a retired token and readers treat
        # the index as stale (serve it, schedule a rebuild) rather than
        # accepting pre-invalidation rows as fresh.
        era = _current_history_era(user_id, logging_style)
        index_day_keys = build_history_index(user, logging_style_override=logging_style)
        cache_history_index(user_id, logging_style, index_day_keys, era=era)

    if not index_day_keys:
        return {"rebuilt": 0, "remaining": 0, "days": 0}

    coverage_day_keys = history_coverage_day_keys(index_day_keys)
    missing_day_keys = _missing_history_day_keys(
        user_id, logging_style, coverage_day_keys
    )
    if not missing_day_keys:
        record_history_repair_complete(user_id, logging_style, len(coverage_day_keys))
        return {"rebuilt": 0, "remaining": 0, "days": len(coverage_day_keys)}
    missing_reason, missing_detail = classify_missing_history_days(
        user_id, logging_style, timedelta(seconds=HISTORY_DAY_CACHE_TIMEOUT)
    )

    target_day_keys = (
        missing_day_keys[:batch_size]
        if batch_size and batch_size > 0
        else missing_day_keys
    )
    rebuilt = 0
    populated = 0
    # A batch runs for tens of seconds on a large history; stop between days
    # when someone is browsing so the rebuild does not compete with their
    # page loads. The unbuilt days stay missing and are picked up next run.
    run = CooperativeRun("history_day_coverage_repair")
    for day_key in run.iter(target_day_keys):
        day_payload = _build_and_cache_history_day(user, day_key, logging_style)
        rebuilt += 1
        if day_payload and day_payload.get("entries"):
            populated += 1

    remaining = max(len(missing_day_keys) - rebuilt, 0)
    if not remaining:
        record_history_repair_complete(user_id, logging_style, len(coverage_day_keys))
    logger.info(
        "history_day_coverage_repair user_id=%s logging_style=%s rebuilt=%s populated=%s remaining=%s days=%s missing=%s missing_reason=%s missing_detail=%s",
        user_id,
        logging_style,
        rebuilt,
        populated,
        remaining,
        len(coverage_day_keys),
        len(missing_day_keys),
        missing_reason,
        missing_detail or "-",
    )
    return {
        "rebuilt": rebuilt,
        "remaining": remaining,
        "days": len(coverage_day_keys),
    }
