# Cache Refresh Analysis (Single Source of Truth)

## Scope and intent
- This document enumerates every cache and cache-busting path in the codebase, including Redis caches, DB TTL guards, UI refresh logic, static asset cache busting, and service worker behavior.
- Goal: keep Yamtrack workable in a 768 MB environment by avoiding large single-pass rebuilds and favoring incremental, per-day caches. This is still a work in progress, not a solved problem.
- This document replaces issue-22 notes as the canonical caching reference.
- Boundaries: this does not cover browser caching outside our headers, any CDN/proxy caching if added later, or third-party provider rate limits (which can look like cache issues).

## Invariants (must always hold)
- Index must be stable; page slicing must not depend on cache hits.
- Day payload keys must be a pure function of `(user_id, logging_style, date, schema version)`.
- A refresh task must warm the days requested when `day_keys` are provided.

## Cache backend and defaults
- `src/config/settings.py`:
  - Redis cache via `django_redis` (`CACHES["default"]`), `CACHE_TIMEOUT=86400` (24h), `VERSION=11`, `KEY_PREFIX` from `REDIS_PREFIX`.
  - Session storage uses cache backend (`SESSION_ENGINE = django.contrib.sessions.backends.cache`).
  - `django-select2` uses Redis with `SELECT2_CACHE_PREFIX`.
- `src/config/test_settings.py`:
  - Cache timeout 5h with FakeRedis connection pool; Celery eager.

## Cache key versions (manual busts)
- History: `history_index_v14`, `history_day_v14`.
- Statistics: `statistics_page_v2`, plus per-day `stats:day`.
- Time-left: `time_left_sorted_v12`.
- Bump these prefixes to invalidate old keys without delete_pattern.

## History cache (per-day)
- Files: `src/app/history_cache.py`, `src/app/views.py`, `src/templates/app/history.html`, `src/static/js/cache-updater.js`.
- Keys:
  - Index: `history_index_v14_{user_id}_{logging_style}` (stores `day_keys` + `built_at`).
  - Day payloads: `history_day_v14_{user_id}_{logging_style}_{YYYYMMDD}`.
  - Lock: `history_refresh_lock_v14_{user_id}_{logging_style}` with `started_at` and optional `day_keys`.
- TTL/stale:
  - Day + index TTL: 6h (`HISTORY_CACHE_TIMEOUT`).
  - Stale after: 1h by default (`HISTORY_STALE_AFTER`, configurable).
  - Lock TTL: 120s (matches frontend polling); stale lock max age 5m.
  - `HISTORY_DAYS_PER_PAGE=30`, `HISTORY_WARM_DAYS` default 0.
  - `HISTORY_COLD_MISS_WARM_DAYS` default 30 (used when index is missing).
- Behavior (page-centric):
  - `get_cached_history_page()` fetches index, then day payloads for the requested page.
  - Index miss: schedule refresh (warm_days) and return empty to avoid inline rebuild.
  - Page-day miss: schedule refresh for those specific day keys and return empty.
  - Partial misses: inline build missing day payloads and cache them.
  - Stale index: serve cached data and schedule refresh.
  - Filters/date_filters/logging_style overrides bypass the per-day cache and build inline to avoid key explosion; filtered views are intentionally not cached.
- Refresh orchestration:
  - `schedule_history_refresh()` debounces via `cache.add`, writes lock payload, and queues Celery task. Falls back to inline refresh if Celery is unavailable.
  - `refresh_history_cache()` rebuilds index and optionally warms day payloads:
    - If `day_keys` provided, warms only those days ("page_days" mode).
    - Else warms `HISTORY_WARM_DAYS` from the newest end of the index.
- Invalidation triggers:
  - `invalidate_history_days()` deletes specific day payloads and schedules index rebuild.
  - `invalidate_history_cache()` clears only when no lock, unless `force=True`.
  - Signals (`src/app/signals.py`) invalidate days on Episode/Movie/Music/Podcast/Game/BoardGame changes.
  - `delete_history_record` forces invalidation because historical deletes do not fire model signals.
  - Changing `game_logging_style` invalidates and schedules refresh (`src/users/views.py`).

