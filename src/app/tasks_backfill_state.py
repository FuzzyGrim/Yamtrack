"""Backfill state management, retry tracking, and shared ID utilities.

Extracted from tasks.py. Re-exported from app.tasks for backward compatibility.
"""

import logging
from collections import defaultdict
from datetime import timedelta

import requests
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from app import history_cache
from app.models import (
    CREDITS_BACKFILL_VERSION,
    Item,
    MediaTypes,
    MetadataBackfillField,
    MetadataBackfillState,
)

logger = logging.getLogger(__name__)

BACKGROUND_TASK_PRIORITY = getattr(settings, "CELERY_TASK_PRIORITY_BACKGROUND", 9)

METADATA_BACKFILL_BASE_DELAY_SECONDS = 60 * 60  # 1 hour
METADATA_BACKFILL_MAX_DELAY_SECONDS = 60 * 60 * 24  # 1 day
METADATA_BACKFILL_MAX_ATTEMPTS = 6
# "The provider answered and has nothing" (no runtime, no release date) does not
# change within hours, so retrying it on the first rungs of the ladder (1 h, 2 h,
# 4 h ...) only repeats the same empty answer.
METADATA_BACKFILL_EMPTY_ANSWER_DELAY_SECONDS = 60 * 60 * 24  # 1 day
# A pending item never gives up (its data may appear months later), so its
# backoff keeps growing past the failure ladder's one-day cap. Without this, a
# season TMDB has no air date for was fetched again every day, indefinitely.
METADATA_BACKFILL_PENDING_MAX_DELAY_SECONDS = 60 * 60 * 24 * 14  # 2 weeks
GENRE_BACKFILL_VERSION = 4
# Bumped when MAL anime became eligible for TMDB watch-provider enrichment.
WATCH_PROVIDERS_BACKFILL_VERSION = 3
EXTERNAL_IDS_BACKFILL_VERSION = 1
# Bump either to re-open every item whose release/status backfill was given up
# on or backed off - e.g. when the fetch strategy for those fields changes.
RELEASE_BACKFILL_VERSION = 1
STATUS_BACKFILL_VERSION = 1

# Provider responses that mean "this identifier will never resolve", as opposed
# to "the provider is having a bad day". A 401/403 is deliberately absent: those
# are usually a missing or expired API key, which a retry can recover from.
TERMINAL_PROVIDER_STATUS_CODES = frozenset({400, 404, 410, 422})


class MalformedItemIdentityError(ValueError):
    """An item cannot be fetched because its own identity is incomplete.

    A season row with no season number can never be fetched, no matter how
    healthy the provider is. This is deliberately its own type rather than a
    bare ``ValueError``: the fetch path raises ``ValueError`` for transient
    reasons too - ``tvdb._request`` raises one when TVDB credentials are not
    configured - and retiring every TVDB item because a key lapsed is exactly
    the failure this classification exists to avoid.
    """


def is_terminal_backfill_error(exc: BaseException) -> bool:
    """Report whether re-fetching this item could plausibly change the answer.

    Production evidence (2026-09-14): a single backfill pass processed 150
    items in 144 seconds and 148 of them failed, overwhelmingly MusicBrainz
    400/404 for recording ids that do not exist. Treating those the same as a
    provider outage meant the same dead ids were fetched on every cycle.

    Only two things are terminal: a provider that answered "this identifier is
    wrong", and an item whose own identity is unusable. Everything else -
    including an unconfigured provider, and any exception this code did not
    anticipate - stays retryable, because the cost of wrongly retrying is one
    request and the cost of wrongly retiring is silent permanent data loss.

    Production evidence (2026-09-20): passes were still reporting 149 failures
    out of 150 every fifteen minutes. ``services.api_request`` deliberately
    re-raises a bare ``requests.exceptions.HTTPError`` for any 4xx it does not
    retry, leaving each provider to wrap it in ``handle_error``; musicbrainz,
    trakt and tvmaze never did. So a MusicBrainz 404 arrived here as a raw
    ``HTTPError``, matched neither branch, and was retried forever. The status
    set is the same either way - this makes the unwrapped path behave like the
    wrapped one rather than making anything newly terminal.
    """
    from app.providers.services import ProviderAPIError

    if isinstance(exc, MalformedItemIdentityError):
        return True
    if isinstance(exc, ProviderAPIError):
        # ProviderNotConfiguredError carries no response, so status_code is
        # None and it correctly lands here as transient.
        return exc.status_code in TERMINAL_PROVIDER_STATUS_CODES
    if isinstance(exc, requests.exceptions.HTTPError):
        # Matched on the concrete type, not on "has a .response": any
        # exception can carry that attribute, and retiring an item on a
        # duck-typed match is the silent data loss this function exists to
        # avoid. A response-less HTTPError has no status and stays retryable.
        status_code = getattr(exc.response, "status_code", None)
        return status_code in TERMINAL_PROVIDER_STATUS_CODES
    return False


