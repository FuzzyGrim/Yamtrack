"""Tasks consumed by the dedicated interactive Celery worker."""

import logging
import os
import time

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="Resolve live playback image")
def resolve_playback_image(user_id: int, slot: str | None = None):
    """Resolve artwork for a cached live playback state in the background."""
    from app import live_playback

    live_playback.resolve_state_image(user_id, slot)


@shared_task(name="app.tasks.statistics_sync_task")
def statistics_sync_task(user_id: int):
    """Rebuild a user's dirty Statistics days and stale ranges, within a budget.

    Runs out of time by queueing its own follow-up; if that message is ever
    lost, ``reconcile_statistics_sync_task`` finds the unfinished work.
    """
    from app import statistics_sync

    statistics_sync.sync_task_body(user_id)


@shared_task(name="Refresh statistics talent fragment", ignore_result=True)
def refresh_statistics_talent_fragment_task(
    user_id: int,
    range_name=None,
    start_date_str=None,
    end_date_str=None,
    compare_mode_param=None,
):
    """Rebuild a Statistics talent section the viewer is already seeing a copy of."""
    from django.contrib.auth import get_user_model

    from app.statistics_views import build_talent_fragment

    user = get_user_model().objects.filter(pk=user_id).first()
    if user is None:
        return
    build_talent_fragment(
        user, range_name, start_date_str, end_date_str, compare_mode_param
    )


# A full scan of the viewer's plays. Pages queue it once per history version
# and never wait for it; the hard limit frees the slot if SQLite is wedged.
@shared_task(
    name="Build person talent context",
    ignore_result=True,
    soft_time_limit=600,
    time_limit=660,
)
def build_person_talent_context_task(user_id: int):
    """Store the all-time watched context that person pages read their totals from."""
    from django.contrib.auth import get_user_model

    from app import statistics_cache

    user = get_user_model().objects.filter(pk=user_id).first()
    if user is None:
        return
    statistics_cache.get_person_talent_context(user)


@shared_task(name="app.tasks.mark_credits_statistics_refresh", ignore_result=True)
def mark_credits_statistics_refresh(user_id: int):
    """Record the credits change left behind by a coalesced backfill batch."""
    from app import statistics_sync

    statistics_sync.mark_credits_statistics_refresh(user_id)


# A reconcile is one query and a bounded fan-out of cache writes, so a run that
# exceeds this is wedged (a held SQLite writer, a hung Redis call), not busy.
# The hard limit kills the child; the soft one cannot interrupt a C-level wait.
@shared_task(
    name="Reconcile statistics sync",
    ignore_result=True,
    soft_time_limit=60,
    time_limit=120,
)
def reconcile_statistics_sync_task():
    """Queue a sync for every user whose Statistics trail their changes.

    Periodic recovery runs on the default worker. Viewer-requested syncs
    select the interactive lane independently.
    """
    from app import statistics_sync

    started = time.monotonic()
    logger.info("reconcile_statistics_sync start pid=%s", os.getpid())
    queued = statistics_sync.reconcile()
    logger.info(
        "reconcile_statistics_sync finish queued=%s duration_ms=%.0f",
        queued,
        (time.monotonic() - started) * 1000,
    )


# Retired names from the chunked refresh run. Kept registered for one release
# so messages queued by the previous version drain into a sync instead of
# failing as unregistered tasks.
@shared_task(name="app.tasks.refresh_statistics_cache_task")
def refresh_statistics_cache_task(user_id: int, range_name: str = "", force=False):
    """Retired: queue a Statistics sync."""
    from app import statistics_sync

    statistics_sync.sync_task_body(user_id)


@shared_task(name="app.tasks.continue_statistics_refresh_task")
def continue_statistics_refresh_task(user_id: int, range_name="", run_id=""):
    """Retired: queue a Statistics sync."""
    from app import statistics_sync

    statistics_sync.sync_task_body(user_id)