## Statistics cache (per-day + range)
- Files: `src/app/statistics_cache.py`, `src/app/views.py`, `src/templates/app/statistics.html`, `src/static/js/cache-updater.js`.
- Keys:
  - Range cache: `statistics_page_v2_{user_id}_{normalized_range}`.
  - Range lock: `statistics_page_v2_refresh_lock_{user_id}_{normalized_range}`.
  - Day cache: `stats:day:{user_id}:{YYYY-MM-DD}`.
  - Dirty set: `stats:dirty:{user_id}` (list of ISO dates).
  - History version: `stats:history_version:{user_id}` (bumped on invalidation).
  - Schedule dedupe: `stats:refresh:scheduled:{user_id}:{history_version}:{range}` (TTL 10m).
- TTL/stale:
  - Range TTL 6h; stale after 15m if no history_version.
  - Day TTL 30d (`STATISTICS_DAY_CACHE_TIMEOUT`).
  - Range lock TTL 5m; stale lock max age 5m.
  - Warm window: `STATISTICS_WARM_DAYS` default 2 (today + yesterday if in range).
- Behavior (incremental):
  - `refresh_statistics_cache()` builds only:
    - dirty days,
    - warm days,
    - missing day caches in the target range.
  - Range aggregates are built by summing cached day payloads.
  - If a day payload is missing during aggregation, it is built for that day only.
  - "All Time" uses sparse day list (`_get_sparse_activity_days`) instead of a contiguous range.
  - Non-predefined ranges are computed inline and not cached.
- Logging:
  - `stats_day_summary` includes `plays`, `missing_runtime`, `missing_genres`.
  - `stats_range_summary` logs days, refreshed, nonempty, elapsed, totals.
- Invalidation triggers:
  - `invalidate_statistics_days()` deletes day caches, updates dirty set, and bumps history_version.
  - Signals (`src/app/signals.py`) invalidate day caches on media changes and schedule all ranges.
  - `delete_history_record` invalidates stats days and schedules all ranges.

## Discover cache (Redis tab cache over DB row/profile caches)
- Files: `src/app/discover/tab_cache.py`, `src/app/discover/cache_repo.py`, `src/app/discover/service.py`, `src/app/views.py`, `src/templates/app/discover.html`, `src/static/js/cache-updater.js`.
- Redis tab keys:
  - Tab payload: `discover_tab_v1_{user_id}_{media_type}_{show_more}`.
  - Refresh lock: `discover_tab_v1_refresh_lock_{user_id}_{media_type}_{show_more}`.
  - Activity version: `discover_tab_v1_activity_version_{user_id}_{media_type}`.
  - Schedule dedupe: `discover_tab_v1_refresh_scheduled_{user_id}_{activity_version}_{media_type}_{show_more}`.
  - Active-page context: `discover_tab_v1_active_{user_id}`.
  - Request warmup throttle: `discover_tab_v1_request_warm_{user_id}`.
  - Discover action undo snapshot: `discover_action_undo_v1_{user_id}_{token}`.
- Tab payload fields:
  - `built_at`
  - `activity_version`
  - `media_type`
  - `show_more`
  - serialized `rows`
  - per-row `reserve_items` used for immediate card replacement after a quick action
  - `optimistic_refreshing` when the cached tab was locally patched and a background rebuild is still pending
- TTL/stale:
  - Tab payload TTL: 6h.
  - Stale after: 15m.
  - Refresh lock TTL: 5m.
  - Active Discover context TTL: 45s.
  - Request warmup throttle TTL: 15m.
  - Undo snapshot TTL: 60s.
  - Recently-built window for UI polling: 60s.
- Lower cache layers kept in DB:
  - `DiscoverRowCache` for row payloads.
  - `DiscoverTasteProfile` for per-user taste vectors.
  - `DiscoverApiCache` for provider payloads.
