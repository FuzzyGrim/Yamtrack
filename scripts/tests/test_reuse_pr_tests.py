"""Tests for scripts/reuse_pr_tests.py: only an identical, green tree is reused.

The failure that matters is a "reuse" for a tree no pull request run tested.
The baseline fixture has the shapes of a real merge (pull request 1910, run
43029064816, recorded 2026-11-11); every other case changes one fact and must
come back "run".

Run: .venv/bin/python -m unittest scripts/tests/test_reuse_pr_tests.py
"""

import copy
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from datetime import UTC, datetime, timedelta
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "reuse_pr_tests.py"
spec = importlib.util.spec_from_file_location("reuse_pr_tests", SCRIPT)
reuse = importlib.util.module_from_spec(spec)
sys.modules["reuse_pr_tests"] = reuse
spec.loader.exec_module(reuse)

REPO = "dannyvfilms/Floppy"
PUSHED = "39cf9669725a891f51bfe7391044243f89f5f1e1"
BASE_BEFORE = "8ddc03ea4099fd61c6ca095a6ff3b023bace6aac"
HEAD = "a4eb6cc19f05ed6db972720c671a42e2e8f5960b"
TESTED = "17e52ba6a306fb0ccf320af6c2d7e628f867d076"
TREE = "15870eaec206e47747f6e894a47f703199622c1f"
OTHER_TREE = "29852ccb3200bb792190194f3305067039154234"
OTHER_SHA = "f33feb5682879467f27b84f0a122877efcc9356a"
NUMBER = 1910
RUN_ID = 43029064816
NOW = datetime(2026, 11, 11, 13, 50, tzinfo=UTC)


def run_fixture(**changes):
    run = {
        "id": RUN_ID,
        "run_number": 4821,
        "run_attempt": 1,
        "event": "pull_request",
        "status": "completed",
        "conclusion": "success",
        "path": ".github/workflows/app-tests.yml",
        "head_sha": HEAD,
        "run_started_at": "2026-11-11T13:25:57Z",
        "pull_requests": [],
        "repository": {"full_name": REPO},
        "referenced_workflows": [
            {
                "path": f"{REPO}/.github/workflows/lint.yml@{TESTED}",
                "sha": TESTED,
                "ref": f"refs/pull/{NUMBER}/merge",
            }
        ],
    }
    run.update(changes)
    return run


def job_fixture(name, conclusion="success", attempt=1, status="completed"):
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "run_attempt": attempt,
    }


