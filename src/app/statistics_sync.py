"""Keep every Statistics range warm in the background.

A change marks the days it touched (``mark_days``) or, for changes with no day,
just bumps the user's generation (``mark_aggregate``). One per-user sync then
rebuilds the dirty day payloads and re-aggregates the ranges, publishing each
as a snapshot. The page serves the last snapshot and never waits.

Durable state lives in the database (``app.models.statistics``): dirty days,
the generation, the lease, the snapshots. No single Celery message is load
bearing: a lost, starved or duplicated sync message is found again by the
reconciler within a minute. See docs/architecture/statistics-sync.md.
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import MappingProxyType, SimpleNamespace

from django.apps import apps
from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, OperationalError, connection, transaction
from django.db.models import F, Model, Q
from django.db.models.functions import Coalesce, Greatest
from django.utils import timezone

from app.db_retry import is_lock_error
from app.history_cache_utils import history_deferred_item_fields
from app.interactive_requests import interactive_request_active
from app.models import StatisticsDirtyDay, StatisticsSnapshot, StatisticsSyncState
from app.statistics_day_cache import (
    STATISTICS_DAY_CACHE_TIMEOUT,
    _day_cache_key,
    _normalize_day_value,
    _set_history_version,
)
from app.task_cooperation import higher_priority_task_waiting

logger = logging.getLogger(__name__)

# Bump when the shape of a published payload changes; older snapshots are
# served until the next sync replaces them.
SNAPSHOT_SCHEMA_VERSION = 21

# Cheap ranges rebuilt on every sync, in this order.
HOT_RANGES = (
    "Today",
    "Yesterday",
    "This Week",
    "Last 7 Days",
    "This Month",
    "Last 30 Days",
)
# Expensive ranges, rebuilt once changes settle (or the delay cap passes).
HEAVY_RANGES = (
    "Last 90 Days",
    "Last 6 Months",
    "This Year",
    "Last 12 Months",
    "All Time",
)
WARM_DAY_COUNT = 2
# Ordinary syncs are bulk derived work and run on the background worker, so a
# webhook or a manual refresh never shares the interactive worker's only slot
# with a range aggregate. The sync still yields at each boundary when webhook
# work is queued, because both workers write the same SQLite database.
INTERACTIVE_QUEUE = "interactive"
BACKGROUND_QUEUE = "celery"
# What a range cost the last time it was built, so a task can tell whether the
# next one fits in what is left of its budget.
RANGE_COST_TIMEOUT = 7 * 24 * 60 * 60
# A sync this far past its budget is worth a warning naming the range.
OVERRUN_WARNING_SECONDS = 5
_import_changes: ContextVar[dict[int, dict]] = ContextVar(
    "statistics_import_changes", default=MappingProxyType({})
)


@contextmanager
def coalesce_import_changes(user_id: int):
    """Keep invalidations durable while one import advances one generation.

    Record the full sweep before writes: even a killed worker leaves recovery
    work behind. Dirty-day tokens still rotate, preserving concurrent marks.
    Other threads/processes retain the ordinary incremental marking path.

    An import that marked each day it wrote sets ``scoped``; a clean finish
    then drops the full sweep requested at the start.
    """
    users = _import_changes.get()
    if user_id in users:
        yield users[user_id]
        return
    fields = ("generation", "last_marked_at", "full_sweep_requested_at")
    previous = (
        StatisticsSyncState.objects.filter(user_id=user_id).values(*fields).first()
    )
    mark_aggregate(user_id, reason="media_import_started", full_sweep=True)
    marker = StatisticsSyncState.objects.filter(user_id=user_id).values(*fields).first()
    changes = {"marked": False, "unchanged": False, "scoped": False}
    token = _import_changes.set(users | {user_id: changes})
    try:
        yield changes
    finally:
        _import_changes.reset(token)
        # A verified no-op poll needs no full history rebuild. Restore only our
        # own untouched generation; another writer's mark makes this a no-op.
        if changes["unchanged"] and not changes["marked"] and previous and marker:
            StatisticsSyncState.objects.filter(
                user_id=user_id,
                generation=previous["generation"] + 1,
                full_sweep_requested_at=marker["full_sweep_requested_at"],
            ).update(**previous)
        elif changes["scoped"] and previous and marker:
            # The import marked every day it wrote itself, so the sweep
            # requested up front (for a worker killed mid-import) is spent:
            # without this one changed row re-marks every day in the library.
            StatisticsSyncState.objects.filter(
                user_id=user_id,
                full_sweep_requested_at=marker["full_sweep_requested_at"],
            ).update(full_sweep_requested_at=previous["full_sweep_requested_at"])


def _bulk_import_active(user_id: int) -> bool:
    """Defer derived work until finite bulk import tasks release the database."""
    from integrations.models import ImportRun
    from integrations.tasks._import_helpers import (
        SELF_RESCHEDULING_IMPORT_SOURCES,
        SOURCE_TIME_LIMITS,
        abandoned_after_seconds,
    )

    runs = ImportRun.objects.filter(status=ImportRun.Status.RUNNING).exclude(
        source__in=SELF_RESCHEDULING_IMPORT_SOURCES
    )
    if connection.vendor != "sqlite":
        runs = runs.filter(user_id=user_id)
    # Match close_abandoned_import_runs: a hard-killed worker must not leave
    # Statistics deferred forever. With no task limit, RUNNING is authoritative.
    time_limit = settings.CELERY_TASK_TIME_LIMIT
    if time_limit:
        now = timezone.now()
        alive = Q(
            started_at__gte=now
            - timedelta(seconds=abandoned_after_seconds("", time_limit)),
        )
        for source in SOURCE_TIME_LIMITS:
            alive |= Q(
                source=source,
                started_at__gte=now
                - timedelta(seconds=abandoned_after_seconds(source, time_limit)),
            )
        # A resumed durable run keeps its original provenance/start timestamp,
        # while its fresh lease proves there is still a live writer.
        runs = runs.filter(alive | Q(lease_expires_at__gt=now))
    return runs.exists()


def _setting(name: str, default: int) -> int:
    return max(0, int(getattr(settings, name, default)))


def _task_budget_seconds() -> int:
    return _setting("STATISTICS_SYNC_TASK_BUDGET_SECONDS", 10)


def _lease_seconds() -> int:
    return max(60, _setting("STATISTICS_SYNC_LEASE_SECONDS", 300))


def _slice_days() -> int:
    return max(1, _setting("STATISTICS_SYNC_SLICE_DAYS", 25))


def _heavy_settle_seconds() -> int:
    return _setting("STATISTICS_SYNC_HEAVY_SETTLE_SECONDS", 90)


def _heavy_max_delay_seconds() -> int:
    return _setting("STATISTICS_SYNC_HEAVY_MAX_DELAY_SECONDS", 600)


def _enqueue_gate_seconds() -> int:
    return _setting("STATISTICS_SYNC_ENQUEUE_GATE_SECONDS", 5)


def _gate_key(user_id: int) -> str:
    return f"stats:sync:enqueued:{user_id}"


def _pending_key(user_id: int) -> str:
    # Spans a queued-but-not-started message, unlike the 5-second interactive
    # gate. The task deletes it as soon as it starts, so it only expires when a
    # published message was lost before delivery.
    return f"stats:sync:pending:{user_id}"


def _day_epoch_key(user_id: int) -> str:
    # Written without a TTL, so volatile-lru never evicts it: its absence means
    # the cache was flushed and every day payload has to be checked.
    return f"stats:sync:day_epoch:{user_id}"


def _eager_mode() -> bool:
    return bool(
        getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False)
        or getattr(settings, "TESTING", False),
    )


# ---------------------------------------------------------------------------
# Marking changes
# ---------------------------------------------------------------------------


def _bump_generation(user_id: int, now, *, full_sweep: bool = False) -> bool:
    updates = {"generation": F("generation") + 1, "last_marked_at": now}
    if full_sweep:
        updates["full_sweep_requested_at"] = now
    if StatisticsSyncState.objects.filter(user_id=user_id).update(**updates):
        return True
    try:
        with transaction.atomic():
            StatisticsSyncState.objects.create(
                user_id=user_id,
                generation=1,
                last_marked_at=now,
                full_sweep_requested_at=now,
            )
    except IntegrityError:
        # Raced with another first mark, or the user no longer exists.
        return bool(
            StatisticsSyncState.objects.filter(user_id=user_id).update(**updates)
        )
    return True


def _after_mark(user_id: int, reason: str | None, days: int) -> None:
    # Still the change token for the per-person talent caches.
    _set_history_version(user_id)
    logger.info(
        "stats_mark user_id=%s days=%s reason=%s",
        user_id,
        days,
        reason or "unspecified",
    )
    transaction.on_commit(lambda: ensure_sync(user_id))


def mark_days(user_id: int, day_values, reason: str | None = None) -> int:
    """Mark days dirty, drop their payloads, and make sure a sync follows."""
    if not user_id:
        return 0
    days = {_normalize_day_value(value) for value in day_values or ()}
    days.discard(None)
    now = timezone.now()
    if days:
        rows = [
            StatisticsDirtyDay(
                user_id=user_id, day=day, token=uuid.uuid4(), marked_at=now
            )
            for day in days
        ]
        try:
            StatisticsDirtyDay.objects.bulk_create(
                rows,
                update_conflicts=True,
                unique_fields=["user", "day"],
                update_fields=["token", "marked_at"],
                batch_size=500,
            )
        except IntegrityError:
            logger.warning("stats_mark_failed user_id=%s (user missing?)", user_id)
            return 0
        cache.delete_many([_day_cache_key(user_id, day) for day in days])
    if user_id in _import_changes.get():
        _import_changes.get()[user_id]["marked"] = True
        return len(days)
    if not _bump_generation(user_id, now):
        return 0
    _after_mark(user_id, reason, len(days))
    return len(days)


def mark_aggregate(
    user_id: int, reason: str | None = None, *, full_sweep: bool = False
) -> None:
    """Record a change no single day captures (undated items, scores, status).

    ``full_sweep`` also has the next sync rebuild every day payload the cache
    is missing.
    """
    if not user_id:
        return
    if user_id in _import_changes.get():
        _import_changes.get()[user_id]["marked"] = True
        return
    if not _bump_generation(user_id, timezone.now(), full_sweep=full_sweep):
        return
    _after_mark(user_id, reason, 0)


def _credits_mark_key(user_id: int) -> str:
    return f"stats:credits_mark:{user_id}"


def _credits_trailing_key(user_id: int) -> str:
    return f"stats:credits_trailing:{user_id}"


def request_credits_statistics_refresh(user_id: int) -> bool:
    """Record a credits change, coalesced to one mark per interval.

    A backfill drain completes a batch about every minute; recording each one
    as its own generation made the Statistics sync run back to back for the
    whole backfill, next to the same single SQLite writer (#1706). Mark on the
    leading edge and leave one trailing mark behind so credits written while
    marks are coalesced are still published. Returns True when the generation
    was bumped now.
    """
    if not user_id:
        return False
    interval = int(settings.STATISTICS_CREDITS_MARK_SECONDS)
    if cache.add(_credits_mark_key(user_id), True, interval):
        mark_aggregate(user_id, reason="credits_backfill")
        return True
    if cache.add(_credits_trailing_key(user_id), True, interval):
        from app.tasks_interactive import mark_credits_statistics_refresh

        mark_credits_statistics_refresh.apply_async(
            args=[user_id],
            countdown=interval,
        )
    return False


def mark_credits_statistics_refresh(user_id: int) -> None:
    """Trailing half of the coalescing: mark credits left by throttled batches."""
    if not user_id:
        return
    interval = int(settings.STATISTICS_CREDITS_MARK_SECONDS)
    cache.delete(_credits_trailing_key(user_id))
    # A mark that landed while this task waited already covered the credits;
    # only bump when the interval really lapsed.
    if cache.add(_credits_mark_key(user_id), True, interval):
        mark_aggregate(user_id, reason="credits_backfill")


def mark_rows(user_id: int, rows, reason: str | None = None) -> int:
    """Mark the activity days of rows written without signals (bulk writes)."""
    days = [
        getattr(row, "end_date", None) or getattr(row, "start_date", None)
        for row in rows or ()
    ]
    days = [day for day in days if day]
    if not days:
        mark_aggregate(user_id, reason=reason)
        return 0
    return mark_days(user_id, days, reason=reason)


def request_manual_refresh(user, range_name: str) -> None:
    """Rebuild every day of a range and all ranges, ahead of other syncs.

    The Refresh button's path. Unlike the old refresh it leaves the other
    days' payloads (and custom ranges built from them) alone.
    """
    from app.statistics_refresh import _get_predefined_range_dates, _resolve_day_list

    start_date, end_date = _get_predefined_range_dates(range_name)
    days = _resolve_day_list(user, start_date, end_date)
    mark_days(user.id, days, reason=f"manual_statistics_refresh:{range_name}")
    # A full sweep also makes the heavy ranges due now rather than after the
    # settling window.
    mark_aggregate(user.id, reason="manual_statistics_refresh", full_sweep=True)
    transaction.on_commit(lambda: ensure_sync(user.id, urgent=True))


# ---------------------------------------------------------------------------
# Enqueueing
# ---------------------------------------------------------------------------


def ensure_sync(user_id: int, *, urgent: bool = False, scheduled=False) -> bool:
    """Queue a sync for the user unless one was queued moments ago.

    Losing this message is harmless: the reconciler finds unfinished work. The
    eager/test path skips it and the read path syncs inline instead.

    ``scheduled`` is for the reconciler and its self-follow-up: it queues at
    most one message per user until the task starts, instead of one per tick
    while the message waits behind a busy worker. The pending marker expires
    after the lease window so a message lost before delivery is still recovered.
    """
    if not user_id or _eager_mode():
        return False
    if _bulk_import_active(user_id) or (not urgent and interactive_request_active()):
        return False
    pending_seconds = _lease_seconds()
    if scheduled:
        # The shorter interactive gate would expire between reconciler ticks
        # and cannot show a message still waiting in the queue.
        if not cache.add(_pending_key(user_id), True, pending_seconds):
            return False
    elif not urgent and not cache.add(
        _gate_key(user_id), True, _enqueue_gate_seconds()
    ):
        return False
    try:
        from app.tasks_interactive import statistics_sync_task

        priority = (
            settings.CELERY_TASK_PRIORITY_INTERACTIVE
            if urgent
            else settings.CELERY_TASK_PRIORITY_STATISTICS_SYNC
        )
        # Urgent means the user is waiting (manual refresh, first build), so it
        # keeps the interactive lane. Everything a playback merely marked is
        # bulk work on the background lane.
        queue = INTERACTIVE_QUEUE if urgent else BACKGROUND_QUEUE
        statistics_sync_task.apply_async(args=[user_id], priority=priority, queue=queue)
    except Exception as exc:  # pragma: no cover - broker unavailable
        cache.delete(_gate_key(user_id))
        if scheduled:
            cache.delete(_pending_key(user_id))
        logger.warning("stats_sync_enqueue_failed user_id=%s error=%s", user_id, exc)
        return False
    # Any successful publication counts as pending, so a mark that queued a
    # message debounces the next reconciler tick too.
    cache.set(_pending_key(user_id), True, pending_seconds)
    return True


# ---------------------------------------------------------------------------
# Lease
# ---------------------------------------------------------------------------


def _ensure_state(user_id: int) -> StatisticsSyncState | None:
    state = StatisticsSyncState.objects.filter(user_id=user_id).first()
    if state is not None:
        return state
    try:
        with transaction.atomic():
            return StatisticsSyncState.objects.create(
                user_id=user_id, full_sweep_requested_at=timezone.now()
            )
    except IntegrityError:
        return StatisticsSyncState.objects.filter(user_id=user_id).first()


def _claim_lease(user_id: int, *, takeover: bool) -> str | None:
    """Atomically claim the sync lease; returns the new fencing token.

    The token is rotated on every claim. Lease renewals, snapshot
    publication and synced-generation updates are fenced on it, so a worker
    whose lease expired (or was taken over) fails its next fenced operation
    instead of acting on the successor's sync.
    """
    now = timezone.now()
    token = uuid.uuid4()
    queryset = StatisticsSyncState.objects.filter(user_id=user_id)
    if not takeover:
        queryset = queryset.filter(
            Q(lease_expires_at__isnull=True) | Q(lease_expires_at__lt=now)
        )
    claimed = queryset.update(
        lease_expires_at=now + timedelta(seconds=_lease_seconds()),
        lease_token=token,
        last_started_at=now,
    )
    return token if claimed else None


def sync_is_running(user_id: int) -> bool:
    """Whether a sync holds the user's lease right now."""
    return StatisticsSyncState.objects.filter(
        user_id=user_id, lease_expires_at__gt=timezone.now()
    ).exists()


def _check_lease(user_id: int, lease_token: str | None) -> None:
    """Raise :class:`_LostLeaseError` when a successor claimed the lease."""
    if lease_token is not None and not _renew_lease(user_id, lease_token):
        raise _LostLeaseError


def _renew_lease(user_id: int, lease_token: str | None) -> bool:
    """Extend the lease iff the caller still owns it.

    The UPDATE is the linearization point: a hit means the caller owned the
    lease at that instant (and just extended it), a miss means a successor
    claimed it — including the natural-expiry case, where a successor can
    only have claimed after this lease had already lapsed.
    """
    if lease_token is None:
        return True
    return bool(
        StatisticsSyncState.objects.filter(
            user_id=user_id, lease_token=lease_token
        ).update(lease_expires_at=timezone.now() + timedelta(seconds=_lease_seconds()))
    )


def _release_lease(user_id: int, lease_token: str | None) -> None:
    """Release the lease iff the caller still owns it."""
    if lease_token is None:
        StatisticsSyncState.objects.filter(user_id=user_id).update(
            lease_expires_at=None, last_finished_at=timezone.now()
        )
        return
    StatisticsSyncState.objects.filter(user_id=user_id, lease_token=lease_token).update(
        lease_expires_at=None, last_finished_at=timezone.now()
    )


# ---------------------------------------------------------------------------
# Snapshots: (de)hydration
# ---------------------------------------------------------------------------

_REF = "__stats_ref__"
_TAGS = (
    "__stats_dt__",
    "__stats_date__",
    "__stats_dec__",
    "__stats_td__",
    "__stats_tuple__",
    "__stats_set__",
    "__stats_ns__",
    "__stats_dict__",
)


class _UndehydratableError(TypeError):
    pass


def _dehydrate_attr(value):
    if isinstance(value, (bool, int, float, str, type(None))):
        return value, True
    if isinstance(value, (datetime, date, Decimal)):
        return dehydrate_payload(value), True
    return None, False


def dehydrate_payload(value):
    """Return a JSON-safe copy of a statistics payload.

    Model instances become references re-fetched by ``hydrate_payload``, with
    their plain non-field attributes (annotations such as ``max_progress`` or
    ``aggregated_score``) kept alongside.
    """
    if isinstance(value, Model):
        field_names = {field.attname for field in value._meta.concrete_fields}
        attrs = {}
        for key, attr in value.__dict__.items():
            if key.startswith("_") or key in field_names:
                continue
            encoded, ok = _dehydrate_attr(attr)
            if ok:
                attrs[key] = encoded
        return {_REF: value._meta.label_lower, "pk": value.pk, "attrs": attrs}
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float, str)):
        return value
    if isinstance(value, datetime):
        return {"__stats_dt__": value.isoformat()}
    if isinstance(value, date):
        return {"__stats_date__": value.isoformat()}
    if isinstance(value, Decimal):
        return {"__stats_dec__": str(value)}
    if isinstance(value, timedelta):
        return {"__stats_td__": value.total_seconds()}
    if isinstance(value, dict):
        if all(isinstance(key, str) for key in value):
            return {key: dehydrate_payload(item) for key, item in value.items()}
        return {
            "__stats_dict__": [
                [dehydrate_payload(key), dehydrate_payload(item)]
                for key, item in value.items()
            ]
        }
    if isinstance(value, list):
        return [dehydrate_payload(item) for item in value]
    if isinstance(value, tuple):
        return {"__stats_tuple__": [dehydrate_payload(item) for item in value]}
    if isinstance(value, (set, frozenset)):
        return {"__stats_set__": [dehydrate_payload(item) for item in value]}
    if isinstance(value, SimpleNamespace):
        return {"__stats_ns__": dehydrate_payload(vars(value))}
    msg = f"cannot store {type(value).__name__} in a statistics snapshot"
    raise _UndehydratableError(msg)