- Behavior:
  - `get_tab_rows()` serves Redis-backed tab payloads in steady state.
  - `discover_debug=1` bypasses the tab cache and renders rows inline from the service layer.
  - Discover views call `get_tab_rows(..., allow_inline_bootstrap=False)`, so page loads never block on a cold rebuild.
  - Cold miss: queue an immediate background refresh, render the loading placeholder, and let the polling loop swap fresh rows in once the build finishes.
  - Stale hit: serve stale rows, mark them stale in the UI, and queue an immediate rebuild for the active tab.
  - Discover page loads also warm missing sibling tabs in the background at low priority.
  - App startup schedules `warm_discover_startup_tabs` once per day via `discover_tab_startup_scheduled`; it warms the default `all` tab for active users if that cache is missing or stale.
  - `DiscoverWarmupMiddleware` schedules per-user warmup on the first eligible authenticated HTML request every 15 minutes; it prioritizes `all` and then warms enabled Discover media tabs without blocking the response.
  - Existing Redis tab payloads are intentionally kept during invalidation so the last built rows can keep rendering while background refresh work runs.
- Manual refresh semantics:
  - Refresh button invalidates the active tab and also `all` when the active tab is not `all`.
  - Lower-level row/profile caches are cleared immediately.
  - Provider API cache is cleared only for manual refreshes (`DiscoverApiCache` is not cleared for automatic invalidation).
  - The UI polls `cache_status?cache_type=discover&media_type=...&show_more=...` and re-fetches only `#discover-rows` when the rebuild completes.
- Discover quick-action fast path:
  - Discover cards can post `planning`, `dismiss`, or `undo` to `discover_action`.
  - `planning` creates a visible tracked row only if the user does not already track that concrete media item; existing tracked rows are treated as a no-op success and do not create duplicates or undo state.
  - `dismiss` writes `DiscoverFeedback(not_interested)` and never creates a visible library entry.
  - Cached tabs are patched optimistically via `apply_cached_action()`:
    - remove the acted-on identity from visible `items` and hidden `reserve_items`
    - rebalance rows immediately from `reserve_items`
    - preserve existing cross-row dedupe semantics
    - mark the tab payload with `optimistic_refreshing=True`
  - Undo uses the Redis snapshot key above to restore the prior cached tab payloads and reverse the side effect for up to 60 seconds.
  - The optimistic patch is only a UI fast path; the normal background rebuild still runs afterward to refill reserves, recompute profiles, and rerank rows.
- Invalidation triggers:
  - Tracked-media saves/deletes invalidate the affected media type plus `all`.
    - `movie -> movie + all`
    - `tv/season/episode -> tv + all`
    - `anime -> anime + all`
    - `music -> music + all`
    - `podcast -> podcast + all`
    - `book/comic/manga/game/boardgame -> same type + all`
  - `ItemTag` changes invalidate the tagged item's mapped Discover media type plus `all`.
  - `ItemPersonCredit` changes invalidate `movie + all` or `tv + all` for users tracking the credited item.
  - Invalidation bumps `activity_version` and clears only the lower-level DB caches, so stale Redis tab payloads can still render while the rebuild runs.
- Rebuilds only for people who use Discover (`DISCOVER_EAGER_REBUILD_DAYS`, default 3):
  - `mark_active()` (every Discover page load, tab poll and recommendations API call) also writes `discover_tab_v3_recently_used_{user_id}`, kept for that many days.
  - A tracked-media change queues background tab rebuilds only when that key is present, the tab is the active one, or the cache cannot answer (`has_key` returns None). Otherwise it only bumps `activity_version`; the next visit serves the stale rows and queues an interactive rebuild, as for any stale tab.
  - On such a deferred change the taste profile is still deleted (feeds and row refreshes read it outside a tab rebuild and check no version), but the cached rows are kept: every read compares their stored `activity_version`, and `refresh_tab_cache()` deletes rows built for an older version before it builds.
  - Hidden-feedback invalidation and manual refreshes stay eager.
  - Rollback: `DISCOVER_EAGER_REBUILD_DAYS=0` rebuilds after every change for everyone, the previous behaviour.
- Active Discover priority mode:
  - Requests that originated from Discover store a short-lived active-tab context from `next` first, then `Referer`.
  - When a later tracked-media mutation affects that active Discover tab:
    - Discover refresh queues immediately (`countdown=0`, `debounce=0`).
    - History refresh is delayed to 15s.
    - Statistics refresh is delayed to 20s.
  - This is queue prioritization and dedupe, not task cancellation.
