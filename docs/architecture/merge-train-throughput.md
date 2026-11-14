# Merge train throughput: speculative validation follow-up

The current train preserves strict up-to-date branch checks and GitHub native
auto-merge. Prefer current green/in-flight PRs, then refresh stale PRs with an
aging rule. A failed required PR workflow wakes the train.

A real merge queue must test immutable cumulative trees, not simply update
several PR heads against the same base. With candidates A, B, C, validate
latest+A, latest+A+B and latest+A+B+C in parallel (initial depth 3). If A
changes, fails or is removed, invalidate all descendants; if B fails, invalidate
B and C only. Track base SHA, ordered PR numbers and head SHAs, candidate tree
SHA, run IDs and conclusions. Fence promotion by re-reading those identities
and the rules.

Do not mark required checks successful based on an unrelated candidate SHA,
disable strict branch protection, run untrusted code with the merge token, or
merge a candidate directly to latest using a privileged bypass. Native
auto-merge does not automatically consume a synthetic candidate's checks.
Establish how GitHub can accept the exact validated cumulative tree without
losing PR attribution or violating required-check rules before implementing
promotion. Otherwise retain the serial train or use a supported native queue.

Benchmark real queue traces: ready-to-merge wait, throughput, runner minutes,
discarded speculative runs and failures. Roll out behind a flag; test head/base
changes, concurrent merges, permission refusals, conflicts, duplicate events,
queue reconstruction and failed ancestors before enabling.
