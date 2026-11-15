"""Tests for scripts/merge_train.py, the queue that keeps approved PRs up to date.

``scripts/`` is not a package, so the script is loaded by path, as in
test_migration_order.
"""

import importlib.util
import json
import re
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
            with self.subTest(head=head), patch.dict("os.environ", {"CI_HEAD": head}):
                code, fake = self.run_train([pr(1), pr(2, enabled=None)], {1: 2})
                self.assertEqual((code, fake.updated), (0, []))

    def test_ci_failure_on_current_head_advances_next_pr(self):
        done = _iso(timedelta(minutes=5))
        failed = pr(1, checks=[("test (3.12)", "COMPLETED", "FAILURE", done, done)])
        with patch.dict("os.environ", {"CI_HEAD": "sha1"}):
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
        self.assertNotIn("conclusion", gate)
        self.assertIn(
            "auto_merge_enabled", self.triggers["pull_request_target"]["types"]
        )

    def test_never_checks_out_pull_request_code(self):
        """pull_request_target has secrets, so only latest's own code may run."""
        steps = self.workflow["jobs"]["merge-train"]["steps"]
        for step in steps:
            if str(step.get("uses", "")).startswith("actions/checkout"):
                self.assertEqual(step.get("with", {}).get("ref"), "latest")



def _sha(char):
    return char * 40


def source(number, sha, fork=True, **kw):
    """A green approved source PR that is behind latest."""
    p = pr(number, checks=passed(), fork=fork, **kw)
    p.update(headRefOid=sha, headRefName="feature", author={"login": "someone"}, id=f"id{number}")
    return p