- Discover feedback model:
  - Hidden dismiss feedback is stored in `DiscoverFeedback`, not in the global visible `Status` enum.
  - Recommendation suppression and negative-learning are same-media-type only, plus the `all` tab through that media type's contribution.
  - Exact-item filtering excludes hidden feedback from all Discover rows, including provider-backed rows.
  - Similarity downranking currently applies only to local/personalized Discover rows using negative genre/tag/person affinities derived from dismissed items.
  - When a user later tracks the same item normally, the matching `DiscoverFeedback(not_interested)` row is deleted automatically.

## Metadata refresh overlay (runtime + genre backfill)
- Statistics day builds detect missing runtime/genres and enqueue backfills:
  - Runtime backfill queue: `runtime_backfill_items_queue` + `runtime_backfill_items_scheduled`.
  - Episode runtime queue: `runtime_backfill_episode_queue` + `runtime_backfill_episode_scheduled`.
  - Genre backfill queue: `genre_backfill_items_queue` + `genre_backfill_items_scheduled`.
  - Queue TTL: 1h; scheduled keys short TTL (debounce).
- Backfill throttling is stored in DB (`MetadataBackfillState`):
  - Exponential backoff (`next_retry_at`) and `give_up` after 6 failures.
  - Logged as `metadata_backfill_retry_later` and `metadata_backfill_give_up`.
  - Runtime failures use `runtime_minutes=999999` sentinel to avoid repeat work.
- Metadata refresh banner:
  - `stats:metadata_refresh:{user_id}` lock (TTL 10m).
  - `stats:metadata_refresh_built:{user_id}` timestamp (TTL 30d).
  - `cache_status` exposes `metadata_refreshing`, `metadata_built_at`, `metadata_recently_built`.
  - `_schedule_metadata_statistics_refresh()` marks metadata refresh, invalidates stats days for affected items, and schedules all ranges.

## Cache status endpoint + UI refresh loop
- `src/app/views.py` `cache_status()`:
  - History: returns `exists`, `built_at`, `is_stale`, `is_refreshing`, `recently_built`.
  - Statistics: adds `any_range_refreshing`, `refresh_scheduled`, and metadata refresh fields.
  - Discover: returns the same tab status shape used by history (`exists`, `built_at`, `is_stale`, `is_refreshing`, `recently_built`, `refresh_scheduled`) for the active `media_type` + `show_more`.
  - Clears stale locks to avoid "refreshing" stuck states.
  - If stats cache is stale and no lock exists, it schedules a refresh during polling.
- `src/static/js/cache-updater.js`:
  - Polls every 2.5s while the cache refresh is healthy, with a 180s active-online timeout and a 10s timeout for each cache-status request.
  - Stops immediately on non-retryable HTTP failures, including 400/401/403/404. This prevents a permanent response from becoming a repeated request loop.
  - Retries 408/425/429/5xx responses and network/request timeouts with exponential backoff from 2.5s up to 30s. `Retry-After` is respected within the remaining 180s refresh window.
  - Pauses requests while the browser reports that it is offline, resumes on the `online` event, and does not count offline time against the refresh timeout.
  - Aborts an in-flight request when the updater stops or the page exits, so boosted navigation cannot leave an orphaned request behind.
  - Uses `built_at` + `wasRefreshing` to avoid reload loops.
  - For statistics, reloads when the active range is ready; other range refreshes can continue in the background.
  - Self-check: `node src/static/js/cache-updater.selfcheck.mjs` covers terminal 404, transient backoff, `Retry-After`, offline pause, request abort/timeout, and the healthy polling cadence.
- Transport boundary:
  - Cache-status refresh remains ordinary HTTP polling. It is intermittent, needs normal HTTP failure semantics, and does not have a measured continuous server-push workload that justifies a persistent WebSocket or WebTransport session.
  - Revisit push transport only when profiling shows a real persistent-update workload and a measurable benefit over bounded HTTP polling.
- Templates:
  - `src/templates/app/history.html`: polling is skipped for filtered views (bypass cache).
  - `src/templates/app/statistics.html`: polling only for predefined ranges.
  - `src/templates/app/discover.html`: shows a top-level background-refresh banner, polls Discover cache status on load/tab switch, and refreshes only `#discover-rows` when the rebuild completes.

