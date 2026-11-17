"""Watch-provider backfill: queryset builder, enqueue, and reconcile tasks.

Populates Item.watch_providers from TMDB metadata so movies/TV/anime can be
filtered by streaming service. Modeled on the simpler parts of tasks_genre.py,
without genre's TVDB anime-detection logic (not applicable here).
"""

import logging

from celery import shared_task
from django.conf import settings
from django.db.models import Q

from app import backfill_queue, reconcile_state
from app.interactive_requests import interactive_request_active
from app.log_safety import exception_summary
from app.models import Item, MediaTypes, MetadataBackfillField, Sources
from app.providers import services
from app.services import metadata_resolution
from app.task_cooperation import CooperativeRun
from app.tasks_backfill_state import (
    WATCH_PROVIDERS_BACKFILL_VERSION,
    _apply_backfill_state_filters,
    _filter_backfill_item_ids,
    _normalize_item_ids,
    _record_backfill_failure,
    _record_backfill_pending,
    _record_backfill_success,
    due_backfill_retry_item_ids,
)

logger = logging.getLogger(__name__)

BACKGROUND_TASK_PRIORITY = getattr(settings, "CELERY_TASK_PRIORITY_BACKGROUND", 9)

PROVIDER_MEDIA_TYPES = (
    MediaTypes.MOVIE.value,
    MediaTypes.TV.value,
    MediaTypes.ANIME.value,
)
WATCH_PROVIDERS_BACKFILL_QUEUE_TTL = 60 * 60  # 1 hour
WATCH_PROVIDERS_BACKFILL_ITEMS_QUEUE_KEY = "watch_providers_backfill_items_queue"
WATCH_PROVIDERS_BACKFILL_ITEMS_SCHEDULED_KEY = (
    "watch_providers_backfill_items_scheduled"
)

_WATCH_PROVIDERS_BATCH_SIZE_DEFAULT = settings.WATCH_PROVIDERS_RECONCILE_BATCH_SIZE
RECONCILE_KEY = "watch_providers"
# How much of the library one reconcile pass may enqueue. At 50 items per chunk
# this caps in-flight work at a size the drain can clear before the next pass,
# instead of pushing the whole candidate set into Redis at once.
RECONCILE_MAX_CHUNKS_PER_RUN = settings.RECONCILE_MAX_CHUNKS_PER_RUN


def _provider_item_scope(prefix: str = ""):
    """Return items whose watch providers can be supplied by TMDB."""
    return Q(
        **{
            f"{prefix}source": Sources.TMDB.value,
            f"{prefix}media_type__in": PROVIDER_MEDIA_TYPES,
        },
    ) | Q(
        **{
            f"{prefix}source": Sources.MAL.value,
            f"{prefix}media_type": MediaTypes.ANIME.value,
        },
    )


def _provider_items_queryset(*, for_reconcile: bool = False):
    from app.models import MetadataBackfillState

    queryset = Item.objects.filter(_provider_item_scope(), watch_providers={})
    queryset = _apply_backfill_state_filters(
        queryset,
        MetadataBackfillField.WATCH_PROVIDERS,
        for_reconcile=for_reconcile,
    )
    completed_ids = MetadataBackfillState.objects.filter(
        field=MetadataBackfillField.WATCH_PROVIDERS,
        give_up=False,
        fail_count=0,
        last_success_at__isnull=False,
        strategy_version__gte=WATCH_PROVIDERS_BACKFILL_VERSION,
    ).values("item_id")
    return queryset.exclude(id__in=completed_ids)


def is_provider_backfill_reconcile_complete() -> bool:
    """Return whether the current watch-provider strategy has no remaining candidates."""
    return not _provider_items_queryset().exists()