def _apply_backfill_state_filters(
    queryset,
    field: str,
    *,
    for_reconcile: bool = False,
    strategy_version: int | None = None,
):
    """Exclude items that shouldn't be attempted right now.

    ``for_reconcile`` additionally excludes items that have *ever* failed. A
    reconcile sweep's job is discovering items nothing has tried yet; retrying
    the failures is already ``backfill_item_metadata``'s job, on its own
    exponential schedule. Without this the candidate set never empties - a failed
    item's ``next_retry_at`` caps at one day, so it re-enters the sweep daily -
    which meant the reconcile could never be marked complete and polled the
    whole library forever (issue #521).

    ``strategy_version`` makes a block version-aware: a row recorded under an
    older strategy stops blocking, so bumping the version re-opens everything
    it had given up on.
    """
    now = timezone.now()
    blocked_filter = Q(give_up=True) | Q(next_retry_at__gt=now)
    if for_reconcile:
        blocked_filter |= Q(fail_count__gt=0)
    states = MetadataBackfillState.objects.filter(field=field)
    if strategy_version is not None:
        states = states.filter(strategy_version__gte=strategy_version)
    blocked = states.filter(blocked_filter).values("item_id")
    return queryset.exclude(id__in=blocked)


def _backfill_delay_seconds(
    fail_count: int,
    max_delay_seconds: int = METADATA_BACKFILL_MAX_DELAY_SECONDS,
) -> int:
    if fail_count <= 0:
        return METADATA_BACKFILL_BASE_DELAY_SECONDS
    # Clamp the exponent first: fail_count runs to 9999 for a pending item, and
    # 2 ** 9998 is a very large integer to build only to throw away.
    delay = METADATA_BACKFILL_BASE_DELAY_SECONDS * (2 ** min(fail_count - 1, 30))
    return min(delay, max_delay_seconds)


def _record_backfill_failure(
    item: Item,
    field: str,
    error_message: str | None = None,
    *,
    terminal: bool = False,
    strategy_version: int | None = None,
    min_delay_seconds: int | None = None,
) -> bool:
    """Record a failed attempt; return True when the item is now given up on.

    ``min_delay_seconds`` is a floor under the backoff, for a caller whose
    "failure" is a provider answering with nothing rather than an error.
    """
    now = timezone.now()
    state, _ = MetadataBackfillState.objects.get_or_create(item=item, field=field)
    state.fail_count = min(state.fail_count + 1, 9999)
    state.last_attempt_at = now
    state.terminal = terminal
    if error_message:
        state.last_error = str(error_message)[:500]
    if terminal:
        state.fail_count = METADATA_BACKFILL_MAX_ATTEMPTS
        state.give_up = True
        state.next_retry_at = None
    elif state.fail_count >= METADATA_BACKFILL_MAX_ATTEMPTS:
        state.give_up = True
        state.next_retry_at = None
    else:
        state.give_up = False
        delay_seconds = _backfill_delay_seconds(state.fail_count)
        if min_delay_seconds is not None:
            delay_seconds = max(delay_seconds, int(min_delay_seconds))
        state.next_retry_at = now + timedelta(seconds=delay_seconds)
    update_fields = [
        "fail_count",
        "last_attempt_at",
        "next_retry_at",
        "last_error",
        "give_up",
        "terminal",
    ]
    if strategy_version is not None:
        state.strategy_version = int(strategy_version)
        update_fields.append("strategy_version")
    state.save(update_fields=update_fields)
    if state.give_up:
        logger.warning(
            "metadata_backfill_give_up item_id=%s media_type=%s field=%s fail_count=%s has_reason=%s",
            item.id,
            item.media_type,
            field,
            state.fail_count,
            bool(error_message or state.last_error),
        )
    else:
        # DEBUG: one line per item per rung of the ladder was ~4,000 lines in two
        # days on one install. Giving up (above) stays a warning, and each task
        # logs how many items failed in its own summary line.
        logger.debug(
            "metadata_backfill_retry_later item_id=%s media_type=%s field=%s fail_count=%s next_retry_at=%s has_reason=%s",
            item.id,
            item.media_type,
            field,
            state.fail_count,
            state.next_retry_at.isoformat() if state.next_retry_at else None,
            bool(error_message or state.last_error),
        )
    return state.give_up


