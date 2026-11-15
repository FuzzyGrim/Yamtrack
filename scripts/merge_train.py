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

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
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
        if marker and marker[0] == latest_oid:
            if marker[1] == "refused":
                return "skip", "GitHub refused the branch update (HTTP 403 or equivalent); the contributor has to merge latest into the branch themselves"
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



# Accelerated lane. Any approved, green, migration-free PR (owner or contributor
# fork alike) can be merged into a cumulative candidate branch in this
# repository; one ordinary PR then validates the exact combined tree and
# merges it with a merge commit, so GitHub closes every source PR by ancestry.
# Source branches are never written to: sources are fetched as data from
# refs/pull/<n>/head, pinned to the approved head SHA. Migration, workflow and
# CI-plumbing changes always stay in the serial lane. See
# docs/architecture/merge-train-throughput.md for the trust model.
BATCH_PREFIX = "merge-train/batch-"
BATCH_MARKER = re.compile(r"<!-- merge-train-batch: (\{[^\n]+\}) -->")
RESULT_MARKER = "<!-- merge-train-result: failed -->"
MIGRATION_PATH = re.compile(r"^src/[^/]+/migrations/")
SHA = re.compile(r"[0-9a-f]{40}")
# CI plumbing: changes here could alter how the candidate is judged, so they
# are merged serially after normal review, never inside a batch.
SERIAL_ONLY = {
    "scripts/merge_train.py",
    "scripts/check_migration_order.py",
    "scripts/reuse_pr_tests.py",
    "scripts/test.sh",
}
CLEAN_STATES = {"CLEAN", "UNSTABLE", "HAS_HOOKS"}
FAILED_LOOKBACK = 30  # recent closed candidates remembered as failed manifests
GIT_TIMEOUT = 300
GIT_BASE = "https://github.com"
FILE_PROTOCOL = "never"  # tests point GIT_BASE at throwaway local repos


def _is_batch(pr: dict) -> bool:
    # A fork cannot create a branch in this repository, so a same-named fork
    # branch is just an ordinary source PR.
    return pr.get("headRefName", "").startswith(BATCH_PREFIX) and not pr.get("isCrossRepository")


def _batch_safe_files(paths: list[str]) -> bool:
    """True when no path (rename sources and destinations included) is protected."""
    return not any(
        MIGRATION_PATH.match(path) or path.startswith(".github/") or path in SERIAL_ONLY
        for path in paths
    )


def _batch_manifest(latest: str, prs: list[dict]) -> dict:
    return {"base": latest, "members": [
        {"number": p["number"], "sha": p["headRefOid"]} for p in prs
    ]}


def _batch_branch(manifest: dict) -> str:
    identity = {"base": manifest["base"], "members": manifest["members"]}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return f"{BATCH_PREFIX}{manifest['base'][:12]}-{digest[:24]}"


