"""Periodic raw SQLite snapshot for disaster recovery (#1053).

CSV export (integrations.exports.write_backup) cannot replace a physically
damaged db.sqlite3: it needs a working Django install to import into, and it
carries only media/ratings/lists, not accounts or integration credentials.
This task writes a verified, atomically-published copy of the live database
itself, so a corrupted file has something real to be replaced with.
"""

import logging
import time
from contextlib import suppress
from pathlib import Path

from celery import shared_task
from django.conf import settings

from app import backup_status
from app.memory_envelope import sample_memory
from config.sqlite_integrity import create_live_database_snapshot

logger = logging.getLogger(__name__)


@shared_task(name="Write database snapshot", ignore_result=True)
def write_database_snapshot():
    """Write a verified raw .sqlite3 snapshot to BACKUP_DIR/database/.

    No-ops when the deployment uses PostgreSQL, or when the feature is
    disabled -- checked here, not only at schedule-definition time, so
    toggling DB_SNAPSHOT_ENABLED takes effect on the next run. Failures raise
    so Celery reports FAILURE rather than a misleading successful task. Celery
    contains task exceptions; this task does not configure automatic retries.
    """
    if not settings.USING_SQLITE_DATABASE:
        return {"status": "skipped", "reason": "postgres"}
    if not settings.DB_SNAPSHOT_ENABLED:
        return {"status": "skipped", "reason": "disabled"}

    # Both cgroup envelopes, bracketing the copy. Production's largest
    # observed excursion -- roughly 1-2 GB to above 5 GB, decaying over hours
    # -- coincides with this task, and the shape (a couple of GB of write I/O,
    # no comparable process growth) points at filesystem page cache rather
    # than at anything Python allocated. This records that rather than
    # inferring it: the copy is written and then read back in full by
    # PRAGMA quick_check, so a snapshot charges the cgroup roughly twice its
    # own size in `file` unless the cache is released afterwards.
    before = sample_memory()
    started = time.perf_counter()
    outcome: dict = {}
    try:
        dest_dir = Path(settings.BACKUP_DIR) / "database"
        path = create_live_database_snapshot(
            str(settings.FLOPPY_DB_PATH),
            dest_dir,
            max_keep=settings.DB_SNAPSHOT_RETENTION_COUNT,
            timeout_seconds=settings.SQLITE_BUSY_TIMEOUT_SECONDS,
            outcome=outcome,
        )
    except Exception as error:
        logger.exception("Database snapshot task failed unexpectedly")
        backup_status.record_failure(backup_status.SNAPSHOT, error)
        raise

    if path is None:
        message = "Database snapshot failed; no verified backup was published"
        backup_status.record_failure(backup_status.SNAPSHOT, message)
        raise RuntimeError(message)

    backup_status.record_success(backup_status.SNAPSHOT, path.name)

    duration_ms = (time.perf_counter() - started) * 1000
    _log_snapshot_envelope(path, before, duration_ms, outcome)
    return {"status": "ok", "path": str(path)}


def _format(value) -> str:
    """Render a possibly-unknown measurement, never as a misleading zero."""
    return "unknown" if value is None else str(value)


def _log_snapshot_envelope(
    path: Path, before, duration_ms: float, outcome: dict | None = None
) -> None:
    """Emit one structured line tying the snapshot to the cgroup it moved.

    Only the file name is logged, not the full path: BACKUP_DIR is operator
    configuration and the name alone is enough to match this line against the
    page_cache_release line the writer emits for the same snapshot.
    ``pinned``, ``steps`` and ``restarts`` say how the copy ran: a pinned WAL
    copy should show restarts=0 however busy the instance was.
    """
    outcome = outcome or {}
    size_bytes = None
    with suppress(OSError):
        size_bytes = path.stat().st_size
    after = sample_memory()
    logger.info(
        "db_snapshot name=%s bytes=%s duration_ms=%.0f "
        "cgroup_before=%s cgroup_after=%s anon_before=%s anon_after=%s "
        "file_before=%s file_after=%s pinned=%s steps=%s restarts=%s",
        path.name,
        _format(size_bytes),
        duration_ms,
        _format(before.cgroup_current_bytes),
        _format(after.cgroup_current_bytes),
        _format(before.cgroup_anon_bytes),
        _format(after.cgroup_anon_bytes),
        _format(before.cgroup_file_bytes),
        _format(after.cgroup_file_bytes),
        _format(outcome.get("pinned")),
        _format(outcome.get("steps")),
        _format(outcome.get("restarts")),
    )
