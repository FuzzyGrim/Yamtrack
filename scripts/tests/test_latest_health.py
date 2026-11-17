"""Tests for scripts/latest_health.py: one issue per broken latest, never two.

The scenarios at the bottom are the failure injections from the plan, run
against a fake GitHub: a break opens one issue naming the test and commit, a
second push while broken updates it, the fix closes it, an older run finishing
late changes nothing, and an infrastructure failure opens nothing.

Run: .venv/bin/python -m unittest scripts/tests/test_latest_health.py
"""

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "latest_health.py"
spec = importlib.util.spec_from_file_location("latest_health", SCRIPT)
lh = importlib.util.module_from_spec(spec)
sys.modules["latest_health"] = lh
spec.loader.exec_module(lh)

# Shapes copied from real runs: the publish gate run 43671959036 on eed87a907
# (2026-11-15) and the pull request run 43646683095.
GATE_LOG_FAIL = """\
2026-11-15T03:52:10.1234567Z ======================================================================
2026-11-15T03:52:10.1234567Z FAIL: test_register_and_source_map_have_no_drift (config.tests.test_ui_docs.UiDocsTests.test_register_and_source_map_have_no_drift)
2026-11-15T03:52:10.1234567Z ----------------------------------------------------------------------
2026-11-15T03:52:10.1234567Z AssertionError: 'quick actions' missing from the register
2026-11-15T03:53:30.1234567Z FAILED (failures=1, skipped=12)
"""
BROWSER_TEST = "app.tests.test_integration.IntegrationTest.test_tv_completed"
BROWSER_LOG_FAIL = f"""\
2026-11-14T22:30:00.0000000Z ERROR: test_tv_completed ({BROWSER_TEST})
2026-11-14T22:30:00.0000000Z playwright._impl._errors.TimeoutError: Locator.click: Timeout 30000ms exceeded.
2026-11-14T22:31:00.0000000Z FAILED (errors=1)
"""

# The evidence table the "Reuse PR test result?" job prints.
REUSE_LOG = """\
2026-11-16T18:20:37.2747876Z **Decision: reuse.** The test shards are skipped.
2026-11-16T18:20:37.2753720Z | run | `43925435829 (attempt 1)` |
2026-11-16T18:20:37.2756554Z | run started | `{started}` |
2026-11-16T18:20:37.2757252Z decision=reuse
"""
BEFORE = "2000-01-01T00:00:00Z"
AFTER = "2999-01-01T00:00:00Z"


def job(name, conclusion="success", step=None, step_conclusion=None, job_id=1):
    steps = [{"name": "Set up job", "conclusion": "success"}]
    if step:
        steps.append({"name": step, "conclusion": step_conclusion or conclusion})
    return {"id": job_id, "name": name, "conclusion": conclusion, "steps": steps}


def browser_job(conclusion="success", step_conclusion=None):
    return job("Browser tests", conclusion, lh.BROWSER_STEP, step_conclusion, job_id=7)


class ParseTests(unittest.TestCase):
    def test_names_from_a_real_log(self):
        self.assertEqual(
            lh.parse_failures(GATE_LOG_FAIL),
            [
                "config.tests.test_ui_docs.UiDocsTests.test_register_and_source_map_have_no_drift"
            ],
        )

    def test_class_only_names_and_errors(self):
        log = "ERROR: setUpClass (app.tests.test_x.Browser)\nFAIL: test_a (app.tests.test_x.T)\n"
        self.assertEqual(
            lh.parse_failures(log),
            ["app.tests.test_x.Browser.setUpClass", "app.tests.test_x.T.test_a"],
        )

    def test_duplicates_collapse(self):
        self.assertEqual(lh.parse_failures(BROWSER_LOG_FAIL * 2), [BROWSER_TEST])


