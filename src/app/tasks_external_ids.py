"""External-ID backfill: queryset builder, enqueue, and reconcile tasks.

Populates ``Item.provider_external_ids`` with the IMDb IDs TMDB already returns
in its detail payloads, so movies can reach the Stremio catalog
(``integrations/stremio_catalog.local_imdb_id``) and IMDb rating sync
(``app.services.imdb_ratings``). Both filter on the ``imdb_id`` key and skipped
every movie while ``tmdb.movie()`` dropped it (issue #1066).

The provider fix alone only helps titles added afterwards - every library
populated before it keeps the gap, and items don't refetch on their own. This
sweep is that catch-up: modeled on tasks_providers.py, gated on durable
MetadataBackfillState so it converges over a library once and then goes quiet.
"""

import logging

from celery import shared_task
from django.conf import settings

from app import backfill_queue, reconcile_state
from app.interactive_requests import interactive_request_active
from app.log_safety import exception_summary
from app.models import Item, MediaTypes, MetadataBackfillField, Sources
from app.providers import services
from app.task_cooperation import CooperativeRun
from app.tasks_backfill_state import (
    EXTERNAL_IDS_BACKFILL_VERSION,
    _apply_backfill_state_filters,
    _filter_backfill_item_ids,
    _normalize_item_ids,
    _record_backfill_failure,
    _record_backfill_success,
    due_backfill_retry_item_ids,
)

logger = logging.getLogger(__name__)

BACKGROUND_TASK_PRIORITY = getattr(settings, "CELERY_TASK_PRIORITY_BACKGROUND", 9)

EXTERNAL_IDS_MEDIA_TYPES = (
    MediaTypes.MOVIE.value,
    MediaTypes.TV.value,
    MediaTypes.ANIME.value,
)
EXTERNAL_IDS_BACKFILL_QUEUE_TTL = 60 * 60  # 1 hour
EXTERNAL_IDS_BACKFILL_ITEMS_QUEUE_KEY = "external_ids_backfill_items_queue"
EXTERNAL_IDS_BACKFILL_ITEMS_SCHEDULED_KEY = "external_ids_backfill_items_scheduled"

_EXTERNAL_IDS_BATCH_SIZE_DEFAULT = settings.EXTERNAL_IDS_RECONCILE_BATCH_SIZE
RECONCILE_KEY = "external_ids"
# See tasks_providers.RECONCILE_MAX_CHUNKS_PER_RUN.
RECONCILE_MAX_CHUNKS_PER_RUN = settings.RECONCILE_MAX_CHUNKS_PER_RUN


def _external_ids_queryset(*, for_reconcile: bool = False):
    from app.models import MetadataBackfillState

    queryset = Item.objects.filter(
        media_type__in=EXTERNAL_IDS_MEDIA_TYPES,
        source=Sources.TMDB.value,
    ).exclude(provider_external_ids__has_key="imdb_id")
    queryset = _apply_backfill_state_filters(
        queryset,
        MetadataBackfillField.EXTERNAL_IDS,
        for_reconcile=for_reconcile,
    )
    completed_ids = MetadataBackfillState.objects.filter(
        field=MetadataBackfillField.EXTERNAL_IDS,
        give_up=False,
        fail_count=0,
        last_success_at__isnull=False,
        strategy_version__gte=EXTERNAL_IDS_BACKFILL_VERSION,
    ).values("item_id")
    return queryset.exclude(id__in=completed_ids)


def is_external_ids_backfill_reconcile_complete() -> bool:
    """Return whether the current external-ID strategy has no remaining candidates."""
    return not _external_ids_queryset().exists()