def _read_batch(pr: dict, owner: str) -> dict | None:
    """Parse a candidate this automation created; anything else is untrusted."""
    if not _is_batch(pr) or (pr.get("author") or {}).get("login") != owner:
        return None
    match = BATCH_MARKER.search(pr.get("body") or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
        members = data["members"]
        if not all(SHA.fullmatch(data[k]) for k in ("base", "head", "tree")):
            return None
        if not 1 <= len(members) <= 5 or len({m["number"] for m in members}) != len(members):
            return None
        if any(type(m["number"]) is not int or m["number"] <= 0
               or not SHA.fullmatch(m["sha"]) for m in members):
            return None
        if _batch_branch(data) != pr["headRefName"] or pr["headRefOid"] != data["head"]:
            return None
        return data
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def _batch_eligible(p: dict, behind: dict) -> bool:
    """A source member: origin does not matter, only a green approved snapshot."""
    return bool(
        not _is_batch(p)
        and not p["isDraft"]
        and p["autoMergeRequest"]
        and p["mergeable"] == "MERGEABLE"
        and (behind.get(p["number"]) or 0) > 0
        and _all_required_passed(p)
    )


def _batch_valid(data: dict, prs: list[dict], latest: str) -> bool:
    by_number = {p["number"]: p for p in prs}
    if data["base"] != latest:
        return False
    for member in data["members"]:
        p = by_number.get(member["number"])
        if (not p or p["headRefOid"] != member["sha"] or p["isDraft"]
                or not p["autoMergeRequest"]):
            return False
    return True


def _base_stable(originals: list[dict], behind: dict, now: datetime) -> str | None:
    """Return why latest is about to move, or None. An up-to-date PR that is
    running or green will merge on its own and would invalidate any candidate."""
    for p in originals:
        if p["isDraft"] or not p["autoMergeRequest"] or behind.get(p["number"]) != 0:
            continue
        if judge(p, 0, now)[0] == "wait":
            return f"#{p['number']} is current and about to merge; latest is not stable yet"
    return None


def _select(originals: list[dict], behind: dict, failed_sets: list[frozenset]) -> list[dict]:
    """Eligible sources, oldest approval first (fairness), independent of any
    serial choice, so a red, stale or unmodifiable PR never gates green work."""
    return sorted(
        (p for p in originals if _batch_eligible(p, behind)),
        key=lambda p: (p["autoMergeRequest"]["enabledAt"], p["number"]),
    )


def _failed_group(group: set, failed_sets: list[frozenset]) -> bool:
    """A group that contains a manifest which already failed is never restaged."""
    return any(f <= group for f in failed_sets)


def _api(repo: str, endpoint: str, *args: str):
    raw = _gh("api", f"repos/{repo}/{endpoint}", *args)
    return json.loads(raw) if raw.strip() else None


_batch_api = _api


def _git_env(token: str) -> dict:
    """Environment for git on untrusted data: no hooks, global config or prompts.
    The token travels in the environment, never in argv, and is never logged."""
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": "/nonexistent",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": f"Authorization: basic {basic}",
        "GIT_AUTHOR_NAME": "Floppy merge train",
        "GIT_AUTHOR_EMAIL": "merge-train@users.noreply.github.com",
        "GIT_COMMITTER_NAME": "Floppy merge train",
        "GIT_COMMITTER_EMAIL": "merge-train@users.noreply.github.com",
    }


def _git(ws: str, env: dict, *args: str, check: bool = True) -> str:
    # core.hooksPath and fsmonitor are neutralised and no merge driver or
    # filter is configured, so contributor .gitattributes cannot run anything.
    result = subprocess.run(  # noqa: S603
        ["git", "-c", f"core.hooksPath={os.devnull}", "-c", "core.fsmonitor=false",  # noqa: S607
         "-c", f"protocol.file.allow={FILE_PROTOCOL}", "-c", "submodule.recurse=false", *args],
        cwd=ws, env=env, capture_output=True, text=True, check=False, timeout=GIT_TIMEOUT,
    )
    if check and result.returncode:
        raise subprocess.CalledProcessError(
            result.returncode, "git", result.stdout, _scrub(result.stderr, env)
        )
    return result.stdout.strip()


def _ok(ws: str, env: dict, *args: str) -> bool:
    try:
        _git(ws, env, *args)
    except subprocess.CalledProcessError:
        return False
    return True


def _scrub(text: str, env: dict) -> str:
    return text.replace(env["GIT_CONFIG_VALUE_0"], "***")


def _changed(ws: str, env: dict, *spec: str) -> list[str]:
    # --no-renames reports a rename as delete + add, so both sides are checked.
    out = _git(ws, env, "diff", "--name-only", "--no-renames", "-z", *spec)
    return [x for x in out.split("\0") if x]


def _verify_candidate(ws: str, env: dict, data: dict, head: str) -> str | None:
    """Return a problem with the candidate, or None. Reads git data only."""
    if _git(ws, env, "rev-parse", f"{head}^{{tree}}") != data["tree"]:
        return "tree does not match the manifest"
    for sha in (data["base"], *(m["sha"] for m in data["members"])):
        if not _ok(ws, env, "merge-base", "--is-ancestor", sha, head):
            return f"{sha[:8]} is not an ancestor of the candidate"
    actual = set(_changed(ws, env, data["base"], head))
    expected = set()
    for m in data["members"]:
        expected |= set(_changed(ws, env, f"{data['base']}...{m['sha']}"))
    if not _batch_safe_files(sorted(actual)):
        return "cumulative diff touches migrations, workflows or CI plumbing"
    if not actual <= expected:
        return "cumulative diff contains paths no member changed"
    return None