def _record_backfill_pending(
    item: Item,
    field: str,
    reason: str | None = None,
    *,
    strategy_version: int | None = None,
    min_delay_seconds: int | None = None,
) -> None:
    """Record a successful fetch that still needs another look later.

    Unlike ``_record_backfill_failure``, this never gives up: an empty TMDB
    watch-provider payload can become populated months later. ``fail_count``
    still advances so the whole-library reconcile can exclude the item
    (issue #521), while ``next_retry_at`` drives a bounded retry queue.

    ``min_delay_seconds`` is a floor for callers whose own schedule outruns the
    default backoff. That backoff caps at one day, so a task on a nightly beat
    finds every one of its misses due again on its very next run - the backoff
    never actually defers anything. A caller whose retry is expensive (one
    candidate can pull a multi-hundred-MB dataset) must set a floor longer than
    the interval it runs on, or it has not deferred the work at all.
    """
    now = timezone.now()
    state, _ = MetadataBackfillState.objects.get_or_create(item=item, field=field)
    state.fail_count = min(state.fail_count + 1, 9999)
    state.last_attempt_at = now
    state.last_success_at = None
    state.give_up = False
    state.terminal = False
    delay_seconds = _backfill_delay_seconds(
        state.fail_count, METADATA_BACKFILL_PENDING_MAX_DELAY_SECONDS
    )
    if min_delay_seconds is not None:
        delay_seconds = max(delay_seconds, int(min_delay_seconds))
    state.next_retry_at = now + timedelta(seconds=delay_seconds)
    if reason:
        state.last_error = str(reason)[:500]
    update_fields = [
        "fail_count",
        "last_attempt_at",
        "next_retry_at",
        "last_success_at",
        "last_error",
        "give_up",
        "terminal",
    ]
    if strategy_version is not None:
        state.strategy_version = int(strategy_version)
        update_fields.append("strategy_version")
    state.save(update_fields=update_fields)
    logger.debug(
        "metadata_backfill_pending item_id=%s media_type=%s field=%s fail_count=%s next_retry_at=%s has_reason=%s",
        item.id,
        item.media_type,
        field,
        state.fail_count,
        state.next_retry_at.isoformat() if state.next_retry_at else None,
        bool(reason or state.last_error),
    )


def _record_backfill_success(
    item: Item,
    field: str,
    strategy_version: int | None = None,
) -> None:
    now = timezone.now()
    state, _ = MetadataBackfillState.objects.get_or_create(item=item, field=field)
    state.fail_count = 0
    state.last_attempt_at = now
    state.next_retry_at = None
    state.last_success_at = now
    state.last_error = ""
    state.give_up = False
    state.terminal = False
    update_fields = [
        "fail_count",
        "last_attempt_at",
        "next_retry_at",
        "last_success_at",
        "last_error",
        "give_up",
        "terminal",
    ]
    if strategy_version is not None:
        state.strategy_version = int(strategy_version)
        update_fields.append("strategy_version")
    state.save(update_fields=update_fields)


def _reset_genre_backfill_state(item: Item) -> None:
    """Clear any genre backfill block so the item is eligible for reprocessing."""
    MetadataBackfillState.objects.filter(
        item=item,
        field=MetadataBackfillField.GENRES,
    ).update(
        give_up=False,
        fail_count=0,
        next_retry_at=None,
        last_success_at=None,
        last_error="",
        terminal=False,
    )


