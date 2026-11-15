from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import sqlite3
import stat
import sys
import tempfile
import time
from collections import Counter
from contextlib import ExitStack, closing, contextmanager, nullcontext, suppress
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

# Shared by the startup messages and the recovery page so the command a person
# is told to run is the one the docs verify. "sqlite_startup" is not an
# executable; it only runs through manage.py from the image's /floppy.
PREPARE_COMMAND = "python manage.py sqlite_startup --prepare-migrations"
RECOVERY_DOC_URL = (
    "https://github.com/dannyvfilms/Floppy/blob/latest/docs/sqlite-startup-recovery.md"
)
_CORRUPTION_HINT = (
    "[entrypoint] The SQLite file may be corrupt (see README: SQLite "
    "network filesystem caveat)"
)
_RELATIONSHIP_HINT = (
    "[entrypoint] Back up the SQLite file, then inspect these relationship "
    "errors before you restart"
)
_BUSY_HINT = (
    "[entrypoint] The SQLite database is busy. Stop other Floppy processes, "
    "then restart"
)
_ALBUM_ARTIST_TABLE = "app_albumartist"
_ACTION_ENV = "FLOPPY_SQLITE_CONFLICT_ACTION"
_MAX_CONFLICT_SAMPLES = 5
# Snapshot copy: pages per backup step and the pause between steps.
_SNAPSHOT_STEP_PAGES = 256
_SNAPSHOT_STEP_PAUSE_SECONDS = 0.002
# A pinned WAL copy never restarts. More than this means the pin did not hold
# or (rollback journal) the instance is too busy to copy unpinned.
_SNAPSHOT_MAX_RESTARTS = 3
_MAX_AFFECTED_TITLES = 5
_REPORT_SUFFIX = ".integrity.json"
_DECISION_SUFFIX = ".integrity.decision"
_STATUS_SUFFIX = ".integrity.status.json"
_VERIFIED_SUFFIX = ".integrity.verified.json"
# The nightly snapshot re-verifies the database every day, so a record older
# than two days means snapshots stopped and the startup scan must run again.
_VERIFIED_FRESH_SECONDS = 48 * 3600.0
_WARM_CHUNK_BYTES = 8 * 1024 * 1024
_WARM_REPORT_INTERVAL_SECONDS = 10.0
_RECOVERY_PAGE_NAME = "floppy-recovery.html"
_PROGRESS_QUIET_AFTER_SECONDS = 45.0
# Lock contention and scan duration are different failures and need different
# bounds. A long busy timeout turns a fast "another process holds the database"
# answer into a long stall, so the diagnostic scan waits briefly for the lock and
# bounds the scan itself with a progress handler instead.
_SCAN_BUSY_TIMEOUT_SECONDS = 5.0
_SCAN_PROGRESS_INSTRUCTIONS = 10_000


def _log(message: str) -> None:
    """Write one operator line to stderr, where the entrypoint collects it."""
    print(message, file=sys.stderr)  # noqa: T201


class _ResolvedReportPublicationError(OSError):
    def __init__(self, message: str, *, previous_restored: bool):
        super().__init__(message)
        self.previous_restored = previous_restored


