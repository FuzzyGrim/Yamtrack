"""Tests for scripts/merge_train.py, the queue that keeps approved PRs up to date.

``scripts/`` is not a package, so the script is loaded by path, as in
test_migration_order.
"""

import importlib.util
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import yaml

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "merge_train.py"
_spec = importlib.util.spec_from_file_location("merge_train", _SCRIPT)
train = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(train)

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
LATEST_SHA = "a" * 40


class _Clock(datetime):
    """datetime whose now() is the fixed NOW, so fixtures never age."""

    @classmethod
    def now(cls, _tz=None):
        return NOW


def _iso(delta=timedelta()):
    return (NOW - delta).isoformat()


def running(minutes=10):
    """All required checks queued or running, started `minutes` ago."""
    started = _iso(timedelta(minutes=minutes))
    return [
        ("lint", "IN_PROGRESS", None, started, None),
        ("test (3.12)", "IN_PROGRESS", None, started, None),
        ("migration-order", "IN_PROGRESS", None, started, None),
    ]


def passed(minutes_ago=5):
    """All required checks finished green `minutes_ago` minutes ago."""
    done = _iso(timedelta(minutes=minutes_ago))
    return [
        ("lint", "COMPLETED", "SUCCESS", done, done),
        ("test (3.12)", "COMPLETED", "SUCCESS", done, done),
        ("migration-order", "COMPLETED", "SUCCESS", done, done),
    ]


def pr(
    number,
    enabled="2026-10-05T01:00:00Z",
    state="BLOCKED",
    draft=False,
    checks=None,
    fork=False,
    editable=True,
    mergeable="MERGEABLE",
    committed=timedelta(minutes=5),
):
    """Build a PR as the GraphQL query returns it (enabled=None: no auto-merge)."""
    return {
        "number": number,
        "body": "Example PR description",
        "isDraft": draft,
        "isCrossRepository": fork,
        "maintainerCanModify": editable,
        "mergeable": mergeable,
        "mergeStateStatus": state,
        "headRefOid": f"sha{number}",
        "autoMergeRequest": {"enabledAt": enabled} if enabled else None,
        "commits": {
            "nodes": [
                {
                    "commit": {
                        "committedDate": _iso(committed),
                        "statusCheckRollup": {
                            "contexts": {
                                "nodes": [
                                    {
                                        "name": n,
                                        "status": s,
                                        "conclusion": c,
                                        "startedAt": t,
                                        "completedAt": d,
                                    }
                                    for n, s, c, t, d in (
                                        running() if checks is None else checks
                                    )
                                ]
                            }
                        },
                    }
                }
            ]
        },
    }


