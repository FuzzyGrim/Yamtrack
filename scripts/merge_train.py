#!/usr/bin/env python3
# ruff: noqa: T201
"""Keep pull requests up to date with latest, one at a time, so auto-merge can land them.

latest requires a pull request to be up to date before it merges, and GitHub's
merge queue is not available on a personal repository. Auto-merge alone then stalls:
after any merge every other waiting PR is behind and nothing updates it. This script
is the queue. Among open PRs that have auto-merge switched on (the owner's approval),
it prefers already-current green or running candidates, then stale green
branches over newer unproven branches. Older non-failing candidates eventually
get FIFO priority; previously failed checks eligible for a refresh go last:

- behind latest: merge latest into it (this restarts CI, and auto-merge lands it),
- up to date and its checks are running or about to merge: it is the one in flight
  and the others wait their turn.

"Behind" is decided by comparing the PR head with the current latest commit, not by
GitHub's mergeStateStatus, which reads UNKNOWN while it recalculates and BLOCKED
while checks run. A PR that cannot merge as it is (conflict, failed or hung required
check, checks that never started, a fork branch the token may not update) is skipped
with the reason, so it cannot hold the queue; its author has to act.

The run exits 0 when a PR was updated, one is in flight, or the queue is empty.
An unmodifiable contributor fork is also an expected manual handoff, not a
broken automation: the contributor must update it, and the warning stays visible.
A stalled queue with merge conflicts or failed checks still exits 1.

Usage: merge_train.py [--dry-run]    (needs GH_TOKEN and GH_REPO)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

REQUIRED_CHECKS = {"lint", "test (3.12)", "migration-order"}
PARKING_CHECKS = REQUIRED_CHECKS
FAILED = {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}
PASSED = {"SUCCESS", "SKIPPED", "NEUTRAL"}
HUNG_AFTER = timedelta(hours=2)  # a required check this old and still pending is stuck
# An up-to-date PR with no required check runs this long after its last commit never
# got CI (for example a change CI ignores); one green this long that GitHub still has
# not merged is stuck on something else. Either would hold the queue forever.
STALLED_AFTER = timedelta(minutes=30)
FAIRNESS_AFTER = timedelta(hours=2)  # Bound bypasses of non-failing stale PRs.
UNKNOWN_RETRIES = 3  # GitHub recalculates mergeability on request; ask again
UNKNOWN_WAIT_SECONDS = 10
ATTEMPT_MARKER = re.compile(r"<!-- merge-train-base:([0-9a-f]{40})(?::(refused))? -->")

QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    ref(qualifiedName: "refs/heads/latest") { target { ... on Commit { oid committedDate } } }
    pullRequests(states: OPEN, baseRefName: "latest", first: 100) {
      nodes {
        number
        body
        isDraft
        isCrossRepository
        maintainerCanModify
        mergeable
        mergeStateStatus
        headRefOid
        autoMergeRequest { enabledAt }
        commits(last: 1) { nodes { commit { committedDate statusCheckRollup { contexts(first: 100) {
          nodes { ... on CheckRun { name status conclusion startedAt completedAt } }
        } } } } }
      }
    }
  }
}
"""


def _commit(pr: dict) -> dict:
    return pr["commits"]["nodes"][0]["commit"]


def _checks(pr: dict, names: set[str]) -> list[dict]:
    rollup = _commit(pr)["statusCheckRollup"] or {"contexts": {"nodes": []}}
    return [c for c in rollup["contexts"]["nodes"] if c.get("name") in names]


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _failure_reason(pr: dict, now: datetime) -> str | None:
    """Return why a check or a conflict parks this PR, or None."""
    if pr["mergeStateStatus"] == "DIRTY" or pr["mergeable"] == "CONFLICTING":
        return "merge conflict with latest; its author has to resolve it"
    for check in _checks(pr, PARKING_CHECKS):
        if check["conclusion"] in FAILED:
            return f"check '{check['name']}' failed; its author has to fix it"
    for check in _checks(pr, REQUIRED_CHECKS):
        pending = check["status"] != "COMPLETED" and check["startedAt"]
        if pending and now - _time(check["startedAt"]) > HUNG_AFTER:
            return f"required check '{check['name']}' has been pending for hours"
    return None


