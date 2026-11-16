# Browser journey measurements (BENCH-03)

Run from the repository root on macOS/Linux with locked development dependencies:

```bash
uv sync --locked
uv run --no-sync playwright install chromium  # only if Chromium is missing
uv run --no-sync python scripts/benchmark_browser.py --control --output /tmp/browser-before
uv run --no-sync python scripts/benchmark_browser.py --control --output /tmp/browser-after --baseline /tmp/browser-before/report.json
```

Each output directory must be new. The command creates **its own synthetic
SQLite database, loopback-only Django WSGI server, authenticated session and
headless Chromium**. It does not connect to an existing server, Chrome profile,
personal browser session, Redis or Celery worker. No provider credentials are
needed. Stop or leave your normal development server running independently.
Browser launch/socket access may require sandbox permission; use the installed
Chromium rather than attaching to a personal debugging port.
Linux hosts missing system browser libraries may need
`uv run --no-sync playwright install --with-deps chromium`.

Default inputs are seed `1729`, 1,000 tracking rows, two fresh worker processes,
desktop `1280x900` and mobile `390x844`, and these eleven routes:

`home`, `movies`, `tv`, `music`, `podcasts`, `history`, `statistics`, `calendar`,
`collection`, `lists`, `list_detail`.

Each worker measures a fresh authenticated context followed by a repeat
navigation in the same context. With `--control`, it also measures matched
journeys without the browser observers/request listeners; worker two reverses
the mode order to reduce order bias. A full command collects 176 journeys and
takes several minutes. The default without controls collects 88 journeys.

For a shorter run:

```bash
uv run --no-sync python scripts/benchmark_browser.py --size 100 --routes home movies history --viewports desktop --observe-ms 500 --control --output /tmp/browser-small
```

Sizes are multiples of ten, at least ten; repeats must be at least two. This
reuses [BENCH-01/02 fixtures and resource helpers](benchmark-fixtures.md), adds
deterministic artist/album/podcast-show trackers, Home rows and a custom list,
and exports `repeat-N/fixture.json.gz` before session-driven requests. The fixture
checksum includes those UI rows and the synthetic user's fixed login date.
Statistics All Time is precomputed from that data, outside journey timers.
Provider HTTP returns a mocked offline failure; queued tasks are counted without
execution. External browser assets return a deterministic SVG. Background
artwork/import completion is intentionally not simulated. Calendar has no seeded
events unless the v2 extras are on. The fixtures are disposable and must never be loaded into production.

## Loading baseline (protocol v2)

Protocol `bench03-browser-v2` measures the React-era pages on the 20k fixture.
One command, from a clean checkout, with an installed Chrome instead of
Playwright's own Chromium:

```bash
uv run --no-sync python scripts/benchmark_browser.py --browser-channel chrome --size 20000 --runs 3 --max-load 4 --output /tmp/loading-plain
uv run --no-sync python scripts/benchmark_browser.py --browser-channel chrome --size 20000 --runs 3 --max-load 4 --viewports mobile --throttle mobile4x --dpr 3 --output /tmp/loading-phone
uv run --no-sync python scripts/benchmark_browser.py --browser-channel chrome --size 20000 --max-load 4 --routes podcast_detail album_detail_huge --viewports desktop --states fresh --output /tmp/loading-stress
```

Timings are only worth keeping on a quiet machine. `--max-load 4` refuses to
start above a 1-minute load average of 4, records the load at the start and
end of every worker in `report.json` (`load_average`), and marks the run
`discarded` if any worker ends above it. Comparisons involving a discarded run
withhold their ratios.

What v2 adds:

- **More pages.** Games, Books, Search results, a History date range, Tags,
  Settings, Discover (legacy, for the shell), and detail pages for a movie, a
  16-season show, a season, a 40-track album and a 60-episode podcast show
  (`podcast_show`). Detail paths are resolved after seeding from rows found by
  their created names.
- **Stress routes** (`STRESS_ROUTES`, outside the default set, named explicitly
  with `--routes`): `podcast_detail` and `album_detail_huge`, the base fixture's
  one podcast show and one album, which hold size / 10 children each (2,000 at
  20k). A journey that fails to load is recorded under `journey_errors` instead
  of stopping the run.