class MergeTrainTests(TestCase):
    """One approved PR at a time is updated; the rest wait or are skipped."""

    def test_empty_queue_is_idle(self):
        """No PR with auto-merge on means there is nothing to do."""
        self.assertEqual(train.choose([], {}, NOW)[0], "idle")

    def test_drafts_and_unapproved_prs_are_never_touched(self):
        """A PR without auto-merge (not approved) or a draft is ignored."""
        prs = [pr(1, enabled=None), pr(2, draft=True)]
        self.assertEqual(train.choose(prs, {1: 3, 2: 3}, NOW), ("idle", None, []))

    def test_oldest_approval_goes_first(self):
        """The PR whose auto-merge was enabled first is updated, not a lower number."""
        prs = [
            pr(5, enabled="2026-10-05T03:00:00Z"),
            pr(9, enabled="2026-10-05T02:00:00Z"),
        ]
        action, number, notes = train.choose(prs, {5: 2, 9: 2}, NOW)
        self.assertEqual((action, number), ("update", 9))
        self.assertIn("#5 queued behind #9", notes)

    def test_up_to_date_front_pr_holds_the_line(self):
        """While the front PR runs CI, a behind PR behind it is not updated."""
        prs = [pr(1), pr(2, enabled="2026-10-05T02:00:00Z")]
        self.assertEqual(train.choose(prs, {1: 0, 2: 4}, NOW)[:2], ("wait", 1))

    def test_current_green_pr_takes_precedence_over_older_stale_branch(self):
        prs = [pr(1), pr(2, checks=passed(), state="CLEAN")]
        self.assertEqual(train.choose(prs, {1: 3, 2: 0}, NOW)[:2], ("wait", 2))

    def test_current_running_pr_takes_precedence_over_stale_green(self):
        prs = [pr(1, checks=passed()), pr(2)]
        self.assertEqual(train.choose(prs, {1: 3, 2: 0}, NOW)[:2], ("wait", 2))

    def test_green_stale_pr_bypasses_recent_pending_pr(self):
        prs = [
            pr(1, enabled="2026-10-05T11:15:00Z"),
            pr(2, enabled="2026-10-05T11:30:00Z", checks=passed()),
        ]
        self.assertEqual(train.choose(prs, {1: 3, 2: 2}, NOW)[:2], ("update", 2))

    def test_aging_rule_prevents_starving_old_pending_pr(self):
        prs = [
            pr(1, enabled="2026-10-05T09:45:00Z"),
            pr(2, enabled="2026-10-05T11:30:00Z", checks=passed()),
        ]
        self.assertEqual(train.choose(prs, {1: 3, 2: 2}, NOW)[:2], ("update", 1))

    def test_old_red_retry_does_not_hold_up_green_stale_pr(self):
        old = pr(1, checks=[
            ("lint", "COMPLETED", "FAILURE", _iso(timedelta(hours=3)), _iso(timedelta(hours=3))),
        ])
        old["_latest_date"] = "2026-10-05T10:00:00Z"
        prs = [old, pr(2, checks=passed())]
        self.assertEqual(train.choose(prs, {1: 3, 2: 2}, NOW)[:2], ("update", 2))

    def test_newer_current_green_pr_takes_precedence_over_running_pr(self):
        prs = [pr(1), pr(2, checks=passed(), state="CLEAN")]
        self.assertEqual(train.choose(prs, {1: 0, 2: 0}, NOW)[:2], ("wait", 2))

    def test_stale_branch_is_updated_whatever_github_calls_the_state(self):
        """A head missing latest commits is behind even if the state is not BEHIND."""
        for state in ("BLOCKED", "UNKNOWN", "UNSTABLE", "BEHIND"):
            with self.subTest(state=state):
                front = pr(1, state=state, mergeable="UNKNOWN")
                self.assertEqual(train.choose([front], {1: 3}, NOW)[:2], ("update", 1))

    def test_behind_state_does_not_override_a_current_head(self):
        """A stale BEHIND reading must not trigger an update of a current branch."""
        front = pr(1, state="BEHIND")
        self.assertEqual(train.choose([front], {1: 0}, NOW)[:2], ("wait", 1))

    def test_unknown_behindness_is_skipped_for_a_retry_not_waited_on(self):
        """If GitHub cannot say, the PR is skipped so the next one can advance."""
        prs = [pr(1), pr(2, enabled="2026-10-05T02:00:00Z")]
        action, number, notes = train.choose(prs, {1: None, 2: 1}, NOW)
        self.assertEqual((action, number), ("update", 2))
        self.assertIn("#1 skipped: could not tell", notes[0])

    def test_unsettled_flags_a_queued_pr_with_unknown_mergeability(self):
        """The script asks GitHub again while a queued PR reads UNKNOWN."""
        self.assertTrue(train.unsettled([pr(1, mergeable="UNKNOWN")]))
        self.assertFalse(train.unsettled([pr(1, mergeable="UNKNOWN", enabled=None)]))
        self.assertFalse(train.unsettled([pr(1)]))

    def test_conflicted_pr_is_skipped(self):
        """A PR that conflicts with latest cannot block the PR after it."""
        prs = [
            pr(1, state="DIRTY"),
            pr(3, mergeable="CONFLICTING", enabled="2026-10-05T01:30:00Z"),
            pr(2, enabled="2026-10-05T02:00:00Z"),
        ]
        action, number, notes = train.choose(prs, {1: 1, 2: 1, 3: 1}, NOW)
        self.assertEqual((action, number), ("update", 2))
        self.assertIn("#1 skipped: merge conflict", notes[0])
        self.assertIn("#3 skipped: merge conflict", notes[1])

    def test_failed_required_check_is_skipped(self):
        """A red required check parks the PR instead of re-running its CI."""
        done = _iso(timedelta(minutes=5))
        red = pr(1, checks=[("test (3.12)", "COMPLETED", "FAILURE", done, done)])
        _, number, notes = train.choose(
            [red, pr(2, enabled="2026-10-05T02:00:00Z")], {1: 0, 2: 1}, NOW
        )
        self.assertEqual(number, 2)
        self.assertIn("'test (3.12)' failed", notes[0])

    def test_failed_migration_order_parks_the_pr(self):
        """A failed required migration check parks the PR for manual repair."""
        done = _iso(timedelta(minutes=5))
        clash = pr(
            1,
            checks=[
                *running(),
                ("migration-order", "COMPLETED", "FAILURE", done, done),
            ],
        )
        self.assertEqual(train.choose([clash], {1: 0}, NOW)[0], "stuck")

    def test_pending_migration_order_keeps_the_queue_in_flight(self):
        waiting = pr(1, checks=[*running()[:-1], ("migration-order", "IN_PROGRESS", None, _iso(), None)])
        self.assertEqual(train.choose([waiting], {1: 0}, NOW)[:2], ("wait", 1))

    def test_failed_optional_check_does_not_block(self):
        """Only the required checks count; CodeQL or the image build do not."""
        done = _iso(timedelta(minutes=5))
        ok = pr(1, checks=[*running(), ("build", "COMPLETED", "FAILURE", done, done)])
        self.assertEqual(train.choose([ok], {1: 0}, NOW)[:2], ("wait", 1))

    def test_hung_required_check_is_skipped(self):
        """A required check pending for hours is treated as stuck."""
        hung = pr(1, checks=running(minutes=180))
        action, _, notes = train.choose([hung], {1: 0}, NOW)
        self.assertEqual(action, "stuck")
        self.assertIn("pending for hours", notes[0])

    def test_running_required_check_is_in_flight(self):
        """A recent pending check is the PR being tested right now."""
        self.assertEqual(train.choose([pr(1)], {1: 0}, NOW)[:2], ("wait", 1))

    def test_checks_not_yet_created_on_a_fresh_commit_are_in_flight(self):
        """Right after an update CI has not started; that is waiting, not stuck."""
        fresh = pr(1, checks=[], committed=timedelta(minutes=2))
        self.assertEqual(train.choose([fresh], {1: 0}, NOW)[:2], ("wait", 1))

    def test_checks_that_never_started_are_skipped(self):
        """A PR CI ignores must not hold the line for ever."""
        ignored = pr(1, checks=[], committed=timedelta(hours=5))
        action, _, notes = train.choose(
            [ignored, pr(2, enabled="2026-10-05T02:00:00Z")], {1: 0, 2: 1}, NOW
        )
        self.assertEqual(action, "update")
        self.assertIn("never started", notes[0])

    def test_partially_missing_required_checks_eventually_park(self):
        partial = pr(1, checks=passed()[:-1], committed=timedelta(hours=3))
        self.assertEqual(train.choose([partial], {1: 0}, NOW)[0], "stuck")

    def test_green_pr_github_just_has_not_merged_yet_is_in_flight(self):
        """All green for a few minutes: auto-merge is about to land it."""
        green = pr(1, state="CLEAN", checks=passed(minutes_ago=3))
        self.assertEqual(train.choose([green], {1: 0}, NOW)[:2], ("wait", 1))

    def test_green_pr_that_never_merges_is_skipped(self):
        """All green for an hour and still open: something else is blocking it."""
        stuck = pr(1, state="BLOCKED", checks=passed(minutes_ago=60))
        action, _, notes = train.choose([stuck], {1: 0}, NOW)
        self.assertEqual(action, "stuck")
        self.assertIn("still reports BLOCKED", notes[0])

    def test_fork_branch_maintainers_cannot_edit_is_reported_not_attempted(self):
        """A fork PR without 'Allow edits from maintainers' needs its contributor."""
        fork = pr(1, fork=True, editable=False)
        action, number, notes = train.choose([fork], {1: 3}, NOW)
        self.assertEqual((action, number), ("manual", None))
        self.assertIn("the contributor has to", notes[0])

    def test_editable_fork_branch_is_updated(self):
        """With 'Allow edits from maintainers' on, a fork PR is updated normally."""
        fork = pr(1, fork=True, editable=True)
        self.assertEqual(train.choose([fork], {1: 3}, NOW)[:2], ("update", 1))

    def test_every_pr_skipped_is_stuck_not_idle(self):
        """A queue of only unworkable PRs must be reported, not read as empty."""
        prs = [pr(1, state="DIRTY"), pr(2, state="DIRTY")]
        self.assertEqual(train.choose(prs, {1: 1, 2: 1}, NOW)[0], "stuck")


