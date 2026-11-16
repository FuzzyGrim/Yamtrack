"""Utilities for caching the Statistics page."""

import calendar
import hashlib
import heapq
import itertools
import logging
import random
import re
import time
from collections import Counter, defaultdict
from datetime import date, datetime
from types import SimpleNamespace

from dateutil.relativedelta import relativedelta
from django.apps import apps
from django.conf import settings
from django.core.cache import cache
from django.db.models import Max, Min, Q
from django.db.models.functions import ExtractDay, ExtractMonth, TruncDate
from django.utils import timezone

from app import cache_utils, config, helpers, history_cache
from app import credits as credit_helpers
from app import statistics as stats
from app.models import (
    CREDITS_BACKFILL_VERSION,
    CreditRoleType,
    Episode,
    Item,
    ItemPersonCredit,
    ItemStudioCredit,
    MediaTypes,
    Movie,
    Person,
    PersonGender,
    Sources,
    Status,
)
from app.statistics_day_cache import (
    DAY_KEY_LENGTH,
    STATISTICS_DAY_CACHE_TIMEOUT,
    STATISTICS_DAY_CACHE_VERSION,
    STATISTICS_DAY_PREFIX,
    STATISTICS_HISTORY_VERSION_PREFIX,
    _day_cache_key,
    _get_history_version,
    _history_version_key,
    _normalize_day_value,
    _set_history_version,
)
from app.statistics_highlights import (
    _cached_horizontal_backdrop,
    _get_history_day_payload,
    _get_history_index_days,
    _get_horizontal_history_image,
    _get_range_history_boundary_days,
    _get_today_history_entries,
    _get_today_release_entry,
    _history_entry_card_payload,
    _normalize_history_highlight_images,
    _normalize_history_highlights_by_type,
    _select_history_entry_for_day,
    normalize_highlight_images,
)
from app.statistics_talent import (
    STATISTICS_TOP_N,
    STATISTICS_TOP_RATED_OVERALL,
    _aggregate_top_talent,
    _build_person_talent_context,
    _is_director_credit,
    _is_writer_credit,
    _resolve_missing_credit_item_ids,
    _safe_runtime_minutes,
    _tv_episode_play_rows,
)
from app.statistics_talent import (
    get_person_talent_totals as _compute_person_talent_totals,
)
from app.templatetags import app_tags

logger = logging.getLogger(__name__)

STATISTICS_CACHE_VERSION = 19
STATISTICS_CACHE_PREFIX = f"statistics_page_v{STATISTICS_CACHE_VERSION}"
STATISTICS_CACHE_TIMEOUT = 60 * 60 * 6  # 6 hours
# Retain page snapshots between visits; freshness is checked independently.
STATISTICS_RANGE_CACHE_TIMEOUT = 60 * 60 * 24 * 7
SCORE_COMPARISON_EPSILON = 1e-6  # tolerance for float score equality checks

# A custom range is assembled from day payloads on request. Its result is
# remembered under a token that changes whenever the predefined snapshots would
# go stale (the user's change generation, the local day, the schema), so the
# lifetime only bounds memory, never freshness. See statistics-sync.md.
STATISTICS_CUSTOM_RANGE_CACHE_VERSION = 1
STATISTICS_CUSTOM_RANGE_PREFIX = f"stats:custom:v{STATISTICS_CUSTOM_RANGE_CACHE_VERSION}"
STATISTICS_CUSTOM_RANGE_CACHE_TIMEOUT = 60 * 15
# A second tab or a reload of the same range waits for the first build rather
# than computing it again. A full history takes several seconds on a large
# library; past this wait the follower builds it itself.
STATISTICS_CUSTOM_RANGE_BUILD_LOCK_TTL = 90
STATISTICS_CUSTOM_RANGE_BUILD_WAIT = 30.0

# Predefined ranges that can be cached
PREDEFINED_RANGES = [
    "Today",
    "Yesterday",
    "This Week",
    "Last 7 Days",
    "This Month",
    "Last 30 Days",
    "Last 90 Days",
    "This Year",
    "Last 6 Months",
    "Last 12 Months",
    "All Time",
]

_HOURS_DISPLAY_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)h\s+(\d+(?:\.\d+)?)min\s*$")


def _normalize_range_name(range_name: str) -> str:
    """Normalize range name for cache key (e.g., 'All Time' -> 'all_time')."""
    if range_name == "All Time":
        return "all_time"
    # Replace spaces with underscores and convert to lowercase
    return range_name.lower().replace(" ", "_")


def _cache_key(user_id: int, range_name: str) -> str:
    """Generate cache key for statistics data."""
    normalized = _normalize_range_name(range_name)
    return f"{STATISTICS_CACHE_PREFIX}_{user_id}_{normalized}"


