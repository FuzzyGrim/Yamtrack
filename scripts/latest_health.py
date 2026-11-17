#!/usr/bin/env python3
# ruff: noqa: T201
"""Keep one GitHub issue open while tests are failing on latest.

Two things test every push to latest:

- browser: .github/workflows/latest-health.yml runs the Playwright tests that
  pull requests must pass. The publish gate leaves them out on purpose, so
  without this nothing ran them on latest.
- gate: the publish gate in docker-image.yml runs every other test. It already
  went red within about 20 minutes of a bad push, but nobody was told. A daily
  scheduled run of latest-health.yml runs the same tests again with no push,
  because some breaks come from the calendar, not a commit (Discover's holiday
  window opened on 15 November and three tests failed on every branch).

Each report is about one source and one commit. The issue body carries the
state of both sources in a hidden JSON comment. The issue is opened when a
source fails, updated (never duplicated) while anything is failing, and
commented on and closed once every source passes again.

A run that could not tell (Chromium failed to install, the job was killed, the
log had no test names) is "unknown". Unknown never opens an issue and never
closes one.

Pull request runs call "pr-note", which writes one line in the job summary
saying whether the tests that failed there are also failing on latest. If the
state of latest cannot be read it says so; it never guesses "green".

Usage:
  latest_health.py report --source browser|gate --run-id ID --sha SHA [--dry-run]
  latest_health.py pr-note --run-id ID
(needs GH_TOKEN and GH_REPO)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

TITLE = "Tests are failing on latest"
LABEL = "bug"
STATE_RE = re.compile(r"<!-- latest-health-state (\{.*?\}) -->", re.DOTALL)
SOURCES = ("browser", "gate")
SOURCE_NAMES = {
    "browser": "Browser tests (Latest Health)",
    "gate": "Other tests (publish gate or daily run)",
}
HEALTH_WORKFLOW = "latest-health.yml"
GATE_WORKFLOW = "docker-image.yml"
# The job and step that run the tests for each source.
BROWSER_JOB = re.compile(r"^Browser tests$")
BROWSER_STEP = "Run browser tests"
# "Release quality gates / test (app, 3.12)": the shards, not the "test (3.12)"
# roll-up, whatever the shards are called.
GATE_JOB = re.compile(r"(^|/ )test \([^,()]+, 3\.\d+\)$")
GATE_STEP = "Run Tests"
GATE_ROLLUP = re.compile(r"(^|/ )test \(3\.\d+\)$")
# "Release quality gates / Reuse PR test result?": says which pull request
# run the gate reused, and when that run started.
REUSE_JOB = re.compile(r"(^|/ )Reuse PR test result\?$")
REUSE_STARTED = re.compile(r"\| run started \| `([0-9T:Z.+-]+)` \|")
# Pull request shard jobs: "test (app, 3.12)".
PR_JOB = re.compile(r"^test \([^,()]+, 3\.\d+\)$")

# unittest prints "FAIL: test_x (app.tests.mod.Class.test_x)". Older Pythons
# print only the class in brackets; a class-level error prints "setUpClass".
FAILURE_LINE = re.compile(
    r"^(?:\S+Z )?(?:FAIL|ERROR): (\w+) \(([\w.]+)\)", re.MULTILINE
)
MAX_LISTED = 30
# Playwright's message when its browser is missing or cannot start.
BROKEN_BROWSER = re.compile(
    r"BrowserType\.launch: (Executable doesn't exist|Failed to launch)"
)


def parse_failures(log: str) -> list[str]:
    """Return the sorted, de-duplicated dotted names of failed tests in a log."""
    names = set()
    for method, dotted in FAILURE_LINE.findall(log):
        names.add(dotted if dotted.endswith("." + method) else f"{dotted}.{method}")
    return sorted(names)


@dataclass
class Result:
    status: str  # "pass", "fail" or "unknown"
    tests: list[str] = field(default_factory=list)
    reason: str = ""
    # Set when the gate passed by reusing a pull request's earlier run: when
    # that run started ("" if it could not be read). None for a real run.
    reused_started: str | None = None


def reuse_started(jobs: list[dict], logs) -> str:
    """Return when the pull request run the gate reused started, or ""."""
    for job in jobs:
        if REUSE_JOB.search(job.get("name", "")):
            try:
                match = REUSE_STARTED.search(logs(job))
            except Exception:  # an unreadable log only loses the time
                return ""
            return match.group(1) if match else ""
    return ""


def classify(
    jobs: list[dict], job_re: re.Pattern, step: str, logs, rollup_re=None
) -> Result:
    """Decide what one run says about its commit.

    jobs: the run's jobs from the Actions API (with steps). logs(job) returns the
    job's log text. A source fails only when its test step failed and the log
    names the failed tests; anything else that is not a clean pass is unknown.
    """
    matching = [job for job in jobs if job_re.search(job.get("name", ""))]
    rollup = [
        job for job in jobs if rollup_re and rollup_re.search(job.get("name", ""))
    ]
    if not any(job.get("conclusion") != "skipped" for job in matching):
        # When the gate reuses the pull request's result, the shards are one
        # skipped job named "test (${{ matrix.suite }}, ...)" and the roll-up
        # passes: the same tree already passed every shard, but at the time of
        # that pull request run, which matters for date-dependent tests.
        if rollup and all(job.get("conclusion") == "success" for job in rollup):
            return Result("pass", reused_started=reuse_started(jobs, logs))
        return Result("unknown", reason="the test jobs did not run")
    failed_tests: set[str] = set()
    unclear = []
    for job in matching:
        conclusion = job.get("conclusion")
        if conclusion in {"success", "skipped"}:
            continue
        step_result = next(
            (
                s.get("conclusion")
                for s in job.get("steps") or []
                if s.get("name") == step
            ),
            None,
        )
        if conclusion == "failure" and step_result == "failure":
            try:
                text = logs(job)
            except Exception as error:  # any read error means "unknown"
                unclear.append(f"{job['name']}: could not read its log ({_why(error)})")
                continue
            if BROKEN_BROWSER.search(text):
                # Every browser test then errors by name; that is the runner, not latest.
                unclear.append(f"{job['name']}: the browser could not start")
                continue
            names = parse_failures(text)
            if names:
                failed_tests.update(names)
                continue
            unclear.append(f"{job['name']}: the test step failed without naming a test")
            continue
        where = (
            f"step {step!r} was {step_result}"
            if step_result
            else "it stopped before the tests"
        )
        unclear.append(f"{job['name']} ended {conclusion}; {where}")
    if failed_tests:
        return Result("fail", sorted(failed_tests), "; ".join(unclear))
    if unclear:
        return Result("unknown", reason="; ".join(unclear))
    if rollup_re is not None:
        if rollup and any(job.get("conclusion") != "success" for job in rollup):
            return Result(
                "unknown", reason="the shards passed but the test roll-up did not"
            )
    return Result("pass")


def read_state(body: str | None) -> dict:
    match = STATE_RE.search(body or "")
    if not match:
        return {"sources": {}}
    try:
        state = json.loads(match.group(1))
    except ValueError:
        return {"sources": {}}
    state.setdefault("sources", {})
    return state


def _short(sha: str | None) -> str:
    return (sha or "?")[:9]


def render_body(state: dict, repo: str) -> str:
    server = "https://github.com"
    lines = [
        "Some tests fail on `latest` itself, so every pull request that is up to date "
        "with it fails them too. This issue is kept up to date by the Latest Health "
        "workflow and closes itself when they pass again.",
        "",
    ]
    for source in SOURCES:
        entry = state["sources"].get(source)
        if not entry:
            continue
        lines.append(f"### {SOURCE_NAMES[source]}")
        status = entry.get("status")
        sha = entry.get("sha")
        commit = f"[{_short(sha)}]({server}/{repo}/commit/{sha})"
        run = f"[run]({entry.get('run_url')})" if entry.get("run_url") else "run"
        if status == "fail":
            lines.append(f"Failing at {commit} ({run}).")
            first_bad, last_good = entry.get("first_bad"), entry.get("last_good")
            if first_bad and first_bad == last_good:
                lines.append(
                    f"First seen at {_short(first_bad)}, which passed earlier. No commit "
                    "caused this: most likely the date (a seasonal or calendar rule) or "
                    "something outside the repository changed."
                )
            elif first_bad and last_good:
                lines.append(
                    f"First seen at {_short(first_bad)}. Last passed at {_short(last_good)}. "
                    f"[Commits that could have caused it]"
                    f"({server}/{repo}/compare/{last_good}...{first_bad})."
                )
            elif first_bad:
                lines.append(
                    f"First seen at {_short(first_bad)}. No earlier passing run is known."
                )
            tests = entry.get("tests") or []
            lines.extend(f"- `{name}`" for name in tests[:MAX_LISTED])
            if len(tests) > MAX_LISTED:
                lines.append(f"- and {len(tests) - MAX_LISTED} more (see the run)")
        elif status == "pass":
            lines.append(f"Passing at {commit} ({run}).")
        if entry.get("unknown_at"):
            lines.append(
                f"The newest run, at {_short(entry['unknown_at'])}, could not tell: "
                f"{entry.get('unknown_reason') or 'see the run'}."
            )
        lines.append("")
    lines.append(f"<!-- latest-health-state {json.dumps(state, sort_keys=True)} -->")
    return "\n".join(lines)


def failing_tests(state: dict) -> set[str]:
    return {
        name
        for entry in state["sources"].values()
        if entry.get("status") == "fail"
        for name in entry.get("tests") or []
    }


@dataclass
class Plan:
    """What a report does to the issue.

    action: none, open, update, close, or persist (silently record the state on
    the closed issue, so later reports can tell what is newer).
    """

    action: str
    state: dict
    comment: str = ""
    note: str = ""


def all_passing(state: dict) -> bool:
    """Return True only when every source's newest result is a clean pass."""
    return all(
        (state["sources"].get(source) or {}).get("status") == "pass"
        and not (state["sources"].get(source) or {}).get("unknown_at")
        for source in SOURCES
    )


