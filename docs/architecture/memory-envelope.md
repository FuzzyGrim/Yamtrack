# Memory envelope

Floppy's ordinary footprint is already small. What is not yet bounded is its
**high water** — the size it reaches briefly, during a routine job, and then
gives back.

A container that settles below 800 MiB is not honestly a "under 1 GB
application" if a nightly backup can take its cgroup to 5 GB. An operator
running Floppy under a 1 GB limit needs it to boot, back up and serve inside
that limit, not on average.

## Two envelopes, deliberately separate

Reporting one number would be misleading, because two different things grow
and they have to be fixed differently.

**Application resident envelope** — long-lived process PSS and private
anonymous memory. This is what Python allocated: object graphs, caches,
fragmentation. It is not reclaimable under pressure; the kernel's only remedy
is to kill the process.

**Operational cgroup envelope** — everything charged to the container,
including kernel memory and the filesystem page cache. It is mostly
reclaimable, but reclaimable is not free: a 1 GB cgroup limit is still a
limit, and a job that needs 3 GB of page cache to finish inside a 1 GB cgroup
spends the difference thrashing.

So "reclaimable" is never an excuse for a raw peak, and a small process PSS is
never on its own proof of a small container.

Long-term targets, against the *aged application* envelope:

| Target | Standing |
| --- | --- |
| < 750 MiB aged warm container | immediate |
| < 600 MiB | near-term |
| < 500 MiB aged application footprint | primary |
| < 400 MiB | strong stretch |
| < 300 MiB | architecture territory |
| < 200 MiB | major redesign territory |