class World:
    """A fake GitHub API and checkout, green and reusable until a test edits it."""

    def __init__(self):
        self.env = {
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_REF": "refs/heads/latest",
            "GITHUB_REPOSITORY": REPO,
            "GITHUB_SHA": PUSHED,
            "REUSE_PR_TEST_RESULTS": "true",
        }
        self.head_commit = PUSHED
        self.pushed_tree = TREE
        self.associated = [{"number": NUMBER}]
        self.pull = {
            "number": NUMBER,
            "merged": True,
            "merge_commit_sha": PUSHED,
            "base": {"ref": "latest", "repo": {"full_name": REPO}},
            "head": {"sha": HEAD},
        }
        self.runs = [run_fixture()]
        self.total_runs = None
        self.commits = {
            TESTED: {
                "sha": TESTED,
                "tree": {"sha": TREE},
                "parents": [{"sha": BASE_BEFORE}, {"sha": HEAD}],
            }
        }
        self.jobs = [
            job_fixture("test (app, 3.12)"),
            job_fixture("test (integrations, 3.12)"),
            job_fixture("test (other, 3.12)"),
            job_fixture("OAuth (PostgreSQL)"),
            job_fixture("release lint", conclusion="skipped"),
            job_fixture("test (3.12)"),
        ]
        self.run_after = None
        self.fail = {}
        self.calls = []

    def api(self, path, params):
        self.calls.append((path, dict(params)))
        for fragment, error in self.fail.items():
            if fragment in path:
                raise error
        prefix = f"/repos/{REPO}"
        if path == f"{prefix}/commits/{self.env['GITHUB_SHA']}/pulls":
            return copy.deepcopy(self.associated)
        if path == f"{prefix}/pulls/{NUMBER}":
            return copy.deepcopy(self.pull)
        if path == f"{prefix}/actions/workflows/app-tests.yml/runs":
            assert params["event"] == "pull_request"
            runs = [run for run in self.runs if run["head_sha"] == params["head_sha"]]
            total = len(runs) if self.total_runs is None else self.total_runs
            return {"total_count": total, "workflow_runs": copy.deepcopy(runs)}
        if path.startswith(f"{prefix}/git/commits/"):
            return copy.deepcopy(self.commits[path.rsplit("/", 1)[1]])
        if path.endswith("/jobs"):
            assert params["filter"] == "latest"
            return {"total_count": len(self.jobs), "jobs": copy.deepcopy(self.jobs)}
        if path.startswith(f"{prefix}/actions/runs/"):
            run_id = int(path.rsplit("/", 1)[1])
            if self.run_after is not None:
                return copy.deepcopy(self.run_after)
            return copy.deepcopy(next(r for r in self.runs if r["id"] == run_id))
        raise AssertionError(f"unexpected API path {path}")

    def git(self, *args):
        if args == ("rev-parse", "HEAD"):
            return self.head_commit
        if args == ("rev-parse", f"{self.env['GITHUB_SHA']}^{{tree}}"):
            return self.pushed_tree
        raise AssertionError(f"unexpected git {args}")

    def decide(self, now=NOW):
        return reuse.decide(self.env, self.api, self.git, now)


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.world = World()

    def assert_reuse(self):
        decision = self.world.decide()
        self.assertEqual(decision.evaluated, "reuse", decision.reason)
        self.assertEqual(decision.decision, "reuse")
        return decision

    def assert_run(self, reason_fragment):
        decision = self.world.decide()
        self.assertEqual(decision.evaluated, "run")
        self.assertEqual(decision.decision, "run")
        self.assertIn(reason_fragment, decision.reason)
        return decision

    # --- the cases that may reuse -------------------------------------

    def test_up_to_date_green_pull_request_merged_by_merge_commit_reuses(self):
        decision = self.assert_reuse()
        self.assertEqual(decision.evidence["pull request"], "#1910")
        self.assertEqual(decision.evidence["pushed tree"], TREE)
        self.assertEqual(decision.evidence["tested tree"], TREE)
        self.assertEqual(decision.evidence["tested commit"], TESTED)
        self.assertIn(str(RUN_ID), decision.evidence["run"])

    def test_squash_merge_with_the_same_tree_reuses(self):
        # A squash commit has one parent and is not the pull request head. The
        # decision never looks at the pushed commit's parents, only its tree.
        self.assert_reuse()
        self.assertFalse(
            any("parents" in str(call) for call in self.world.calls),
        )

    def test_head_moved_after_the_green_run_reuses_only_the_same_tree(self):
        # The newest run for the final head is what counts. An older head's run
        # is never looked up, so a moved head reuses only if its own run tested
        # the pushed tree.
        self.world.runs.append(run_fixture(id=1, run_number=1, head_sha=OTHER_SHA))
        self.assert_reuse()

    def test_rerun_uses_the_latest_attempt(self):
        self.world.runs = [run_fixture(run_attempt=2)]
        self.world.jobs[-1] = job_fixture("test (3.12)", attempt=2)
        self.assert_reuse()

    # --- not applicable -----------------------------------------------

    def test_tag_push_is_not_applicable(self):
        self.world.env["GITHUB_REF"] = "refs/tags/v26.11.11"
        self.assert_run("not applicable")
        self.assertEqual(self.world.calls, [])

    def test_release_branch_is_not_applicable(self):
        self.world.env["GITHUB_REF"] = "refs/heads/release"
        self.assert_run("not applicable")
        self.assertEqual(self.world.calls, [])

    def test_other_events_are_not_applicable(self):
        for event in ("workflow_dispatch", "pull_request", "schedule", ""):
            with self.subTest(event=event):
                self.world.env["GITHUB_EVENT_NAME"] = event
                self.assert_run("not applicable")
        self.assertEqual(self.world.calls, [])

    # --- the pushed commit and its pull request -----------------------

    def test_direct_push_does_not_reuse(self):
        self.world.associated = []
        self.assert_run("no pull request")

    def test_two_pull_requests_for_one_commit_do_not_reuse(self):
        self.world.associated = [{"number": NUMBER}, {"number": 1911}]
        self.assert_run("2 pull requests")

    def test_unmerged_pull_request_does_not_reuse(self):
        # A direct push of a commit that an open pull request also contains.
        self.world.pull["merged"] = False
        self.assert_run("not merged")

    def test_pull_request_into_another_branch_does_not_reuse(self):
        self.world.pull["base"]["ref"] = "release"
        self.assert_run("targets 'release'")

    def test_pull_request_into_another_repository_does_not_reuse(self):
        self.world.pull["base"]["repo"]["full_name"] = "someone/Floppy"
        self.assert_run("another repository")

    def test_commit_that_is_not_the_merge_commit_does_not_reuse(self):
        # A later direct push that merely contains the pull request's commits.
        self.world.pull["merge_commit_sha"] = OTHER_SHA
        self.assert_run("merged as another commit")

    def test_checkout_that_is_not_the_pushed_commit_does_not_reuse(self):
        self.world.head_commit = OTHER_SHA
        self.assert_run("checkout is not the pushed commit")

    # --- tree identity ------------------------------------------------

    def test_merged_while_behind_latest_does_not_reuse(self):
        # Bypass merge of a branch that latest had moved past: the run tested
        # the head merged into the old latest, the push is the head merged into
        # the new latest.
        self.world.pushed_tree = OTHER_TREE
        decision = self.assert_run("tested tree differs")
        self.assertEqual(decision.evidence["pushed tree"], OTHER_TREE)
        self.assertEqual(decision.evidence["tested tree"], TREE)

    def test_run_on_a_stale_base_does_not_reuse_even_when_head_tree_matches(self):
        # The pull request head's tree equals the pushed tree, but the run
        # executed the head merged into a base it was behind. Comparing with
        # the head would wrongly reuse; the tested commit's tree does not match.
        self.world.commits[TESTED]["tree"]["sha"] = OTHER_TREE
        self.assert_run("tested tree differs")

    def test_tested_commit_that_does_not_merge_the_head_does_not_reuse(self):
        self.world.commits[TESTED]["parents"][1]["sha"] = OTHER_SHA
        self.assert_run("does not merge the pull request head")

    def test_tested_commit_that_is_not_a_merge_does_not_reuse(self):
        self.world.commits[TESTED]["parents"] = [{"sha": HEAD}]
        self.assert_run("not a two-parent merge")

    def test_run_for_another_pull_request_with_the_same_head_does_not_reuse(self):
        # Two pull requests can share a head commit and target different bases.
        self.world.runs[0]["referenced_workflows"][0]["ref"] = "refs/pull/1911/merge"
        self.assert_run("refs/pull/1911/merge")

    def test_run_that_does_not_report_its_tested_commit_does_not_reuse(self):
        for references in (
            [],
            None,
            [
                {
                    "path": "other/repo/.github/workflows/x.yml@v1",
                    "sha": TESTED,
                    "ref": f"refs/pull/{NUMBER}/merge",
                }
            ],
        ):
            with self.subTest(references=references):
                self.world.runs[0]["referenced_workflows"] = references
                self.assert_run("does not report the commit it tested")

    def test_run_reporting_two_tested_commits_does_not_reuse(self):
        self.world.runs[0]["referenced_workflows"].append(
            {
                "path": f"{REPO}/.github/workflows/other.yml@{OTHER_SHA}",
                "sha": OTHER_SHA,
                "ref": f"refs/pull/{NUMBER}/merge",
            }
        )
        self.assert_run("more than one tested commit")

    def test_workflow_referenced_by_a_chosen_ref_does_not_reuse(self):
        # "uses: owner/repo/.github/workflows/x.yml@<ref>" lets a workflow name
        # a commit of its choosing. Only the local form is accepted.
        self.world.runs[0]["referenced_workflows"][0]["path"] = (
            f"{REPO}/.github/workflows/lint.yml@refs/heads/latest"
        )
        self.assert_run("something other than its tested commit")

    # --- the run and its result ---------------------------------------

    def test_failed_run_merged_by_bypass_does_not_reuse(self):
        self.world.runs[0]["conclusion"] = "failure"
        self.assert_run("concluded failure")

    def test_cancelled_skipped_and_neutral_runs_do_not_reuse(self):
        for conclusion in ("cancelled", "skipped", "neutral", "timed_out", None):
            with self.subTest(conclusion=conclusion):
                self.world.runs[0]["conclusion"] = conclusion
                self.assert_run("concluded")

    def test_run_still_in_progress_does_not_reuse(self):
        self.world.runs[0].update(status="in_progress", conclusion=None)
        self.assert_run("is in_progress")

    def test_docs_only_pull_request_without_a_run_does_not_reuse(self):
        self.world.runs = []
        self.assert_run("no App Tests run")

    def test_newer_failed_run_outvotes_an_older_green_one(self):
        self.world.runs.append(
            run_fixture(id=RUN_ID + 1, run_number=4822, conclusion="failure")
        )
        self.assert_run("concluded failure")

    def test_newer_run_for_another_pull_request_blocks_reuse(self):
        newer = run_fixture(id=RUN_ID + 1, run_number=4822)
        newer["referenced_workflows"][0]["ref"] = "refs/pull/1911/merge"
        self.world.runs.append(newer)
        self.assert_run("refs/pull/1911/merge")

    def test_more_runs_than_one_page_does_not_reuse(self):
        self.world.total_runs = 101
        self.assert_run("too many App Tests runs")

    def test_run_of_another_workflow_event_repository_or_head_does_not_reuse(self):
        cases = {
            "path": (".github/workflows/lint.yml", "another workflow"),
            "event": ("push", "not a pull_request run"),
            "repository": ({"full_name": "someone/Floppy"}, "another repository"),
        }
        for key, (value, reason) in cases.items():
            with self.subTest(key=key):
                self.world.runs = [run_fixture(**{key: value})]
                self.assert_run(reason)

    def test_aggregate_job_must_have_succeeded(self):
        for conclusion in ("failure", "skipped", "cancelled", None):
            with self.subTest(conclusion=conclusion):
                self.world.jobs[-1] = job_fixture("test (3.12)", conclusion=conclusion)
                self.assert_run("'test (3.12)'")

    def test_missing_or_duplicated_aggregate_job_does_not_reuse(self):
        self.world.jobs.pop()
        self.assert_run("0 jobs named")
        self.world.jobs += [job_fixture("test (3.12)"), job_fixture("test (3.12)")]
        self.assert_run("2 jobs named")

    def test_aggregate_job_from_an_earlier_attempt_does_not_reuse(self):
        self.world.runs = [run_fixture(run_attempt=2)]
        self.assert_run("not from the latest attempt")

    def test_rerun_started_during_inspection_does_not_reuse(self):
        self.world.run_after = run_fixture(
            status="in_progress", conclusion=None, run_attempt=2
        )
        self.assert_run("changed while it was being inspected")

    # --- age ----------------------------------------------------------

    def test_run_at_the_age_limit_reuses_and_just_past_it_does_not(self):
        started = datetime(2026, 11, 11, 13, 25, 57, tzinfo=UTC)
        decision = self.world.decide(now=started + reuse.MAX_AGE)
        self.assertEqual(decision.decision, "reuse", decision.reason)
        decision = self.world.decide(now=started + reuse.MAX_AGE + timedelta(seconds=1))
        self.assertEqual(decision.decision, "run")
        self.assertIn("older than 72 hours", decision.reason)

    def test_run_from_the_future_or_without_a_start_does_not_reuse(self):
        decision = self.world.decide(now=NOW - timedelta(hours=1))
        self.assertEqual(decision.decision, "run")
        self.assertIn("in the future", decision.reason)
        for value in (None, "", "yesterday", "2026-11-11T13:25:57+00:00"):
            with self.subTest(value=value):
                self.world.runs = [run_fixture(run_started_at=value)]
                self.assertEqual(self.world.decide().decision, "run")

    # --- failures of the API itself -----------------------------------

    def test_every_api_failure_runs_the_gate(self):
        errors = [
            urllib.error.HTTPError("u", 500, "Server Error", {}, None),
            urllib.error.HTTPError("u", 403, "rate limit exceeded", {}, None),
            urllib.error.HTTPError("u", 404, "Not Found", {}, None),
            urllib.error.URLError("timed out"),
            TimeoutError("timed out"),
            ValueError("not JSON"),
        ]
        fragments = [
            "/pulls",
            "/pulls/1910",
            "/actions/workflows/",
            "/git/commits/",
            "/jobs",
            f"/actions/runs/{RUN_ID}",
        ]
        for fragment in fragments:
            for error in errors:
                with self.subTest(fragment=fragment, error=error):
                    self.world = World()
                    self.world.fail = {fragment: error}
                    decision = self.assert_run("error")
                    self.assertEqual(decision.decision, "run")

    def test_malformed_responses_run_the_gate(self):
        edits = {
            "associated is a dict": lambda w: setattr(
                w, "associated", {"number": NUMBER}
            ),
            "pull is a list": lambda w: setattr(w, "pull", []),
            "pull has no head": lambda w: w.pull.pop("head"),
            "pull number is a string": lambda w: setattr(
                w, "associated", [{"number": "1910"}]
            ),
            "merged is a string": lambda w: w.pull.update(merged="true"),
            "commit has no tree": lambda w: w.commits[TESTED].pop("tree"),
            "commit sha differs": lambda w: w.commits[TESTED].update(sha=OTHER_SHA),
            "tested sha is short": lambda w: w.runs[0]["referenced_workflows"][
                0
            ].update(
                sha=TESTED[:12], path=f"{REPO}/.github/workflows/lint.yml@{TESTED[:12]}"
            ),
            "run has no attempt": lambda w: w.runs[0].pop("run_attempt"),
            "runs are unordered": lambda w: w.runs.append(run_fixture(id=2)),
            "git has no tree": lambda w: setattr(w, "pushed_tree", ""),
        }
        for name, edit in edits.items():
            with self.subTest(name=name):
                self.world = World()
                edit(self.world)
                self.assertEqual(self.world.decide().decision, "run")

    def test_missing_api_runs_the_gate(self):
        decision = reuse.decide(self.world.env, None, self.world.git, NOW)
        self.assertEqual(decision.decision, "run")
        self.assertIn("error", decision.reason)

    # --- the switch ---------------------------------------------------

    def test_shadow_mode_reports_reuse_but_decides_run(self):
        for value in (None, "", "false", "True", "1", "yes"):
            with self.subTest(value=value):
                self.world.env.pop("REUSE_PR_TEST_RESULTS", None)
                if value is not None:
                    self.world.env["REUSE_PR_TEST_RESULTS"] = value
                decision = self.world.decide()
                self.assertEqual(decision.evaluated, "reuse")
                self.assertEqual(decision.decision, "run")
                self.assertIn("shadow mode", reuse.summary(decision))

    def test_summary_carries_the_evidence_and_cannot_be_broken_by_a_reason(self):
        text = reuse.summary(self.assert_reuse())
        for value in ("#1910", str(RUN_ID), TREE, TESTED, PUSHED):
            self.assertIn(value, text)
        hostile = reuse.Decision("run", "a\nb | c\r\ndecision=reuse", enabled=True)
        self.assertNotIn("\ndecision=reuse", reuse.summary(hostile))
        self.assertEqual(reuse.one_line("a\nb|c"), "a b c")

    # --- the warning when GitHub stops reporting the tested commit ----

    def test_missing_tested_commit_on_a_merge_raises_a_warning(self):
        self.world.runs[0]["referenced_workflows"] = []
        alert = reuse.warning(self.world.decide())
        self.assertIsNotNone(alert)
        self.assertTrue(alert.startswith("::warning "))
        self.assertIn("does not report the commit it tested", alert)
        self.assertNotIn("\n", alert)

    def test_ordinary_reasons_not_to_reuse_raise_no_warning(self):
        cases = {
            "reused": lambda w: None,
            "direct push": lambda w: setattr(w, "associated", []),
            "tag": lambda w: w.env.update(GITHUB_REF="refs/tags/v1"),
            "behind": lambda w: setattr(w, "pushed_tree", OTHER_TREE),
            "failed run": lambda w: w.runs[0].update(conclusion="failure"),
            "running": lambda w: w.runs[0].update(status="in_progress"),
        }
        for name, change in cases.items():
            with self.subTest(name):
                world = World()
                change(world)
                self.assertIsNone(reuse.warning(world.decide()))