def _collect_refs(value, refs):
    if isinstance(value, dict):
        if _REF in value:
            refs.setdefault(value[_REF], set()).add(value["pk"])
            return
        for item in value.values():
            _collect_refs(item, refs)
    elif isinstance(value, list):
        for item in value:
            _collect_refs(item, refs)


def _fetch_instances(refs):
    instances = {}
    for label, pks in refs.items():
        model = apps.get_model(label)
        related = {field.name for field in model._meta.get_fields()}
        item_paths = ["item"] if "item" in related else []
        if model._meta.model_name == "season":
            item_paths.append("related_tv__item")
        elif model._meta.model_name == "episode":
            item_paths += ["related_season__item", "related_season__related_tv__item"]
        # The hydrated payload is cached; keep the wide item columns no card
        # reads out of it, as the aggregator does when it builds the payload.
        queryset = model.objects.filter(pk__in=pks)
        if item_paths:
            # select_related() with no names would follow every relation.
            queryset = queryset.select_related(*item_paths).defer(
                *history_deferred_item_fields(*item_paths)
            )
        for instance in queryset:
            instances[(label, instance.pk)] = instance
    return instances


def _hydrate(value, instances):
    if isinstance(value, list):
        hydrated = [_hydrate(item, instances) for item in value]
        # A referenced row deleted since publishing is dropped from its list.
        return [item for item in hydrated if item is not _MISSING]
    if not isinstance(value, dict):
        return value
    if _REF in value:
        instance = instances.get((value[_REF], value["pk"]))
        if instance is None:
            return _MISSING
        for key, attr in (value.get("attrs") or {}).items():
            setattr(instance, key, _hydrate(attr, instances))
        return instance
    if len(value) == 1:
        tag, inner = next(iter(value.items()))
        if tag in _TAGS:
            return _hydrate_tag(tag, inner, instances)
    hydrated = {}
    for key, item in value.items():
        result = _hydrate(item, instances)
        if result is _MISSING:
            # A card whose row was deleted since publishing drops as a whole.
            return _MISSING
        hydrated[key] = result
    return hydrated


