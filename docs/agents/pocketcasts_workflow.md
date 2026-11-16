# Pocket Casts Workflow

This document captures the end-to-end Pocket Casts workflow in Yamtrack: connection, import, recurring sync, data modeling, UI surfaces, and the duplicate-handling logic. It is intentionally detailed around recurring sync and duplicate creation.

## Scope and entry points
- **Connection + scheduling UI**: `src/integrations/views.py` (`pocketcasts_connect`, `import_pocketcasts`, `pocketcasts_disconnect`) and `src/templates/users/import_data.html`.
- **Account state**: `src/integrations/models.py` (`PocketCastsAccount`).
- **Import pipeline**: `src/integrations/imports/pocketcasts.py` (`PocketCastsImporter`).
- **Recurring task**: `src/integrations/tasks.py` (`import_pocketcasts_history`).
- **RSS episode refresh**: `src/events/tasks.py` (`refresh_podcast_episodes`).
- **Podcast models**: `src/app/models.py` (`PodcastShow`, `PodcastEpisode`, `Podcast`, `PodcastShowTracker`).
- **Podcast UI**: `src/app/views.py` (`media_details` for pocketcasts, which is the one show page; `podcast_show_detail` only redirects there via `podcast_show_url`; `podcast_save`, `podcast_mark_all_played`) plus list adapters.
- **External sources**: `src/integrations/pocketcasts_api.py`, `src/integrations/podcast_rss.py`, `src/integrations/pocketcasts_artwork.py`, and `src/app/providers/pocketcasts.py` (iTunes search/lookup).

## Data model (podcasts)
- **PodcastShow**: container for show metadata; unique on `podcast_uuid`. Fields include `title`, `author`, `image`, `description`, `rss_feed_url`, `genres`, `language`.
- **PodcastEpisode**: container for episode metadata; unique on `episode_uuid`. Contains `show`, `title`, `published`, `duration`, `audio_url`, `episode_number`, `season_number`, `is_deleted`.
- **Podcast (Media subclass)**: per-user tracking record for a specific episode. Holds `item`, `show`, `episode`, `status`, `progress`, `played_up_to_seconds`, `last_seen_status`, `end_date`. Multiple Podcast rows per episode are allowed to represent multiple plays.
- **PodcastShowTracker**: per-user tracking for a show (status/score/dates/notes), similar to ArtistTracker.
- **Item**: the generic media identity. For podcasts, `Item.media_id` is the episode UUID for episode tracking, or the show UUID when building list adapters.

## External APIs and data sources
- **Pocket Casts API** (`src/integrations/pocketcasts_api.py` and `src/integrations/imports/pocketcasts.py`):
  - `POST /user/login` for credentials login (returns access/refresh tokens).
  - `POST /user/refresh` for token refresh.
  - `POST /user/history` returns the last 100 history entries (no pagination). Not currently called
    by the importer (only referenced by the unused `pocketcasts_api.validate_token`). Pocket Casts'
    own support docs confirm this history is account-wide and subscription-independent — it includes
    episodes from shows the user has since unfollowed — but the 100-entry cap has no known
    workaround, so it can only supplement forward-looking sync, not backfill full unfollowed-show
    history. See "Unfollowed-show history (issue #609)" below for the discovery writeup.
  - `POST /user/podcast/list` returns show metadata (descriptions, titles, authors).
  - `GET /discover/images/{size}/{podcast_uuid}.jpg` for authenticated artwork (used as fallback).
- **RSS feeds** (`src/integrations/podcast_rss.py`): public RSS/Atom parsing for full episode lists and metadata.
- **Artwork fallback** (`src/integrations/pocketcasts_artwork.py`): prefers RSS feed artwork, then iTunes search; Pocket Casts images require auth.
- **iTunes search/lookup** (`src/app/providers/pocketcasts.py`): used for search results and for show enrichment when the app only has an iTunes ID.

## Connection and credential storage
- **Connect flow** (`pocketcasts_connect` in `src/integrations/views.py`):
  - Requires email + password (for Apple/Google accounts, user must set a password first).
  - Calls `pocketcasts_api.login`, stores encrypted credentials and tokens.
  - Parses JWT expiration via `parse_token_expiration`.
  - Clears `connection_broken` on success.
  - Creates a 2-hour Celery beat schedule if it does not exist.
  - Immediately enqueues an initial import (`tasks.import_pocketcasts.delay`).
- **Disconnect flow** (`pocketcasts_disconnect`):
  - Deletes the periodic task.
  - Deletes the `PocketCastsAccount` row (full disconnect).
