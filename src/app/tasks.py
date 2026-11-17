"""Celery tasks for the app."""

import logging
from datetime import timedelta

from celery import shared_task, states
from django.conf import settings
from django.core.cache import cache
from django.db.models import Q
from django.db.models.functions import Coalesce
from django.utils import timezone
from django_celery_results.models import TaskResult

from app import cache_safety, history_cache, image_cache, metadata_utils
from app.interactive_requests import interactive_request_active
from app.log_safety import exception_summary
from app.models import (
    DISCOVER_MOVIE_METADATA_BACKFILL_VERSION,
    TV,
    Item,
    MediaTypes,
    MetadataBackfillField,
    MetadataBackfillState,
    Sources,
)
from app.providers.services import ProviderNotConfiguredError
from app.services import game_lengths as game_length_services
from app.services import trakt_popularity as trakt_popularity_service

logger = logging.getLogger(__name__)


@shared_task(name="Settle check-in", ignore_result=True)
def settle_checkin(user_id):
    """Complete a manual check-in once its assumed end has passed."""
    from app import checkin

    checkin.settle(user_id)


@shared_task(name="Settle check-in session", ignore_result=True)
def settle_checkin_session(user_id):
    """Drop a game or board game session left running past its time limit."""
    from app import checkin_session

    checkin_session.settle(user_id)


@shared_task(name="Cleanup task results", ignore_result=True)
def cleanup_task_results(batch_size=5000):
    """Remove expired and abandoned durable task-status rows in small batches.

    Also closes import runs whose task was killed before it could say so.
    """
    from integrations.tasks._import_helpers import close_abandoned_import_runs

    # Same housekeeping cadence: a killed import leaves no other record.
    close_abandoned_import_runs()

    batch_size = max(int(batch_size), 0)
    if not batch_size:
        return 0

    now = timezone.now()
    terminal_cutoff = now - timedelta(seconds=settings.CELERY_RESULT_EXPIRES)
    pending_cutoff = now - timedelta(minutes=30)
    eligible = (
        Q(status__in=states.READY_STATES, date_done__lt=terminal_cutoff)
        | Q(status=states.PENDING, date_created__lt=pending_cutoff)
        | Q(status=states.STARTED, date_created__lt=terminal_cutoff)
    )
    candidate_ids = list(
        TaskResult.objects.filter(eligible)
        .order_by("pk")
        .values_list("pk", flat=True)[:batch_size],
    )
    if not candidate_ids:
        return 0

    # Reapply the age/state filter after selecting IDs so a task that completed
    # between the two queries is not deleted as an abandoned result.
    deleted, _ = TaskResult.objects.filter(pk__in=candidate_ids).filter(eligible).delete()
    return deleted


@shared_task(name="Cleanup image cache", ignore_result=True)
def cleanup_image_cache():
    """Remove provider images that have been unused for thirty days."""
    summary = image_cache.cleanup_stale_images()
    if summary["removed_count"]:
        logger.info(
            "Removed %s stale cached images (%s bytes)",
            summary["removed_count"],
            summary["removed_bytes"],
        )
    return summary


@shared_task(name="Repair Celery broker bindings", ignore_result=True)
def repair_celery_broker_bindings():
    """Sweep and normalize Kombu Redis pidbox/fanout bindings periodically.

    The same repair runs once at process startup (app/apps.py), but a control
    command reply (e.g. control.revoke, the celery_ping health check) can write
    a malformed binding at any point during a container's uptime, and that
    entry then breaks every later control-command reply until the next
    restart. Running this on a schedule bounds how long a bad write survives.
    """
    from app.celery_broker import repair_celery_redis_bindings

    try:
        summary = repair_celery_redis_bindings()
    except Exception as error:
        logger.warning(
            "Periodic Kombu Redis binding repair failed: %s",
            exception_summary(error),
        )
        return None
    if summary["repaired"] or summary["removed"]:
        logger.info(
            (
                "Normalized Kombu Redis bindings "
                "(keys=%s members=%s repaired=%s removed=%s)"
            ),
            summary["keys"],
            summary["members"],
            summary["repaired"],
            summary["removed"],
        )
    return summary

