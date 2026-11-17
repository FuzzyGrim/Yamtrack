"""Discover cache warming and refresh Celery tasks.

Extracted from tasks.py. Re-exported from app.tasks for backward compatibility.
Tasks keep their original explicit Celery names so queued tasks survive the deploy.
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone

from app import cache_safety, history_cache
from app.history_cache_utils import HISTORY_LOGGING_STYLES, history_coverage_styles
from app.interactive_requests import interactive_request_active
from app.models import MediaTypes
from app.providers import credentials
from app.task_cooperation import CooperativeRun

logger = logging.getLogger(__name__)

# Mirrors BACKGROUND_TASK_PRIORITY in tasks.py — both read from the same setting.
BACKGROUND_TASK_PRIORITY = getattr(settings, "CELERY_TASK_PRIORITY_BACKGROUND", 9)
DISCOVER_PROFILE_REFRESH_LOCK_SECONDS = 30 * 60


@shared_task(name="Refresh Discover Rows")
def refresh_discover_rows(
    user_id: int, media_type: str, row_keys: list[str], show_more: bool = False
):
    """Refresh selected Discover rows for a user."""
    from app.discover.service import release_row_refresh

    row_keys = row_keys or []
    try:
        result = _refresh_rows(user_id, media_type, row_keys, show_more)
    except Exception:
        release_row_refresh(user_id, media_type, row_keys, show_more)
        raise
    # The user object is for the tab rebuild only; the result goes to the
    # Celery result backend as JSON, on every return path.
    user = result.pop("user", None)
    rebuild_tab = result.pop("rebuild_tab", None)
    # Keep the higher-level tab cache aligned with refreshed row caches, unless
    # another row refresh for this tab is still queued: that one rebuilds it.
    if not release_row_refresh(user_id, media_type, row_keys, show_more):
        logger.info(
            "discover_refresh_rows_tab_coalesced user_id=%s media_type=%s row_keys=%s",
            user_id,
            media_type,
            ",".join(row_keys),
        )
        return result
    if rebuild_tab:
        _rebuild_tab_after_rows(user, result["media_type"], show_more)
    return result


@shared_task(name="Refresh Discover Feed Row")
def refresh_discover_feed_row(
    user_id: int,
    media_type: str,
    row_key: str,
    skip_planning: bool,
    skip_collected: bool,
):
    """Build the row a Discover RSS link serves, off the request path."""
    from app.discover.feeds import feed_row_key, store_feed_row

    try:
        user = get_user_model().objects.filter(id=user_id).first()
        if user is None:
            return {"stored": False, "reason": "missing_user"}
        entry = store_feed_row(
            user,
            media_type,
            row_key,
            skip_planning=skip_planning,
            skip_collected=skip_collected,
        )
        return {"stored": entry is not None}
    finally:
        key = feed_row_key(user_id, media_type, row_key, skip_planning, skip_collected)
        cache.delete(f"{key}:refreshing")


def _refresh_rows(user_id, media_type, row_keys, show_more):
    from app.discover import tab_cache as discover_tab_cache
    from app.discover.service import refresh_rows_for_user
    from app.discover.service_helpers import credit_maps_scope

    user_model = get_user_model()
    user = user_model.objects.filter(id=user_id).first()
    if not user:
        logger.warning("discover_refresh_rows_user_missing user_id=%s", user_id)
        return {"refreshed": 0, "reason": "missing_user"}
    if not user.show_discover:
        return {"refreshed": 0, "reason": "discover_disabled"}

    requested_media_type = (
        (media_type or discover_tab_cache.ALL_MEDIA_KEY).strip().lower()
    )
    if (
        requested_media_type != discover_tab_cache.ALL_MEDIA_KEY
        and not discover_tab_cache.media_type_is_enabled_for_user(
            user, requested_media_type
        )
    ):
        logger.info(
            "discover_refresh_rows_skipped user_id=%s media_type=%s reason=disabled_media_type",
            user_id,
            requested_media_type,
        )
        return {"refreshed": 0, "reason": "disabled_media_type", "user_id": user_id}

    with credentials.current_user_scope(user), credit_maps_scope():
        refreshed = refresh_rows_for_user(
            user,
            requested_media_type,
            row_keys,
            show_more=show_more,
        )
    return {
        "refreshed": refreshed,
        "user_id": user_id,
        "media_type": requested_media_type,
        "rebuild_tab": True,
        "user": user,
    }


def _rebuild_tab_after_rows(user, media_type, show_more):
    from app.discover.service import stale_refresh_suppressed
    from app.discover.tab_cache import refresh_tab_cache

    with (
        credentials.current_user_scope(user),
        stale_refresh_suppressed(),
    ):
        refresh_tab_cache(
            user,
            media_type,
            show_more=show_more,
            force=False,
            clear_provider_cache=False,
        )


@shared_task(name="Refresh Discover Tab Cache")
def refresh_discover_tab_cache(
    user_id: int,
    media_type: str,
    show_more: bool = False,
    force: bool = False,
    clear_provider_cache: bool = False,
):
    """Refresh the Redis-backed Discover tab cache for a user/media type."""
    from app.discover import tab_cache as discover_tab_cache
    from app.discover.tab_cache import refresh_tab_cache

    user_model = get_user_model()
    user = user_model.objects.filter(id=user_id).first()
    if not user:
        logger.warning("discover_tab_refresh_user_missing user_id=%s", user_id)
        return {"refreshed": False, "reason": "missing_user"}
    if not user.show_discover:
        discover_tab_cache.release_refresh_reservation(
            user_id,
            (media_type or discover_tab_cache.ALL_MEDIA_KEY).strip().lower(),
            show_more=show_more,
        )
        return {"refreshed": False, "reason": "discover_disabled"}

    requested_media_type = (
        (media_type or discover_tab_cache.ALL_MEDIA_KEY).strip().lower()
    )
    if (
        requested_media_type != discover_tab_cache.ALL_MEDIA_KEY
        and not discover_tab_cache.media_type_is_enabled_for_user(
            user, requested_media_type
        )
    ):
        logger.info(
            "discover_tab_refresh_skipped user_id=%s media_type=%s reason=disabled_media_type",
            user_id,
            requested_media_type,
        )
        return {"refreshed": False, "reason": "disabled_media_type", "user_id": user_id}

    if (
        not force
        and not clear_provider_cache
        and discover_tab_cache.has_fresh_tab_cache(
            user_id, requested_media_type, show_more=show_more
        )
    ):
        # A copy queued behind a long task finds the tab already rebuilt for
        # this activity version by the copy that ran first.
        discover_tab_cache.release_refresh_lock(
            user_id, requested_media_type, show_more=show_more
        )
        logger.info(
            "discover_tab_refresh_skipped user_id=%s media_type=%s show_more=%s reason=already_fresh",
            user_id,
            requested_media_type,
            int(bool(show_more)),
        )
        return {"refreshed": False, "reason": "already_fresh", "user_id": user_id}

    with credentials.current_user_scope(user):
        rows = refresh_tab_cache(
            user,
            requested_media_type,
            show_more=show_more,
            force=force,
            clear_provider_cache=clear_provider_cache,
        )
    return {
        "refreshed": True,
        "row_count": len(rows),
        "user_id": user_id,
        "media_type": requested_media_type,
        "show_more": bool(show_more),
        "force": bool(force),
        "clear_provider_cache": bool(clear_provider_cache),
    }


# Maximum discover tab tasks enqueued in a single startup warmup. After a
# Redis restart all freshness locks are gone, so without this cap every user x
# every media type would be enqueued at once, burying the celery queue.
# Users whose tabs are not pre-warmed receive them on first page load via the
# request-time warmup path (maybe_schedule_user_warmup).
STARTUP_WARMUP_TASK_LIMIT = settings.WARMUP_USER_LIMIT

# The same reasoning applies to the recurring sweeps below, which had no cap at
# all: they iterated every active user (x2 logging styles, for history coverage)
# on every run. A cursor makes a capped sweep still reach everyone, just across
# several runs instead of one (issue #521).
WARMUP_CURSOR_TTL_SECONDS = 60 * 60 * 24 * 7
# Only recompute taste profiles for users who might actually look at them.
PROFILE_REFRESH_ACTIVE_DAYS = 30


def _rotating_user_batch(queryset, cursor_key: str, limit: int):
    """Return up to `limit` users, resuming after the last sweep's cursor.

    Ordered by id so the cursor is meaningful, and wrapping to the start once the
    end is reached, so no user is starved by a cap.
    """
    cursor = cache.get(cursor_key) or 0
    batch = list(queryset.filter(id__gt=cursor).order_by("id")[:limit])
    if not batch and cursor:
        # Reached the end; wrap around for this run rather than doing nothing.
        batch = list(queryset.order_by("id")[:limit])
    cache.set(
        cursor_key,
        batch[-1].id if batch else 0,
        timeout=WARMUP_CURSOR_TTL_SECONDS,
    )
    return batch


@shared_task(name="Warm Discover Startup Tabs")
def warm_discover_startup_tabs(user_ids: list[int] | None = None):
    """Warm the default Discover tab cache for users after app startup."""
    from app.discover.registry import ALL_MEDIA_KEY
    from app.discover.tab_cache import schedule_user_tab_warmup

    user_model = get_user_model()
    users = user_model.objects.filter(is_active=True, show_discover=True)
    if user_ids:
        users = users.filter(id__in=user_ids)

    scheduled = 0
    users_count = 0
    for user in users.iterator(chunk_size=200):
        if scheduled >= STARTUP_WARMUP_TASK_LIMIT:
            break
        users_count += 1
        scheduled += schedule_user_tab_warmup(
            user,
            media_types=[ALL_MEDIA_KEY],
            prioritize_media_type=ALL_MEDIA_KEY,
            show_more=False,
        )

    return {
        "scheduled": scheduled,
        "users_count": users_count,
    }


@shared_task(name="Warm History Day Cache Coverage")
def warm_history_day_cache_coverage(
    user_ids: list[int] | None = None,
    logging_styles: list[str] | None = None,
):
    """Queue chunked history day coverage repair for active users.

    Each user's own logging style unless ``logging_styles`` names styles; the
    repair itself keeps to the recent window and skips a style the user does
    not read (see ``history_cache_utils.history_coverage_styles``).
    """
    if interactive_request_active():
        logger.info(
            "warm_history_day_cache_coverage skipped reason=interactive_request_active",
        )
        return {
            "scheduled": 0,
            "users_count": 0,
            "logging_styles": list(logging_styles or HISTORY_LOGGING_STYLES),
            "reason": "interactive_request_active",
        }

    user_model = get_user_model()
    users = user_model.objects.filter(is_active=True)
    if user_ids:
        # An explicit request targets exactly those users, uncapped.
        selected = list(users.filter(id__in=user_ids))
    else:
        selected = _rotating_user_batch(
            users,
            "history_coverage_warmup_cursor",
            settings.WARMUP_USER_LIMIT,
        )

    scheduled = 0
    styles_seen = set()
    users_count = 0
    run = CooperativeRun("history_coverage_warmup")
    # Checked per user rather than once at entry: a sweep that starts while the
    # user is away must stop when they come back, not run to completion.
    for user in run.iter(selected):
        users_count += 1
        styles = logging_styles or history_coverage_styles(user)
        styles_seen.update(styles)
        for logging_style in styles:
            scheduled += int(
                history_cache.schedule_history_day_cache_coverage(
                    user.id,
                    logging_style=logging_style,
                    countdown=0,
                    batch_size=history_cache.HISTORY_COVERAGE_REPAIR_BATCH_SIZE,
                    priority=BACKGROUND_TASK_PRIORITY,
                ),
            )

    return {
        "scheduled": scheduled,
        "users_count": users_count,
        "logging_styles": sorted(styles_seen),
    }


@shared_task(name="Refresh Discover Profile For User")
def refresh_discover_profile_for_user(
    user_id: int, media_types: list[str], force: bool = False
):
    """Refresh stale profiles, allowing explicit callers to force recomputation."""
    from app.discover.profile import get_or_compute_taste_profile

    user_model = get_user_model()
    user = user_model.objects.filter(id=user_id).first()
    if not user:
        return {"profiles_refreshed": 0, "reason": "missing_user"}
    if not user.show_discover:
        return {"profiles_refreshed": 0, "reason": "discover_disabled"}

    refreshed = 0
    skipped = 0
    for media_type in media_types:
        lock_key = f"discover_profile_refresh:{user_id}:{media_type}"
        if not force and not cache_safety.acquire_lock(
            lock_key,
            timeout=DISCOVER_PROFILE_REFRESH_LOCK_SECONDS,
            on_error=cache_safety.ON_ERROR_SKIP,
        ):
            skipped += 1
            continue
        try:
            get_or_compute_taste_profile(user, media_type, force=force)
            refreshed += 1
        finally:
            if not force:
                cache_safety.release_lock(lock_key)
    return {"profiles_refreshed": refreshed, "profiles_skipped": skipped}


@shared_task(name="Refresh Discover Profiles")
def refresh_discover_profiles(
    user_ids: list[int] | None = None,
    media_types: list[str] | None = None,
    force: bool = False,
):
    """Fan out Discover taste profile refreshes, capped per run.

    Previously computed every user's profile inline in one task, so both the
    runtime and the peak RSS of a single worker child grew with the user count
    (issue #521). Now one subtask per user, capped and rotating, and only for
    users who have been active recently - a profile nobody will look at is not
    worth recomputing.
    """
    from app.discover.registry import ALL_MEDIA_KEY

    user_model = get_user_model()
    target_media_types = media_types or [ALL_MEDIA_KEY]

    if user_ids:
        selected = list(
            user_model.objects.filter(id__in=user_ids, show_discover=True),
        )
    else:
        cutoff = timezone.now() - timedelta(days=PROFILE_REFRESH_ACTIVE_DAYS)
        selected = _rotating_user_batch(
            user_model.objects.filter(
                is_active=True,
                show_discover=True,
                last_login__gte=cutoff,
            ),
            "discover_profile_refresh_cursor",
            settings.WARMUP_USER_LIMIT,
        )

    for index, user in enumerate(selected):
        refresh_discover_profile_for_user.apply_async(
            kwargs={
                "user_id": user.id,
                "media_types": target_media_types,
                "force": force,
            },
            # Spread them out so a single beat tick doesn't become a burst.
            countdown=index * 5,
            priority=BACKGROUND_TASK_PRIORITY,
        )

    return {
        "scheduled": len(selected),
        "users_count": len(selected),
        "media_types": target_media_types,
    }


@shared_task(name="Purge expired Discover caches", ignore_result=True)
def purge_expired_discover_caches():
    """Bound durable Discover cache tables by deleting long-expired rows."""
    from app.discover import cache_repo

    deleted = cache_repo.purge_expired_caches()
    logger.info("discover_cache_purge deleted=%s", deleted)
    return deleted


@shared_task(name="Warm Discover API Cache")
def warm_discover_api_cache():
    """Warm provider-backed Discover API cache for core TMDb and Trakt rows."""
    if interactive_request_active():
        logger.info("warm_discover_api_cache skipped reason=interactive_request_active")
        return {
            "skipped": True,
            "reason": "interactive_request_active",
            "warmed": 0,
            "failed": 0,
        }

    if not get_user_model().objects.filter(is_active=True, show_discover=True).exists():
        return {
            "skipped": True,
            "reason": "discover_disabled",
            "warmed": 0,
            "failed": 0,
        }

    from app.discover.providers.tmdb_adapter import TMDbDiscoverAdapter
    from app.discover.providers.trakt_adapter import TraktDiscoverAdapter

    adapter = TMDbDiscoverAdapter()
    trakt_adapter = TraktDiscoverAdapter()
    warmed = 0
    failed = 0
    for media_type in (MediaTypes.MOVIE.value, MediaTypes.TV.value):
        for fetcher in (
            adapter.trending,
            adapter.current_cycle,
            adapter.upcoming,
            adapter.top_rated,
        ):
            try:
                fetcher(media_type, limit=20)
                warmed += 1
            except Exception as error:
                failed += 1
                logger.warning(
                    "discover_api_warm_failed media_type=%s fetcher=%s error=%s",
                    media_type,
                    getattr(fetcher, "__name__", "unknown"),
                    error,
                )

    try:
        trakt_adapter.movie_watched_weekly(limit=25)
        warmed += 1
    except Exception as error:
        failed += 1
        logger.warning(
            "discover_api_warm_failed media_type=%s fetcher=%s error=%s",
            MediaTypes.MOVIE.value,
            "movie_watched_weekly",
            error,
        )

    try:
        trakt_adapter.movie_popular(page=1, limit=25)
        warmed += 1
    except Exception as error:
        failed += 1
        logger.warning(
            "discover_api_warm_failed media_type=%s fetcher=%s error=%s",
            MediaTypes.MOVIE.value,
            "movie_popular",
            error,
        )

    try:
        trakt_adapter.movie_anticipated(page=1, limit=25)
        warmed += 1
    except Exception as error:
        failed += 1
        logger.warning(
            "discover_api_warm_failed media_type=%s fetcher=%s error=%s",
            MediaTypes.MOVIE.value,
            "movie_anticipated",
            error,
        )

    return {"warmed": warmed, "failed": failed}