def _not_passing(state: dict) -> list[str]:
    out = []
    for source in SOURCES:
        entry = state["sources"].get(source) or {}
        if entry.get("status") == "fail":
            out.append(f"{SOURCE_NAMES[source]} still failing")
        elif entry.get("unknown_at"):
            out.append(
                f"{SOURCE_NAMES[source]} could not tell at {_short(entry['unknown_at'])}"
            )
        elif entry.get("status") != "pass":
            out.append(f"{SOURCE_NAMES[source]} has not reported yet")
    return out


def plan_report(
    issue: dict | None,
    source: str,
    sha: str,
    run_url: str,
    result: Result,
    *,
    newer_recorded,
    last_good_fallback,
    now: str,
    closed: dict | None = None,
) -> Plan:
    """Decide the change to the tracking issue for one source's result at sha.

    issue: the open tracking issue (number, body) or None. closed: the newest
    closed tracking issue, whose state carries each source's last result while
    nothing is open. newer_recorded(recorded_sha) is True when recorded_sha is a
    later commit than sha, so this report is stale. last_good_fallback() returns
    the commit the source last passed on when no state holds one.
    """
    if issue:
        state = read_state(issue["body"])
    elif closed:
        state = read_state(closed["body"])
    else:
        state = {"sources": {}}
    entry = dict(state["sources"].get(source) or {})
    recorded = entry.get("unknown_at") or entry.get("sha")
    if recorded and recorded != sha and newer_recorded(recorded):
        return Plan(
            "none",
            state,
            note=f"ignored: {_short(sha)} is older than {_short(recorded)}",
        )
    quiet = "persist" if closed else "none"

    if (
        result.status == "pass"
        and result.reused_started is not None
        and entry.get("status") == "fail"
        and not _after(result.reused_started, entry.get("failing_since"))
    ):
        # The gate reused a pull request run from before these tests started
        # failing. The tree is the same, but a test that broke with the date
        # would pass then and fail now, so this proves nothing either way.
        when = result.reused_started or "an unknown time"
        result = Result(
            "unknown",
            reason=(
                f"it reused a pull request run that started at {when}, before "
                "these tests started failing"
            ),
        )

    if result.status == "unknown":
        if entry.get("unknown_at") == sha:
            return Plan("none", state, note="already recorded as unknown")
        entry.update(unknown_at=sha, unknown_reason=result.reason, unknown_run=run_url)
        state["sources"][source] = entry
        if not issue:
            return Plan(quiet, state, note=f"could not determine: {result.reason}")
        comment = (
            f"{SOURCE_NAMES[source]} could not tell at {_short(sha)}: {result.reason}. "
            f"Nothing changed here. [Run]({run_url})"
        )
        return Plan("update", state, comment=comment, note="unknown recorded")

    previous_status = entry.get("status")
    previous_sha = entry.get("sha")
    previous_tests = entry.get("tests") or []
    entry.pop("unknown_at", None)
    entry.pop("unknown_reason", None)
    entry.pop("unknown_run", None)
    entry.update(sha=sha, run_url=run_url, status=result.status, at=now)

    if result.status == "fail":
        entry["tests"] = result.tests
        if previous_status != "fail" or not issue:
            entry["first_bad"] = sha
            entry["failing_since"] = now
            entry["last_good"] = (
                previous_sha if previous_status == "pass" else None
            ) or last_good_fallback()
        state["sources"][source] = entry
        if not issue:
            return Plan("open", state, note="opened")
        if previous_status != "fail":
            comment = f"{SOURCE_NAMES[source]} started failing at {_short(sha)}. [Run]({run_url})"
        elif previous_tests != result.tests:
            added = sorted(set(result.tests) - set(previous_tests))
            fixed = sorted(set(previous_tests) - set(result.tests))
            parts = [f"{SOURCE_NAMES[source]} at {_short(sha)}:"]
            if added:
                parts.append(
                    "now also failing " + ", ".join(f"`{n}`" for n in added) + "."
                )
            if fixed:
                parts.append(
                    "passing again " + ", ".join(f"`{n}`" for n in fixed) + "."
                )
            comment = " ".join(parts) + f" [Run]({run_url})"
        else:
            comment = ""
        return Plan("update", state, comment=comment, note="still failing")

    # pass
    entry["tests"] = []
    entry.pop("first_bad", None)
    entry.pop("last_good", None)
    entry.pop("failing_since", None)
    state["sources"][source] = entry
    if not issue:
        return Plan(quiet, state, note="passing, no issue open")
    behind = [
        SOURCE_NAMES[s]
        for s in SOURCES
        if (state["sources"].get(s) or {}).get("status") == "pass"
        and state["sources"][s].get("sha") != sha
    ]
    if not all_passing(state) or behind:
        # Closing needs a clean pass from every source on this same commit.
        waiting = _not_passing(state) + [
            f"{n} has not passed {_short(sha)} yet" for n in behind
        ]
        comment = ""
        if previous_status == "fail":
            comment = (
                f"{SOURCE_NAMES[source]} pass again at {_short(sha)}. "
                f"Not closing yet: {'; '.join(waiting)}. [Run]({run_url})"
            )
        return Plan(
            "update", state, comment=comment, note="waiting: " + "; ".join(waiting)
        )
    comment = (
        f"Every test passes on latest again at {_short(sha)}. Closing. [Run]({run_url})"
    )
    return Plan("close", state, comment=comment, note="closed")