class BatchTests(TestCase):
    """Eligibility, identity and safety rules that need no git."""

    def test_migrations_ci_and_policy_files_never_enter_the_lane(self):
        safe = train._batch_safe_files
        self.assertFalse(safe(["src/app/migrations/0207_one.py"]))
        self.assertFalse(safe(["src/app/migrations/README.md"]))  # non-numbered
        self.assertFalse(safe(["src/users/migrations/0009_old.py", "elsewhere.py"]))  # rename source
        self.assertFalse(safe([".github/workflows/app-tests.yml"]))
        self.assertFalse(safe(["scripts/reuse_pr_tests.py"]))
        self.assertFalse(safe(["scripts/merge_train.py"]))
        self.assertTrue(safe(["src/app/views.py", "src/app/tests/migrations_notes.py"]))

    def test_owner_and_fork_are_equally_eligible(self):
        for fork, editable in ((False, False), (True, True), (True, False)):
            p = source(1, _sha("b"), fork=fork, editable=editable)
            self.assertTrue(train._batch_eligible(p, {1: 3}), (fork, editable))

    def test_ineligible_sources(self):
        base = source(1, _sha("b"))
        cases = {
            "draft": {"isDraft": True},
            "unapproved": {"autoMergeRequest": None},
            "conflicting": {"mergeable": "CONFLICTING"},
            "unknown": {"mergeable": "UNKNOWN"},
            "red": {"commits": pr(1, checks=running())["commits"]},
        }
        for label, change in cases.items():
            with self.subTest(label):
                self.assertFalse(train._batch_eligible({**base, **change}, {1: 3}))
        self.assertFalse(train._batch_eligible(base, {1: 0}))  # current: merges itself
        self.assertFalse(train._batch_eligible(base, {}))  # behind unknown

    def test_fork_branch_named_like_a_candidate_is_an_ordinary_source(self):
        p = source(1, _sha("b"))
        p["headRefName"] = train.BATCH_PREFIX + "evil"
        self.assertFalse(train._is_batch(p))
        self.assertTrue(train._batch_eligible(p, {1: 3}))

    def test_candidate_identity_requires_matching_manifest_head_and_author(self):
        a, b = source(1, _sha("b")), source(2, _sha("c"))
        data = {**train._batch_manifest(LATEST_SHA, [a, b]), "head": _sha("d"), "tree": _sha("e")}
        cand = pr(99, fork=False)
        cand.update(author={"login": "owner"}, headRefName=train._batch_branch(data),
                    headRefOid=_sha("d"), body="<!-- merge-train-batch: " + json.dumps(data) + " -->")
        self.assertEqual(train._read_batch(cand, "owner"), data)
        self.assertIsNone(train._read_batch({**cand, "author": {"login": "x"}}, "owner"))
        self.assertIsNone(train._read_batch({**cand, "headRefOid": _sha("f")}, "owner"))
        self.assertIsNone(train._read_batch({**cand, "isCrossRepository": True}, "owner"))
        self.assertIsNone(train._read_batch({**cand, "headRefName": train.BATCH_PREFIX + "x"}, "owner"))

    def test_candidate_invalidated_by_base_source_push_or_revocation(self):
        a, b = source(1, _sha("b")), source(2, _sha("c"))
        data = train._batch_manifest(LATEST_SHA, [a, b])
        self.assertTrue(train._batch_valid(data, [a, b], LATEST_SHA))
        self.assertFalse(train._batch_valid(data, [a, b], _sha("d")))
        self.assertFalse(train._batch_valid(data, [a, {**b, "headRefOid": _sha("9")}], LATEST_SHA))
        self.assertFalse(train._batch_valid(data, [a, {**b, "autoMergeRequest": None}], LATEST_SHA))
        self.assertFalse(train._batch_valid(data, [a, {**b, "isDraft": True}], LATEST_SHA))
        self.assertFalse(train._batch_valid(data, [a], LATEST_SHA))

    def test_base_not_stable_while_a_current_pr_is_in_flight(self):
        inflight = pr(5, checks=running())
        self.assertIn("#5", train._base_stable([inflight], {5: 0}, NOW))
        red = pr(5, checks=[("test (3.12)", "COMPLETED", "FAILURE", _iso(), _iso())])
        self.assertIsNone(train._base_stable([red], {5: 0}, NOW))
        self.assertIsNone(train._base_stable([inflight], {5: 2}, NOW))  # stale: not merging

    def test_selection_is_fifo_and_not_gated_by_serial_choice(self):
        red = pr(1, enabled="2026-10-05T00:00:00Z", checks=[("test (3.12)", "COMPLETED", "FAILURE", _iso(), _iso())])
        a = source(3, _sha("b"), enabled="2026-10-05T03:00:00Z")
        b = source(2, _sha("c"), enabled="2026-10-05T02:00:00Z")
        picked = train._select([red, a, b], {1: 4, 2: 1, 3: 1}, [])
        self.assertEqual([p["number"] for p in picked], [2, 3])

    def test_failed_group_blocks_only_that_combination(self):
        failed = [frozenset({(1, "x"), (2, "y")})]
        self.assertTrue(train._failed_group({(1, "x"), (2, "y"), (3, "z")}, failed))
        self.assertFalse(train._failed_group({(1, "x"), (3, "z")}, failed))
        self.assertFalse(train._failed_group({(2, "y")}, failed))

    def test_policy_reasons(self):
        def rules(strict=True, ids=15368, names=None):
            return [{"type": "required_status_checks", "parameters": {
                "strict_required_status_checks_policy": strict,
                "required_status_checks": [{"context": n, "integration_id": ids}
                                           for n in (names or train.REQUIRED_CHECKS)],
            }}]
        with patch.object(train, "_api") as api:
            api.return_value = rules()
            self.assertIsNone(train._batch_policy("o/r"))
            for bad in (rules(strict=False), rules(ids=1), rules(names=["lint"]), []):
                api.return_value = bad
                self.assertIsNotNone(train._batch_policy("o/r"))
            api.side_effect = subprocess.CalledProcessError(1, "gh", "", "HTTP 403")
            self.assertIn("HTTP 403", train._batch_policy("o/r"))