class _FakeGh:
    """Stands in for the gh CLI: GraphQL, compare and update-branch calls."""

    def __init__(self, prs, behind, refuse=()):
        self.prs, self.behind, self.refuse = prs, behind, dict(refuse)
        self.updated = []

    def __call__(self, *args):
        if args[:2] == ("api", "graphql"):
            data = {
                "repository": {
                    "ref": {"target": {"oid": LATEST_SHA, "committedDate": "2026-10-05T10:00:00Z"}},
                    "pullRequests": {"nodes": self.prs},
                }
            }
            return json.dumps({"data": data})
        if args[1].startswith(f"repos/o/r/compare/{LATEST_SHA}..."):
            head = args[1].rsplit("...", 1)[1]
            behind = self.behind[int(head.removeprefix("sha"))]
            if behind is None:
                raise subprocess.CalledProcessError(1, args, stderr="boom")
            return f"{behind}\n"
        if args[2] == "PATCH":
            number = int(args[3].split("/")[-1])
            next(pr for pr in self.prs if pr["number"] == number)["body"] = args[-1].removeprefix("body=")
            return "{}"
        number = int(args[3].split("/")[4])
        if number in self.refuse:
            raise subprocess.CalledProcessError(1, args, stderr=self.refuse[number])
        self.updated.append(number)
        return "{}"


