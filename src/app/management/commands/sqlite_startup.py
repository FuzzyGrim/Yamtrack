"""Bound startup admission; run comprehensive checks as explicit maintenance."""

import signal
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection

from config import sqlite_integrity as integrity
from config.sqlite_availability import (
    MigrationPermitRequiredError,
    StartupBlockedError,
    _permit_path,
    startup_gate,
    verify_snapshot,
)
from config.sqlite_background import BackgroundSliceError, run_background_slice
from config.sqlite_startup_watchdog import _stop


class Command(BaseCommand):
    """Admit bounded startup or run an explicitly requested verification."""

    help = "SQLite startup gate and snapshot-based verification (stop writers before preparation)"
    requires_system_checks = []

    def add_arguments(self, parser):
        """Separate admission, online observation and offline preparation."""
        modes = parser.add_mutually_exclusive_group()
        modes.add_argument("--prepare-migrations", action="store_true")
        modes.add_argument("--background", action="store_true")
        modes.add_argument("--recover", action="store_true")
        parser.add_argument(
            "--fast",
            action="store_true",
            help="With --prepare-migrations: structural snapshot check only",
        )
        parser.add_argument(
            "--worker", action="store_true", help="Internal deadline worker"
        )
        parser.add_argument("--timeout", type=int, default=3600)

    def handle(self, *args, **options):
        """Keep expensive work in a killable child with a fixed deadline."""
        if not settings.USING_SQLITE_DATABASE:
            return
        maintenance = any(
            options[key] for key in ("prepare_migrations", "background", "recover")
        )
        if maintenance and not options["worker"]:
            if options["timeout"] <= 0:
                msg = "--timeout must be positive"
                raise CommandError(msg)
            if options["prepare_migrations"]:
                _permit_path(str(settings.FLOPPY_DB_PATH)).unlink(missing_ok=True)
            worker = subprocess.Popen([sys.executable, *sys.argv, "--worker"])  # noqa: S603

            def forward(signum, _frame):
                worker.send_signal(signum)

            handlers = {
                signum: signal.signal(signum, forward)
                for signum in (signal.SIGTERM, signal.SIGINT)
            }
            try:
                result = worker.wait(timeout=options["timeout"])
            except subprocess.TimeoutExpired as error:
                _stop(worker)
                msg = "Verification deadline reached; no migration permit granted. Inspect verification status/logs."
                raise CommandError(msg) from error
            finally:
                for signum, handler in handlers.items():
                    signal.signal(signum, handler)
            if result:
                msg = f"SQLite maintenance exited with status {result}"
                raise CommandError(msg)
            return
        db_path = str(settings.FLOPPY_DB_PATH)
        started = time.monotonic()
        started_at = datetime.now(UTC).isoformat()
        try:
            if options["recover"]:
                from config.sqlite_recovery_policy import check_database_for_startup

                report = integrity._read_incident_report(db_path)
                if report and report.get("status") != "resolved":
                    check_database_for_startup(db_path)
                else:
                    # An old env approval or decision must not turn a healthy
                    # restart into a new full scan after its incident closed.
                    integrity._decision_path(db_path).unlink(missing_ok=True)
                    self.stdout.write(
                        "No unresolved recovery incident; no repair or scan requested."
                    )
            elif maintenance:
                if options["background"]:
                    # Supervisor runs config/sqlite_background.py instead,
                    # which does the same without starting Django.
                    try:
                        run_background_slice(db_path)
                    except BackgroundSliceError as error:
                        raise CommandError(str(error)) from error
                    return
                verify_snapshot(
                    db_path,
                    Path(settings.BACKUP_DIR) / "verification",
                    prepare=options["prepare_migrations"],
                    connection=connection,
                    fast=options["fast"],
                )
            else:
                needs_migrations = startup_gate(db_path, connection)
                integrity.write_startup_status(
                    db_path,
                    status="ok",
                    phase="admission",
                    started_at=started_at,
                    elapsed_seconds=time.monotonic() - started,
                )
                if not needs_migrations:
                    raise SystemExit(10)  # skip even the no-op migrate handlers
        except MigrationPermitRequiredError:
            raise SystemExit(
                11
            ) from None  # entrypoint takes a snapshot, then re-admits
        except (
            StartupBlockedError,
            OSError,
            ValueError,
            sqlite3.DatabaseError,
            DatabaseError,
        ) as error:
            if not maintenance:
                integrity.write_startup_status(
                    db_path,
                    status="blocked",
                    phase="admission",
                    started_at=started_at,
                    elapsed_seconds=time.monotonic() - started,
                    error_class="startup_admission",
                    error_message=str(error),
                )
            raise CommandError(str(error)) from error