class StageTests(TestCase):
    """_stage against throwaway local git repositories (no network, no GitHub)."""

    def setUp(self):
        import os
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        root = Path(self.tmp.name)
        (root / "o").mkdir()
        self.remote = root / "o" / "r.git"
        self.work = root / "work"
        self.g("init", "-q", "--bare", str(self.remote), cwd=root)
        self.g("init", "-q", str(self.work), cwd=root)
        self.write("README", "base\n")
        self.write("src/app/a.py", "a = 1\n")
        self.write("src/app/b.py", "b = 1\n")
        self.write("src/app/migrations/0001_initial.py", "# m\n")
        self.commit("base", "Owner <owner@example.com>")
        self.g("branch", "-M", "latest")
        self.g("push", "-q", str(self.remote), "latest:refs/heads/latest")
        self.latest = self.head()
        patcher = [
            patch.object(train, "GIT_BASE", str(root)),
            patch.object(train, "FILE_PROTOCOL", "always"),
            patch.dict("os.environ", {"GH_TOKEN": "secret-token"}),
        ]
        for p in patcher:
            p.start()
            self.addCleanup(p.stop)

    def g(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd or self.work, env=self.env,  # noqa: S603, S607
                              capture_output=True, text=True, check=True).stdout.strip()

    def write(self, path, text):
        f = self.work / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)

    def commit(self, msg, author="Contributor <c@example.com>"):
        self.g("add", "-A")
        self.g("-c", "user.name=T", "-c", "user.email=t@e.com", "commit", "-q",
               "-m", msg, "--author", author)

    def head(self):
        return self.g("rev-parse", "HEAD")

    def pull(self, number, files, author="Contributor <c@example.com>", delete=()):
        """Publish refs/pull/<n>/head built on the base, return its sha."""
        self.g("checkout", "-q", "--detach", self.latest)
        for path, text in files.items():
            self.write(path, text)
        for path in delete:
            (self.work / path).unlink()
        self.commit(f"pr {number}", author)
        sha = self.head()
        self.g("push", "-q", str(self.remote), f"{sha}:refs/pull/{number}/head")
        return sha

    def candidates(self, *specs):
        return [source(n, sha, fork=fork) for n, sha, fork in specs]

    def stage(self, cands, depth=3, failed=()):
        notes = []
        return train._stage("o/r", self.latest, cands, depth, list(failed), notes), notes

    def remote_refs(self):
        return self.g("for-each-ref", "--format=%(refname)", cwd=self.remote).split()

    def test_mixed_origin_members_merge_with_ancestry_and_authorship(self):
        a = self.pull(1, {"src/app/a.py": "a = 2\n"}, "Fork Dev <fork@example.com>")
        b = self.pull(2, {"src/app/b.py": "b = 2\n"}, "Owner Dev <own@example.com>")
        before = self.remote_refs()
        data, notes = self.stage(self.candidates((1, a, True), (2, b, False)))
        self.assertEqual([m["number"] for m in data["members"]], [1, 2], notes)
        for sha in (a, b, self.latest):
            self.g("merge-base", "--is-ancestor", sha, data["head"], cwd=self.remote)
        self.assertEqual(self.g("log", "-1", "--format=%an", a, cwd=self.remote), "Fork Dev")
        # Only the staging ref is new: sources and latest are untouched.
        self.assertEqual(set(self.remote_refs()) - set(before), {f"refs/heads/{train._batch_branch(data)}"})
        self.assertEqual(self.g("rev-parse", "refs/pull/1/head", cwd=self.remote), a)
        self.assertEqual(self.g("rev-parse", "refs/heads/latest", cwd=self.remote), self.latest)
        self.assertEqual(self.g("rev-parse", f"{data['head']}^{{tree}}", cwd=self.remote), data["tree"])

    def test_conflicting_member_is_left_out_but_others_still_batch(self):
        a = self.pull(1, {"src/app/a.py": "a = 2\n"})
        b = self.pull(2, {"src/app/a.py": "a = 3\n"})  # conflicts with #1
        c = self.pull(3, {"src/app/b.py": "b = 2\n"})
        data, notes = self.stage(self.candidates((1, a, True), (2, b, True), (3, c, True)))
        self.assertEqual([m["number"] for m in data["members"]], [1, 3])
        self.assertTrue(any("#2 not batched: conflicts" in n for n in notes))

    def test_migration_member_cannot_enter_the_fast_lane(self):
        a = self.pull(1, {"src/app/migrations/0002_x.py": "# m\n"})
        deleted = self.pull(2, {}, delete=["src/app/migrations/0001_initial.py"])
        ok1 = self.pull(3, {"src/app/a.py": "a = 5\n"})
        ok2 = self.pull(4, {"src/app/b.py": "b = 5\n"})
        data, notes = self.stage(self.candidates((1, a, True), (2, deleted, True), (3, ok1, True), (4, ok2, True)))
        self.assertEqual([m["number"] for m in data["members"]], [3, 4])
        self.assertEqual(sum("serial lane" in n for n in notes), 2)

    def test_renamed_migration_is_detected(self):
        self.g("checkout", "-q", "--detach", self.latest)
        self.g("mv", "src/app/migrations/0001_initial.py", "src/app/renamed.py")
        self.commit("rename")
        sha = self.head()
        self.g("push", "-q", str(self.remote), f"{sha}:refs/pull/1/head")
        other = self.pull(2, {"src/app/b.py": "b = 9\n"})
        data, _ = self.stage(self.candidates((1, sha, True), (2, other, True)))
        self.assertEqual([m["number"] for m in data["members"]], [2])

    def test_moved_head_is_not_staged(self):
        a = self.pull(1, {"src/app/a.py": "a = 2\n"})
        b = self.pull(2, {"src/app/b.py": "b = 2\n"})
        stale = self.candidates((1, _sha("9"), True), (2, b, True))
        data, notes = self.stage(stale)
        self.assertEqual([m["number"] for m in data["members"]], [2])
        self.assertTrue(any("#1 not batched: head moved" in n for n in notes))
        del a

    def test_single_fork_stages_alone_but_single_local_pr_goes_serial(self):
        a = self.pull(1, {"src/app/a.py": "a = 2\n"})
        data, _ = self.stage(self.candidates((1, a, True)))
        self.assertEqual([m["number"] for m in data["members"]], [1])
        data, notes = self.stage(self.candidates((1, a, False)))
        self.assertIsNone(data)
        self.assertTrue(any("serial update" in n for n in notes))

    def test_failed_manifest_is_not_restaged_but_members_still_go_alone(self):
        a = self.pull(1, {"src/app/a.py": "a = 2\n"})
        b = self.pull(2, {"src/app/b.py": "b = 2\n"})
        failed = [frozenset({(1, a), (2, b)})]
        data, _ = self.stage(self.candidates((1, a, True), (2, b, True)), failed=failed)
        self.assertEqual([m["number"] for m in data["members"]], [1])
        failed.append(frozenset({(1, a)}))
        data, _ = self.stage(self.candidates((1, a, True), (2, b, True)), failed=failed)
        self.assertEqual([m["number"] for m in data["members"]], [2])

    def test_base_movement_during_staging_is_detected(self):
        a = self.pull(1, {"src/app/a.py": "a = 2\n"})
        self.g("checkout", "-q", "--detach", self.latest)
        self.write("README", "moved\n")
        self.commit("move")
        self.g("push", "-q", str(self.remote), "HEAD:refs/heads/latest")
        data, notes = self.stage(self.candidates((1, a, True)))
        self.assertIsNone(data)
        self.assertIn("latest moved", notes[0])

    def test_orphan_staging_ref_is_reused_when_valid(self):
        a = self.pull(1, {"src/app/a.py": "a = 2\n"})
        first, _ = self.stage(self.candidates((1, a, True)))
        # A second run with the same manifest finds the ref, validates and reuses it.
        again, notes = self.stage(self.candidates((1, a, True)))
        self.assertEqual(again["head"], first["head"], notes)

    def test_orphan_staging_ref_with_wrong_content_is_rejected(self):
        a = self.pull(1, {"src/app/a.py": "a = 2\n"})
        first, _ = self.stage(self.candidates((1, a, True)))
        self.g("update-ref", "-d", f"refs/heads/{train._batch_branch(first)}", cwd=self.remote)
        self.g("checkout", "-q", "--detach", self.latest)
        self.write("src/app/a.py", "a = 666\n")
        self.commit("unrelated")
        self.g("push", "-q", str(self.remote), f"HEAD:refs/heads/{train._batch_branch(first)}")
        data, notes = self.stage(self.candidates((1, a, True)))
        self.assertIsNone(data)
        self.assertTrue(any("rejected" in n for n in notes), notes)

    def test_token_never_reaches_argv_or_errors(self):
        env = train._git_env("secret-token")
        self.assertNotIn("secret-token", " ".join(train._git(str(self.work), env, "status", "--short").split()))
        a = self.pull(1, {"src/app/a.py": "a = 2\n"})
        with patch.object(train, "GIT_BASE", "/nonexistent"), self.assertRaises(
            subprocess.CalledProcessError
        ) as caught:
            self.stage(self.candidates((1, a, True)))
        self.assertNotIn("secret-token", caught.exception.stderr)
        self.assertNotIn(env["GIT_CONFIG_VALUE_0"], caught.exception.stderr)