## Known failure modes (symptoms -> suspects)
- Page 1 works, pages 2+ blank: page miss schedules refresh, but task warms newest days instead of requested `day_keys`.
- Repeated `warmed=30` with continued page misses: day key mismatch or refresh task not honoring `day_keys`.
- UI stuck on "refreshing": stale lock not cleared (clock skew or lock payload missing `started_at`).
- Cache-status returns 404/401/403 repeatedly: client polling should stop after the first non-retryable response; run the cache-updater self-check and verify the route/auth state instead of increasing retries.
- Cache-status request hangs or returns 5xx/429: the client should use bounded backoff and stop at the overall active-online timeout rather than sending fixed-interval failures.
- Browser goes offline during a refresh: status polling pauses and resumes after reconnect; static assets can remain available from the service worker cache, but dynamic tracked-data routes are not made offline-safe by browser HTML caching.
- "Warmed" logs but view still misses: key mismatch (logging_style default vs explicit, YYYYMMDD vs YYYY-MM-DD, or version mismatch).
- Inline rebuild spikes on filtered views: expected; filtered paths bypass cache and build inline.
- Book/comic/manga details show plain author text (not links): provider item cache payload is stale and lacks `authors_full`, so `authors_linked` cannot be built from IDs.

## Ops runbook (verify + actions)
- Cold miss vs broken:
  - Expected: first request logs miss + scheduled refresh, second request after task logs hit>0 and renders entries.
  - Broken: refresh completes but page still shows hit=0/miss=30 for the same days.
- Cache-status browser failure:
  - `404`, `401`, or `403`: expect one failed cache-status request, then no further requests from that updater.
  - `408`, `425`, `429`, or `5xx`: expect the retry delay to increase instead of remaining at 2.5s; a valid `Retry-After` can extend the delay within the overall refresh window.
  - Offline: expect no cache-status requests until the browser reports `online` again.
  - Hung request: expect the request to abort at 10s and enter transient-failure backoff.
- Log checks for history:
  - Good: `history_day_cache_cold_miss ... scheduled=True`, then `history_cache_refresh_done ... warmed=30`, then `history_day_cache_get_many ... hit>0`.
  - Bad: `history_cache_refresh_done ... warmed=30`, but subsequent `history_day_cache_get_many ... hit=0 miss=30`.
- Log checks for statistics:
  - Good: `stats_day_summary` for dirty day(s), `stats_range_summary` with refreshed>0.
  - Bad: `stats_range_summary` shows refreshed=0 while dirty set exists.
- Safe cache busting:
  - Bump versioned prefixes (`history_index_v14`, `history_day_v14`, `statistics_page_v2`, `time_left_sorted_v12`) to invalidate.
  - `clear_search_cache()` only clears `search_*` keys; it does not clear `bgg_search_*` or `musicbrainz_*`.

## Other Redis caches (application)
- Time-left sorted TV lists:
  - Key: `time_left_sorted_v12_{user_id}_{media_type}_{status}_{search}_{direction}_{rating}`.
  - TTL: 300s for list entries; registry TTL uses `CACHE_TIMEOUT`.
  - Registry key: `time_left_sorted_v12_registry_{user_id}`.
  - Invalidation: `cache_utils.clear_time_left_cache_for_user` on TV saves and explicit Season saves (Season overrides `save()` and now clears this cache).
  - Release sync throttle: `timeleft:release-sync:{source}:{media_id}` (TTL 1h).
- TMDB season cache (used by time-left + runtime fallback):
  - Key: `tmdb_season_{media_id}_{season_number}`.
  - Stored by TMDB provider; default TTL.
- Calendar:
  - `tvmaze_map_{tvdb_id}` cached with default TTL.
- Webhooks:
  - `anime_mapping_data` cached with default TTL.
- Runtime population guards:
  - `runtime_population_startup_scheduled` (24h, app startup).
  - `runtime_population_completed` (1h, tasks).
- Search cache clearing:
  - `clear_search_cache()` deletes `search_*` only (TMDB/MAL/IGDB/Hardcover/Pocket Casts).
  - `bgg_search_*`, `musicbrainz_*`, and other prefixes are not cleared by this endpoint.

