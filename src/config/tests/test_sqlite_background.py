"""The idle verification slice runs without Django and does the same work (OPS-10)."""

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

from config import sqlite_background as background
from config.data_paths import resolve_data_paths

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
ENTRY = SRC / "config" / "sqlite_background.py"
# Timestamps and the file's own path and inode differ between the two
# databases; everything else must match.
_STAMP_KEYS = {
    "verified_at",
    "deep_at",
    "updated_at",
    "verified_through",
    "slices",
    "database",
    "identity",
}


def _without_stamps(value):
    if isinstance(value, dict):
        return {k: _without_stamps(v) for k, v in value.items() if k not in _STAMP_KEYS}
    if isinstance(value, list):
        return [_without_stamps(v) for v in value]
    return value


class SqliteBackgroundEntryTests(SimpleTestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory, True)
        self.database = self.make_database(self.directory / "a")

    @staticmethod
    def make_database(folder):
        folder.mkdir(parents=True)
        path = folder / "db.sqlite3"
        with sqlite3.connect(path) as db:
            db.executescript(
                "CREATE TABLE parent(id INTEGER PRIMARY KEY);"
                "CREATE TABLE child(id INTEGER PRIMARY KEY, "
                "parent_id INTEGER REFERENCES parent(id), note TEXT);"
                "INSERT INTO parent VALUES (1);"
                "INSERT INTO child VALUES (1, 1, 'x');"
            )
        return path

    def run_entry(self, *args, database=None, **env):
        environment = {
            "PATH": os.environ["PATH"],
            "FLOPPY_DB_PATH": str(database or self.database),
            "PYTHONDONTWRITEBYTECODE": "1",
            **env,
        }
        return subprocess.run(  # noqa: S603 - test-controlled interpreter and file
            [sys.executable, str(ENTRY), *args],
            cwd=self.directory,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )

    def progress(self, database):
        return json.loads(Path(f"{database}.verification.progress.json").read_text())

    def test_supervisor_runs_the_django_free_entry(self):
        conf = (ROOT / "supervisord.conf").read_text()
        program = conf.split("[program:sqlite-verification]", 1)[1].split("\n[", 1)[0]
        command = re.search(r"^command=(.*)$", program, re.MULTILINE).group(1)
        self.assertIn("python config/sqlite_background.py", command)
        self.assertNotIn("manage.py", command)
        self.assertIn("sleep 600", command)

    def test_slice_imports_neither_django_nor_celery(self):
        # PYTHONPROFILEIMPORTTIME reaches the deadline worker too, so this
        # covers both processes of a real supervised slice.
        result = self.run_entry(PYTHONPROFILEIMPORTTIME="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[sqlite-verification] slice status=", result.stderr)
        imported = {
            line.rsplit("|", 1)[1].strip()
            for line in result.stderr.splitlines()
            if line.startswith("import time:") and "|" in line
        }
        heavy = sorted(
            name
            for name in imported
            if name.split(".")[0] in {"django", "celery", "kombu", "redis"}
        )
        self.assertEqual(heavy, [])
        self.assertTrue(Path(f"{self.database}.verification.progress.json").exists())

    def test_entry_records_the_same_progress_as_the_command_path(self):
        twin = self.make_database(self.directory / "b")
        for _ in range(2):  # the second pass is the idle one: nothing is due
            result = self.run_entry(background.WORKER_FLAG)
            self.assertEqual(result.returncode, 0, result.stderr)
            with mock.patch("config.sqlite_background.os.nice"):
                background.run_background_slice(str(twin))
            self.assertEqual(
                _without_stamps(self.progress(self.database)),
                _without_stamps(self.progress(twin)),
            )
        self.assertEqual(self.progress(self.database)["status"], "ok")

    def test_command_background_path_runs_the_shared_slice(self):
        with (
            override_settings(USING_SQLITE_DATABASE=True, FLOPPY_DB_PATH=self.database),
            mock.patch(
                "app.management.commands.sqlite_startup.run_background_slice"
            ) as run,
        ):
            call_command("sqlite_startup", background=True, worker=True)
        run.assert_called_once_with(str(self.database))

    def test_failed_slice_exits_nonzero_with_the_reason(self):
        self.database.write_bytes(b"not a database" * 100)
        result = self.run_entry(background.WORKER_FLAG)
        self.assertEqual(result.returncode, 1)
        self.assertIn("[sqlite-verification] slice error:", result.stderr)

    def test_database_path_matches_settings_resolution(self):
        data_dir = self.directory / "data"
        external = self.directory / "elsewhere" / "floppy.sqlite3"
        cases = (
            ({"FLOPPY_DATA_DIR": str(data_dir)}, data_dir / "db.sqlite3"),
            (
                {"FLOPPY_DATA_DIR": str(data_dir), "FLOPPY_DB_PATH": str(external)},
                external,
            ),
        )
        for overrides, expected in cases:
            with (
                self.subTest(overrides=overrides),
                mock.patch.dict(os.environ, overrides, clear=False),
            ):
                os.environ.pop("DB_HOST", None)
                self.assertEqual(background.database_path(), str(expected))
                self.assertEqual(
                    resolve_data_paths(
                        lambda key, default=None: os.environ.get(key, default),
                        SRC,
                    )[1],
                    expected,
                )

    def test_postgresql_runs_nothing(self):
        with (
            mock.patch.dict(os.environ, {"DB_HOST": "db.example"}),
            mock.patch("config.sqlite_background.subprocess.Popen") as popen,
        ):
            self.assertEqual(background.main([]), 0)
        popen.assert_not_called()

    def test_supervise_stops_a_worker_past_its_deadline(self):
        started = time.monotonic()
        status = background.supervise(
            [sys.executable, "-c", "import time; time.sleep(60)"], timeout=0.5
        )
        self.assertEqual(status, 1)
        self.assertLess(time.monotonic() - started, 30)

    def test_supervise_reports_the_worker_status(self):
        self.assertEqual(background.supervise([sys.executable, "-c", "pass"]), 0)
        self.assertEqual(
            background.supervise([sys.executable, "-c", "raise SystemExit(3)"]), 1
        )