def _populate_providers_for_items(items):
    updated_count = 0
    error_count = 0
    run = CooperativeRun("watch_providers_backfill")
    for item in run.iter(items):
        try:
            provider_media_id = item.media_id
            provider_source = item.source
            if (
                item.source == Sources.MAL.value
                and item.media_type == MediaTypes.ANIME.value
            ):
                identity = metadata_resolution.resolve_mal_tmdb_identity(item.media_id)
                provider_source = Sources.TMDB.value
                if not identity:
                    # No exact mapping is a completed result for this pinned
                    # mapping strategy. A later strategy-version bump will
                    # reconsider the item without retrying it every day now.
                    _record_backfill_success(
                        item,
                        MetadataBackfillField.WATCH_PROVIDERS,
                        strategy_version=WATCH_PROVIDERS_BACKFILL_VERSION,
                    )
                    continue
                metadata_resolution.persist_mal_tmdb_identity(item, identity)
                provider_media_id = identity.media_id
                provider_media_type = identity.media_type
            else:
                provider_media_type = item.media_type.lower()

            metadata = services.get_media_metadata(
                provider_media_type,
                provider_media_id,
                provider_source,
            )
            if not isinstance(metadata, dict):
                error_count += 1
                _record_backfill_failure(
                    item, MetadataBackfillField.WATCH_PROVIDERS, "no metadata"
                )
                continue

            providers = metadata.get("providers")
            if not isinstance(providers, dict):
                providers = {}

            if not providers:
                # An empty TMDB payload is not a completed backfill: the title
                # may gain a provider later, so keep retrying without giving up.
                _record_backfill_pending(
                    item,
                    MetadataBackfillField.WATCH_PROVIDERS,
                    "empty providers",
                    strategy_version=WATCH_PROVIDERS_BACKFILL_VERSION,
                )
                continue

            item.watch_providers = providers
            item.save(update_fields=["watch_providers"])
            _record_backfill_success(
                item,
                MetadataBackfillField.WATCH_PROVIDERS,
                strategy_version=WATCH_PROVIDERS_BACKFILL_VERSION,
            )
            updated_count += 1
        except Exception as exc:
            error_count += 1
            logger.exception(
                "Error updating watch providers for %s: %s",
                item.title,
                exception_summary(exc),  # noqa: TRY401  # exception_summary() is the project's sanitised rendering
            )
            _record_backfill_failure(
                item,
                MetadataBackfillField.WATCH_PROVIDERS,
                f"exception: {exception_summary(exc)}",
            )

    run.reenqueue_if_deferred(enqueue_provider_backfill_items)
    logger.info(
        "Watch provider population batch completed: %s updated, %s errors",
        updated_count,
        error_count,
    )
    return updated_count, error_count


def enqueue_provider_backfill_items(item_ids, countdown=10):
    """Queue item IDs for watch-provider backfill."""
    normalized = _normalize_item_ids(item_ids)
    normalized = _filter_backfill_item_ids(
        normalized, MetadataBackfillField.WATCH_PROVIDERS
    )
    if not normalized:
        return 0
    queued = backfill_queue.enqueue(
        WATCH_PROVIDERS_BACKFILL_ITEMS_QUEUE_KEY,
        WATCH_PROVIDERS_BACKFILL_ITEMS_SCHEDULED_KEY,
        normalized,
        ttl=WATCH_PROVIDERS_BACKFILL_QUEUE_TTL,
        drain_task=populate_provider_backfill_queue,
        countdown=countdown,
    )
    if not queued:
        logger.debug("Watch provider backfill queue unavailable, dispatching directly")
        populate_provider_data_for_items.apply_async(
            args=[normalized], countdown=countdown
        )
    return len(normalized)


@shared_task(name="app.tasks.populate_provider_data_for_items")
def populate_provider_data_for_items(item_ids: list[int]):
    """Populate watch-provider data for a targeted list of item IDs."""
    normalized = _normalize_item_ids(item_ids)
    if not normalized:
        return {"updated": 0, "errors": 0, "message": "No item IDs provided"}

    items_to_update = list(_provider_items_queryset().filter(id__in=normalized))
    if not items_to_update:
        return {
            "updated": 0,
            "errors": 0,
            "message": "No targeted items need watch-provider data",
        }

    updated_count, error_count = _populate_providers_for_items(items_to_update)
    return {
        "updated": updated_count,
        "errors": error_count,
        "message": f"Processed {len(items_to_update)} targeted items",
    }