def _old_non_migration_failure(pr: dict) -> bool:
    latest_date = pr.get("_latest_date")
    if not latest_date:
        return False
    failures = [
        check
        for check in _checks(pr, REQUIRED_CHECKS - {"migration-order"})
        if check.get("conclusion") in FAILED
    ]
    return bool(failures) and all(
        check.get("completedAt") and _time(check["completedAt"]) < _time(latest_date)
        for check in failures
    )


def _attempt_marker(pr: dict) -> tuple[str, str | None] | None:
    match = ATTEMPT_MARKER.search(pr.get("body") or "")
    return (match.group(1), match.group(2)) if match else None


def _all_required_passed(pr: dict) -> bool:
    checks = _checks(pr, REQUIRED_CHECKS)
    return {c["name"] for c in checks} == REQUIRED_CHECKS and all(
        c["status"] == "COMPLETED" and c["conclusion"] in PASSED for c in checks
    )


def _up_to_date_verdict(pr: dict, now: datetime) -> tuple[str, str]:
    """Judge a PR whose head already contains latest: in flight, or stuck."""
    checks = _checks(pr, REQUIRED_CHECKS)
    since_commit = now - _time(_commit(pr)["committedDate"])
    if len({c["name"] for c in checks}) < len(REQUIRED_CHECKS):
        if not checks and since_commit > STALLED_AFTER:
            return "skip", (
                "required checks never started on its latest commit (a change CI "
                "ignores, such as docs only, needs a manual merge)"
            )
        return "wait", "required checks are queued or running"
    if _all_required_passed(pr):
        finished = max(_time(c["completedAt"]) for c in checks)
        if now - finished > STALLED_AFTER:
            return "skip", (
                f"required checks passed but GitHub still reports "
                f"{pr['mergeStateStatus']} (a review, rule or conversation may be "
                "holding it)"
            )
        return "wait", "all required checks passed; GitHub should merge it"
    return "wait", "required checks are running"


def judge(pr: dict, behind: int | None, now: datetime) -> tuple[str, str]:
    """Return (verdict, reason) for one queued PR: 'update', 'wait' or 'skip'."""
    if pr["mergeStateStatus"] == "DIRTY" or pr["mergeable"] == "CONFLICTING":
        return "skip", "merge conflict with latest; its author has to resolve it"
    if any(
        c.get("name") == "migration-order" and c.get("conclusion") in FAILED
        for c in _checks(pr, {"migration-order"})
    ):
        return "skip", "required check 'migration-order' failed; migration conflicts need manual reconciliation"
    if behind is None:
        return "skip", "could not tell whether it is behind latest; will retry"
    latest_oid = pr.get("_latest_oid")
    marker = _attempt_marker(pr)
    old_failure = _old_non_migration_failure(pr)
    if behind > 0 and old_failure:
        if marker and marker[0] == latest_oid:
            if marker[1] == "refused":
                return "skip", "GitHub refused the branch update (HTTP 403 or equivalent); the contributor has to merge latest into the branch themselves"
            return "wait", "branch update was attempted for this latest commit; waiting for fresh required checks"
        return "update", f"required check failed before latest advanced; refreshing {behind} commits"
    reason = _failure_reason(pr, now)
    if reason:
        return "skip", reason
    if behind == 0:
        return _up_to_date_verdict(pr, now)
    if pr["isCrossRepository"] and not pr["maintainerCanModify"]:
        return "skip", (
            f"{behind} commits behind latest on a fork branch that maintainers "
            "may not update; the contributor has to merge latest into it or "
            "enable 'Allow edits from maintainers'"
        )
    return "update", f"{behind} commits behind latest"