def _inspect_foreign_keys(
    conn: sqlite3.Connection, only_table: str | None = None
) -> dict:
    """Summarize every relationship conflict without retaining every row.

    ``only_table`` limits the scan to one table (``foreign_key_check(table)``).
    """
    counts = Counter()
    samples = []
    conflict_hash = 0
    can_quarantine = True
    total = 0

    pragma = "PRAGMA foreign_key_check"
    if only_table is not None:
        pragma += "(" + _quote_identifier(only_table) + ")"
    for conflict_table, row_id, parent, foreign_key_id in conn.execute(pragma):
        total += 1
        counts[(conflict_table, parent, foreign_key_id)] += 1
        can_quarantine = can_quarantine and row_id is not None
        if len(samples) < _MAX_CONFLICT_SAMPLES:
            samples.append(
                {
                    "foreign_key_id": foreign_key_id,
                    "parent": parent,
                    "row": row_id,
                    "table": conflict_table,
                }
            )
        conflict_hash = (
            conflict_hash
            + int.from_bytes(
                sha256(
                    json.dumps(
                        [conflict_table, row_id, parent, foreign_key_id],
                        separators=(",", ":"),
                    ).encode()
                ).digest(),
                byteorder="big",
            )
        ) % (1 << 256)

    groups = [
        {
            "count": count,
            "foreign_key_id": foreign_key_id,
            "parent": parent,
            "table": table,
        }
        for (table, parent, foreign_key_id), count in sorted(counts.items())
    ]
    unsafe_reasons = []
    affected_tables = {table for table, _parent, _foreign_key_id in counts}
    for table in sorted(affected_tables):
        schema_rows = conn.execute(
            "SELECT type, sql FROM main.sqlite_schema WHERE name = ? COLLATE BINARY",
            [table],
        ).fetchall()
        if len(schema_rows) != 1 or schema_rows[0][0] != "table":
            unsafe_reasons.append(f"affected object {table!r} is not one main table")
            continue
        shadowed_aliases = sorted(
            name
            for (name,) in conn.execute(
                "SELECT name FROM pragma_table_xinfo(?, 'main')",
                [table],
            )
            if name.casefold() in {"rowid", "_rowid_", "oid"}
        )
        if shadowed_aliases:
            unsafe_reasons.append(
                f"affected table {table!r} declares {shadowed_aliases!r} and "
                "shadows hidden row identity"
            )
        # sqlite_schema.tbl_name keeps the spelling used by CREATE TRIGGER while
        # foreign_key_check reports the canonical table name. SQLite resolves
        # both case-insensitively, so a BINARY match would miss a trigger
        # declared as ON CHILD and let quarantine fire it.
        triggers = conn.execute(
            "SELECT name FROM main.sqlite_schema "
            "WHERE type = 'trigger' AND tbl_name = ? COLLATE NOCASE "
            "UNION ALL SELECT name FROM temp.sqlite_schema "
            "WHERE type = 'trigger' AND tbl_name = ? COLLATE NOCASE",
            [table, table],
        )
        for (trigger_name,) in triggers:
            unsafe_reasons.append(
                f"affected table {table!r} has trigger {trigger_name!r}; "
                "DELETE trigger safety cannot be proven"
            )

    if not can_quarantine:
        unsafe_reasons.append("one or more affected tables use WITHOUT ROWID")
    can_quarantine = can_quarantine and not unsafe_reasons
    fingerprint_source = json.dumps(
        {
            "conflict_hash": f"{conflict_hash:064x}",
            "groups": groups,
            "total": total,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return {
        "can_quarantine": can_quarantine,
        "fingerprint": sha256(fingerprint_source.encode()).hexdigest(),
        "groups": groups,
        "samples": samples,
        "total_conflicts": total,
        "unsafe_reasons": unsafe_reasons,
    }


def _incident_report_path(db_path: str) -> Path:
    database_path = Path(db_path).resolve()
    return database_path.with_name(f"{database_path.name}{_REPORT_SUFFIX}")


def _read_incident_report(db_path: str) -> dict | None:
    report_path = _incident_report_path(db_path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(report_path, flags)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return None
        with os.fdopen(descriptor) as report_file:
            descriptor = -1
            report = json.load(report_file)
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return None
    finally:
        if descriptor != -1:
            os.close(descriptor)
    if not isinstance(report, dict) or report.get("database") != str(
        Path(db_path).resolve()
    ):
        return None
    return report


def _status_path(db_path: str) -> Path:
    database_path = Path(db_path).resolve()
    return database_path.with_name(f"{database_path.name}{_STATUS_SUFFIX}")


def write_startup_status(
    db_path: str,
    *,
    status: str,
    phase: str,
    started_at: str,
    elapsed_seconds: float,
    read_bytes: int | None = None,
    progress_callbacks: int | None = None,
    phase_started_at: str | None = None,
    last_progress_at: str | None = None,
    error_class: str | None = None,
    error_message: str | None = None,
    version: str | None = None,
    commit_sha: str | None = None,
) -> None:
    """Persist a bounded, non-secret snapshot of the startup scan's progress.

    Unlike the incident report, this sidecar carries nothing that needs the
    approval-token protections behind ``_publish_report``'s default mode, so
    it is published world-readable. A write failure here is logged and
    swallowed: this file only describes what was happening when the scan was
    last observed, and must never itself change whether startup may continue.
    """
    updated_at = datetime.now(UTC)
    payload = {
        "commit_sha": commit_sha,
        "database": str(Path(db_path).resolve()),
        "elapsed_seconds": elapsed_seconds,
        "error_class": error_class,
        "error_message": error_message,
        "phase": phase,
        "phase_started_at": phase_started_at or started_at,
        "progress_callbacks": progress_callbacks,
        "last_progress_at": last_progress_at,
        "read_bytes": read_bytes,
        "schema_version": 1,
        "started_at": started_at,
        "status": status,
        "updated_at": updated_at.isoformat(),
        "version": version,
    }
    diagnostics = startup_progress_diagnostics(payload, now=updated_at)
    payload.update(
        {
            "progress_age_seconds": diagnostics["last_progress_age_seconds"],
            "progress_rate_per_minute": diagnostics["progress_rate_per_minute"],
            "progress_state": diagnostics["progress_state"],
        }
    )
    contents = json.dumps(payload, sort_keys=True) + "\n"
    try:
        _publish_report(_status_path(db_path), contents, mode=0o644)
    except OSError as error:
        _log(f"[entrypoint] Could not publish SQLite startup status: {error}")


def _read_sidecar(sidecar_path: Path, db_path: str) -> dict | None:
    """Read a JSON sidecar that names this database, or ``None``."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(sidecar_path, flags)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return None
        with os.fdopen(descriptor) as sidecar_file:
            descriptor = -1
            payload = json.load(sidecar_file)
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return None
    finally:
        if descriptor != -1:
            os.close(descriptor)
    if not isinstance(payload, dict) or payload.get("database") != str(
        Path(db_path).resolve()
    ):
        return None
    return payload


def read_startup_status(db_path: str) -> dict | None:
    """Read the startup-status sidecar, or ``None`` if absent or unreadable."""
    return _read_sidecar(_status_path(db_path), db_path)


def _verified_path(db_path: str) -> Path:
    database_path = Path(db_path).resolve()
    return database_path.with_name(f"{database_path.name}{_VERIFIED_SUFFIX}")


def same_verified_file(record: dict, path: Path, file_stat) -> bool:
    """Whether a live file is the one a verification record describes.

    ``st_dev`` is not compared: btrfs subvolumes, device-mapper, and loop
    devices get it at mount time, so it changes across reboots. ``f_fsid``
    names the filesystem stably (on btrfs it includes the subvolume, so a
    rolled-back snapshot, whose inodes repeat, does not match).
    """
    if record.get("inode") != file_stat.st_ino:
        return False
    if "fsid" not in record:
        # shortcut: a record from before "fsid" was stored is matched by inode
        # alone until the next verification rewrites it.
        return True
    return record["fsid"] == os.statvfs(path).f_fsid


def record_verified_database(db_path: str, *, source: str) -> None:
    """Record a historical verification and its source file identity.

    ``startup`` is the legacy/offline policy's full scan; ``snapshot`` checks
    structure only and cannot vouch for relationships. ``offline-snapshot``
    checks both. This record never authorizes pending migrations: that needs
    an unchanged recovery snapshot and a separate migration permit.
    """
    database_path = Path(db_path).resolve()
    try:
        file_stat = database_path.stat()
        payload = {
            "commit_sha": os.environ.get("COMMIT_SHA"),
            "database": str(database_path),
            "device": file_stat.st_dev,
            "fsid": os.statvfs(database_path).f_fsid,
            "inode": file_stat.st_ino,
            "schema_version": 1,
            "source": source,
            "verified_at": datetime.now(UTC).isoformat(),
            "version": os.environ.get("VERSION"),
        }
        _publish_report(
            _verified_path(db_path),
            json.dumps(payload, sort_keys=True) + "\n",
            mode=0o644,
        )
    except OSError as error:
        _log(f"[integrity] Could not record the verified database: {error}")


def recent_verification(
    db_path: str,
    previous_status: dict | None,
) -> tuple[dict | None, str]:
    """Return the verification record that lets startup skip the full scan.

    The full scan exists to catch storage damage and relationship conflicts
    before migrations touch them. It is skipped only while a recent full check
    still vouches for this exact file under this exact image, and nothing since
    has cast doubt on it. ``previous_status`` is the startup-status sidecar as
    the previous start left it, read before this start overwrites it. Returns
    ``(record, reason)``; ``record`` is ``None`` when the full scan must run,
    and ``reason`` says why either way.
    """
    record = _read_sidecar(_verified_path(db_path), db_path)
    if record is None:
        return None, "no verification on record"
    if record.get("source") == "snapshot":
        return None, "snapshot quick_check does not verify relationships"
    verified_at = _parse_timestamp(record.get("verified_at"))
    now = datetime.now(UTC)
    if verified_at is None or verified_at > now:
        return None, "verification record has no usable time"
    age = (now - verified_at).total_seconds()
    if age > _VERIFIED_FRESH_SECONDS:
        return None, f"last verification is {age / 3600:.0f}h old"
    # A local build bakes the placeholder "unknown" (Dockerfile ARG default),
    # so two different local images would otherwise look identical.
    commit_sha = (os.environ.get("COMMIT_SHA") or "").strip()
    if (
        commit_sha.lower() in {"", "unknown", "none"}
        or record.get("commit_sha") != commit_sha
    ):
        return None, "the image changed since the last verification"
    database_path = Path(db_path).resolve()
    try:
        same = same_verified_file(record, database_path, database_path.stat())
    except OSError as error:
        return None, f"database could not be inspected: {error}"
    if not same:
        return None, "the database file was replaced since the last verification"
    report = _read_incident_report(db_path)
    if report and report.get("status") != "resolved":
        return None, f"a recovery report is {report.get('status')}"
    if previous_status and previous_status.get("status") != "ok":
        return None, (
            f"the previous startup check ended as {previous_status.get('status')}"
        )
    return record, (
        f"verified by {record.get('source', 'unknown')} at {record['verified_at']}"
    )


def warm_database_cache(
    db_path: str,
    *,
    on_progress: Callable[[int], None] | None = None,
) -> tuple[int, float]:
    """Read the database and its WAL front to back, once, before the scan.

    ``PRAGMA quick_check`` walks b-trees, so on a large, long-lived file it
    issues hundreds of thousands of small reads scattered across the disk.
    Slow storage (a NAS, a spinning disk) serves those at a few hundred per
    second, which turns a one-minute check into a many-minute one. One
    sequential pass is the fastest way a disk can deliver the same bytes, and
    it leaves them in the page cache for the scan to read from memory. The
    scan reads every page anyway, so this changes the order of the reads, not
    how much is cached.

    Best-effort: a read error ends the warm-up early and the scan runs as it
    always did. Returns the bytes read and the seconds it took.
    """
    started = time.monotonic()
    last_report = started
    total = 0
    buffer = bytearray(_WARM_CHUNK_BYTES)
    for path in (Path(db_path), Path(f"{db_path}-wal")):
        try:
            with path.open("rb", buffering=0) as source:
                advise = getattr(os, "posix_fadvise", None)
                sequential = getattr(os, "POSIX_FADV_SEQUENTIAL", None)
                if advise is not None and sequential is not None:
                    with suppress(OSError):
                        advise(source.fileno(), 0, 0, sequential)
                while count := source.readinto(buffer):
                    total += count
                    now = time.monotonic()
                    if (
                        on_progress
                        and now - last_report >= _WARM_REPORT_INTERVAL_SECONDS
                    ):
                        on_progress(total)
                        last_report = now
        except FileNotFoundError:
            continue
        except OSError as error:
            _log(f"[integrity] Cache warm-up stopped early at {path.name}: {error}")
            break
    return total, time.monotonic() - started


def _live_elapsed_text(status: dict) -> str:
    """Compute elapsed time as of now, not as of the sidecar's last write.

    A phase that blocks on one long call has no progress callback to update
    the sidecar with -- ``PRAGMA quick_check`` on a large file, for example,
    writes its status once at the start of the phase and not again until it
    finishes. Echoing the stored ``elapsed_seconds`` verbatim would freeze
    every heartbeat at that phase's start time for its entire duration,
    which is exactly the case a heartbeat exists to cover.
    """
    try:
        started = datetime.fromisoformat(str(status.get("started_at")))
    except (TypeError, ValueError):
        elapsed = status.get("elapsed_seconds")
        return f"{elapsed:.0f}s" if isinstance(elapsed, int | float) else "unknown"
    elapsed = (datetime.now(UTC) - started).total_seconds()
    return f"{max(elapsed, 0):.0f}s"


def _parse_timestamp(value: object) -> datetime | None:
    try:
        timestamp = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp


def _timestamp_age_seconds(value: object, *, now: datetime) -> float | None:
    timestamp = _parse_timestamp(value)
    if timestamp is None:
        return None
    return max((now - timestamp).total_seconds(), 0.0)


def startup_progress_diagnostics(
    status: dict,
    *,
    now: datetime | None = None,
) -> dict:
    """Calculate operator-facing progress facts from one status snapshot.

    A quiet progress state means that no SQLite VM progress callback has been
    observed recently. It is a useful warning about slow I/O or a blocked
    operation, not proof that the database is corrupt or permanently stuck.
    Timeout snapshots use their own publication time as the reference so the
    report describes the scan when it stopped, even if the page is opened much
    later.
    """
    reference = now or datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    if status.get("status") == "timeout":
        timeout_at = _parse_timestamp(status.get("updated_at"))
        if timeout_at is not None:
            reference = timeout_at

    phase_elapsed = _timestamp_age_seconds(
        status.get("phase_started_at") or status.get("started_at"),
        now=reference,
    )
    progress_age = _timestamp_age_seconds(
        status.get("last_progress_at"),
        now=reference,
    )
    progress_callbacks = status.get("progress_callbacks")
    if not isinstance(progress_callbacks, int | float):
        progress_callbacks = None

    if progress_age is None and progress_callbacks == 0:
        progress_age = phase_elapsed
        progress_state = (
            "quiet"
            if progress_age is not None and progress_age > _PROGRESS_QUIET_AFTER_SECONDS
            else "none_yet"
        )
    elif progress_age is None:
        progress_state = "unknown"
    elif progress_age > _PROGRESS_QUIET_AFTER_SECONDS:
        progress_state = "quiet"
    else:
        progress_state = "active"

    progress_rate = None
    if progress_callbacks is not None and phase_elapsed and phase_elapsed > 0:
        progress_rate = progress_callbacks / phase_elapsed * 60

    return {
        "last_progress_age_seconds": progress_age,
        "phase_elapsed_seconds": phase_elapsed,
        "progress_callbacks": progress_callbacks,
        "progress_rate_per_minute": progress_rate,
        "progress_state": progress_state,
    }


def _format_seconds(value: object) -> str:
    return f"{value:.0f}s" if isinstance(value, int | float) else "unknown"


def print_startup_heartbeat(db_path: str, extra: str = "") -> None:
    """Emit one operator heartbeat line from the startup-status sidecar.

    Called periodically by the entrypoint while the scan runs, in place of the
    ``/proc/<pid>/io`` polling this used to do directly. Best-effort only: a
    missing or unreadable sidecar still produces a line rather than silence.
    """
    status = read_startup_status(db_path)
    if status is None:
        suffix = f" {extra}" if extra else ""
        _log(f"[entrypoint] SQLite integrity scan heartbeat: still running{suffix}")
        return
    phase = status.get("phase", "unknown")
    diagnostics = startup_progress_diagnostics(status)
    detail = (
        f"phase={phase} elapsed={_live_elapsed_text(status)} "
        f"phase_elapsed={_format_seconds(diagnostics['phase_elapsed_seconds'])}"
    )
    read_bytes = status.get("read_bytes")
    if isinstance(read_bytes, int | float) and read_bytes:
        detail += f" read={read_bytes / 1_048_576:.0f}MB"
    progress_callbacks = status.get("progress_callbacks")
    if isinstance(progress_callbacks, int | float):
        detail += f" progress_callbacks={progress_callbacks:.0f}"
    progress_rate = diagnostics["progress_rate_per_minute"]
    if isinstance(progress_rate, int | float):
        detail += f" progress_rate={progress_rate:.1f}/min"
    progress_age = diagnostics["last_progress_age_seconds"]
    progress_age_text = _format_seconds(progress_age)
    detail += (
        f" last_progress={progress_age_text}_ago"
        if progress_age_text != "unknown"
        else " last_progress=unknown"
    )
    detail += f" progress_state={diagnostics['progress_state']}"
    if extra:
        detail += f" {extra}"
    _log(f"[entrypoint] SQLite integrity scan heartbeat: {detail}")


def mark_startup_status_timeout(
    db_path: str,
    timeout_seconds: float,
    *,
    reason: str | None = None,
    read_bytes: int | None = None,
) -> None:
    """Ensure the sidecar reflects a timeout even if the scanner was killed.

    Also logs the last observed phase/elapsed/bytes. The startup watchdog
    stops the scanner before it can publish its own terminal status, so the
    watchdog calls this immediately after. It preserves whatever the scanner
    last reported and only fills in what is missing. ``reason`` says why the
    scan was stopped; ``read_bytes`` is what the watchdog saw it read.
    """
    previous = read_startup_status(db_path) or {}
    phase = previous.get("phase", "unknown")
    elapsed = previous.get("elapsed_seconds")
    elapsed = (
        float(elapsed) if isinstance(elapsed, int | float) else float(timeout_seconds)
    )
    elapsed = max(elapsed, float(timeout_seconds))
    if read_bytes is None:
        read_bytes = previous.get("read_bytes")
    progress_callbacks = previous.get("progress_callbacks")
    phase_started_at = previous.get("phase_started_at")
    last_progress_at = previous.get("last_progress_at")
    write_startup_status(
        db_path,
        status="timeout",
        phase=phase,
        started_at=previous.get("started_at") or datetime.now(UTC).isoformat(),
        elapsed_seconds=elapsed,
        read_bytes=read_bytes,
        progress_callbacks=progress_callbacks,
        phase_started_at=phase_started_at,
        last_progress_at=last_progress_at,
        error_class="timeout",
        error_message=reason or f"scan exceeded {timeout_seconds:g}s",
        version=previous.get("version") or os.environ.get("VERSION"),
        commit_sha=previous.get("commit_sha") or os.environ.get("COMMIT_SHA"),
    )
    timeout_status = read_startup_status(db_path) or previous
    diagnostics = startup_progress_diagnostics(timeout_status)
    detail = (
        f"phase={phase} elapsed={elapsed:.0f}s "
        f"phase_elapsed={_format_seconds(diagnostics['phase_elapsed_seconds'])}"
    )
    if isinstance(read_bytes, int | float) and read_bytes:
        detail += f" read={read_bytes / 1_048_576:.0f}MB"
    if isinstance(progress_callbacks, int | float):
        detail += f" progress_callbacks={progress_callbacks:.0f}"
    progress_rate = diagnostics["progress_rate_per_minute"]
    if isinstance(progress_rate, int | float):
        detail += f" progress_rate={progress_rate:.1f}/min"
    progress_age = diagnostics["last_progress_age_seconds"]
    progress_age_text = _format_seconds(progress_age)
    detail += (
        f" last_progress={progress_age_text}_ago"
        if progress_age_text != "unknown"
        else " last_progress=unknown"
    )
    detail += f" progress_state={diagnostics['progress_state']}"
    because = f" ({reason})" if reason else ""
    _log(
        "[entrypoint] SQLite integrity scan timed out after "
        f"{timeout_seconds:g}s{because}; last observed {detail}",
    )


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_report(report_path: Path, contents: str, *, mode: int = 0o600) -> None:
    """Publish text atomically, so a reader never sees a half-written file.

    The report holds an approval token and stays owner-only. The recovery page
    holds nothing secret and must stay readable by the person who opens it.
    """
    descriptor, temporary_name = tempfile.mkstemp(
        dir=report_path.parent,
        prefix=f".{report_path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w") as report_file:
            descriptor = -1
            report_file.write(contents)
            report_file.flush()
            os.fsync(report_file.fileno())
        temporary_path.replace(report_path)
        _fsync_directory(report_path.parent)
    finally:
        if descriptor != -1:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)


def _write_incident_report(
    db_path: str,
    incident: dict,
    *,
    status: str,
    resolution: str | None = None,
    backup_path: Path | None = None,
    incident_token: str | None = None,
    deleted_rows: int | None = None,
) -> Path:
    """Persist a bounded machine-readable recovery report beside SQLite."""
    report_path = _incident_report_path(db_path)
    actions = {"halt": "halt"}
    if status == "blocked" and incident_token:
        actions["accept"] = f"accept:{incident_token}"
        if incident["can_quarantine"]:
            actions["quarantine"] = f"quarantine:{incident_token}"
    payload = {
        "actions": actions,
        "affected": incident.get("affected", []),
        "affected_other_titles": incident.get("other_titles", 0),
        "affected_other_titles_count": incident.get("other_titles_count", 0),
        "affected_unidentified": incident.get("unidentified", 0),
        "backup_path": str(backup_path) if backup_path else None,
        "can_quarantine": incident["can_quarantine"],
        "database": str(Path(db_path).resolve()),
        "deleted_rows": deleted_rows,
        "fingerprint": incident["fingerprint"],
        "groups": incident["groups"],
        "incident_token": incident_token,
        "recorded_at": datetime.now(UTC).isoformat(),
        "resolution": resolution,
        "samples": incident["samples"],
        "status": status,
        "total_conflicts": incident["total_conflicts"],
        "unsafe_reasons": incident.get("unsafe_reasons", []),
        "version": 1,
    }
    contents = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    previous = _read_incident_report(db_path) if status == "resolved" else None
    try:
        _publish_report(report_path, contents)
    except OSError as publication_error:
        if previous is None:
            raise
        previous_contents = json.dumps(previous, indent=2, sort_keys=True) + "\n"
        try:
            _publish_report(report_path, previous_contents)
        except OSError as restoration_error:
            message = (
                "resolved report publication failed and prior report restoration "
                f"could not be confirmed: {restoration_error}"
            )
            raise _ResolvedReportPublicationError(
                message,
                previous_restored=False,
            ) from publication_error
        message = f"resolved report publication failed: {publication_error}"
        raise _ResolvedReportPublicationError(
            message,
            previous_restored=True,
        ) from publication_error
    return report_path


def _print_incident(db_path: str, incident: dict) -> None:
    shown = len(incident["samples"])
    _log(
        f"[entrypoint] Found {incident['total_conflicts']} foreign key conflict(s) "
        f"across {len(incident['groups'])} relationship group(s); showing {shown}",
    )
    for sample in incident["samples"]:
        _log(
            "[entrypoint] Database foreign key check failed: "
            f"table={sample['table']!r}, row={sample['row']!r}, "
            f"parent={sample['parent']!r}",
        )
    _log(
        f"[entrypoint] Incident fingerprint: {incident['fingerprint']}",
    )
    _log(
        f"[entrypoint] Bounded report: {_incident_report_path(db_path)}",
    )


def _print_resolution_options(incident: dict, incident_token: str) -> None:
    _log(
        "[entrypoint] Resolution options (back up the database first):",
    )
    _log(
        "[entrypoint] - Restore a known-good SQLite backup, then restart.",
    )
    _log(
        "[entrypoint] - Accept without changing rows: "
        f"{_ACTION_ENV}=accept:{incident_token}",
    )
    if incident["can_quarantine"]:
        _log(
            "[entrypoint] - Back up and remove orphaned child rows: "
            f"{_ACTION_ENV}=quarantine:{incident_token}",
        )
    else:
        for reason in incident.get("unsafe_reasons", []):
            _log(f"[entrypoint] - Manual repair required: {reason}")


def _decision_path(db_path: str) -> Path:
    database_path = Path(db_path).resolve()
    return database_path.with_name(f"{database_path.name}{_DECISION_SUFFIX}")


def _read_decision(db_path: str, incident: dict, incident_token: str) -> str | None:
    """Read a choice made on the recovery page, then remove the file.

    The choice carries the identity of the incident it was made for. A choice
    made for an earlier incident must never apply to a later one, so the file
    is held to the same test as the environment variable.
    """
    decision_path = _decision_path(db_path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(decision_path, flags)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return None
        with os.fdopen(descriptor) as decision_file:
            descriptor = -1
            decision = json.load(decision_file)
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        decision = None
    finally:
        if descriptor != -1:
            os.close(descriptor)
    # Remove the file whether or not it was valid. A choice is used once.
    with suppress(OSError):
        decision_path.unlink()
    if not isinstance(decision, dict):
        return None
    if decision.get("fingerprint") != incident["fingerprint"]:
        _log(
            "[entrypoint] The choice on file was made for a different problem; "
            "using halt",
        )
        return None
    token = decision.get("token")
    if token and token != incident_token:
        _log("[entrypoint] The choice on file has an old code; using halt")
        return None
    action = decision.get("action")
    return action if action in {"accept", "quarantine"} else None


def _selected_action(incident: dict, incident_token: str) -> str:
    configured = os.environ.get(_ACTION_ENV, "halt").strip()
    if not configured or configured == "halt":
        return "halt"

    action, separator, supplied_token = configured.partition(":")
    if action not in {"accept", "quarantine"}:
        _log(
            f"[entrypoint] Invalid {_ACTION_ENV}={configured!r}; using halt",
        )
        return "halt"
    if separator and supplied_token != incident_token:
        # The operator supplied a token, but it does not match current incident
        _log(
            f"[entrypoint] {_ACTION_ENV} does not carry the current incident "
            f"token for fingerprint {incident['fingerprint']}; using halt",
        )
        return "halt"
    return action


def _prune_recovery_backups(
    recovery_dir: Path, max_keep: int = 10, *, newest: Path | None = None
) -> None:
    """Retain only the most recent recovery backups to bound disk usage."""
    max_keep = max(1, max_keep)
    try:
        backups = [
            path
            for path in recovery_dir.glob("*.sqlite3")
            if path.is_file()
            and not path.is_symlink()
            and not path.name.startswith(".")
        ]
        if len(backups) <= max_keep:
            return
        backups.sort(key=lambda p: (p == newest, p.stat().st_mtime), reverse=True)
        for stale in backups[max_keep:]:
            with suppress(OSError):
                stale.unlink()
    except OSError:
        pass


def _check_disk_space(recovery_dir: Path, database_path: Path) -> None:
    """Verify available space before attempting to stage a backup."""
    try:
        free_bytes = shutil.disk_usage(recovery_dir).free
    except (AttributeError, OSError):
        return
    db_size = database_path.stat().st_size
    if free_bytes < db_size:
        message = (
            f"insufficient free disk space in {recovery_dir} "
            f"({free_bytes} bytes free, {db_size} bytes needed for backup)"
        )
        raise OSError(message)


def _report_corruption(db_path: str, reason: str) -> None:
    """Publish the report that makes the recovery page show the damaged card.

    A damaged file cannot be repaired by removing rows, so any choice left on
    disk is stale. It is removed here; otherwise the entrypoint sees a choice
    that no code path consumes and never parks.
    """
    with suppress(OSError):
        _decision_path(db_path).unlink()
    try:
        _write_incident_report(
            db_path,
            {
                # groups and samples are read without a default, so a report
                # that omits them is never written and the page never appears.
                "groups": [],
                "samples": [],
                "total_conflicts": 0,
                "fingerprint": secrets.token_hex(32),
                "can_quarantine": False,
                "unsafe_reasons": [f"Physical corruption detected: {reason}"],
            },
            status="corrupt",
        )
    except (OSError, KeyError, TypeError, ValueError) as error:
        # The page is a courtesy; the log is the record. Say why it is missing
        # rather than hiding the reason behind a bare except.
        _log(f"[entrypoint] Could not publish the damaged-file report: {error}")


class _SnapshotVerificationError(sqlite3.DatabaseError):
    """The copy failed quick_check: the source's pages were copied verbatim."""


def _sweep_copy(staging_path) -> bool | None:
    """Page-sweep the finished copy: True ok, False damaged, None unavailable."""
    from config import sqlite_page_sweep

    try:
        result = sqlite_page_sweep.sweep(str(staging_path))
    except (
        sqlite_page_sweep.SweepUnavailableError,
        sqlite_page_sweep.SweepExpiredError,
    ):
        return None
    if result["problems"]:
        _log(f"[db-snapshot] page sweep of the copy failed: {result['problems'][:3]}")
        return False
    return True


class _SnapshotNotConvergingError(sqlite3.OperationalError):
    """The copy kept restarting because the source kept changing under it."""


@contextmanager
def _pinned_read(source):
    """Hold one read transaction on ``source`` for the duration of the block.

    The backup then copies that one snapshot. Without it, SQLite restarts a
    stepped backup from page one whenever another connection commits between
    steps, so a copy longer than the gap between commits never finishes.
    """
    isolation_level = source.isolation_level
    source.isolation_level = None
    source.execute("BEGIN")
    try:
        # BEGIN is deferred: the first read is what takes the snapshot.
        source.execute("SELECT count(*) FROM sqlite_master").fetchone()
        yield
    finally:
        try:
            source.execute("ROLLBACK")
        finally:
            source.isolation_level = isolation_level


def _copy_source(source, destination, outcome=None):
    """Copy ``source`` into ``destination`` and record how the copy went.

    Always a stepped copy with a short pause between steps, so live web and
    worker traffic keeps getting the disk.

    WAL: the copy runs inside one pinned read snapshot. WAL readers never block
    writers, so the pin costs writers nothing; it only holds back checkpoints
    until the copy ends. Without it, every commit from another connection
    restarts the copy from page one.

    Rollback journal (Floppy's fallback on SQLite builds with the WAL-reset
    bug): a held read lock, or a single-step copy, blocks every writer at
    commit for the whole copy (2.8 s for 711 MB in CONC-43, minutes for a
    multi-GB file, past the busy timeout). So the copy is not pinned, writers
    wait at most one step, and a busy instance can restart the copy. The
    restart limit turns that into a reported failure instead of a loop.
    """
    journal_mode = source.execute("PRAGMA journal_mode").fetchone()[0].lower()
    stats = {"pinned": journal_mode == "wal", "steps": 0, "restarts": 0}
    if outcome is not None:
        outcome.update(stats)
    last_remaining = None

    def progress(_status, remaining, _total):
        nonlocal last_remaining
        stats["steps"] += 1
        # A restart starts again from page one: more pages left than before.
        if last_remaining is not None and remaining > last_remaining:
            stats["restarts"] += 1
            if stats["restarts"] > _SNAPSHOT_MAX_RESTARTS:
                if outcome is not None:
                    outcome.update(stats)
                message = (
                    f"snapshot copy restarted {stats['restarts']} times "
                    "because the database kept changing; giving up"
                )
                raise _SnapshotNotConvergingError(message)
        last_remaining = remaining
        time.sleep(_SNAPSHOT_STEP_PAUSE_SECONDS)

    pin = _pinned_read(source) if stats["pinned"] else nullcontext()
    with pin:
        source.backup(destination, pages=_SNAPSHOT_STEP_PAGES, progress=progress)
    if outcome is not None:
        outcome.update(stats)


def _backup_to_staging(
    source,
    directory_descriptor,
    staging_name,
    *,
    timeout=5.0,
    verify="quick_check",
    outcome=None,
):
    """Verify a copy before handing it to the descriptor-based publisher.

    ``verify="page_sweep"`` checks the copy with the sequential page sweep
    instead of ``PRAGMA quick_check`` (random b-tree reads: hours on slow
    storage). If the sweep cannot run it falls back to ``quick_check``. What
    was actually used is stored in ``outcome["verified"]``.

    Linux opens the reserved file through its pinned directory descriptor.
    Other POSIX hosts use a private system-temporary workspace: SQLite needs
    a pathname (including for journals), and /dev/fd is not a directory-path
    substitute on macOS. Only verified bytes cross into the reserved file,
    using openat, never a potentially replaced destination directory path.
    The fallback needs one additional database-sized copy on temporary storage.
    """
    with ExitStack() as stack:
        if sys.platform.startswith("linux"):
            staging_path = Path(f"/proc/self/fd/{directory_descriptor}/{staging_name}")
        else:
            workspace = Path(stack.enter_context(tempfile.TemporaryDirectory()))
            staging_path = workspace / "snapshot.sqlite3.tmp"
            descriptor = os.open(
                staging_path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600
            )
            os.close(descriptor)
        with closing(sqlite3.connect(staging_path, timeout=timeout)) as destination:
            _copy_source(source, destination, outcome)
            swept = None
            if verify == "page_sweep":
                # The sweep opens its own connection: close this one first so
                # the copy is checkpointed and a plain file.
                destination.close()
                swept = _sweep_copy(staging_path)
                if swept is False:
                    message = "backup page sweep found structural damage"
                    raise _SnapshotVerificationError(message)
            if swept is None:
                with closing(sqlite3.connect(staging_path, timeout=timeout)) as check:
                    result = check.execute("PRAGMA quick_check").fetchall()
                if result != [("ok",)]:
                    message = "backup quick_check did not return 'ok'"
                    raise _SnapshotVerificationError(message)
            if outcome is not None:
                outcome["verified"] = "page_sweep" if swept else "quick_check"
        if not sys.platform.startswith("linux"):
            descriptor = os.open(
                staging_name,
                os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_descriptor,
            )
            with (
                os.fdopen(descriptor, "wb") as target,
                staging_path.open("rb") as verified,
            ):
                if not stat.S_ISREG(os.fstat(target.fileno()).st_mode):
                    message = "backup staging path is not a regular file"
                    raise OSError(message)
                shutil.copyfileobj(verified, target, length=1024 * 1024)


def _create_verified_backup(db_path: str, fingerprint: str) -> Path:
    """Create a consistent SQLite backup while the caller holds the write lock."""
    database_path = Path(db_path).resolve()
    recovery_dir = database_path.parent / "sqlite-recovery"
    with suppress(FileExistsError):
        recovery_dir.mkdir(mode=0o700)
    directory_flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        recovery_descriptor = os.open(recovery_dir, directory_flags)
    except OSError as error:
        message = f"recovery directory is not a safe real directory: {error}"
        raise OSError(message) from error
    recovery_stat = os.fstat(recovery_descriptor)
    try:
        if not stat.S_ISDIR(recovery_stat.st_mode):
            message = "recovery directory is not a safe real directory"
            raise OSError(message)
        os.fchmod(recovery_descriptor, 0o700)
        _check_disk_space(recovery_dir, database_path)

        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        final_name = (
            f"{database_path.stem}-{timestamp}-{fingerprint}-"
            f"{secrets.token_hex(4)}.sqlite3"
        )
        staging_name = f".{database_path.stem}-{secrets.token_hex(16)}.sqlite3.tmp"
        staging_descriptor = os.open(
            staging_name,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=recovery_descriptor,
        )
        os.close(staging_descriptor)

        source = None
        published = False
        succeeded = False
        try:
            source = sqlite3.connect(f"{database_path.as_uri()}?mode=ro", uri=True)
            _backup_to_staging(source, recovery_descriptor, staging_name)
            source.close()
            source = None

            staging_descriptor = os.open(
                staging_name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=recovery_descriptor,
            )
            try:
                if not stat.S_ISREG(os.fstat(staging_descriptor).st_mode):
                    message = "backup staging path is not a regular file"
                    raise OSError(message)
                os.fsync(staging_descriptor)
            finally:
                os.close(staging_descriptor)

            current_stat = recovery_dir.stat(follow_symlinks=False)
            if (current_stat.st_dev, current_stat.st_ino) != (
                recovery_stat.st_dev,
                recovery_stat.st_ino,
            ):
                message = "recovery directory changed during backup"
                raise OSError(message)
            os.link(
                staging_name,
                final_name,
                src_dir_fd=recovery_descriptor,
                dst_dir_fd=recovery_descriptor,
                follow_symlinks=False,
            )
            published = True
            os.unlink(staging_name, dir_fd=recovery_descriptor)
            os.fsync(recovery_descriptor)
            succeeded = True
            # Prune after the new copy is in place, never before. Pruning first
            # would leave max_keep copies and then add one more, so the folder
            # would settle one above the bound it is meant to hold.
            _prune_recovery_backups(recovery_dir, newest=recovery_dir / final_name)
            return recovery_dir / final_name
        finally:
            if source is not None:
                source.close()
            with suppress(FileNotFoundError):
                os.unlink(staging_name, dir_fd=recovery_descriptor)
            # Key the rollback off this call's own outcome. sys.exc_info() also
            # reports an exception the caller is already handling, which would
            # delete a good backup and leave the delete path without one.
            if published and not succeeded:
                with suppress(FileNotFoundError):
                    os.unlink(final_name, dir_fd=recovery_descriptor)
    finally:
        os.close(recovery_descriptor)


def create_live_database_snapshot(
    db_path: str,
    dest_dir: Path,
    *,
    max_keep: int | None,
    timeout_seconds: float,
    verify: str = "quick_check",
    outcome: dict | None = None,
) -> Path | None:
    """Write a verified SQLite snapshot from a live, concurrently-written database.

    ``verify`` and ``outcome`` are passed to ``_backup_to_staging``.

    Unlike _create_verified_backup, the caller holds no write lock here: other
    processes (gunicorn, other Celery workers) may be writing to db_path at the
    same time. WAL mode's MVCC is what makes this safe -- the backup's read
    snapshot never blocks, and is never blocked by, a concurrent writer; that
    is what the online backup API is for. This is deliberately a separate,
    function, sharing only the verified staging operation with the bootstrap
    backup. Publication and cleanup remain independent of the repair path.
    Expected I/O and SQLite failures are logged and returned as None; the
    Celery task turns that outcome into a failed task without automatic retries.
    """
    try:
        return _write_live_snapshot(
            Path(db_path).resolve(),
            dest_dir,
            max_keep=max_keep,
            timeout_seconds=timeout_seconds,
            verify=verify,
            outcome=outcome,
        )
    except sqlite3.DatabaseError as error:
        _log(f"[db-snapshot] Could not write a database snapshot: {error}")
        code = getattr(error, "sqlite_errorcode", 0) or 0
        if isinstance(error, _SnapshotVerificationError) or code & 0xFF in {
            sqlite3.SQLITE_CORRUPT,
            sqlite3.SQLITE_NOTADB,
        }:
            # A failed quick_check on the copy, or an explicit corruption code,
            # justifies a damaged-source report.
            # CANTOPEN, IOERR and FULL can describe destination failures. A
            # damaged source database must surface the same recovery-page
            # report here, not just a log line, or a live snapshot failure
            # leaves operators with no record of why backups stopped.
            _report_corruption(db_path, str(error))
        return None
    except (OSError, ValueError) as error:
        _log(f"[db-snapshot] Could not write a database snapshot: {error}")
        return None


def _release_page_cache(descriptor: int, name: str) -> str:
    """Ask the kernel to drop a written-and-durable file's page cache.

    A snapshot of a multi-GiB database is charged to the container twice
    over: once as it is written, and again as ``PRAGMA quick_check`` reads
    every page back. Both land in the cgroup's *file* accounting, and nothing
    in Floppy reads a snapshot again -- it exists for the day the live
    database is unreadable. Until then those pages are cache the container is
    paying for and nobody is using.

    This is advisory in both directions. ``POSIX_FADV_DONTNEED`` drops only
    *clean* pages, so it is issued strictly after ``fsync``: dirty pages are
    left alone by the kernel, which means the hint can never cost durability
    however it is timed. It is also never applied to the live database -- that
    file's cache is doing useful work. And the file itself is untouched: no
    truncate, no unlink, only a hint about the cache in front of it.

    Returns "issued", "unsupported" (no ``posix_fadvise`` on this platform) or
    "failed". Never raises: a hint that did not take must not turn a good
    backup into a failed one.
    """
    advise = getattr(os, "posix_fadvise", None)
    dontneed = getattr(os, "POSIX_FADV_DONTNEED", None)
    if advise is None or dontneed is None:
        return "unsupported"
    try:
        # offset 0, length 0 means "the whole file", including anything
        # appended after this descriptor was opened.
        advise(descriptor, 0, 0, dontneed)
    except OSError as error:
        _log(f"[db-snapshot] Could not release page cache for {name}: {error}")
        return "failed"
    return "issued"


def _write_live_snapshot(
    database_path: Path,
    dest_dir: Path,
    *,
    max_keep: int | None,
    timeout_seconds: float,
    verify: str = "quick_check",
    outcome: dict | None = None,
) -> Path:
    with suppress(FileExistsError):
        dest_dir.mkdir(mode=0o700, parents=True)
    directory_flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        dest_descriptor = os.open(dest_dir, directory_flags)
    except OSError as error:
        message = f"destination directory is not a safe real directory: {error}"
        raise OSError(message) from error
    dest_stat = os.fstat(dest_descriptor)
    try:
        if not stat.S_ISDIR(dest_stat.st_mode):
            message = "destination directory is not a safe real directory"
            raise OSError(message)
        os.fchmod(dest_descriptor, 0o700)
        _check_disk_space(dest_dir, database_path)

        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        final_name = f"{database_path.stem}-{timestamp}-{secrets.token_hex(4)}.sqlite3"
        staging_name = f".{database_path.stem}-{secrets.token_hex(16)}.sqlite3.tmp"
        staging_descriptor = os.open(
            staging_name,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=dest_descriptor,
        )
        os.close(staging_descriptor)

        source = None
        published = False
        succeeded = False
        try:
            source = sqlite3.connect(
                f"{database_path.as_uri()}?mode=ro",
                uri=True,
                timeout=timeout_seconds,
            )
            _backup_to_staging(
                source,
                dest_descriptor,
                staging_name,
                timeout=timeout_seconds,
                verify=verify,
                outcome=outcome,
            )
            source.close()
            source = None

            staging_check = os.open(
                staging_name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=dest_descriptor,
            )
            advice = "skipped"
            try:
                if not stat.S_ISREG(os.fstat(staging_check).st_mode):
                    message = "snapshot staging path is not a regular file"
                    raise OSError(message)
                snapshot_bytes = os.fstat(staging_check).st_size
                os.fsync(staging_check)
                # Strictly after fsync, and before the descriptor closes:
                # the pages are clean now, so the kernel can actually drop
                # them, and the hint needs a descriptor to name the file.
                advice = _release_page_cache(staging_check, final_name)
            finally:
                os.close(staging_check)

            current_stat = dest_dir.stat(follow_symlinks=False)
            if (current_stat.st_dev, current_stat.st_ino) != (
                dest_stat.st_dev,
                dest_stat.st_ino,
            ):
                message = "destination directory changed during snapshot"
                raise OSError(message)
            os.link(
                staging_name,
                final_name,
                src_dir_fd=dest_descriptor,
                dst_dir_fd=dest_descriptor,
                follow_symlinks=False,
            )
            published = True
            os.unlink(staging_name, dir_fd=dest_descriptor)
            os.fsync(dest_descriptor)
            succeeded = True
            # Record structural verification only. This does not establish
            # relational integrity or authorize pending migrations.
            record_verified_database(str(database_path), source="snapshot")
            # Prune after the new copy is in place, never before -- see the
            # matching comment on the _create_verified_backup call site.
            if max_keep is not None:
                _prune_recovery_backups(
                    dest_dir, max_keep=max_keep, newest=dest_dir / final_name
                )
            _log(
                f"[db-snapshot] page_cache_release path={final_name} "
                f"bytes={snapshot_bytes} advice={advice}",
            )
            return dest_dir / final_name
        finally:
            if source is not None:
                source.close()
            with suppress(FileNotFoundError):
                os.unlink(staging_name, dir_fd=dest_descriptor)
            if published and not succeeded:
                with suppress(FileNotFoundError):
                    os.unlink(final_name, dir_fd=dest_descriptor)
    finally:
        os.close(dest_descriptor)


def _quote_identifier(identifier: str) -> str:
    escaped = identifier.replace('"', '""')
    return f'"{escaped}"'


def _drop_conflict_snapshot(conn: sqlite3.Connection) -> None:
    """Remove the snapshot. The table may not exist if the build failed."""
    with suppress(sqlite3.DatabaseError):
        conn.execute("DROP TABLE floppy_fk_conflicts")


def _snapshot_conflicts(conn: sqlite3.Connection, *, require_row_ids: bool) -> int:
    """Record every conflicting row id in a temporary table.

    The delete path must refuse a row it cannot address, so it sets
    ``require_row_ids``. The description path only reads, so it skips such rows.
    """
    conn.execute(
        "CREATE TEMP TABLE floppy_fk_conflicts ("
        "table_name TEXT NOT NULL, row_id INTEGER NOT NULL, "
        "PRIMARY KEY (table_name, row_id))"
    )
    skipped = 0
    cursor = conn.execute("PRAGMA foreign_key_check")
    while rows := cursor.fetchmany(500):
        if require_row_ids and any(
            row_id is None for _table, row_id, _parent, _key in rows
        ):
            message = (
                "one or more conflicts cannot be quarantined automatically "
                "because the child table has no rowid"
            )
            raise ValueError(message)
        skipped += sum(1 for _t, row_id, _p, _k in rows if row_id is None)
        conn.executemany(
            "INSERT OR IGNORE INTO floppy_fk_conflicts VALUES (?, ?)",
            (
                (table, row_id)
                for table, row_id, _parent, _key in rows
                if row_id is not None
            ),
        )
    # A row we cannot address is still an affected row. Report it rather than
    # let the breakdown quietly add up to less than the total.
    return skipped


def _describe_affected(conn: sqlite3.Connection) -> dict:
    """Name the affected content, grouped and bounded.

    The reference that broke is not the one that carries the title. An affected
    row usually still points at a valid ``app_item``, so the title survives even
    though the parent row is gone. This only reads columns that already exist.
    """
    described: Counter = Counter()
    unidentified = 0
    # The scan below reads every foreign key, so test the one thing that makes
    # it worth doing before paying for it.
    has_item_table = conn.execute(
        "SELECT 1 FROM main.sqlite_schema "
        "WHERE type = 'table' AND name = 'app_item' COLLATE NOCASE",
    ).fetchone()
    if not has_item_table:
        return {}
    try:
        unidentified += _snapshot_conflicts(conn, require_row_ids=False)
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT table_name FROM floppy_fk_conflicts "
                "ORDER BY table_name",
            )
        ]
        for table in tables:
            total = conn.execute(
                "SELECT COUNT(*) FROM floppy_fk_conflicts WHERE table_name = ?",
                [table],
            ).fetchone()[0]
            columns = {
                name.casefold()
                for (name,) in conn.execute(
                    "SELECT name FROM pragma_table_xinfo(?, 'main')",
                    [table],
                )
            }
            if "item_id" not in columns:
                unidentified += total
                continue
            named = 0
            for title, season, count in conn.execute(
                f"SELECT i.title, i.season_number, COUNT(*) "  # noqa: S608
                f"FROM main.{_quote_identifier(table)} AS child "
                "JOIN main.app_item AS i ON i.id = child.item_id "
                "WHERE child.rowid IN ("
                "SELECT row_id FROM floppy_fk_conflicts WHERE table_name = ?) "
                "GROUP BY i.title, i.season_number",
                [table],
            ):
                described[(title, season)] += count
                named += count
            unidentified += total - named
    finally:
        _drop_conflict_snapshot(conn)

    affected = [
        {"count": count, "season": season, "title": title}
        for (title, season), count in described.most_common(_MAX_AFFECTED_TITLES)
    ]
    remaining = sum(described.values()) - sum(item["count"] for item in affected)
    return {
        "affected": affected,
        "other_titles": len(described) - len(affected),
        "other_titles_count": remaining,
        "unidentified": unidentified,
    }


def _delete_orphaned_rows(conn: sqlite3.Connection) -> int:
    """Delete every child row named by the stable foreign-key snapshot."""
    try:
        return _delete_snapshotted_rows(conn)
    finally:
        _drop_conflict_snapshot(conn)


def _delete_snapshotted_rows(conn: sqlite3.Connection) -> int:
    _snapshot_conflicts(conn, require_row_ids=True)
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT table_name FROM floppy_fk_conflicts ORDER BY table_name"
        )
    ]
    deleted = 0
    for table in tables:
        result = conn.execute(
            f"DELETE FROM main.{_quote_identifier(table)} "  # noqa: S608
            "WHERE rowid IN (SELECT row_id FROM floppy_fk_conflicts "
            "WHERE table_name = ?)",
            [table],
        )
        deleted += result.rowcount
    return deleted