@shared_task(name="app.tasks.populate_provider_backfill_queue")
def populate_provider_backfill_queue(batch_size: int = 50):
    """Drain the watch-provider backfill queue and process items in small batches."""
    with backfill_queue.drain(
        WATCH_PROVIDERS_BACKFILL_ITEMS_QUEUE_KEY,
        WATCH_PROVIDERS_BACKFILL_ITEMS_SCHEDULED_KEY,
        batch_size,
        drain_task=populate_provider_backfill_queue,
    ) as batch:
        if not batch:
            return {"processed": 0, "message": "No queued watch-provider items"}

        return populate_provider_data_for_items(batch)


def enqueue_due_provider_backfill_retries(
    batch_size: int = _WATCH_PROVIDERS_BATCH_SIZE_DEFAULT,
) -> int:
    """Queue empty or failed provider backfills whose retry time has arrived.

    Reconcile only discovers never-attempted items (issue #521). Retries of
    empty TMDB payloads and earlier failures are this bounded queue, so they
    keep running after the whole-library sweep has been marked complete.
    """
    due_ids = due_backfill_retry_item_ids(
        MetadataBackfillField.WATCH_PROVIDERS,
        Item.objects.filter(_provider_item_scope(), watch_providers={}),
        max(int(batch_size), 1),
    )
    if not due_ids:
        return 0
    return enqueue_provider_backfill_items(due_ids, countdown=10)


@shared_task(name="app.tasks.reconcile_provider_backfill")
def reconcile_provider_backfill(
    strategy_version: int | None = None,
    batch_size: int = _WATCH_PROVIDERS_BATCH_SIZE_DEFAULT,
    max_chunks: int | None = None,
):
    """Queue a bounded slice of watch-provider backfill candidates.

    Bounded, and resuming from a stored cursor, rather than draining the whole
    candidate set in one pass. The old version looped until the queryset was
    empty and enqueued everything it found, so on a large library each run put
    every outstanding item back into the queue while the drain removed only 50
    every ten seconds - the queue never shrank and the same growing value was
    rewritten continuously (issue #521).
    """
    batch_size = max(int(batch_size), 1)
    max_chunks = RECONCILE_MAX_CHUNKS_PER_RUN if max_chunks is None else max_chunks
    resolved_version = int(strategy_version or WATCH_PROVIDERS_BACKFILL_VERSION)
    state = reconcile_state.get_state(RECONCILE_KEY, resolved_version)

    cursor = state.last_cursor_item_id
    selected = 0
    enqueued = 0

    for chunk_index in range(max_chunks):
        batch_ids = list(
            _provider_items_queryset(for_reconcile=True)
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
        enqueued += enqueue_provider_backfill_items(
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
        "reconcile_provider_backfill selected=%d enqueued=%d cursor=%d version=%s",
        selected,
        enqueued,
        cursor,
        resolved_version,
    )
    return {"selected": selected, "enqueued": enqueued, "cursor": cursor}


@shared_task(name="Ensure watch provider backfill reconcile")
def ensure_provider_backfill_reconcile(
    strategy_version: int | None = None,
    batch_size: int = _WATCH_PROVIDERS_BATCH_SIZE_DEFAULT,
):
    """Run a watch-provider reconcile pass unless one isn't needed."""
    if interactive_request_active():
        logger.info(
            "ensure_provider_backfill_reconcile skipped reason=interactive_request_active"
        )
        return {"skipped": True, "reason": "interactive_request_active"}

    retry_enqueued = enqueue_due_provider_backfill_retries(batch_size=batch_size)

    resolved_version = int(strategy_version or WATCH_PROVIDERS_BACKFILL_VERSION)
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
        result = reconcile_provider_backfill(
            strategy_version=resolved_version,
            batch_size=batch_size,
        )
        result["retry_enqueued"] = retry_enqueued
        return result
    finally:
        reconcile_state.release(RECONCILE_KEY)