def _range_cache_component(value: datetime | date | str | None) -> str:
    if value is None:
        return "none"
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _person_talent_context_cache_key(
    user_id: int,
    history_version: str,
    start_date: datetime | date | None,
    end_date: datetime | date | None,
) -> str:
    return (
        f"{STATISTICS_CACHE_PREFIX}:person_talent_context:{user_id}:"
        f"{history_version}:{CREDITS_BACKFILL_VERSION}:"
        f"{_range_cache_component(start_date)}:{_range_cache_component(end_date)}"
    )


def _person_talent_totals_cache_key(
    user_id: int,
    history_version: str,
    person_source: str,
    person_id: str | int,
    start_date: datetime | date | None,
    end_date: datetime | date | None,
) -> str:
    return (
        f"{STATISTICS_CACHE_PREFIX}:person_talent_totals:{user_id}:"
        f"{history_version}:{CREDITS_BACKFILL_VERSION}:{person_source}:{person_id}:"
        f"{_range_cache_component(start_date)}:{_range_cache_component(end_date)}"
    )


def _encode_person_talent_media_key(media_key) -> str:
    media_type, media_id = media_key
    return f"{media_type}::{media_id}"


def _decode_person_talent_media_key(encoded_key):
    if isinstance(encoded_key, tuple):
        return encoded_key
    media_type, _separator, media_id = encoded_key.partition("::")
    return (media_type, media_id)


def _serialize_person_talent_totals_for_cache(totals):
    if not isinstance(totals, dict):
        return totals

    serialized = dict(totals)
    for field_name in ("minutes_by_media_key", "plays_by_media_key"):
        raw_map = serialized.get(field_name)
        if not isinstance(raw_map, dict):
            continue
        serialized[field_name] = {
            _encode_person_talent_media_key(media_key): value
            for media_key, value in raw_map.items()
        }
    return serialized


def _deserialize_person_talent_totals_from_cache(totals):
    if not isinstance(totals, dict):
        return totals

    deserialized = dict(totals)
    for field_name in ("minutes_by_media_key", "plays_by_media_key"):
        raw_map = deserialized.get(field_name)
        if not isinstance(raw_map, dict):
            continue
        deserialized[field_name] = {
            _decode_person_talent_media_key(encoded_key): value
            for encoded_key, value in raw_map.items()
        }
    return deserialized


def is_statistics_cache_stale(cache_entry, user_id: int) -> bool:
    """Whether a published range trails the user's changes or today's date."""
    from app import statistics_sync

    return statistics_sync.entry_is_stale(cache_entry, user_id=user_id)


def _normalize_hours_display(value):
    if not isinstance(value, str):
        return value
    if "play" in value:
        return value
    match = _HOURS_DISPLAY_RE.match(value)
    if not match:
        return value
    try:
        hours = float(match.group(1))
        minutes = float(match.group(2))
    except ValueError:
        return value
    total_minutes = (hours * 60) + minutes
    return stats._format_hours_minutes(total_minutes)


def _normalize_hours_per_media_type(hours_per_media_type):
    if not isinstance(hours_per_media_type, dict):
        return hours_per_media_type
    for media_type, value in hours_per_media_type.items():
        hours_per_media_type[media_type] = _normalize_hours_display(value)
    return hours_per_media_type


def get_history_version(user_id: int) -> str:
    """Public accessor for the per-user history version token.

    Any tracked-media change (and the statistics refresh button) bumps this
    value, so cache keys embedding it self-invalidate without extra wiring.
    """
    return _get_history_version(user_id)


def invalidate_statistics_days(
    user_id: int, day_values, reason: str | None = None
) -> None:
    """Mark days dirty; the background sync rebuilds them and every range."""
    from app import statistics_sync

    statistics_sync.mark_days(user_id, day_values, reason=reason)


