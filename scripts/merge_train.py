#!/usr/bin/env python3
# ruff: noqa: T201
"""Keep approved pull requests up to date with latest so auto-merge can land them.

latest requires a pull request to be up to date before it merges, and GitHub's
merge queue is not available on a personal repository. Auto-merge alone then stalls:
after any merge every other waiting PR is behind and nothing updates it. This script
is the queue. It only ever does one thing to a PR: merge latest into its branch
(GitHub's "Update branch"), which restarts that PR's own CI. GitHub's native
auto-merge then lands whichever up-to-date PR goes green first. Nothing else is
created, merged or pushed.

PRs with auto-merge switched on (the owner's approval) run in two lanes:

- normal: up to MERGE_TRAIN_DEPTH (default 3) PRs are refreshed and tested at the
  same time. A PR already running or green on the current latest never holds the
  others back. Free slots go to PRs that are already green on an older latest,
  earliest green first, then to the rest in approval order; a PR waiting longer
  than FAIRNESS_AFTER counts as ready so it cannot starve. A previously failed PR
  is retried once per new latest, last.
- migration: any PR that changes src/*/migrations/ (added, deleted or renamed, or
  a diff too large to inspect) is refreshed one at a time in approval order, as
  before, and has to pass migration-order against the latest it merges into.

When one PR merges, every other in-flight PR is behind again; the next run
refreshes it against the new latest (the strict rule makes that retest
mandatory, so a stale green result can never merge).

"Behind" is decided by comparing the PR head with the current latest commit, not by
GitHub's mergeStateStatus, which reads UNKNOWN while it recalculates and BLOCKED
while checks run. A PR that cannot merge as it is (conflict, failed or hung required
check, checks that never started, a fork branch the token may not update) is skipped
with the reason, so it cannot hold the queue; its author has to act.

The run exits 0 when a PR was updated, one is in flight, or the queue is empty.
An unmodifiable contributor fork is an expected manual handoff, not a broken
automation: the contributor must update it, and the warning stays visible. A
stalled queue with merge conflicts or failed checks still exits 1.

Usage: merge_train.py [--dry-run]    (needs GH_TOKEN and GH_REPO)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
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
MIGRATION_PATH = re.compile(r"^src/[^/]+/migrations/")
COMPARE_FILE_LIMIT = 300  # GitHub's compare API lists at most this many files
DEFAULT_DEPTH = 3
NORMAL, MIGRATION = "normal", "migration"

QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    ref(qualifiedName: "refs/heads/latest") { target { ... on Commit { oid committedDate } } }
    pullRequests(states: OPEN, baseRefName: "latest", first: 100) {
      nodes {
        id
        number
        body
        headRefName
        author { login }
        createdAt
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
        if since_commit > HUNG_AFTER:
            return "skip", "some required checks never appeared within the CI timeout"
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
        # A refusal (":refused", written by older versions) means no refresh
        # happened, so it does not use up the one retry for this base.
        if marker and marker[0] == latest_oid and marker[1] != "refused":
            return "skip", "branch refresh already attempted for this base; failed checks still need attention"
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


@dataclass
class Plan:
    """What one run should do.

    ``ranked`` lists, per lane, the PRs to refresh in order; ``free`` is how many
    of them may start now.
    """

    ranked: dict[str, list[dict]] = field(default_factory=dict)
    free: dict[str, int] = field(default_factory=dict)
    in_flight: int = 0
    queued: int = 0
    manual_only: bool = True  # every skip so far is a fork permission handoff
    notes: list[str] = field(default_factory=list)


def _ready_at(pr: dict, now: datetime) -> datetime | None:
    """Return when this PR counts as ready, or None while it is not.

    Ready means green on all required checks, or approved for longer than
    FAIRNESS_AFTER (so an unproven PR cannot starve).
    """
    if _all_required_passed(pr):
        return max(_time(c["completedAt"]) for c in _checks(pr, REQUIRED_CHECKS))
    enabled = _time(pr["autoMergeRequest"]["enabledAt"])
    if now - enabled >= FAIRNESS_AFTER:
        return enabled
    return None


def _rank(pr: dict, lane: str, now: datetime) -> tuple:
    enabled = _time(pr["autoMergeRequest"]["enabledAt"])
    if _old_non_migration_failure(pr):
        return (2, enabled, pr["number"])
    if lane == MIGRATION:  # migrations keep strict approval order
        return (0, enabled, pr["number"])
    ready = _ready_at(pr, now)
    return (0, ready, pr["number"]) if ready else (1, enabled, pr["number"])


def _manual_skip(pr: dict, reason: str) -> bool:
    """Report whether a skip is only a fork permission boundary.

    That is a healthy manual handoff. Failed checks, conflicts, API errors and
    unknown states remain actionable automation failures.
    """
    return pr["isCrossRepository"] and "fork branch that maintainers may not update" in reason


def plan(
    prs: list[dict],
    behind: dict[int, int | None],
    migrating: dict[int, bool],
    now: datetime,
    depth: int = DEFAULT_DEPTH,
) -> Plan:
    """Sort the approved PRs into in flight, to refresh (ranked per lane) and skipped.

    A PR whose migration status is unknown goes to the migration lane: failing
    closed only costs it parallelism.
    """
    queue = sorted(
        (p for p in prs if not p["isDraft"] and p["autoMergeRequest"]),
        key=lambda p: (p["autoMergeRequest"]["enabledAt"], p["number"]),
    )
    result = Plan(queued=len(queue), manual_only=bool(queue))
    capacity = {NORMAL: max(1, depth), MIGRATION: 1}
    flying = {NORMAL: 0, MIGRATION: 0}
    waiting: dict[str, list[dict]] = {NORMAL: [], MIGRATION: []}
    for pr in queue:
        number = pr["number"]
        lane = MIGRATION if migrating.get(number, True) else NORMAL
        verdict, reason = judge(pr, behind.get(number), now)
        if verdict == "skip":
            result.notes.append(f"#{number} skipped: {reason}")
            if not _manual_skip(pr, reason):
                result.manual_only = False
        elif verdict == "wait":
            flying[lane] += 1
            result.notes.append(f"#{number} in flight ({lane} lane): {reason}")
        else:
            pr["_reason"] = reason
            waiting[lane].append(pr)
    result.in_flight = sum(flying.values())
    for lane in (NORMAL, MIGRATION):
        result.ranked[lane] = sorted(waiting[lane], key=lambda p: _rank(p, lane, now))
        result.free[lane] = max(0, capacity[lane] - flying[lane])
    return result


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


def _touches_migrations(paths: list[str]) -> bool:
    return any(MIGRATION_PATH.match(path) for path in paths)


def _compare(owner: str, name: str, latest: str, head: str) -> tuple[int | None, bool]:
    """Return (commits on latest the head lacks, whether the PR changes migrations).

    The diff is latest...head, the PR's own changes. Rename sources count as well
    as destinations. A diff GitHub truncates is treated as changing migrations.
    On an API error behind is None (the PR is skipped and retried next run).
    """
    try:
        data = json.loads(
            _gh(
                "api",
                f"repos/{owner}/{name}/compare/{latest}...{head}",
                "--jq",
                "{behind: .behind_by, files: [.files[]? | .filename, (.previous_filename // empty)],"
                " count: (.files | length)}",
            )
        )
        behind = int(data["behind"])
    except (subprocess.CalledProcessError, ValueError, KeyError, TypeError):
        return None, True
    truncated = data.get("count", 0) >= COMPARE_FILE_LIMIT
    return behind, truncated or _touches_migrations(data.get("files") or [])


def _github_message(stderr: str) -> str:
    """GitHub's own reason from gh's error output (no credentials appear there)."""
    first = next((line for line in stderr.splitlines() if line.strip()), "")
    return first.removeprefix("gh: ").strip()[:200]


def _refusal(stderr: str) -> str:
    if "HTTP 403" in stderr:
        return (
            "GitHub will not let the token update the head repository "
            f"(GitHub said: {_github_message(stderr)}); retrying on the next run. "
            "If it keeps failing, the contributor has to merge latest into the branch"
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




def _depth() -> int:
    try:
        return max(1, min(10, int(os.environ.get("MERGE_TRAIN_DEPTH") or DEFAULT_DEPTH)))
    except ValueError:
        return DEFAULT_DEPTH


class UpdateError(Exception):
    """GitHub refused a branch update in a way the owner has to look at."""


def _update(owner: str, name: str, pr: dict) -> str | None:
    """Merge latest into the PR branch.

    Return None on success, or a skip line when GitHub refuses a contributor
    fork (a manual handoff). Raise UpdateError for refusals that are real errors.
    """
    number = pr["number"]
    try:
        _gh(
            "api", "-X", "PUT", f"repos/{owner}/{name}/pulls/{number}/update-branch",
            "-f", f"expected_head_sha={pr['headRefOid']}",
        )
    except subprocess.CalledProcessError as error:
        # Nothing was refreshed, so nothing is recorded: the PR is retried on
        # the next run instead of being parked until latest moves again.
        failure = f"#{number} skipped: {_refusal(error.stderr.strip())}"
        if pr["isCrossRepository"] and "HTTP 403" in error.stderr:
            return failure
        raise UpdateError(failure) from error
    return None


def main(argv: list[str]) -> int:
    """Fetch the open PRs and refresh every one that has a free slot in its lane."""
    dry_run = "--dry-run" in argv
    owner, name = os.environ["GH_REPO"].split("/")
    prs, latest = _fetch(owner, name)
    # Ignore CI events for unrelated or superseded PR heads.
    ci_head = os.environ.get("CI_HEAD")
    if ci_head and not any(
        p["headRefOid"] == ci_head and not p["isDraft"] and p["autoMergeRequest"]
        for p in prs
    ):
        _report(["CI event is not on a current approved PR head; ignored"], False)
        return 0
    for _ in range(UNKNOWN_RETRIES):
        if not unsettled(prs):
            break
        time.sleep(UNKNOWN_WAIT_SECONDS)
        prs, latest = _fetch(owner, name)
    behind: dict[int, int | None] = {}
    migrating: dict[int, bool] = {}
    for p in prs:
        if not p["isDraft"] and p["autoMergeRequest"]:
            behind[p["number"]], migrating[p["number"]] = _compare(
                owner, name, latest, p["headRefOid"]
            )
    result = plan(prs, behind, migrating, datetime.now(UTC), _depth())
    lines = list(result.notes)
    updated = 0
    errors = False
    for lane in (NORMAL, MIGRATION):
        free = result.free[lane]
        for pr in result.ranked[lane]:
            number = pr["number"]
            if free == 0:
                lines.append(f"#{number} waiting for a free {lane} slot ({pr['_reason']})")
                continue
            if dry_run:
                lines.append(f"dry run: would update #{number} ({lane} lane): {pr['_reason']}")
                free -= 1
                updated += 1
                continue
            try:
                refused = _update(owner, name, pr)
            except UpdateError as error:
                lines.append(str(error))
                errors = True
                continue
            if refused:
                lines.append(refused)
                continue
            lines.append(f"#{number} updated ({lane} lane): merged latest into its branch; {pr['_reason']}")
            free -= 1
            updated += 1
    advancing = bool(updated or result.in_flight)
    stuck = bool(result.queued) and not advancing and not result.manual_only
    problem = errors or stuck
    _report(lines or ["queue empty"], problem)
    return 1 if problem else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
