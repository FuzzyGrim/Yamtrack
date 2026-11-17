"""Credits backfill: queryset helpers, enqueue, and populate tasks.

Extracted from tasks.py. Re-exported from app.tasks for backward compatibility.
"""

import logging
from http import HTTPStatus

from celery import shared_task
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from app import backfill_queue
from app import credits as credit_helpers
from app.db_retry import run_retryable_db_operation
from app.interactive_requests import interactive_request_active
from app.log_safety import exception_summary
from app.models import (
    CREDITS_BACKFILL_VERSION,
    Item,
    MediaTypes,
    MetadataBackfillField,
    MetadataBackfillState,
    Sources,
)
from app.providers import services
from app.task_cooperation import DEFERRED_RETRY_SECONDS, CooperativeRun
from app.tasks_backfill_state import (
    _filter_backfill_item_ids,
    _normalize_item_ids,
    _record_backfill_failure,
    _record_backfill_success,
    _schedule_metadata_statistics_refresh,
)
from app.tasks_metadata_cache import _clear_items_metadata_cache, _fetch_item_metadata

logger = logging.getLogger(__name__)

CREDITS_BACKFILL_SOURCES = ("tmdb", "tvdb")
CREDITS_BACKFILL_QUEUE_TTL = 60 * 60  # 1 hour
CREDITS_BACKFILL_ITEMS_QUEUE_KEY = "credits_backfill_items_queue"
CREDITS_BACKFILL_ITEMS_SCHEDULED_KEY = "credits_backfill_items_scheduled"


def _missing_credits_item_ids(item_ids):
    return credit_helpers.missing_credits_backfill_item_ids(item_ids)


def _credits_backfill_blocked_states(now):
    """States ``_filter_backfill_item_ids`` drops for credits, as a subquery."""
    return MetadataBackfillState.objects.filter(
        field=MetadataBackfillField.CREDITS,
        item_id=OuterRef("pk"),
    ).filter(
        Q(give_up=True)
        | Q(next_retry_at__gt=now)
        | Q(
            give_up=False,
            fail_count=0,
            last_success_at__isnull=False,
            strategy_version__gte=CREDITS_BACKFILL_VERSION,
        ),
    )


def _next_credits_backfill_item_ids(batch_size: int, scan_multiplier: int):
    if batch_size <= 0:
        return []
    window = max(batch_size * max(scan_multiplier, 1), batch_size)
    candidates = (
        Item.objects.filter(
            source__in=CREDITS_BACKFILL_SOURCES,
            media_type__in=[
                MediaTypes.MOVIE.value,
                MediaTypes.TV.value,
                MediaTypes.SEASON.value,
                MediaTypes.EPISODE.value,
            ],
        )
        # TVDB only exposes cast/crew on the series-level payload: seasons get
        # no credits payload at all, and tvdb.episode() hardcodes empty
        # cast/crew, so both would otherwise be reprocessed every cycle for
        # nothing (a season eventually gives up; an episode never fails, so
        # it never stops being requeued).
        .exclude(
            source=Sources.TVDB.value,
            media_type__in=[MediaTypes.SEASON.value, MediaTypes.EPISODE.value],
        )
        # Items whose state already says done / backing off / given up are
        # skipped in SQL, so completed rows never occupy the scan window.
        .exclude(Exists(_credits_backfill_blocked_states(timezone.now())))
        .order_by("id")
        .values_list("id", flat=True)
    )
    # Walk the remaining candidates window by window (keyset on id) until the
    # batch is full or the items run out: a window with nothing missing no
    # longer hides the missing items that sit after it.
    selected = []
    last_id = 0
    while len(selected) < batch_size:
        window_ids = list(candidates.filter(id__gt=last_id)[:window])
        if not window_ids:
            break
        selected.extend(_missing_credits_item_ids(window_ids))
        if len(window_ids) < window:
            break
        last_id = window_ids[-1]
    return selected[:batch_size]