def _stage(repo: str, latest: str, candidates: list[dict], depth: int,
           failed_sets: list[frozenset], notes: list[str]) -> dict | None:
    """Build the cumulative merge commits in a throwaway workspace and push only
    the new staging ref. Returns the manifest (with head and tree) or None."""
    env = _git_env(os.environ["GH_TOKEN"])
    url = f"{GIT_BASE}/{repo}.git"
    with tempfile.TemporaryDirectory(prefix="merge-train-") as ws:
        _git(ws, env, "init", "-q", f"--template={os.devnull}")
        _git(ws, env, "fetch", "-q", "--no-tags", url, "refs/heads/latest:refs/remotes/o/latest")
        if _git(ws, env, "rev-parse", "refs/remotes/o/latest") != latest:
            notes.append("latest moved while staging; will retry on the next event")
            return None
        _git(ws, env, "checkout", "-q", "--detach", latest)
        members: list[dict] = []
        for p in candidates[: depth * 2]:
            if len(members) == depth:
                break
            n, sha = p["number"], p["headRefOid"]
            if _failed_group({(m["number"], m["sha"]) for m in members} | {(n, sha)}, failed_sets):
                notes.append(f"#{n} not batched: its group already failed CI")
                continue
            try:
                _git(ws, env, "fetch", "-q", "--no-tags", url, f"refs/pull/{n}/head:refs/pr/{n}")
            except subprocess.CalledProcessError as error:
                notes.append(f"#{n} not batched: could not fetch its head ({error.stderr.strip()[:120]})")
                continue
            if _git(ws, env, "rev-parse", f"refs/pr/{n}") != sha:
                notes.append(f"#{n} not batched: head moved since approval")
                continue
            if not _batch_safe_files(_changed(ws, env, f"{latest}...{sha}")):
                notes.append(f"#{n} not batched: touches migrations, workflows or CI plumbing (serial lane)")
                continue
            if not _ok(ws, env, "merge", "--no-ff", "--no-edit", "-q", "-m",
                       f"Merge train: pull request #{n} at {sha[:12]}", sha):
                _git(ws, env, "merge", "--abort", check=False)
                notes.append(f"#{n} not batched: conflicts with latest or an earlier member")
                continue
            members.append({"number": n, "sha": sha})
        if not members:
            return None
        if len(members) == 1 and not next(
            p for p in candidates if p["number"] == members[0]["number"]
        )["isCrossRepository"]:
            notes.append("only one compatible source and it can be updated in place; serial update is equivalent")
            return None
        head = _git(ws, env, "rev-parse", "HEAD")
        data = {"base": latest, "members": members, "head": head,
                "tree": _git(ws, env, "rev-parse", "HEAD^{tree}")}
        branch = _batch_branch(data)
        existing = _git(ws, env, "ls-remote", url, f"refs/heads/{branch}")
        if existing:
            # Orphan staging ref from an interrupted run: reuse only if it is a
            # valid candidate for this exact manifest.
            _git(ws, env, "fetch", "-q", "--no-tags", url, f"refs/heads/{branch}:refs/o/orphan")
            head = _git(ws, env, "rev-parse", "refs/o/orphan")
            data["head"], data["tree"] = head, _git(ws, env, "rev-parse", f"{head}^{{tree}}")
        problem = _verify_candidate(ws, env, data, head)
        if problem:
            notes.append(f"candidate rejected: {problem}")
            return None
        if not existing:
            _git(ws, env, "push", "-q", url, f"{head}:refs/heads/{branch}")
        return data


def _close_batch(repo: str, pr: dict, reason: str, failed: bool = False) -> None:
    # Closing prevents further native auto-merge; never close source PRs.
    # A failed candidate is remembered in its body so the same group is not retried.
    args = ["-X", "PATCH", "-f", "state=closed"]
    if failed:
        args += ["-f", f"body={(pr.get('body') or '').rstrip()}\n\n{RESULT_MARKER}"]
    _api(repo, f"pulls/{pr['number']}", *args)
    _report([f"#{pr['number']} retired batch: {reason}"], False)