- **Account state** (`PocketCastsAccount` in `src/integrations/models.py`):
  - `access_token`, `refresh_token`, `email`, `password` stored encrypted.
  - `token_expires_at`, `last_sync_at`, `connection_broken` tracked.
  - `is_connected` uses credentials or tokens; `connection_broken` only blocks if no credentials.
  - `is_token_expired` compares `token_expires_at` with now.

## Scheduling and recurring sync
- **Recurring task**: `Import from Pocket Casts (Recurring)` in `src/integrations/tasks.py`.
  - Scheduled via `django_celery_beat` for every 2 hours (minute 0, hour */2).
  - The scheduled task (`import_pocketcasts_history`) enqueues the actual import (`import_pocketcasts.delay`), so two tasks are involved.
- **Manual import**: `import_pocketcasts` view queues an import and also sets up the recurring schedule if missing.
- **Mode**: Pocket Casts imports always run with `mode="new"` (no overwrite workflow).
- **No global lock**: there is no shared lock around imports. Concurrent manual + recurring runs can overlap.

## Import pipeline (PocketCastsImporter.import_data)
Step-by-step behavior in `src/integrations/imports/pocketcasts.py`:
1. **Ensure token**: `_ensure_valid_token` prefers login with credentials; falls back to refresh token. Sets `connection_broken` on failures, can delete schedule via `_disconnect_account`.
2. **Fetch show metadata**: `pocketcasts_api.get_podcast_list` populates `self.podcast_metadata` (descriptions, titles, authors).
3. **Fetch history**: `_fetch_history` calls `/user/history` (last 100 entries only).
4. **First import check**: `is_first_import = not Podcast.objects.filter(user=self.user).exists()`.
5. **First pass episode processing**:
   - For each history entry: call `_process_episode`.
   - If the episode is new and this is not the first import, defer completion date inference and collect into `new_completed_podcasts` if it looks completed.
6. **Completion date inference (recurring sync)**:
   - Sync window: `last_sync_at` to now (defaults to now - 2 hours if none).
   - Uses `_get_history_items_in_range` to build a timeline of completed items (podcast, music, episodes, movies).
   - `_infer_completion_date` attempts to place completion times without overlapping scrobbled items, sequences new podcasts by published date, and clamps to sync window.
7. **Bulk create / cleanup**:
   - `helpers.cleanup_existing_media` (only relevant in overwrite mode).
   - `helpers.bulk_create_media` for new Podcast entries (bulk create with history).
   - Records pending history for new entries after bulk create (`_pending_history`).
8. **RSS sync for processed shows**:
   - `_sync_episodes_from_rss` runs for any show with `rss_feed_url`.
9. **Update last sync**: `PocketCastsAccount.last_sync_at = now`.
10. **Duplicate cleanup**: `_cleanup_duplicate_episodes` runs the global merge routine.
11. **Cache refresh**: triggers history + statistics cache refresh if any podcasts were imported.

## Episode processing details (_process_episode)
### Show creation and metadata
- `PodcastShow` is created from Pocket Casts data when missing.
- Title/author/description are updated from history or `/user/podcast/list`.
- RSS feed discovery:
  - Uses metadata fields (`rssUrl`, `feedUrl`) if present.
  - Falls back to iTunes lookup via `pocketcasts_artwork.fetch_podcast_artwork_and_rss`.
  - Stores `rss_feed_url` on the show.
- Artwork handling:
  - Pocket Casts image URLs require auth, so they are treated as temporary.
  - If no public artwork, attempts RSS + iTunes (cached).
  - Falls back to Pocket Casts URL if no alternatives.
- Ensures a `PodcastShowTracker` exists for the user.

### Episode creation and merging
- First attempts to find a `PodcastEpisode` by `episode_uuid` (Pocket Casts UUID).
- If not found, tries **title + published date** within the same show:
  - If matched and the existing episode already has the Pocket Casts UUID elsewhere, it merges:
    - Moves `Podcast` rows to the Pocket Casts UUID episode.
    - Moves `Item` references to the Pocket Casts UUID item.
    - Deletes the duplicate episode and item.
  - Otherwise, updates the matched episode UUID to the Pocket Casts UUID.
- If still not found, creates a new `PodcastEpisode`.
- Updates episode fields: duration, published, audio_url, is_deleted.

### Item creation
- Creates/updates `Item` with `media_id=episode_uuid`, `source=pocketcasts`, `media_type=podcast`.
- Stores `runtime_minutes` and `release_datetime` from episode data.