def _incident_from_report(report: dict) -> dict:
    return {
        "can_quarantine": report["can_quarantine"],
        "fingerprint": report["fingerprint"],
        "groups": report["groups"],
        "samples": report["samples"],
        "total_conflicts": report["total_conflicts"],
        "unsafe_reasons": report.get("unsafe_reasons", []),
    }


def _reconcile_report(db_path: str, report: dict) -> None:
    if report.get("status") not in {"accepted", "blocked", "prepared"}:
        return
    incident = _incident_from_report(report)
    resolution = "manual-repair"
    backup_path = None
    deleted_rows = report.get("deleted_rows")
    if report["status"] == "prepared":
        resolution = report.get("resolution") or "quarantine"
        raw_backup_path = report.get("backup_path")
        if not raw_backup_path:
            message = "prepared recovery report has no verified backup"
            raise OSError(message)
        backup_path = Path(raw_backup_path)
        expected_directory = Path(db_path).resolve().parent / "sqlite-recovery"
        try:
            backup_stat = backup_path.lstat()
        except OSError as error:
            message = f"prepared recovery backup is unavailable: {error}"
            raise OSError(message) from error
        if (
            backup_path.parent != expected_directory
            or not stat.S_ISREG(backup_stat.st_mode)
            or backup_path.is_symlink()
        ):
            message = "prepared recovery backup path is unsafe"
            raise OSError(message)
        backup = sqlite3.connect(f"{backup_path.as_uri()}?mode=ro", uri=True)
        try:
            result = backup.execute("PRAGMA quick_check").fetchone()
            if not result or result[0] != "ok":
                message = "prepared recovery backup failed quick_check"
                raise sqlite3.DatabaseError(message)
        finally:
            backup.close()
    _write_incident_report(
        db_path,
        incident,
        status="resolved",
        resolution=resolution,
        backup_path=backup_path,
        deleted_rows=deleted_rows,
    )