def _collect_stale_reading_score_days(
    user, day_whitelist: set[date] | None = None
) -> set[date]:
    """Return reading activity days where cached score metadata is stale."""
    active_media_types = set(getattr(user, "get_active_media_types", list)())
    if not active_media_types:
        active_media_types = set(MediaTypes.values)

    expected_by_day: dict[date, list[tuple[str, int, float]]] = defaultdict(list)
    for media_type in (
        MediaTypes.ANIME.value,
        MediaTypes.BOOK.value,
        MediaTypes.COMIC.value,
        MediaTypes.MANGA.value,
    ):
        if media_type not in active_media_types:
            continue
        model = apps.get_model("app", media_type)
        rows = (
            model.objects.filter(
                user=user,
                score__isnull=False,
            )
            .values(
                "item_id",
                "score",
                "start_date",
                "end_date",
                "created_at",
            )
            .iterator(chunk_size=500)
        )
        for row in rows:
            item_id = row.get("item_id")
            if not item_id:
                continue
            activity_key = history_cache.history_day_key(
                row.get("end_date") or row.get("start_date") or row.get("created_at"),
            )
            day = _normalize_day_value(activity_key)
            if not day:
                continue
            if day_whitelist is not None and day not in day_whitelist:
                continue
            score = row.get("score")
            try:
                expected_score = float(score)
            except (TypeError, ValueError):
                continue
            expected_by_day[day].append((media_type, int(item_id), expected_score))

    if not expected_by_day:
        return set()

    key_map = {day: _day_cache_key(user.id, day) for day in expected_by_day}
    cached_payloads = cache.get_many(key_map.values())

    stale_days = set()
    for day, expected_entries in expected_by_day.items():
        day_payload = cached_payloads.get(key_map[day])
        if not isinstance(day_payload, dict):
            stale_days.add(day)
            continue
        items_payload = day_payload.get("items") or {}
        for media_type, item_id, expected_score in expected_entries:
            media_items = items_payload.get(media_type) or {}
            item_meta = media_items.get(str(item_id))
            if item_meta is None:
                item_meta = media_items.get(item_id)
            if not isinstance(item_meta, dict):
                stale_days.add(day)
                break
            cached_score = item_meta.get("score")
            if cached_score is None:
                stale_days.add(day)
                break
            try:
                cached_score_value = float(cached_score)
            except (TypeError, ValueError):
                stale_days.add(day)
                break
            if abs(cached_score_value - expected_score) > SCORE_COMPARISON_EPSILON:
                stale_days.add(day)
                break

    return stale_days


def build_statistics_data(user, start_date, end_date):
    """Build statistics data for a user and date range.

    This extracts the computation logic from the statistics() view.
    Returns a dictionary with all statistics data needed for the view.
    """
    # Get all user media data in a single operation
    user_media, media_count = stats.get_user_media(
        user,
        start_date,
        end_date,
    )

    # Handle season_enabled preference
    if not user.season_enabled:
        season_key = MediaTypes.SEASON.value
        season_count = media_count.pop(season_key, 0)
        if season_count:
            media_count["total"] = max(media_count.get("total", 0) - season_count, 0)
        user_media.pop(season_key, None)

    # Calculate minutes per media type (used by multiple stats)
    minutes_per_media_type = stats.calculate_minutes_per_media_type(
        user_media,
        start_date,
        end_date,
        user=user,
    )
    # Calculate all statistics from the retrieved data
    media_type_distribution = stats.get_media_type_distribution(
        media_count,
        minutes_per_media_type,
    )
    score_distribution, top_rated, top_rated_by_type = stats.get_score_distribution(
        user_media
    )
    status_distribution = stats.get_status_distribution(user_media)
    status_pie_chart_data = stats.get_status_pie_chart_data(
        status_distribution,
    )
    top_played = stats.get_top_played_media(user_media, start_date, end_date)
    top_talent = _aggregate_top_talent(user, start_date, end_date)

    # Calculate hours and detailed consumption summaries
    hours_per_media_type = stats.get_hours_per_media_type(
        user_media,
        start_date,
        end_date,
        minutes_per_media_type,
        duration_format=user.duration_format,
    )
    tv_consumption = stats.get_tv_consumption_stats(
        user_media,
        start_date,
        end_date,
        minutes_per_media_type,
    )
    movie_consumption = stats.get_movie_consumption_stats(
        user_media,
        start_date,
        end_date,
        minutes_per_media_type,
    )
    anime_consumption = stats.get_anime_consumption_stats(
        user_media,
        start_date,
        end_date,
        minutes_per_media_type,
    )
    music_consumption = stats.get_music_consumption_stats(
        user_media,
        start_date,
        end_date,
        minutes_per_media_type,
    )
    podcast_consumption = stats.get_podcast_consumption_stats(
        user_media,
        start_date,
        end_date,
        minutes_per_media_type,
        user=user,
    )
    game_consumption = stats.get_game_consumption_stats(
        user_media,
        start_date,
        end_date,
        minutes_per_media_type,
        user=user,
    )
    book_consumption = stats.get_reading_consumption_stats(
        user_media,
        start_date,
        end_date,
        MediaTypes.BOOK.value,
    )
    comic_consumption = stats.get_reading_consumption_stats(
        user_media,
        start_date,
        end_date,
        MediaTypes.COMIC.value,
    )
    manga_consumption = stats.get_reading_consumption_stats(
        user_media,
        start_date,
        end_date,
        MediaTypes.MANGA.value,
    )

    # Daily hours per media type (used by the Activity History-attached chart)
    daily_hours_by_media_type = stats.get_daily_hours_by_media_type(
        user_media,
        start_date,
        end_date,
    )

    activity_data = stats.get_activity_data(
        user, start_date, end_date, daily_hours_data=daily_hours_by_media_type
    )

    return {
        "media_count": media_count,
        "activity_data": activity_data,
        "media_type_distribution": media_type_distribution,
        "score_distribution": score_distribution,
        "top_rated": top_rated,
        "top_rated_by_type": top_rated_by_type,
        "top_played": top_played,
        "top_talent": top_talent,
        "status_distribution": status_distribution,
        "status_pie_chart_data": status_pie_chart_data,
        "minutes_per_media_type": minutes_per_media_type,
        "hours_per_media_type": hours_per_media_type,
        "tv_consumption": tv_consumption,
        "movie_consumption": movie_consumption,
        "anime_consumption": anime_consumption,
        "music_consumption": music_consumption,
        "podcast_consumption": podcast_consumption,
        "game_consumption": game_consumption,
        "book_consumption": book_consumption,
        "comic_consumption": comic_consumption,
        "manga_consumption": manga_consumption,
        "daily_hours_by_media_type": daily_hours_by_media_type,
    }