def _after(started: str, since: str | None) -> bool:
    """Return True when the ISO time started is after since; unreadable is False."""
    try:
        return datetime.fromisoformat(started) > datetime.fromisoformat(since or "")
    except (TypeError, ValueError):
        return False


def pr_note(pr_failures: list[str], baseline: dict | None, error: str = "") -> str:
    """One short summary for a pull request run whose tests failed.

    baseline: {"issue": number or None, "state": dict, "health_green": bool} or
    None when latest's state could not be read.
    """
    if baseline is None:
        why = f" ({error})" if error else ""
        return (
            f"Unknown whether these failures also happen on `latest`: its test state "
            f'could not be read{why}. Check the open issues for "{TITLE}".'
        )
    if not pr_failures:
        return "This run failed without naming a failing test, so it cannot be compared with `latest`."
    issue = baseline.get("issue")
    if issue:
        on_latest = failing_tests(baseline["state"])
        shared = sorted(set(pr_failures) & on_latest)
        ref = f"#{issue}"
        if len(shared) == len(pr_failures):
            return (
                f"All {len(pr_failures)} failing tests here also fail on `latest` ({ref}). "
                "This branch is probably not the cause; it should go green once `latest` is fixed."
            )
        if shared:
            new = [n for n in pr_failures if n not in on_latest]
            return (
                f"{len(shared)} of {len(pr_failures)} failing tests here also fail on `latest` ({ref}). "
                "Only these are new on this branch: "
                + ", ".join(f"`{n}`" for n in new[:10])
            )
        return (
            f"`latest` has failing tests ({ref}), but none of the {len(pr_failures)} failing here. "
            "These failures are probably from this branch."
        )
    if baseline.get("health_green"):
        return (
            "No tests are known to fail on `latest`: both its browser tests and its other "
            "tests passed on its newest commit. These failures are probably from this branch."
        )
    why = baseline.get("why") or "not every check of its newest commit has passed"
    return f"Unknown whether these failures also happen on `latest`: {why}."


