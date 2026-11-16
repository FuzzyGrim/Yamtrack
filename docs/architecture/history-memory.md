# History request memory

The history routes are the largest request-level memory consumers Floppy has
had in production. This records what the cost actually was, what removed it,
which history paths are still proportional to the history rather than to the
response, and how index invalidation stays correct under concurrency.

## Index invalidation and the era token

Typed indexes (history narrowed to media types) used to be tracked in a
registry maintained by read/append/write. Two builders could lose each
other's registrations, and a builder could re-create a registry that an
invalidation had just deleted, leaving a pre-invalidation typed index
reachable as the "current" one. Readers then accepted it as fresh for up to
`HISTORY_STALE_AFTER`.

Invalidation no longer tries to delete racing publishes. Each
(user, logging style) has an **era token** in cache
(`history_cache_utils._current_history_era` / `_bump_history_era`):

- Tokens are globally unique (`_new_history_era_token`) and only ever
  compared for equality. Invalidation writes a fresh token unconditionally;
  concurrent invalidations race to install distinct tokens and *any* winner
  retires every older namespace. There is no read-modify-write, so no bump
  can be lost and no `incr`/Lua atomicity is required.
- A typed index key embeds the era (`_typed_history_index_key`), and the
  index payload embeds it too. A reader accepts a typed index only when both
  the namespace and the payload token equal the era it read.
- Publishers capture the era **before** reading the rows that feed the
  index (`cache_history_index(..., era=...)`). If invalidation lands
  mid-build, the publish lands in a retired namespace — unreachable, and
  reclaimed by TTL or the next registry cleanup. Nothing is check-then-set.
- The main (untyped) index embeds the token in its payload; readers treat a
  token mismatch like staleness — serve the payload, schedule a refresh —
  which preserves the intentional stale-while-refresh behaviour while making
  a late stale write converge in seconds instead of sitting "fresh" for an
  hour.
- A missing/expired/evicted era key is reseeded with a *new unique* token,
  so an orphaned typed index can never be adopted again (no ABA). The era
  key's TTL (24h) exceeds the index TTL (6h) and is refreshed on publish.

Residual, pre-existing window: invalidation fires from signals inside the
writing transaction, so a reader that rebuilds between the era bump and the
row commit reads pre-write rows and republishes them under the new era. The
next invalidation (any save) or staleness timer repairs it; this window
existed before the era protocol and is unchanged by it.

Day refreshes are deduped per set of days (`_refresh_dedupe_key`): while one
is queued or running, another request for the same days is not queued. Such a
request leaves a marker (`_refresh_rerun_key`) instead of vanishing. The
refresh reads the marker before it reads rows and again when it finishes; a
marker that appeared in between means a change landed after its read, so it
queues one more pass. A marker already there at the start is covered by the
read and is cleared. A finishing refresh releases only its own dedupe key, and
the style lock only while that lock still names it, so a request for other
days that is still waiting keeps its lock. Regression:
`app.tests.test_history_refresh_rerun`.

Regression: `app.tests.test_history_index_race_safety` drives the original
interleavings deterministically (patched cache/build calls, never sleeps):
lost-registry registrations, invalidation between row read and publish,
publication with a retired token, era-key eviction, registry loss, full and
day invalidation, style/user isolation, empty indexes, and a bounded
repeated schedule.

## A missing index and which days stay warm

The month view reads only the index days that start with its month; the
previous and next month links are calendar arithmetic. So when no index is
cached for the user and style (six quiet hours, a Redis restart, an eviction),
`get_month_history` answers from `build_history_index(..., window=(first, last))`,
which runs the same per-source day queries restricted to that month (games in
repeats style by span overlap, so a play crossing midnight or a month boundary
still lists every day). It schedules the whole-history build in the background
once (`schedule_history_refresh`, deduplicated by the refresh lock, never
inline) and **never publishes the month list as the index**: readers treat the
index key as every day that exists, so a month-only list there would empty
every other month. If the task queue is down, each request keeps answering
from its month. The era protocol is untouched: the background build captures
its era before reading rows, as before. On a 9,600-day fixture this took the
cold-index month request from 84 to 145 ms of CPU (the whole library, read
per source) to about 8 ms, and its peak allocation from 1.7 MiB to 0.3 MiB.
The anniversary view, the paged API view and Statistics highlights still
build the whole index when it is missing; they need every year.