class ClassifyTests(unittest.TestCase):
    def classify(self, jobs, log="", source="browser"):
        if source == "browser":
            return lh.classify(jobs, lh.BROWSER_JOB, lh.BROWSER_STEP, lambda j: log)
        return lh.classify(
            jobs, lh.GATE_JOB, lh.GATE_STEP, lambda j: log, lh.GATE_ROLLUP
        )

    def test_pass(self):
        self.assertEqual(self.classify([browser_job()]).status, "pass")

    def test_named_failure(self):
        result = self.classify([browser_job("failure")], BROWSER_LOG_FAIL)
        self.assertEqual((result.status, result.tests), ("fail", [BROWSER_TEST]))

    def test_chromium_install_failure_is_unknown(self):
        broken = job("Browser tests", "failure", "Install dependencies and Chromium")
        broken["steps"].append({"name": lh.BROWSER_STEP, "conclusion": "skipped"})
        result = self.classify([broken])
        self.assertEqual(result.status, "unknown")
        self.assertIn("skipped", result.reason)

    def test_missing_chromium_is_unknown_even_with_test_names(self):
        # Seen locally: every browser test errors by name when Chromium is absent.
        log = BROWSER_LOG_FAIL + (
            "playwright._impl._errors.Error: BrowserType.launch: Executable doesn't exist at "
            "/home/runner/.cache/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell\n"
        )
        result = self.classify([browser_job("failure")], log)
        self.assertEqual(result.status, "unknown")
        self.assertIn("browser could not start", result.reason)

    def test_test_step_failed_without_names_is_unknown(self):
        result = self.classify([browser_job("failure")], "Killed by the watchdog\n")
        self.assertEqual(result.status, "unknown")

    def test_timed_out_job_is_unknown(self):
        self.assertEqual(
            self.classify([browser_job("cancelled", "cancelled")]).status, "unknown"
        )

    def test_log_read_error_is_unknown(self):
        def boom(_job):
            raise OSError("HTTP 410")

        result = lh.classify(
            [browser_job("failure")], lh.BROWSER_JOB, lh.BROWSER_STEP, boom
        )
        self.assertEqual(result.status, "unknown")

    def test_no_test_jobs_is_unknown(self):
        self.assertEqual(self.classify([job("Lint")]).status, "unknown")

    def test_gate_reads_only_shards(self):
        jobs = [
            job(
                "Release quality gates / test (app, 3.12)",
                "success",
                "Run Tests",
                job_id=1,
            ),
            job(
                "Release quality gates / test (other, 3.12)",
                "failure",
                "Run Tests",
                job_id=2,
            ),
            job("Release quality gates / test (3.12)", "failure", job_id=3),
            job("Release quality gates / release lint / lint", "failure", job_id=4),
            job("Build and push", "failure", job_id=5),
        ]
        result = self.classify(jobs, GATE_LOG_FAIL, "gate")
        self.assertEqual(result.status, "fail")
        self.assertEqual(len(result.tests), 1)

    def test_gate_reused_shards_pass(self):
        jobs = [
            job("Release quality gates / test (app, 3.12)", "skipped", job_id=1),
            job("Release quality gates / test (3.12)", "success", job_id=3),
        ]
        self.assertEqual(self.classify(jobs, source="gate").status, "pass")

    def test_gate_reuse_with_unexpanded_matrix_name_passes(self):
        # Run 43680572742 (1bf26c6b6): the reuse decision skipped the shards.
        jobs = [
            job(
                "Release quality gates / test (${{ matrix.suite }}, ${{ matrix.python-version }})",
                "skipped",
            ),
            job("Release quality gates / test (3.12)", "success", job_id=3),
            job("Release quality gates / Upgrade (postgres)", "failure", job_id=4),
        ]
        self.assertEqual(self.classify(jobs, source="gate").status, "pass")

    def test_gate_reuse_reads_when_the_reused_run_started(self):
        # Shape of run 43927493021 (e05b97d45, 2026-11-16).
        jobs = [
            job("Release quality gates / Reuse PR test result?", job_id=9),
            job(
                "Release quality gates / test (${{ matrix.suite }}, ${{ matrix.python-version }})",
                "skipped",
            ),
            job("Release quality gates / test (3.12)", "success", job_id=3),
        ]
        log = REUSE_LOG.format(started="2026-11-16T18:07:03Z")
        result = self.classify(jobs, log, source="gate")
        self.assertEqual(result.status, "pass")
        self.assertEqual(result.reused_started, "2026-11-16T18:07:03Z")
        # A real run is not a reuse.
        real = [job("Release quality gates / test (app, 3.12)", "success", "Run Tests")]
        self.assertIsNone(self.classify(real, source="gate").reused_started)

    def test_gate_without_shards_or_rollup_is_unknown(self):
        jobs = [job("Release quality gates / test (3.12)", "cancelled", job_id=3)]
        self.assertEqual(self.classify(jobs, source="gate").status, "unknown")

    def test_gate_rollup_red_with_green_shards_is_unknown(self):
        jobs = [
            job(
                "Release quality gates / test (app, 3.12)",
                "success",
                "Run Tests",
                job_id=1,
            ),
            job("Release quality gates / test (3.12)", "failure", job_id=3),
        ]
        self.assertEqual(self.classify(jobs, source="gate").status, "unknown")