def _get_empty_statistics_data():
    """Return an empty statistics data structure with all required keys.

    Used when cache is missing and refresh is in progress to avoid
    expensive inline rebuilds that cause page load delays.
    """
    return {
        "media_count": {"total": 0},
        "activity_data": [],
        "media_type_distribution": {},
        "score_distribution": {},
        "top_rated": [],
        "top_rated_by_type": {},
        "top_played": [],
        "top_talent": {
            "sort_by": "plays",
            "by_sort": {
                "plays": {
                    "top_actors": [],
                    "top_actresses": [],
                    "top_directors": [],
                    "top_writers": [],
                    "top_studios": [],
                },
                "time": {
                    "top_actors": [],
                    "top_actresses": [],
                    "top_directors": [],
                    "top_writers": [],
                    "top_studios": [],
                },
                "titles": {
                    "top_actors": [],
                    "top_actresses": [],
                    "top_directors": [],
                    "top_writers": [],
                    "top_studios": [],
                },
            },
            "top_actors": [],
            "top_actresses": [],
            "top_directors": [],
            "top_writers": [],
            "top_studios": [],
        },
        "status_distribution": {},
        "status_pie_chart_data": {},
        "minutes_per_media_type": {},
        "hours_per_media_type": {},
        "tv_consumption": {},
        "movie_consumption": {},
        "anime_consumption": {},
        "music_consumption": {},
        "podcast_consumption": {},
        "video_consumption": {},
        "game_consumption": {
            "hours": {
                "total": 0,
                "per_year": 0,
                "per_month": 0,
                "per_day": 0,
            },
            "charts": {
                "by_year": {"labels": [], "datasets": []},
                "by_month": {"labels": [], "datasets": []},
                "by_daily_average": {
                    "labels": [],
                    "datasets": [],
                    "top_games_per_band": {},
                },
            },
            "has_data": False,
            "top_genres": [],
            "top_daily_average_games": [],
            "platform_breakdown": [],
        },
        "book_consumption": {},
        "comic_consumption": {},
        "manga_consumption": {},
        "daily_hours_by_media_type": {},
        "history_highlights": {
            "first_play": None,
            "last_play": None,
            "today_in_history": None,
            "today_in_user_history": None,
            "today_in_history_year": None,
            "today_in_user_history_year": None,
            "today_month": None,
            "today_day": None,
        },
        "history_highlights_by_type": {},
        "summary_stats_by_type": {},
        "consumption_stats_by_type": {},
    }


def cache_statistics_data(
    user_id: int, range_name: str, data: dict, history_version: str | None = None
):
    """Publish a range payload at the user's current generation."""
    from app import statistics_sync

    statistics_sync.publish_snapshot(
        user_id, range_name, data, statistics_sync.current_generation(user_id)
    )


def _top_talent_bucket_has_game_counts(bucket: dict) -> bool:
    for entries_key in (
        "top_actors",
        "top_actresses",
        "top_directors",
        "top_writers",
        "top_studios",
    ):
        entries = bucket.get(entries_key)
        if entries:
            return "unique_games" in entries[0]
    return True


def range_needs_top_talent_upgrade(user_id: int, range_name: str) -> bool:
    """Return True when cached top_talent payload is missing the current shape."""
    if range_name not in PREDEFINED_RANGES:
        return False

    return entry_needs_top_talent_upgrade(cache.get(_cache_key(user_id, range_name)))


def entry_needs_top_talent_upgrade(cache_entry) -> bool:
    """Return True when a loaded range entry's top_talent lacks the current shape."""
    if not isinstance(cache_entry, dict):
        return False

    data = cache_entry.get("data")
    if not isinstance(data, dict):
        return True

    top_talent = data.get("top_talent")
    if not isinstance(top_talent, dict):
        return True

    by_sort = top_talent.get("by_sort")
    if not isinstance(by_sort, dict):
        return True

    for mode in ("plays", "time", "titles"):
        bucket = by_sort.get(mode)
        if not isinstance(bucket, dict):
            return True
        if not _top_talent_bucket_has_game_counts(bucket):
            return True

    return False


