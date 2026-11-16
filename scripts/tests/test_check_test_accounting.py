"""The CI accounting check must fail, naming the tests, for every gap.

These are the failure injections from the shard plan, run against the real
script: a missing module, a module in two shards, a shard that died before
reporting, plus the cases where a whole artifact is missing.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_test_accounting.py"
REFRESH = ROOT / "scripts" / "refresh_test_shard_weights.py"
spec = importlib.util.spec_from_file_location("check_test_accounting", SCRIPT)
accounting = importlib.util.module_from_spec(spec)
spec.loader.exec_module(accounting)

APP = [f"app.tests.test_a.A.test_{n}" for n in range(3)]
OTHER = [f"users.tests.test_b.B.test_{n}" for n in range(2)]


def _write(directory: Path, name: str, ids) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text("".join(f"{i}\n" for i in ids))


class AccountingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.expected = self.root / "expected"
        self.shards = self.root / "shards"
        _write(self.expected / "app", "selected.txt", APP)
        _write(self.expected / "other", "selected.txt", OTHER)

    def tearDown(self):
        self._tmp.cleanup()

    def shard(self, name, selected, reported=None):
        _write(self.shards / name, "selected.txt", selected)
        _write(
            self.shards / name,
            "reported.txt",
            selected if reported is None else reported,
        )

    def run_check(self):
        passed, report = accounting.check(self.expected, self.shards)
        return passed, "\n".join(report)

    def test_complete_and_disjoint_passes(self):
        self.shard("test-accounting-unit-1", APP[:2])
        self.shard("test-accounting-unit-2", APP[2:] + OTHER)
        passed, report = self.run_check()
        self.assertTrue(passed, report)
        self.assertIn("OK: all 5 required tests", report)

    def test_module_missing_from_every_shard(self):
        self.shard("test-accounting-unit-1", APP)
        passed, report = self.run_check()
        self.assertFalse(passed)
        self.assertIn("Required but selected by no shard: 2", report)
        for test_id in OTHER:
            self.assertIn(test_id, report)

    def test_module_in_two_shards(self):
        self.shard("test-accounting-unit-1", APP + OTHER)
        self.shard("test-accounting-unit-2", OTHER)
        passed, report = self.run_check()
        self.assertFalse(passed)
        self.assertIn("Selected by more than one shard: 2", report)
        self.assertIn(
            f"{OTHER[0]}  (test-accounting-unit-1, test-accounting-unit-2)", report
        )

    def test_shard_died_before_reporting(self):
        # os._exit in a test: reported.txt holds only what finished first.
        self.shard("test-accounting-unit-1", APP + OTHER, reported=APP[:1])
        passed, report = self.run_check()
        self.assertFalse(passed)
        self.assertIn(
            "test-accounting-unit-1: selected but no result reported: 4", report
        )
        self.assertIn(APP[1], report)

    def test_shard_with_no_reported_file(self):
        _write(self.shards / "test-accounting-browser", "selected.txt", APP + OTHER)
        passed, report = self.run_check()
        self.assertFalse(passed)
        self.assertIn("no result reported: 5", report)

    def test_selected_twice_within_one_shard(self):
        self.shard("test-accounting-unit-1", APP + OTHER + APP[:1])
        passed, report = self.run_check()
        self.assertFalse(passed)
        self.assertIn("selected more than once: 1", report)

    def test_test_outside_the_contract(self):
        self.shard(
            "test-accounting-unit-1", [*APP, *OTHER, "lists.tests.test_x.X.test_y"]
        )
        passed, report = self.run_check()
        self.assertFalse(passed)
        self.assertIn("Selected but not in the required set: 1", report)

    def test_no_discovery_or_no_shards_fails(self):
        passed, report = accounting.check(self.root / "nothing", self.shards)
        self.assertFalse(passed)
        self.assertIn("No discovery output", "\n".join(report))
        passed, report = accounting.check(self.expected, self.root / "nothing")
        self.assertFalse(passed)
        self.assertIn("No shard output", "\n".join(report))

    def test_class_level_skip_answers_for_its_tests(self):
        # A setUpClass that raises SkipTest is reported once, for the class.
        self.shard(
            "test-accounting-browser",
            APP + OTHER,
            reported=["setUpClass (app.tests.test_a.A)", *OTHER],
        )
        passed, report = self.run_check()
        self.assertTrue(passed, report)

    def test_class_level_outcome_does_not_cover_other_classes(self):
        self.shard(
            "test-accounting-unit-1",
            APP + OTHER,
            reported=["setUpClass (app.tests.test_a.AB)", *OTHER],
        )
        passed, report = self.run_check()
        self.assertFalse(passed)
        self.assertIn("no result reported: 3", report)

    def test_empty_shard_is_allowed(self):
        # The browser shard on the publish gate selects nothing.
        self.shard("test-accounting-browser", [])
        self.shard("test-accounting-unit-1", APP + OTHER)
        passed, report = self.run_check()
        self.assertTrue(passed, report)

    def test_command_line_exit_status(self):
        self.shard("test-accounting-unit-1", APP)
        completed = subprocess.run(  # noqa: S603
            [
                sys.executable,
                str(SCRIPT),
                "--expected",
                str(self.expected),
                "--shards",
                str(self.shards),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn(OTHER[0], completed.stdout)


class RefreshWeightsTests(unittest.TestCase):
    def test_sums_modules_across_shards(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, seconds in (("u1", {"a": 1.5, "b": 2}), ("u2", {"c": 3.25})):
                (root / name).mkdir()
                (root / name / "module_seconds.json").write_text(json.dumps(seconds))
            output = root / "weights.json"
            subprocess.run(  # noqa: S603
                [
                    sys.executable,
                    str(REFRESH),
                    "--run",
                    "123",
                    "--output",
                    str(output),
                    str(root / "u1"),
                    str(root / "u2"),
                ],
                check=True,
                capture_output=True,
            )
            document = json.loads(output.read_text())
        self.assertEqual(document["modules"], {"a": 1.5, "b": 2.0, "c": 3.25})
        self.assertIn("123", document["source"])

    def test_averages_a_module_seen_in_several_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, seconds in (("r1-u1", {"a": 2.0}), ("r2-u3", {"a": 4.0, "b": 1})):
                (root / name).mkdir()
                (root / name / "module_seconds.json").write_text(json.dumps(seconds))
            output = root / "weights.json"
            subprocess.run(  # noqa: S603
                [sys.executable, str(REFRESH), "--run", "1", "--run", "2",
                 "--output", str(output), str(root / "r1-u1"), str(root / "r2-u3")],
                check=True, capture_output=True,
            )
            document = json.loads(output.read_text())
        self.assertEqual(document["modules"], {"a": 3.0, "b": 1.0})


if __name__ == "__main__":
    unittest.main()
