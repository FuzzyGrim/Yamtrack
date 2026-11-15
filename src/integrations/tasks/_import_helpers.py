import contextlib
import logging
from datetime import timedelta
from io import BytesIO

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from app.log_safety import exception_summary
from app.models import MediaTypes
from app.templatetags import app_tags
from integrations import plex as plex_api
from integrations.imports import helpers
from integrations.upload_staging import open_import_file

logger = logging.getLogger(__name__)

ERROR_TITLE = "\n\n\n Couldn't import the following media: \n\n"
IMPORT_COUNT_METRIC_KEYS = frozenset(
    {
        "created",
        "failed",
        "rejected",
        "updated",
        "skipped",
        # Rows the upstream reported exactly as before: looked at, not written.
        "unchanged",
        "baseline",
        "skipped_ignored",
        "skipped_missing_ids",
        "skipped_numbering_mismatch",
        "skipped_existing",
        "skipped_unknown_type",
        "skipped_other_user",
        "music_unique_tracks",
    },
)
GOODREADS_IMPORT_TASK_NAME = "Import from Goodreads"
LEGACY_GOODREADS_IMPORT_TASK_NAMES = (
    "Import from GoodReads",
    "integrations.tasks.import_goodreads",
)


def _is_expected_plex_lookup_error(exc):
    """Return True for expected Plex library lookup failures that don't need tracebacks."""
    if isinstance(exc, plex_api.PlexClientError):
        return True

    summary = exception_summary(exc).lower()
    if "timeout" in summary or "timed out" in summary:
        return True

    exc_type = type(exc).__name__.lower()
    return "timeout" in exc_type


def _coerce_uploaded_file(file):
    """Normalize uploaded file task args to a binary file-like object."""
    if hasattr(file, "read"):
        with contextlib.suppress(AttributeError, OSError):
            file.seek(0)
        return file
    if isinstance(file, str):
        return BytesIO(file.encode("utf-8"))
    if isinstance(file, bytes):
        return BytesIO(file)
    msg = f"Unsupported uploaded file payload type: {type(file)!r}"
    raise TypeError(msg)


# Last.fm and Koito backfills reschedule themselves across many task
# invocations, so one run legitimately outlives any single task's time limit.
SELF_RESCHEDULING_IMPORT_SOURCES = ("lastfm", "koito")

# Stremio imports set their own limits, which can exceed a lowered global one.
STREMIO_IMPORT_SOFT_TIME_LIMIT = 20 * 60
STREMIO_IMPORT_TIME_LIMIT = 30 * 60

# A WeTrakr export resolves most shows through TMDB one lookup at a time, so a
# full library outlives the global limit. It is a one-off, user-started import.
WETRAKR_IMPORT_SOFT_TIME_LIMIT = 90 * 60
WETRAKR_IMPORT_TIME_LIMIT = 120 * 60

# ImportRun.source -> hard limit, for sources that outlive the global one.
SOURCE_TIME_LIMITS = {
    "stremio": STREMIO_IMPORT_TIME_LIMIT,
    "wetrakr": WETRAKR_IMPORT_TIME_LIMIT,
}


def abandoned_after_seconds(source, global_limit):
    """Seconds after which a RUNNING run of ``source`` cannot still be alive."""
    return max(global_limit, SOURCE_TIME_LIMITS.get(source, 0)) + 300


def close_abandoned_import_runs():
    """Mark import runs FAILED once their task can no longer be running.

    A hard time limit or a container restart kills the task without running
    any of its code, so its ImportRun would read "running" for ever. A run
    older than the task time limit plus a margin cannot still be alive.
    """
    from integrations.imports.durable import recover_outboxes
    from integrations.models import ImportRun

    recover_outboxes()

    time_limit = settings.CELERY_TASK_TIME_LIMIT
    if not time_limit:
        return 0
    now = timezone.now()
    # Sources with their own, longer limits are judged against those.
    stale = Q(
        started_at__lt=now - timedelta(seconds=abandoned_after_seconds("", time_limit)),
    ) & ~Q(source__in=SOURCE_TIME_LIMITS)
    for source in SOURCE_TIME_LIMITS:
        stale |= Q(
            source=source,
            started_at__lt=now
            - timedelta(seconds=abandoned_after_seconds(source, time_limit)),
        )
    closed = (
        ImportRun.objects.filter(status=ImportRun.Status.RUNNING)
        .filter(stale)
        .filter(Q(lease_expires_at__isnull=True) | Q(lease_expires_at__lte=now))
        .exclude(source__in=SELF_RESCHEDULING_IMPORT_SOURCES)
        .update(status=ImportRun.Status.FAILED, finished_at=now)
    )
    if closed:
        logger.warning(
            "Marked %s import run(s) failed: still running past the task time "
            "limit, so the worker was killed or restarted mid-import",
            closed,
        )
    return closed


def _run_file_import(importer_func, file, user_id, mode, **extra_kwargs):
    """Run a file-backed importer while cleaning staged task payloads."""
    from integrations.tasks._media_imports import import_media

    with open_import_file(file) as uploaded_file:
        return import_media(
            importer_func,
            uploaded_file,
            user_id,
            mode,
            **extra_kwargs,
        )