def _valid_blocked_token(report: dict | None, incident: dict) -> str | None:
    if (
        report
        and report.get("status") == "blocked"
        and report.get("fingerprint") == incident["fingerprint"]
    ):
        token = report.get("incident_token")
        if isinstance(token, str) and re.fullmatch(r"[0-9a-f]{32}", token):
            return token
    return None


def _check_foreign_keys(conn: sqlite3.Connection, db_path: str) -> None:
    prior_report = _read_incident_report(db_path)
    conn.execute("BEGIN IMMEDIATE")
    incident = _inspect_foreign_keys(conn)
    if not incident["total_conflicts"]:
        conn.commit()
        if prior_report:
            try:
                _reconcile_report(db_path, prior_report)
            except (KeyError, OSError, sqlite3.DatabaseError) as error:
                # Every relationship is already clean and any quarantine delete
                # is already committed, so refusing to start cannot protect the
                # data; it only wedges a healthy container. Leave the report
                # untouched so the next startup retries the reconciliation.
                _log(
                    "[entrypoint] SQLite recovery report could not be finalized; "
                    f"the database is healthy and startup continues: {error}",
                )
        return

    # Naming the affected titles is a convenience. The report is the only way
    # out of the incident, so a failure here must never stop it being written.
    try:
        incident.update(_describe_affected(conn))
    except Exception as error:
        _log(f"[entrypoint] Could not name the affected entries: {error}")
    _print_incident(db_path, incident)
    if (
        prior_report
        and prior_report.get("status") == "accepted"
        and prior_report.get("fingerprint") == incident["fingerprint"]
    ):
        conn.rollback()
        _log(
            f"[entrypoint] Previously accepted {incident['total_conflicts']} "
            "unchanged foreign key conflict(s) without changing rows",
        )
        return

    incident_token = _valid_blocked_token(prior_report, incident)
    if incident_token is None:
        incident_token = secrets.token_hex(16)
    action = _read_decision(db_path, incident, incident_token) or _selected_action(
        incident,
        incident_token,
    )
    try:
        report_path = _write_incident_report(
            db_path,
            incident,
            status="blocked",
            incident_token=incident_token,
        )
    except OSError as error:
        conn.rollback()
        _log(
            f"[entrypoint] Could not publish SQLite incident report: {error}",
        )
        sys.exit(1)

    if action == "accept":
        conn.rollback()
        try:
            _write_incident_report(
                db_path,
                incident,
                status="accepted",
                resolution="accept",
            )
        except OSError as error:
            _log(
                f"[entrypoint] Could not publish SQLite acceptance: {error}",
            )
            sys.exit(1)
        _log(
            f"[entrypoint] Accepted {incident['total_conflicts']} foreign key "
            "conflict(s) without changing rows",
        )
        return

    only_album_artist = {group["table"] for group in incident["groups"]} == {
        _ALBUM_ARTIST_TABLE
    }
    auto_repair_enabled = os.environ.get(
        "FLOPPY_SQLITE_AUTO_REPAIR", "true"
    ).strip().lower() not in {"0", "false", "no", "off"}
    should_quarantine = (
        action == "quarantine"
        or (auto_repair_enabled and incident["can_quarantine"])
        or only_album_artist
    )
    if should_quarantine:
        if not incident["can_quarantine"]:
            conn.rollback()
            _log(
                "[entrypoint] One or more conflicts cannot be quarantined "
                "automatically; manual repair is required",
            )
            _print_resolution_options(incident, incident_token)
            sys.exit(1)

        backup_path = None
        try:
            backup_path = _create_verified_backup(
                db_path,
                incident["fingerprint"],
            )
            deleted = _delete_orphaned_rows(conn)
            remaining = _inspect_foreign_keys(conn)
            if remaining["total_conflicts"]:
                message = f"{remaining['total_conflicts']} conflict(s) remain after quarantine"
                raise sqlite3.IntegrityError(message)
            if only_album_artist and action != "quarantine" and not auto_repair_enabled:
                resolution = "automatic-album-artist"
            elif action == "quarantine":
                resolution = "quarantine"
            else:
                resolution = "auto-repair"
            _write_incident_report(
                db_path,
                incident,
                status="prepared",
                resolution=resolution,
                backup_path=backup_path,
                deleted_rows=deleted,
            )
        except (OSError, sqlite3.DatabaseError, ValueError) as error:
            conn.rollback()
            with suppress(OSError):
                _write_incident_report(
                    db_path,
                    incident,
                    status="blocked",
                    resolution="quarantine-failed",
                    backup_path=backup_path,
                    incident_token=incident_token,
                )
            _log(
                f"[entrypoint] SQLite quarantine failed without changing the "
                f"database: {error}",
            )
            _print_resolution_options(incident, incident_token)
            sys.exit(1)

        try:
            conn.commit()
        except sqlite3.DatabaseError as error:
            conn.rollback()
            _log(
                f"[entrypoint] SQLite quarantine commit failed; the prepared "
                f"report and verified backup remain: {error}",
            )
            sys.exit(1)

        try:
            _write_incident_report(
                db_path,
                incident,
                status="resolved",
                resolution=resolution,
                backup_path=backup_path,
                deleted_rows=deleted,
            )
        except OSError as error:
            if (
                isinstance(error, _ResolvedReportPublicationError)
                and error.previous_restored
            ):
                message = (
                    "SQLite quarantine committed, but final report publication "
                    f"failed; the prepared report was restored: {error}"
                )
            else:
                message = (
                    "SQLite quarantine committed, but final report publication "
                    "failed; prior report restoration could not be confirmed. "
                    f"Inspect the report and verified backup before restart: {error}"
                )
            _log(f"[entrypoint] {message}")
            sys.exit(1)
        if only_album_artist and resolution == "automatic-album-artist":
            _log(
                f"[entrypoint] Removed {deleted} orphaned album artist credit "
                f"row(s) after backup to {backup_path}",
            )
        elif resolution == "auto-repair":
            _log(
                f"[entrypoint] Auto-repaired {deleted} orphaned child row(s); "
                f"backup: {backup_path}",
            )
        else:
            _log(
                f"[entrypoint] Quarantined {deleted} orphaned row(s); "
                f"backup: {backup_path}",
            )
        return

    conn.rollback()
    _log(
        f"[entrypoint] Startup is blocked by report {report_path}",
    )
    _print_resolution_options(incident, incident_token)
    _log(_RELATIONSHIP_HINT)
    sys.exit(1)