def reset_backfill_state_for_identity_change(items) -> None:
    """Clear every backfill verdict for items whose provider identity changed.

    A terminal "this id does not exist" verdict is about the id, not the row.
    When something re-points rows at a different provider id (the TVDB
    migration rewrites ``media_id`` in place), the old verdict no longer
    describes anything true, so those items become candidates again.

    Accepts anything the ORM can filter on: a queryset, or a list of items.
    """
    MetadataBackfillState.objects.filter(item__in=items).update(
        give_up=False,
        fail_count=0,
        next_retry_at=None,
        last_success_at=None,
        last_error="",
        terminal=False,
    )


def _filter_backfill_item_ids(item_ids, field: str):
    if not item_ids:
        return []
    now = timezone.now()
    blocked_ids = set(
        MetadataBackfillState.objects.filter(field=field, item_id__in=item_ids)
        .filter(Q(give_up=True) | Q(next_retry_at__gt=now))
        .values_list("item_id", flat=True)
    )
    if field == MetadataBackfillField.CREDITS:
        blocked_ids.update(
            MetadataBackfillState.objects.filter(
                field=field,
                item_id__in=item_ids,
                give_up=False,
                fail_count=0,
                last_success_at__isnull=False,
                strategy_version__gte=CREDITS_BACKFILL_VERSION,
            ).values_list("item_id", flat=True),
        )
    if field == MetadataBackfillField.GENRES:
        blocked_ids.update(
            MetadataBackfillState.objects.filter(
                field=field,
                item_id__in=item_ids,
                give_up=False,
                fail_count=0,
                last_success_at__isnull=False,
                strategy_version__gte=GENRE_BACKFILL_VERSION,
            ).values_list("item_id", flat=True),
        )
    return [item_id for item_id in item_ids if item_id not in blocked_ids]


# How many due state rows one retry pass may look at, per item it can queue.
DUE_RETRY_WINDOW_FACTOR = 4


def due_backfill_retry_item_ids(field: str, still_missing, batch_size: int) -> list[int]:
    """Return up to ``batch_size`` item ids whose retry for ``field`` is due.

    Asks the state table alone first, oldest retry first, then checks only that
    window's items against ``still_missing`` (an ``Item`` queryset of items that
    still lack the field). The single joined query this replaces had to read the
    item row of every due state row to find the few still missing it, and rows
    whose item was filled some other way stay due forever, so on a long-lived
    library every tick read more of the library to queue nothing.

    Window rows whose item no longer needs the field are moved to the back of the
    queue (the pending cap, two weeks) so they cannot hold the front of it.
    """
    now = timezone.now()
    window = list(
        MetadataBackfillState.objects.filter(
            field=field,
            give_up=False,
            last_success_at__isnull=True,
        )
        .filter(Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=now))
        .order_by("next_retry_at", "item_id")
        .values_list("item_id", flat=True)[: batch_size * DUE_RETRY_WINDOW_FACTOR]
    )
    if not window:
        return []
    missing = set(still_missing.filter(id__in=window).values_list("id", flat=True))
    satisfied = [item_id for item_id in window if item_id not in missing]
    if satisfied:
        MetadataBackfillState.objects.filter(
            field=field, item_id__in=satisfied
        ).update(
            next_retry_at=now
            + timedelta(seconds=METADATA_BACKFILL_PENDING_MAX_DELAY_SECONDS)
        )
    return [item_id for item_id in window if item_id in missing][:batch_size]


def _add_user_day_key(user_day_keys, user_id, day_key):
    if not user_id or not day_key:
        return
    user_day_keys[user_id].add(day_key)


_DAY_KEY_QUERY_CHUNK = 500


def _chunked_ids(ids):
    ids = list(ids)
    for start in range(0, len(ids), _DAY_KEY_QUERY_CHUNK):
        yield ids[start : start + _DAY_KEY_QUERY_CHUNK]