def _hydrate_tag(tag, inner, instances):
    if tag == "__stats_dt__":
        return datetime.fromisoformat(inner)
    if tag == "__stats_date__":
        return date.fromisoformat(inner)
    if tag == "__stats_dec__":
        return Decimal(inner)
    if tag == "__stats_td__":
        return timedelta(seconds=inner)
    if tag == "__stats_tuple__":
        return tuple(_hydrate(item, instances) for item in inner)
    if tag == "__stats_set__":
        return {_hydrate(item, instances) for item in inner}
    if tag == "__stats_ns__":
        return SimpleNamespace(**_hydrate(inner, instances))
    return {_hydrate(key, instances): _hydrate(item, instances) for key, item in inner}


_MISSING = object()


def hydrate_payload(payload):
    """Rebuild a payload stored by ``dehydrate_payload``."""
    refs: dict[str, set] = {}
    _collect_refs(payload, refs)
    return _hydrate(payload, _fetch_instances(refs))


def _snapshot_entry(data, generation, built_day, built_at, schema_version):
    return {
        "data": data,
        "built_at": built_at,
        "built_day": built_day,
        "generation": generation,
        "schema_version": schema_version,
    }


_SNAPSHOT_META_FIELDS = ("built_at", "built_day", "generation", "schema_version")