class MainTests(TestCase):
    """The whole run: what it calls on GitHub and what exit code it reports."""

    def run_train(self, prs, behind, refuse=(), argv=()):
        """Run main() against fake GitHub data and return (exit code, fake)."""
        fake = _FakeGh(prs, behind, refuse)
        env = {"GH_REPO": "o/r"}
        with (
            patch.object(train, "_gh", fake),
            patch.object(train, "time"),
            patch.object(train, "datetime", _Clock),
            patch.dict("os.environ", env),
        ):
            return train.main(["merge_train.py", *argv]), fake

    def test_stale_branch_is_updated_and_the_run_succeeds(self):
        """The ordinary path: behind latest, so latest is merged into it."""
        code, fake = self.run_train([pr(1, state="UNKNOWN")], {1: 2})
        self.assertEqual((code, fake.updated), (0, [1]))

    def test_old_failed_check_is_refreshed_once_after_latest_advances(self):
        old = pr(
            1,
            checks=[
                ("lint", "COMPLETED", "FAILURE", _iso(timedelta(hours=3)), _iso(timedelta(hours=3))),
                ("test (3.12)", "COMPLETED", "SUCCESS", _iso(), _iso()),
                ("migration-order", "COMPLETED", "SUCCESS", _iso(), _iso()),
            ],
        )
        code, fake = self.run_train([old], {1: 2})
        self.assertEqual((code, fake.updated), (0, [1]))
        self.assertIn(f"merge-train-base:{LATEST_SHA}", old["body"])

    def test_old_failed_check_is_not_retried_against_same_base(self):
        old = pr(1, checks=[
            ("lint", "COMPLETED", "FAILURE", _iso(timedelta(hours=3)), _iso(timedelta(hours=3))),
            ("test (3.12)", "COMPLETED", "SUCCESS", _iso(), _iso()),
            ("migration-order", "COMPLETED", "SUCCESS", _iso(), _iso()),
        ])
        old["_latest_oid"] = LATEST_SHA
        old["_latest_date"] = "2026-10-05T10:00:00Z"
        old["body"] += f"\n<!-- merge-train-base:{LATEST_SHA} -->"
        self.assertEqual(train.judge(old, 2, NOW)[0], "skip")

    def test_failed_fork_update_records_refusal_and_actionable_fallback(self):
        old = pr(
            1, fork=True,
            checks=[
                ("lint", "COMPLETED", "FAILURE", _iso(timedelta(hours=3)), _iso(timedelta(hours=3))),
                ("test (3.12)", "COMPLETED", "SUCCESS", _iso(), _iso()),
                ("migration-order", "COMPLETED", "SUCCESS", _iso(), _iso()),
            ],
        )
        code, fake = self.run_train([old], {1: 2}, {1: "gh: user doesn't have permission (HTTP 403)"})
        self.assertEqual((code, fake.updated), (0, []))
        self.assertIn(f"merge-train-base:{LATEST_SHA}:refused", old["body"])

    def test_dry_run_updates_nothing(self):
        """--dry-run only reports."""
        code, fake = self.run_train([pr(1)], {1: 2}, argv=["--dry-run"])
        self.assertEqual((code, fake.updated), (0, []))

    def test_ci_failure_on_obsolete_or_unapproved_head_is_ignored(self):
        for head in ("obsolete", "sha2"):
            with self.subTest(head=head), patch.dict("os.environ", {"FAILED_CI_HEAD": head}):
                code, fake = self.run_train([pr(1), pr(2, enabled=None)], {1: 2})
                self.assertEqual((code, fake.updated), (0, []))

    def test_ci_failure_on_current_head_advances_next_pr(self):
        done = _iso(timedelta(minutes=5))
        failed = pr(1, checks=[("test (3.12)", "COMPLETED", "FAILURE", done, done)])
        with patch.dict("os.environ", {"FAILED_CI_HEAD": "sha1"}):
            code, fake = self.run_train([failed, pr(2, checks=passed())], {1: 0, 2: 2})
        self.assertEqual((code, fake.updated), (0, [2]))

    def test_empty_queue_succeeds(self):
        """Nothing approved is a healthy, idle train."""
        self.assertEqual(self.run_train([pr(1, enabled=None)], {})[0], 0)

    def test_in_flight_pr_succeeds_even_with_a_conflicted_pr_behind_it(self):
        """A skipped PR is a warning when another PR is advancing."""
        prs = [pr(1), pr(2, state="DIRTY", enabled="2026-10-05T02:00:00Z")]
        code, fake = self.run_train(prs, {1: 0, 2: 1})
        self.assertEqual((code, fake.updated), (0, []))

    def test_refused_fork_update_moves_on_to_the_next_pr(self):
        """A 403 on a fork branch is reported and the next PR is updated."""
        prs = [
            pr(1, fork=True),
            pr(2, enabled="2026-10-05T02:00:00Z"),
        ]
        refuse = {1: "gh: user doesn't have permission (HTTP 403)"}
        code, fake = self.run_train(prs, {1: 1, 2: 1}, refuse)
        self.assertEqual((code, fake.updated), (0, [2]))

    def test_refused_fork_update_is_a_visible_manual_handoff(self):
        """A token cannot update a contributor fork; report it without a false CI error."""
        refuse = {1: "gh: user doesn't have permission (HTTP 403)"}
        code, fake = self.run_train([pr(1, fork=True)], {1: 1}, refuse)
        self.assertEqual((code, fake.updated), (0, []))

    def test_refused_local_branch_remains_an_error(self):
        """A 403 updating our own branch signals broken token permissions."""
        refuse = {1: "gh: user doesn't have permission (HTTP 403)"}
        code, fake = self.run_train([pr(1, fork=False)], {1: 1}, refuse)
        self.assertEqual((code, fake.updated), (1, []))

    def test_fork_permission_skip_does_not_hide_an_unrelated_conflict(self):
        prs = [pr(1, fork=True, editable=False), pr(2, state="DIRTY")]
        code, fake = self.run_train(prs, {1: 1, 2: 1})
        self.assertEqual((code, fake.updated), (1, []))

    def test_prior_refused_fork_is_a_manual_handoff_on_same_base(self):
        """Avoid failing forever on the already-recorded contributor-side refusal."""
        fork = pr(1, fork=True)
        fork["_latest_oid"] = LATEST_SHA
        fork["_latest_date"] = "2026-10-05T10:00:00Z"
        fork["body"] += f"\n<!-- merge-train-base:{LATEST_SHA}:refused -->"
        fork["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"]["nodes"] = [
            {"name": "lint", "status": "COMPLETED", "conclusion": "FAILURE",
             "startedAt": _iso(timedelta(hours=3)), "completedAt": _iso(timedelta(hours=3))}
        ]
        action, _, notes = train.choose([fork], {1: 2}, NOW)
        self.assertEqual(action, "manual")
        self.assertIn("contributor has to", notes[0])

    def test_conflict_found_while_updating_is_reported(self):
        """GitHub says 422 when latest does not merge cleanly into the branch."""
        refuse = {1: "gh: merge conflict between base and head (HTTP 422)"}
        code, _ = self.run_train([pr(1)], {1: 1}, refuse)
        self.assertEqual(code, 1)

    def test_queue_of_only_conflicted_prs_fails_the_run(self):
        """The case that used to end 'success': nothing can move."""
        code, fake = self.run_train([pr(1, state="DIRTY")], {1: 1})
        self.assertEqual((code, fake.updated), (1, []))

    def test_unsettled_mergeability_is_asked_again(self):
        """UNKNOWN is retried a bounded number of times, then the run carries on."""
        fake = _FakeGh([pr(1, mergeable="UNKNOWN")], {1: 1})
        calls = []

        def counting(*args):
            calls.append(args)
            return fake(*args)

        with (
            patch.object(train, "_gh", counting),
            patch.object(train, "time") as fake_time,
            patch.object(train, "datetime", _Clock),
            patch.dict("os.environ", {"GH_REPO": "o/r"}),
        ):
            code = train.main(["merge_train.py"])
        graphql = [c for c in calls if c[:2] == ("api", "graphql")]
        self.assertEqual(len(graphql), 1 + train.UNKNOWN_RETRIES)
        self.assertEqual(fake_time.sleep.call_count, train.UNKNOWN_RETRIES)
        self.assertEqual((code, fake.updated), (0, [1]))

    def test_compare_failure_does_not_block_the_next_pr(self):
        """An API error on one PR is a skip, not a stall."""
        prs = [pr(1), pr(2, enabled="2026-10-05T02:00:00Z")]
        code, fake = self.run_train(prs, {1: None, 2: 1})
        self.assertEqual((code, fake.updated), (0, [2]))