## HTTP response cache control
- `@never_cache` on `track_modal` to prevent browser caching. The view also explicitly sets `Cache-Control: no-cache, no-store, must-revalidate`, `Pragma: no-cache`, and `Expires: 0` headers for Safari compatibility.
- `@never_cache` on `collection_modal` to prevent browser caching. The view also explicitly sets `Cache-Control: no-cache, no-store, must-revalidate, max-age=0`, `Pragma: no-cache`, `Expires: 0`, and `Vary: Cookie, HX-Request` to avoid Safari reusing stale modal HTML.
- `@never_cache` on `lists.views.lists` to avoid stale list data after items are added/removed. The view also explicitly sets `Cache-Control: no-cache, no-store, must-revalidate`, `Pragma: no-cache`, `Expires: 0`, and `Vary: Cookie` headers for Safari compatibility and to ensure user-specific responses aren't cached.
- `@never_cache` on `lists.views.list_detail` to avoid stale list grids/context rows after HTMX sorts and back/forward navigation.
- `@never_cache` on `app.views.media_list` to avoid stale media grids/context rows after HTMX sorts and back/forward navigation.
- `custom_lists.html` reloads on `pageshow` when `event.persisted` is true to bust Safari's back/forward cache restoring older list HTML.
- `list_detail.html` reloads on `pageshow` when `event.persisted` is true to bust Safari's back/forward cache restoring older list HTML.
- `media_list.html` reloads on `pageshow` when `event.persisted` is true for the same Safari back/forward cache issue.
- `media_details.html` reloads on `pageshow` when `event.persisted` is true to bust Safari's back/forward cache restoring the page.
- `custom_lists.html` disables HTMX history caching on `#lists-grid` (`hx-history="false"`) to prevent stale list grids from being restored.
- `list_detail.html` disables HTMX history caching on `#items-grid` (`hx-history="false"`) to prevent stale list grids from being restored.
- `custom_lists.html` sets `htmx.config.getCacheBusterParam = true` so HTMX GETs append `org.htmx.cache-buster`, which avoids Safari reusing cached list HTML.
- `list_detail.html` sets `htmx.config.getCacheBusterParam = true` so HTMX GETs append `org.htmx.cache-buster`, which avoids Safari reusing cached list HTML.
- `media_details.html` sets `htmx.config.getCacheBusterParam = true` for HTMX GET requests (including track and collection modal requests). This prevents stale modal HTML on previously visited pages. The configuration is set immediately and also on DOMContentLoaded as a fallback to ensure it's applied before any HTMX requests.
- `base.html` adds a global HTMX `htmx:configRequest` hook to append a `cache_bust` param for `track_modal` and `collection/modal` requests. This is a targeted safety valve when HTMX cache busting + response headers are still not enough (e.g., Safari reusing modal HTML).
- `custom_lists.html` includes meta tags (`cache-control`, `pragma`, `expires`) in the `<head>` to provide additional cache-busting hints to browsers.
- Podcast episode list fragment sets `Cache-Control: no-cache, no-store, must-revalidate`, plus `Pragma`/`Expires`.
- `sync_metadata()` uses `cache.ttl()` to prevent immediate re-sync, and deletes provider cache keys when allowed.
- `media_details()` now self-heals stale reading metadata cache entries with missing `authors_full`: when author details exist but linkable author IDs are missing, it deletes `{source}_{media_type}_{media_id}`, refetches provider metadata, and backfills `ItemPersonCredit` author links.
- Static asset busting: `get_static_file_mtime()` appends `?mtime` to static URLs.
  - Applied to `css/main.css`, `js/date-range.js`, `js/statistics-charts.js`, and `js/barcode-scanner.js` (added for cache invalidation during development/debugging).
- Lists view uses annotated `items_count` instead of `items.count()` to avoid stale prefetch cache. The count is always computed fresh from the database via `Count("items", distinct=True)` annotation.

## Service worker caching (frontend)
- `src/static/js/serviceworker.js` caches a small list of static assets and additional static GETs.
- Does not cache HTML navigation or `/api/*` requests (keeps cache-status real-time and avoids serving authenticated dynamic HTML from a cross-session browser cache).
- Offline support from this worker is limited to static assets. Full offline tracked-data use needs app-owned local state plus queued, idempotent replay/reconciliation; it must not be implemented by caching authenticated route responses indiscriminately.
- Versioned via `CACHE_NAME` and clears older caches on activate.