class CommandTests(unittest.TestCase):
    """The script as the workflow runs it: it always exits 0 and says "run"."""

    def _run(self, **env):
        tmp = Path(tempfile.mkdtemp(prefix="reuse-pr-tests-"))
        self.addCleanup(lambda: [p.unlink() for p in tmp.iterdir()] and tmp.rmdir())
        output, summary = tmp / "output", tmp / "summary"
        output.touch()
        summary.touch()
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "GITHUB_OUTPUT": str(output),
                "GITHUB_STEP_SUMMARY": str(summary),
                **env,
            },
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        return result, output.read_text(), summary.read_text()

    def test_pull_request_event_writes_run_without_calling_the_api(self):
        result, output, summary = self._run(
            GITHUB_EVENT_NAME="pull_request",
            GITHUB_REF="refs/pull/1/merge",
            REUSE_PR_TEST_RESULTS="true",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("decision=run\n", output)
        self.assertNotIn("decision=reuse", output)
        self.assertIn("not applicable", summary)

    def test_unreachable_api_writes_run_and_exits_zero(self):
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=SCRIPT.parent,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        if not head:
            self.skipTest("not a git checkout")
        result, output, _ = self._run(
            GITHUB_EVENT_NAME="push",
            GITHUB_REF="refs/heads/latest",
            GITHUB_REPOSITORY=REPO,
            GITHUB_SHA=head,
            GITHUB_API_URL="https://127.0.0.1:1",
            REUSE_PR_TEST_RESULTS="true",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("decision=run\n", output)
        self.assertNotIn("decision=reuse", output)

    def test_insecure_api_url_writes_run(self):
        result, output, _ = self._run(
            GITHUB_EVENT_NAME="push",
            GITHUB_REF="refs/heads/latest",
            GITHUB_REPOSITORY=REPO,
            GITHUB_SHA=PUSHED,
            GITHUB_API_URL="http://example.invalid",
            REUSE_PR_TEST_RESULTS="true",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("decision=run\n", output)


if __name__ == "__main__":
    unittest.main()