class PrNoteTests(unittest.TestCase):
    def state(self, *tests):
        return {"sources": {"browser": {"status": "fail", "tests": list(tests)}}}

    def test_unreadable_is_unknown_never_green(self):
        text = lh.pr_note(["a.b"], None, "HTTP 502")
        self.assertIn("Unknown", text)
        self.assertNotIn("green", text.lower().replace("go green", ""))

    def test_all_shared(self):
        text = lh.pr_note(["a.b"], {"issue": 9, "state": self.state("a.b", "c.d")})
        self.assertIn("All 1 failing tests here also fail on `latest` (#9)", text)

    def test_some_shared(self):
        text = lh.pr_note(["a.b", "x.y"], {"issue": 9, "state": self.state("a.b")})
        self.assertIn("1 of 2", text)
        self.assertIn("`x.y`", text)

    def test_none_shared(self):
        text = lh.pr_note(["x.y"], {"issue": 9, "state": self.state("a.b")})
        self.assertIn("none of the 1", text)

    def test_no_issue_and_green_health(self):
        text = lh.pr_note(["x.y"], {"issue": None, "health_green": True})
        self.assertIn("probably from this branch", text)

    def test_no_issue_and_no_clean_health_is_unknown(self):
        text = lh.pr_note(["x.y"], {"issue": None, "health_green": False})
        self.assertTrue(text.startswith("Unknown"))


class FakeGitHub:
    """Issues, runs and commit order in memory. Commits are ordered by `line`."""

    repo = "owner/floppy"

    def __init__(self):
        self.issues_by_number = {}
        self.comments = []
        self.runs = {}
        self.line = ["good0", "good1"]
        self.successes = {lh.HEALTH_WORKFLOW: "good1", lh.GATE_WORKFLOW: "good1"}

    # Run fixtures
    def add_run(self, run_id, jobs, log=""):
        self.runs[run_id] = (jobs, log)

    def jobs(self, run_id):
        return self.runs[run_id][0]

    def log(self, job):
        for jobs, log in self.runs.values():
            if any(j is job for j in jobs):
                return log
        return ""

    # Issues
    def open_list(self):
        return [i for i in self.issues_by_number.values() if i["state"] == "open"]

    def open_issue(self):
        found = self.open_list()
        return min(found, key=lambda i: i["number"]) if found else None

    def closed_issue(self):
        closed = [i for i in self.issues_by_number.values() if i["state"] == "closed"]
        return max(closed, key=lambda i: i["number"]) if closed else None

    def latest_head(self):
        return self.line[-1]

    def create(self, body):
        number = len(self.issues_by_number) + 100
        self.issues_by_number[number] = {
            "number": number,
            "body": body,
            "state": "open",
        }
        return number

    def edit(self, number, body):
        self.issues_by_number[number]["body"] = body

    def comment(self, number, text):
        self.comments.append((number, text))

    def close(self, number):
        self.issues_by_number[number]["state"] = "closed"

    # Commits and runs
    def is_newer(self, recorded, sha):
        return self.line.index(recorded) > self.line.index(sha)

    def last_success(self, workflow):
        return self.successes.get(workflow)


