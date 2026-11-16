#!/usr/bin/env python3
# ruff: noqa: T201
"""Decide whether a push to latest may reuse its pull request's green test run.

The Docker publish gate re-runs the three test shards on every push to latest.
When the push is a pull request merge and the pull request's own App Tests run
already executed on a commit with *exactly the same tree*, that second run adds
nothing. This script decides, from Git and the GitHub API only, whether that is
the case. It prints "reuse" only when every condition below holds, and "run" for
everything else, including any error. It never fails the job: a crash here must
not be able to skip tests, so the workflow treats anything but a clean "reuse"
as "run the full gate".

Identity is the Git tree, not the commit. A merge commit never equals the pull
request head, but a tree hash names the complete content of the checkout:
sources, tests, lockfile, workflows and tooling. Two commits with the same tree
give a test run byte-identical input files.

The tested tree is read from GitHub's own record of the run, not inferred from
the pull request head. A pull_request run executes on GitHub's test merge commit
(refs/pull/N/merge). The run's ``head_sha`` is the pull request head, which has
a different tree whenever the branch was behind its base. The test merge commit
is reported in ``referenced_workflows`` (present because App Tests references
the local lint workflow), as ``sha`` with ``ref: refs/pull/N/merge``. That also
ties the run to one pull request, which ``head_sha`` cannot: two pull requests
can share a head commit and target different bases.

Conditions for "reuse" (all of them):

 1. The event is a push to refs/heads/latest.
 2. The pushed commit is associated with exactly one pull request, which is
    merged into latest in this repository with this commit as its merge commit.
 3. The newest App Tests pull_request run for that pull request's head is
    completed and successful, is this repository's .github/workflows/app-tests.yml,
    and ran on refs/pull/N/merge for that same pull request.
 4. The commit that run executed is a merge whose second parent is the pull
    request head, and its tree equals the pushed commit's tree.
 5. The run's "test (3.12)" job succeeded on the latest attempt, and the run did
    not change while it was being inspected.
 6. The run started no more than MAX_AGE ago.

With REUSE_PR_TEST_RESULTS unset the script still evaluates and reports what it
would have decided, but always outputs "run" (shadow mode).

Usage: reuse_pr_tests.py    (reads GITHUB_* and GH_TOKEN from the environment)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

BASE_BRANCH = "latest"
WORKFLOW_FILE = "app-tests.yml"
WORKFLOW_PATH = f".github/workflows/{WORKFLOW_FILE}"
REQUIRED_JOB = "test (3.12)"
ENABLE_VARIABLE = "REUSE_PR_TEST_RESULTS"

# A result is a statement about a tree on the runner image, Python patch release
# and browser build of the day it ran. Hosted images roll roughly weekly. Three
# days covers a pull request that went green on Friday and merged on Monday
# while staying inside one image generation in the common case. The normal path
# (auto-merge right after the checks pass) is minutes old.
MAX_AGE = timedelta(hours=72)
CLOCK_SKEW = timedelta(minutes=5)

PAGE_SIZE = 100
SHA = re.compile(r"[0-9a-f]{40}")

REUSE = "reuse"
RUN = "run"


class NoReuseError(Exception):
    """A condition for reuse does not hold. The message is the reason."""


@dataclass
class Decision:
    """What was decided, why, and the evidence it rests on."""

    evaluated: str
    reason: str
    enabled: bool
    evidence: dict[str, str] = field(default_factory=dict)

    @property
    def decision(self) -> str:
        """Return what the workflow acts on: reuse only when it is switched on."""
        return REUSE if self.enabled and self.evaluated == REUSE else RUN


def require(condition, reason: str) -> None:
    """Stop with ``reason`` unless ``condition`` is exactly True."""
    if condition is not True:
        raise NoReuseError(reason)


def is_sha(value) -> bool:
    """Return True for a full lowercase 40-character hex object name."""
    return isinstance(value, str) and SHA.fullmatch(value) is not None


def parse_time(value) -> datetime:
    """Parse a GitHub timestamp. Anything else is a reason not to reuse."""
    require(isinstance(value, str), "run has no start time")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise NoReuseError(f"unreadable run start time {value!r}") from error
    return parsed.replace(tzinfo=UTC)


def find_pull_request(api, repo: str, pushed: str) -> dict:
    """Return the one merged pull request whose merge commit is ``pushed``."""
    associated = api(f"/repos/{repo}/commits/{pushed}/pulls", {"per_page": PAGE_SIZE})
    require(isinstance(associated, list), "unexpected pull request list")
    require(len(associated) > 0, "no pull request is associated with this commit")
    require(
        len(associated) == 1,
        f"{len(associated)} pull requests are associated with this commit",
    )
    number = associated[0].get("number")
    require(type(number) is int and number > 0, "pull request has no number")

    # The list entry is a summary. Read the pull request itself for "merged".
    pull = api(f"/repos/{repo}/pulls/{number}", {})
    require(isinstance(pull, dict), "unexpected pull request response")
    require(pull.get("number") == number, "pull request number changed")
    require(pull.get("merged") is True, f"pull request #{number} is not merged")
    base = pull.get("base") or {}
    require(
        base.get("ref") == BASE_BRANCH,
        f"pull request #{number} targets {base.get('ref')!r}, not {BASE_BRANCH}",
    )
    require(
        (base.get("repo") or {}).get("full_name") == repo,
        f"pull request #{number} targets another repository",
    )
    require(
        pull.get("merge_commit_sha") == pushed,
        f"pull request #{number} was merged as another commit",
    )
    require(
        is_sha((pull.get("head") or {}).get("sha")),
        f"pull request #{number} has no head commit",
    )
    return pull


def newest_run(api, repo: str, head: str) -> dict:
    """Return the newest App Tests pull_request run for ``head``.

    Newest wins, as it does for GitHub's own required checks: an older green
    run must not outvote a newer failure, or a newer run for another pull
    request that shares this head.
    """
    listing = api(
        f"/repos/{repo}/actions/workflows/{WORKFLOW_FILE}/runs",
        {"event": "pull_request", "head_sha": head, "per_page": PAGE_SIZE},
    )
    require(isinstance(listing, dict), "unexpected workflow run list")
    runs = listing.get("workflow_runs")
    require(isinstance(runs, list), "unexpected workflow run list")
    require(len(runs) > 0, "the pull request head has no App Tests run")
    require(
        listing.get("total_count") == len(runs),
        "too many App Tests runs for the pull request head to inspect",
    )
    numbers = [run.get("run_number") for run in runs]
    require(
        all(type(number) is int for number in numbers)
        and len(set(numbers)) == len(numbers),
        "App Tests runs cannot be ordered",
    )
    return max(runs, key=lambda run: run["run_number"])


def check_run(run: dict, repo: str, head: str) -> None:
    """Require a finished, successful App Tests pull_request run for ``head``."""
    run_id = run.get("id")
    require(type(run_id) is int and run_id > 0, "run has no id")
    require(
        run.get("event") == "pull_request", f"run {run_id} is not a pull_request run"
    )
    require(run.get("head_sha") == head, f"run {run_id} is for another head commit")
    require(run.get("path") == WORKFLOW_PATH, f"run {run_id} is another workflow")
    require(
        (run.get("repository") or {}).get("full_name") == repo,
        f"run {run_id} belongs to another repository",
    )
    require(
        run.get("status") == "completed",
        f"newest App Tests run {run_id} is {run.get('status')}",
    )
    require(
        run.get("conclusion") == "success",
        f"newest App Tests run {run_id} concluded {run.get('conclusion')}",
    )
    attempt = run.get("run_attempt")
    require(type(attempt) is int and attempt > 0, f"run {run_id} has no attempt number")


def tested_commit(run: dict, repo: str, number: int) -> str:
    """Return the test merge commit GitHub recorded for ``run``.

    Only this repository's own workflow references count, every one of them
    must name the same commit, and it must have been reached through this pull
    request's merge ref. A run that reports none (the workflow stopped calling a
    local reusable workflow) cannot be reused.
    """
    references = run.get("referenced_workflows")
    require(isinstance(references, list), "run does not report the commit it tested")
    prefix = f"{repo}/.github/workflows/"
    local = [
        reference
        for reference in references
        if isinstance(reference, dict)
        and isinstance(reference.get("path"), str)
        and reference["path"].startswith(prefix)
    ]
    require(len(local) > 0, "run does not report the commit it tested")
    shas = {reference.get("sha") for reference in local}
    require(len(shas) == 1, "run reports more than one tested commit")
    tested = shas.pop()
    require(is_sha(tested), "run reports an unreadable tested commit")
    expected_ref = f"refs/pull/{number}/merge"
    for reference in local:
        require(
            reference.get("ref") == expected_ref,
            f"run tested {reference.get('ref')!r}, not {expected_ref}",
        )
        require(
            reference["path"].endswith(f"@{tested}"),
            "run references a workflow by something other than its tested commit",
        )
    return tested


def tested_tree(api, repo: str, tested: str, head: str) -> str:
    """Return the tree of the test merge commit, after checking what it merged."""
    commit = api(f"/repos/{repo}/git/commits/{tested}", {})
    require(isinstance(commit, dict), "unexpected commit response")
    require(commit.get("sha") == tested, "tested commit could not be read back")
    parents = commit.get("parents")
    require(
        isinstance(parents, list) and len(parents) == 2,  # noqa: PLR2004
        "tested commit is not a two-parent merge",
    )
    require(
        (parents[1] or {}).get("sha") == head,
        "tested commit does not merge the pull request head",
    )
    tree = (commit.get("tree") or {}).get("sha")
    require(is_sha(tree), "tested commit has no tree")
    return tree


def check_required_job(api, repo: str, run: dict) -> None:
    """Require the aggregate test job to have passed on the latest attempt."""
    run_id = run["id"]
    listing = api(
        f"/repos/{repo}/actions/runs/{run_id}/jobs",
        {"filter": "latest", "per_page": PAGE_SIZE},
    )
    require(isinstance(listing, dict), "unexpected job list")
    jobs = listing.get("jobs")
    require(isinstance(jobs, list), "unexpected job list")
    require(
        listing.get("total_count") == len(jobs),
        f"run {run_id} has too many jobs to inspect",
    )
    required = [job for job in jobs if job.get("name") == REQUIRED_JOB]
    require(
        len(required) == 1,
        f"run {run_id} has {len(required)} jobs named {REQUIRED_JOB!r}",
    )
    job = required[0]
    require(
        job.get("run_attempt") == run["run_attempt"],
        f"{REQUIRED_JOB!r} is not from the latest attempt of run {run_id}",
    )
    require(
        job.get("status") == "completed" and job.get("conclusion") == "success",
        f"{REQUIRED_JOB!r} in run {run_id} is "
        f"{job.get('status')}/{job.get('conclusion')}",
    )


def check_unchanged(api, repo: str, run: dict) -> None:
    """Read the run again: a re-run started meanwhile invalidates what was read."""
    again = api(f"/repos/{repo}/actions/runs/{run['id']}", {})
    require(isinstance(again, dict), "unexpected workflow run response")
    for key in ("id", "status", "conclusion", "run_attempt", "head_sha"):
        require(
            again.get(key) == run.get(key),
            f"run {run['id']} changed while it was being inspected",
        )


def evaluate(env, api, git, now: datetime, evidence: dict[str, str]) -> None:
    """Raise NoReuseError unless every condition for reuse holds."""
    require(
        env.get("GITHUB_EVENT_NAME") == "push",
        f"not applicable: event is {env.get('GITHUB_EVENT_NAME')!r}",
    )
    require(
        env.get("GITHUB_REF") == f"refs/heads/{BASE_BRANCH}",
        f"not applicable: ref is {env.get('GITHUB_REF')!r}",
    )
    repo = env.get("GITHUB_REPOSITORY")
    require(
        isinstance(repo, str)
        and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) is not None,
        "no repository",
    )
    pushed = env.get("GITHUB_SHA")
    require(is_sha(pushed), "no pushed commit")
    evidence["pushed commit"] = pushed

    # The checkout this job (and every later job) builds from must be the push.
    require(git("rev-parse", "HEAD") == pushed, "checkout is not the pushed commit")
    pushed_tree = git("rev-parse", f"{pushed}^{{tree}}")
    require(is_sha(pushed_tree), "pushed commit has no tree")
    evidence["pushed tree"] = pushed_tree

    pull = find_pull_request(api, repo, pushed)
    number = pull["number"]
    head = pull["head"]["sha"]
    evidence["pull request"] = f"#{number}"
    evidence["pull request head"] = head

    run = newest_run(api, repo, head)
    evidence["run"] = f"{run.get('id')} (attempt {run.get('run_attempt')})"
    check_run(run, repo, head)

    tested = tested_commit(run, repo, number)
    evidence["tested commit"] = tested
    tree = tested_tree(api, repo, tested, head)
    evidence["tested tree"] = tree
    require(
        tree == pushed_tree,
        "the tested tree differs from the pushed tree",
    )

    check_required_job(api, repo, run)

    started = parse_time(run.get("run_started_at"))
    evidence["run started"] = run["run_started_at"]
    age = now - started
    require(age >= -CLOCK_SKEW, "the run started in the future")
    require(
        age <= MAX_AGE,
        f"the run is older than {int(MAX_AGE.total_seconds() // 3600)} hours",
    )

    check_unchanged(api, repo, run)


def decide(env, api, git, now: datetime) -> Decision:
    """Evaluate every condition. Anything unexpected means "run"."""
    enabled = env.get(ENABLE_VARIABLE) == "true"
    evidence: dict[str, str] = {}
    try:
        evaluate(env, api, git, now, evidence)
    except NoReuseError as error:
        return Decision(RUN, str(error), enabled, evidence)
    except Exception as error:  # fail closed on anything at all
        return Decision(
            RUN, f"error: {type(error).__name__}: {error}", enabled, evidence
        )
    return Decision(
        REUSE,
        "the pull request's green run tested the pushed tree",
        enabled,
        evidence,
    )


def one_line(text: str) -> str:
    """Flatten ``text`` so it cannot add lines to a step output or a table."""
    return re.sub(r"[\x00-\x1f\x7f|]+", " ", str(text)).strip()[:400]


def summary(decision: Decision) -> str:
    """Return the Markdown job summary."""
    lines = ["## Reuse of the pull request test result", ""]
    if decision.decision == REUSE:
        lines.append("**Decision: reuse.** The test shards are skipped.")
    elif decision.evaluated == REUSE:
        lines.append(
            "**Decision: run** (shadow mode). Every condition for reuse held, "
            f"but `{ENABLE_VARIABLE}` is not `true`, so the full gate runs."
        )
    else:
        lines.append("**Decision: run.** The full gate runs.")
    lines += ["", f"Reason: {one_line(decision.reason)}", ""]
    if decision.evidence:
        lines += ["| Evidence | Value |", "| --- | --- |"]
        lines += [
            f"| {one_line(key)} | `{one_line(value)}` |"
            for key, value in decision.evidence.items()
        ]
    return "\n".join(lines) + "\n"


# Reasons that mean GitHub stopped describing runs the way this script relies
# on, rather than an ordinary "this push was not tested". Reuse then stops,
# which is safe, but on every merge, so it is raised as a warning on the run
# instead of only sitting in the job summary.
CANARY_REASONS = (
    "run does not report the commit it tested",
    "run reports more than one tested commit",
    "run reports an unreadable tested commit",
    "run references a workflow by something other than its tested commit",
    "unexpected ",
    "error: ",
)


def warning(decision: Decision) -> str | None:
    """Return a workflow warning when a pull request merge failed for a canary."""
    if decision.evaluated == REUSE or "pull request" not in decision.evidence:
        return None
    if not decision.reason.startswith(CANARY_REASONS):
        return None
    return (
        "::warning title=PR test result not reused::"
        f"{one_line(decision.reason)}. The full gate ran. If this repeats on every "
        "merge, GitHub changed what it reports for workflow runs; see "
        "scripts/reuse_pr_tests.py."
    )


def github_api(base_url: str, token: str):
    """Return a function that GETs one GitHub API path and parses the JSON."""

    def call(path: str, params: dict):
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        request = urllib.request.Request(  # noqa: S310 - base_url is checked in main
            f"{base_url}{path}{query}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "reuse-pr-tests",
            },
        )
        # No retries: an API that is failing is a reason to run the tests.
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return json.load(response)

    return call


def run_git(*args: str) -> str:
    """Run git in the checkout and return its trimmed output."""
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout.strip()


def main() -> int:
    """Write the decision to the step output and the job summary."""
    env = dict(os.environ)
    base_url = env.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    if not base_url.startswith("https://"):
        api = None
    else:
        api = github_api(base_url, env.get("GH_TOKEN", ""))
    try:
        decision = decide(env, api, run_git, datetime.now(UTC))
        text = summary(decision)
    except Exception as error:  # never let a crash skip tests
        decision = Decision(RUN, f"error: {type(error).__name__}", enabled=False)
        text = "## Reuse of the pull request test result\n\n**Decision: run.**\n"

    print(text)
    print(f"decision={decision.decision}")
    alert = warning(decision)
    if alert:
        print(alert)
    try:
        if env.get("GITHUB_OUTPUT"):
            with Path(env["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
                output.write(f"decision={decision.decision}\n")
                output.write(f"evaluated={decision.evaluated}\n")
                output.write(f"reason={one_line(decision.reason)}\n")
        if env.get("GITHUB_STEP_SUMMARY"):
            with Path(env["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as output:
                output.write(text)
    except OSError as error:
        # Without the output the workflow sees no "reuse" and runs the tests.
        print(f"could not write the decision: {error}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