def _failed_manifests(repo: str, owner: str) -> list[frozenset]:
    closed = _api(repo, f"pulls?state=closed&base=latest&sort=updated&direction=desc&per_page={FAILED_LOOKBACK}")
    out = []
    for p in closed or []:
        if (p["head"]["ref"].startswith(BATCH_PREFIX) and not p.get("merged_at")
                and p["user"]["login"] == owner and RESULT_MARKER in (p.get("body") or "")):
            match = BATCH_MARKER.search(p["body"])
            if match:
                with suppress(ValueError, KeyError):
                    out.append(frozenset(
                        (m["number"], m["sha"]) for m in json.loads(match.group(1))["members"]
                    ))
    return out


def _batch_policy(repo: str) -> str | None:
    """Return None when strict protection is verified on latest, else why not.
    Reads the effective rules with the runner's own credential (read access)."""
    try:
        rules = _api(repo, "rules/branches/latest")
    except subprocess.CalledProcessError as error:
        return f"could not read branch rules ({error.stderr.strip()[:100]})"
    checks = [r["parameters"] for r in rules if r["type"] == "required_status_checks"]
    if not checks or not all(c.get("strict_required_status_checks_policy") for c in checks):
        return "latest does not require up-to-date branches"
    required = {(c["context"], c.get("integration_id")) for p in checks
                for c in p["required_status_checks"]}
    if required != {(name, 15368) for name in REQUIRED_CHECKS}:
        return "required checks on latest are not exactly lint, test (3.12), migration-order from GitHub Actions"
    return None


def _promote(repo: str, owner: str, name: str, p: dict, data: dict, dry_run: bool) -> str:
    """Enable native auto-merge on a candidate only after a final re-validation.
    Source approval is the snapshot taken here: auto-merge on each member's exact
    head SHA. Revocation after this instant cannot be made atomic with the merge."""
    if p["autoMergeRequest"]:
        return "auto-merge already enabled; waiting for GitHub to merge"
    if not _all_required_passed(p) or p["mergeStateStatus"] not in CLEAN_STATES:
        return "validating the exact combined tree"
    if dry_run:
        return "dry run: would promote this candidate"
    fresh, base = _fetch(owner, name)
    cur = next((x for x in fresh if x["number"] == p["number"]), None)
    if (not cur or cur["headRefOid"] != data["head"] or not _batch_valid(data, fresh, base)
            or cur["mergeStateStatus"] not in CLEAN_STATES):
        return "state changed during promotion; re-evaluating on the next event"
    try:
        _gh("api", "graphql", "-f", f"id={p['id']}", "-f", "query=mutation($id: ID!) {"
            " enablePullRequestAutoMerge(input: {pullRequestId: $id, mergeMethod: MERGE,"
            " expectedHeadOid: \"" + data["head"] + "\"}) { pullRequest { number } } }")
        return "promoted: native auto-merge enabled"
    except subprocess.CalledProcessError as error:
        if "clean status" not in error.stderr.lower():
            raise
    # Checks already complete: GitHub refuses auto-merge, so merge the exact
    # head directly. Everything the rules require was just verified above.
    _api(f"{owner}/{name}", f"pulls/{p['number']}/merge", "-X", "PUT",
         "-f", "merge_method=merge", "-f", f"sha={data['head']}")
    return "promoted: merged the verified candidate"


