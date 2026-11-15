# ruff: noqa: S311 - deterministic fixture data
"""The physical-order sweep agrees with SQLite on healthy churned databases."""

import os
import random
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import suppress
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from config import sqlite_page_sweep as sweep


def build(page_size, auto_vacuum):
    directory = tempfile.mkdtemp()
    path = Path(directory) / "t.db"
    random.seed(3)
    with sqlite3.connect(path) as db:
        db.execute(f"PRAGMA page_size={page_size}")
        db.execute(f"PRAGMA auto_vacuum={auto_vacuum}")
        db.executescript(
            "CREATE TABLE a(id INTEGER PRIMARY KEY, k TEXT, v BLOB, w TEXT);"
            "CREATE INDEX ik ON a(k); CREATE UNIQUE INDEX iw ON a(w, k);"
            "CREATE TABLE n(x TEXT PRIMARY KEY, y INT) WITHOUT ROWID;"
        )
        for r in range(6):
            db.executemany(
                "INSERT INTO a(k,v,w) VALUES (?,?,?)",
                [
                    (
                        os.urandom(random.choice([5, 900, 1500])).hex(),
                        os.urandom(random.choice([10, 5000, 30000])),
                        f"{r}-{i}",
                    )
                    for i in range(40)
                ],
            )
            db.executemany(
                "INSERT OR IGNORE INTO n VALUES (?,?)",
                [(os.urandom(random.choice([5, 2000])).hex(), i) for i in range(30)],
            )
            db.execute("DELETE FROM a WHERE id%3=0 AND id%7<4")
            db.execute("UPDATE a SET v=? WHERE id%5=0", (os.urandom(20000),))
    return path


def _has_dbstat() -> bool:
    """Return whether this SQLite build has the dbstat table the test reads."""
    try:
        sqlite3.connect(":memory:").execute("SELECT 1 FROM dbstat LIMIT 1")
    except sqlite3.OperationalError:
        return False
    return True


class PageSweepTests(TestCase):
    def test_healthy_churned_databases_have_no_findings(self):
        for page_size in (512, 4096, 65536):
            for auto_vacuum in (0, 1, 2):
                with self.subTest(page_size=page_size, auto_vacuum=auto_vacuum):
                    path = build(page_size, auto_vacuum)
                    self.addCleanup(shutil.rmtree, path.parent, True)
                    self.assertEqual(sweep.sweep(str(path))["problems"], [])

    @unittest.skipUnless(_has_dbstat(), "This SQLite build has no dbstat table.")
    def test_truncation_zeroed_and_clobbered_pages_are_found(self):
        path = build(4096, 0)
        self.addCleanup(shutil.rmtree, path.parent, True)
        pages = path.stat().st_size // 4096
        with sqlite3.connect(path) as db:
            overflow = db.execute(
                "SELECT pageno FROM dbstat WHERE pagetype='overflow' LIMIT 1"
            ).fetchone()[0]
            leaf = db.execute(
                "SELECT pageno FROM dbstat WHERE name='a' AND pagetype='leaf' LIMIT 1"
            ).fetchone()[0]
        for damage in ("overflow", "zero-leaf", "truncate"):
            with self.subTest(damage=damage):
                copy = path.with_name(f"{damage}.db")
                shutil.copy(path, copy)
                with copy.open("r+b") as handle:
                    if damage == "overflow":
                        handle.seek(4096 * (overflow - 1))
                        handle.write(b"\xff" * 16)
                    elif damage == "zero-leaf":
                        handle.seek(4096 * (leaf - 1))
                        handle.write(bytes(4096))
                    else:
                        handle.truncate((pages - 5) * 4096)
                with suppress(sqlite3.DatabaseError):  # SQLite refuses truncation
                    self.assertTrue(sweep.sweep(str(copy))["problems"])


class SnapshotGateTests(TestCase):
    def snapshot(self, **kwargs):
        from config import sqlite_integrity as integrity

        path = build(4096, 0)
        self.addCleanup(shutil.rmtree, path.parent, True)
        outcome = {}
        result = integrity.create_live_database_snapshot(
            str(path),
            path.parent / "snaps",
            max_keep=None,
            timeout_seconds=2,
            verify="page_sweep",
            outcome=outcome,
            **kwargs,
        )
        return result, outcome

    def test_good_copy_passes_the_sweep_without_quick_check(self):
        result, outcome = self.snapshot()
        self.assertIsNotNone(result)
        self.assertEqual(outcome["verified"], "page_sweep")

    def test_damaged_copy_fails_the_gate(self):
        problem = {"pages": 1, "problems": ["page 2: bad header"]}
        with patch.object(sweep, "sweep", return_value=problem):
            result, outcome = self.snapshot()
        self.assertIsNone(result)
        self.assertNotIn("verified", outcome)

    def test_unavailable_sweep_falls_back_to_quick_check(self):
        with patch.object(sweep, "sweep", side_effect=sweep.SweepUnavailableError):
            result, outcome = self.snapshot()
        self.assertIsNotNone(result)
        self.assertEqual(outcome["verified"], "quick_check")