- **Fixture extras** (`app.benchmark_data.add_loading_extras`, skip with
  `--no-extras`): provider-shaped artwork URLs on every item, the 16-season
  show `bench-long-show`, a 40-track album, a 60-episode podcast show, dense
  calendar events around the fixture date, and 40 tags. Other benchmarks keep their fixture checksums.
- **Realistic artwork** (`--images realistic`, the default): `benchmark_artwork.py`
  generates JPEGs at the size each URL names and serves them over loopback
  HTTPS; Chromium's resolver sends the provider hosts there. Throttling and the
  HTTP cache apply, which they do not for faked responses. Other external hosts
  are refused. `--images placeholder` restores the v1 SVG routing.
- **Production-like static files:** nginx.conf's immutable cache header and
  gzip are applied in the runner, so cold and warm bytes differ as they do in a
  real install.
- **Four moments per load:** TTFB and DOMContentLoaded (Navigation Timing),
  island mount and useful content (the first frame after a per-page selector in
  `READY` matches; a MutationObserver watches for it), and settled (last
  response once nothing has been in flight for 750 ms). Requests and wire bytes
  are split into phases: to DOMContentLoaded, to content, to settled.
  Layout shift and long tasks are recorded too.
- **Cold and warm:** cold is a new browser context plus a cleared server
  cache; warm is a second visit in that context. The first journey of each
  worker is marked `first_in_process` (cold process). `--runs N` repeats the
  cold and warm pair N times inside each worker.
- **Throttling:** `--throttle mobile4x` is Lighthouse's phone profile (4x CPU,
  150 ms RTT, 1.6 Mbps down). `--dpr` sets the device pixel ratio.
- **Service workers:** `--service-workers allow` lets the app's worker run.
- **Simulators:** `--serve-port 8455` seeds the same fixture and serves it on
  loopback for the iOS simulator and Android emulator (as 10.0.2.2) instead of
  driving Chromium. A throwaway password goes to `serve/credentials.json`.
- `--dump-dom` saves cold DOM snapshots, for picking readiness selectors.

## Metric definitions (v1)

- **Shell time:** Python wall time through `DOMContentLoaded`, including the
  Playwright command round trip.
- **Readiness:** visible `main .media-card` for media lists, otherwise visible
  `main`, followed by two animation frames. Pending deferred rows/HTMX at this
  point and at observation end are retained. It is a bounded readiness proxy,
  not a claim that all background content finished.
- **Navigation timing:** native browser DNS/connect/request/response, DOM/load
  timestamps and byte counts from `PerformanceNavigationTiming`. Navigation
  entries can have `loadEventEnd=0` if not complete yet; unsupported/missing values
  are null. The default dwell is 1,000 ms after readiness.
- **LCP:** latest observed numeric candidate, excluding DOM elements/text and
  URLs. The value is provisional at observation end; this is not
  a finalized field Web Vital. Missing candidates/unsupported APIs are null.
- **Long tasks:** supported main-thread tasks of at least 50 ms, with start and
  duration only, capped at 10,000 entries with a dropped count.
- **Network waterfall:** request start/end, browser timing phases, method/type,
  status and failure flag. Queries, origins, fragments and identifier path
  segments are removed. A stable opaque path hash keeps different row IDs from
  being merged into false polling candidates. Up to 10,000 requests are retained;
  unfinished requests remain explicit at window end.
  Request timing phases of `-1` mean unavailable/not performed (for example TLS
  on plain HTTP); native sentinels are retained rather than converted to zero.
- **Polling:** fetch/XHR counts, intervals and inferred Hz for repeated endpoint
  groups. Repetition alone does not prove a timer. Declared HTMX `every` triggers
  are also retained. Short windows are right-censored: absence of repeated calls
  is not evidence that a page never polls.

To observe longer polling cadences without multiplying every route's runtime:

```bash
uv run --no-sync python scripts/benchmark_browser.py --routes home --viewports desktop --states fresh --observe-ms 32000 --output /tmp/browser-polling
```

The server clock is fixed at `2025-01-01T00:00:00Z`. Browser `Date` is anchored to
that epoch and advances with native monotonic time; native Performance entries,
timers and animation frames remain intact. Playwright's general clock emulation
is deliberately not used because it replaces the Performance entry API too.
Mobile means touch/viewport emulation on desktop Chromium, DPR 1, UTC, `en-US`
and light scheme. CPU/network are not throttled to phone hardware.