def no_issue_baseline(closed: dict | None, head: str) -> dict:
    """With no open issue, latest counts as green only if both sources passed its head."""
    state = read_state(closed["body"]) if closed else {"sources": {}}
    if not all_passing(state):
        return {
            "issue": None,
            "health_green": False,
            "why": "; ".join(_not_passing(state)),
        }
    stale = [
        SOURCE_NAMES[s]
        for s in SOURCES
        if (state["sources"][s].get("sha") or "") != head
    ]
    if stale:
        return {
            "issue": None,
            "health_green": False,
            "why": f"its newest commit {_short(head)} is still being checked ({', '.join(stale)})",
        }
    return {"issue": None, "health_green": True}


# --- GitHub ---------------------------------------------------------------


class GitHub:
    def __init__(self, repo: str):
        self.repo = repo

    def _gh(self, *args: str, stdin: str | None = None) -> str:
        return subprocess.run(  # noqa: S603
            ["gh", *args],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
            input=stdin,
        ).stdout

    def api(self, path: str) -> dict | list:
        return json.loads(self._gh("api", f"repos/{self.repo}/{path}"))

    def jobs(self, run_id: str) -> list[dict]:
        return self.api(f"actions/runs/{run_id}/jobs?per_page=100&filter=latest")[
            "jobs"
        ]

    def log(self, job: dict) -> str:
        return self._gh("api", f"repos/{self.repo}/actions/jobs/{job['id']}/logs")

    def issues(self, state: str) -> list[dict]:
        # The issues API also lists pull requests; keep issues whose title matches exactly.
        items = self.api(
            f"issues?state={state}&labels={LABEL}&per_page=100&sort=created&direction=desc"
        )
        return [i for i in items if i.get("title") == TITLE and "pull_request" not in i]

    def open_issue(self) -> dict | None:
        found = self.issues("open")
        # Oldest first, so a duplicate someone made by hand never takes over.
        return min(found, key=lambda i: i["number"]) if found else None

    def closed_issue(self) -> dict | None:
        """Return the newest closed tracking issue, which holds the last state."""
        found = self.issues("closed")
        return max(found, key=lambda i: i["number"]) if found else None

    def latest_head(self) -> str:
        return self.api("git/ref/heads/latest")["object"]["sha"]

    def is_newer(self, recorded: str, sha: str) -> bool:
        """True when recorded is a later commit than sha on the same line."""
        try:
            status = self.api(f"compare/{sha}...{recorded}")["status"]
        except (subprocess.CalledProcessError, ValueError, KeyError):
            return False
        return status == "ahead"

    def last_success(self, workflow: str) -> str | None:
        try:
            runs = self.api(
                f"actions/workflows/{workflow}/runs?branch=latest&event=push&status=success&per_page=1"
            )["workflow_runs"]
        except (subprocess.CalledProcessError, ValueError, KeyError):
            return None
        return runs[0]["head_sha"] if runs else None

    def create(self, body: str) -> int:
        out = self._gh(
            "issue",
            "create",
            "--repo",
            self.repo,
            "--title",
            TITLE,
            "--label",
            LABEL,
            "--body-file",
            "-",
            stdin=body,
        )
        return int(out.strip().rstrip("/").rsplit("/", 1)[-1])

    def edit(self, number: int, body: str) -> None:
        self._gh(
            "issue",
            "edit",
            str(number),
            "--repo",
            self.repo,
            "--body-file",
            "-",
            stdin=body,
        )

    def comment(self, number: int, text: str) -> None:
        self._gh(
            "issue",
            "comment",
            str(number),
            "--repo",
            self.repo,
            "--body-file",
            "-",
            stdin=text,
        )

    def close(self, number: int) -> None:
        self._gh("issue", "close", str(number), "--repo", self.repo)