def publish_snapshot(
    user_id: int,
    range_name: str,
    data: dict,
    generation: int,
    lease_token: str | None = None,
):
    """Publish durably to the database, with a revision-checked cache copy.

    With ``lease_token`` the publication is fenced: the ownership check (a
    lease renewal) and the snapshot write run inside one transaction, and
    the snapshot write itself never moves a range to an older generation.
    A worker failing the ownership check publishes nothing. Redis writes
    can arrive late or precede an outer transaction's rollback; load_snapshot
    verifies them against the durable revision before returning them.
    Callers without a lease may omit the token; their database writes still
    cannot regress a newer generation.
    """
    from app.statistics_cache import (
        STATISTICS_RANGE_CACHE_TIMEOUT,
        _cache_key,
        _normalize_hours_per_media_type,
    )

    _normalize_hours_per_media_type(data.get("hours_per_media_type"))
    built_at = timezone.now()
    built_day = timezone.localdate()
    entry = _snapshot_entry(
        data, generation, built_day, built_at, SNAPSHOT_SCHEMA_VERSION
    )
    try:
        with transaction.atomic():
            if lease_token is not None and not _renew_lease(user_id, lease_token):
                raise _LostLeaseError
            persisted = _persist_snapshot(
                user_id,
                range_name,
                {
                    "payload": dehydrate_payload(data),
                    "built_day": built_day,
                    "built_at": built_at,
                    "schema_version": SNAPSHOT_SCHEMA_VERSION,
                },
                generation,
            )
    except _UndehydratableError as exc:
        logger.warning(
            "stats_snapshot_not_persisted user_id=%s range=%s error=%s",
            user_id,
            range_name,
            exc,
        )
        cache.set(
            _cache_key(user_id, range_name),
            entry,
            timeout=STATISTICS_RANGE_CACHE_TIMEOUT,
        )
        return entry
    if persisted:
        # This atomic block won, but an outer transaction may still roll
        # back and another writer may publish before this cache write.
        # load_snapshot validates the durable revision on every read.
        cache.set(
            _cache_key(user_id, range_name),
            entry,
            timeout=STATISTICS_RANGE_CACHE_TIMEOUT,
        )
    else:
        logger.info(
            "stats_snapshot_superseded user_id=%s range=%s generation=%s",
            user_id,
            range_name,
            generation,
        )
    return entry


def _persist_snapshot(
    user_id: int, range_name: str, fields: dict, generation: int
) -> bool:
    """Write a snapshot unless a newer generation is already published."""
    updated = StatisticsSnapshot.objects.filter(
        user_id=user_id, range_name=range_name, generation__lte=generation
    ).update(generation=generation, **fields)
    if updated:
        return True
    try:
        StatisticsSnapshot.objects.create(
            user_id=user_id, range_name=range_name, generation=generation, **fields
        )
    except IntegrityError:
        # A row appeared with a newer generation between the update and the
        # insert; leave it alone.
        return False
    return True