class ReconcileTests(TestCase):
    """The scheduler around the lane, with GitHub mocked and staging stubbed."""

    def setUp(self):
        self.env = patch.dict("os.environ", {"MERGE_TRAIN_BATCH": "true", "GH_TOKEN": "t"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.calls = []
        self.policy = patch.object(train, "_batch_policy", return_value=None)
        self.policy.start()
        self.addCleanup(self.policy.stop)

    def api(self, repo, endpoint, *args):
        self.calls.append((endpoint, args))
        if endpoint.startswith("pulls?state=all"):
            return []
        if endpoint.startswith("pulls?state=closed"):
            return []
        if endpoint == "pulls":
            return {"number": 900}
        return None

    def run_reconcile(self, prs, behind, stage=None, dry=False):
        with patch.object(train, "_api", self.api), \
             patch.object(train, "_fetch", return_value=(prs, LATEST_SHA)), \
             patch.object(train, "_stage", return_value=stage), \
             patch.object(train, "_report") as report:
            code = train._reconcile_batch("owner", "r", prs, LATEST_SHA, behind, NOW, dry)
        return code, report

    def manifest(self, *prs_):
        data = train._batch_manifest(LATEST_SHA, list(prs_))
        data.update(head=_sha("d"), tree=_sha("e"))
        return data

    def candidate(self, data, checks=None, state="CLEAN", auto=False):
        c = pr(900, checks=passed() if checks is None else checks, state=state, fork=False,
               enabled="2026-10-05T11:00:00Z" if auto else None)
        c.update(author={"login": "owner"}, headRefName=train._batch_branch(data),
                 headRefOid=data["head"], id="idc", createdAt=_iso(timedelta(minutes=20)),
                 body="<!-- merge-train-batch: " + json.dumps(data) + " -->")
        return c

    def test_two_forks_launch_one_candidate_without_any_owner_pr(self):
        a, b = source(1, _sha("b")), source(2, _sha("c"))
        data = self.manifest(a, b)
        code, _ = self.run_reconcile([a, b], {1: 3, 2: 3}, stage=data)
        self.assertEqual(code, 0)
        posted = [c for c in self.calls if c[0] == "pulls"]
        self.assertEqual(len(posted), 1)
        self.assertIn("merge-train-batch", " ".join(posted[0][1]))

    def test_in_flight_current_pr_defers_staging_and_falls_back_serial(self):
        a, b = source(1, _sha("b")), source(2, _sha("c"))
        flying = pr(3, checks=running())
        code, report = self.run_reconcile([a, b, flying], {1: 3, 2: 3, 3: 0}, stage=self.manifest(a, b))
        self.assertIsNone(code)
        self.assertIn("latest is not stable", " ".join(report.call_args[0][0]))
        self.assertFalse([c for c in self.calls if c[0] == "pulls"])

    def test_red_stale_original_does_not_block_unrelated_green_work(self):
        red = pr(1, enabled="2026-10-05T00:00:00Z",
                 checks=[("test (3.12)", "COMPLETED", "FAILURE", _iso(), _iso())])
        a, b = source(2, _sha("b")), source(3, _sha("c"))
        data = self.manifest(a, b)
        code, _ = self.run_reconcile([red, a, b], {1: 3, 2: 3, 3: 3}, stage=data)
        self.assertEqual(code, 0)

    def test_batching_disabled_retires_active_candidate(self):
        a, b = source(1, _sha("b")), source(2, _sha("c"))
        data = self.manifest(a, b)
        with patch.dict("os.environ", {"MERGE_TRAIN_BATCH": "false"}):
            code, _ = self.run_reconcile([a, b, self.candidate(data)], {1: 3, 2: 3})
        self.assertIsNone(code)
        self.assertTrue([c for c in self.calls if c[0] == "pulls/900" and "state=closed" in c[1]])

    def test_green_candidate_is_promoted_only_after_revalidation(self):
        a, b = source(1, _sha("b")), source(2, _sha("c"))
        data = self.manifest(a, b)
        cand = self.candidate(data)
        with patch.object(train, "_gh") as gh:
            code, report = self.run_reconcile([a, b, cand], {1: 3, 2: 3})
        self.assertEqual(code, 0)
        self.assertIn("enablePullRequestAutoMerge", gh.call_args[0][-1])
        self.assertIn(data["head"], gh.call_args[0][-1])

    def test_promotion_falls_back_to_pinned_merge_when_checks_already_complete(self):
        a, b = source(1, _sha("b")), source(2, _sha("c"))
        data = self.manifest(a, b)
        refused = subprocess.CalledProcessError(1, "gh", "", "Pull request is in clean status")
        with patch.object(train, "_gh", side_effect=refused):
            self.run_reconcile([a, b, self.candidate(data)], {1: 3, 2: 3})
        merge = [c for c in self.calls if c[0] == "pulls/900/merge"]
        self.assertEqual(len(merge), 1)
        self.assertIn(f"sha={data['head']}", merge[0][1])

    def test_revoked_source_prevents_promotion_and_retires_candidate(self):
        a, b = source(1, _sha("b")), source(2, _sha("c"))
        data = self.manifest(a, b)
        b["autoMergeRequest"] = None
        with patch.object(train, "_gh") as gh:
            self.run_reconcile([a, b, self.candidate(data)], {1: 3, 2: 3})
        gh.assert_not_called()
        self.assertTrue([c for c in self.calls if c[0] == "pulls/900" and "state=closed" in c[1]])

    def test_failed_candidate_is_tagged_so_the_group_is_not_retried(self):
        a, b = source(1, _sha("b")), source(2, _sha("c"))
        data = self.manifest(a, b)
        failing = self.candidate(data, checks=[("test (3.12)", "COMPLETED", "FAILURE", _iso(), _iso())])
        self.run_reconcile([a, b, failing], {1: 3, 2: 3})
        close = next(c for c in self.calls if c[0] == "pulls/900")
        self.assertIn(train.RESULT_MARKER, " ".join(close[1]))

    def test_candidate_waits_while_checks_run_and_base_moves_retire_it(self):
        a, b = source(1, _sha("b")), source(2, _sha("c"))
        data = self.manifest(a, b)
        code, _ = self.run_reconcile([a, b, self.candidate(data, checks=running(), state="BLOCKED")], {1: 3, 2: 3})
        self.assertEqual(code, 0)
        self.assertFalse([c for c in self.calls if c[0] == "pulls/900"])
        stale = {**data, "base": _sha("1")}
        stale["head"] = _sha("d")
        cand = self.candidate(stale)
        self.run_reconcile([a, b, cand], {1: 3, 2: 3})
        self.assertTrue([c for c in self.calls if c[0] == "pulls/900"])

    def test_timed_out_candidate_is_retired(self):
        a, b = source(1, _sha("b")), source(2, _sha("c"))
        data = self.manifest(a, b)
        cand = self.candidate(data, checks=running(), state="BLOCKED")
        cand["createdAt"] = _iso(timedelta(hours=3))
        self.run_reconcile([a, b, cand], {1: 3, 2: 3})
        self.assertTrue([c for c in self.calls if c[0] == "pulls/900"])

    def test_same_manifest_already_attempted_goes_serial(self):
        a, b = source(1, _sha("b")), source(2, _sha("c"))
        data = self.manifest(a, b)
        original = self.api
        self.api = lambda repo, e, *a_: [{"number": 1}] if e.startswith("pulls?state=all") else original(repo, e, *a_)
        code, _ = self.run_reconcile([a, b], {1: 3, 2: 3}, stage=data)
        self.assertIsNone(code)

    def test_dry_run_changes_nothing(self):
        a, b = source(1, _sha("b")), source(2, _sha("c"))
        code, report = self.run_reconcile([a, b], {1: 3, 2: 3}, stage=self.manifest(a, b), dry=True)
        self.assertEqual(code, 0)
        self.assertFalse([c for c in self.calls if c[0] == "pulls"])


class WorkflowSecurityTests(TestCase):
    """Evaluate what pull_request workflows may do; a candidate is a same-repo PR."""

    NOT_ON_PULL_REQUESTS = ("github.event_name == 'push'", "github.event_name != 'pull_request'", "refs/tags/")
    WRITE_OK = {"codeql.yml": {"analyze"}, "docker-image.yml": {"build"}}
    WORKFLOWS = _SCRIPT.parents[1] / ".github" / "workflows"

    def docs(self):
        for path in sorted(self.WORKFLOWS.glob("*.yml")):
            yield path.name, yaml.safe_load(path.read_text(encoding="utf-8"))

    @staticmethod
    def triggers(doc):
        on = doc.get(True, doc.get("on"))
        return set(on if isinstance(on, (list, dict)) else [on])

    def test_pull_request_jobs_have_no_write_scopes_unless_skipped_for_candidates(self):
        for name, doc in self.docs():
            if "pull_request" not in self.triggers(doc):
                continue
            self.assertIn("permissions", doc, f"{name} needs explicit top-level permissions")
            for job, spec in doc["jobs"].items():
                perms = spec.get("permissions", doc["permissions"])
                writes = {k for k, v in perms.items() if v == "write"} if isinstance(perms, dict) else {"all"}
                if not writes:
                    continue
                if any(x in str(spec.get("if")) for x in self.NOT_ON_PULL_REQUESTS):
                    continue  # never runs for a pull request
                self.assertIn(job, self.WRITE_OK.get(name, set()), f"{name}:{job} writes {writes}")
                self.assertIn("startsWith(github.head_ref, 'merge-train/batch-')", str(spec.get("if")),
                              f"{name}:{job} must skip merge-train candidates")

    def test_no_job_that_runs_candidate_code_reads_a_secret_or_oidc(self):
        text = {}
        for name, doc in self.docs():
            if "pull_request" not in self.triggers(doc):
                continue
            for job, spec in doc["jobs"].items():
                body = json.dumps(spec)
                if job in self.WRITE_OK.get(name, ()) or name == "owner-auto-merge.yml" or any(
                    x in str(spec.get("if")) for x in self.NOT_ON_PULL_REQUESTS
                ):
                    continue  # skipped for candidates, or never runs on a pull request
                for secret in set(re.findall(r"secrets\.([A-Z_]+)", body)):
                    text.setdefault(f"{name}:{job}", set()).add(secret)
                self.assertNotIn("id-token", body)
        self.assertEqual(text, {}, "candidate-visible jobs may not use repository secrets")

    def test_auto_merge_token_only_in_trusted_workflows(self):
        users = {n for n, d in self.docs() if "AUTO_MERGE_TOKEN" in json.dumps(d)}
        self.assertEqual(users, {"merge-train.yml", "owner-auto-merge.yml"})
        trusted = yaml.safe_load((self.WORKFLOWS / "merge-train.yml").read_text(encoding="utf-8"))
        self.assertTrue(all(
            s.get("with", {}).get("ref") == "latest"
            for s in trusted["jobs"]["merge-train"]["steps"] if "checkout" in str(s.get("uses", ""))
        ))
        owner = yaml.safe_load((self.WORKFLOWS / "owner-auto-merge.yml").read_text(encoding="utf-8"))
        job = owner["jobs"]["enable-auto-merge"]
        self.assertIn("!startsWith(github.event.pull_request.head.ref, 'merge-train/batch-')", job["if"])
        self.assertFalse([s for s in job["steps"] if "checkout" in str(s.get("uses", ""))])

    def test_push_workflows_do_not_run_on_staging_branches(self):
        for name, doc in self.docs():
            push = doc.get(True, {}).get("push") if isinstance(doc.get(True), dict) else None
            for branch in (push or {}).get("branches", []):
                self.assertFalse(train.BATCH_PREFIX.startswith(branch.rstrip("*")) and "*" in branch, name)