class WorkflowTests(TestCase):
    """The workflow wakes on queue changes and never runs a fork's code."""

    @classmethod
    def setUpClass(cls):
        """Load the workflow once. PyYAML reads the bare key `on` as True."""
        super().setUpClass()
        path = _SCRIPT.parents[1] / ".github" / "workflows" / "merge-train.yml"
        cls.workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        cls.triggers = cls.workflow[True]

    def test_runs_on_events_not_only_on_the_schedule(self):
        """Approval, ready, push and close events advance the queue at once."""
        self.assertLessEqual(
            {"push", "schedule", "pull_request_target", "workflow_dispatch"},
            set(self.triggers),
        )
        self.assertEqual(self.triggers["workflow_run"]["types"], ["completed"])
        self.assertEqual(
            set(self.triggers["workflow_run"]["workflows"]),
            {"App Tests", "Lint", "Migration Order"},
        )
        gate = self.workflow["jobs"]["merge-train"]["if"]
        self.assertIn("github.event.workflow_run.event == 'pull_request'", gate)
        self.assertIn('"failure"', gate)
        self.assertNotIn('"success"', gate)
        self.assertIn(
            "auto_merge_enabled", self.triggers["pull_request_target"]["types"]
        )

    def test_never_checks_out_pull_request_code(self):
        """pull_request_target has secrets, so only latest's own code may run."""
        steps = self.workflow["jobs"]["merge-train"]["steps"]
        for step in steps:
            if str(step.get("uses", "")).startswith("actions/checkout"):
                self.assertEqual(step.get("with", {}).get("ref"), "latest")

