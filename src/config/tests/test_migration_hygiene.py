from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from app.management.commands import check_migration_hygiene


class _FakeGraph:
    def __init__(self, leaf_map):
        self._leaf_map = leaf_map

    def leaf_nodes(self, app_label):
        return self._leaf_map.get(app_label, [])


class MigrationHygieneCommandTests(TestCase):
    """Tests for migration hygiene command helpers and smoke behavior."""

    @override_settings(MIGRATION_MODULES={})
    def test_strict_check_fails_when_the_base_ref_is_missing(self):
        with (
            patch.object(
                check_migration_hygiene,
                "_resolve_base_ref",
                side_effect=CommandError("missing base"),
            ),
            self.assertRaisesRegex(CommandError, "requires a resolvable base ref"),
        ):
            call_command("check_migration_hygiene", strict=True, stdout=StringIO())

    @override_settings(MIGRATION_MODULES={})
    def test_command_passes_with_head_baseline_for_users(self):
        """The command should pass against HEAD baseline for a stable app graph."""
        # Inspect the real graph even when FAST_DB builds the test schema
        # without migrations. This command's contract is the on-disk graph.
        output = StringIO()

        call_command(
            "check_migration_hygiene",
            base_ref="HEAD",
            apps="users",
            stdout=output,
        )

        self.assertIn("Migration hygiene checks passed", output.getvalue())

    def test_multi_leaf_check_runs_even_when_base_ref_cannot_be_resolved(self):
        """A graph conflict must fail the command even with no resolvable base ref.

        Regression test for the bug where `_resolve_base_ref` raised before the
        multi-leaf check ever ran, so a genuine leaf-node conflict was masked by
        an unrelated "base ref not found" error whenever no upstream-style ref
        (upstream/dev, origin/dev, dev) existed in the checkout.
        """
        output = StringIO()
        fake_graph = _FakeGraph(
            {
                "users": [
                    ("users", "0040_feature_branch_a"),
                    ("users", "0040_feature_branch_b"),
                ],
            }
        )

        class _FakeLoader:
            def __init__(self, *_args, **_kwargs):
                self.graph = fake_graph

        with (
            patch.object(check_migration_hygiene, "MigrationLoader", _FakeLoader),
            self.assertRaises(CommandError) as raised,
        ):
            call_command(
                "check_migration_hygiene",
                base_ref="definitely-not-a-real-ref",
                apps="users",
                stdout=output,
                stderr=StringIO(),
            )

        self.assertIn("Multiple migration leaf nodes detected", str(raised.exception))
        self.assertNotIn("Base ref", str(raised.exception))

    def test_graph_check_without_a_base_ref_is_what_migration_order_runs(self):
        """The Migration Order job runs --no-strict, with no upstream ref fetched.

        Two migrations that merge without a text conflict leave two leaf nodes;
        that must fail, with the hint pointing at renumbering rather than a
        merge migration, while a single leaf passes without needing a base ref.
        """

        def run(leaves):
            fake_graph = _FakeGraph({"users": leaves})

            class _FakeLoader:
                def __init__(self, *_args, **_kwargs):
                    self.graph = fake_graph

            with patch.object(check_migration_hygiene, "MigrationLoader", _FakeLoader):
                call_command(
                    "check_migration_hygiene",
                    base_ref="definitely-not-a-real-ref",
                    apps="users",
                    strict=False,
                    stdout=StringIO(),
                    stderr=StringIO(),
                )

        run([("users", "0040_only_leaf")])
        with self.assertRaises(CommandError) as raised:
            run([("users", "0040_branch_a"), ("users", "0040_branch_b")])
        self.assertIn("0040_branch_a, users.0040_branch_b", str(raised.exception))
        self.assertIn("renumber", str(raised.exception))

    def test_collect_multi_leaf_apps_flags_branch_splits(self):
        """Branch splits should be reported when an app has multiple leaf nodes."""
        graph = _FakeGraph(
            {
                "users": [
                    ("users", "0040_feature_branch_a"),
                    ("users", "0040_feature_branch_b"),
                ],
                "app": [("app", "0093_latest")],
            }
        )

        result = check_migration_hygiene._collect_multi_leaf_apps(
            graph, ["users", "app"]
        )

        self.assertEqual(
            result,
            {
                "users": [
                    "users.0040_feature_branch_a",
                    "users.0040_feature_branch_b",
                ]
            },
        )

    def test_find_risky_operations_detects_raw_schema_ops(self):
        """Raw migrations.AddConstraint should be detected as a risky operation."""
        with TemporaryDirectory() as tmp_dir:
            migration_path = Path(tmp_dir) / "9999_bad_migration.py"
            migration_path.write_text(
                (
                    "from django.db import migrations\n"
                    "\n"
                    "class Migration(migrations.Migration):\n"
                    "    operations = [\n"
                    "        migrations.AddConstraint(\n"
                    "            model_name='user',\n"
                    "            constraint=None,\n"
                    "        ),\n"
                    "        AddConstraintIfNotExists(\n"
                    "            model_name='user',\n"
                    "            constraint=None,\n"
                    "        ),\n"
                    "    ]\n"
                ),
                encoding="utf-8",
            )

            violations = check_migration_hygiene._find_risky_operations(
                migration_path,
                "src/users/migrations/9999_bad_migration.py",
            )

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].operation, "AddConstraint")
        self.assertEqual(violations[0].line_number, 5)