Neither envelope has a committed ceiling yet. Choosing one needs measurements
this repository cannot take; see [Docker validation plan](#docker-validation-plan).

## Instrumentation: `app/memory_envelope.py`

Until now the only evidence of an excursion was a Portainer graph correlated
against timestamps by hand, which cannot say whether the memory was Python's
or the page cache's. The envelope module samples both at request and task
boundaries.

What it reads, all of it cheap:

| Field | Source |
| --- | --- |
| `rss` | `/proc/self/statm` field 2 |
| `rss_anon` | `/proc/self/statm` field 2 minus field 3 (file and shmem pages) |
| `hwm` | `/proc/self/status`, `VmHWM` |
| `cgroup_current` | `/sys/fs/cgroup/memory.current` |
| `anon`, `file`, `kernel` | `/sys/fs/cgroup/memory.stat` |
| `cgroup_peak` | `/sys/fs/cgroup/memory.peak` |

`VmHWM` is the field that makes a *freed* excursion visible: a request that
builds a 1.5 GiB object graph and releases it before returning leaves RSS
almost unchanged at both boundaries, and VmHWM keeps the mark.

No `smaps`, and no PSS. PSS costs a full VMA traversal per sample and stays
where it belongs, in the diagnostic sampler
(`scripts/container_memory_sample.py`). What is here measured at **39 µs a
sample**, so roughly 80 µs a boundary — cheap enough to leave enabled, which is
the point: the sample that explains a 5 GB spike is the one that was already
running when it happened.

cgroup v2 first. Every probe degrades independently to `unknown` rather than to
zero, so a cgroup v1 host still reports process RSS and VmHWM, and a host with
no `/proc` at all still serves requests. Reporting is wrapped so that
instrumentation can never turn a 200 into a 500.

### When it logs

Nothing for an ordinary boundary. A structured `memory_high_water` event fires
only when one of these crosses its threshold:

| Reason | Setting | Default |
| --- | --- | --- |
| `duration` | `MEMORY_HIGH_WATER_DURATION_MS` | 10 000 |
| `rss_growth` | `MEMORY_HIGH_WATER_RSS_DELTA_BYTES` | 32 MiB |
| `peak_rss` (new VmHWM) | `MEMORY_HIGH_WATER_HWM_DELTA_BYTES` | 32 MiB |
| `cgroup_growth` | `MEMORY_HIGH_WATER_CGROUP_DELTA_BYTES` | 128 MiB |
| `page_cache_growth` | `MEMORY_HIGH_WATER_CGROUP_FILE_DELTA_BYTES` | 128 MiB |
| `near_recycle_ceiling` (on `rss_anon`, as the recyclers measure) | `MEMORY_HIGH_WATER_CEILING_RATIO` | 0.85 |

Zero disables a signal. `MEMORY_HIGH_WATER_ENABLED=False` disables the whole
layer, including the sampling.

These are starting points, chosen to catch the known production events, and
are expected to be tuned once real events accumulate. **None of them is a
memory guarantee.**

The event carries `kind`, `name`, `pid`, `role`, `reasons`, `duration_ms`,
`rss_before/after/delta`, `rss_anon_before/after`, `hwm_before/after`, `cgroup_before/after/delta`,
`anon_before/after`, `file_before/after`, `kernel_after`, `cgroup_peak` and the
role's recycle `ceiling`.

### What it will not log

Request names are the resolved route with its captured parameters substituted
back in — `/medialist/movie`, not `medialist/<str:media_type>`, because "which
list blew up" is the question these events exist to answer. Never the query
string, and never a parameter whose name says it carries a credential in the
path (`key`, `token`, `uidb36`, `sid`, `signature`, …); those are redacted by
name. An unresolved request logs `<unresolved>` rather than falling back to the
raw URL.

Celery events carry the task name and its opaque task id, never its arguments:
arguments carry user ids, search terms and credentials.

### Reading an event

Read `rss_growth` and `peak_rss` against `rss_anon_before/after` first. RSS
includes the memory-mapped database (see
[Mapped database pages are not worker memory](#mapped-database-pages-are-not-worker-memory)):
RSS up with `rss_anon` flat is **SQLite reading the file through its map**,
page cache that recycling cannot return. `rss_anon` up is **process-anonymous
growth** — Python, or SQLite's own heap, built something. `page_cache_growth` with `anon` flat is
**filesystem cache** — something wrote or read a large file. Both together
usually means a large read into Python. `duration` alone means slow but not
large, which is a responsiveness problem rather than a memory one.

## Known high-water sources

| Source | Kind | Status |
| --- | --- | --- |
| `reconcile_trakt_popularity` | process-anonymous | **confirmed and bounded** |
| Database snapshot | page cache | **confirmed** page cache; ladder benchmark added |
| Metadata backfill non-convergence | duration + page cache | **confirmed and fixed** |
| `/medialist/movie` | process-anonymous | structurally bounded |
| Pocket Casts recurring poll | process-anonymous + duration | convergence fixed, counters added |
| Statistics restart thrash | duration / worker occupancy | settling window added |
| Statistics FINISH | process-anonymous | **profiled, partly reduced**, documented below |
| Memory-mapped database pages in worker RSS | page cache counted as process memory | **confirmed; ceilings fixed** |
| History day-cache warming | process-anonymous | bounded to a recent window, one style |
| Interactive worker first-webhook growth | process-anonymous | measured, **not** imports; see below |

### Mapped database pages are not worker memory

**Measured** (validations D, E and G), in the real image under `docker-compose.memory-benchmark.yml`: standard tier,
two web workers with four threads, default ceilings. The fixture is
`app.benchmark_data.build_dataset(60000)` widened for this run: 132,000 items,
three users of 20,000 tracked titles each; every movie, TV and anime item has
a synopsis and a TMDB-shaped `watch_providers` blob, **35% of them 146 to
180 KiB** (median 11 KiB); activity spread over ten years; one 8,000-item
smart list; a 1.8 GB database. Each surface ran on a fresh worker that had
already served one unrelated request, three times, cold (cache cleared) and
then warm. The worker's VmHWM was reset (`clear_refs`) before each request, so
"peak" is that request's own.

SQLite memory-maps the database (`SQLITE_MMAP_SIZE_MIB`, 256 MiB per
connection on the standard tier, 1 GiB on the large one, since `8f24d925d`).
Mapped pages count as resident. Every heavy surface is mostly that:

| Surface (cold, median of 3) | Peak over start | of which anonymous | Notes |
| --- | --- | --- | --- |
| History, date range | +437 MiB | +97 | ends at 566 MiB RSS; anonymous still held 60 s later |
| `/medialist/movie?sort=runtime` (validation E) | +370 MiB | +92 | ends at ~500 MiB RSS; 504 at 60 s in 5 of 6 runs, warm too |
| Statistics sync, Celery background child (validation G) | +264 MiB | ~+15 | peak 393 MiB = 121 anonymous + 272 mapped |
| History, anniversary | +262 MiB | +21 | |
| Search, TV scope | +169 MiB | +15 | |
| Smart list page (8,000 items) | +168 MiB | +24 | |
| History, month | +164 MiB | +9 | |
| Home row, Season "upcoming" | +155 MiB | +36 | |
| Home row, TV "next episode air date" | +153 MiB | +33 | `tracemalloc` peak ~25 MiB |
| Smart list sync task | +150 MiB | ~+10 | `tracemalloc` peak 2 MiB |
| Metadata backfill task (Celery, after the fix) | +381 MiB | +40 | one run, from the live log |
| History rebuild task (`refresh_history_cache_task`) | +43 to +104 MiB | none retained | `tracemalloc` peak 6 MiB, 95 s |
| `/medialist/movie` (default) | +133 MiB | +16 | |
| `GET /api/v1/statistics` | +126 MiB | +3 | warm too |
| `/medialist/tv` | +110 MiB | +16 | 17 s cold on this host |
| Statistics page, All Time | +2 MiB | +1 | served from the snapshot |

Two web workers can each hold several hundred MiB of mapped pages at once, but
they are the same physical page cache, and the kernel reclaims them under
pressure. They belong to the operational envelope, not the resident one.

The recyclers did not know that. Gunicorn's `post_request` compared
`/proc/self/statm` resident, and billiard compares `ru_maxrss` (a lifetime
peak, file pages included). So a date-range History request or a runtime
sort alone put a worker over its 400 MiB ceiling (566 and ~500 MiB RSS), and
a Statistics sync left its child at 386 MiB of lifetime peak against a
400 MiB ceiling. Not measured here, but the same arithmetic: the constrained
tier also maps 256 MiB, under a 250 MiB background ceiling. Recycling for those returns nothing: the
replacement maps the same file back in, and the user waits for the restart.
This fits the production records that were never reproduced: a worker
growing 588 MB during one search and then recycling (2026-10-31, four days
after the map was enabled) and +30 to +125 MB per cold Home row.

**Fixed.** Both ceilings, and the `near_recycle_ceiling` signal, now compare
the anonymous resident size, statm resident minus shared
(`config.runtime_profile.current_anon_rss_bytes`): gunicorn's hook directly,
and Celery by replacing `billiard.pool.mem_rss` in each pool child. The map
itself is unchanged; it is what keeps cold reads fast on slow disks. A
`/proc` that cannot be read keeps the old behaviour (gunicorn never retires
on a guess; Celery falls back to billiard's figure).

**Still open, as structural hypotheses** (not measured further here):

- The anonymous part is mostly not Python. `tracemalloc` peaks at 23 MiB for
  the date-range History request while anonymous memory grows 97 MiB and
  stays. The likely owner is SQLite's own heap: its page cache
  (`SQLITE_CACHE_SIZE_MIB`, per connection) and in-memory sorts
  (`SQLITE_TEMP_STORE=MEMORY`) of rows that carry the wide `Item` columns,
  which glibc then keeps. A lone `TV.objects.select_related("item").first()`
  (ordered, one row) grew a process by 70 MiB anonymous on this fixture.
- The runtime sort's non-SQL path is also a latency failure (60 s, 504) on a
  20,000-title library; that is the "updated"-sort class of prompt 08.

### `reconcile_trakt_popularity` — 600 MiB of Python, for four scalars

The largest *process-anonymous* excursion yet identified, and the only one of
the three below that was a real heap.

One production run: `recomputed=2972`, 13.3 s, `VmHWM` 198 MiB at task entry
and **799 MiB** at completion — with an ending RSS of 129 MiB. The memory was
genuinely allocated and genuinely freed, which is precisely the shape only
`VmHWM` makes visible. The 400 MiB Celery child guardrail contained it by
retiring the child afterwards. Containment is not a fix.

Three compounding faults, all in one statement:

```python
all_items = list(
    trakt_popularity_service.tracked_items_queryset().iterator(chunk_size=500)
)
```

The `.iterator()` was nullified by the `list()` around it, so every tracked
item was held at once. `tracked_items_queryset()` is a bare
`Item.objects.filter(...)`, so each of those items was a **fully hydrated
`Item`** — all ~60 columns, including `synopsis` and the `watch_providers`
blob that every other bounded path is careful to defer, roughly 146 KiB a
title. The loop read four scalars: `id`, `trakt_popularity_fetched_at`,
`trakt_rating`, `trakt_rating_count`. 2972 × ~146 KiB ≈ 424 MiB of JSON
before decode churn. And `Item.Meta.ordering = ["media_id"]` was never
overridden, so the `DISTINCT` sorted full rows by a 500-char column.

It now collects ids only, then walks them in chunks of 900, projecting the
four columns it reads with `values_list` and writing through `bulk_update`.
The chunk is 900 rather than a round 1000 because an `id__in` spends one query
parameter per id and Django does not batch that lookup the way it batches
`bulk_update` — 999 is `SQLITE_MAX_VARIABLE_NUMBER` before SQLite 3.32.
Two passes rather than one streamed cursor is deliberate: no read cursor stays
open across the writes, which is the guarantee the original `list()` bought by
paying for the whole library. Write round trips fell from one per row (2972)
to one per chunk.

`total` is now logged beside `recomputed`, because `recomputed` alone cannot
say how large the working set was — which is the number a regression here
would appear in.

Measured with `tracemalloc` in
`app.tests.test_trakt_popularity_benchmarks` (8 KiB/row payload, roughly
one-eighteenth of production's):

| Library | Old shape peak | New shape peak |
| --- | --- | --- |
| 500 | 14.2 MiB | 3.6 MiB |
| 1 000 | 23.8 MiB | 6.0 MiB |
| 1 500 | 33.4 MiB | 5.9 MiB |
| 3 000 | 62.2 MiB | 6.4 MiB |

The old shape is linear in rows × payload. The new shape rises to the chunk
size and then stops: 3× the library costs 6.6% more memory. That is the
structural claim; the Docker number is Validation A.

### Database snapshot — the largest raw excursion

At 02:30 the cgroup rose from roughly 1–2 GB to above 5 GB alongside about
2 GB of write I/O, then decayed over hours. Decay over hours is what
reclaimable page cache does; it is not what a Python leak does.

The write path supports that reading. `sqlite3.Connection.backup()` copies the
whole database through ordinary buffered I/O, and `PRAGMA quick_check` then
reads every page of the copy back — so one snapshot charges the container
roughly **twice the database's size** in the cgroup's `file` accounting, for a
file Floppy will not read again until the live database is unreadable.

**This is now confirmed.** A production capture of a 31-second snapshot of a
~2.06 GB database: process RSS moved **0.2 MB**, cgroup rose **1.75 GB**, page
cache rose from 398 MB to 2.14 GB, and `anon` stayed flat. It is page cache,
not heap, exactly as the write path predicted.

The existing hint also appears to be doing useful work: the cache remaining
after completion was roughly *one* database-copy rather than two.

What remains is that the backup reads most of the live ~2 GB database while
writing the ~2 GB destination, warming both sides at once, and `quick_check`
then reads the whole copy back before the hint is issued. **Nothing bounds that
cache during the copy**, and that is a deliberate decision rather than an
omission — see below.

The fix issues `POSIX_FADV_DONTNEED` on the finished snapshot. Its safety
comes from ordering: the hint is issued strictly *after* `fsync` and while the
descriptor is still open. `DONTNEED` drops only clean pages, so it can never
cost durability however it is timed, and after `fsync` there are none to skip.
The live database is never hinted — its cache is doing useful work. The file
itself is untouched: no truncate, no unlink, only a hint about the cache in
front of it. `posix_fadvise` is feature-detected; a platform without it still
publishes, and a hint that fails is logged and ignored. Global `drop_caches` is
never used.

#### How the copy runs, and what was not changed

The copy is stepped: `Connection.backup(dest, pages=256)` with a 2 ms pause
between steps, so the snapshot does not monopolise the disk while web and
worker traffic runs (added with background verification in `1d4f07a39`).

A stepped copy alone restarts from page one whenever another connection
commits between steps. Measured on an 82 MB database (SQLite 3.42): a commit
every 0.25 s caused 175 restarts in 45 s and the copy never finished. So
`_copy_source` in `config/sqlite_integrity.py` picks by the source's own
journal mode:

- **WAL:** the copy runs inside one read transaction on the source
  (`BEGIN` plus one read). It copies that single snapshot in one pass, with
  commits every 50 ms, and never blocks a writer (the copy's disk traffic
  can still slow a commit: slowest 184 ms against 449 ms to 4 s for the old
  unpinned copy, on a 711 MB file). The cost is that
  checkpoints cannot move past the snapshot until the copy ends, so the WAL
  grows for one pass and shrinks at the next checkpoint (CONC-43 receipts).
- **Rollback journal** (the fallback on SQLite builds with the WAL-reset bug):
  the copy is not pinned. A held read lock, or a single-step copy, blocks
  every writer at commit for the whole copy: 2.8 s for a 711 MB file in
  CONC-43, minutes for a multi-GB one, past the busy timeout. Unpinned,
  writers wait at most one step, but a busy instance can restart the copy.

Each run records `pinned`, `steps` and `restarts` on the `db_snapshot` log
line. A pinned copy should show `restarts=0`; more than three restarts fails
the snapshot with "the database kept changing" rather than running to the
task time limit. On a busy rollback-journal install that means a missed,
reported snapshot instead of stalled writes. Measured on a laptop with SQLite
3.42 (macOS) and 3.53.4 (the Linux image), not on a production instance.

Bounding the destination cache *during* the copy (a periodic `fdatasync` and
`POSIX_FADV_DONTNEED` on the staging file) was investigated and not
implemented. `DONTNEED` drops only clean pages, so a mid-copy hint needs extra
syncs, and whether the kernel then reclaims depends on the filesystem and
writeback. `quick_check` would also re-warm the copy afterwards, and it cannot
advise as it goes because it runs on the write connection.

So the deliverable is measurement, not a guess:
`scripts/snapshot_memory_ladder.sh` runs the real `write_database_snapshot`
against a real database at each rung of a memory ladder — 512 MiB, 768 MiB,
1 GiB, 1.5 GiB, 2 GiB by default, one fresh container and one fresh volume per
rung so no rung inherits another's cache — and reports status, duration, OOM
kills, `memory.peak`, the anon/file split, I/O and settling at +30 s, +60 s and
+5 min. `--limits 0` records the unconstrained natural peak; `--no-fadvise`
repeats the last rung with `os.posix_fadvise` hidden, exercising the
feature-detection fallback.

This answers the question that matters — *what limit does the backup actually
require?* — rather than *how much cache does Linux take when given 32 GiB?* A
4.8 GB unconstrained peak is ugly, but if the same backup completes inside
768 MiB because the kernel reclaims, then 5 GB was never the requirement.
**If a low rung OOMs, the incremental-backup question comes back — with
evidence.**

The default snapshot minute also moved from `:30` to `:37`. The incremental
metadata backfill runs at `*/15` or `*/30` depending on tier, so the old
default started a whole-database copy in the same minute as a bulk sweep —
exactly the pairing production logged. An operator who has set
`DB_SNAPSHOT_MINUTE` keeps their own value.

### `/medialist/movie` — 110–125 s across 11 queries

Eleven queries is not an N+1 problem. It is a few queries each returning far
more than the page needs, which is why a query-count budget stayed green
throughout.

`_aggregate_duplicate_data` filters by *item id*, not by page, so on a list
that is not paginated in SQL it returns a row for every tracked title. It
fetched them with `select_related("item")` and no deferral, hydrating a full
`Item` each — including `synopsis` and the `watch_providers` blob, roughly
146 KiB a title, that every surrounding queryset is careful to defer. One SQL
query, hundreds of MiB of JSON decoding.

It now projects the eight scalar columns the aggregation reads and joins
`app_item` not at all. It also dropped `Media`'s default ordering
(`["user", "item", "-created_at"]`), which made the database join `users_user`
and `app_item` purely to sort a result set that is immediately grouped into a
dict by item id.

Separately, the separate-entries ("show each play") list carried its own copy
of the deferred-field list, and the copy had drifted: it no longer deferred
`item__watch_providers`, so that mode loaded the blob for the whole library.
The copy is deleted and the one definition imported.

Note what makes a request take the non-SQL-paginated path at all — it is easy
to fall onto and hard to notice: a non-empty `pinned_watch_providers`, a
persisted `no_status` filter, `movie_show_each_play`, or a sort of `runtime` or
`time_watched`.

### Pocket Casts — 1000 s every two hours, importing nothing

`synced=5237 skipped=401` could not distinguish 5237 rows written from 5237
rows inspected and left alone. The counters are now `examined`, `unchanged`,
`changed`, `created`, `written`, `hydrated`, and `changed > 0` with
`written = 0` is the readable signature of a rewrite loop.

One such loop is fixed: the freshness check compared the raw provider value
against a stored one the database had already coerced, so a duration the
provider does not send as a plain `int` (`"1800"`, `1800.0`) differed forever —
write, normalise, differ, write. Whether that is what production is hitting is
**not established**; there is no recording of the live wire format in the repo.
The counters are what the next run should be read for.

Two bounds alongside it: the end-of-run duplicate sweep no longer hydrates
every `PodcastEpisode` to discover a clean catalog has no duplicates, and
`episode_uuid` has an index (`unique_together` leads with `show_id`, so the
per-episode lookup by uuid alone was a table scan).

### Metadata backfill — 149 failures out of 150, every fifteen minutes

Terminal-vs-transient failure handling had already shipped, and production was
still logging `150 processed / 142 errors`, then `150 processed / 149 errors`,
in 2–2.5 minute passes that warmed hundreds of MiB of page cache while worker
RSS barely moved. The failures were overwhelmingly MusicBrainz 400/404 for
recording ids that do not exist — the exact population the terminal handling
was built for.

The cause was a gap between two deliberate designs, each reasonable alone.

`services.api_request` re-raises a bare `requests.exceptions.HTTPError` for any
4xx it does not retry, leaving each provider to wrap it in its own
`handle_error`. Eleven provider modules have one. **musicbrainz, trakt and
tvmaze did not** — `musicbrainz._mb_request` logged the failure and re-raised
the raw error. Meanwhile `is_terminal_backfill_error` only ever inspected
`ProviderAPIError`. So a MusicBrainz 404 matched neither branch, fell through
to `return False`, and was recorded as `metadata_backfill_retry_later`. Since
`next_retry_at` caps at one day, the same dead ids re-entered every cycle
forever.

Nothing in the state machine was broken: `MediaTypes.MUSIC` is in
`RELEASE_BACKFILL_MEDIA_TYPES`, so the terminal-recording path was already
wired. Only the classifier was blind.

Both boundaries are now closed. `is_terminal_backfill_error` resolves a status
code from a `requests.exceptions.HTTPError` as well, against the same
`TERMINAL_PROVIDER_STATUS_CODES` — so the unwrapped path behaves like the
wrapped one rather than anything becoming newly terminal — and this covers
trakt, tvmaze and any provider added later. It matches on the concrete
exception type, **not** on "has a `.response` attribute": any exception can
carry that, and `tvdb._request` raises a bare `ValueError` when credentials are
missing, which must stay retryable. Separately, musicbrainz gained a
`handle_error` so it follows the same convention as the other eleven.

`MetadataBackfillField.DISCOVER` was also recorded without `terminal=`, so a
TMDB 404 retired an item's release and status fields but never its discover
field. It now passes the same classification.

The regression tests deliberately use real `requests.exceptions.HTTPError`
objects carrying real `Response` objects. Every pre-existing test constructed a
`ProviderAPIError` by hand or patched `_fetch_item_metadata`, which is why a
path that had been failing in production for weeks was fully green.

### Statistics restart thrash

A run that aborts on `history_version_changed` used to restart immediately.
Under a credits backfill — which bumps the version roughly every ten seconds
while its queue drains — that produced seven aborted All Time refreshes in a
minute. An abort of that kind now waits a short settling window that coalesces
further aborts. The sync that replaced runs never aborts at all; see [statistics-sync.md](statistics-sync.md)
for the state machine itself.

### History day-cache repair — 13.6 h of CPU in 16.7 h (#1158)

A production log from a 1-CPU, `minimal`-tier instance spent 49,120 s of task
time in `Repair History Day Cache Coverage` over 16.7 hours; every other task
together spent about 400 s. The repair never converged: its `remaining` count
climbed back to the full day count every fifteen minutes.

Two faults multiplied each other.

**An empty poll wiped the whole cache.** `Import from GPodder (Recurring)` runs
every fifteen minutes and usually imports nothing, yet the importer ended with
an unconditional `invalidate_history_cache(user, force=True)` and a full
statistics refresh. `import_media` already does both, guarded by
`has_imported_media`, so the importer's copy only ever added the empty-poll
case. The log labelled it `reason=album_score_change`, a hardcoded string in
`invalidate_history_cache`; it now reads `full_invalidate`. The same poll also
downloaded every subscribed feed twice before learning the action delta was
empty; it now reads each feed once, and only on a full resync or when there is
listening activity to match.

**A repeats day cost the whole game library.** `build_history_day`'s repeats
branch loaded every `Game` and `BoardGame` row the user had, with full `Item`
columns, and date-filtered them in Python — per day. On the maintainer's
library (1,601 games, 10,308 repeats days) that was 334 ms a day against
33 ms for sessions: about 57 CPU-minutes for one full pass. A SQL prefilter
(`_span_may_touch_day`), a strict superset of the unchanged Python check, cuts
it to 26 ms a day; peak RSS for a 1,000-day pass fell from 265 to 171 MiB, and
the payloads of 1,000 sampled days per style were byte-identical before and
after. Movie play counts are likewise scoped to the day's titles.

Measured in Docker against a copy of that database — `cpus: 1.0`, 1 GiB,
`FLOPPY_RESOURCE_TIER=minimal`, cold Redis, an empty GPodder poll every fifteen
minutes, 49 minutes each, cgroup `cpu.stat` (`container_memory_sample.py` now
reports `cpu_usage_usec`):

| | CPU average | CPU time | repair at end |
|---|---|---|---|
| before | 75 % (pinned at ~100 % after warm-up) | 2,220 s | reset to full by the empty poll; never converges |
| after | 28 % | 823 s | `remaining=0` for both styles at ~40 min, then 0–1 % CPU; empty poll resets nothing |

Still open: a poll that *does* import a play still clears every day. After this
change that costs minutes rather than an hour; scoping it to the imported days
is the next step if it still shows on the minimal tier.

## Still unverified, and still unfixed

**What memory limit the snapshot requires: 768 MiB, measured.** The ladder
ran on the pinned copy (CONC-43) against a
synthetic 2.43 GB database (622,592 pages; 184,800 items, 201,600 episodes,
403,200 history rows; wide `watch_providers` make `app_item` 2.2 GB of it),
with the whole stack (gunicorn, Celery with beat, nginx) idle in the same
container. Every copy was pinned with `restarts=0`.

| Limit | Quiet | With 4 commits/s |
| --- | --- | --- |
| No limit | ok, 126 s, peak 2.3 GB | |
| 2 GiB | ok, 116 s | |
| 1.5 GiB | ok, 130 s | |
| 1 GiB | ok, 134 s | ok, 117 s |
| 768 MiB | ok, 261 s | ok, 90 s |
| 512 MiB | 11 OOM kills | snapshot killed, no copy |

- At 768 MiB nothing is killed and the snapshot takes at most about twice its
  unconstrained time: the kernel reclaims the copy's page cache as it goes.
  With the writer, commits never failed (p99 139 ms, slowest 0.85 s) and the
  WAL peaked at 6.6 MB.
- 512 MiB fails on **anonymous** memory, not cache. The idle stack plus its
  boot-time tasks already reach about 490 MB `anon`; cache was squeezed to
  near zero and the kernel killed Celery children, gunicorn and, with the
  writer, the snapshot itself. A smaller copy would not fix that rung.
- The startup recovery snapshot (`sqlite_startup --prepare-migrations
  --fast`, services stopped) took 44 s at 768 MiB and 39 s unconstrained,
  with no kills.
- Without `posix_fadvise` the snapshot still publishes; page cache right
  after it was 780 MB against 412 MB with the hint, and both settled within
  five minutes.

So the stepped, pinned copy is adequate and the incremental-backup question
stays closed. Limits of the evidence: it was measured under Docker Desktop,
whose virtual disk is slower than a NAS, so durations are only comparable to
each other; the ladder triggers the task through `manage.py shell`, which adds
about 90 MB of `anon` that a Celery worker would not; and the fixture is
lighter on episodes and history than production, which affects `quick_check`
time more than cache.

**Whether the Trakt reconcile fix holds in a container.** The allocation
profile is structurally bounded and benchmarked, but `tracemalloc` is Python
allocation, not RSS. Validation A is what turns it into a memory claim.

**Whether `/medialist/movie` now fits in a worker: measured
(validation E), and it does not on the slow path.** On the SQL-paginated
default it adds 133 MiB, 16 MiB of it anonymous. Sorted by `runtime` (the
non-SQL-paginated path) it adds about 370 MiB, about 92 MiB anonymous and the
rest the mapped database, ends near 500 MiB RSS, and timed out at 60 s in five
of six runs, warm included. With the ceilings now measuring anonymous size it
no longer retires the worker; the 92 MiB and the minute are still there. Not
compared against `3fe29eff`; this was a ranking run on current code only.

**Statistics FINISH: profiled, partly reduced.** `_aggregate_statistics_from_days`
is a short sequence of phases in `statistics_aggregator.py`: the day walk
(`_walk_days`, merging each payload into one `_RangeAccumulators` with
`_merge_day`), then undated rows, status and scores with top rated
(`_build_scores_and_top_rated`), top played, activity and play charts, games,
reading (`_build_reading_consumption` per type), History highlights, summary
cards, top talent, and packing the per-type metric cards. Each phase is a
function that reads the accumulators and returns its part of the payload, and
`stats_range_summary` logs each one's wall time in `phases_ms` under the same
names, so a slow range names its own cause. The profile was taken on a synthetic library
(one user, 3,414 active days, about 15,000 items; production is about 9,570
days and 250,000 items), with day payloads warm:

- The finish is most of the cost for every bounded range (85% or more) and
  about 60% for All Time. The walk over the day payloads is the rest.
- All Time held about 74 MB of Python allocations at peak and grew the process
  by about 90 to 110 MB. Both scale with the distinct items in the range.
- A snapshot's cache copy holds full tracker and `Item` instances, wide columns
  included: about 900 `Item` rows in the All Time snapshot.

Removed, with every range's payload unchanged: the day payloads were decoded
twice (once to look for missing days, once to merge them); top-rated rows were
re-read and annotated once for the overall list and again for each media type;
every completed reading row was loaded as two models to read six scalars; and
each range, and each media type inside it, re-read the History index and the
same History days. That is about a third of the CPU of a hot pass and a fifth
of a heavy one on the profiled library.

**Across the ranges of a pass (2026-11-14).** Profiled again per range inside
whole passes (same library shape, 3,400 days, about 18,000 items). In a heavy
pass the top-rated card was the largest phase of every range (about 45% of the
ranges' CPU), and 54% of the rows it annotated and 62% of the card rows loaded
had already been loaded by an earlier range of the same pass. The walks over
the day payloads of all bounded ranges together were about 0.25 s. So the
pass now loads and annotates each card row once and hands each range a copy
(`statistics-sync.md`); the day walk is still per range, and sharing it is not
worth the accumulator snapshots it would need. For one pass of all eleven
ranges: 25,033 model instances built became 12,755, queries about 1,460
became 953, and the Python allocation peak 139 MB became 101 MB; every payload
is byte-identical.

Measured in the real Celery background child (validation G,
memory half): one sync of a 20,000-title, ten-year library peaked at 393 MiB
RSS, of which 121 MiB anonymous and 272 MiB the mapped database; anonymous
growth per run was about 10 to 15 MiB. The webhook-injection half of G
(chunk duration, FINISH duration, aborted runs versus follow-ups) was not run.

Still open, largest first for All Time:

- **The day walk** (fetch and merge, about a third of All Time). Each range
  walks its own days; see above for why the ranges do not share one walk.
- **Top-rated annotation.** `MediaManager.annotate_max_progress` is called per
  row for seasons and re-reads rows the aggregate has just fetched. It now runs
  once per row per pass rather than per range.
- **Reading items.** `_fetch_reading_items_with_authors` loads a full `Item`
  for every reading title in the range to read its release date and authors.
- **Top talent**, and the wide `Item` columns loaded by `_fetch_media_objects`
  and `statistics_sync._fetch_instances`, which have their own owners.
- The sparse activity scan that resolves All Time's day list runs once per
  All Time aggregate.

**The next largest target.** With the Trakt reconcile bounded, the history
day-cache repair loop fixed (above) and Statistics FINISH profiled, the largest
known *unfixed* process-anonymous source is still the All Time aggregate: its
day walk and the model instances its snapshot carries.

**History day-cache warming.** Thousands of historical days did not need
eager warming: every reader builds a missing day on demand and caches it.
Warming now covers only the recent window of the style the user reads (see
"A missing index and which days stay warm" in `history-memory.md`). On a
3,650-day fixture one full pass had cost about 96 s of CPU, 55,000 queries and
10 MB of payloads per style, and with the 30-day TTL that repeated for every
day, in both styles, indefinitely; a cold old page costs about 0.1 to 0.3 s
the first time someone opens it.

**Interactive worker first-webhook growth — measured, and it is not imports.**
The first real Plex webhook took the interactive child from ~99 MiB to
~154 MiB and then left it flat, which has the shape of one-time lazy warm-up
rather than a leak. The obvious remedy would be preloading those modules in the
interactive parent so the pages are shared copy-on-write.

That remedy does not apply. Importing the entire webhook path after
`django.setup()` — `integrations.webhooks.plex`, `app.providers.tmdb`,
`app.services.metadata_resolution`, `app.services.music_scrobble`,
`integrations.plex`, `app.live_playback` — costs about **0.9 MiB**, essentially
all of it the webhook module itself. There is no ~50 MiB of modules to preload.

So the growth is the working set of doing the work once — query and response
object graphs, provider client state, and allocator arenas that Python does not
return to the OS — not deferred imports. A preload would move roughly 1 MiB and
would add provider stacks to a lane that is deliberately narrow. Recorded here
so the next person does not re-derive it; **not worth pursuing further without
a profile of the webhook body itself.**

**Gunicorn concurrency.** Production sets `WEB_CONCURRENCY=2` explicitly even
though the runtime would choose one worker on that host, and each extra worker
holds its own resident copy of the application. Deliberately not changed here:
the trade needs Docker, not reasoning.

## Docker validation plan

Read `memory_high_water` and `db_snapshot` lines from the container log
throughout; where a step says "sample", use
`scripts/container_memory_sample.py`, which reports PSS per process alongside
the cgroup.

### Capturing PSS correctly — reject a degraded capture

A production capture was taken without `smaps` access and its PSS was
unusable for most application processes. The cgroup accounting was still
valid, which is what makes this failure mode dangerous: the capture looks like
a capture.

`docker exec --user root` is **not** sufficient. Docker drops `CAP_SYS_PTRACE`
by default, so reading `/proc/<pid>/smaps_rollup` for a process owned by
another uid still fails the ptrace access check, and the sampler falls back to
`VmRSS`. Always take production captures with:

```bash
docker exec --privileged -u 0 <container> python - < scripts/container_memory_sample.py
```

`docker-compose.memory-benchmark.yml` already sets `cap_add: SYS_PTRACE`, so
the benchmark harnesses are unaffected; it is the ad-hoc capture against a
container started without it that degrades.

**Reject or flag any capture unless both hold:**

- `smaps_detail == "full"`
- `rss_only_processes == 0`

The sampler now states this itself: `capture_valid` is a single boolean, and
`capture_warning` names the cause. Note that `smaps_detail` alone is not
enough — it reports `"full"` when *nothing* was measured, because there are
then no rss-only processes to report. `capture_valid` covers that case.

### A. Trakt popularity reconcile

Against a production-scale library, on the same database, comparing this
change against the commit before it.

Record worker **PSS, RSS and private-anon before**, `VmHWM` and
`memory.peak` **during**, **after**, and **at +60 s**. The `memory_high_water`
event for the task carries `hwm_before`/`hwm_after` directly.

The excursion to beat is 198 MiB → 799 MiB `VmHWM` for 2972 rows. A pass is
that same row count adding well under 50 MiB of transient RSS, and the child
never approaching its 400 MiB recycle ceiling — so it is not retired, which is
how the old behaviour was being masked.

### B. Database snapshot

First **unconstrained**, to record the natural peak:

```bash
scripts/snapshot_memory_ladder.sh --image <image> --database <db> --limits 0
```

Then the ladder, which is the question that matters:

```bash
scripts/snapshot_memory_ladder.sh --image <image> --database <db> --no-fadvise
```

Per rung, confirm `file` is what rises (not `anon`), that the `db_snapshot`
line's `file_after - file_before` matches the cgroup trace, and that
`page_cache_release ... advice=issued` appears. **The deliverable is the
smallest rung with `status=ok` and `oom_kills=0`** — that is the backup's
actual memory requirement, as opposed to its unconstrained cache appetite.

The `--no-fadvise` rung must still publish a valid snapshot; that is the
feature-detection fallback under test.

Two practical notes from the first harness run against a 2.1 GB database:

- **Budget at least 3× the database size of Docker storage.** Each rung holds
  the database and a full second copy of it. Floppy's own pre-flight correctly
  refuses the backup without the room, which scores the rung `unverified`; the
  ladder now checks disk before booting and scores `no_disk` instead, so a
  disk problem is never mistaken for a memory result. On Docker Desktop the
  constraint is the VM disk, not the host's.
- **Boot dominates the rung.** Startup integrity `quick_check` reads the whole
  database before the app is ready — several minutes at this size, and its own
  full database of page cache. The +20 s settle before the "before" reading
  separates it from the snapshot's, but expect each rung to take far longer
  than the snapshot itself.

### C. Metadata backfill

A first pass against a library with known-dead MusicBrainz ids, then a repeat
pass after terminal state has been learned.

Converged looks like `errors` collapsing toward zero on the second pass, with
`metadata_backfill_give_up` lines replacing `metadata_backfill_retry_later`,
and the run no longer taking 2–2.5 minutes and hundreds of MiB of page cache
every fifteen minutes. The failure this replaces was stable at 149 errors out
of 150, indefinitely.

### D. Aged container

Read recycling from the sampler, not from the cgroup alone. Sample every
process's `RssAnon` and `RssFile` once a second beside a mixed web workload
(every surface in a loop, caches cleared every third pass, plus
`scripts/memory_library_workload.py`), and count distinct worker pids: a
worker pool that recycles every minute is a sawtooth, and the recycle checks
compare anonymous size (see
[Mapped database pages are not worker memory](#mapped-database-pages-are-not-worker-memory)),
so RSS above the ceiling with anonymous size below it must not recycle.

Run the production-like workload for several hours. Report the two envelopes
**separately** — application-resident (process PSS and private-anon) and raw
cgroup including page cache — and verify each returns to a repeatable envelope
rather than ratcheting. Only after this should `X` and `Y` be chosen.

### E. `/medialist/movie`

Against a large library, with a user configured to take the **non-SQL-paginated
path** (pin a watch provider, or sort by `runtime`) — otherwise the fast path
hides the regression this targets.

Record worker RSS, PSS and private-anon **before, at peak, after, and once
settled**. Confirm the worker does not reach the 400 MiB recycle ceiling and
is not retired. Compare against `3fe29eff`.

### F. Pocket Casts

Across a full no-op recurring run: background child memory and duration, and
the `examined / unchanged / changed / created / written / hydrated` counts.

A converged run is `unchanged ≈ examined` with `written = 0`. If `changed` is
high while `written` is 0, another field has the same representation mismatch
duration had — the counters now name the failure rather than hiding it.

### G. Statistics

Run a large rebuild while repeatedly injecting Plex/Stremio webhooks. Record
max chunk duration, FINISH duration, webhook wait time, the number of aborted
runs, and the number of follow-ups scheduled. Expect follow-ups to be far
fewer than aborts — that is the coalescing working.



## Reporting the result

When the measurements exist, report **two** numbers, never one:

- **Application resident ceiling** — process PSS / private-anon after aged
  workloads.
- **Operational cgroup ceiling** — raw cgroup memory including filesystem
  cache during supported routine jobs.

"Floppy normally settles below X, and routine supported workloads remain below
Y."