def _populate_external_ids_for_items(items):
    from app.services import metadata_resolution

    updated_count = 0
    error_count = 0
    run = CooperativeRun("external_ids_backfill")
    for item in run.iter(items):
        try:
            metadata = services.get_media_metadata(
                item.media_type.lower(),
                item.media_id,
                item.source,
            )
            if not isinstance(metadata, dict):
                error_count += 1
                _record_backfill_failure(
                    item, MetadataBackfillField.EXTERNAL_IDS, "no metadata"
                )
                continue

            external_ids = metadata_resolution.upsert_provider_links(
                item,
                metadata,
                provider=item.source,
                provider_media_type=item.media_type,
            )

            # A successful fetch completes the backfill even when TMDB has no
            # IMDb ID for the title. Unlike watch providers - which a title can
            # gain later, so tasks_providers keeps those pending - a missing
            # IMDb ID is usually permanent, and treating it as pending would
            # leave those items retrying forever and the sweep never
            # converging.
            _record_backfill_success(
                item,
                MetadataBackfillField.EXTERNAL_IDS,
                strategy_version=EXTERNAL_IDS_BACKFILL_VERSION,
            )
            if external_ids.get("imdb_id"):
                updated_count += 1
        except Exception as exc:
            error_count += 1
            logger.exception(
                "Error updating external IDs for %s: %s",
                item.title,
                exception_summary(exc),  # noqa: TRY401  # exception_summary() is the project's sanitised rendering
            )
            _record_backfill_failure(
                item,
                MetadataBackfillField.EXTERNAL_IDS,
                f"exception: {exception_summary(exc)}",
            )

    run.reenqueue_if_deferred(enqueue_external_ids_backfill_items)
    logger.info(
        "External ID population batch completed: %s resolved, %s errors",
        updated_count,
        error_count,
    )
    return updated_count, error_count


def enqueue_external_ids_backfill_items(item_ids, countdown=10):
    """Queue item IDs for external-ID backfill."""
    normalized = _normalize_item_ids(item_ids)
    normalized = _filter_backfill_item_ids(
        normalized, MetadataBackfillField.EXTERNAL_IDS
    )
    if not normalized:
        return 0
    queued = backfill_queue.enqueue(
        EXTERNAL_IDS_BACKFILL_ITEMS_QUEUE_KEY,
        EXTERNAL_IDS_BACKFILL_ITEMS_SCHEDULED_KEY,
        normalized,
        ttl=EXTERNAL_IDS_BACKFILL_QUEUE_TTL,
        drain_task=populate_external_ids_backfill_queue,
        countdown=countdown,
    )
    if not queued:
        logger.debug("External ID backfill queue unavailable, dispatching directly")
        populate_external_ids_for_items.apply_async(
            args=[normalized], countdown=countdown
        )
    return len(normalized)


@shared_task(name="app.tasks.populate_external_ids_for_items")
def populate_external_ids_for_items(item_ids: list[int]):
    """Populate external-ID data for a targeted list of item IDs."""
    normalized = _normalize_item_ids(item_ids)
    if not normalized:
        return {"updated": 0, "errors": 0, "message": "No item IDs provided"}

    items_to_update = list(_external_ids_queryset().filter(id__in=normalized))
    if not items_to_update:
        return {
            "updated": 0,
            "errors": 0,
            "message": "No targeted items need external-ID data",
        }

    updated_count, error_count = _populate_external_ids_for_items(items_to_update)
    return {
        "updated": updated_count,
        "errors": error_count,
        "message": f"Processed {len(items_to_update)} targeted items",
    }


@shared_task(name="app.tasks.populate_external_ids_backfill_queue")
def populate_external_ids_backfill_queue(batch_size: int = 50):
    """Drain the external-ID backfill queue and process items in small batches."""
    with backfill_queue.drain(
        EXTERNAL_IDS_BACKFILL_ITEMS_QUEUE_KEY,
        EXTERNAL_IDS_BACKFILL_ITEMS_SCHEDULED_KEY,
        batch_size,
        drain_task=populate_external_ids_backfill_queue,
    ) as batch:
        if not batch:
            return {"processed": 0, "message": "No queued external-ID items"}

        return populate_external_ids_for_items(batch)


def enqueue_due_external_ids_backfill_retries(
    batch_size: int = _EXTERNAL_IDS_BATCH_SIZE_DEFAULT,
) -> int:
    """Queue failed external-ID backfills whose retry time has arrived.

    Reconcile only discovers never-attempted items (issue #521), so earlier
    failures are this bounded queue's job and keep running after the
    whole-library sweep has been marked complete.
    """
    due_ids = due_backfill_retry_item_ids(
        MetadataBackfillField.EXTERNAL_IDS,
        Item.objects.filter(
            media_type__in=EXTERNAL_IDS_MEDIA_TYPES,
            source=Sources.TMDB.value,
        ).exclude(provider_external_ids__has_key="imdb_id"),
        max(int(batch_size), 1),
    )
    if not due_ids:
        return 0
    return enqueue_external_ids_backfill_items(due_ids, countdown=10)