def choose(
    prs: list[dict], behind: dict[int, int | None], now: datetime
) -> tuple[str, int | None, list[str]]:
    """Return (action, pr number, notes): 'update', 'wait', 'manual', 'stuck' or 'idle'.

    'manual' means only contributor forks inaccessible to this token were
    skipped; 'stuck' indicates a genuine unresolved problem. notes has one line
    per PR looked at, saying what was decided and why.
    """
    queue = sorted(
        (p for p in prs if not p["isDraft"] and p["autoMergeRequest"]),
        key=lambda p: p["autoMergeRequest"]["enabledAt"],
    )
    notes: list[str] = []
    chosen: tuple[str, int] | None = None
    manual_only = bool(queue)
    # Evaluate every candidate; FIFO is the tie-breaker within a priority.
    decisions = [
        (pr, *judge(pr, behind.get(pr["number"]), now)) for pr in queue
    ]

    def priority(item: tuple[dict, str, str]) -> int:
        pr, verdict, _ = item
        if verdict == "wait":
            if behind.get(pr["number"]) == 0 and _all_required_passed(pr):
                return 0
            return 1  # Avoid invalidating an integration run already in flight.
        if _old_non_migration_failure(pr):
            return 5
        if now - _time(pr["autoMergeRequest"]["enabledAt"]) >= FAIRNESS_AFTER:
            return 2
        if _all_required_passed(pr):
            return 3
        return 4

    candidates = [item for item in decisions if item[1] != "skip"]
    if candidates:
        selected, verdict, _ = min(candidates, key=priority)
        chosen = (verdict, selected["number"])
    for pr, verdict, reason in decisions:
        number = pr["number"]
        if verdict == "skip":
            notes.append(f"#{number} skipped: {reason}")
            # Only an external fork permission boundary is a healthy manual
            # handoff. Failed checks, conflicts, API errors and unknown states
            # remain actionable automation failures.
            if not (
                pr["isCrossRepository"]
                and (
                    "fork branch that maintainers may not update" in reason
                    or "GitHub refused the branch update (HTTP 403 or equivalent)" in reason
                )
            ):
                manual_only = False
        elif chosen and number == chosen[1]:
            notes.append(f"#{number} {verdict}: {reason}")
        elif chosen:
            notes.append(f"#{number} queued behind #{chosen[1]}")
    if chosen:
        return chosen[0], chosen[1], notes
    if not queue:
        return "idle", None, notes
    return ("manual" if manual_only else "stuck"), None, notes


def unsettled(prs: list[dict]) -> bool:
    """Report whether GitHub is still calculating mergeability for a queued PR."""
    return any(
        p["mergeable"] == "UNKNOWN"
        for p in prs
        if not p["isDraft"] and p["autoMergeRequest"]
    )