def _published_entry(user_id: int, range_name: str):
    """Return the published entry for a range, queueing a sync if it is stale.

    Never builds on the request path and never reports progress: the page
    shows the last published numbers while the background sync catches up.
    """
    from app import statistics_sync

    entry = statistics_sync.load_snapshot(user_id, range_name)
    if entry is None or statistics_sync.entry_is_stale(entry, user_id=user_id):
        statistics_sync.ensure_sync(user_id, urgent=entry is None)
    return entry


def get_top_talent_data(user, start_date, end_date, range_name=None):
    """Return top_talent payload without rebuilding the full statistics page payload."""
    if range_name in PREDEFINED_RANGES:
        cache_entry = _published_entry(user.id, range_name)
        if isinstance(cache_entry, dict):
            data = cache_entry.get("data") or {}
            top_talent = data.get("top_talent")
            if (
                isinstance(top_talent, dict)
                and isinstance(top_talent.get("by_sort"), dict)
                and not entry_needs_top_talent_upgrade(cache_entry)
            ):
                return top_talent

    return _aggregate_top_talent(user, start_date, end_date)


def get_statistics_media_count(user, start_date, end_date, range_name=None):
    """Return media counts without rebuilding the full statistics payload."""
    if range_name in PREDEFINED_RANGES:
        cache_entry = _published_entry(user.id, range_name)
        if isinstance(cache_entry, dict):
            data = cache_entry.get("data") or {}
            media_count = data.get("media_count")
            if isinstance(media_count, dict):
                return media_count

    _user_media, media_count = stats.get_user_media(user, start_date, end_date)
    return media_count or {"total": 0}


def get_person_talent_context(user, start_date=None, end_date=None):
    """Return cached watched-item context for per-person statistics lookups."""
    if not user:
        return None

    history_version = _get_history_version(user.id)
    cache_key = _person_talent_context_cache_key(
        user.id,
        history_version,
        start_date,
        end_date,
    )
    cached_context = cache.get(cache_key)
    if cached_context is not None:
        return cached_context or None

    context = _build_person_talent_context(user, start_date, end_date)
    # An empty library stores {} so the next read is a hit, not another scan.
    cache.set(cache_key, context or {}, timeout=STATISTICS_CACHE_TIMEOUT)
    cache.set(
        _person_talent_latest_key(user.id, start_date, end_date),
        history_version,
        timeout=STATISTICS_CACHE_TIMEOUT,
    )
    return context


# Building the watched-item context scans every play in the library. On a
# large library on spinning disks that took 30 to 200 s, so pages never build
# it; they queue this build once per history version and read what is stored.
PERSON_TALENT_BUILD_GUARD_TTL = 10 * 60


def _person_talent_latest_key(user_id, start_date, end_date) -> str:
    return (
        f"{STATISTICS_CACHE_PREFIX}:person_talent_latest:{user_id}:"
        f"{CREDITS_BACKFILL_VERSION}:"
        f"{_range_cache_component(start_date)}:{_range_cache_component(end_date)}"
    )


def schedule_person_talent_context_build(user_id: int, history_version: str) -> bool:
    """Queue one background build of the all-time person talent context."""
    guard_key = (
        f"{STATISTICS_CACHE_PREFIX}:person_talent_build:{user_id}:{history_version}"
    )
    if not cache.add(guard_key, True, PERSON_TALENT_BUILD_GUARD_TTL):
        return False
    from app.tasks_interactive import build_person_talent_context_task

    try:
        build_person_talent_context_task.delay(user_id)
    except Exception as error:  # the page already shows what is stored
        cache.delete(guard_key)
        logger.warning(
            "person_talent_build_enqueue_failed user_id=%s error=%s",
            user_id,
            type(error).__name__,
        )
        return False
    return True


def get_ready_person_talent_totals(user, person_source, person_id):
    """Return ``(totals, pending)`` for a person page from stored data only.

    Never scans the library on the request path. When the context for the
    current history version is missing, the last built one is used (the page
    shows slightly older numbers, as Statistics does) and a rebuild is queued.
    With nothing built yet, ``pending`` is True and the page fills in later.
    """
    if not user or not person_source or person_id is None:
        return None, False

    history_version = _get_history_version(user.id)

    def totals_key(version):
        return _person_talent_totals_cache_key(
            user.id, version, person_source, person_id, None, None
        )

    cached_totals = cache.get(totals_key(history_version))
    if cached_totals is not None:
        return _deserialize_person_talent_totals_from_cache(cached_totals), False

    context_version = history_version
    context = cache.get(
        _person_talent_context_cache_key(user.id, history_version, None, None)
    )
    if context is None and _eager_statistics_mode():
        # No worker in eager/test mode: build inline, as the Statistics read
        # path does (docs/architecture/statistics-sync.md).
        context = get_person_talent_context(user) or {}
    if context is None:
        schedule_person_talent_context_build(user.id, history_version)
        latest_version = cache.get(_person_talent_latest_key(user.id, None, None))
        if not latest_version or latest_version == history_version:
            return None, True
        context_version = latest_version
        stale_totals = cache.get(totals_key(latest_version))
        if stale_totals is not None:
            return _deserialize_person_talent_totals_from_cache(stale_totals), False
        context = cache.get(
            _person_talent_context_cache_key(user.id, latest_version, None, None)
        )
        if context is None:
            return None, True

    if not context:  # built, and the library has no plays
        return None, False
    totals = _compute_person_talent_totals(
        user, person_source, person_id, context=context
    )
    if totals is not None:
        cache.set(
            totals_key(context_version),
            _serialize_person_talent_totals_for_cache(totals),
            timeout=STATISTICS_CACHE_TIMEOUT,
        )
    return totals, False