def import_run_counts(imported_counts):
    """Return ``(created_count, updated_count)`` for an ``ImportRun`` row."""
    created = imported_counts.get("created")
    updated = imported_counts.get("updated")
    if created is not None or updated is not None:
        return created or 0, updated or 0
    media_total = sum(
        count
        for key, count in imported_counts.items()
        if key not in IMPORT_COUNT_METRIC_KEYS and isinstance(count, int)
    )
    return media_total, 0


def has_imported_media(imported_counts):
    """Return whether an importer run changed any media rows."""
    created, updated = import_run_counts(imported_counts)
    return created + updated > 0


def format_media_type_display(count, media_type):
    """Format media type display with proper pluralization."""
    if count == 0:
        return None
    if count == 1:
        return f"{count} {dict(MediaTypes.choices).get(media_type, media_type)}"
    return f"{count} {app_tags.media_type_readable_plural(media_type)}"


def format_import_message(imported_counts, warning_messages=None):
    """Format the import result message based on counts and warnings."""
    parts = []

    # Handle music specially - show both play events and unique tracks
    music_play_events = imported_counts.get(MediaTypes.MUSIC.value, 0)
    music_unique_tracks = imported_counts.get("music_unique_tracks", 0)

    if music_play_events > 0:
        if music_unique_tracks > 0:
            # Show both play events and unique tracks
            parts.append(
                f"{music_play_events} music play event{'s' if music_play_events != 1 else ''} "
                f"({music_unique_tracks} unique track{'s' if music_unique_tracks != 1 else ''})",
            )
        else:
            # Fallback to standard format if unique tracks not available
            parts.append(
                format_media_type_display(music_play_events, MediaTypes.MUSIC.value)
            )

    # Add other media types (excluding music which we handled above)
    media_type_values = set(MediaTypes.values)
    for media_type, count in imported_counts.items():
        if (
            media_type in (MediaTypes.MUSIC.value, "music_unique_tracks")
            or media_type not in media_type_values
        ):
            continue
        formatted = format_media_type_display(count, media_type)
        if formatted:
            parts.append(formatted)

    collection_count = imported_counts.get("collection", 0)
    if collection_count > 0:
        parts.append(
            f"{collection_count} collection "
            f"entr{'ies' if collection_count != 1 else 'y'}",
        )

    lists_count = imported_counts.get("lists", 0)
    if lists_count > 0:
        parts.append(f"{lists_count} list{'s' if lists_count != 1 else ''}")

    parts = [p for p in parts if p is not None]

    if not parts:
        info_message = "No media was imported."
    else:
        info_message = f"Imported {helpers.join_with_commas_and(parts)}."

    metric_parts = []
    metric_mappings = [
        ("created", "created"),
        ("updated", "updated"),
        ("skipped", "skipped (unchanged)"),
        ("unchanged", "unchanged"),
        ("baseline", "starting playtimes remembered"),
        ("skipped_missing_ids", "skipped (missing IDs)"),
        ("skipped_existing", "skipped (existing)"),
        ("skipped_unknown_type", "skipped (unknown type)"),
        ("skipped_other_user", "skipped (other users)"),
    ]
    for key, label in metric_mappings:
        value = imported_counts.get(key)
        if value:
            metric_parts.append(f"{value} {label}")

    if metric_parts:
        info_message = f"{info_message} {helpers.join_with_commas_and(metric_parts)}."

    if warning_messages:
        return f"{info_message} {ERROR_TITLE} {warning_messages}"
    return info_message


def format_watchlist_sync_message(sync_counts, warning_messages=None):
    """Format the Plex watchlist sync result message."""
    created_parts = []
    movie_count = sync_counts.get(MediaTypes.MOVIE.value, 0)
    tv_count = sync_counts.get(MediaTypes.TV.value, 0)

    if movie_count:
        created_parts.append(
            f"{movie_count} movie{'s' if movie_count != 1 else ''}",
        )
    if tv_count:
        created_parts.append(
            f"{tv_count} TV show{'s' if tv_count != 1 else ''}",
        )

    if created_parts:
        info_message = (
            "Synced Plex watchlist. "
            f"Imported {helpers.join_with_commas_and(created_parts)}."
        )
    else:
        info_message = "Synced Plex watchlist. No new watchlist media was imported."

    metric_parts = []
    metric_mappings = [
        ("created", "created"),
        ("linked_existing", "linked to existing media"),
        ("removed", "removed from Planning"),
        ("deactivated", "deactivated"),
        ("skipped_missing_ids", "skipped (missing IDs)"),
        ("skipped_unknown_type", "skipped (unknown type)"),
        ("skipped_metadata", "skipped (metadata errors)"),
    ]
    for key, label in metric_mappings:
        value = sync_counts.get(key)
        if value:
            metric_parts.append(f"{value} {label}")

    if metric_parts:
        info_message = f"{info_message} {helpers.join_with_commas_and(metric_parts)}."

    if warning_messages:
        return f"{info_message} {ERROR_TITLE} {warning_messages}"
    return info_message