class InlineOperationGuardTests(TestCase):
    """New migrations must import the idempotent operations, not copy them."""

    def _write_migration(self, repo_root: Path, app: str, name: str, body: str):
        migration_dir = repo_root / "src" / app / "migrations"
        migration_dir.mkdir(parents=True, exist_ok=True)
        path = migration_dir / name
        path.write_text(body, encoding="utf-8")
        return path

    def test_inline_class_in_a_new_migration_is_reported(self):
        with TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            self._write_migration(
                repo_root,
                "users",
                "9999_new_inline.py",
                "from django.db import migrations\n"
                "\n"
                "\n"
                "class RemoveConstraintIfExists(migrations.RemoveConstraint):\n"
                "    pass\n",
            )
            self._write_migration(
                repo_root,
                "users",
                "9998_imports_shared.py",
                "from app.migration_operations import AddFieldIfNotExists\n",
            )

            violations = check_migration_hygiene._inline_operation_violations(
                repo_root, ["users"]
            )

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].path, "src/users/migrations/9999_new_inline.py")
        self.assertEqual(violations[0].class_name, "RemoveConstraintIfExists")
        self.assertEqual(violations[0].line_number, 4)

    def test_allowlisted_shipped_migration_is_not_reported(self):
        rel_path = sorted(check_migration_hygiene._inline_allowlist())[0]
        app = rel_path.split("/")[1]
        with TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            self._write_migration(
                repo_root,
                app,
                Path(rel_path).name,
                "class AddFieldIfNotExists:\n    pass\n",
            )

            violations = check_migration_hygiene._inline_operation_violations(
                repo_root, [app]
            )

        self.assertEqual(violations, [])

    def test_allowlist_only_names_real_inline_copies(self):
        """Every entry is a real file that still defines a class inline.

        The files are frozen, so this holds forever; a typo or a stale entry
        would otherwise quietly widen the allowlist.
        """
        repo_root = check_migration_hygiene._repo_root()
        for rel_path in sorted(check_migration_hygiene._inline_allowlist()):
            with self.subTest(path=rel_path):
                path = repo_root / rel_path
                self.assertTrue(path.exists())
                self.assertTrue(
                    check_migration_hygiene._find_inline_operation_classes(
                        path, rel_path
                    )
                )

    def test_shipped_tree_has_no_unlisted_inline_copies(self):
        repo_root = check_migration_hygiene._repo_root()
        self.assertEqual(
            check_migration_hygiene._inline_operation_violations(
                repo_root, list(check_migration_hygiene.DEFAULT_APPS)
            ),
            [],
        )

    @override_settings(MIGRATION_MODULES={})
    def test_strict_command_fails_with_a_pointer_to_the_shared_module(self):
        violation = check_migration_hygiene.InlineOperationViolation(
            path="src/users/migrations/9999_new_inline.py",
            line_number=4,
            class_name="AddFieldIfNotExists",
        )
        with (
            patch.object(
                check_migration_hygiene,
                "_inline_operation_violations",
                return_value=[violation],
            ),
            self.assertRaises(CommandError) as raised,
        ):
            call_command(
                "check_migration_hygiene",
                base_ref="HEAD",
                apps="users",
                stdout=StringIO(),
            )

        message = str(raised.exception)
        self.assertIn("from app.migration_operations import", message)
        self.assertIn("9999_new_inline.py:4: class AddFieldIfNotExists", message)

    @override_settings(MIGRATION_MODULES={})
    def test_no_strict_only_warns(self):
        violation = check_migration_hygiene.InlineOperationViolation(
            path="src/users/migrations/9999_new_inline.py",
            line_number=4,
            class_name="AddFieldIfNotExists",
        )
        output = StringIO()
        with patch.object(
            check_migration_hygiene,
            "_inline_operation_violations",
            return_value=[violation],
        ):
            call_command(
                "check_migration_hygiene",
                base_ref="HEAD",
                apps="users",
                strict=False,
                stdout=output,
            )

        self.assertIn("1 inline idempotent operation classes", output.getvalue())