def _gh(*args: str) -> str:
    return subprocess.run(  # noqa: S603
        ["gh", *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _fetch(owner: str, name: str) -> tuple[list[dict], str]:
    data = json.loads(
        _gh(
            "api",
            "graphql",
            "-f",
            f"owner={owner}",
            "-f",
            f"name={name}",
            "-f",
            f"query={QUERY}",
        )
    )["data"]["repository"]
    latest = data["ref"]["target"]
    prs = data["pullRequests"]["nodes"]
    for pr in prs:
        pr["_latest_oid"] = latest["oid"]
        pr["_latest_date"] = latest.get("committedDate")
    return prs, latest["oid"]


def _record_attempt(owner: str, name: str, pr: dict, refused: bool = False) -> None:
    body = pr.get("body") or ""
    marker = f"<!-- merge-train-base:{pr['_latest_oid']}{':refused' if refused else ''} -->"
    body = ATTEMPT_MARKER.sub("", body).rstrip()
    updated = f"{body}\n\n{marker}" if body else marker
    _gh(
        "api", "-X", "PATCH", f"repos/{owner}/{name}/pulls/{pr['number']}",
        "-f", f"body={updated}",
    )


def _behind_by(owner: str, name: str, latest: str, head: str) -> int | None:
    """Count commits on latest that the PR head lacks (None if GitHub won't say)."""
    try:
        return int(
            _gh(
                "api",
                f"repos/{owner}/{name}/compare/{latest}...{head}",
                "--jq",
                ".behind_by",
            )
        )
    except (subprocess.CalledProcessError, ValueError):
        return None


def _refusal(stderr: str) -> str:
    if "HTTP 403" in stderr:
        return (
            "GitHub will not let the token update the head repository (HTTP 403); "
            "the contributor has to merge latest into the branch themselves"
        )
    if "HTTP 422" in stderr or "HTTP 409" in stderr:
        return f"merge conflict while updating; its author has to resolve it: {stderr}"
    return f"GitHub refused to update it: {stderr}"


def _report(lines: list[str], problem: bool) -> None:
    """Print the decisions, and append them to the Actions job summary."""
    for line in lines:
        skipped = " skipped: " in line
        print(f"::warning::{line}" if skipped else line)
    if problem:
        print("::error::Nothing advanced: every PR in the queue is stuck (see above).")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary and lines:
        with Path(summary).open("a", encoding="utf-8") as handle:
            handle.write("### Merge train\n" + "\n".join(f"- {x}" for x in lines))
            handle.write("\n")


def main(argv: list[str]) -> int:
    """Fetch the open PRs, pick one, and update its branch unless this is a dry run."""
    dry_run = "--dry-run" in argv
    owner, name = os.environ["GH_REPO"].split("/")
    prs, latest = _fetch(owner, name)
    # Ignore failures on unrelated or superseded PR heads.
    failed_head = os.environ.get("FAILED_CI_HEAD")
    if failed_head and not any(
        p["headRefOid"] == failed_head
        and not p["isDraft"]
        and p["autoMergeRequest"]
        for p in prs
    ):
        _report(["CI failure is not on a current approved PR head; ignored"], False)
        return 0
    for _ in range(UNKNOWN_RETRIES):
        if not unsettled(prs):
            break
        time.sleep(UNKNOWN_WAIT_SECONDS)
        prs, latest = _fetch(owner, name)
    behind = {
        p["number"]: _behind_by(owner, name, latest, p["headRefOid"])
        for p in prs
        if not p["isDraft"] and p["autoMergeRequest"]
    }
    failed: list[str] = []  # PRs GitHub refused to update this run
    critical_failures: list[str] = []
    while True:
        action, number, notes = choose(prs, behind, datetime.now(UTC))
        lines = [x for x in notes if not x.startswith(f"#{number} update:")] + failed
        if action != "update":
            problem = action == "stuck" or bool(critical_failures)
            _report(lines or ["queue empty"], problem)
            return 1 if problem else 0
        if dry_run:
            _report([*lines, f"dry run: would update the branch of #{number}"], False)
            return 0
        pr = next(p for p in prs if p["number"] == number)
        retrying_failed_checks = _old_non_migration_failure(pr)
        if retrying_failed_checks:
            try:
                _record_attempt(owner, name, pr)
            except subprocess.CalledProcessError as error:
                failure = (
                    f"#{number} skipped: could not record the one-time update attempt: "
                    f"{error.stderr.strip()}"
                )
                failed.append(failure)
                critical_failures.append(failure)
                prs = [p for p in prs if p["number"] != number]
                continue
        try:
            _gh(
                "api", "-X", "PUT", f"repos/{owner}/{name}/pulls/{number}/update-branch",
                "-f", f"expected_head_sha={pr['headRefOid']}",
            )
        except subprocess.CalledProcessError as error:
            if retrying_failed_checks:
                with suppress(subprocess.CalledProcessError):
                    _record_attempt(owner, name, pr, refused=True)
            failure = f"#{number} skipped: {_refusal(error.stderr.strip())}"
            failed.append(failure)
            if not (pr["isCrossRepository"] and "HTTP 403" in error.stderr):
                critical_failures.append(failure)
            prs = [p for p in prs if p["number"] != number]
            continue
        _report([*lines, f"#{number} updated: merged latest into its branch"], False)
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