class InjectionScenarios(unittest.TestCase):
    """The five failure injections, end to end through run_report."""

    def setUp(self):
        self.gh = FakeGitHub()
        self._summary = tempfile.NamedTemporaryFile(delete=False)
        self._summary.close()
        os.environ["GITHUB_STEP_SUMMARY"] = self._summary.name
        self.addCleanup(os.environ.pop, "GITHUB_STEP_SUMMARY", None)
        self.addCleanup(os.unlink, self._summary.name)

    def push(self, sha):
        self.gh.line.append(sha)

    def browser(self, run_id, sha, broken):
        self.gh.add_run(
            run_id,
            [browser_job("failure" if broken else "success")],
            BROWSER_LOG_FAIL if broken else "",
        )
        return lh.run_report(self.gh, "browser", run_id, sha)

    def gate(self, run_id, sha, broken=False, unknown=False, reused=None):
        if reused is not None:
            # The shards were skipped because the pull request run that
            # started at `reused` tested this tree ("" = no time in the log).
            jobs = [
                job("Release quality gates / Reuse PR test result?", job_id=9),
                job(
                    "Release quality gates / test (${{ matrix.suite }}, 3.12)",
                    "skipped",
                ),
                job("Release quality gates / test (3.12)", "success", job_id=3),
            ]
            log = REUSE_LOG.format(started=reused) if reused else "no table"
            self.gh.add_run(run_id, jobs, log)
            return lh.run_report(self.gh, "gate", run_id, sha)
        if unknown:
            jobs = [job("Release quality gates / test (3.12)", "cancelled", job_id=3)]
        else:
            jobs = [
                job(
                    "Release quality gates / test (other, 3.12)",
                    "failure" if broken else "success",
                    "Run Tests",
                    job_id=2,
                ),
                job(
                    "Release quality gates / test (3.12)",
                    "failure" if broken else "success",
                    job_id=3,
                ),
            ]
        self.gh.add_run(run_id, jobs, GATE_LOG_FAIL if broken else "")
        return lh.run_report(self.gh, "gate", run_id, sha)

    def test_break_update_fix(self):
        # 1. A commit breaks one browser test: one issue names the test and commit.
        self.push("bad1")
        self.browser("r1", "bad1", broken=True)
        self.assertEqual(len(self.gh.open_list()), 1)
        issue = self.gh.open_issue()
        self.assertIn(BROWSER_TEST, issue["body"])
        self.assertIn("bad1", issue["body"])
        self.assertIn("compare/good1...bad1", issue["body"])

        # 2. An unrelated push while still broken: same issue, no duplicate, no noise.
        self.push("bad2")
        self.browser("r2", "bad2", broken=True)
        self.assertEqual(len(self.gh.issues_by_number), 1)
        body = self.gh.open_issue()["body"]
        self.assertIn("Failing at [bad2]", body)
        self.assertIn("First seen at bad1", body)
        self.assertEqual(self.gh.comments, [])

        # 3. The fix: commented and closed once the publish gate has also passed.
        self.push("fix")
        self.browser("r3", "fix", broken=False)
        self.assertEqual(len(self.gh.open_list()), 1)
        self.assertIn("has not reported yet", self.gh.comments[-1][1])
        self.gate("g3", "fix")
        self.assertEqual(self.gh.open_list(), [])
        self.assertIn("passes on latest again at fix", self.gh.comments[-1][1])

        # A later break opens a new issue whose last good commit is the fix.
        self.push("bad3")
        self.browser("r4", "bad3", broken=True)
        self.assertEqual(len(self.gh.open_list()), 1)
        self.assertIn("compare/fix...bad3", self.gh.open_issue()["body"])

    def test_older_run_finishing_late_changes_nothing(self):
        # 4. Two quick pushes. Concurrency cancels the older run; if it still
        # reports (it finished first, or a re-run), the newer state wins.
        self.push("bad1")
        self.push("fix")
        self.browser("r2", "fix", broken=False)
        self.assertEqual(self.gh.issues_by_number, {})
        self.push("bad2")
        self.browser("r3", "bad2", broken=True)
        number = self.gh.open_issue()["number"]
        plan = self.browser("r1", "bad1", broken=True)
        self.assertEqual(plan.action, "none")
        self.assertIn("Failing at [bad2]", self.gh.issues_by_number[number]["body"])
        plan = self.browser("r0", "fix", broken=False)
        self.assertEqual(plan.action, "none")
        self.assertEqual(len(self.gh.open_list()), 1)

    def test_infrastructure_failure_opens_nothing(self):
        # 5. Chromium fails to install: "could not determine", no issue.
        self.push("sha1")
        broken = job(
            "Browser tests", "failure", "Install dependencies and Chromium", job_id=7
        )
        broken["steps"].append({"name": lh.BROWSER_STEP, "conclusion": "skipped"})
        self.gh.add_run("r1", [broken])
        plan = lh.run_report(self.gh, "browser", "r1", "sha1")
        self.assertEqual(plan.action, "none")
        self.assertEqual(self.gh.issues_by_number, {})
        self.assertIn("could not determine", plan.note)
        self.assertIn("unknown", Path(self._summary.name).read_text())

    def test_unknown_while_broken_keeps_the_issue_open(self):
        self.push("bad1")
        self.browser("r1", "bad1", broken=True)
        self.push("sha2")
        self.gh.add_run("r2", [browser_job("cancelled", "cancelled")])
        plan = lh.run_report(self.gh, "browser", "r2", "sha2")
        self.assertEqual(plan.action, "update")
        issue = self.gh.open_issue()
        self.assertIn(BROWSER_TEST, issue["body"])
        self.assertIn("could not tell", issue["body"])
        # The same unknown reported twice comments once.
        lh.run_report(self.gh, "browser", "r2", "sha2")
        self.assertEqual(len(self.gh.comments), 1)
        self.push("fix")
        self.browser("r3", "fix", broken=False)
        self.gate("g3", "fix")
        self.assertEqual(self.gh.open_list(), [])

    def test_both_sources_must_pass_before_closing(self):
        self.push("bad1")
        self.gh.add_run(
            "g1",
            [
                job(
                    "Release quality gates / test (other, 3.12)",
                    "failure",
                    "Run Tests",
                    job_id=2,
                )
            ],
            GATE_LOG_FAIL,
        )
        lh.run_report(self.gh, "gate", "g1", "bad1")
        self.browser("b1", "bad1", broken=True)
        self.assertEqual(len(self.gh.issues_by_number), 1)
        body = self.gh.open_issue()["body"]
        self.assertIn("Other tests (publish gate or daily run)", body)
        self.assertIn(BROWSER_TEST, body)

        self.push("fix")
        self.browser("b2", "fix", broken=False)
        self.assertEqual(len(self.gh.open_list()), 1)
        self.assertIn(
            "Not closing yet: Other tests (publish gate or daily run) still failing",
            self.gh.comments[-1][1],
        )

        self.gh.add_run(
            "g2",
            [
                job(
                    "Release quality gates / test (other, 3.12)",
                    "success",
                    "Run Tests",
                    job_id=2,
                ),
                job("Release quality gates / test (3.12)", "success", job_id=3),
            ],
        )
        lh.run_report(self.gh, "gate", "g2", "fix")
        self.assertEqual(self.gh.open_list(), [])

    def test_test_set_change_comments(self):
        self.push("bad1")
        self.browser("r1", "bad1", broken=True)
        self.push("bad2")
        self.gh.add_run(
            "r2", [browser_job("failure")], BROWSER_LOG_FAIL + GATE_LOG_FAIL
        )
        lh.run_report(self.gh, "browser", "r2", "bad2")
        self.assertIn("now also failing", self.gh.comments[-1][1])

    def test_dry_run_writes_nothing(self):
        self.push("bad1")
        self.gh.add_run("r1", [browser_job("failure")], BROWSER_LOG_FAIL)
        plan = lh.run_report(self.gh, "browser", "r1", "bad1", dry_run=True)
        self.assertEqual(plan.action, "open")
        self.assertEqual(self.gh.issues_by_number, {})

    def test_pr_note_reads_the_open_issue(self):
        self.push("bad1")
        self.browser("r1", "bad1", broken=True)
        self.gh.add_run(
            "pr1",
            [
                job("test (app, 3.12)", "failure", "Run Tests", job_id=1),
                job("test (3.12)", "failure", job_id=3),
            ],
            BROWSER_LOG_FAIL,
        )
        text = lh.run_pr_note(self.gh, "pr1")
        self.assertIn("All 1 failing tests here also fail on `latest`", text)