def load_snapshot(user_id: int, range_name: str) -> dict | None:
    """Return a cache entry only when it matches the durable publication.

    Database commit and Redis publication cannot be atomic. A delayed writer
    or a rolled-back outer transaction can leave any revision in Redis. Read
    the small snapshot metadata first; only load its payload on a cache miss.
    ``built_at`` also distinguishes rebuilds within the same generation.
    """
    from app.statistics_cache import STATISTICS_RANGE_CACHE_TIMEOUT, _cache_key

    key = _cache_key(user_id, range_name)
    snapshot = (
        StatisticsSnapshot.objects.filter(user_id=user_id, range_name=range_name)
        .defer("payload")
        .first()
    )
    if snapshot is None:
        return None
    entry = cache.get(key)
    if (
        isinstance(entry, dict)
        and entry.get("generation") == snapshot.generation
        and entry.get("built_at") == snapshot.built_at
        and entry.get("schema_version") == snapshot.schema_version
    ):
        return entry
    # A writer may publish between the metadata read and this cache miss.
    # Fetch payload and metadata together rather than lazily loading only
    # payload onto an object that still carries the previous revision.
    snapshot = StatisticsSnapshot.objects.filter(
        user_id=user_id, range_name=range_name
    ).first()
    if snapshot is None:
        return None
    entry = _snapshot_entry(
        hydrate_payload(snapshot.payload),
        snapshot.generation,
        snapshot.built_day,
        snapshot.built_at,
        snapshot.schema_version,
    )
    cache.set(key, entry, timeout=STATISTICS_RANGE_CACHE_TIMEOUT)
    return entry


def load_snapshot_meta(user_id: int, range_name: str) -> dict | None:
    """Read durable metadata without loading or unpickling the range payload.

    A separate Redis metadata key can outlive a rolled-back publication or
    lag behind a successor. This small indexed query preserves the poller's
    bounded read while using the same committed revision as load_snapshot.
    """
    return (
        StatisticsSnapshot.objects.filter(user_id=user_id, range_name=range_name)
        .values(*_SNAPSHOT_META_FIELDS)
        .first()
    )


def current_generation(user_id: int) -> int:
    """Return the user's change generation (0 before their first change)."""
    value = (
        StatisticsSyncState.objects.filter(user_id=user_id)
        .values_list("generation", flat=True)
        .first()
    )
    return value or 0


def entry_is_stale(entry, generation: int | None = None, user_id=None) -> bool:
    """Whether a published entry trails the user's changes or today's date."""
    if not isinstance(entry, dict) or "generation" not in entry:
        return True
    if entry.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        return True
    if _normalize_day_value(entry.get("built_day")) != timezone.localdate():
        return True
    if generation is None:
        generation = current_generation(user_id)
    return int(entry.get("generation") or 0) < generation


# ---------------------------------------------------------------------------
# The sync
# ---------------------------------------------------------------------------


class _OutOfTimeError(Exception):
    pass


class _LostLeaseError(Exception):
    """The sync's lease was claimed by a successor; stop touching its state."""


def _check_deadline(deadline) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise _OutOfTimeError


def _yield_if_interactive(enabled: bool, user_id: int | None = None) -> None:
    if enabled and (
        interactive_request_active()
        or higher_priority_task_waiting(
            INTERACTIVE_QUEUE, settings.CELERY_TASK_PRIORITY_INTERACTIVE
        )
        or (user_id is not None and _bulk_import_active(user_id))
    ):
        raise _OutOfTimeError


def _range_cost_key(user_id: int, range_name: str) -> str:
    return f"stats:sync:range_seconds:{user_id}:{range_name}"


def _check_range_fits(user_id: int, range_name: str, deadline, built_one: bool):
    """Stop, unless the range is expected to finish inside the budget that is left.

    The first range a task builds always runs, so a range that is dearer than a
    whole budget still gets a task of its own instead of never being built.
    """
    if deadline is None or not built_one:
        return
    expected = cache.get(_range_cost_key(user_id, range_name))
    if expected and time.monotonic() + expected > deadline:
        raise _OutOfTimeError


def _ordered_ranges(user, heavy_due: bool, only_ranges=None) -> list[str]:
    if only_ranges:
        return list(only_ranges)
    # The cheap ranges first, so a change shows up in seconds. A heavy default
    # range waits for changes to settle like the others (rebuilding All Time on
    # every pass while a backfill drains kept the worker busy for nothing), but
    # goes first among them.
    preferred = getattr(user, "statistics_default_range", None)
    ordered = list(HOT_RANGES)
    if heavy_due and preferred in HEAVY_RANGES:
        ordered.append(preferred)
    if heavy_due:
        ordered.extend(name for name in HEAVY_RANGES if name not in ordered)
    return ordered


def _heavy_due(state, now, *, full: bool, today) -> bool:
    if full or state.synced_day != today:
        return True
    if state.heavy_synced_generation >= state.generation:
        return False
    settled = state.last_marked_at is None or state.last_marked_at <= now - timedelta(
        seconds=_heavy_settle_seconds()
    )
    capped = state.heavy_synced_at is None or state.heavy_synced_at <= now - timedelta(
        seconds=_heavy_max_delay_seconds()
    )
    return settled or capped


def _missing_days(user_id: int, days) -> set:
    missing = set()
    days = list(days)
    for offset in range(0, len(days), 200):
        chunk = days[offset : offset + 200]
        keys = [_day_cache_key(user_id, day) for day in chunk]
        cached = cache.get_many(keys)
        missing.update(
            day for day, key in zip(chunk, keys, strict=False) if key not in cached
        )
    return missing