@shared_task(name="app.tasks.reconcile_external_ids_backfill")
def reconcile_external_ids_backfill(
    strategy_version: int | None = None,
    batch_size: int = _EXTERNAL_IDS_BATCH_SIZE_DEFAULT,
    max_chunks: int | None = None,
):
    """Queue a bounded slice of external-ID backfill candidates.

    Bounded, and resuming from a stored cursor, so a large library is swept
    across successive passes rather than pushed into Redis all at once
    (issue #521).
    """
    batch_size = max(int(batch_size), 1)
    max_chunks = RECONCILE_MAX_CHUNKS_PER_RUN if max_chunks is None else max_chunks
    resolved_version = int(strategy_version or EXTERNAL_IDS_BACKFILL_VERSION)
    state = reconcile_state.get_state(RECONCILE_KEY, resolved_version)

    cursor = state.last_cursor_item_id
    selected = 0
    enqueued = 0

    for chunk_index in range(max_chunks):
        batch_ids = list(
            _external_ids_queryset(for_reconcile=True)
            .filter(id__gt=cursor)
            .order_by("id")
            .values_list("id", flat=True)[:batch_size],
        )
        if not batch_ids:
            # Reached the end of the table. Starting from the top and finding
            # nothing means the strategy is genuinely reconciled; otherwise wrap
            # so the next pass rechecks the rows before the cursor, and only the
            # pass after that can conclude completion.
            complete = cursor == state.last_cursor_item_id == 0
            cursor = 0
            if complete:
                reconcile_state.mark_complete(RECONCILE_KEY, resolved_version)
                return {"selected": selected, "enqueued": enqueued, "complete": True}
            break

        cursor = batch_ids[-1]
        selected += len(batch_ids)
        enqueued += enqueue_external_ids_backfill_items(
            batch_ids,
            countdown=10 + chunk_index * 15,
        )

    reconcile_state.mark_progress(
        RECONCILE_KEY,
        resolved_version,
        cursor=cursor,
        enqueued=enqueued,
    )

    logger.info(
        "reconcile_external_ids_backfill selected=%d enqueued=%d cursor=%d version=%s",
        selected,
        enqueued,
        cursor,
        resolved_version,
    )
    return {"selected": selected, "enqueued": enqueued, "cursor": cursor}


@shared_task(name="Ensure external ID backfill reconcile")
def ensure_external_ids_backfill_reconcile(
    strategy_version: int | None = None,
    batch_size: int = _EXTERNAL_IDS_BATCH_SIZE_DEFAULT,
):
    """Run an external-ID reconcile pass unless one isn't needed."""
    if interactive_request_active():
        logger.info(
            "ensure_external_ids_backfill_reconcile skipped reason=interactive_request_active"
        )
        return {"skipped": True, "reason": "interactive_request_active"}

    retry_enqueued = enqueue_due_external_ids_backfill_retries(batch_size=batch_size)

    resolved_version = int(strategy_version or EXTERNAL_IDS_BACKFILL_VERSION)
    # Answered from the state row alone - no Item query, no cache read - so a
    # finished or backing-off reconcile costs one indexed row lookup rather than
    # a NOT IN subquery over the whole library.
    state = reconcile_state.should_run(RECONCILE_KEY, resolved_version)
    if state is None:
        return {
            "skipped": True,
            "reason": "not_due",
            "retry_enqueued": retry_enqueued,
        }

    if not reconcile_state.acquire(RECONCILE_KEY, state):
        return {
            "skipped": True,
            "reason": "already_running",
            "retry_enqueued": retry_enqueued,
        }

    try:
        result = reconcile_external_ids_backfill(
            strategy_version=resolved_version,
            batch_size=batch_size,
        )
        result["retry_enqueued"] = retry_enqueued
        return result
    finally:
        reconcile_state.release(RECONCILE_KEY)