def _reconcile_batch(owner: str, name: str, prs: list[dict], latest: str,
                     behind: dict, now: datetime, dry_run: bool) -> int | None:
    """Return None to continue with the serial lane. Never pushes latest."""
    repo = f"{owner}/{name}"
    originals = [p for p in prs if not _is_batch(p)]
    active = [p for p in prs if _read_batch(p, owner)]
    notes: list[str] = []
    try:
        problem = None if os.environ.get("MERGE_TRAIN_BATCH") == "true" else "MERGE_TRAIN_BATCH is not true"
        problem = problem or _batch_policy(repo)
        if problem:
            notes.append(f"batching off: {problem}")
        failed_sets = _failed_manifests(repo, owner) if not problem or active else []
        healthy = None
        for p in active:
            data = _read_batch(p, owner)
            failed = _failure_reason(p, now)
            reason = (problem or ("more than one candidate" if healthy or len(active) > 1 else None)
                      or (None if _batch_valid(data, originals, latest)
                          else "base moved or a source head or approval changed")
                      or failed
                      or ("timed out" if now - _time(p["createdAt"]) > HUNG_AFTER else None))
            if reason and dry_run:
                notes.append(f"dry run: would retire #{p['number']}: {reason}")
            elif reason:
                _close_batch(repo, p, reason, failed=bool(failed))
                if failed:
                    failed_sets.append(frozenset((m["number"], m["sha"]) for m in data["members"]))
            else:
                healthy = (p, data)
        if healthy:
            p, data = healthy
            notes.append(f"#{p['number']} {_promote(repo, owner, name, p, data, dry_run)}")
            _report(notes, False)
            return 0
        if problem or len(prs) >= 100:
            _report(notes, False)
            return None
        why = _base_stable(originals, behind, now)
        if why:
            notes.append(f"no candidate staged: {why}")
            _report(notes, False)
            return None
        candidates = _select(originals, behind, failed_sets)
        if not candidates:
            return None
        depth = max(1, min(5, int(os.environ.get("MERGE_TRAIN_DEPTH", "3"))))
        if dry_run:
            listed = ", ".join(f"#{p['number']}" for p in candidates[:depth])
            _report([*notes, f"dry run: would stage a candidate from {listed}"], False)
            return 0
        data = _stage(repo, latest, candidates, depth, failed_sets, notes)
        if not data:
            _report(notes or ["no candidate staged; using the serial train"], False)
            return None
        branch = _batch_branch(data)
        if _api(repo, f"pulls?state=all&base=latest&head={owner}:{branch}&per_page=1"):
            notes.append("this exact group was already attempted; using the serial train")
            _report(notes, False)
            return None
        fresh, base = _fetch(owner, name)
        if not _batch_valid(data, fresh, base):
            _report([*notes, "state changed while staging; not opening a candidate"], False)
            return 0
        numbers = ", ".join(f"#{m['number']}" for m in data["members"])
        body = (
            f"Automated cumulative integration of approved snapshots: {numbers}. Each member "
            "was fetched at its exact approved head; no source branch was modified. Do not "
            "merge or update this branch by hand. Native auto-merge is enabled by the merge "
            "train only after the exact tree passes the required checks.\n\n"
            f"<!-- merge-train-batch: {json.dumps(data, sort_keys=True)} -->"
        )
        created = _api(repo, "pulls", "-X", "POST", "-f", "base=latest", "-f", f"head={branch}",
                       "-f", f"title=Merge train batch: {numbers}", "-f", f"body={body}")
        _report([*notes, f"#{created['number']} launched cumulative CI for {numbers}"], False)
        return 0
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError, KeyError) as error:
        # An active candidate may still merge: never start another integration
        # until reconciliation succeeds.
        _report([*notes, f"Batch reconciliation stopped: {type(error).__name__}"], bool(active))
        return 1 if active else None


def main(argv: list[str]) -> int:
    """Fetch the open PRs, pick one, and update its branch unless this is a dry run."""
    dry_run = "--dry-run" in argv
    owner, name = os.environ["GH_REPO"].split("/")
    prs, latest = _fetch(owner, name)
    # Ignore CI events for unrelated or superseded PR heads.
    failed_head = os.environ.get("CI_HEAD")
    if failed_head and not any(
        p["headRefOid"] == failed_head
        and not p["isDraft"]
        and (p["autoMergeRequest"] or _is_batch(p))
        for p in prs
    ):
        _report(["CI event is not on a current approved PR head; ignored"], False)
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
    if os.environ.get("MERGE_TRAIN_BATCH") == "true" or any(_is_batch(p) for p in prs):
        result = _reconcile_batch(owner, name, prs, latest, behind, datetime.now(UTC), dry_run)
        if result is not None:
            return result
    prs = [p for p in prs if not _is_batch(p)]
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