def _build_days(
    user,
    days,
    deadline,
    dirty_tokens,
    *,
    yield_to_interactive=False,
    lease_token: str | None = None,
) -> int:
    """Build day payloads in slices; clear each slice's dirty rows as it lands."""
    from app.statistics_day_builder import (
        _build_prefetch_for_range,
        build_stats_for_day,
    )
    from app.statistics_refresh import _enqueue_collected_backfills

    credit_hints = 0
    size = _slice_days()
    for offset in range(0, len(days), size):
        _yield_if_interactive(yield_to_interactive, user.id)
        _check_deadline(deadline)
        if lease_token is not None and not _renew_lease(user.id, lease_token):
            raise _LostLeaseError
        chunk = days[offset : offset + size]
        prefetch = _build_prefetch_for_range(user, chunk)
        collector = {
            "runtime_item_ids": set(),
            "genre_item_ids": set(),
            "episode_runtime_keys": set(),
            "credit_item_ids": set(),
        }
        pending = {}
        processed_days = []
        deferred = False
        for day in chunk:
            if processed_days and (
                (deadline is not None and time.monotonic() >= deadline)
                or (yield_to_interactive and interactive_request_active())
            ):
                deferred = True
                break
            day_stats = build_stats_for_day(
                user.id,
                day,
                user=user,
                prefetch=prefetch,
                defer_cache_write=True,
                backfill_collector=collector,
            )
            if day_stats:
                pending[_day_cache_key(user.id, day)] = day_stats
                credit_hints += int(
                    day_stats.get("backfill", {}).get("missing_credits") or 0
                )
            processed_days.append(day)
        # Building a slice can outlive its lease. Renew under a transaction
        # immediately before publishing; the state-row write lock prevents a
        # successor from claiming until the day writes and clearing finish.
        with transaction.atomic():
            _check_lease(user.id, lease_token)
            if pending:
                failed = set(
                    cache.set_many(pending, timeout=STATISTICS_DAY_CACHE_TIMEOUT) or ()
                )
                if failed:
                    cache.set_many(
                        {key: pending[key] for key in failed},
                        timeout=STATISTICS_DAY_CACHE_TIMEOUT,
                    )
            _clear_dirty(user.id, processed_days, dirty_tokens)
        _enqueue_collected_backfills(user.id, collector)
        if deferred:
            raise _OutOfTimeError
    return credit_hints


def _clear_dirty(user_id: int, days, dirty_tokens) -> None:
    query = Q()
    matched = False
    for day in days:
        token = dirty_tokens.get(day)
        if token is not None:
            query |= Q(day=day, token=token)
            matched = True
    if matched:
        StatisticsDirtyDay.objects.filter(query, user_id=user_id).delete()


_NOT_A_PHASE = ("days_ms",)


def _phase_summary(timings: dict) -> str:
    """Wall milliseconds per aggregate phase, as ``phase:ms`` pairs."""
    return ",".join(
        f"{key[:-3]}:{value:.0f}"
        for key, value in timings.items()
        if key.endswith("_ms")
        and not key.endswith("_cpu_ms")
        and key not in _NOT_A_PHASE
    )


def _aggregate_range(
    user,
    range_name: str,
    credit_hints: int,
    deadline=None,
    lease_token: str | None = None,
    timings: dict | None = None,
    pass_memo: dict | None = None,
) -> dict:
    from app.statistics_aggregator import _aggregate_statistics_from_days
    from app.statistics_highlights import normalize_highlight_images
    from app.statistics_refresh import _get_predefined_range_dates, _resolve_day_list

    timings = {} if timings is None else timings
    start_date, end_date = _get_predefined_range_dates(range_name)
    resolve_started = time.monotonic()
    day_list = _resolve_day_list(user, start_date, end_date)
    timings["resolve_days_ms"] = (time.monotonic() - resolve_started) * 1000
    # A bounded range walks every calendar day, and a day never built (most
    # are empty) would otherwise be built one at a time inside the aggregate,
    # ~13 queries each. The aggregate reports the days its own read did not
    # find, a batch at a time, and they are built here with one prefetch per
    # slice. Checking beforehand instead decoded every day payload twice.
    timings["missing_days"] = 0
    timings["days_ms"] = 0.0

    def build_missing(days):
        timings["missing_days"] += len(days)
        build_started = time.monotonic()
        try:
            return _build_days(
                user,
                sorted(days, reverse=True),
                deadline,
                {},
                lease_token=lease_token,
            )
        finally:
            timings["days_ms"] += (time.monotonic() - build_started) * 1000

    data = _aggregate_statistics_from_days(
        user,
        day_list,
        start_date,
        end_date,
        build_missing=True,
        credit_backfill_hints=credit_hints,
        timings=timings,
        pass_memo=pass_memo,
        build_days=build_missing,
    )
    # Cache-only: artwork the cache lacks is fetched by the background worker.
    images_started = time.monotonic()
    normalize_highlight_images(data)
    timings["images_ms"] = (time.monotonic() - images_started) * 1000
    return data