# ---------------------------------------------------------------------------
# Modular task re-exports
# These tasks are defined in focused sub-modules but re-exported here so all
# existing callers (import paths, test patches on "app.tasks.*", apps.py
# dynamic imports) continue to work without any call-site changes.
# ---------------------------------------------------------------------------
from app.tasks_anime_library_repair import (  # noqa: E402
    convert_anime_library_shape_task,
    repair_duplicated_anime_libraries_task,
)
from app.tasks_backdrops import warm_backdrops_task  # noqa: E402, F401
from app.tasks_backfill_state import (  # noqa: E402
    EXTERNAL_IDS_BACKFILL_VERSION,
    GENRE_BACKFILL_VERSION,
    METADATA_BACKFILL_BASE_DELAY_SECONDS,
    METADATA_BACKFILL_MAX_ATTEMPTS,
    METADATA_BACKFILL_MAX_DELAY_SECONDS,
    RELEASE_BACKFILL_VERSION,
    STATUS_BACKFILL_VERSION,
    WATCH_PROVIDERS_BACKFILL_VERSION,
    _add_user_day_key,
    _apply_backfill_state_filters,
    _backfill_delay_seconds,
    _collect_backfill_day_keys,
    _filter_backfill_item_ids,
    _normalize_item_ids,
    _record_backfill_failure,
    _record_backfill_pending,
    _record_backfill_success,
    _schedule_metadata_statistics_refresh,
    is_terminal_backfill_error,
)
from app.tasks_bulk_plays import (  # noqa: E402
    bulk_consumption_batch_task,
    bulk_episode_plays_task,
    bulk_music_plays_task,
    bulk_show_plays_task,
)
from app.tasks_credits import (  # noqa: E402
    CREDITS_BACKFILL_ITEMS_QUEUE_KEY,
    CREDITS_BACKFILL_ITEMS_SCHEDULED_KEY,
    CREDITS_BACKFILL_QUEUE_TTL,
    CREDITS_BACKFILL_SOURCES,
    _missing_credits_item_ids,
    _next_credits_backfill_item_ids,
    _populate_credits_for_items,
    enqueue_credits_backfill_items,
    populate_credits_backfill_queue,
    populate_credits_data_for_items,
)
from app.tasks_db_backup import write_database_snapshot  # noqa: E402
from app.tasks_db_optimize import optimize_database_statistics  # noqa: E402
from app.tasks_db_warm import warm_database_page_cache  # noqa: E402
from app.tasks_discover import (  # noqa: E402
    purge_expired_discover_caches,
    refresh_discover_profiles,
    refresh_discover_rows,
    refresh_discover_tab_cache,
    warm_discover_api_cache,
    warm_discover_startup_tabs,
    warm_history_day_cache_coverage,
)
from app.tasks_episode import (  # noqa: E402
    populate_episode_runtime_data,
    populate_episode_runtime_queue,
)
from app.tasks_external_ids import (  # noqa: E402
    enqueue_due_external_ids_backfill_retries,
    enqueue_external_ids_backfill_items,
    ensure_external_ids_backfill_reconcile,
    is_external_ids_backfill_reconcile_complete,
    populate_external_ids_backfill_queue,
    populate_external_ids_for_items,
    reconcile_external_ids_backfill,
)
from app.tasks_game_groups import (  # noqa: E402
    populate_game_group_backfill_queue,
    populate_game_group_data_for_items,
    reconcile_game_groups_backfill,
)
from app.tasks_genre import (  # noqa: E402
    GENRE_BACKFILL_ITEMS_QUEUE_KEY,
    GENRE_BACKFILL_ITEMS_SCHEDULED_KEY,
    GENRE_BACKFILL_QUEUE_TTL,
    GENRE_BACKFILL_RECONCILE_FALLBACK_INTERVAL_SECONDS,
    GENRE_BACKFILL_SOURCES,
    _genre_items_queryset,
    _populate_genres_for_items,
    enqueue_genre_backfill_items,
    ensure_genre_backfill_reconcile,
    is_genre_backfill_reconcile_complete,
    populate_genre_backfill_queue,
    populate_genre_data_for_items,
    reconcile_genre_backfill,
)
from app.tasks_igdb_ratings import (  # noqa: E402
    IGDB_RATINGS_BACKFILL_ITEMS_QUEUE_KEY,
    IGDB_RATINGS_BACKFILL_ITEMS_SCHEDULED_KEY,
    IGDB_RATINGS_BACKFILL_QUEUE_TTL,
    IGDB_RATINGS_BACKFILL_VERSION,
    _igdb_rating_items_queryset,
    count_igdb_rating_backfill_items,
    enqueue_igdb_rating_backfill_items,
    populate_igdb_rating_backfill_queue,
    populate_igdb_rating_data_for_items,
    reconcile_igdb_rating_backfill,
)
from app.tasks_imdb import refresh_imdb_game_credits_from_datasets  # noqa: E402
from app.tasks_interactive import (  # noqa: E402
    continue_statistics_refresh_task,  # noqa: F401
    reconcile_statistics_sync_task,  # noqa: F401
    refresh_statistics_cache_task,  # noqa: F401
    resolve_playback_image,  # noqa: F401
    statistics_sync_task,  # noqa: F401
)
from app.tasks_mal import sync_mal_ratings_from_api  # noqa: E402
from app.tasks_metadata_cache import (  # noqa: E402
    _clear_item_metadata_cache,
    _exception_with_details,
    _fetch_item_metadata,
    _metadata_cache_keys_for_item,
    provider_key_scope,
)
from app.tasks_music import (  # noqa: E402
    enrich_albums_task,
    enrich_music_library_task,
    fast_runtime_backfill_task,
    populate_album_tracks_batch,
    prefetch_album_covers_batch,
    prefetch_artist_images_batch,
    repair_music_album_page,
    repair_music_artist_page,
    repair_music_track_page,
)
from app.tasks_opencritic import (  # noqa: E402
    backfill_opencritic_scores,  # noqa: F401
    refresh_item_opencritic_score,  # noqa: F401
)
from app.tasks_podcast import (  # noqa: E402
    PODCAST_WEBSITE_BACKFILL_VERSION,
    backfill_podcast_show_websites,
    ensure_podcast_website_backfill_reconcile,
    reconcile_podcast_website_backfill,
    refresh_podcast_show_from_rss_task,  # noqa: F401
)
from app.tasks_providers import (  # noqa: E402
    WATCH_PROVIDERS_BACKFILL_ITEMS_QUEUE_KEY,
    WATCH_PROVIDERS_BACKFILL_ITEMS_SCHEDULED_KEY,
    WATCH_PROVIDERS_BACKFILL_QUEUE_TTL,
    _populate_providers_for_items,
    _provider_items_queryset,
    enqueue_due_provider_backfill_retries,
    enqueue_provider_backfill_items,
    ensure_provider_backfill_reconcile,
    is_provider_backfill_reconcile_complete,
    populate_provider_backfill_queue,
    populate_provider_data_for_items,
    reconcile_provider_backfill,
)
from app.tasks_runtime import (  # noqa: E402
    RUNTIME_BACKFILL_EPISODES_LOCK_PREFIX,
    RUNTIME_BACKFILL_EPISODES_LOCK_TTL,
    RUNTIME_BACKFILL_EPISODES_QUEUE_KEY,
    RUNTIME_BACKFILL_EPISODES_SCHEDULED_KEY,
    RUNTIME_BACKFILL_ITEMS_QUEUE_KEY,
    RUNTIME_BACKFILL_ITEMS_SCHEDULED_KEY,
    RUNTIME_BACKFILL_QUEUE_TTL,
    RUNTIME_BACKFILL_SOURCES,
    _episode_runtime_items_queryset,
    _filter_episode_runtime_season_keys,
    _normalize_season_keys,
    _populate_runtime_for_items,
    _reset_stale_give_up_episode_runtimes,
    _runtime_items_queryset,
    enqueue_episode_runtime_backfill,
    enqueue_runtime_backfill_items,
    populate_runtime_backfill_queue,
    populate_runtime_data_batch,
    populate_runtime_data_continuous,
    populate_runtime_data_for_items,
)
from app.tasks_trakt import (  # noqa: E402
    TRAKT_POPULARITY_BACKFILL_ITEMS_QUEUE_KEY,
    TRAKT_POPULARITY_BACKFILL_ITEMS_SCHEDULED_KEY,
    TRAKT_POPULARITY_BACKFILL_QUEUE_TTL,
    enqueue_trakt_popularity_backfill_items,
    populate_trakt_episode_ratings_for_season,
    populate_trakt_popularity_backfill_queue,
    populate_trakt_popularity_data_for_items,
    reconcile_trakt_popularity,
)
from app.tasks_tv_provider_migration import (  # noqa: E402
    migrate_tv_shows_to_preferred_provider_task,
    move_user_tv_library_task,
)
from app.tasks_watch_state import (  # noqa: E402
    backfill_user_watch_state,
    backfill_watch_state,
    ensure_watch_state_repair,
)
from app.tasks_youtube_game import (  # noqa: E402,F401
    backfill_youtube_game_tags,
    lookup_youtube_game_tag,
)

