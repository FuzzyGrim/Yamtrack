"""Tests for scripts/check_migration_order.py, the guard against migration collisions.

``scripts/`` is not a package, so the script is loaded by path, as in
test_container_memory_sample. Each test builds a throwaway git repository that
reproduces how two parallel PRs collide.
"""

import importlib.util
import os
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_migration_order.py"
_spec = importlib.util.spec_from_file_location("check_migration_order", _SCRIPT)
order = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(order)


class MigrationOrderTests(TestCase):
    """A PR that adds a migration must contain the migrations already on latest."""

    def setUp(self):
        """Start a repo whose `latest` branch already has users 0001."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = tmp.name
        self._git("init", "-q", "-b", "latest")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test")
        self._commit("src/users/migrations/0001_initial.py")

    def _git(self, *args):
        return subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            cwd=self.repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    def _commit(self, path):
        file = Path(self.repo) / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("x\n")
        self._git("add", path)
        self._git("commit", "-q", "-m", f"add {path}")

    def _branch_from_latest(self, name):
        self._git("checkout", "-q", "-b", name, "latest")

    def _check(self, head):
        return order.migrations_missing_from_head(head, "latest", cwd=self.repo)

    def _fails(self, head):
        changed, gained = self._check(head)
        return bool(changed and gained)

    def test_two_prs_adding_the_next_migration_collide(self):
        """The second PR to merge must be told to pick up the first one's migration."""
        self._branch_from_latest("pr-a")
        self._commit("src/users/migrations/0002_a.py")
        self._git("checkout", "-q", "latest")
        self._branch_from_latest("pr-b")
        self._commit("src/users/migrations/0002_b.py")

        # PR A merges first, so latest now has 0002_a.
        self._git("checkout", "-q", "latest")
        self._git("merge", "-q", "--no-ff", "-m", "merge A", "pr-a")

        self.assertTrue(self._fails("pr-b"))
        changed, gained = self._check("pr-b")
        self.assertEqual(changed, ["src/users/migrations/0002_b.py"])
        self.assertEqual(gained, ["src/users/migrations/0002_a.py"])

    def test_passes_after_the_pr_merges_latest(self):
        """Updating the branch from latest clears the failure."""
        self._branch_from_latest("pr-b")
        self._commit("src/users/migrations/0002_b.py")
        self._git("checkout", "-q", "latest")
        self._commit("src/users/migrations/0002_a.py")
        self.assertTrue(self._fails("pr-b"))

        self._git("checkout", "-q", "pr-b")
        self._git("merge", "-q", "--no-edit", "latest")

        self.assertFalse(self._fails("pr-b"))

    def test_pr_without_migrations_is_never_blocked(self):
        """Only PRs that touch migrations can collide, so the rest pass."""
        self._branch_from_latest("pr-docs")
        self._commit("README.md")
        self._git("checkout", "-q", "latest")
        self._commit("src/users/migrations/0002_a.py")

        self.assertFalse(self._fails("pr-docs"))

    def test_latest_gaining_no_migration_does_not_block(self):
        """A moving latest is fine while it adds no migration."""
        self._branch_from_latest("pr-b")
        self._commit("src/users/migrations/0002_b.py")
        self._git("checkout", "-q", "latest")
        self._commit("src/app/views.py")

        self.assertFalse(self._fails("pr-b"))

    def test_only_migration_files_count(self):
        """__init__.py and files outside migrations folders are not migrations."""
        self._branch_from_latest("pr-b")
        self._commit("src/users/migrations/0002_b.py")
        self._git("checkout", "-q", "latest")
        self._commit("src/users/migrations/__init__.py")
        self._commit("docs/migrations/0003_notes.py")

        self.assertFalse(self._fails("pr-b"))

    def test_main_exit_codes(self):
        """The command line wrapper returns 1 for a stale PR and 0 otherwise."""
        self._branch_from_latest("pr-b")
        self._commit("src/users/migrations/0002_b.py")
        self._git("checkout", "-q", "latest")
        self._commit("src/users/migrations/0002_a.py")

        cwd = Path.cwd()
        try:
            os.chdir(self.repo)
            self.assertEqual(order.main(["x", "pr-b", "latest"]), 1)
            self._git("checkout", "-q", "pr-b")
            self._git("merge", "-q", "--no-edit", "latest")
            self.assertEqual(order.main(["x", "pr-b", "latest"]), 1)
            self._git("mv", "src/users/migrations/0002_b.py", "src/users/migrations/0003_b.py")
            self._git("commit", "-q", "-m", "renumber conflicting migration")
            self.assertEqual(order.main(["x", "pr-b", "latest"]), 0)
        finally:
            os.chdir(cwd)

    def test_existing_duplicates_are_grandfathered(self):
        self._commit("src/users/migrations/0001_historical.py")
        self._branch_from_latest("pr-new")
        self._commit("src/users/migrations/0002_new.py")
        self.assertEqual(order.new_duplicate_numbers("pr-new", "latest", cwd=self.repo), [])

    def test_new_duplicate_in_one_pr_is_rejected(self):
        self._branch_from_latest("pr-new")
        self._commit("src/users/migrations/0002_one.py")
        self._commit("src/users/migrations/0002_two.py")
        self.assertEqual(
            order.new_duplicate_numbers("pr-new", "latest", cwd=self.repo),
            ["src/users/migrations/0002_one.py", "src/users/migrations/0002_two.py"],
        )

    def test_same_number_in_different_apps_is_allowed(self):
        self._branch_from_latest("pr-new")
        self._commit("src/app/migrations/0001_initial.py")
        self.assertEqual(order.new_duplicate_numbers("pr-new", "latest", cwd=self.repo), [])