def run_sync(
    user_id: int,
    *,
    budget_seconds: float | None = None,
    only_ranges=None,
    takeover: bool = False,
) -> dict:
    """Bring a user's day payloads and range snapshots up to date.

    Returns ``{"status": ..., "published": {range: data}}`` where status is
    ``done``, ``busy`` (another sync holds the lease), ``continued`` (the time
    budget ran out), ``deferred`` (an interactive request is active),
    ``lost_lease`` (a successor claimed the lease mid-run; its fenced
    operations stopped touching the user's state), ``locked`` (SQLite stayed
    locked past the wait; the reconciler retries), or ``missing``.

    ``only_ranges`` rebuilds just those ranges, unconditionally; it is the
    inline path the eager/test read and the top-talent upgrade use.
    """
    started = time.monotonic()
    deadline = None if budget_seconds is None else started + budget_seconds
    yield_to_interactive = budget_seconds is not None and only_ranges is None
    if yield_to_interactive and (
        interactive_request_active() or _bulk_import_active(user_id)
    ):
        return {"status": "deferred", "published": {}}
    user_model = apps.get_model(settings.AUTH_USER_MODEL)
    user = user_model.objects.filter(pk=user_id).first()
    if user is None:
        return {"status": "missing", "published": {}}
    try:
        if _ensure_state(user_id) is None:
            return {"status": "missing", "published": {}}
        lease_token = _claim_lease(user_id, takeover=takeover)
    except OperationalError as exc:
        if not is_lock_error(exc):
            raise
        logger.warning("stats_sync_locked user_id=%s error=%s", user_id, exc)
        return {"status": "locked", "published": {}}
    if lease_token is None:
        return {"status": "busy", "published": {}}

    published: dict[str, dict] = {}
    range_seconds_by_name: dict[str, float] = {}
    days_built = 0
    days_seconds = 0.0
    status = "done"
    now = timezone.now()
    today = timezone.localdate()
    try:
        state = StatisticsSyncState.objects.get(user_id=user_id)
        generation = state.generation
        day_epoch = cache.get(_day_epoch_key(user_id))
        full = state.full_sweep_requested_at is not None or day_epoch is None

        if state.full_sweep_requested_at is not None:
            sweep_epoch = state.full_sweep_requested_at.isoformat()
            if day_epoch != sweep_epoch:
                from app.statistics_refresh import _get_sparse_activity_days

                # Prepare once per full-sweep request. Durable dirty tokens
                # retain unfinished days across continuations and worker death;
                # a lost cache marker only repeats this safe preparation.
                sweep_days = _get_sparse_activity_days(user)
                # The scan can outlive the lease. Renew inside the write
                # transaction so a worker that lost it cannot rotate dirty
                # tokens or delete day payloads its successor published.
                with transaction.atomic():
                    _check_lease(user_id, lease_token)
                    StatisticsDirtyDay.objects.bulk_create(
                        [
                            StatisticsDirtyDay(
                                user_id=user_id,
                                day=day,
                                token=uuid.uuid4(),
                                marked_at=now,
                            )
                            for day in sweep_days
                        ],
                        update_conflicts=True,
                        unique_fields=["user", "day"],
                        update_fields=["token", "marked_at"],
                        batch_size=500,
                    )
                    cache.delete_many(
                        [_day_cache_key(user_id, day) for day in sweep_days]
                    )
                    cache.set(_day_epoch_key(user_id), sweep_epoch, timeout=None)

        dirty_tokens = dict(
            StatisticsDirtyDay.objects.filter(user_id=user_id).values_list(
                "day", "token"
            )
        )
        work = set(dirty_tokens)
        warm_days = [today - timedelta(days=offset) for offset in range(WARM_DAY_COUNT)]
        work.update(
            _missing_days(user_id, warm_days)
            if state.full_sweep_requested_at is not None
            else warm_days
        )
        if full:
            from app.statistics_refresh import _get_sparse_activity_days

            work.update(_missing_days(user_id, _get_sparse_activity_days(user)))
        # Repairs scores written without signals (legacy data). It scans every
        # reading row, so it runs once a day and on explicit rebuilds, not on
        # every change.
        stale_scan_key = f"statistics_stale_scan:{user_id}:{today.isoformat()}"
        scan_stale = full or only_ranges or state.synced_day != today
        if scan_stale and not (full or only_ranges) and cache.get(stale_scan_key):
            # An earlier pass today already scanned and recorded what it
            # found; its dirty rows carry the progress. Rescanning would
            # rediscover days it just rebuilt.
            scan_stale = False
        if scan_stale:
            from app.statistics_cache import _collect_stale_reading_score_days

            stale_days = set(_collect_stale_reading_score_days(user))
            work.update(stale_days)
            new_dirty = stale_days - set(dirty_tokens)
            if deadline is not None and not only_ranges:
                # Found by a scan, not by a write, so nothing records that
                # they are unfinished. On a host slower than the budget allows
                # for them, every continuation would rescan, rebuild the same
                # days from the start and never reach the ranges or settle
                # ``synced_day``. Make them durable so each slice's progress
                # sticks, and scan once a day per user.
                if new_dirty:
                    with transaction.atomic():
                        _check_lease(user_id, lease_token)
                        StatisticsDirtyDay.objects.bulk_create(
                            [
                                StatisticsDirtyDay(
                                    user_id=user_id,
                                    day=day,
                                    token=uuid.uuid4(),
                                    marked_at=now,
                                )
                                for day in new_dirty
                            ],
                            update_conflicts=True,
                            unique_fields=["user", "day"],
                            update_fields=["token", "marked_at"],
                            batch_size=500,
                        )
                    dirty_tokens = dict(
                        StatisticsDirtyDay.objects.filter(user_id=user_id).values_list(
                            "day", "token"
                        )
                    )
                cache.set(stale_scan_key, 1, timeout=86400)
        # Dirty days first, newest first: repeatedly warming Today must not
        # consume every continuation's budget before older dirty days progress.
        work_days = sorted(set(dirty_tokens), reverse=True) + sorted(
            work - set(dirty_tokens), reverse=True
        )

        days_built = len(work_days)
        days_started = time.monotonic()
        try:
            credit_hints = _build_days(
                user,
                work_days,
                deadline,
                dirty_tokens,
                yield_to_interactive=yield_to_interactive,
                lease_token=lease_token,
            )
        finally:
            days_seconds = time.monotonic() - days_started
        if full:
            with transaction.atomic():
                _check_lease(user_id, lease_token)
                cache.set(
                    _day_epoch_key(user_id),
                    state.full_sweep_requested_at.isoformat()
                    if state.full_sweep_requested_at is not None
                    else now.isoformat(),
                    timeout=None,
                )

        snapshots = {
            snapshot.range_name: snapshot
            for snapshot in StatisticsSnapshot.objects.filter(user_id=user_id).only(
                "range_name", "generation", "built_day", "schema_version"
            )
        }
        # A schema bump must reach the heavy ranges too, even when the
        # generation and day markers say they were synced already.
        heavy_due = _heavy_due(state, now, full=full, today=today) or any(
            snapshot.schema_version != SNAPSHOT_SCHEMA_VERSION
            for snapshot in snapshots.values()
        )

        # Lookups the ranges of this pass share. In memory for this pass
        # only: never cached, never published.
        pass_memo: dict = {}

        def needs_rebuild(range_name):
            snapshot = snapshots.get(range_name)
            return (
                snapshot is None
                or snapshot.generation < generation
                or snapshot.built_day != today
                or snapshot.schema_version != SNAPSHOT_SCHEMA_VERSION
            )

        for range_name in _ordered_ranges(user, heavy_due, only_ranges):
            if not only_ranges and not needs_rebuild(range_name):
                continue
            _yield_if_interactive(yield_to_interactive, user.id)
            _check_deadline(deadline)
            if not only_ranges:
                _check_range_fits(user_id, range_name, deadline, bool(published))
            range_started = time.monotonic()
            range_timings: dict[str, float] = {}
            data = _aggregate_range(
                user,
                range_name,
                credit_hints,
                deadline,
                lease_token=lease_token,
                timings=range_timings,
                pass_memo=pass_memo,
            )
            publish_snapshot(
                user_id, range_name, data, generation, lease_token=lease_token
            )
            published[range_name] = data
            _check_lease(user_id, lease_token)
            range_seconds = time.monotonic() - range_started
            range_seconds_by_name[range_name] = range_seconds
            cache.set(
                _range_cost_key(user_id, range_name),
                range_seconds,
                timeout=RANGE_COST_TIMEOUT,
            )
            logger.info(
                "stats_range_summary user_id=%s range=%s generation=%s elapsed_ms=%.2f "
                "missing_days=%s days_ms=%.2f days_decoded=%s phases_ms=%s",
                user_id,
                range_name,
                generation,
                range_seconds * 1000,
                range_timings.get("missing_days", 0),
                range_timings.get("days_ms", 0.0),
                range_timings.get("days_decoded", 0),
                _phase_summary(range_timings),
            )

        # Cleared only once the pass completes: a continuation must still see
        # the full sweep, or the heavy ranges it made due would be skipped.
        # Fenced on the lease and bounded by this pass's start so a worker
        # that lost its lease cannot clear a newer request, and a request
        # raised after this pass started is left for the successor.
        if full and not only_ranges:
            StatisticsSyncState.objects.filter(
                user_id=user_id,
                lease_token=lease_token,
                full_sweep_requested_at__lte=now,
            ).update(full_sweep_requested_at=None)
        if not only_ranges:
            # Monotone: a stale pass (or an equal-generation predecessor)
            # cannot pull the synced markers back below what a successor
            # already recorded.
            updates = {
                "hot_synced_generation": Greatest(
                    Coalesce(F("hot_synced_generation"), 0), generation
                ),
                "last_error": "",
            }
            if heavy_due:
                updates.update(
                    heavy_synced_generation=Greatest(
                        Coalesce(F("heavy_synced_generation"), 0), generation
                    ),
                    heavy_synced_at=timezone.now(),
                    synced_day=Greatest(Coalesce(F("synced_day"), today), today),
                )
            StatisticsSyncState.objects.filter(
                user_id=user_id, lease_token=lease_token
            ).update(**updates)
    except _LostLeaseError:
        status = "lost_lease"
    except _OutOfTimeError:
        status = "continued"
    except Exception as exc:
        if isinstance(exc, OperationalError) and is_lock_error(exc):
            # Another writer held the database past the wait. Nothing is lost:
            # the dirty days and generation are durable, and the reconciler
            # queues a fresh sync within a minute.
            status = "locked"
            logger.warning("stats_sync_locked user_id=%s error=%s", user_id, exc)
        else:
            StatisticsSyncState.objects.filter(
                user_id=user_id, lease_token=lease_token
            ).update(last_error=f"{type(exc).__name__}: {exc}"[:1000])
            raise
    finally:
        try:
            _release_lease(user_id, lease_token)
        except OperationalError as exc:
            if not is_lock_error(exc):
                raise
            # The lease lapses on its own, after which the reconciler can claim.
            logger.warning(
                "stats_sync_release_locked user_id=%s error=%s", user_id, exc
            )

    if status == "locked":
        # Queueing a follow-up now would just meet the same lock.
        return {"status": status, "published": published}

    elapsed = time.monotonic() - started
    logger.info(
        "stats_sync user_id=%s status=%s ranges=%s elapsed_ms=%.2f days=%s days_ms=%.2f",
        user_id,
        status,
        len(published),
        elapsed * 1000,
        days_built,
        days_seconds * 1000,
    )
    if (
        budget_seconds is not None
        and elapsed > budget_seconds + OVERRUN_WARNING_SECONDS
    ):
        # Names where the time went: the range aggregate is the one step that
        # cannot stop partway, so it is the usual overrun.
        slowest = max(
            range_seconds_by_name.items(), key=lambda pair: pair[1], default=("-", 0.0)
        )
        logger.warning(
            "stats_sync_overrun user_id=%s budget_s=%s elapsed_ms=%.2f days_ms=%.2f slowest_range=%s slowest_range_ms=%.2f",
            user_id,
            budget_seconds,
            elapsed * 1000,
            days_seconds * 1000,
            slowest[0],
            slowest[1] * 1000,
        )
    if (
        not only_ranges
        and not (yield_to_interactive and interactive_request_active())
        and (status == "continued" or _has_hot_work(user_id))
    ):
        ensure_sync(user_id, scheduled=True)
    return {"status": status, "published": published}