Day payloads are built ahead of a visit only for the **style the user reads**
(`game_logging_style`) and only for the **recent window**:
`history_coverage_day_keys` keeps the days of the last
`HISTORY_COVERAGE_WINDOW_DAYS` (62, the current and previous month) and never
fewer than one page, so someone who has been away still finds their latest
page warm. Every older day, and every day of the other style, is built by the
reader the first time a page asks for it and then lives for its TTL. A write
still invalidates both styles: the user's style keeps its old payloads
readable until the scheduled rebuild replaces them; the other style's changed
days and its index are deleted at once (`history_rebuild_styles`), so it is
never served from before the write. A style switch therefore starts cold and
correct. `HISTORY_COVERAGE_WINDOW_DAYS=0` warms every day again and
`HISTORY_COVERAGE_BOTH_STYLES=true` warms both styles again.

## What production showed

A filtered `GET /api/v1/history/` on a real instance:

```
history_build_episodes count=8218
history_build_end entries=8218 history_days=23
rss_kb_start=151084 rss_kb_end=337856 rss_kb_delta=186772
duration_ms=8124 queries=7  response 16,946 bytes
```

One request, ~182 MiB of worker RSS, a 17 KiB response. The worker exited
soon after and was replaced by the RSS ceiling. The ceiling contained the
symptom; it was never the fix.

## Root cause

The cost was never the entry dictionaries. It was hydrating `Item` rows.

An episode play `select_related`s three items -- the episode, its season and
its show -- and `Item` is a wide row. `Item.watch_providers` holds TMDB's
availability for every region it knows, around 146 KiB a title. History reads
fourteen item columns and none of the heavy ones, but the queryset loaded and
JSON-decoded every column, three times per play.

Measured in the benchmark container against a copy of a real library, listing
6,454 episode plays:

| queryset | resident delta |
| --- | --- |
| `select_related` as it was | 802 MiB |
| deferring `watch_providers` alone | 100 MiB |
| deferring every column history never reads | 53 MiB |

`watch_providers` alone is ~87% of it.

## What changed

- `app/history_cache_utils.py` names the item columns history never reads and
  prefixes them per `select_related` path.
- Every history queryset in `history_cache.py` and `history_cache_day_builder.py`
  defers them. Deferring rather than `only()` is the safe direction: an
  unforeseen reader loads the column late instead of seeing it missing.
- The episode title map selects five columns instead of whole items. It has
  one row per episode played, so selecting items there undid the deferral.
- A date-filtered history request pages the cached day index instead of
  rebuilding the matching history. `start_date`/`end_date` are whole-day
  bounds, so a date range only ever drops whole days -- it never changes what
  a day contains -- which is what makes this safe.

Regressions: `app.tests.test_history_item_projection` (no history query may
name an unread item column; the query count must not grow with the history)
and `app.tests.test_history_date_window` (the indexed window and the builder
agree day for day, entry for entry, over the same range).

## Measured

Benchmark container, one gunicorn worker with recycling disabled, 3 GB cgroup
limit, against a copy of a real library (18,230 episode plays). The request is
`GET /api/v1/history/?media_type=tv&start_date=2022-01-01` -- 6,454 plays in
range, 1,197 days, a 56 KiB response of 20 days.

| | worker PSS delta | duration cold / warm | cgroup peak |
| --- | --- | --- | --- |
| before | +808,613 KiB (790 MiB) | 8.5s / 12.0s | 3.0 GB (the limit) |
| item projection | +105,221 KiB (103 MiB) | 2.7s / 2.5s | 1.17 GB |
| + day-index paging | +6,072 KiB (5.9 MiB) | 1.3s / 0.30s | 1.51 GB |

**99.2%** off the request's resident cost. The delta is the worker's own
PSS/anonymous PSS, read from `smaps_rollup` inside the container, and it does
not come back: at +30s, +60s and +120s the worker sits where it landed, which
is why the before number is a permanent 790 MiB and not a spike.

Repeated cycles settle rather than ratchet: +0.2 to +0.4 MiB per request after
the first, no recycle. Before the change the first request had already taken
the arena to its high-water mark, so every later delta read ~0 while the
worker sat at 867 MiB.

An unfiltered `start_date=1900-01-01` over all 18,230 plays exhausted the 3 GB
container on the old code and returned 502. It now answers from the index.

### Scaling

The same request at six history sizes, one probe per rung against a freshly
restarted worker, 8 GB limit so the old code's largest rung completes rather
than being killed. Every rung returns the same 20-day page.