class CodexFindings(unittest.TestCase):
    """Cases from the Codex review of PR #2006."""

    setUp = InjectionScenarios.setUp
    push = InjectionScenarios.push
    browser = InjectionScenarios.browser
    gate = InjectionScenarios.gate

    def test_unknown_gate_keeps_the_issue_open_after_browser_recovers(self):
        self.push("bad1")
        self.browser("b1", "bad1", broken=True)
        self.gate("g1", "bad1", unknown=True)
        self.push("fix")
        self.browser("b2", "fix", broken=False)
        self.assertEqual(len(self.gh.open_list()), 1)
        self.assertIn("could not tell at bad1", self.gh.comments[-1][1])
        self.gate("g2", "fix")
        self.assertEqual(self.gh.open_list(), [])

    def test_rerun_of_an_old_failure_after_close_is_ignored(self):
        self.push("bad1")
        self.browser("b1", "bad1", broken=True)
        self.push("fix")
        self.browser("b2", "fix", broken=False)
        self.gate("g2", "fix")
        self.assertEqual(self.gh.open_list(), [])
        plan = self.browser("b1-rerun", "bad1", broken=True)
        self.assertEqual(plan.action, "none")
        self.assertEqual(self.gh.open_list(), [])

    def test_passes_without_an_issue_are_recorded_on_the_closed_one(self):
        self.push("bad1")
        self.browser("b1", "bad1", broken=True)
        self.push("fix")
        self.browser("b2", "fix", broken=False)
        self.gate("g2", "fix")
        self.push("next")
        plan = self.browser("b3", "next", broken=False)
        self.assertEqual(plan.action, "persist")
        closed = self.gh.closed_issue()
        self.assertEqual(
            lh.read_state(closed["body"])["sources"]["browser"]["sha"], "next"
        )
        comments = len(self.gh.comments)
        # A rerun of the fix commit, older than "next", is now stale too.
        self.assertEqual(self.browser("b2-rerun", "fix", broken=True).action, "none")
        self.assertEqual(len(self.gh.comments), comments)

    def test_duplicate_from_a_concurrent_writer_is_folded(self):
        self.push("bad1")
        real_create = self.gh.create

        def racing_create(body):
            # The other report job opened its issue a moment earlier.
            self.gh.issues_by_number[50] = {
                "number": 50,
                "body": lh.render_body({"sources": {}}, "o/r"),
                "state": "open",
            }
            self.gh.create = real_create
            return real_create(body)

        self.gh.create = racing_create
        self.browser("b1", "bad1", broken=True)
        self.assertEqual([i["number"] for i in self.gh.open_list()], [50])
        self.assertIn(BROWSER_TEST, self.gh.issues_by_number[50]["body"])
        self.assertIn((101, "Duplicate of #50."), self.gh.comments)
        self.assertEqual(self.gh.issues_by_number[101]["state"], "closed")

    def test_lost_edit_is_redone(self):
        self.push("bad1")
        self.gate("g1", "bad1", broken=True)
        number = self.gh.open_issue()["number"]
        stale = self.gh.issues_by_number[number]["body"]
        real_edit = self.gh.edit

        def racing_edit(n, body):
            real_edit(n, body)
            # The gate job wrote right after, from the body it read before ours.
            real_edit(n, stale)
            self.gh.edit = real_edit

        self.gh.edit = racing_edit
        self.browser("b1", "bad1", broken=True)
        state = lh.read_state(self.gh.issues_by_number[number]["body"])
        self.assertEqual(set(state["sources"]), {"browser", "gate"})