### Podcast (per-user) creation/update
- Looks up existing Podcast via:
  - `self.existing_podcasts[(episode_uuid, pocketcasts)]`, built at importer init.
  - Fallback: `Podcast.objects.filter(item=item, user=user).order_by("-created_at").first()`.
- If existing Podcast is completed with an end_date, processing is skipped.
- Progress/status calculation:
  - Uses `playingStatus` and `playedUpTo`.
  - Requires significant progress (> 60s or > 10% of duration) to mark completed.
  - Tracks `played_up_to_seconds` and `last_seen_status` for UI.
- Completion date:
  - First import: published + duration (or published if no duration).
  - Recurring sync: inferred later if new; or derived from last in-progress record.
- History recording:
  - For existing entries, `_record_history` updates progress to create historical entries.
  - Skips duplicate history if end_date is within 5 minutes of latest history.

## Completion date inference (recurring sync)
- Inference uses the sync window, existing history (podcasts + scrobbled items), and ordering by published date.
- If there is a last in-progress record, it uses remaining time from that progress.
- Avoids overlapping with precise history items (music/TV episodes).
- Clamps completion times to the sync window bounds.

## RSS episode sync
There are two RSS-driven sync paths:
- **Per-import**: `_sync_episodes_from_rss` in `PocketCastsImporter`.
  - Fetches all RSS episodes for processed shows.
  - Matches by GUID or title + published date.
  - Updates metadata or creates missing episodes.
  - Preserves Pocket Casts UUIDs when they look like UUIDs (36 chars + 4 hyphens).
- **Global refresh**: `events.tasks.refresh_podcast_episodes`.
  - Runs for all shows with `rss_feed_url` (triggered from `reload_calendar` when called without a specific user).
  - Same match logic and updates.
  - Runs `_cleanup_duplicate_episodes_global` after refresh.

## UI surfaces and manual actions
- **Media list**: shows `PodcastShowTracker` entries in place of individual episodes (`media_list` adapter in `src/app/views.py`).
- **Show detail**:
  - `media_details` routes to show view for `source=pocketcasts` + `media_type=podcast`.
  - If `media_id` is numeric, treats it as an iTunes ID and creates a show with `podcast_uuid="itunes:{id}"`, then fetches episodes from RSS.
  - Ensures full RSS episode list for the show when loading detail.
  - Uses `podcast_episodes_api` for infinite scroll.
- **Track modal**:
  - Show tracker uses `PodcastShowTrackerForm`.
  - Episode play tracking uses `podcast_save`.
- **Manual play** (`podcast_save`):
  - Creates/updates a `Podcast` row and writes history by updating `end_date`.
  - Avoids duplicate history when end_date is within 5 minutes of the last entry.
- **Mark all played** (`podcast_mark_all_played`):
  - Optionally refreshes episodes from RSS first.
  - Creates a `Podcast` entry for every unplayed episode.

## Duplicate creation and recurring sync (deep dive)
This is the critical area for recurring sync issues.

### Where duplicates can be created
- **History returns duplicates within a single import**:
  - `/user/history` can include the same episode multiple times.
  - New episodes added to `bulk_media` are not added to `self.existing_podcasts` during the same run.
  - Result: multiple `Podcast` rows for the same episode can be created in the same import pass (before bulk create hits the DB).
- **Concurrent imports**:
  - The recurring task enqueues the real import task (`import_pocketcasts_history` -> `import_pocketcasts.delay`).
  - If a new scheduled run fires before the previous import finishes, two imports can run in parallel.
  - There is no global lock in `import_media`, so both imports can create overlapping Podcast rows.
- **Episode UUID mismatches between Pocket Casts and RSS**:
  - RSS GUIDs often differ from Pocket Casts UUIDs.
  - If title or published date is missing/mismatched, the title+date fallback cannot match, so a second `PodcastEpisode` is created.
  - The episode-level unique constraint is only on UUID, so duplicates with different UUIDs are allowed.
- **Show duplication from iTunes enrichment**:
  - Shows created from iTunes use `podcast_uuid="itunes:{id}"`.
  - Pocket Casts imports use the Pocket Casts UUID.
  - Same real-world show can exist twice at the show level; episodes may be duplicated as a result.
- **Existing Podcast row ambiguity**:
  - `self.existing_podcasts` is built once and can only hold one Podcast per episode UUID.
  - If multiple Podcast rows already exist (multiple plays), the dict keeps only one (unordered).
  - The importer may update a non-completed row and create a new completion even when another completed row already exists.