def _populate_credits_for_items(items, delay_seconds):
    from app import (
        credits,  # noqa: A004  # app.credits module, not the site builtin
    )

    updated_count = 0
    error_count = 0
    updated_items = []

    # Drop every item's cached metadata up front, in one cache round trip,
    # so each fetch below still reaches the provider; items of the same show
    # then share the payload the first of them just fetched.
    _clear_items_metadata_cache(items)

    run = CooperativeRun("credits_backfill")
    for item in run.iter(items):
        try:
            if item.media_type == MediaTypes.EPISODE.value and (
                item.season_number is None or item.episode_number is None
            ):
                logger.warning(
                    "Episode item %s is missing season/episode numbers; skipping credits backfill",
                    item.id,
                )
                error_count += 1
                _record_backfill_failure(
                    item,
                    MetadataBackfillField.CREDITS,
                    "missing season/episode numbers",
                )
                continue

            if (
                item.media_type == MediaTypes.SEASON.value
                and item.season_number is None
            ):
                logger.warning(
                    "Season item %s is missing season_number; skipping credits backfill",
                    item.id,
                )
                error_count += 1
                _record_backfill_failure(
                    item,
                    MetadataBackfillField.CREDITS,
                    "missing season number",
                )
                continue

            metadata = _fetch_item_metadata(item)

            if not isinstance(metadata, dict):
                logger.warning(
                    "No metadata returned for %s (%s, %s)",
                    item.title,
                    item.media_type,
                    item.source,
                )
                error_count += 1
                _record_backfill_failure(
                    item, MetadataBackfillField.CREDITS, "no metadata"
                )
                continue

            has_payload = any(
                key in metadata for key in ("cast", "crew", "studios_full")
            )
            if not has_payload:
                logger.warning("No credits payload available for %s", item.title)
                error_count += 1
                _record_backfill_failure(
                    item, MetadataBackfillField.CREDITS, "no credits payload"
                )
                continue

            # Suppress per-row signal side effects (each ItemPersonCredit
            # delete/create would otherwise schedule its own Discover rebuild);
            # _schedule_metadata_statistics_refresh below handles the follow-up
            # invalidation for the whole batch instead.
            from app.signals import suppress_media_change_side_effects

            # The sync is one atomic block, so a lock error rolls it back whole
            # and a retry is safe. A lock held by a concurrent statistics sync
            # usually clears within a few seconds.
            with suppress_media_change_side_effects():
                run_retryable_db_operation(
                    lambda item=item, metadata=metadata: (
                        credits.sync_item_credits_from_metadata(item, metadata)
                    ),
                    operation_name="credits sync",
                    operation_logger=logger,
                )
            _record_backfill_success(
                item,
                MetadataBackfillField.CREDITS,
                strategy_version=CREDITS_BACKFILL_VERSION,
            )
            updated_count += 1
            updated_items.append(item)

            if delay_seconds > 0:
                import time

                time.sleep(delay_seconds)
        except services.ProviderAPIError as exc:
            error_count += 1
            terminal = exc.status_code == HTTPStatus.NOT_FOUND
            logger.warning(
                "Credits metadata fetch failed for %s status=%s terminal=%s",
                item.title,
                exc.status_code,
                terminal,
            )
            _record_backfill_failure(
                item,
                MetadataBackfillField.CREDITS,
                f"provider error: {exception_summary(exc)}",
                terminal=terminal,
            )
        except Exception as exc:
            error_count += 1
            logger.exception(
                "Error syncing credits for %s: %s",
                item.title,
                exception_summary(exc),  # noqa: TRY401  # exception_summary() is the project's sanitised rendering
            )
            _record_backfill_failure(
                item,
                MetadataBackfillField.CREDITS,
                f"exception: {exception_summary(exc)}",
            )

    run.reenqueue_if_deferred(enqueue_credits_backfill_items)
    logger.info(
        "Credits population batch completed: %s updated, %s errors",
        updated_count,
        error_count,
    )
    if updated_items:
        _schedule_metadata_statistics_refresh(
            updated_items,
            MetadataBackfillField.CREDITS,
            "credits_backfill",
        )
    return updated_count, error_count


def enqueue_credits_backfill_items(item_ids, countdown=10):
    """Return the enqueue credits backfill items."""
    normalized = _normalize_item_ids(item_ids)
    normalized = _filter_backfill_item_ids(normalized, MetadataBackfillField.CREDITS)
    normalized = _missing_credits_item_ids(normalized)
    if not normalized:
        return 0
    queued = backfill_queue.enqueue(
        CREDITS_BACKFILL_ITEMS_QUEUE_KEY,
        CREDITS_BACKFILL_ITEMS_SCHEDULED_KEY,
        normalized,
        ttl=CREDITS_BACKFILL_QUEUE_TTL,
        drain_task=populate_credits_backfill_queue,
        countdown=countdown,
    )
    if not queued:
        logger.debug("Credits backfill queue unavailable, dispatching directly")
        populate_credits_data_for_items.apply_async(
            args=[normalized], countdown=countdown
        )
    return len(normalized)


@shared_task(name="app.tasks.populate_credits_data_for_items")
def populate_credits_data_for_items(item_ids: list[int], delay_seconds: float = 0.0):
    """Populate cast/crew/studio credits for a targeted list of item IDs."""
    normalized = _normalize_item_ids(item_ids)
    normalized = _filter_backfill_item_ids(normalized, MetadataBackfillField.CREDITS)
    normalized = _missing_credits_item_ids(normalized)
    if not normalized:
        return {
            "updated": 0,
            "errors": 0,
            "message": "No targeted items need credits data",
        }

    items_to_update = list(
        Item.objects.filter(
            id__in=normalized,
            source__in=CREDITS_BACKFILL_SOURCES,
            media_type__in=[
                MediaTypes.MOVIE.value,
                MediaTypes.TV.value,
                MediaTypes.SEASON.value,
                MediaTypes.EPISODE.value,
            ],
        ),
    )
    if not items_to_update:
        logger.info("No targeted items need credits data")
        return {
            "updated": 0,
            "errors": 0,
            "message": "No targeted items need credits data",
        }

    updated_count, error_count = _populate_credits_for_items(
        items_to_update, delay_seconds
    )
    return {
        "updated": updated_count,
        "errors": error_count,
        "message": f"Processed {len(items_to_update)} targeted items",
    }


@shared_task(name="app.tasks.populate_credits_backfill_queue")
def populate_credits_backfill_queue(batch_size: int = 50, delay_seconds: float = 0.0):
    """Drain the credits backfill queue and process items in small batches.

    While someone is using the app a run only does its one guaranteed item (the
    cooperative minimum that keeps the queue moving) and the next run waits out
    the interactive flag. Taking the full batch then would load and clear the
    metadata cache of 50 items, process one, and put 49 back, on every tick.
    """
    busy = interactive_request_active()
    with backfill_queue.drain(
        CREDITS_BACKFILL_ITEMS_QUEUE_KEY,
        CREDITS_BACKFILL_ITEMS_SCHEDULED_KEY,
        1 if busy else batch_size,
        drain_task=populate_credits_backfill_queue,
        countdown=DEFERRED_RETRY_SECONDS if busy else 10,
    ) as batch:
        if not batch:
            return {"processed": 0, "message": "No queued credits items"}

        return populate_credits_data_for_items(batch, delay_seconds=delay_seconds)