class BatchTests(TestCase):
    def test_migrations_never_enter_batch_even_on_rename(self):
        self.assertFalse(train._batch_safe_files([
            {"filename": "src/app/migrations/0207_one.py"},
        ], 100))
        self.assertFalse(train._batch_safe_files([
            {"filename": "elsewhere.py", "previous_filename": "src/users/migrations/0009_old.py"},
        ], 100))
        self.assertFalse(train._batch_safe_files([
            {"filename": ".github/workflows/app-tests.yml"},
        ], 100))
        self.assertTrue(train._batch_safe_files([{"filename": "src/app/views.py"}], 100))
        self.assertFalse(train._batch_safe_files([{"filename": "file.py"}] * 100, 100))

    def test_batch_requires_green_approved_owner_pr(self):
        p = pr(1, checks=passed())
        p["author"] = {"login": "owner"}
        p["headRefName"] = "feature"
        self.assertTrue(train._batch_eligible(p, "owner", {1: 3}))
        self.assertFalse(train._batch_eligible(p, "owner", {1: 0}))
        p["isCrossRepository"] = True
        self.assertFalse(train._batch_eligible(p, "owner", {1: 3}))

    def test_batch_identity_and_invalidation(self):
        a, b = pr(1), pr(2)
        for p, sha in ((a, "b" * 40), (b, "c" * 40)):
            p["author"] = {"login": "owner"}
            p["headRefOid"] = sha
        data = train._batch_manifest(LATEST_SHA, [a, b])
        candidate = pr(99)
        candidate["author"] = {"login": "owner"}
        candidate["headRefName"] = train._batch_branch(data)
        candidate["body"] = "<!-- merge-train-batch: " + json.dumps(data) + " -->"
        self.assertEqual(train._read_batch(candidate, "owner"), data)
        self.assertTrue(train._batch_valid(data, [a, b], LATEST_SHA, "owner"))
        self.assertFalse(train._batch_valid(data, [a, b], "d" * 40, "owner"))
        b["autoMergeRequest"] = None
        self.assertFalse(train._batch_valid(data, [a, b], LATEST_SHA, "owner"))

    def test_protected_ruleset_required(self):
        rule = {
            "enforcement": "active",
            "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
            "rules": [{"type": "required_status_checks", "parameters": {
                "strict_required_status_checks_policy": True,
                "required_status_checks": [
                    {"context": name, "integration_id": 15368} for name in train.REQUIRED_CHECKS
                ],
            }}],
        }
        with patch.object(train, "_batch_api", return_value=rule):
            self.assertTrue(train._batch_policy("o/r"))
            rule["rules"][0]["parameters"]["strict_required_status_checks_policy"] = False
            self.assertFalse(train._batch_policy("o/r"))