- **RSS refresh timing**:
  - Every import triggers `events.tasks.reload_calendar.delay()`, which runs a global RSS refresh.
  - RSS refresh can update or create episodes while an import is processing, leading to mismatched UUIDs and duplicate episodes.

### Current defenses against duplicates
- **Early exit for completed episodes**: if an existing Podcast row is completed with `end_date`, `_process_episode` returns early.
- **In-import dedupe**: history entries are deduped by episode UUID, preferring completed entries and any available event timestamp.
- **Fallback lookup by Item**: if UUID lookup fails, the importer tries `Podcast.objects.filter(item=item, user=user)`.
- **Existing completed guard**: before creating a new Podcast row, it checks for any completed Podcast for the same Item.
- **Duplicate completion guard**: if Pocket Casts repeats a completed entry with the same progress/duration, the importer skips creating a new play row.
- **Episode merge logic**: if a title+date match exists but another episode has the Pocket Casts UUID, it merges and deletes the duplicate.
- **Global cleanup**: `_cleanup_duplicate_episodes_global` merges episodes when (show, normalized title, published date) match and prefers Pocket Casts UUIDs.
- **History dedupe**: `_record_history` skips entries when `end_date` is within 5 minutes of the last history entry.

### Gaps to watch during recurring sync
- **No in-memory dedupe for new episodes**: the bulk list can contain repeated Podcast entries if history repeats an episode.
- **Title/date matching is fragile**: missing `published` dates or minor title changes prevent merges.
- **UUID churn from RSS**: episodes created from RSS with a hash GUID cannot be matched later without title/date; they remain as parallel episodes.
- **Concurrent imports are not serialized**: recurring + manual imports can overlap.

## Unfollowed-show history (issue #609)
Discovery for [#609](https://github.com/dannyvfilms/Floppy/issues/609): can Floppy import listening
history for Pocket Casts shows the user has since unfollowed?

- **The current importer cannot see this history at all.** `import_data()` discovers episodes by
  walking `POST /user/podcast/list` (subscribed shows) and then `POST /user/podcast/episodes` per
  show. A show that's been unfollowed is absent from `/user/podcast/list`, so nothing about it is
  ever fetched, regardless of past listening.
- **`POST /user/history` is the account-wide, subscription-independent source.** Pocket Casts' own
  support docs state Listening History "will show episodes regardless of whether you are following
  the podcast or not." Multiple independent reverse-engineered clients (e.g.
  `rv3392/spotify-to-pocketcasts`, the `pocketcasts` npm package) confirm the endpoint is a plain
  bearer-token `POST` with no required body.
- **It is capped at the last 100 played episodes with no known pagination.** This matches Pocket
  Casts' own documented behavior for the official apps ("will only show the last 100 episodes
  you've played") and this repo's existing note next to the endpoint. No pagination/cursor
  parameter is documented by any community client.
- **Live re-verification against a real connected account was inconclusive, not disproving.**
  Testing from this repo (2026-08) got `400 Bad Request` from `/user/history` across several
  request-body variants matching what community clients use — but a known-good, currently-used
  endpoint (`/user/podcast/list`) also started 400ing after the same short burst of test calls,
  which points to rate-limiting from the test itself rather than a genuinely broken contract. The
  account's stored token/connection state was unaffected. Re-testing should be a single, isolated
  request (not a burst) if attempted again.

**Conclusion:** unfollowed-show history is *proven reachable in principle* via `/user/history`
(subscription-independent, confirmed by Pocket Casts' own docs), but *hard-capped at 100 entries
account-wide* with no way to page past it — so it can only catch unfollowed-show plays going
forward from whenever `/user/history` polling is added, not backfill history older than the last
100 plays. That gap is a Pocket Casts platform limitation, not something Floppy can work around.
Per this issue's scope rule, no importer/endpoint changes were made here — a follow-up
implementation (poll `/user/history` in addition to the existing per-show walk, using new episode
UUIDs not present in `podcast_metadata` to trigger `_fetch_show_full_metadata` lookups) should be
its own scoped issue if pursued.

## Cache refresh
- When podcasts are imported, `schedule_history_refresh` and `statistics_cache.schedule_all_ranges_refresh` are called to keep history and stats caches fresh.

## Quick reference: key files
- Auth helpers: `src/integrations/pocketcasts_api.py`
- Import pipeline: `src/integrations/imports/pocketcasts.py`
- Recurring tasks: `src/integrations/tasks.py`
- RSS parsing: `src/integrations/podcast_rss.py`
- Artwork helpers: `src/integrations/pocketcasts_artwork.py`
- Models: `src/app/models.py`
- Podcast UI: `src/app/views.py`