def _same_commit_case(self):
    self.push("a")
    self.gate("g1", "a", broken=True)
    self.browser("b1", "a", broken=False)
    self.push("b")
    self.gate("g2", "b")
    self.assertEqual(len(self.gh.open_list()), 1)
    self.browser("b2", "b", broken=False)
    self.assertEqual(self.gh.open_list(), [])


CodexFindings.test_close_needs_both_sources_on_the_same_commit = _same_commit_case


class NoIssueBaselineTests(unittest.TestCase):
    def closed(self, **sources):
        return {"body": lh.render_body({"sources": sources}, "o/r")}

    def test_green_only_when_both_pass_the_head(self):
        closed = self.closed(
            browser={"status": "pass", "sha": "h"}, gate={"status": "pass", "sha": "h"}
        )
        self.assertTrue(lh.no_issue_baseline(closed, "h")["health_green"])

    def test_gate_unknown_is_not_green(self):
        closed = self.closed(
            browser={"status": "pass", "sha": "h"},
            gate={"status": "pass", "sha": "g", "unknown_at": "h"},
        )
        baseline = lh.no_issue_baseline(closed, "h")
        self.assertFalse(baseline["health_green"])
        self.assertTrue(lh.pr_note(["x.y"], baseline).startswith("Unknown"))

    def test_gate_still_running_is_not_green(self):
        closed = self.closed(
            browser={"status": "pass", "sha": "h"},
            gate={"status": "pass", "sha": "old"},
        )
        baseline = lh.no_issue_baseline(closed, "h")
        self.assertFalse(baseline["health_green"])
        self.assertIn("still being checked", lh.pr_note(["x.y"], baseline))

    def test_nothing_recorded_is_not_green(self):
        self.assertFalse(lh.no_issue_baseline(None, "h")["health_green"])