def get_person_talent_totals(
    user, person_source, person_id, start_date=None, end_date=None
):
    """Return cached per-person talent totals for the watched range."""
    if not user or not person_source or person_id is None:
        return None

    history_version = _get_history_version(user.id)
    cache_key = _person_talent_totals_cache_key(
        user.id,
        history_version,
        person_source,
        person_id,
        start_date,
        end_date,
    )
    cached_totals = cache.get(cache_key)
    if cached_totals is not None:
        return _deserialize_person_talent_totals_from_cache(cached_totals)

    context = get_person_talent_context(user, start_date, end_date)
    if not context:  # no plays in range; never rebuild it a second time
        return None
    totals = _compute_person_talent_totals(
        user,
        person_source,
        person_id,
        start_date,
        end_date,
        context=context,
    )
    cache.set(
        cache_key,
        _serialize_person_talent_totals_for_cache(totals),
        timeout=STATISTICS_CACHE_TIMEOUT,
    )
    return totals


def _eager_statistics_mode() -> bool:
    return bool(
        getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False)
        or getattr(settings, "TESTING", False),
    )


def _schedule_missing_day_builds(user, missing_days, start_date, end_date) -> None:
    """Queue a background build of missing per-day stat caches.

    Request paths must never build day caches inline (a cold multi-year
    range means thousands of queries and a multi-second block); instead
    the missing days are built in Celery and the page self-corrects on a
    later visit. ``missing_days`` comes from the read that walked the range,
    so the range is not fetched and decoded a second time to find them.
    """
    if not missing_days:
        return

    start_token = start_date.isoformat() if start_date else "all"
    end_token = end_date.isoformat() if end_date else "all"
    guard_key = f"stats:daybuild:{user.id}:{start_token}:{end_token}"
    if not cache.add(guard_key, True, 60 * 5):
        return

    try:
        from app.tasks import build_statistics_days_task

        build_statistics_days_task.apply_async(
            args=[user.id, start_token, end_token],
            priority=getattr(settings, "CELERY_TASK_PRIORITY_FOLLOWUP", 3),
        )
    except Exception:  # pragma: no cover - Celery not available
        cache.delete(guard_key)
        logger.debug(
            "Could not schedule statistics day build for user %s",
            user.id,
            exc_info=True,
        )


def _custom_range_token(user, start_date, end_date) -> str:
    """Return what a remembered custom range must match to be served.

    The same things make a predefined snapshot stale: the user's change
    generation (every mark bumps it and records its time), the local day and
    the snapshot schema.
    The dates carry their UTC offset, and the timezone name and the user
    preferences the aggregate reads are included so a change to either can
    never be served from an entry built before it.
    """
    from app import statistics_sync
    from app.models import StatisticsSyncState

    state = (
        StatisticsSyncState.objects.filter(user_id=user.id)
        .values_list("generation", "last_marked_at")
        .first()
    ) or (0, None)
    parts = (
        statistics_sync.SNAPSHOT_SCHEMA_VERSION,
        STATISTICS_DAY_CACHE_VERSION,
        # The mark time and the account's creation time keep a generation
        # number that comes round again (a restored database, a re-created
        # user id) from matching an entry built before it.
        *state,
        getattr(user, "date_joined", None),
        timezone.localdate().isoformat(),
        timezone.get_current_timezone_name(),
        start_date.isoformat() if start_date else "all",
        end_date.isoformat() if end_date else "all",
        tuple(getattr(user, "get_active_media_types", list)()),
        getattr(user, "season_enabled", None),
        getattr(user, "rating_scale_max", None),
        getattr(user, "top_talent_sort_by", None),
        getattr(user, "week_start_day", None),
        getattr(user, "duration_format", None),
    )
    return hashlib.sha256(repr(parts).encode()).hexdigest()[:32]


def _custom_range_key(kind, user_id, token) -> str:
    return f"{STATISTICS_CUSTOM_RANGE_PREFIX}:{kind}:{user_id}:{token}"