RELEASE_BACKFILL_SOURCES = (
    Sources.TMDB.value,
    Sources.TVDB.value,
    Sources.MAL.value,
    Sources.MANGAUPDATES.value,
    Sources.MANGABAKA.value,
    Sources.IGDB.value,
    Sources.OPENLIBRARY.value,
    Sources.HARDCOVER.value,
    Sources.COMICVINE.value,
    Sources.GCD.value,
    Sources.BGG.value,
    Sources.MUSICBRAINZ.value,
)
STATUS_BACKFILL_MEDIA_TYPES = (
    MediaTypes.MOVIE.value,
    MediaTypes.TV.value,
    MediaTypes.ANIME.value,
    MediaTypes.MANGA.value,
)
RELEASE_BACKFILL_MEDIA_TYPES = (
    MediaTypes.MOVIE.value,
    MediaTypes.TV.value,
    MediaTypes.SEASON.value,
    MediaTypes.ANIME.value,
    MediaTypes.MANGA.value,
    MediaTypes.GAME.value,
    MediaTypes.BOOK.value,
    MediaTypes.COMIC.value,
    MediaTypes.BOARDGAME.value,
    MediaTypes.MUSIC.value,
)
TRACKED_TMDB_TV_REFRESH_STALE_AFTER = timedelta(days=1)
GAME_LENGTHS_BACKFILL_VERSION = 2
NIGHTLY_METADATA_QUALITY_GENRE_BATCH_SIZE = 1500
NIGHTLY_METADATA_QUALITY_RUNTIME_BATCH_SIZE = 500
NIGHTLY_METADATA_QUALITY_EPISODE_SEASONS_BATCH_SIZE = 300
NIGHTLY_METADATA_QUALITY_CREDITS_BATCH_SIZE = 2500
NIGHTLY_METADATA_QUALITY_CREDITS_SCAN_MULTIPLIER = 20
NIGHTLY_METADATA_QUALITY_TRAKT_POPULARITY_BATCH_SIZE = 300
NIGHTLY_METADATA_QUALITY_GENRE_COUNTDOWN = 5
NIGHTLY_METADATA_QUALITY_RUNTIME_COUNTDOWN = 15
NIGHTLY_METADATA_QUALITY_EPISODE_COUNTDOWN = 30
NIGHTLY_METADATA_QUALITY_CREDITS_COUNTDOWN = 45
NIGHTLY_METADATA_QUALITY_TRAKT_POPULARITY_COUNTDOWN = 60
NIGHTLY_METADATA_QUALITY_IMDB_GAME_CREDITS_COUNTDOWN = 90
DISCOVER_METADATA_REFRESH_DEBOUNCE_SECONDS = 60 * 10
DISCOVER_METADATA_REFRESH_COUNTDOWN_SECONDS = 60
BACKGROUND_TASK_PRIORITY = getattr(settings, "CELERY_TASK_PRIORITY_BACKGROUND", 9)
HISTORY_COVERAGE_REPAIR_REQUEUE_SECONDS = 15
# A batch that built nothing yielded to an interactive user; retrying in 15 s
# only repeats the missing-day scan, so wait for them to go idle.
HISTORY_COVERAGE_REPAIR_STALLED_REQUEUE_SECONDS = 120


def _release_items_queryset():
    stale_tv_cutoff = timezone.now() - TRACKED_TMDB_TV_REFRESH_STALE_AFTER
    queryset = Item.objects.filter(
        Q(
            release_datetime__isnull=True,
            media_type__in=RELEASE_BACKFILL_MEDIA_TYPES,
            source__in=metadata_utils.backfill_sources(RELEASE_BACKFILL_SOURCES),
        )
        | Q(
            # Revisit tracked TMDB shows even after first-air date is stored so
            # newly announced or started seasons can refresh time-left data.
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            metadata_fetched_at__isnull=False,
            metadata_fetched_at__lte=stale_tv_cutoff,
            # A subquery on the tracked shows, not a join: the join needed
            # DISTINCT, and together with the ORDER BY it led SQLite to walk
            # every item in fetch order whenever nothing was due.
            id__in=TV.objects.values("item_id"),
        ),
    )
    # An item whose provider id does not resolve can never grow a release date,
    # so without this it stayed at the front of the queue - ordered by oldest
    # metadata_fetched_at - and was re-fetched on every single cycle.
    return _apply_backfill_state_filters(
        queryset,
        MetadataBackfillField.RELEASE.value,
        strategy_version=RELEASE_BACKFILL_VERSION,
    )


def count_release_backfill_items() -> int:
    return _release_items_queryset().count()


def _status_items_queryset():
    queryset = Item.objects.filter(
        status="",
        media_type__in=STATUS_BACKFILL_MEDIA_TYPES,
        metadata_fetched_at__isnull=False,
    )
    return _apply_backfill_state_filters(
        queryset,
        MetadataBackfillField.STATUS.value,
        strategy_version=STATUS_BACKFILL_VERSION,
    )


def count_status_backfill_items() -> int:
    return _status_items_queryset().count()


def _discover_movie_metadata_items_queryset():
    queryset = Item.objects.filter(
        source=Sources.TMDB.value,
        media_type=MediaTypes.MOVIE.value,
        metadata_fetched_at__isnull=False,
    )
    queryset = _apply_backfill_state_filters(queryset, MetadataBackfillField.DISCOVER)
    completed_ids = MetadataBackfillState.objects.filter(
        field=MetadataBackfillField.DISCOVER,
        give_up=False,
        fail_count=0,
        last_success_at__isnull=False,
        strategy_version__gte=DISCOVER_MOVIE_METADATA_BACKFILL_VERSION,
    ).values("item_id")
    return queryset.exclude(id__in=completed_ids)


def count_discover_movie_metadata_backfill_items() -> int:
    return _discover_movie_metadata_items_queryset().count()


def _game_length_items_queryset():
    queryset = (
        Item.objects.filter(
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            metadata_fetched_at__isnull=False,
        )
        .exclude(
            provider_game_lengths_source=game_length_services.GAME_LENGTH_SOURCE_HLTB,
        )
        .exclude(
            provider_game_lengths_match=game_length_services.HLTB_MATCH_AMBIGUOUS,
        )
    )
    queryset = _apply_backfill_state_filters(
        queryset, MetadataBackfillField.GAME_LENGTHS
    )
    completed_ids = MetadataBackfillState.objects.filter(
        field=MetadataBackfillField.GAME_LENGTHS,
        give_up=False,
        fail_count=0,
        last_success_at__isnull=False,
        strategy_version__gte=GAME_LENGTHS_BACKFILL_VERSION,
    ).values("item_id")
    return queryset.exclude(id__in=completed_ids)


def count_game_length_backfill_items() -> int:
    return _game_length_items_queryset().count()


def _oldest_fetched_first():
    """Order by fetch time without letting SQLite walk the fetch-time index.

    Given that index for the ORDER BY, SQLite reads items in fetch order until it
    has its few matches. When none match, the usual state of a long-lived
    library, that is every fetched item on every tick. Ordering by an expression
    leaves it the selective media-type index and a sort of the matches. Same
    order: the column is never NULL in the querysets that use this.
    """
    return (Coalesce("metadata_fetched_at", "metadata_fetched_at"), "id")


def _initial_metadata_items_queryset():
    """Return initial metadata candidates, skipping Sonarr-seeded TV library rows.

    Sonarr imports can create large batches of season/episode rows using local
    library data. Treating those rows as generic "never fetched" metadata work
    causes avoidable provider storms and can monopolize SQLite during imports.
    """
    from django.db.models import Exists, OuterRef

    from integrations.models import CollectionSourceState

    sonarr_episode_collection_state = CollectionSourceState.objects.filter(
        source="sonarr",
        item__media_type=MediaTypes.EPISODE.value,
        item__media_id=OuterRef("media_id"),
        item__source=OuterRef("source"),
    )
    return (
        Item.objects.filter(metadata_fetched_at__isnull=True)
        .annotate(has_sonarr_episode_collection=Exists(sonarr_episode_collection_state))
        .exclude(
            media_type__in=[MediaTypes.SEASON.value, MediaTypes.EPISODE.value],
            has_sonarr_episode_collection=True,
        )
    )


