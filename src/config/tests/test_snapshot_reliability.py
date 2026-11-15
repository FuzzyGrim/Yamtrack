"""Real disposable snapshots, including failed publication and WAL recovery."""

import contextlib
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import closing
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from config import sqlite_integrity


class SnapshotReliabilityTests(SimpleTestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)
        self.database = self.root / "live.sqlite3"
        self.destination = self.root / "backups"
        self.source = sqlite3.connect(self.database)
        self.addCleanup(self.source.close)
        self.source.execute("PRAGMA journal_mode=WAL")
        self.source.execute("CREATE TABLE tracking (title TEXT)")
        self.source.execute("INSERT INTO tracking VALUES ('preserve me')")
        self.source.commit()

    def snapshot(self, retention=7):
        return sqlite_integrity.create_live_database_snapshot(
            str(self.database), self.destination, max_keep=retention, timeout_seconds=1
        )

    def test_portable_workspace_preserves_live_wal_and_private_permissions(self):
        # Force the portable branch even on Linux CI.
        with mock.patch.object(sqlite_integrity.sys, "platform", "darwin"):
            snapshot = self.snapshot()
        self.assertIsNotNone(snapshot)
        self.assertEqual(stat.S_IMODE(snapshot.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.destination.stat().st_mode), 0o700)
        self.assertEqual(list(self.destination.iterdir()), [snapshot])
        restored = self.root / "restored.sqlite3"
        restored.write_bytes(snapshot.read_bytes())
        with sqlite3.connect(restored) as connection:
            self.assertEqual(
                connection.execute("PRAGMA integrity_check").fetchall(), [("ok",)]
            )
            self.assertEqual(
                connection.execute("SELECT * FROM tracking").fetchall(),
                [("preserve me",)],
            )

    def test_recovery_backup_also_uses_portable_workspace(self):
        with mock.patch.object(sqlite_integrity.sys, "platform", "darwin"):
            snapshot = sqlite_integrity._create_verified_backup(
                str(self.database), "test"
            )
        with sqlite3.connect(snapshot) as connection:
            self.assertEqual(
                connection.execute("SELECT * FROM tracking").fetchall(),
                [("preserve me",)],
            )
        self.assertEqual(stat.S_IMODE(snapshot.stat().st_mode), 0o600)

    def test_failed_copy_preserves_previous_snapshot_and_reports_no_corruption(self):
        previous = self.snapshot()
        with (
            mock.patch.object(sqlite_integrity.sys, "platform", "darwin"),
            mock.patch.object(
                sqlite_integrity.shutil, "copyfileobj", side_effect=OSError("disk full")
            ),
            mock.patch.object(sqlite_integrity, "_report_corruption") as report,
        ):
            self.assertIsNone(self.snapshot(retention=1))
        report.assert_not_called()
        self.assertEqual(list(self.destination.iterdir()), [previous])

    def test_destination_sqlite_failure_is_not_source_corruption(self):
        error = sqlite3.OperationalError("unable to open database file")
        error.sqlite_errorcode = sqlite3.SQLITE_CANTOPEN
        with (
            mock.patch.object(
                sqlite_integrity, "_backup_to_staging", side_effect=error
            ),
            mock.patch.object(sqlite_integrity, "_report_corruption") as report,
        ):
            self.assertIsNone(self.snapshot())
        report.assert_not_called()
        self.assertEqual(list(self.destination.iterdir()), [])
        self.assertFalse(Path(f"{self.database}.integrity.verified.json").exists())

    def test_damaged_index_copied_verbatim_is_reported_as_corruption(self):
        # backup() copies a damaged b-tree page without raising; only the
        # quick_check on the copy notices, and that must still be reported.
        damaged = self.root / "damaged.sqlite3"
        with closing(sqlite3.connect(damaged)) as connection:
            connection.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
            connection.execute("CREATE INDEX ix ON t (v)")
            connection.executemany(
                "INSERT INTO t (v) VALUES (?)",
                [(f"value-{i:06d}",) for i in range(5000)],
            )
            connection.commit()
        data = bytearray(damaged.read_bytes())
        offset = len(data) - 4096 + 200
        data[offset : offset + 400] = b"\x55" * 400
        damaged.write_bytes(bytes(data))

        with mock.patch.object(sqlite_integrity, "_report_corruption") as report:
            result = sqlite_integrity.create_live_database_snapshot(
                str(damaged), self.destination, max_keep=3, timeout_seconds=1
            )

        self.assertIsNone(result)
        report.assert_called_once()

    def test_failed_publication_or_directory_sync_does_not_prune_previous(self):
        previous = self.snapshot()
        real_fsync = os.fsync

        def fail_directory_sync(descriptor):
            if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise OSError("directory fsync failed")
            real_fsync(descriptor)

        for operation, replacement in [
            ("link", mock.Mock(side_effect=OSError("link failed"))),
            ("fsync", fail_directory_sync),
        ]:
            with (
                self.subTest(operation=operation),
                mock.patch.object(sqlite_integrity.os, operation, replacement),
            ):
                self.assertIsNone(self.snapshot(retention=1))
            self.assertEqual(list(self.destination.iterdir()), [previous])

    def test_interruption_never_publishes_partial_copy(self):
        previous = self.snapshot()
        with (
            mock.patch.object(sqlite_integrity.sys, "platform", "darwin"),
            mock.patch.object(
                sqlite_integrity.shutil, "copyfileobj", side_effect=KeyboardInterrupt
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.snapshot(retention=1)
        self.assertEqual(list(self.destination.iterdir()), [previous])

    def test_nonpositive_retention_keeps_a_recovery_point(self):
        for retention in [0, -1]:
            with self.subTest(retention=retention):
                snapshot = self.snapshot(retention=retention)
                self.assertIsNotNone(snapshot)
                self.assertTrue(snapshot.exists())
                self.assertEqual(list(self.destination.iterdir()), [snapshot])

    def test_symlink_destination_is_refused(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.destination.symlink_to(outside, target_is_directory=True)
        self.assertIsNone(self.snapshot())
        self.assertEqual(list(outside.iterdir()), [])

    def test_directory_replacement_during_copy_cannot_redirect_writes(self):
        outside = self.root / "outside"
        outside.mkdir()
        moved = self.root / "moved"
        real_copy = sqlite_integrity.shutil.copyfileobj

        def replace_directory(source, destination, **kwargs):
            self.destination.rename(moved)
            self.destination.symlink_to(outside, target_is_directory=True)
            return real_copy(source, destination, **kwargs)

        with (
            mock.patch.object(sqlite_integrity.sys, "platform", "darwin"),
            mock.patch.object(
                sqlite_integrity.shutil, "copyfileobj", replace_directory
            ),
        ):
            self.assertIsNone(self.snapshot())
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(list(moved.iterdir()), [])

    def test_hard_process_exit_leaves_no_published_partial_snapshot(self):
        previous = self.snapshot()
        script = """
import os
import sys
from pathlib import Path
from config import sqlite_integrity
sqlite_integrity.os.link = lambda *args, **kwargs: os._exit(73)
sqlite_integrity.create_live_database_snapshot(
    sys.argv[1], Path(sys.argv[2]), max_keep=1, timeout_seconds=1
)
"""
        environment = {
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        }
        result = subprocess.run(  # noqa: S603 -- fixed interpreter and disposable paths
            [sys.executable, "-c", script, str(self.database), str(self.destination)],
            env=environment,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 73, result.stderr[-500:])
        self.assertEqual(list(self.destination.glob("*.sqlite3")), [previous])
        leftovers = list(self.destination.glob(".*.tmp"))
        self.assertEqual(len(leftovers), 1)
        self.assertEqual(stat.S_IMODE(leftovers[0].stat().st_mode), 0o600)

    def test_clock_skew_cannot_prune_the_new_verified_snapshot(self):
        previous = self.snapshot()
        os.utime(previous, (4102444800, 4102444800))
        current = self.snapshot(retention=1)
        self.assertIsNotNone(current)
        self.assertTrue(current.exists())
        self.assertEqual(list(self.destination.iterdir()), [current])


class _Committer:
    """Commit one tiny row to the source every ``interval`` seconds."""

    def __init__(self, database, interval, *, limit=8.0):
        self.database = database
        self.interval = interval
        self.limit = limit
        self.commits = 0
        self.slowest = 0.0
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        deadline = time.monotonic() + self.limit
        with closing(
            sqlite3.connect(self.database, timeout=10, isolation_level=None)
        ) as writer:
            while time.monotonic() < deadline and not self.stop.wait(self.interval):
                started = time.monotonic()
                writer.execute("INSERT INTO plays (n) VALUES (?)", (self.commits,))
                self.slowest = max(self.slowest, time.monotonic() - started)
                self.commits += 1

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join()


class SnapshotUnderWritesTests(SimpleTestCase):
    """The copy must converge while another connection keeps committing.

    The real copy uses 256 pages per step; a test-sized database uses 4 so one
    pass takes about a second, far longer than the 50 ms between commits.
    """

    ROWS = 1000

    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)
        self.destination = self.root / "backups"
        patcher = mock.patch.object(sqlite_integrity, "_SNAPSHOT_STEP_PAGES", 4)
        patcher.start()
        self.addCleanup(patcher.stop)

    def build(self, journal_mode):
        database = self.root / f"{journal_mode}.sqlite3"
        with closing(sqlite3.connect(database, isolation_level=None)) as db:
            db.execute(f"PRAGMA journal_mode={journal_mode}")
            db.execute("CREATE TABLE body (id INTEGER PRIMARY KEY, data BLOB)")
            db.execute("CREATE TABLE plays (id INTEGER PRIMARY KEY, n INTEGER)")
            db.execute("BEGIN")
            db.executemany(
                "INSERT INTO body (data) VALUES (randomblob(3000))",
                [()] * self.ROWS,
            )
            db.execute("COMMIT")
        return database

    def snapshot(self, database, outcome):
        return sqlite_integrity.create_live_database_snapshot(
            str(database),
            self.destination,
            max_keep=3,
            timeout_seconds=5,
            outcome=outcome,
        )

    def assert_complete_copy(self, snapshot):
        with closing(sqlite3.connect(snapshot)) as copy:
            self.assertEqual(copy.execute("PRAGMA quick_check").fetchall(), [("ok",)])
            self.assertEqual(
                copy.execute("SELECT count(*) FROM body").fetchone()[0], self.ROWS
            )
            plays = [n for (n,) in copy.execute("SELECT n FROM plays ORDER BY n")]
        # A consistent image: a prefix of the committed plays, no gaps.
        self.assertEqual(plays, list(range(len(plays))))

    def test_wal_copy_finishes_in_one_pass_while_writes_continue(self):
        database = self.build("wal")
        outcome = {}
        with _Committer(database, 0.05) as committer:
            started = time.monotonic()
            snapshot = self.snapshot(database, outcome)
            elapsed = time.monotonic() - started
            still_writing = committer.thread.is_alive()
        self.assertIsNotNone(snapshot)
        # Unpinned, the copy restarts on every commit and only finishes after
        # the committer gives up at 8 s.
        self.assertTrue(
            still_writing, f"copy only finished after writes stopped ({elapsed:.1f} s)"
        )
        self.assertGreater(committer.commits, 3)
        self.assertEqual(outcome.get("restarts"), 0)
        self.assertTrue(outcome.get("pinned"))
        self.assertGreater(outcome.get("steps"), 100)
        # WAL readers never block the writer: commits stay quick during the copy.
        self.assertLess(committer.slowest, 0.5)
        self.assert_complete_copy(snapshot)

    def test_wal_pin_ends_so_checkpoint_can_shrink_the_wal(self):
        database = self.build("wal")
        # Keep a connection open so SQLite does not delete the WAL on close.
        with closing(sqlite3.connect(database)) as keeper:
            keeper.execute("SELECT count(*) FROM plays").fetchone()
            with _Committer(database, 0.02, limit=2.0):
                self.assertIsNotNone(self.snapshot(database, {}))
            wal = Path(f"{database}-wal")
            self.assertGreater(wal.stat().st_size, 0)
            busy, _log, _done = keeper.execute(
                "PRAGMA wal_checkpoint(TRUNCATE)"
            ).fetchone()
            self.assertEqual(busy, 0)
            self.assertEqual(wal.stat().st_size, 0)

    def test_rollback_journal_copy_never_holds_writers(self):
        # Pinning or a single-step copy would make a commit wait for the whole
        # ~1 s pass. Unpinned, a busy instance restarts the copy instead, and
        # the restart limit reports that rather than looping.
        database = self.build("delete")
        outcome = {}
        with (
            mock.patch.object(sqlite_integrity, "_log") as log,
            _Committer(database, 0.05, limit=4.0) as committer,
        ):
            time.sleep(0.2)
            snapshot = self.snapshot(database, outcome)
            time.sleep(0.2)
        self.assertFalse(outcome.get("pinned"))
        self.assertLess(committer.slowest, 0.5)
        if snapshot is None:
            self.assertGreater(
                outcome["restarts"], sqlite_integrity._SNAPSHOT_MAX_RESTARTS
            )
            self.assertIn("kept changing", str(log.call_args_list))
        else:
            self.assert_complete_copy(snapshot)

    def test_quiet_rollback_journal_copy_completes_stepped(self):
        database = self.build("delete")
        outcome = {}
        snapshot = self.snapshot(database, outcome)
        self.assertIsNotNone(snapshot)
        self.assertFalse(outcome["pinned"])
        self.assertEqual(outcome["restarts"], 0)
        self.assertGreater(outcome["steps"], 100)
        self.assert_complete_copy(snapshot)

    def test_copy_that_keeps_restarting_fails_loudly(self):
        database = self.build("wal")
        outcome = {}
        with (
            mock.patch.object(
                sqlite_integrity,
                "_pinned_read",
                lambda source: contextlib.nullcontext(),
            ),
            mock.patch.object(sqlite_integrity, "_log") as log,
            _Committer(database, 0.05),
        ):
            started = time.monotonic()
            self.assertIsNone(self.snapshot(database, outcome))
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 6)
        self.assertGreater(outcome["restarts"], sqlite_integrity._SNAPSHOT_MAX_RESTARTS)
        self.assertIn("kept changing", str(log.call_args_list))
        self.assertEqual(list(self.destination.iterdir()), [])
