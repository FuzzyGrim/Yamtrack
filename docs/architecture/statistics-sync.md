# Statistics sync

How the Statistics page stays warm, why it is built this way, and what to check
when it looks wrong. Replaces the chunked "refresh run" design (#1272).

## The promise

The user never waits for Statistics and never sees a refresh banner. Every
change marks what it touched; a background sync rebuilds that and republishes
the ranges before the page is next opened. The page always serves the last
published numbers. Only two things show progress:

* the **Refresh** button, and
* a range that has **never** been built (fresh install, new user), which shows
  "Building your statistics for the first time…" once and reloads when ready.

A small "Updated N minutes ago" next to the Refresh button is the only other
indicator.

## Two layers

| Layer | Unit | Where | Rebuilt when |
| --- | --- | --- | --- |
| Day payload | one user, one local day | cache (`stats:day:v7:{uid}:{day}`) | the day is dirty, missing, or today/yesterday |
| Range snapshot | one user, one predefined range | database (`StatisticsSnapshot`) + cache copy | its generation trails the user's, or it was built on an earlier local day |

Day payloads are derived and rebuildable, so they stay in the cache. A range
payload also reads live database state (undated items, current status and
score, reading completions, talent), which is why *any* change makes every
range stale while only the days it touched need rebuilding.

## Durable state (`app/models/statistics.py`)

* `StatisticsDirtyDay(user, day, token)` — one row per dirty day. Marking is an
  upsert that rewrites the token. A sync clears only rows whose token still
  matches what it read, so a day re-marked mid-rebuild stays dirty.
* `StatisticsSyncState(user)` — `generation` counts changes; the synced
  generations, `synced_day`, the lease and `full_sweep_requested_at` let one
  query find unfinished work.
* `StatisticsSnapshot(user, range_name)` — the published payload, stored
  dehydrated: model rows become references re-fetched on read
  (`statistics_sync.dehydrate_payload` / `hydrate_payload`).

Why the database: the cache is `volatile-lru` and shares its budget with
provider payloads (`app/redis_tuning.py`). The old design kept the dirty list,
the freshness token, the run lock and the published payloads there, so an
eviction dropped invalidations or blanked a page, and the dirty list was a
non-atomic read-modify-write. `BackfillReconcileState` moved out of the cache
for the same reason (#521).

## Marking (`app/statistics_sync.py`)

* `mark_days(user_id, days)` — upserts dirty rows, drops those day payloads,
  bumps the generation, and queues a sync on commit.
* `mark_aggregate(user_id)` — a change no day captures (undated items, status,
  score, credits — talent is aggregated per range from the database). Bumps the
  generation only; no day is rebuilt.
* `mark_rows(user_id, rows)` — for bulk writes that skip signals.

Credits are the one field marked through `request_credits_statistics_refresh`
instead of `mark_aggregate` directly. A credits backfill drain completes a batch
about every minute, and marking every batch kept the sync running back to back
for the whole backfill against the same single SQLite writer (#1706). Credits
marks are coalesced to `STATISTICS_CREDITS_MARK_SECONDS` (default 600, the
heavy-range max delay, so heavy ranges are not made due more often than today).
The first batch marks immediately; later batches schedule one trailing
`app.tasks.mark_credits_statistics_refresh`, so the last credits written are
still published even after the backfill stops.

The older names still work and route here: `invalidate_statistics_days` →
`mark_days`, `invalidate_statistics_cache` and `schedule_all_ranges_refresh` →
`mark_aggregate`, `invalidate_all_statistics_days` → drop every day payload and
request a full sweep (imports, provider migrations, preference changes).

## The sync

`statistics_sync_task(user_id)` runs `run_sync` on the background `celery`
queue at `CELERY_TASK_PRIORITY_STATISTICS_SYNC = 1`: ahead of follow-up imports
and backfills (3+) on that worker. Ordinary syncs are bulk derived work, so
they never share the interactive worker's only slot with webhook scrobbles or a
manual refresh. Urgent requests (a manual Refresh, a range that has never been
built) override the queue to `interactive`, because the user is waiting. On the
minimal tier one worker consumes every queue, so priority ordering still
separates them; on the other tiers the processes do.

When an interactive browser request is active, the background task yields before
claiming its database lease or between day slices and ranges. The reconciler
finds deferred work after browsing quiets down. Snapshot publication uses one
database upsert per range to avoid a read-then-write lock upgrade.

1. Claim the database lease (a sync already running → return).
2. Read the generation and the dirty rows (with tokens).
3. Build days, newest first, in prefetched slices of
   `STATISTICS_SYNC_SLICE_DAYS`: dirty days, today and yesterday, and on a full
   sweep every day payload the cache is missing (a missing
   `stats:sync:day_epoch:{uid}` key, written without a TTL, means the cache was
   flushed). Clear each slice's dirty rows as it lands.
4. Publish ranges that trail: the hot ranges (Today … Last 30 Days) on every
   pass — a scrobble reaches them in seconds. The heavy ranges (Last 90 Days …
   All Time, the user's default first among them) wait until changes have been
   quiet for `STATISTICS_SYNC_HEAVY_SETTLE_SECONDS` (90 s) but never trail by
   more than `STATISTICS_SYNC_HEAVY_MAX_DELAY_SECONDS` (10 min); a new day, a
   full sweep or a manual refresh makes them due at once. On a large library
   one All Time aggregate takes ~15 s and cannot be split, which is why it is
   not rebuilt on every pass while changes keep arriving.
   Each range builds any calendar day it has never built with one prefetch per
   slice first.
5. Record progress, release the lease, and queue another pass if changes landed
   meanwhile.

**It never aborts.** A snapshot built at generation *g* is still newer than the
one it replaces; a change during the sync simply causes another pass. The old
design aborted on every version change and needed a settling window to stop
restarting in a loop.

**It is bounded.** After `STATISTICS_SYNC_TASK_BUDGET_SECONDS` (10 s) it stops
at the next slice or range, queues its own continuation and returns, so a range
aggregate cannot hold a worker indefinitely and the same SQLite writer is not
occupied for a whole All Time rebuild.

A range aggregate cannot stop partway, so the budget is enforced by choosing
what to start: each finished range records its cost (`stats:sync:range_seconds`),
and a task starts a further range only if it is expected to fit in what is left.
The first range a task builds always runs, so a range dearer than a whole budget
still gets a task of its own. At each slice and range boundary the sync also
looks at the broker and yields when a webhook-priority (0) task is queued: the
worker cannot be preempted, so this is how a background sync steps aside
between slices while webhook work is waiting on the same SQLite writer. The
worst case is therefore the budget plus one range.
A sync more than 5 s over budget logs `stats_sync_overrun` naming its slowest
range; `stats_sync` carries `days` and `days_ms` for the day-building phase,
and each `stats_range_summary` carries `missing_days` and `days_ms` so the
range's own day-building can be told apart from its aggregation. A range that
is slow with `missing_days=0` is aggregation work, not a cold cache.
`phases_ms` then says which part: wall milliseconds per phase of the aggregate
as `phase:ms` pairs (`day_fetch` and `day_merge` are the walk over the day
payloads, the rest is the finish), and `days_decoded` is how many day payloads
it read.

Ranges of one pass share an in-memory memo of lookups that do not depend on
the range: the History index and the History days the highlight cards read,
and the tracker rows the cards show (loaded by `_fetch_media_objects`, and the
top-rated ones annotated with their progress totals). Nested ranges show many
of the same rows, so each row is loaded and annotated once per pass and every
range gets its own copy of it, related item included: a range's per-card
attributes (`aggregated_score`) never reach another range, and the rows the
memo keeps are never handed out. It lives for the pass only: it is never
cached and never part of a snapshot, and a pass still stops at any range
boundary. A range that follows others in a pass is cheaper than the same range
built first, so its recorded `stats:sync:range_seconds` can understate what it
costs at the start of a continuation; the first range of a task always runs,
so this can only let one range start a little late, never stop one starting.

Those rows are narrow. The cards read a few item columns (title, image, ids,
numbers), so every place that loads a row a range payload keeps defers the
wide item columns in `history_cache_utils.HISTORY_UNREAD_ITEM_FIELDS`
(`watch_providers` alone is ~146 KiB a TMDB title): `_fetch_media_objects`,
`hydrate_payload`'s `_fetch_instances`, and `stats._annotate_top_rated_media`,
including the season and episode items it prefetches under a show. The one
exception is a manual anime, manga or comic in the top-rated cards, loaded
whole because its progress total is read from its custom details. A deferred
column that something does read comes back with one query per row, so
`test_statistics_narrow_rows` fails on any such query while a range is built
or rendered. The change needs no cache or schema bump: the snapshot format
(references) is unchanged, and an entry cached with whole rows before an
upgrade renders exactly as a narrow one.

The ranges do not share their walk over the day payloads. On the profiled
library (3,414 active days) the walks of every bounded range together cost
about a quarter of a second of CPU per heavy pass, against several seconds for
the rest; sharing them would mean freezing about thirty accumulators at every
range boundary, and the calendar ranges (This Week, This Month, This Year) do
not nest with the rolling ones.

The highlights ("on this day") read the History day cache and keep any day they
had to build, so a History cache that keeps losing days (see the coverage repair
reasons in the log) also makes every Statistics range slower. The "today in
history" release candidate scan cannot use a date index - it matches a
month/day across every year - and a range asks for it once per media type, so
its handful of matching items is cached for
`statistics_highlights.TODAY_RELEASE_CACHE_TIMEOUT` (5 min). Every range and
media type of a sync pass then shares one scan; the random card choice is still
made per range.

## Lease fencing — an expired worker is inert

The lease carries a fencing token (`StatisticsSyncState.lease_token`), rotated
on every claim. Every state-touching operation is fenced on it:

* **Claim** (`_claim_lease`) is one atomic `UPDATE` that installs a fresh token;
  only a free-or-expired lease (or an explicit takeover) can win it.
* **Renew** (`_renew_lease`) and **release** only match the caller's token, so
  an expired worker can neither extend a successor's lease nor release it and
  admit a third sync while the successor runs.
* **Publication** (`publish_snapshot`) runs its ownership renewal and its
  snapshot write inside one transaction, and the write itself refuses to move a
  range to an older generation (`generation__lte` update, insert on miss,
  `IntegrityError` on a lost insert race). Redis publication is not atomic
  with database commit. `load_snapshot` checks the durable generation,
  publication time and schema version before accepting a cache entry, so a
  delayed write or outer transaction rollback cannot expose the wrong
  revision. A warm read adds one indexed metadata query; the payload column
  is fetched only on a miss. Equal-generation rebuilds are distinguished by
  publication time. Status pollers also read only durable metadata; a separate
  Redis metadata copy could otherwise describe a rolled-back publication.
* **Day publication** renews ownership inside a transaction after building
  each slice, holding the state-row write lock through the cache writes and
  dirty-day clearing. A worker that lost its lease during the expensive
  build cannot overwrite its successor's days. The full-sweep epoch marker
  uses the same fence.
  This deliberately includes Redis I/O in the writer transaction: a delayed
  cache publication blocks other SQLite writers. Moving that publication
  outside the transaction is unsafe with the current shared day keys:
  a takeover can publish newer days before the old Redis write arrives, and
  a subsequent lease check cannot undo that overwrite. Snapshot publication
  avoids this race through durable revision validation; day readers do not
  have that protocol.
* **Markers** (`hot/heavy_synced_generation`, `synced_day`) advance
  monotonically (`Greatest`) and only under the caller's token, so a stale pass
  cannot regress what a successor recorded; the full-sweep clear is likewise
  fenced and bounded by the pass's start time.

A worker that loses its lease gets `status="lost_lease"` and stops touching the
user's state; the inline read path treats that like `busy` and leaves durable
work for the successor. Dirty-day clearing also checks the captured dirty
token so a newer invalidation remains queued. **Drain all old workers before
upgrading and restart them together after migration 0196.** Old workers do
not rotate or honor the fencing token; mixed-version execution does not have
the new ownership guarantees.

Regression: `app/tests/test_statistics_lease_fencing.py` drives claim/renew/
release/publication/marker schedules deterministically, including the
successor-arrives-mid-range case.

## The reconciler — why a lost message cannot strand a page

`Reconcile statistics sync` is published every 60 s on the default `celery`
queue, with no queue override in its route. A page does not await this periodic
sweep; viewer-requested urgent syncs choose the interactive lane independently.
Background-worker saturation can delay periodic recovery until capacity returns.
The sweep finds users
whose lease is free and who have dirty rows, an unsynced generation, a pending
full sweep, overdue heavy ranges, or a `synced_day` before today — and queues a
sync for each. That covers every way a sync message can go missing (a dropped
or starved message, a worker restart, a second consumer on the same Redis) and
also midnight rollover and the first build after an upgrade: users with no
state row are bootstrapped with a full sweep.

Beat gives each new reconciler message a 60-second publication lifetime
(`options.expire_seconds` for Django's database scheduler). The source-owned
canonical entry explicitly clears any previous absolute expiry (`expires=None`)
in favor of that rolling lifetime; its persisted enabled/disabled state is
preserved. These fields rely on the configured Django DatabaseScheduler, and
apply only to this canonical entry, leaving differently named legacy rows alone.
When the background worker resumes, Celery discards expired ticks before running their database
queries; the next fresh tick still finds durable unfinished work. Redis may
retain expired envelopes until a consumer discards them. This applies to new
publications; older messages without an expiry keep their original lifetime.
It does not detect duplicate Beat producers or remove legacy periodic schedules.
Routing changes also affect new publications only: previously queued messages
retain their original queue and expiry, and remain registered for consumption.

The reconciler does not publish a second per-user sync message while the last one
is still waiting. A successful enqueue writes `stats:sync:pending:{uid}`; the task
deletes it when it starts, and the marker expires after the lease window
(`STATISTICS_SYNC_LEASE_SECONDS`) if delivery was lost before that. So a busy
worker sees one queued sync per user instead of one per 60 s tick. A lost
publication becomes eligible for recovery when its marker expires and a fresh
tick can run. The 5-second
interactive gate still coalesces rapid marks; the scheduled path uses the
pending marker because a tick interval outlives the gate.

## The read path

`statistics_cache.get_statistics_data` loads the snapshot (cache, then
database). Stale → `ensure_sync` (gated, idempotent) and serve it anyway. None
→ an empty payload with `statistics_building=True`, and an urgent sync.
`/api/cache-status/` reports the snapshot and never touches a running sync.

Eager/test mode (`CELERY_TASK_ALWAYS_EAGER` or `TESTING`) has no worker, so the
read path rebuilds the requested range inline and `ensure_sync` is a no-op.
Custom ranges and the comparison-period minutes build their missing days the
way the sync does (`statistics_cache._eager_day_builder` →
`statistics_sync._build_days`, one range-wide prefetch per slice), not one day
at a time; the day payloads and stored rows are the same
(`app/tests/test_statistics_eager_day_builds.py`). An empty library's page went
from about 9,000 queries to under 500. The prefetch also carries audiobook
listening minutes, so a prefetched day issues no query of its own. Tests that exercise the real queueing override both settings and patch
`app.tasks_interactive.statistics_sync_task.apply_async`
(`app/tests/test_statistics_sync.py`).

## Manual refresh

`request_manual_refresh(user, range_name)` marks every day in that range dirty,
requests a full sweep (so the heavy ranges are due too), and queues an urgent
sync on the interactive lane, since the user is waiting on it. It no longer
throws away every day payload the user has.

## What marks changes

Signals (`app/signals.py`, `_handle_media_cache_change`) mark the days a media
row touched; a change with no day marks the aggregate. A `pre_save`/`post_save`
pair marks the day a Movie, Music, Podcast, Game, BoardGame, Anime, Manga, Book
or Comic entry moved *away* from (Episode already did this). Audiobook listening
sessions (`BookListeningSession`) mark the day they started on, since Statistics
reports their minutes apart from page counts. Paths without
signals mark explicitly: generic imports
(`invalidate_all_statistics_days`), the TV and season completion fan-outs,
episode-order remaps, music track relinks and TV provider migrations.

A recurring poll must not use that blanket drop: one changed row would send
thousands of days back to the sync, and the next poll arrives before it has
caught up. Importers listed in `DAY_SCOPED_IMPORTERS`
(`integrations/tasks/_media_imports.py`) write through the ORM (signals mark the
day, including the day a moved row left, for History as well as Statistics),
through `helpers.bulk_create_media`, or through a bulk write they report with
`helpers.note_bulk_rows`. The task then calls `helpers.publish_bulk_rows` to
refresh just those days in History and Statistics (undated rows re-aggregate),
and `coalesce_import_changes` drops the full sweep it requested up front.
A write that moves rows between libraries (Stremio promoting a show to grouped
anime) calls `helpers.note_full_refresh()`, which falls back to the blanket drop.

A poll whose upstream has not changed must write nothing and count nothing as
imported: `coalesce_import_changes` then restores the generation it bumped up
front, and the task skips its calendar reload. Count only rows the run actually
wrote; report rows it looked at and left alone as `unchanged`, a metric key
that is not imported media. `integrations/tests/test_recurring_sync_no_change.py`
(tagged `slow`) runs every recurring importer twice against the same fake
upstream and holds that line.

Not in the list: Trakt (its durable, resumable import publishes through
`durable.publish_pending`, which still drops everything) and the one-off file
importers, where a full refresh after a big upload is what the user expects.
Add an importer only after checking that every write it makes takes one of the
paths above.

If you add a write path that bypasses signals (`bulk_create`, `bulk_update`,
`queryset.update`) on anything Statistics reads, mark it.

## Retired

`statistics_refresh_run.py`, the refresh lock, the Redis dirty list, the
per-user `history_version` as a freshness token (it remains only as the change
token for the per-person talent caches), `STATISTICS_REFRESH_CHUNK_*`,
`STATISTICS_REFRESH_RUN_LEASE`, `STATISTICS_HISTORY_DEBOUNCE_SECONDS` and
`CELERY_TASK_PRIORITY_STATISTICS_CONTINUATION`. The task names
`app.tasks.refresh_statistics_cache_task` and
`app.tasks.continue_statistics_refresh_task` stay registered for one release and
run a sync, so messages queued by the previous version drain cleanly.

## #1272 — what went wrong

The reporter's worker logged the START of two refresh runs but never received
a chunk continuation, so neither run finished. Every further Refresh only
recorded a follow-up against the still-live lock, and the page polled until its
180 s timeout. Nothing could recover it: progress depended on each
self-published continuation arriving, and no periodic task looked for stuck
runs. Separately, `cache_status` deleted a live run's lock whenever the
published entry looked fresh, and the next chunk aborted silently. The sync
above has no message whose loss matters, and polling never touches it.

## Log lines

| Line | Meaning |
| --- | --- |
| `stats_mark user_id days reason` | a change was recorded |
| `stats_sync user_id status ranges elapsed_ms` | a sync pass ended (`done`, `continued`, `busy`, `lost_lease`) |
| `stats_range_summary user_id range generation elapsed_ms missing_days days_ms days_decoded phases_ms` | one range published (`days_ms` is the day-building part of `elapsed_ms`; `phases_ms` splits the aggregate) |
| `stats_snapshot_superseded user_id range generation` | a publish was skipped: a newer generation is already out |
| `stats_reconcile candidates queued` | the reconciler found unfinished work |
| `stats_sync_enqueue_failed` | the broker refused a sync; the reconciler will retry |

`StatisticsSyncState.last_error` holds the last exception a sync raised.