class IntegrityScanTimeoutError(Exception):
    """The storage scan did not finish inside its deadline."""


def inspect_database(db_path: str, *, timeout_seconds: float | None = None) -> dict:
    """Report SQLite storage and relationship state without changing anything.

    Returns ``{"quick_check": str, "conflicts": dict | None}``. ``conflicts`` is
    the summary from :func:`_inspect_foreign_keys`, or ``None`` when every
    relationship is intact.

    The startup path (:func:`check_database_integrity`) reaches its verdict from
    the same two primitives, but it also repairs rows, quarantines them,
    publishes incident reports and exits the process. Diagnostics need the
    verdict alone. The two callers therefore share the primitives; neither calls
    the other. :func:`_check_foreign_keys` inspects inside ``BEGIN IMMEDIATE``
    because the quarantine delete that follows must operate on the rows that
    inspection found. Taking that write lock here would block a running Floppy,
    so this function does not take it.

    ``timeout_seconds`` bounds the scan. ``sqlite3.connect(timeout=...)`` bounds
    only the wait for a lock, so ``PRAGMA quick_check`` on a large file would run
    past any value given there. A progress handler aborts the running statement
    instead, and raises :class:`IntegrityScanTimeoutError`.

    SQLite may checkpoint the write-ahead log when the last connection closes.
    That moves committed data from the log into the main file. It does not change
    what the database contains.
    """
    conn = None
    deadline = None
    if timeout_seconds is not None:
        deadline = time.monotonic() + timeout_seconds
    try:
        warm_database_cache(db_path)
        conn = sqlite3.connect(db_path, timeout=_SCAN_BUSY_TIMEOUT_SECONDS)
        if deadline is not None:
            # Returning non-zero from the handler aborts the running statement.
            conn.set_progress_handler(
                lambda: 1 if time.monotonic() > deadline else 0,
                _SCAN_PROGRESS_INSTRUCTIONS,
            )
        try:
            row = conn.execute("PRAGMA quick_check").fetchone()
            status = row[0] if row else None
            incident = _inspect_foreign_keys(conn)
        except sqlite3.OperationalError as error:
            if deadline is not None and time.monotonic() > deadline:
                message = f"storage scan exceeded {timeout_seconds:g}s"
                raise IntegrityScanTimeoutError(message) from error
            raise
    finally:
        if conn is not None:
            conn.set_progress_handler(None, 0)
            conn.close()

    return {
        "quick_check": status,
        "conflicts": incident if incident["total_conflicts"] else None,
    }


def check_database_integrity(db_path: str) -> None:
    """Check SQLite storage and foreign keys before migrations."""
    conn = None
    try:
        conn = sqlite3.connect(db_path, timeout=30.0)
        result = conn.execute("PRAGMA quick_check").fetchone()
        status = result[0] if result else None
        if status != "ok":
            _log(
                "[entrypoint] Database integrity check failed: "
                f"quick_check returned {status!r}",
            )
            _log(_CORRUPTION_HINT)
            _report_corruption(db_path, f"quick_check returned {status!r}")
            sys.exit(1)

        _check_foreign_keys(conn, db_path)
    except sqlite3.DatabaseError as e:
        _log(f"[entrypoint] Database integrity check failed: {e}")
        busy = getattr(e, "sqlite_errorcode", None) in {
            sqlite3.SQLITE_BUSY,
            sqlite3.SQLITE_LOCKED,
        }
        _log(_BUSY_HINT if busy else _CORRUPTION_HINT)
        if not busy:
            # A damaged file usually raises here rather than returning a
            # quick_check string, so the recovery page needs the same report
            # it gets from the returned-status path.
            _report_corruption(db_path, str(e))
        sys.exit(1)
    finally:
        if conn is not None:
            conn.close()