| plays in range | before | KiB/play | after | KiB/play |
| --- | --- | --- | --- | --- |
| 590 | +69 MiB | 119.8 | +5.8 MiB | 10.11 |
| 1,806 | +200 MiB | 113.3 | +3.8 MiB | 2.17 |
| 5,045 | +628 MiB | 127.4 | +4.0 MiB | 0.81 |
| 9,050 | +1,041 MiB | 117.8 | +3.8 MiB | 0.43 |
| 13,300 | +1,499 MiB | 115.4 | +3.8 MiB | 0.29 |
| 18,230 | +1,924 MiB | 108.1 | +3.2 MiB | 0.18 |

Before: a flat ~118 KiB of worker memory per historical event, over a 31x
range. After: a constant ~4 MiB whatever the history size -- the per-play
slope falls as the history grows, which is what "bounded by the response"
looks like. Duration goes from 1.1s-22.5s to 0.09s-0.48s.

### Response equivalence

Same total, same days, same ordering, same entry titles and counts.
Verified field by field on the real library, comparing the indexed page
against the builder's output for the same range:

| case | days | index total | day/count/order mismatches | field diffs |
| --- | --- | --- | --- | --- |
| `media_type=tv` all-time | 4,260 | 4,260 | 0 | `display_title` x9 |
| `media_type=movie` from 2019 | 388 | 388 | 0 | none |

The paginated `total` is exact in both, including the sparse case where most
indexed days hold no matching entry. `played_at_local` renders identically on
the wire from either path.

`display_title` changes for 9 of ~600 entries -- to the value a type-only
request already returned before this change. Those media have duplicate `Item`
rows differing only in `library_media_type` and carrying conflicting titles
("Big Boys" stored as the title of a *Big Boys* episode), and the two builders
picked different rows. Projecting the builder's title map to five columns
does not itself change the winner: compared over all 35,972 keys in the
library, the map is identical either way. Verified on the old build: a type-only request and a
date-range request already disagreed on exactly those 9 entries. This removes
the inconsistency. The duplicate rows are a separate data-integrity issue.

## Still proportional to the history

These build every matching entry and then emit a page of it:

- `/api/v1/history/?flat=1`. Flat mode paginates over individual entries, so
  it cannot page the day index; it builds every matching entry to slice one
  window of them.
- Any filter that reaches inside a day -- `genre`, `implied_genre`,
  `person_source`/`person_id`, `tv`, `season`, `album`, `artist`,
  `podcast_show`, `media_id`/`source`. The day index is keyed by media type,
  so these still go through the builder.
- The web history view (`app/history_views.py`) calls the builder directly.
- A custom-list home row (`users/home_screen.py::_custom_list_entries`)
  hydrates every item in the list to emit ten cards. Its watch-providers
  cost is gone, but slicing in SQL first needs an ordering contract this
  code does not have: the home sort keys live on the per-media-type media
  model (`date_added` sorts by `media.created_at`, not by the list's own
  `date_added`), and duplicate media rows per item are resolved in Python
  afterwards.

They are all far cheaper than they were, because the item projection applies
to the builder itself, but they remain O(matching history) in entry
dictionaries rather than O(response).

## Measuring it

Metadata language repair retains History caches when provider responses leave
`title`, `original_title`, and `localized_title` unchanged. Synopsis is not stored
in History cards. A changed title invalidates both History styles for every
tracking owner of the repaired item, once per batch, and requests the existing
default-style refresh. Earlier title writes are flushed even when a later fetch
fails or a preference reset ends the batch. Language agreement, the second
post-fetch preference check, cursor/backoff, and bulk continuation routing remain
unchanged. Regressions live in `app.tests.test_metadata_language_repair`.

`scripts/history_request_memory.py` runs inside the disposable benchmark
container and reports the serving worker's RSS/PSS/private/anonymous around a
request, with the response hash beside it so a candidate can be compared on
memory and on equivalence at once.

It deliberately does not warm up the scenario it measures. glibc keeps a grown
arena, so a warm-up request moves the whole cost into an unmeasured call and
leaves every later delta reading ~0 -- which is exactly how this cost stayed
invisible in earlier aging runs.

## The same columns in the import preload

Every importer starts by loading the user's whole library through
`get_existing_media` and `get_existing_children`
(`integrations/imports/helpers.py`). Those loaded full items too, so a large
library ran a Trakt export import out of memory before it wrote a row (#1252).
They now defer the same columns. With 600 rows carrying a 32 KiB payload the
preload's peak Python allocation went from ~51 MiB to ~2.5 MiB.

Regressions: `integrations.tests.imports.test_import_preload_memory` (the
preloaded items defer the heavy columns; its `slow` benchmark compares the
preload against full hydration).
