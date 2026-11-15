# Merge train throughput: cumulative integration candidates

`latest` stays strict (lint, test (3.12), migration-order from GitHub Actions,
up-to-date branches). GitHub's merge queue needs an organization, so the train
(`scripts/merge_train.py`) is the queue. Without it every approved PR needs its
own full CI cycle after each merge. The accelerated lane builds one cumulative
candidate from up to `MERGE_TRAIN_DEPTH` (default 3) approved, green PRs and
tests it once.

## Who is eligible

Any open, non-draft PR with native auto-merge on its current head, a known
`MERGEABLE` state, behind `latest`, and all three required checks passing. Owner
or contributor, fork or not, makes no difference, and nobody has to enable
"Allow edits from maintainers" or update their branch. The `auto-merge` label
only mirrors GitHub's state. Real conflicts and failed checks still block.

A PR that is current and running or green is about to merge by itself and would
move `latest` under a candidate, so no candidate is staged until `latest` is
stable. Everything else is ranked independently (oldest approval first), so a
red, stale or unmodifiable PR never holds up green work. A lone fork PR becomes
a one-member candidate (no write to the fork needed); a lone same-repo PR
uses the serial branch update, which costs the same. The scheduler logs why each
PR was or was not batched.

## How a candidate is built

In a throwaway workspace (hooks, global config and merge drivers off; nothing
from the sources is executed or sourced) the train fetches `latest` and each
source at `refs/pull/<n>/head`, requires that SHA to equal the approved head,
and merges the heads with `--no-ff` onto the pinned `latest` SHA. Conflicting
members are left out. Only the new `merge-train/batch-*` ref is pushed; source
branches and `latest` are never written. It then verifies the base and every
member head are ancestors, and that the cumulative diff (renames counted as
delete + add) stays inside the members' own changes and is safe. A staging ref
left by an interrupted run is reused only if it passes the same checks.

The candidate PR's body carries a manifest: base SHA, ordered members with head
SHAs, candidate head and tree. It is trusted only when the PR is in this
repository, authored by the train's account, its branch name matches the manifest
digest, and its head equals the recorded head. A contributor cannot forge one.

## What never enters the lane

Anything touching `src/*/migrations/` (additions, deletions, renames in or out,
non-numbered files), `.github/`, `scripts/merge_train.py`,
`scripts/check_migration_order.py`, `scripts/reuse_pr_tests.py` or
`scripts/test.sh`. Those merge serially after normal review with
`migration-order` and the Django migration tests. Duplicate migration numbers
within an app are rejected by `check_migration_order.py`; because candidates are
migration-free, two migration PRs cannot land together through the lane, and
candidates and serial updates both run against the current `latest`.

## Trust boundary for contributor code

A candidate is a same-repository PR, so GitHub does not apply the fork rule
(no secrets, read-only token) to it. Safety comes from what those workflows
are able to do, and is enforced by `test_workflow_security`:

- Every workflow triggered by `pull_request` declares least-privilege permissions;
  the only write grants (`docker-image` build `packages: write`, CodeQL
  `security-events: write`) are skipped for `merge-train/batch-*` heads. Neither
  is a required check and every member already ran both as its own PR.
- No job that runs on a candidate references a repository secret other than the
  token, and none gets OIDC. `AUTO_MERGE_TOKEN` appears only in trusted
  workflows that never check out PR code (`merge-train.yml` at `latest`,
  `owner-auto-merge.yml`, which skips candidates).
- Workflow definitions that run on the candidate are those on `latest`, because
  `.github/` changes cannot be in a candidate (the diff is verified).
- Caches written by a PR run are scoped to that PR's ref and cannot poison
  `latest`'s; `push` workflows are limited to `latest`/`release`, so pushing a
  staging branch starts nothing.

A bot-owned fork would be a stronger boundary but needs a new account and
credential; this is not required by the design above. If the workflow audit
ever fails, disable the lane with `MERGE_TRAIN_BATCH=false`.

## Promotion and cancellation

The train never enables auto-merge at creation. When a candidate's three
checks pass on its exact head, `latest` still equals the manifest base, and every
member still has auto-merge on its recorded head, the train re-fetches state and
enables native auto-merge (with `expectedHeadOid`). If GitHub refuses because the
checks are already complete, it merges that exact head with a `sha`-pinned
request after the same re-check. The account that does this is the owner's
token, which can bypass rules, so the checks above (current base, CLEAN merge
state, head match) are what keep it from acting as a bypass; there is a short
window between re-check and merge that GitHub's strict rule closes if `latest`
moves.

The approval snapshot is each member's auto-merge on its exact head at staging
and again at promotion. Revocation after that instant cannot be made atomic
with the merge. Closing the candidate is the cancellation mechanism. Retired
for: base moved, source head or approval changed, failed check, 2 hour timeout,
batching disabled, policy unverifiable (checked via the runner's own credential
against `rules/branches/latest`, with the reason logged). Failed candidates are
tagged in their body; the same group of (PR, head) pairs is never restaged, and
members are retried separately, so one bad member cannot stall the others.

Candidates merge with a merge commit, so GitHub closes each source PR by
ancestry with the author's commits intact. This was verified only with local
git until the first real candidate merges; see the pull request for receipts.

## Controls and wakeups

`MERGE_TRAIN_BATCH=false` disables the lane and retires active candidates.
`MERGE_TRAIN_DEPTH` sets the size (1 to 5). The train wakes on pushes to `latest`,
PR events, and every finished required-check run on a pull request; a
single-slot concurrency group collapses bursts. Dry run and the 15 minute
schedule remain for recovery. Record per candidate: PRs merged, queue wait,
runner minutes.