def _custom_range_slot_key(kind, user_id) -> str:
    return f"{STATISTICS_CUSTOM_RANGE_PREFIX}:{kind}:{user_id}:current"


def _remember_custom_range(kind, user, key, value, missing_days) -> None:
    """Keep a custom range's result, unless it may already be incomplete.

    Nothing marks a change when the background task fills in a missing day,
    so a result read with a day missing is never kept; the next visit reads
    the days again. A bulk import defers its marks until it ends, and a
    pending full sweep rebuilds day payloads without a new mark, so nothing is
    kept while either is outstanding. Each user keeps one entry per kind: a
    new range replaces the last one.
    """
    if missing_days:
        return
    from app import statistics_sync
    from app.models import StatisticsSyncState

    if statistics_sync._bulk_import_active(user.id):
        return
    if StatisticsSyncState.objects.filter(
        user_id=user.id, full_sweep_requested_at__isnull=False
    ).exists():
        return
    slot_key = _custom_range_slot_key(kind, user.id)
    previous = cache.get(slot_key)
    cache.set(key, value, timeout=STATISTICS_CUSTOM_RANGE_CACHE_TIMEOUT)
    cache.set(slot_key, key, timeout=STATISTICS_CUSTOM_RANGE_CACHE_TIMEOUT)
    if previous and previous != key:
        cache.delete(previous)


def _custom_range_result(kind, user, start_date, end_date, build):
    """Serve a remembered custom range, or build it once and remember it.

    ``build(missing_days)`` computes the result and appends the days it found
    without a payload. A concurrent request for the same range waits for the
    first one instead of computing it twice.
    """
    key = _custom_range_key(
        kind, user.id, _custom_range_token(user, start_date, end_date)
    )
    cached = cache.get(key)
    if cached is not None:
        return cached
    owns_lock = cache_utils.acquire_build_lock(
        key, STATISTICS_CUSTOM_RANGE_BUILD_LOCK_TTL
    )
    if not owns_lock and cache_utils.wait_for_build(
        key,
        lambda: cache.has_key(key),
        STATISTICS_CUSTOM_RANGE_BUILD_WAIT,
    ):
        cached = cache.get(key)
        if cached is not None:
            return cached
    missing_days = []
    try:
        value = build(missing_days)
        _remember_custom_range(kind, user, key, value, missing_days)
    finally:
        if owns_lock:
            cache_utils.release_build_lock(key)
    if not _eager_statistics_mode():
        _schedule_missing_day_builds(user, missing_days, start_date, end_date)
    return value


def _eager_day_builder(user):
    """Build a batch of missing days the way the background sync does.

    Eager (test) mode has no worker, so reads build their missing days
    inline. Going through ``statistics_sync._build_days`` builds each slice
    from one range-wide prefetch instead of ~13 queries per day, and writes
    the same day payloads to the cache.
    """
    from app import statistics_sync

    def build(days):
        return statistics_sync._build_days(user, sorted(days, reverse=True), None, {})

    return build


def get_statistics_minutes_by_type(user, start_date, end_date, range_name=None):
    """Return only minute totals for a statistics range.

    This avoids rebuilding the full statistics payload for lightweight comparison cards.
    """
    if range_name in PREDEFINED_RANGES:
        cache_entry = _published_entry(user.id, range_name)
        if isinstance(cache_entry, dict):
            data = cache_entry.get("data") or {}
            minutes_per_type = data.get("minutes_per_media_type")
            if isinstance(minutes_per_type, dict):
                return minutes_per_type

    day_list = _resolve_day_list(user, start_date, end_date)
    if not day_list:
        return {}
    eager = _eager_statistics_mode()

    def build(missing_days):
        return _aggregate_minutes_per_media_type_from_days(
            user,
            day_list,
            build_missing=eager,
            build_days=_eager_day_builder(user) if eager else None,
            missing_days=missing_days,
        )

    return _custom_range_result("minutes", user, start_date, end_date, build)


def _finalize_for_read(data):
    """Normalize a statistics payload for display without calling providers."""
    _normalize_hours_per_media_type(data.get("hours_per_media_type"))
    normalize_highlight_images(data)