class StateTests(unittest.TestCase):
    def test_round_trip_through_the_body(self):
        state = {
            "sources": {"gate": {"status": "fail", "tests": ["a.b"], "sha": "abc"}}
        }
        self.assertEqual(lh.read_state(lh.render_body(state, "o/r")), state)

    def test_hand_edited_body_reads_as_empty(self):
        self.assertEqual(lh.read_state("someone rewrote this"), {"sources": {}})

    def test_long_lists_are_capped(self):
        tests = [f"t.T.test_{i}" for i in range(lh.MAX_LISTED + 5)]
        body = lh.render_body(
            {"sources": {"browser": {"status": "fail", "tests": tests}}}, "o/r"
        )
        self.assertIn("and 5 more", body)


if __name__ == "__main__":
    unittest.main()


class CalendarBreaks(unittest.TestCase):
    """Breaks that come from the date, not a commit.

    The real case: Discover's holiday window opened on 15 November 2026 and
    three DiscoverServiceTests failed on every branch with no commit to blame.
    """

    setUp = InjectionScenarios.setUp
    push = InjectionScenarios.push
    browser = InjectionScenarios.browser
    gate = InjectionScenarios.gate

    def test_daily_run_catches_a_date_break_on_an_unchanged_commit(self):
        # An earlier issue closed with both sources passing at "head"; its
        # state is how the next report knows "head" passed.
        self.push("bad0")
        self.browser("b0", "bad0", broken=True)
        self.push("head")
        self.browser("b1", "head", broken=False)
        self.gate("g1", "head")
        self.assertEqual(self.gh.open_list(), [])
        # The date rolls; no push. The daily run fails on the same commit.
        plan = self.gate("daily1", "head", broken=True)
        self.assertEqual(plan.action, "open")
        body = self.gh.open_issue()["body"]
        self.assertIn("config.tests.test_ui_docs.UiDocsTests", body)
        self.assertIn("No commit caused this", body)
        self.assertNotIn("compare/head...head", body)

    def test_first_ever_daily_failure_uses_the_last_gate_success(self):
        # No issue has ever existed, so no state: the last good commit comes
        # from the newest successful gate run, which is this same commit.
        self.gh.successes[lh.GATE_WORKFLOW] = "head"
        self.push("head")
        self.gate("daily1", "head", broken=True)
        self.assertIn("No commit caused this", self.gh.open_issue()["body"])

    def test_reused_run_from_before_the_break_does_not_close(self):
        self.push("head")
        self.gate("daily1", "head", broken=True)
        self.push("merge")
        self.browser("b2", "merge", broken=False)
        self.gate("g2", "merge", reused=BEFORE)
        self.assertEqual(len(self.gh.open_list()), 1)
        body = self.gh.open_issue()["body"]
        self.assertIn("before these tests started failing", body)
        # An unreadable reuse time proves nothing either.
        self.push("merge2")
        self.browser("b3", "merge2", broken=False)
        self.gate("g3", "merge2", reused="")
        self.assertEqual(len(self.gh.open_list()), 1)
        # A fix whose pull request ran after the break was seen closes it.
        self.push("fix")
        self.browser("b4", "fix", broken=False)
        self.gate("g4", "fix", reused=AFTER)
        self.assertEqual(self.gh.open_list(), [])

    def test_reused_pass_with_nothing_failing_is_a_pass(self):
        self.push("head")
        self.browser("b1", "head", broken=False)
        plan = self.gate("g1", "head", reused=BEFORE)
        self.assertEqual(plan.action, "none")
        self.assertEqual(self.gh.issues_by_number, {})
        self.assertIn("passing", plan.note)