def _why(error: Exception) -> str:
    """Return a short reason for a failed GitHub call, for people to read."""
    stderr = getattr(error, "stderr", None)
    text = (stderr or str(error) or type(error).__name__).strip().splitlines()
    return (text[0] if text else type(error).__name__)[:200]


def _summary(text: str) -> None:
    print(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with Path(path).open("a", encoding="utf-8") as handle:
            handle.write(text + "\n")


def run_report(gh, source: str, run_id: str, sha: str, dry_run: bool = False) -> Plan:
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    run_url = f"{server}/{gh.repo}/actions/runs/{run_id}"
    if source == "browser":
        job_re, step, rollup, workflow = (
            BROWSER_JOB,
            BROWSER_STEP,
            None,
            HEALTH_WORKFLOW,
        )
    else:
        job_re, step, rollup, workflow = GATE_JOB, GATE_STEP, GATE_ROLLUP, GATE_WORKFLOW
    try:
        result = classify(gh.jobs(run_id), job_re, step, gh.log, rollup)
    except (subprocess.CalledProcessError, ValueError, KeyError) as error:
        result = Result("unknown", reason=f"could not read the run ({_why(error)})")
    for attempt in range(3):
        plan = _report_once(gh, source, sha, run_url, result, workflow, dry_run)
        if plan is not None:
            return plan
        _summary(
            f"Another report changed the issue at the same time; retrying ({attempt + 1})."
        )
    _summary("Gave up after three tries; the next push will report again.")
    return Plan("none", {"sources": {}}, note="conflict")


def _report_once(gh, source, sha, run_url, result, workflow, dry_run) -> Plan | None:
    """Apply one report. Return None when a concurrent writer got in the way.

    The two report jobs (browser and publish gate) can run at the same moment.
    GitHub concurrency groups would drop a queued report, so instead each write
    is checked afterwards: a duplicate issue is folded into the oldest one, and
    an edit that lost this source's entry is redone from the fresh body.
    """
    issue = gh.open_issue()
    closed = None if issue else gh.closed_issue()
    plan = plan_report(
        issue,
        source,
        sha,
        run_url,
        result,
        newer_recorded=lambda recorded: gh.is_newer(recorded, sha),
        last_good_fallback=lambda: gh.last_success(workflow),
        now=datetime.now(UTC).isoformat(timespec="seconds"),
        closed=closed,
    )
    tests = f" ({len(result.tests)} failing)" if result.tests else ""
    _summary(
        f"{SOURCE_NAMES[source]} at {_short(sha)}: {result.status}{tests}. Issue: {plan.note}."
    )
    if result.reason:
        _summary(f"Detail: {result.reason}")
    if dry_run or plan.action == "none":
        if dry_run:
            print(f"[dry run] would {plan.action}; comment: {plan.comment!r}")
        return plan
    body = render_body(plan.state, gh.repo)
    if plan.action == "open":
        number = gh.create(body)
        oldest = gh.open_issue()
        if oldest and oldest["number"] != number:
            gh.comment(number, f"Duplicate of #{oldest['number']}.")
            gh.close(number)
            return None
        _summary(f"Opened #{number}.")
        return plan
    target = issue or closed
    if target is None:
        return plan
    gh.edit(target["number"], body)
    if plan.action == "persist":
        return plan
    fresh = gh.open_issue()
    if not fresh or fresh["number"] != target["number"]:
        return None
    if read_state(fresh["body"])["sources"].get(source) != plan.state["sources"].get(
        source
    ):
        return None
    if plan.comment:
        gh.comment(target["number"], plan.comment)
    if plan.action == "close":
        gh.close(target["number"])
    return plan


def run_pr_note(gh, run_id: str) -> str:
    try:
        jobs = [j for j in gh.jobs(run_id) if PR_JOB.search(j.get("name", ""))]
        failures: set[str] = set()
        for job in jobs:
            if job.get("conclusion") == "failure":
                failures.update(parse_failures(gh.log(job)))
    except (subprocess.CalledProcessError, ValueError, KeyError) as error:
        text = f"Could not read this run's failures ({_why(error)}), so it cannot be compared with `latest`."
        _summary(text)
        return text
    try:
        issue = gh.open_issue()
        if issue:
            baseline = {"issue": issue["number"], "state": read_state(issue["body"])}
        else:
            baseline = no_issue_baseline(gh.closed_issue(), gh.latest_head())
        text = pr_note(sorted(failures), baseline)
    except (subprocess.CalledProcessError, ValueError, KeyError) as error:
        text = pr_note(sorted(failures), None, _why(error))
    _summary("### Compared with latest\n" + text)
    return text


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    report = sub.add_parser("report")
    report.add_argument("--source", choices=SOURCES, required=True)
    report.add_argument("--run-id", required=True)
    report.add_argument("--sha", required=True)
    report.add_argument("--dry-run", action="store_true")
    note = sub.add_parser("pr-note")
    note.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)
    gh = GitHub(os.environ["GH_REPO"])
    if args.command == "report":
        run_report(gh, args.source, args.run_id, args.sha, args.dry_run)
        return 0
    # The note only informs; it must never turn a run red.
    try:
        run_pr_note(gh, args.run_id)
    except Exception as error:  # the note must never fail the job
        _summary(f"Unknown whether these failures also happen on `latest` ({error}).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