## Resource use and observer cost

Python CPU/RSS/USS/PSS snapshots cover the worker, including the threaded WSGI
server and Python Playwright callbacks. Chromium CDP provides browser process
IDs internally for process CPU deltas and psutil memory snapshots; PIDs are not
exported. Per-metric availability counts accompany memory totals. A total is
null if any enumerated process lacks that metric; RSS sums double-count shared
pages. Snapshot churn can miss exited-process CPU and transient memory peaks;
new-process CPU includes startup. Renderer CDP task/script/layout durations and
heap bytes are additional measurements, not process RSS.

The Playwright Node driver and total-machine resources are not measured. PSS is
normally unavailable on macOS; USS depends on permissions. Missing metrics are
null, never reported as zero. Hardware/runtime/cgroup/rlimit metadata comes from
BENCH-02; it does not establish host identity or every hardware/load condition.

Resources are sampled outside the operation wall timer, although browser CPU
deltas can include measurement calls. Five idle-browser resource probes after
contexts close calibrate their own cost; this can cost less than probing an
active renderer. Matched-control readiness ratios estimate the incremental
observer/network-listener overhead. Controls still retain timers, readiness
checks, fixed-date setup, routing and resource snapshots. Ratios are noisy
observations, not proof of a production regression or speedup.

For opt-in main-renderer CPU profiles:

```bash
uv run --no-sync python scripts/benchmark_browser.py --routes movies --viewports desktop --profile --output /tmp/browser-profile
```

This adds separate warm-context `profiled` trials and sanitized
`profile-desktop-movies.cpuprofile` artifacts under each repeat directory. Open
them with Chromium DevTools' JavaScript profiler. These extra trials are listed
separately from normal measurements; they include profiler overhead.

## Artifacts and reproducibility

- `report.json`: full raw samples, input/settings metadata, fixture/source/lockfile
  hashes, Git SHA/dirty state, versions, summaries, control ratios and optional
  baseline comparisons. Output/baseline command paths are redacted.
- `report.md`: readable readiness/variance, LCP/long-task/network/CPU tables,
  overhead ratios, comparison tables and metric limitations.
- `repeat-N/sample.json` and `fixture.json.gz`: portable worker receipts and
  anonymous fixture. The SQLite database is deleted after a successful run.

No cookies, request/response headers or bodies, screenshots, HTML, console text,
DOM text or personal tabs are exported. CPU profiles retain public function names
and sanitized URLs. File/data URL content is redacted. Review any separately
recorded local invocation/log before sharing it; synthetic model tokens in the
fixture are known test credentials and are not production-safe.

Repeat workers must have identical fixture checksums or the command fails.
Timing/resource samples vary with process layout, caches, permissions and load.
Summaries report sample count, min/max and population CV; two repetitions are a
repeatability check, not statistical confidence. `--baseline` withholds ratios
when recorded browser/version, fixture, machine, viewport, observation window,
route order, state policy or settings differ. Git/source revisions may differ
because comparing code changes is the purpose. BENCH-01/02 or the old browser
test outputs are different protocols and cannot serve as strict baselines.

## Validation

```bash
SECRET=test-only FLOPPY_TEST_FAST_DB=1 scripts/test.sh app.tests.test_browser_journeys.BrowserJourneyContractTests app.tests.test_benchmark_fixtures --parallel 1
SECRET=test-only FLOPPY_TEST_FAST_DB=1 RUN_PLAYWRIGHT_BENCHMARKS=1 scripts/test.sh app.tests.test_browser_journeys.BrowserObserverIntegrationTests app.tests.test_media_list_browser_benchmarks --parallel 1
uv run --no-sync ruff check src
uv run --no-sync ruff check scripts/benchmark_browser.py scripts/benchmark_fixtures.py
```

The browser integration test proves that native navigation timing survives the
fixed date, a real LCP candidate and induced 80 ms long task are captured,
120 ms polling is observed, and profile/query secrets are not exported. Browser
tests are tagged `slow`, `benchmark`, `playwright` and require the explicit opt-in.
The legacy benchmark now starts/stops Playwright outside ORM setup/teardown,
asserts successful authenticated navigation and mocks external assets.