def _schedule_discover_refresh_for_movie_items(items: list[Item]) -> None:
    movie_item_ids = [
        item.id
        for item in items
        if item.source == Sources.TMDB.value
        and item.media_type == MediaTypes.MOVIE.value
    ]
    if not movie_item_ids:
        return

    from app.discover import cache_repo
    from app.discover.registry import ALL_MEDIA_KEY
    from app.models import Movie

    user_ids = sorted(
        set(
            Movie.objects.filter(
                item_id__in=movie_item_ids,
                user__show_discover=True,
            ).values_list("user_id", flat=True),
        ),
    )
    if not user_ids:
        return

    target_media_types = [MediaTypes.MOVIE.value, ALL_MEDIA_KEY]
    cache_repo.delete_taste_profiles(user_ids, target_media_types)
    cache_repo.delete_row_caches(user_ids, target_media_types)

    refresh_discover_profiles.apply_async(
        kwargs={
            "user_ids": user_ids,
            "media_types": target_media_types,
        },
        countdown=DISCOVER_METADATA_REFRESH_COUNTDOWN_SECONDS,
        priority=BACKGROUND_TASK_PRIORITY,
    )

    for user_id in user_ids:
        refresh_key = f"discover_movie_metadata_refresh:{user_id}"
        # Skipping when the cache is unavailable is the right call, not just the
        # convenient one: this warms a Redis-backed cache, so there is nothing to
        # warm into while Redis is unwell.
        if not cache_safety.acquire_lock(
            refresh_key,
            timeout=DISCOVER_METADATA_REFRESH_DEBOUNCE_SECONDS,
            on_error=cache_safety.ON_ERROR_SKIP,
        ):
            continue
        for media_type in target_media_types:
            refresh_discover_tab_cache.apply_async(
                kwargs={
                    "user_id": user_id,
                    "media_type": media_type,
                    "show_more": False,
                    "force": True,
                    "clear_provider_cache": False,
                },
                countdown=DISCOVER_METADATA_REFRESH_COUNTDOWN_SECONDS,
                priority=BACKGROUND_TASK_PRIORITY,
            )