def _has_hot_work(user_id: int) -> bool:
    """Whether changes landed after this sync read the state."""
    if StatisticsDirtyDay.objects.filter(user_id=user_id).exists():
        return True
    return StatisticsSyncState.objects.filter(
        user_id=user_id, hot_synced_generation__lt=F("generation")
    ).exists()


def sync_task_body(user_id: int) -> dict:
    """Body of the Celery sync task: bounded by the task budget."""
    cache.delete(_gate_key(user_id))
    cache.delete(_pending_key(user_id))
    return run_sync(user_id, budget_seconds=_task_budget_seconds())


def refresh_range_inline(user_id: int, range_name: str):
    """Rebuild one range in this process and return its payload."""
    result = run_sync(user_id, only_ranges=[range_name])
    if result["status"] in ("busy", "lost_lease", "locked"):
        # Preserve the running worker's lease and leave durable work for its
        # continuation/reconciler, including same-generation schema upgrades.
        mark_aggregate(user_id, reason="inline_refresh_deferred", full_sweep=True)
    return result["published"].get(range_name)


# ---------------------------------------------------------------------------
# Reconciler
# ---------------------------------------------------------------------------


def users_needing_sync(limit: int | None = None) -> list[int]:
    """Users with unfinished Statistics work and no live sync."""
    now = timezone.now()
    today = timezone.localdate()
    settle_cutoff = now - timedelta(seconds=_heavy_settle_seconds())
    cap_cutoff = now - timedelta(seconds=_heavy_max_delay_seconds())
    heavy_behind = Q(heavy_synced_generation__lt=F("generation"))
    dirty_users = StatisticsDirtyDay.objects.values("user_id")
    queryset = (
        StatisticsSyncState.objects.filter(
            Q(lease_expires_at__isnull=True) | Q(lease_expires_at__lt=now)
        )
        .filter(
            Q(hot_synced_generation__lt=F("generation"))
            | Q(full_sweep_requested_at__isnull=False)
            | Q(synced_day__isnull=True)
            | Q(synced_day__lt=today)
            | Q(user_id__in=dirty_users)
            | (heavy_behind & Q(last_marked_at__lte=settle_cutoff))
            | (heavy_behind & Q(heavy_synced_at__isnull=True))
            | (heavy_behind & Q(heavy_synced_at__lte=cap_cutoff))
        )
        .order_by(F("last_finished_at").asc(nulls_first=True))
        .values_list("user_id", flat=True)
    )
    if limit:
        queryset = queryset[:limit]
    return list(queryset)


def _bootstrap_states() -> int:
    """Give every active user a sync state, so upgrades warm without a visit."""
    user_model = apps.get_model(settings.AUTH_USER_MODEL)
    missing = user_model.objects.filter(is_active=True).exclude(
        pk__in=StatisticsSyncState.objects.values("user_id")
    )
    now = timezone.now()
    rows = [
        StatisticsSyncState(user_id=pk, full_sweep_requested_at=now)
        for pk in missing.values_list("pk", flat=True)
    ]
    StatisticsSyncState.objects.bulk_create(rows, ignore_conflicts=True)
    return len(rows)


def reconcile() -> int:
    """Queue a sync for every user with unfinished work. Returns the count."""
    _bootstrap_states()
    limit = _setting("STATISTICS_SYNC_RECONCILE_USER_LIMIT", 100) or None
    user_ids = users_needing_sync(limit)
    queued = sum(1 for user_id in user_ids if ensure_sync(user_id, scheduled=True))
    if user_ids:
        logger.info("stats_reconcile candidates=%s queued=%s", len(user_ids), queued)
    return queued
