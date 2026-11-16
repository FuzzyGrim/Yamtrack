"""Module sharding and test accounting in the serial runner.

FLOPPY_TEST_SHARD splits one run into separate serial processes. These tests
pin down the two properties the CI accounting check relies on: every module
lands in exactly one shard, and every outcome is written down as it happens.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from config import test_runner, test_sharding

LABEL = "config.tests"


class ParseShardTests(SimpleTestCase):
    def test_unset_means_no_sharding(self):
        self.assertIsNone(test_sharding.parse_shard(None))
        self.assertIsNone(test_sharding.parse_shard(""))
        self.assertIsNone(test_sharding.parse_shard("  "))

    def test_index_and_count(self):
        self.assertEqual(test_sharding.parse_shard("2/4"), (2, 4))
        self.assertEqual(test_sharding.parse_shard(" 1/1 "), (1, 1))

    def test_rejects_anything_else(self):
        for raw in ("0/4", "5/4", "3", "a/b", "1/0", "-1/2", "1/2/3"):
            with self.subTest(raw=raw), self.assertRaises(test_sharding.ShardSpecError):
                test_sharding.parse_shard(raw)


class AssignTests(SimpleTestCase):
    def test_every_module_lands_in_exactly_one_shard(self):
        modules = [f"app.tests.test_{n}" for n in range(200)]
        weights = {name: float(n % 17) for n, name in enumerate(modules[:150])}
        for count in (1, 2, 3, 5, 8):
            with self.subTest(count=count):
                assignment = test_sharding.assign(modules, weights, count)
                self.assertEqual(set(assignment), set(modules))
                self.assertTrue(
                    all(1 <= shard <= count for shard in assignment.values())
                )

    def test_heaviest_first_on_the_lightest_shard(self):
        weights = {"a": 10.0, "b": 6.0, "c": 5.0, "d": 1.0}
        self.assertEqual(
            test_sharding.assign(weights, weights, 2),
            {"a": 1, "b": 2, "c": 2, "d": 1},
        )

    def test_order_of_discovery_does_not_matter(self):
        modules = [f"m{n}" for n in range(50)]
        weights = {name: float(len(name)) for name in modules[::2]}
        forwards = test_sharding.assign(modules, weights, 4)
        backwards = test_sharding.assign(list(reversed(modules)), weights, 4)
        self.assertEqual(forwards, backwards)

    def test_new_module_uses_a_stable_hash(self):
        # sha256, not Python's salted hash(): the same in every process.
        assignment = test_sharding.assign(["app.tests.test_brand_new"], {}, 5)
        self.assertEqual(assignment, {"app.tests.test_brand_new": 2})
        self.assertEqual(test_sharding.stable_bucket("app.tests.test_brand_new", 5), 2)

    def test_weights_for_absent_modules_are_ignored(self):
        assignment = test_sharding.assign(["kept"], {"gone": 99.0, "kept": 1.0}, 3)
        self.assertEqual(assignment, {"kept": 1})

    def test_committed_weights_load(self):
        weights = test_sharding.load_weights()
        self.assertIn("app.tests.views.test_statistics", weights)
        self.assertTrue(all(seconds >= 0 for seconds in weights.values()))

    def test_each_shard_gets_its_own_database_name(self):
        self.assertEqual(
            test_sharding.shard_database_name("test_floppy", None), "test_floppy"
        )
        self.assertEqual(
            test_sharding.shard_database_name("test_floppy", ""), "test_floppy"
        )
        names = {
            test_sharding.shard_database_name("test_floppy", f"{i}/4")
            for i in range(1, 5)
        }
        self.assertEqual(len(names), 4)
        self.assertIn("test_floppy_shard2of4", names)
        with self.assertRaises(test_sharding.ShardSpecError):
            test_sharding.shard_database_name("test_floppy", "9/4")

    def test_missing_weights_file_is_empty(self):
        self.assertEqual(test_sharding.load_weights(Path("/nonexistent/w.json")), {})


def _ids(suite):
    return [test.id() for test in test_runner.iter_test_cases(suite)]


class RunnerShardTests(SimpleTestCase):
    def _build(self, env):
        with (
            patch.dict("os.environ", env, clear=False),
            patch("sys.stderr", StringIO()),
            patch("sys.stdout", StringIO()),
        ):
            runner = test_runner.ResilientDiscoverRunner(verbosity=0)
            return runner.build_suite([LABEL])

    def test_shards_partition_the_suite(self):
        everything = _ids(self._build({"FLOPPY_TEST_SHARD": ""}))
        shards = [_ids(self._build({"FLOPPY_TEST_SHARD": f"{i}/3"})) for i in (1, 2, 3)]
        combined = [test_id for shard in shards for test_id in shard]
        self.assertEqual(sorted(combined), sorted(everything))
        self.assertEqual(len(combined), len(set(combined)))

    def test_a_module_is_never_split(self):
        shards_of = {}
        for index in (1, 2, 3):
            suite = self._build({"FLOPPY_TEST_SHARD": f"{index}/3"})
            for test in test_runner.iter_test_cases(suite):
                shards_of.setdefault(test_sharding.module_of(test), set()).add(index)
        self.assertTrue(shards_of)
        self.assertEqual({m: s for m, s in shards_of.items() if len(s) > 1}, {})

    def test_writes_selected_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            suite = self._build(
                {"FLOPPY_TEST_SHARD": "2/2", "FLOPPY_TEST_ACCOUNTING_DIR": directory}
            )
            written = (Path(directory) / "selected.txt").read_text().split()
        self.assertEqual(written, sorted(_ids(suite)))

    def test_refuses_the_parallel_pool(self):
        with patch.dict(
            "os.environ", {"FLOPPY_TEST_SHARD": "1/2", "FLOPPY_TEST_PARALLEL": "2"}
        ):
            runner = test_runner.ResilientDiscoverRunner(verbosity=0)
            with self.assertRaises(ImproperlyConfigured):
                runner.build_suite([LABEL])


class OwnedEnvironmentTests(SimpleTestCase):
    def test_a_runner_built_inside_a_test_inherits_nothing(self):
        # The CI shard's own runner takes these variables. A runner that a
        # test builds afterwards (or a subprocess) must not see them, or it
        # rewrites the shard's selected.txt. That happened on the first CI run.
        env = {
            "FLOPPY_TEST_SHARD": "2/5",
            "FLOPPY_TEST_ACCOUNTING_DIR": "outer-accounting",
            "FLOPPY_TEST_LIST_ONLY": "1",
        }
        with patch.dict("os.environ", env):
            outer = test_runner.ResilientDiscoverRunner(verbosity=0)
            leaked = {name for name in env if name in os.environ}
            inner = test_runner.ResilientDiscoverRunner(verbosity=0)
        self.assertEqual(leaked, set())
        self.assertEqual(outer._shard_spec, "2/5")
        self.assertEqual(outer._accounting_dir, Path("outer-accounting"))
        self.assertIsNone(inner._shard_spec)
        self.assertIsNone(inner._accounting_dir)
        self.assertFalse(inner._list_only)


class ListOnlyTests(SimpleTestCase):
    def test_lists_without_a_database(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(
                "os.environ",
                {"FLOPPY_TEST_LIST_ONLY": "1", "FLOPPY_TEST_ACCOUNTING_DIR": directory},
            ),
            patch("sys.stderr", StringIO()),
            patch("sys.stdout", StringIO()),
        ):
            runner = test_runner.ResilientDiscoverRunner(verbosity=0)
            with (
                patch.object(runner, "setup_test_environment"),
                patch.object(runner, "teardown_test_environment"),
                patch.object(
                    runner, "setup_databases", side_effect=AssertionError("no database")
                ),
            ):
                self.assertEqual(runner.run_tests([LABEL]), 0)
            listed = (Path(directory) / "selected.txt").read_text().split()
        self.assertIn(f"{__name__}.ListOnlyTests.test_lists_without_a_database", listed)

    def test_a_module_that_fails_to_import_fails_the_listing(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(
                "os.environ",
                {"FLOPPY_TEST_LIST_ONLY": "1", "FLOPPY_TEST_ACCOUNTING_DIR": directory},
            ),
            patch("sys.stderr", StringIO()),
            patch("sys.stdout", StringIO()),
        ):
            runner = test_runner.ResilientDiscoverRunner(verbosity=0)
            failed, _message = unittest.loader._make_failed_test(
                "config.tests.test_broken",
                ImportError("boom"),
                unittest.TestSuite,
                "boom",
            )
            with (
                patch.object(runner, "setup_test_environment"),
                patch.object(runner, "teardown_test_environment"),
                patch.object(runner, "build_suite", return_value=failed),
            ):
                self.assertEqual(runner.run_tests([LABEL]), 1)

    def test_needs_somewhere_to_write(self):
        with patch.dict(
            "os.environ",
            {"FLOPPY_TEST_LIST_ONLY": "1", "FLOPPY_TEST_ACCOUNTING_DIR": ""},
        ):
            runner = test_runner.ResilientDiscoverRunner(verbosity=0)
            with self.assertRaises(ImproperlyConfigured):
                runner.run_tests([LABEL])


def _outcomes():
    """Built on demand so the runner never discovers these as real tests."""

    class Outcomes(unittest.TestCase):
        def test_pass(self):
            pass

        def test_fail(self):
            self.fail("expected")

        def test_error(self):
            raise RuntimeError("expected")

        @unittest.skip("expected")
        def test_skip(self):
            pass

        def test_failing_subtest(self):
            with self.subTest(n=1):
                self.fail("expected")

    return Outcomes


class RecordingResultTests(SimpleTestCase):
    def test_every_outcome_is_reported_and_timed(self):
        names = [
            "test_pass",
            "test_fail",
            "test_error",
            "test_skip",
            "test_failing_subtest",
        ]
        outcomes = _outcomes()
        suite = unittest.TestSuite(outcomes(name) for name in names)
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict("os.environ", {"FLOPPY_TEST_ACCOUNTING_DIR": directory}),
            patch("sys.stderr", StringIO()),
            patch("sys.stdout", StringIO()),
        ):
            runner = test_runner.ResilientDiscoverRunner(verbosity=0)
            result = runner.run_suite(suite)
            reported = (Path(directory) / "reported.txt").read_text().split()
            seconds = json.loads((Path(directory) / "module_seconds.json").read_text())
        self.assertEqual(result.testsRun, 5)
        self.assertEqual(set(reported), {outcomes(name).id() for name in names})
        self.assertEqual(list(seconds), [__name__])

    def test_reported_ids_survive_a_dead_process(self):
        # Lines are flushed as they are written, so a shard killed mid-run
        # (os._exit, a segfault, the timeout) leaves what it finished.
        with tempfile.TemporaryDirectory() as directory:
            sink = test_runner._AccountingSink(Path(directory))
            test = _outcomes()("test_pass")
            sink.reported(test)
            on_disk = (Path(directory) / "reported.txt").read_text()
            sink.close()
        self.assertEqual(on_disk, f"{test.id()}\n")

    def test_without_the_variable_nothing_changes(self):
        with patch.dict("os.environ", {"FLOPPY_TEST_ACCOUNTING_DIR": ""}):
            runner = test_runner.ResilientDiscoverRunner(verbosity=0)
            self.assertIsNone(runner.get_resultclass())