## Provider/API caches (Redis-backed)
Default TTL is `CACHE_TIMEOUT` unless specified.

- TMDB (`src/app/providers/tmdb.py`):
  - `search_TMDB_{media_type}_{query}_{page}`
  - `find_TMDB_{external_id}_{external_source}`
  - `TMDB_movie_{media_id}`, `TMDB_tv_{media_id}`
  - `tmdb_season_{media_id}_{season_number}`
- IGDB (`src/app/providers/igdb.py`):
  - Access token: `IGDB_access_token` (TTL `expires_in - 60`).
  - `external_game_IGDB_{source}_{external_id}`, `search_IGDB_game_{query}_{page}`, `IGDB_game_{media_id}`.
- MAL (`src/app/providers/mal.py`):
  - `search_MAL_{media_type}_{query}_{page}`, `MAL_anime_{media_id}`, `MAL_manga_{media_id}`.
- Hardcover (`src/app/providers/hardcover.py`):
  - `search_HARDCOVER_{media_type}_{query}_{page}`, `HARDCOVER_book_{media_id}`.
- ComicVine (`src/app/providers/comicvine.py`):
  - `search_COMICVINE_{media_type}_{query}_{page}`, `COMICVINE_comic_{media_id}`,
    `COMICVINE_issue_{media_id}`, `COMICVINE_similar_{publisher_id}_{current_id}`.
- GCD (`src/app/providers/gcd.py`):
  - `search_gcd_{media_type}_{query}_{page}`, `gcd_comic_{media_id}`,
    `gcd_comicissue_{media_id}`, `gcd_series_{media_id}_issues`, `gcd_publisher_{publisher_id}`.
- OpenLibrary (`src/app/providers/openlibrary.py`):
  - `search_OPENLIBRARY_{media_type}_{query}_{page}`, `OPENLIBRARY_book_{media_id}`.
- MangaUpdates (`src/app/providers/mangaupdates.py`):
  - `search_MANGAUPDATES_{media_type}_nsfw_{MU_NSFW}_{query}_{page}` (the flag selects the result set, so both states cache separately), `MANGAUPDATES_manga_{media_id}`.
- MangaBaka (`src/app/providers/mangabaka.py`):
  - `search_MANGABAKA_manga_nsfw_{MANGABAKA_NSFW}_{query}_{page}` (the flag selects the result set, so both states cache separately), `MANGABAKA_manga_{media_id}`.
- BGG (`src/app/providers/bgg.py`):
  - `bgg_search_ids_{query}` (24h).
  - `bgg_search_page_{query}_p{page}` (24h).
  - `bgg_metadata_{media_id}` (7d).
- MusicBrainz (`src/app/providers/musicbrainz.py`):
  - `wikipedia_data_{title}` (7d, misses 1d).
  - `musicbrainz_cover_{release_id}_{release_group_id}` (7d).
  - `musicbrainz_search_{query}_p{page}` (24h, with `_no_art` variant).
  - `musicbrainz_recording_{media_id}` (7d).
  - `musicbrainz_artist_search_{query}_p{page}` (24h).
  - `musicbrainz_release_search_{query}_p{page}` (24h, with `_no_art` variant).
  - `musicbrainz_artist_{artist_id}` (7d).
  - `musicbrainz_artist_discography_v2_{artist_id}` (7d, with `_no_art` variant).
  - `musicbrainz_release_for_group_{release_group_id}` (7d).
  - `musicbrainz_release_{release_id}` (7d).
  - `musicbrainz_combined_search_{query}_p{page}` (24h).
- Pocket Casts (`src/app/providers/pocketcasts.py`):
  - `search_POCKETCASTS_podcast_{query}_{page}` (default TTL).
  - `itunes_lookup_{itunes_collection_id}` (7d).
- iTunes music artwork (`src/integrations/itunes_music_artwork.py`):
  - `itunes_album_artwork_{artist}_{album}` (7d, misses cached 7d).
  - `itunes_artist_artwork_{artist}` (7d, misses cached 7d).
- Pocket Casts artwork (`src/integrations/pocketcasts_artwork.py`):
  - `podcast_artwork_{podcast_uuid}` (7d, misses cached 1d).