@shared_task(name="Post playback webhook", ignore_result=True)
def post_playback_webhook(user_id: int):
    """POST the user's live playback state to their configured webhook.

    Its own task, not part of the webhook that triggered it: a slow or dead
    endpoint must not delay or fail the playback state write that just
    succeeded. Nothing retries — a now-playing push is superseded by the next
    event, so a stale redelivery is worse than a miss.
    """
    import hashlib
    import hmac
    import json

    import requests

    from api.fork_views_playback import build_now_playing_payload
    from users.models import User

    user = User.objects.filter(pk=user_id).first()
    if not user or not user.playback_webhook_url:
        return

    payload = build_now_playing_payload(user) or {"active": False}

    # Serialised once, here, and sent as raw bytes. Signing a payload and then
    # letting `requests` re-encode it with `json=` is the classic way to ship a
    # signature the receiver cannot reproduce: the bytes on the wire have to be
    # the bytes that were signed.
    body = json.dumps(payload, separators=(",", ":")).encode()
    headers = {"Content-Type": "application/json"}
    secret = user.get_playback_webhook_secret()
    if secret:
        signature = hmac.new(
            secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        # `sha256=<hex>`, the GitHub webhook convention, so existing receiver
        # code and libraries verify it without a bespoke parser. Nothing here
        # defends against replay: a redelivered state is superseded by the next
        # event, which for a now-playing push is the whole lifetime of the fact.
        headers["X-Floppy-Signature"] = f"sha256={signature}"

    try:
        response = requests.post(
            user.playback_webhook_url,
            data=body,
            headers=headers,
            # (connect, read). A host that blackholes the SYN would otherwise
            # hold a worker for the full read timeout on every playback event.
            timeout=(3.05, 10),
            # The URL is user-supplied, so a redirect is a redirect the server
            # follows on that user's behalf — the one hop that could reach a
            # host they could not have named directly. `image_cache` refuses
            # them for the same reason; a webhook has no use for one.
            allow_redirects=False,
        )
        if response.is_redirect:
            # A 3xx is not an error status, so `raise_for_status` would call
            # this delivered. Nothing received the body.
            logger.warning(
                "Playback webhook for %s redirected, which is not followed",
                user.username,
            )
            return
        response.raise_for_status()
    except requests.RequestException as error:
        # `exception_summary`, never `str(error)`: a requests exception
        # stringifies with the full URL in it, which would put the webhook's
        # bearer path straight into the log.
        logger.warning(
            "Playback webhook failed for %s: %s",
            user.username,
            exception_summary(error),
        )


@shared_task(name="Build statistics day caches")
def build_statistics_days_task(user_id: int, start_token: str, end_token: str):
    """Build missing per-day statistics caches for an arbitrary date range.

    Scheduled from the statistics request path when a custom range (or a
    comparison period) has uncached days, so the rebuild happens here
    instead of blocking the page.
    """
    from datetime import datetime

    from django.contrib.auth import get_user_model

    from app import statistics_cache

    user_model = get_user_model()
    try:
        user = user_model.objects.get(pk=user_id)
    except user_model.DoesNotExist:
        return

    start_date = datetime.fromisoformat(start_token) if start_token != "all" else None  # noqa: S105  # pagination cursor, not a credential
    end_date = datetime.fromisoformat(end_token) if end_token != "all" else None  # noqa: S105  # pagination cursor, not a credential

    day_list = statistics_cache._resolve_day_list(user, start_date, end_date)
    if not day_list:
        return
    statistics_cache._aggregate_minutes_per_media_type_from_days(
        user,
        day_list,
        build_missing=True,
    )


@shared_task(name="Refresh item game lengths")
def refresh_item_game_lengths(
    item_id: int, force: bool = False, fetch_hltb: bool = True
):
    """Refresh persisted game-length metadata for a game item."""
    lock_key = game_length_services.get_game_lengths_refresh_lock_key(
        item_id,
        force=force,
        fetch_hltb=fetch_hltb,
    )

    try:
        item = Item.objects.filter(id=item_id).first()
        if not item:
            return {"updated": False, "reason": "missing_item"}
        if (
            item.source != Sources.IGDB.value
            or item.media_type != MediaTypes.GAME.value
        ):
            return {"updated": False, "reason": "unsupported_item"}

        try:
            payload = game_length_services.refresh_game_lengths(
                item,
                force=force,
                fetch_hltb=fetch_hltb,
            )
        except Exception as exc:
            error_message = _exception_with_details(exc)
            _record_backfill_failure(
                item,
                MetadataBackfillField.GAME_LENGTHS,
                f"exception: {error_message}",
            )
            logger.exception(
                "game_lengths_refresh_error item_id=%s media_id=%s error=%s",
                item.id,
                item.media_id,
                error_message,
            )
            return {
                "updated": False,
                "error": error_message,
                "item_id": item.id,
            }

        _record_backfill_success(
            item,
            MetadataBackfillField.GAME_LENGTHS,
            strategy_version=GAME_LENGTHS_BACKFILL_VERSION,
        )
        return {
            "updated": True,
            "item_id": item.id,
            "active_source": payload.get("active_source"),
            "match": item.provider_game_lengths_match,
        }
    finally:
        cache.delete(lock_key)


@shared_task(name="Nightly metadata quality backfill")
def nightly_metadata_quality_backfill_task(
    genre_batch_size: int = NIGHTLY_METADATA_QUALITY_GENRE_BATCH_SIZE,
    runtime_batch_size: int = NIGHTLY_METADATA_QUALITY_RUNTIME_BATCH_SIZE,
    episode_season_batch_size: int = NIGHTLY_METADATA_QUALITY_EPISODE_SEASONS_BATCH_SIZE,
    credits_batch_size: int = NIGHTLY_METADATA_QUALITY_CREDITS_BATCH_SIZE,
    credits_scan_multiplier: int = NIGHTLY_METADATA_QUALITY_CREDITS_SCAN_MULTIPLIER,
    trakt_popularity_batch_size: int = NIGHTLY_METADATA_QUALITY_TRAKT_POPULARITY_BATCH_SIZE,
):
    """Queue targeted metadata backfill batches for genres/runtime/credits.

    This runs on a nightly schedule and uses queue-based workers so metadata quality
    converges over time without requiring user-triggered maintenance commands.
    """
    genre_batch_size = max(int(genre_batch_size), 0)
    runtime_batch_size = max(int(runtime_batch_size), 0)
    episode_season_batch_size = max(int(episode_season_batch_size), 0)
    credits_batch_size = max(int(credits_batch_size), 0)
    credits_scan_multiplier = max(int(credits_scan_multiplier), 1)
    trakt_popularity_batch_size = max(int(trakt_popularity_batch_size), 0)

    _reset_stale_give_up_episode_runtimes()

    genre_item_ids = []
    if genre_batch_size:
        genre_item_ids = list(
            _genre_items_queryset()
            .order_by("id")
            .values_list("id", flat=True)[:genre_batch_size],
        )

    runtime_item_ids = []
    if runtime_batch_size:
        runtime_item_ids = list(
            _runtime_items_queryset()
            .order_by("id")
            .values_list("id", flat=True)[:runtime_batch_size],
        )

    episode_season_keys = []
    if episode_season_batch_size:
        episode_season_keys = list(
            _episode_runtime_items_queryset()
            .exclude(season_number__isnull=True)
            .values_list("media_id", "source", "season_number")
            .distinct()
            .order_by("media_id", "source", "season_number")[
                :episode_season_batch_size
            ],
        )

    credits_item_ids = _next_credits_backfill_item_ids(
        credits_batch_size,
        scan_multiplier=credits_scan_multiplier,
    )
    trakt_popularity_item_ids = []
    if (
        trakt_popularity_batch_size
        and trakt_popularity_service.trakt_provider.is_configured()
    ):
        trakt_popularity_item_ids = [
            item.id
            for item in trakt_popularity_service.select_items_for_refresh(
                limit=trakt_popularity_batch_size,
            )
        ]

    queued_genres = 0
    if genre_item_ids:
        queued_genres = enqueue_genre_backfill_items(
            genre_item_ids,
            countdown=NIGHTLY_METADATA_QUALITY_GENRE_COUNTDOWN,
        )

    queued_runtime = 0
    if runtime_item_ids:
        queued_runtime = enqueue_runtime_backfill_items(
            runtime_item_ids,
            countdown=NIGHTLY_METADATA_QUALITY_RUNTIME_COUNTDOWN,
        )

    queued_episode_seasons = 0
    if episode_season_keys:
        queued_episode_seasons = enqueue_episode_runtime_backfill(
            episode_season_keys,
            countdown=NIGHTLY_METADATA_QUALITY_EPISODE_COUNTDOWN,
        )

    queued_credits = 0
    if credits_item_ids:
        queued_credits = enqueue_credits_backfill_items(
            credits_item_ids,
            countdown=NIGHTLY_METADATA_QUALITY_CREDITS_COUNTDOWN,
        )

    queued_trakt_popularity = 0
    if trakt_popularity_item_ids:
        queued_trakt_popularity = enqueue_trakt_popularity_backfill_items(
            trakt_popularity_item_ids,
            countdown=NIGHTLY_METADATA_QUALITY_TRAKT_POPULARITY_COUNTDOWN,
        )

    refresh_imdb_game_credits_from_datasets.apply_async(
        countdown=NIGHTLY_METADATA_QUALITY_IMDB_GAME_CREDITS_COUNTDOWN,
    )

    summary = {
        "selected": {
            "genres": len(genre_item_ids),
            "runtime": len(runtime_item_ids),
            "episode_seasons": len(episode_season_keys),
            "credits": len(credits_item_ids),
            "trakt_popularity": len(trakt_popularity_item_ids),
        },
        "queued": {
            "genres": queued_genres,
            "runtime": queued_runtime,
            "episode_seasons": queued_episode_seasons,
            "credits": queued_credits,
            "trakt_popularity": queued_trakt_popularity,
        },
        "remaining": {
            "genres": _genre_items_queryset().count(),
            "runtime": _runtime_items_queryset().count(),
            "episode_runtime": _episode_runtime_items_queryset().count(),
            "trakt_popularity": len(
                trakt_popularity_service.select_items_for_refresh(),
            )
            if trakt_popularity_service.trakt_provider.is_configured()
            else 0,
        },
    }
    logger.info("nightly_metadata_quality_backfill summary=%s", summary)
    return summary


@shared_task
def refresh_history_cache_task(
    user_id: int,
    logging_style: str = "repeats",
    warm_days: int | None = None,
    day_keys=None,
    *args,
    **kwargs,
):
    """Rebuild the cached History page for a user."""
    if logging_style not in ("sessions", "repeats"):
        for candidate in (logging_style, *args, kwargs.get("logging_style")):
            if candidate in ("sessions", "repeats"):
                logging_style = candidate
                break
        else:
            logging_style = "repeats"
    if warm_days is None:
        for candidate in (*args, kwargs.get("warm_days")):
            if candidate is None:
                continue
            try:
                warm_days = int(candidate)
                break
            except (TypeError, ValueError):
                continue
    if warm_days is not None and warm_days < 0:
        warm_days = None
    if day_keys is None:
        candidate = kwargs.get("day_keys")
        if candidate:
            day_keys = candidate
    if day_keys is None:
        for candidate in args:
            if isinstance(candidate, (list, tuple)):
                day_keys = candidate
                break
    history_cache.refresh_history_cache(
        user_id,
        logging_style=logging_style,
        warm_days=warm_days,
        day_keys=day_keys,
    )


@shared_task(name="Repair History Day Cache Coverage")
def repair_history_day_cache_coverage_task(
    user_id: int,
    logging_style: str = "repeats",
    batch_size: int | None = None,
):
    """Repair missing persisted history day payloads without blocking navigation."""
    repair_key = history_cache._coverage_repair_key(user_id, logging_style)
    if interactive_request_active():
        cache.delete(repair_key)
        return {
            "skipped": True,
            "reason": "interactive_request_active",
        }
    result = history_cache.repair_history_day_cache_coverage(
        user_id,
        logging_style=logging_style,
        batch_size=batch_size,
    )
    if result.get("remaining"):
        cache.set(
            repair_key,
            {
                "started_at": timezone.now().isoformat(),
                "batch_size": batch_size,
            },
            history_cache.HISTORY_COVERAGE_REPAIR_LOCK_TTL,
        )
        repair_history_day_cache_coverage_task.apply_async(
            kwargs={
                "user_id": user_id,
                "logging_style": logging_style,
                "batch_size": batch_size,
            },
            countdown=(
                HISTORY_COVERAGE_REPAIR_STALLED_REQUEUE_SECONDS
                if result.get("rebuilt", 1) == 0
                else HISTORY_COVERAGE_REPAIR_REQUEUE_SECONDS
            ),
            priority=BACKGROUND_TASK_PRIORITY,
        )
    else:
        cache.delete(repair_key)
    return result


@shared_task(name="Backfill item metadata")
def backfill_item_metadata_task(
    batch_size: int = 10, game_length_batch_size: int | None = None
):
    """Backfill metadata fields, missing release dates, and game-length metadata.

    Args:
        batch_size: Number of items to process in this batch (default: 10)
        game_length_batch_size: Max already-fetched IGDB games to enrich with game lengths.

    Returns:
        dict: Results including success_count, error_count, and message
    """
    if game_length_batch_size is None:
        game_length_batch_size = min(max(int(batch_size), 0), 25)
    else:
        game_length_batch_size = max(int(game_length_batch_size), 0)

    from app.tasks_metadata_language import repair_metadata_language_batch

    language_repair = repair_metadata_language_batch(batch_size=min(batch_size, 25))
    if interactive_request_active():
        logger.info("metadata_backfill_skipped reason=interactive_request_active")
        # No "remaining" counts here: each is a pass over the library, five of
        # them to report on a run that is standing back to save that work.
        return {
            "skipped": True,
            "reason": "interactive_request_active",
            "success_count": 0,
            "error_count": 0,
            "message": "Skipped metadata backfill while an interactive request was active",
        }

    batch_size = max(batch_size - language_repair["attempted"], 0)
    initial_items = list(_initial_metadata_items_queryset().order_by("id")[:batch_size])
    initial_item_ids = [item.id for item in initial_items]
    remaining_slots = max(batch_size - len(initial_items), 0)
    game_length_backfill_items = []
    release_backfill_items = []
    discover_backfill_items = []
    status_backfill_items = []

    if remaining_slots > 0 and game_length_batch_size > 0:
        game_length_limit = min(remaining_slots, game_length_batch_size)
        game_length_backfill_items = list(
            _game_length_items_queryset()
            .exclude(id__in=initial_item_ids)
            .order_by("provider_game_lengths_fetched_at", "metadata_fetched_at", "id")[
                :game_length_limit
            ],
        )
        remaining_slots = max(remaining_slots - len(game_length_backfill_items), 0)

    if remaining_slots > 0:
        selected_ids = initial_item_ids + [
            item.id for item in game_length_backfill_items
        ]
        release_backfill_items = list(
            _release_items_queryset()
            .filter(metadata_fetched_at__isnull=False)
            .exclude(id__in=selected_ids)
            .order_by("metadata_fetched_at", "id")[:remaining_slots],
        )
        remaining_slots = max(remaining_slots - len(release_backfill_items), 0)

    if remaining_slots > 0:
        release_item_ids = [item.id for item in release_backfill_items]
        selected_ids = (
            initial_item_ids
            + [item.id for item in game_length_backfill_items]
            + release_item_ids
        )
        discover_backfill_items = list(
            _discover_movie_metadata_items_queryset()
            .exclude(id__in=selected_ids)
            .order_by(*_oldest_fetched_first())[:remaining_slots],
        )
        remaining_slots = max(remaining_slots - len(discover_backfill_items), 0)

    if remaining_slots > 0:
        discover_item_ids_selected = [item.id for item in discover_backfill_items]
        selected_ids = (
            initial_item_ids
            + [item.id for item in game_length_backfill_items]
            + release_item_ids
            + discover_item_ids_selected
        )
        # One pick per media type: as a single IN (...) over four types the
        # planner reads the whole item table instead of seeking by type.
        status_backfill_items = sorted(
            (
                item
                for media_type in STATUS_BACKFILL_MEDIA_TYPES
                for item in _status_items_queryset()
                .filter(media_type=media_type)
                .exclude(id__in=selected_ids)
                .order_by(*_oldest_fetched_first())[:remaining_slots]
            ),
            key=lambda item: (item.metadata_fetched_at, item.id),
        )[:remaining_slots]

    items = (
        initial_items
        + release_backfill_items
        + discover_backfill_items
        + game_length_backfill_items
        + status_backfill_items
    )
    if not items:
        return {
            "success_count": language_repair["success"],
            "error_count": language_repair["errors"],
            "metadata_language_repair": language_repair,
            "remaining_metadata": 0,
            "remaining_release": 0,
            "remaining_discover_movie_metadata": 0,
            "remaining_game_lengths": 0,
            "remaining_status": 0,
            "message": (
                f"Attempted {language_repair['attempted']} metadata language repairs"
                if language_repair["attempted"]
                else "No items need metadata, release-date, Discover metadata, "
                "game-length, or status backfill"
            ),
        }

    success_count = language_repair["success"]
    error_count = language_repair["errors"]
    release_updated_count = 0
    processed_movie_discover_items: list[Item] = []
    discover_item_ids = {item.id for item in discover_backfill_items}
    game_length_item_ids = {item.id for item in game_length_backfill_items}
    status_item_ids = {item.id for item in status_backfill_items}
    deferred_for_interactive_request = False

    for index, item in enumerate(items):
        if index > 0 and interactive_request_active():
            deferred_for_interactive_request = True
            logger.info(
                "metadata_backfill_deferred reason=interactive_request_active processed=%s remaining=%s",
                success_count + error_count,
                len(items) - index,
            )
            break
        initial_metadata_backfill = item.metadata_fetched_at is None
        discover_metadata_backfill = item.id in discover_item_ids
        game_lengths_backfill = item.id in game_length_item_ids
        # An item can land in another bucket (e.g. release, for a blank
        # release_datetime) before ever being selected by the status query,
        # so also check the item's own blank status directly rather than
        # relying solely on status_item_ids bucket membership — otherwise a
        # same-fetch status value would be silently dropped by include_core=False.
        status_backfill = item.id in status_item_ids or (
            item.media_type in STATUS_BACKFILL_MEDIA_TYPES and not item.status
        )
        try:
            if item.release_datetime is None:
                _clear_item_metadata_cache(item)

            metadata = _fetch_item_metadata(item)
            # Shared background fetches use the instance language, not the
            # language chosen by the user who last synced this item (#1579).
            # Fill missing synopses without replacing an existing translation.
            if item.synopsis:
                metadata = {**metadata, "synopsis": item.synopsis}

            update_fields = []

            if initial_metadata_backfill or status_backfill:
                update_fields.extend(
                    metadata_utils.apply_item_metadata(
                        item,
                        metadata,
                        include_core=True,
                        include_provider=True,
                        include_release=True,
                    ),
                )
            else:
                update_fields.extend(
                    metadata_utils.apply_item_metadata(
                        item,
                        metadata,
                        include_core=False,
                        include_provider=True,
                        include_release=True,
                    ),
                )

            if "release_datetime" in update_fields:
                release_updated_count += 1

            item.metadata_fetched_at = timezone.now()
            update_fields.append("metadata_fetched_at")

            item.save(update_fields=update_fields)

            if (
                item.source == Sources.IGDB.value
                and item.media_type == MediaTypes.GAME.value
            ):
                try:
                    with provider_key_scope(item):
                        game_length_services.refresh_game_lengths(
                            item,
                            igdb_metadata=metadata,
                            force=False,
                            fetch_hltb=True,
                        )
                    _record_backfill_success(
                        item,
                        MetadataBackfillField.GAME_LENGTHS,
                        strategy_version=GAME_LENGTHS_BACKFILL_VERSION,
                    )
                except Exception as exc:
                    error_message = _exception_with_details(exc)
                    _record_backfill_failure(
                        item,
                        MetadataBackfillField.GAME_LENGTHS,
                        f"exception: {error_message}",
                    )
                    logger.warning(
                        "game_lengths_backfill_error item_id=%s media_id=%s error=%s",
                        item.id,
                        item.media_id,
                        error_message,
                    )

            if (
                item.source == Sources.TMDB.value
                and item.media_type == MediaTypes.TV.value
            ):
                from events.calendar.main import cleanup_invalid_events, save_events
                from events.calendar.tv import process_tv

                tv_events_bulk = []
                process_tv(
                    item,
                    tv_events_bulk,
                    tv_metadata=metadata,
                )
                if tv_events_bulk:
                    save_events(tv_events_bulk)
                    cleanup_invalid_events(tv_events_bulk)

            if (
                item.source == Sources.TMDB.value
                and item.media_type == MediaTypes.MOVIE.value
            ):
                _record_backfill_success(
                    item,
                    MetadataBackfillField.DISCOVER,
                    strategy_version=DISCOVER_MOVIE_METADATA_BACKFILL_VERSION,
                )
                processed_movie_discover_items.append(item)

            # Record what the provider was actually able to give us. A fetch
            # that succeeds but still leaves the field blank is "pending", not
            # "done": the provider may fill it in later, but it must not be
            # re-fetched on every cycle in the meantime.
            if item.media_type in RELEASE_BACKFILL_MEDIA_TYPES:
                if item.release_datetime is None:
                    _record_backfill_pending(
                        item,
                        MetadataBackfillField.RELEASE.value,
                        "provider has no release date",
                        strategy_version=RELEASE_BACKFILL_VERSION,
                    )
                else:
                    _record_backfill_success(
                        item,
                        MetadataBackfillField.RELEASE.value,
                        strategy_version=RELEASE_BACKFILL_VERSION,
                    )

            if item.media_type in STATUS_BACKFILL_MEDIA_TYPES:
                if item.status:
                    _record_backfill_success(
                        item,
                        MetadataBackfillField.STATUS.value,
                        strategy_version=STATUS_BACKFILL_VERSION,
                    )
                else:
                    _record_backfill_pending(
                        item,
                        MetadataBackfillField.STATUS.value,
                        "provider has no status",
                        strategy_version=STATUS_BACKFILL_VERSION,
                    )

            success_count += 1
            logger.info(
                (
                    "metadata_backfill_success item_id=%s media_type=%s "
                    "country=%s format=%s release_datetime=%s initial=%s discover=%s "
                    "game_lengths=%s status_backfill=%s"
                ),
                item.id,
                item.media_type,
                item.country,
                item.format,
                item.release_datetime.isoformat() if item.release_datetime else None,
                initial_metadata_backfill,
                discover_metadata_backfill,
                game_lengths_backfill,
                status_backfill,
            )

        except Exception as e:
            error_count += 1
            terminal = is_terminal_backfill_error(e)
            if (
                item.source == Sources.TMDB.value
                and item.media_type == MediaTypes.MOVIE.value
            ):
                _record_backfill_failure(
                    item,
                    MetadataBackfillField.DISCOVER,
                    f"exception: {exception_summary(e)}",
                    terminal=terminal,
                )
            # A provider id that does not resolve - a MusicBrainz recording id
            # returning 400/404, a season row with no season number - is not a
            # provider outage and will not resolve on the next pass either.
            # Without this, those items sorted to the front of the release and
            # status queues forever and were re-fetched every cycle.
            if item.media_type in RELEASE_BACKFILL_MEDIA_TYPES:
                _record_backfill_failure(
                    item,
                    MetadataBackfillField.RELEASE.value,
                    f"exception: {exception_summary(e)}",
                    terminal=terminal,
                    strategy_version=RELEASE_BACKFILL_VERSION,
                )
            if item.media_type in STATUS_BACKFILL_MEDIA_TYPES:
                _record_backfill_failure(
                    item,
                    MetadataBackfillField.STATUS.value,
                    f"exception: {exception_summary(e)}",
                    terminal=terminal,
                    strategy_version=STATUS_BACKFILL_VERSION,
                )
            # Still mark as fetched even if there was an error, to avoid retrying infinitely
            item.metadata_fetched_at = timezone.now()
            item.save(update_fields=["metadata_fetched_at"])

            if isinstance(e, ProviderNotConfiguredError):
                # Missing keys are a setup state, not a fault: one line, no
                # traceback, instead of an error per game on every pass.
                logger.info(
                    "metadata_backfill_skipped item_id=%s media_type=%s reason=%s",
                    item.id,
                    item.media_type,
                    exception_summary(e),
                )
            else:
                logger.exception(
                    "metadata_backfill_error item_id=%s media_type=%s error=%s",
                    item.id,
                    item.media_type,
                    exception_summary(e),  # noqa: TRY401  # exception_summary() is the project's sanitised rendering
                )

    remaining_metadata = _initial_metadata_items_queryset().count()
    remaining_release = count_release_backfill_items()
    remaining_discover_movie_metadata = count_discover_movie_metadata_backfill_items()
    remaining_game_lengths = count_game_length_backfill_items()
    remaining_status = count_status_backfill_items()

    if processed_movie_discover_items:
        _schedule_discover_refresh_for_movie_items(processed_movie_discover_items)

    result = {
        "success_count": success_count,
        "metadata_language_repair": language_repair,
        "release_updated_count": release_updated_count,
        "error_count": error_count,
        "remaining_metadata": remaining_metadata,
        "remaining_release": remaining_release,
        "remaining_discover_movie_metadata": remaining_discover_movie_metadata,
        "remaining_game_lengths": remaining_game_lengths,
        "remaining_status": remaining_status,
        "remaining": remaining_metadata,
        "message": (
            f"Processed {success_count + error_count} items, "
            f"{remaining_metadata} metadata items remaining, "
            f"{remaining_release} release items remaining, "
            f"{remaining_discover_movie_metadata} Discover movie items remaining, "
            f"{remaining_game_lengths} game-length items remaining, "
            f"{remaining_status} status items remaining"
        ),
    }
    if deferred_for_interactive_request:
        result["deferred"] = True
        result["reason"] = "interactive_request_active"
    return result


@shared_task(name="Enqueue item backfills", ignore_result=True)
def enqueue_item_backfills(item_id: int, kinds: list[str]):
    """Queue per-item backfills a save would otherwise run inside its request."""
    if "runtime" in kinds:
        enqueue_runtime_backfill_items([item_id])
    if "genres" in kinds:
        enqueue_genre_backfill_items([item_id])
    if "credits" in kinds:
        enqueue_credits_backfill_items([item_id])
    if "credits_if_needed" in kinds:
        from app.signals import _schedule_credits_backfill_if_needed

        _schedule_credits_backfill_if_needed(item_id)


__all__ = [
    "CREDITS_BACKFILL_ITEMS_QUEUE_KEY",
    "CREDITS_BACKFILL_ITEMS_SCHEDULED_KEY",
    "CREDITS_BACKFILL_QUEUE_TTL",
    "CREDITS_BACKFILL_SOURCES",
    "EXTERNAL_IDS_BACKFILL_VERSION",
    "GENRE_BACKFILL_ITEMS_QUEUE_KEY",
    "GENRE_BACKFILL_ITEMS_SCHEDULED_KEY",
    "GENRE_BACKFILL_QUEUE_TTL",
    "GENRE_BACKFILL_RECONCILE_FALLBACK_INTERVAL_SECONDS",
    "GENRE_BACKFILL_SOURCES",
    "GENRE_BACKFILL_VERSION",
    "IGDB_RATINGS_BACKFILL_ITEMS_QUEUE_KEY",
    "IGDB_RATINGS_BACKFILL_ITEMS_SCHEDULED_KEY",
    "IGDB_RATINGS_BACKFILL_QUEUE_TTL",
    "IGDB_RATINGS_BACKFILL_VERSION",
    "METADATA_BACKFILL_BASE_DELAY_SECONDS",
    "METADATA_BACKFILL_MAX_ATTEMPTS",
    "METADATA_BACKFILL_MAX_DELAY_SECONDS",
    "PODCAST_WEBSITE_BACKFILL_VERSION",
    "RUNTIME_BACKFILL_EPISODES_LOCK_PREFIX",
    "RUNTIME_BACKFILL_EPISODES_LOCK_TTL",
    "RUNTIME_BACKFILL_EPISODES_QUEUE_KEY",
    "RUNTIME_BACKFILL_EPISODES_SCHEDULED_KEY",
    "RUNTIME_BACKFILL_ITEMS_QUEUE_KEY",
    "RUNTIME_BACKFILL_ITEMS_SCHEDULED_KEY",
    "RUNTIME_BACKFILL_QUEUE_TTL",
    "RUNTIME_BACKFILL_SOURCES",
    "TRAKT_POPULARITY_BACKFILL_ITEMS_QUEUE_KEY",
    "TRAKT_POPULARITY_BACKFILL_ITEMS_SCHEDULED_KEY",
    "TRAKT_POPULARITY_BACKFILL_QUEUE_TTL",
    "WATCH_PROVIDERS_BACKFILL_ITEMS_QUEUE_KEY",
    "WATCH_PROVIDERS_BACKFILL_ITEMS_SCHEDULED_KEY",
    "WATCH_PROVIDERS_BACKFILL_QUEUE_TTL",
    "WATCH_PROVIDERS_BACKFILL_VERSION",
    "_add_user_day_key",
    "_backfill_delay_seconds",
    "_collect_backfill_day_keys",
    "_filter_backfill_item_ids",
    "_filter_episode_runtime_season_keys",
    "_igdb_rating_items_queryset",
    "_metadata_cache_keys_for_item",
    "_missing_credits_item_ids",
    "_normalize_item_ids",
    "_normalize_season_keys",
    "_populate_credits_for_items",
    "_populate_genres_for_items",
    "_populate_providers_for_items",
    "_populate_runtime_for_items",
    "_provider_items_queryset",
    "_schedule_metadata_statistics_refresh",
    "backfill_podcast_show_websites",
    "backfill_user_watch_state",
    "backfill_watch_state",
    "bulk_consumption_batch_task",
    "bulk_episode_plays_task",
    "bulk_music_plays_task",
    "bulk_show_plays_task",
    "convert_anime_library_shape_task",
    "count_igdb_rating_backfill_items",
    "enqueue_due_external_ids_backfill_retries",
    "enqueue_due_provider_backfill_retries",
    "enqueue_external_ids_backfill_items",
    "enqueue_igdb_rating_backfill_items",
    "enqueue_item_backfills",
    "enqueue_provider_backfill_items",
    "enrich_albums_task",
    "enrich_music_library_task",
    "ensure_external_ids_backfill_reconcile",
    "ensure_genre_backfill_reconcile",
    "ensure_podcast_website_backfill_reconcile",
    "ensure_provider_backfill_reconcile",
    "ensure_watch_state_repair",
    "fast_runtime_backfill_task",
    "is_external_ids_backfill_reconcile_complete",
    "is_genre_backfill_reconcile_complete",
    "is_provider_backfill_reconcile_complete",
    "migrate_tv_shows_to_preferred_provider_task",
    "move_user_tv_library_task",
    "optimize_database_statistics",
    "populate_album_tracks_batch",
    "populate_credits_backfill_queue",
    "populate_credits_data_for_items",
    "populate_episode_runtime_data",
    "populate_episode_runtime_queue",
    "populate_external_ids_backfill_queue",
    "populate_external_ids_for_items",
    "populate_game_group_backfill_queue",
    "populate_game_group_data_for_items",
    "populate_genre_backfill_queue",
    "populate_genre_data_for_items",
    "populate_igdb_rating_backfill_queue",
    "populate_igdb_rating_data_for_items",
    "populate_provider_backfill_queue",
    "populate_provider_data_for_items",
    "populate_runtime_backfill_queue",
    "populate_runtime_data_batch",
    "populate_runtime_data_continuous",
    "populate_runtime_data_for_items",
    "populate_trakt_episode_ratings_for_season",
    "populate_trakt_popularity_backfill_queue",
    "populate_trakt_popularity_data_for_items",
    "prefetch_album_covers_batch",
    "prefetch_artist_images_batch",
    "purge_expired_discover_caches",
    "reconcile_external_ids_backfill",
    "reconcile_game_groups_backfill",
    "reconcile_genre_backfill",
    "reconcile_igdb_rating_backfill",
    "reconcile_podcast_website_backfill",
    "reconcile_provider_backfill",
    "reconcile_trakt_popularity",
    "refresh_discover_rows",
    "repair_duplicated_anime_libraries_task",
    "repair_music_album_page",
    "repair_music_artist_page",
    "repair_music_track_page",
    "sync_mal_ratings_from_api",
    "warm_database_page_cache",
    "warm_discover_api_cache",
    "warm_discover_startup_tabs",
    "warm_history_day_cache_coverage",
    "write_database_snapshot",
]