def get_statistics_data(user, start_date, end_date, range_name=None):
    """Return cached statistics, rebuilding if needed.

    Always returns cached data if available (even if stale) to avoid timeouts.
    Schedules background refresh if cache is stale.

    Args:
        user: User instance
        start_date: Start date for statistics (datetime or None)
        end_date: End date for statistics (datetime or None)
        range_name: Predefined range name (e.g., "Last 12 Months") or None

    Returns:
        Dictionary with statistics data
    """
    if range_name is None or range_name not in PREDEFINED_RANGES:
        # A custom range is aggregated from the per-day caches and remembered
        # until the next change (see _custom_range_result). Missing days are
        # built in the background rather than inline so a cold multi-year
        # range can't block the request for minutes.
        day_list = _resolve_day_list(user, start_date, end_date)
        if not day_list and not apps.get_model("app", "AlbumTracker").objects.filter(user=user).exists():
            return _get_empty_statistics_data()
        eager = _eager_statistics_mode()

        def build(missing_days):
            return _aggregate_statistics_from_days(
                user,
                day_list,
                start_date,
                end_date,
                build_missing=eager,
                build_days=_eager_day_builder(user) if eager else None,
                missing_days=missing_days,
            )

        data = _custom_range_result("range", user, start_date, end_date, build)
        _finalize_for_read(data)
        return data

    from app import statistics_sync

    entry = statistics_sync.load_snapshot(user.id, range_name)
    stale = entry is None or statistics_sync.entry_is_stale(entry, user_id=user.id)
    if stale and _eager_statistics_mode():
        # Eager/test mode has no worker: rebuild inline so reads see changes.
        data = refresh_statistics_cache(user.id, range_name)
        if data is not None:
            _finalize_for_read(data)
            return data
    if stale:
        # A never-built range is the one case the user is waiting on.
        statistics_sync.ensure_sync(user.id, urgent=entry is None)
    if entry is None:
        data = _get_empty_statistics_data()
        data["statistics_building"] = True
        return data
    data = entry.get("data", {})
    _finalize_for_read(data)
    return data


# Re-exports — keep all public symbols importable from this module.
from app.statistics_aggregator import (  # noqa: E402
    _aggregate_minutes_per_media_type_from_days,
    _aggregate_statistics_from_days,
    _build_activity_data,
    _build_daily_hours_chart,
    _build_media_charts_from_counts,
    _compute_metric_breakdown_for_range,
    _empty_reading_consumption,
    _empty_top_talent_payload,
    _fetch_media_objects,
    _parse_activity_dt,
)
from app.statistics_day_builder import (  # noqa: E402
    _day_boundary_datetime,
    _day_bounds,
    _iter_day_range,
    _overlap_day_filter,
    build_stats_for_day,
)
from app.statistics_refresh import (  # noqa: E402
    _get_activity_bounds,
    _get_predefined_range_dates,
    _get_sparse_activity_days,
    _range_day_bounds,
    _resolve_day_list,
    invalidate_all_statistics_days,
    invalidate_statistics_cache,
    refresh_statistics_cache,
    schedule_all_ranges_refresh,
    schedule_statistics_refresh,
)

__all__ = [
    "DAY_KEY_LENGTH",
    "STATISTICS_DAY_CACHE_TIMEOUT",
    "STATISTICS_DAY_CACHE_VERSION",
    "STATISTICS_DAY_PREFIX",
    "STATISTICS_HISTORY_VERSION_PREFIX",
    "STATISTICS_TOP_N",
    "STATISTICS_TOP_RATED_OVERALL",
    "Counter",
    "CreditRoleType",
    "Episode",
    "ExtractDay",
    "ExtractMonth",
    "Item",
    "ItemPersonCredit",
    "ItemStudioCredit",
    "Max",
    "Min",
    "Movie",
    "Person",
    "PersonGender",
    "Q",
    "SimpleNamespace",
    "Sources",
    "Status",
    "TruncDate",
    "_build_activity_data",
    "_build_daily_hours_chart",
    "_build_media_charts_from_counts",
    "_cached_horizontal_backdrop",
    "_compute_metric_breakdown_for_range",
    "_day_boundary_datetime",
    "_day_bounds",
    "_day_cache_key",
    "_empty_reading_consumption",
    "_empty_top_talent_payload",
    "_fetch_media_objects",
    "_get_activity_bounds",
    "_get_history_day_payload",
    "_get_history_index_days",
    "_get_history_version",
    "_get_horizontal_history_image",
    "_get_predefined_range_dates",
    "_get_range_history_boundary_days",
    "_get_sparse_activity_days",
    "_get_today_history_entries",
    "_get_today_release_entry",
    "_history_entry_card_payload",
    "_history_version_key",
    "_is_director_credit",
    "_is_writer_credit",
    "_iter_day_range",
    "_normalize_day_value",
    "_normalize_history_highlight_images",
    "_normalize_history_highlights_by_type",
    "_overlap_day_filter",
    "_parse_activity_dt",
    "_range_day_bounds",
    "_resolve_missing_credit_item_ids",
    "_safe_runtime_minutes",
    "_select_history_entry_for_day",
    "_set_history_version",
    "_tv_episode_play_rows",
    "app_tags",
    "build_stats_for_day",
    "calendar",
    "config",
    "credit_helpers",
    "heapq",
    "helpers",
    "invalidate_all_statistics_days",
    "invalidate_statistics_cache",
    "itertools",
    "normalize_highlight_images",
    "random",
    "relativedelta",
    "schedule_all_ranges_refresh",
    "schedule_statistics_refresh",
    "time",
]
