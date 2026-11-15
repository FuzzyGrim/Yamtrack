import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, override_settings

from app import backup_status
from app.tasks_db_backup import write_database_snapshot
from config import sqlite_integrity


class WriteDatabaseSnapshotTaskTests(SimpleTestCase):
    @override_settings(USING_SQLITE_DATABASE=True, DB_SNAPSHOT_ENABLED=False)
    def test_skips_when_disabled(self):
        with mock.patch(
            "app.tasks_db_backup.create_live_database_snapshot"
        ) as snapshot:
            result = write_database_snapshot()

        snapshot.assert_not_called()
        self.assertEqual(result, {"status": "skipped", "reason": "disabled"})

    @override_settings(USING_SQLITE_DATABASE=False, DB_SNAPSHOT_ENABLED=True)
    def test_skips_on_postgres(self):
        with mock.patch(
            "app.tasks_db_backup.create_live_database_snapshot"
        ) as snapshot:
            result = write_database_snapshot()

        snapshot.assert_not_called()
        self.assertEqual(result, {"status": "skipped", "reason": "postgres"})

    @override_settings(USING_SQLITE_DATABASE=True, DB_SNAPSHOT_ENABLED=True)
    def test_celery_reports_failure_when_snapshot_raises(self):
        with mock.patch(
            "app.tasks_db_backup.create_live_database_snapshot",
            side_effect=RuntimeError("boom"),
        ):
            result = write_database_snapshot.apply(throw=False)

        self.assertEqual(result.state, "FAILURE")
        self.assertIsInstance(result.result, RuntimeError)

    @override_settings(USING_SQLITE_DATABASE=True, DB_SNAPSHOT_ENABLED=True)
    def test_celery_reports_failure_when_snapshot_returns_none(self):
        with mock.patch(
            "app.tasks_db_backup.create_live_database_snapshot",
            return_value=None,
        ):
            result = write_database_snapshot.apply(throw=False)

        self.assertEqual(result.state, "FAILURE")
        self.assertIsInstance(result.result, RuntimeError)

    def test_happy_path_writes_a_real_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "db.sqlite3"
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
            conn.commit()
            conn.close()
            backup_dir = Path(tmp_dir) / "backups"

            with override_settings(
                USING_SQLITE_DATABASE=True,
                DB_SNAPSHOT_ENABLED=True,
                FLOPPY_DB_PATH=db_path,
                BACKUP_DIR=str(backup_dir),
                DB_SNAPSHOT_RETENTION_COUNT=7,
                SQLITE_BUSY_TIMEOUT_SECONDS=5,
            ):
                result = write_database_snapshot()

            self.assertEqual(result["status"], "ok")
            snapshot_files = list((backup_dir / "database").glob("*.sqlite3"))
            self.assertEqual(len(snapshot_files), 1)

    def make_database(self, tmp_dir):
        db_path = Path(tmp_dir) / "db.sqlite3"
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
            conn.commit()
        return db_path

    def test_completion_line_says_how_the_copy_ran(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = self.make_database(tmp_dir)
            with (
                override_settings(
                    USING_SQLITE_DATABASE=True,
                    DB_SNAPSHOT_ENABLED=True,
                    FLOPPY_DB_PATH=db_path,
                    BACKUP_DIR=str(Path(tmp_dir) / "backups"),
                    DB_SNAPSHOT_RETENTION_COUNT=7,
                    SQLITE_BUSY_TIMEOUT_SECONDS=5,
                ),
                self.assertLogs("app.tasks_db_backup", level="INFO") as logs,
            ):
                self.assertEqual(write_database_snapshot()["status"], "ok")
        line = next(entry for entry in logs.output if "db_snapshot name=" in entry)
        self.assertIn("pinned=True", line)
        self.assertIn("restarts=0", line)
        self.assertRegex(line, r"steps=[1-9]")

    def test_snapshot_that_cannot_converge_shows_as_failing(self):
        error = sqlite_integrity._SnapshotNotConvergingError(
            "snapshot copy restarted 4 times because the database kept changing"
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = self.make_database(tmp_dir)
            with (
                override_settings(
                    USING_SQLITE_DATABASE=True,
                    DB_SNAPSHOT_ENABLED=True,
                    FLOPPY_DB_PATH=db_path,
                    BACKUP_DIR=str(Path(tmp_dir) / "backups"),
                    DB_SNAPSHOT_RETENTION_COUNT=7,
                    SQLITE_BUSY_TIMEOUT_SECONDS=5,
                ),
                mock.patch.object(sqlite_integrity, "_copy_source", side_effect=error),
                mock.patch.object(sqlite_integrity, "_report_corruption") as report,
            ):
                result = write_database_snapshot.apply(throw=False)
                status = backup_status.freshness(backup_status.SNAPSHOT)

        self.assertEqual(result.state, "FAILURE")
        self.assertTrue(status["failing"])
        # The database is fine; it was only busy.
        report.assert_not_called()