def _collect_backfill_day_keys(items, field: str):
    """Return ``{user_id: {day_key}}`` for the days a batch of items touches.

    One query per model for the whole batch, not one per item: the keys are
    unioned per user, so which item a row came from does not matter.
    """
    from app.models import (
        Anime,
        Book,
        Comic,
        Episode,
        Game,
        Manga,
        Movie,
    )

    user_day_keys = defaultdict(set)
    if not items:
        return user_day_keys

    item_ids_by_type = defaultdict(list)
    for item in items:
        item_ids_by_type[item.media_type].append(item.id)

    def add_activity_rows(model, item_ids, *, expand_runtime_range=False):
        for chunk in _chunked_ids(item_ids):
            rows = model.objects.filter(item_id__in=chunk).values(
                "user_id",
                "start_date",
                "end_date",
                "created_at",
            )
            for row in rows:
                if (
                    expand_runtime_range
                    and row.get("start_date")
                    and row.get("end_date")
                ):
                    day_keys = history_cache.history_day_keys_for_range(
                        row.get("start_date"),
                        row.get("end_date"),
                    )
                    if day_keys:
                        user_day_keys[row.get("user_id")].update(day_keys)
                    continue
                activity_dt = (
                    row.get("end_date")
                    or row.get("start_date")
                    or row.get("created_at")
                )
                _add_user_day_key(
                    user_day_keys,
                    row.get("user_id"),
                    history_cache.history_day_key(activity_dt),
                )

    def add_episode_rows(lookup, item_ids):
        for chunk in _chunked_ids(item_ids):
            rows = Episode.objects.filter(**{f"{lookup}__in": chunk}).values(
                "related_season__user_id", "end_date"
            )
            for row in rows:
                _add_user_day_key(
                    user_day_keys,
                    row.get("related_season__user_id"),
                    history_cache.history_day_key(row.get("end_date")),
                )

    for media_type, model in (
        (MediaTypes.MOVIE.value, Movie),
        (MediaTypes.GAME.value, Game),
        (MediaTypes.BOOK.value, Book),
        (MediaTypes.COMIC.value, Comic),
        (MediaTypes.MANGA.value, Manga),
    ):
        if item_ids_by_type.get(media_type):
            add_activity_rows(model, item_ids_by_type[media_type])

    if item_ids_by_type.get(MediaTypes.ANIME.value) and (
        field != MetadataBackfillField.GENRES
    ):
        add_activity_rows(
            Anime,
            item_ids_by_type[MediaTypes.ANIME.value],
            expand_runtime_range=field == MetadataBackfillField.RUNTIME,
        )

    if item_ids_by_type.get(MediaTypes.TV.value) and field in (
        MetadataBackfillField.GENRES,
        MetadataBackfillField.CREDITS,
    ):
        add_episode_rows(
            "related_season__related_tv__item_id",
            item_ids_by_type[MediaTypes.TV.value],
        )

    if (
        item_ids_by_type.get(MediaTypes.SEASON.value)
        and field == MetadataBackfillField.CREDITS
    ):
        add_episode_rows(
            "related_season__item_id", item_ids_by_type[MediaTypes.SEASON.value]
        )

    if (
        item_ids_by_type.get(MediaTypes.EPISODE.value)
        and field == MetadataBackfillField.RUNTIME
    ):
        add_episode_rows("item_id", item_ids_by_type[MediaTypes.EPISODE.value])

    return user_day_keys


def _schedule_metadata_statistics_refresh(items, field: str, reason: str):
    if not items:
        return
    from app import statistics_cache

    user_day_keys = _collect_backfill_day_keys(items, field)
    for user_id, day_keys in user_day_keys.items():
        if not day_keys:
            continue
        if field == MetadataBackfillField.CREDITS:
            # Day payloads carry no credits (talent is aggregated per range
            # from the database), so re-aggregating is enough. Rebuilding the
            # days turned every backfill batch into hundreds of day builds.
            # Coalesced: a backfill batch lands about every minute, and
            # marking each one kept the Statistics sync running back to back
            # against the same SQLite writer (#1706).
            from app import statistics_sync

            if not statistics_sync.request_credits_statistics_refresh(user_id):
                logger.debug(
                    "metadata_refresh_coalesced user_id=%s field=%s reason=%s",
                    user_id,
                    field,
                    reason,
                )
                continue
        else:
            statistics_cache.invalidate_statistics_days(
                user_id, day_keys, reason=reason
            )
        logger.info(
            "metadata_refresh_scheduled user_id=%s field=%s days=%s reason=%s",
            user_id,
            field,
            len(day_keys),
            reason,
        )


def _normalize_item_ids(item_ids):
    normalized = []
    for item_id in item_ids or []:
        try:
            item_id = int(item_id)  # noqa: PLW2901  # deliberate in-loop normalisation
        except (TypeError, ValueError):
            continue
        if item_id > 0:
            normalized.append(item_id)
    return sorted(set(normalized))