- Other:
  - `tvmaze_map_{tvdb_id}` (calendar, default TTL).
  - `anime_mapping_data` (webhooks, default TTL).

## DB TTL guards and in-memory caches
- Plex sections refresh:
  - `_should_refresh_plex_sections()` uses `PlexAccount.sections_refreshed_at` with `PLEX_SECTIONS_TTL_HOURS`.
- Backfill guardrails:
  - `MetadataBackfillState` enforces retry windows and give-up behavior.
- Request-scope caches:
  - `history_cache.py` uses local maps (genre_cache, episode_title_map, track_duration_cache).
  - `statistics.py` uses `season_metadata_cache` and avoids API calls unless cached metadata exists.
- ORM prefetch cache invalidation:
  - `refresh_from_db()` + `prefetch_related_objects()` used after progress updates.

## 768 MB environment notes
- History and statistics moved to per-day caches to avoid scanning and aggregating huge ranges in one task.
- Cold misses schedule background refreshes and return empty payloads rather than inline rebuilding entire history.
- Statistics "All Time" uses a sparse day list (distinct activity days) instead of contiguous day buckets.
- This reduces memory spikes but does not fully guarantee 768 MB safety yet; continue profiling under real workloads.

## Redis memory notes
- Approx key count per user per logging_style: `~#days` day payloads + 1 index + locks. For long histories (10k+ days), key volume can be large even with 6h TTLs.
- Redis eviction policy matters in a 768 MB footprint (allkeys-lru vs volatile-lru changes whether hot caches survive long enough for paging).

### Bounded since #521
Redis previously had no ceiling at all: no `maxmemory` in any shipped compose file, so it
defaulted to `maxmemory 0` / `noeviction` and grew until the host OOMed.

- `app/redis_tuning.py` applies `maxmemory` (~15% of detected host memory, clamped to
  96 MiB–512 MiB) and `maxmemory-policy volatile-lru` at startup, but **only when the
  operator has not set `maxmemory` themselves**. Override or disable with
  `FLOPPY_REDIS_MAXMEMORY` (`0` = never touch Redis). `manage.py tune_redis --dry-run`
  shows what it would do.
- The policy is `volatile-lru`, not `allkeys-lru`, because this Redis is also the Celery
  broker: Kombu's queue keys carry no TTL, so `volatile-lru` cannot evict them.
  **Consequence: any `cache.set(..., timeout=None)` writes a key eviction can never
  reclaim.** Prefer an explicit TTL for anything large.
- The largest values are `tmdb_tv_*` and `tmdb_season_*`. Seasons now expire at
  `SEASON_CACHE_TIMEOUT` (12h) rather than the 24h default, and `search_*` at
  `SEARCH_CACHE_TIMEOUT` (1h) since those keys are unbounded in count (arbitrary query
  strings) and the cheapest to refetch.
- Backfill work queues are Redis **sets** (`app/backfill_queue.py`), not pickled lists.
  The old read-modify-write of a whole ID list per batch was O(N²) bytes per reconcile
  sweep and, with `--appendonly yes`, O(N²) disk writes too.

## Cache failures are not errors
`CACHES["default"]["OPTIONS"]["IGNORE_EXCEPTIONS"]` is on, so a Redis fault reads as a
cache miss instead of propagating. That is right for the ~460 read-through call sites and
wrong for the ~20 that use `cache.add` as a lock, because `None` is falsy and reads as
"someone else holds it". Those go through `app/cache_safety.py`, which exposes the three
states and requires the caller to say which failure direction is safe:

- `ON_ERROR_SKIP` — best-effort maintenance. Skipping a cache warm during an outage costs
  nothing; adding load to a struggling host costs plenty.
- `ON_ERROR_PROCEED` — work the user is waiting on (imports, webhook scrobbles). Silently
  refusing is data loss; these paths have their own downstream duplicate protection.

`DJANGO_REDIS_LOG_IGNORED_EXCEPTIONS` keeps the swallowed failures visible in the logs.
Redis is deliberately **not** in the `/health/` liveness subset any more — the app serves
from Postgres through an outage, so reporting unhealthy would get a working container
restarted.
