# Merge train throughput: cumulative integration batches

The train retains the strict latest ruleset (lint, test (3.12), migration-order)
and native GitHub auto-merge. The batch lane combines up to three previously
green stale owner-authored, approved, same-repo PRs into one ordinary
integration PR. Original head SHAs are preserved as ancestors, so GitHub can
recognize indirect merges. All required checks run on the combined tree in
parallel as usual, once per batch rather than once per original PR. The
original branches are not force-pushed.

Migration files (including renames from a migration directory), workflow
changes and merge-train policy files are excluded from batches. The actual
combined diff is independently checked for migration files. Migration changes
and contributor forks stay in the existing serial lane, where the original
migration-order gate remains required. Do not relax the strict ruleset.

The candidate is bound to the latest SHA and original approved head SHAs.
Source changes, approval withdrawal, base advancement, failure or timeout
cause the train to retire the candidate on its next reconciliation and fall
back to serial. Native auto-merge is asynchronous, so source approval changes
cannot be made atomic with a candidate's merge; its creation is approval of
the immutable snapshot, and closing the candidate directly is the cancellation
mechanism. Never use the administrator-token immediate merge fallback on a
batch. Do not manually merge or update batch branches.

Depth defaults to three (MERGE_TRAIN_DEPTH, range 2–5). Set the repository
variable MERGE_TRAIN_BATCH=false to disable and retire active batches. Strict
ruleset verification fails closed to serial. Existing in-flight ordinary
PRs are allowed to finish before a batch starts. The serial scheduler remains
responsible for conflicts and failed required checks. No synthetic checks
or direct pushes to latest are used.

This is one cumulative batch, not multiple speculative prefix PRs. It
deliberately spends one integrated suite per batch. If the combined test
fails, the ordinary train identifies the failing original individually.
Track queue wait, batch size, failure rate, merged PRs per hour, and runner
minutes. Confirm indirect PR closure and native auto-merge on the first
real batch before considering the rollout validated.
