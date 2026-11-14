"""Helpers for reconciling collection metadata across multiple sources."""

from django.utils import timezone

from app.models import CollectionEntry
from integrations.imports.helpers import retry_on_lock
from integrations.models import CollectionSourceState

QUALITY_RANKS = {
    "480p": 1,
    "576p": 2,
    "720p": 3,
    "1080p": 4,
    "1440p": 5,
    "2160p": 6,
    "4k": 6,
}


def _quality_rank(label: str) -> int:
    normalized = str(label or "").strip().lower()
    if not normalized:
        return 0
    if normalized in QUALITY_RANKS:
        return QUALITY_RANKS[normalized]
    for key, rank in QUALITY_RANKS.items():
        if key in normalized:
            return rank
    return 0


def _get_best_state(states):
    """Return the preferred source state for a collection entry."""
    best_state = None

    for state in states:
        if best_state is None:
            best_state = state
            continue

        state_rank = _quality_rank(state.quality_label)
        best_rank = _quality_rank(best_state.quality_label)
        if state_rank > best_rank:
            best_state = state
            continue

        if state_rank == best_rank and (
            state.last_source_updated_at or timezone.now()
        ) > (best_state.last_source_updated_at or timezone.now()):
            best_state = state

    return best_state


def _collection_entry_queryset(*, user, item):
    return CollectionEntry.objects.filter(user=user, item=item).order_by(
        "-updated_at",
        "-collected_at",
        "-id",
    )


def _entry_is_source_synced_placeholder(entry) -> bool:
    """Return True when an entry only contains source-sync-managed defaults."""
    return not any(
        [
            entry.media_type,
            entry.hdr,
            entry.audio_codec,
            entry.audio_channels,
            entry.bitrate is not None,
            entry.is_3d,
            entry.plex_rating_key,
            entry.plex_uri,
            entry.plex_rating_key_updated_at,
        ],
    )


def _reconcile_collection_entry(*, user, item):
    """Apply the current source-state snapshot to the durable collection entry."""
    states = list(CollectionSourceState.objects.filter(user=user, item=item))
    entries = _collection_entry_queryset(user=user, item=item)
    entry = entries.first()

    if not states:
        if (
            entry
            and entries.count() == 1
            and _entry_is_source_synced_placeholder(entry)
        ):
            entry.delete()
            return None
        return entry

    if entry is None:
        entry = CollectionEntry.objects.create(user=user, item=item)

    # An empty quality label means unknown (Jellyfin webhooks, Mylar3,
    # Kapowarr): it neither blanks nor outranks a resolution set by hand,
    # through the API or by another source.
    best_state = _get_best_state([state for state in states if state.quality_label])
    if best_state and entry.resolution != best_state.quality_label:
        entry.resolution = best_state.quality_label
        entry.save(update_fields=["resolution", "updated_at"])

    return entry


def upsert_collection_source_state(
    *,
    user,
    item,
    source: str,
    quality_label: str = "",
    source_updated_at=None,
    source_instance_id: int | None = None,
):
    """Persist a source snapshot and reconcile the durable collection entry."""
    entry, _changed = sync_collection_source_state(
        user=user,
        item=item,
        source=source,
        quality_label=quality_label,
        source_updated_at=source_updated_at,
        source_instance_id=source_instance_id,
    )
    return entry


def sync_collection_source_state(
    *,
    user,
    item,
    source: str,
    quality_label: str = "",
    source_updated_at=None,
    source_instance_id: int | None = None,
):
    """Persist a source snapshot; return ``(entry, changed)``.

    ``source_updated_at`` is the source's own timestamp for the copy, or None
    when it sends none. A snapshot the source reports exactly as before is
    left alone, so a scheduled sync of an unchanged library writes nothing;
    the collection entry is still reconciled, which repairs drift and writes
    only what differs.
    """
    quality_label = quality_label or ""

    def _upsert_state():
        state = CollectionSourceState.objects.filter(
            user=user,
            item=item,
            source=source,
            source_instance_id=source_instance_id,
        ).first()
        changed = state is None or state.quality_label != quality_label or (
            source_updated_at is not None
            and state.last_source_updated_at != source_updated_at
        )
        if changed:
            CollectionSourceState.objects.update_or_create(
                user=user,
                item=item,
                source=source,
                source_instance_id=source_instance_id,
                defaults={
                    "quality_label": quality_label,
                    # No source timestamp: first seen now, then kept.
                    "last_source_updated_at": source_updated_at
                    or (state.last_source_updated_at if state else None)
                    or timezone.now(),
                },
            )
        return _reconcile_collection_entry(user=user, item=item), changed

    return retry_on_lock(_upsert_state)


def prune_collection_source_states(
    *,
    user,
    source: str,
    source_instance_id: int | None,
    keep_item_ids,
) -> int:
    """Drop this instance's states for items it no longer reports as owned.

    Scoped to the caller's ``source`` and ``source_instance_id``: a Radarr run
    can never prune another source's snapshots, another instance's snapshots,
    or a collection entry that has no source snapshot behind it. Call only
    after a complete read of the instance; an item missing from a partial or
    failed run is not proof that the server deleted it.
    """
    stale = (
        CollectionSourceState.objects.filter(
            user=user,
            source=source,
            source_instance_id=source_instance_id,
        )
        .exclude(item_id__in=keep_item_ids)
        .select_related("item")
    )
    removed = 0
    for state in stale:
        remove_collection_source_state(
            user=user,
            item=state.item,
            source=source,
            source_instance_id=source_instance_id,
        )
        removed += 1
    return removed


def remove_collection_source_state(
    *,
    user,
    item,
    source: str,
    source_instance_id: int | None = None,
):
    """Remove a source-specific collection snapshot and reconcile the durable entry."""

    def _remove_state():
        removed_count, _ = CollectionSourceState.objects.filter(
            user=user,
            item=item,
            source=source,
            source_instance_id=source_instance_id,
        ).delete()
        if not removed_count:
            return _collection_entry_queryset(user=user, item=item).first()
        return _reconcile_collection_entry(user=user, item=item)

    return retry_on_lock(_remove_state)
