# Code map

The page to read before the other architecture documents: which module owns a
subject, where new code of each kind goes, and what to search before adding a
helper. Each line points at the document that governs the subject; this page
does not restate those rules.

`scripts/check_code_map.py` fails when a path or test label in backticks here
does not exist. A change that adds a module or moves a responsibility updates
this page in the same pull request.

## Where things live

Django apps live under `src/`. Paths below are repository-relative.

| Subject | Owning modules | Governing document |
|---|---|---|
| Models and their querysets | `src/app/models/` (one file per subject; `src/app/models/manager.py` holds the per-row hydration and progress annotations shared by detail, Home and list pages) | `docs/agents/domain_model.md` |
| List-shaped pages (media list, list pages, Home shelves, list API) | `src/app/library_query/` decides which rows and in what order; `src/app/media_list_views.py` renders one page of them; `src/app/media_list_filters.py` is the API entry | `docs/architecture/library-query.md` |
| Detail pages | `src/app/media_details_views.py`, `src/app/season_details_views.py`, `src/app/detail_builders.py`, `src/app/detail_related.py` | `docs/architecture/media-card.md` |
| History | `src/app/history_views.py`, `src/app/history_processor.py`, `src/app/history_cache*.py` | `docs/architecture/history-memory.md` |
| Statistics | `src/app/statistics*.py` (pipeline), `src/app/stats_*.py` (sections), `src/app/statistics_views.py` | `docs/architecture/statistics-sync.md` |
| Discover | `src/app/discover/`, `src/app/discover_views.py`, `src/app/tasks_discover.py` | |
| Writing plays and tracking state | `src/app/services/` (one module per write subject), `src/app/fork_services_*.py` (movie, episode, music, podcast and history writes the API shares), `src/app/save_views.py`, `src/app/track_actions.py` | `docs/architecture/watched-state-sync.md`, `docs/architecture/podcast-plays.md` |
| Metadata providers | `src/app/providers/` (one module per provider; `src/app/providers/services.py` dispatches and owns the HTTP session) | `docs/architecture/outbound-fetch.md`, `docs/architecture/provider-credential-consumers.md` |
| Game artwork | `src/app/game_artwork/` | `docs/architecture/game-artwork.md` |
| Imports, scheduled syncs and webhooks | `src/integrations/imports/`, `src/integrations/tasks/`, `src/integrations/webhooks/` | `docs/architecture/webhook-write-rules.md`, `docs/architecture/webhook-delivery.md`, `docs/architecture/connection-health.md` |
| View caches | `src/app/cache_utils.py` | `docs/architecture/view-caches.md` |
| Background tasks | `src/app/tasks.py`, `src/app/tasks_*.py`, `src/integrations/tasks/`, `src/lists/tasks.py`, `src/events/tasks.py`; queues in `src/config/celery.py` and `src/config/settings.py` | `AGENTS.md` (queue rule under Local Commands) |
| Calendar and release events | `src/events/` | `docs/architecture/calendar-reload.md` |
| Lists and smart lists | `src/lists/` (`src/lists/smart_rules.py` for rules, `src/lists/views_*.py` by subject) | `docs/architecture/library-query.md` |
| Home, settings and accounts | `src/users/` (`src/users/home_screen.py` for Home, `src/users/provider_views.py` for provider settings) | `docs/architecture/provider-settings.md` |
| REST API | `src/api/` (endpoints in `src/api/fork_views_*.py`, routes in `src/api/fork_urls.py`) | `docs/architecture/api-scopes.md` |
| SQLite, startup and process runtime | `src/config/` | `docs/architecture/sqlite-runtime-safety.md`, `docs/architecture/sqlite-startup.md` |
| Django templates and template tags | `src/templates/`, `src/app/templatetags/`, `src/users/templatetags/` | `docs/ui/source-map.md` |
| React UI (web) and Expo (native) | `frontend/` (shared components in `frontend/ui/`), `native-proof/` | `docs/architecture/ui-modernization.md`, `docs/ui/component-register.md` |
| Tests | `src/<app>/tests/`; repository scripts' tests in `scripts/tests/` | `docs/architecture/test-suite-cost.md` |

### Names that do not explain themselves

- **`stats_*` and `statistics_*`.** There is a consistent meaning, with two
  exceptions. `src/app/stats_*.py` compute one section of the Statistics page
  (time, score, activity, one media type) from rows they are given.
  `src/app/statistics*.py` are the pipeline around them: `statistics.py`
  orchestrates and re-exports the sections, and the others build, cache,
  aggregate, refresh and sync days and ranges. The exceptions are
  `statistics_highlights.py` and `statistics_talent.py`, which compute
  sections. Rule for new files: a section calculator is `stats_<section>.py`;
  pipeline code is `statistics_<stage>.py`.
- **`fork_`.** The prefix marks a file that was kept apart from a file shared
  with the upstream project, so upstream changes could merge cleanly. It says
  nothing about the subject. In `src/api/`, every endpoint module is
  `fork_views_<subject>.py` wired through `fork_urls.py`; add new endpoints to
  the matching subject file, or a new `fork_views_<subject>.py` beside them.
  Everywhere else, do not start a new `fork_` file: name it for its subject.
- **`src/app/views.py`** is mostly an index that re-exports the subject view
  modules (`*_views.py`) for `src/app/urls.py`, plus a few views of its own.
  Put a new view in the subject's `*_views.py`, not in `views.py`.
- **List logic has one home.** Which rows a list page shows and in what order
  is `src/app/library_query/`. `src/app/media_list_views.py` turns one page
  into a response. `src/app/models/manager.py` hydrates and annotates rows
  once they are chosen; its full-list `get_media_list` remains only for the
  surfaces `library-query.md` lists as keeping their own pipeline.
- **`*_helpers.py` and `*_utils.py`.** Each is tied to its subject:
  `history_cache_utils.py` (History cache keys), `stats_utils.py` (Statistics
  values), `metadata_utils.py` (storing provider metadata on items),
  `video_helpers.py` (video plays API and YouTube imports),
  `collection_helpers.py` (collection metadata), `cache_utils.py` (view-cache
  keys and clearing). `src/app/helpers.py` takes small value and request
  helpers with no narrower subject. Do not add a new `utils` module.

<!-- Helper homes: add the helper families that have one shared home here, one line each with the module that owns them. -->
<!-- Shared view builders: add the public modules that own detail-page builders here. -->
<!-- Split view files: add each subject module split out of a large view file here. -->

## Where new code goes

| New code | Home | The rule that matters |
|---|---|---|
| A page view | The subject's `*_views.py` in its app; route in that app's `urls.py` | Authentication and public routes: `docs/agents/view_authentication.md` |
| An API endpoint | `src/api/fork_views_<subject>.py`, route in `src/api/fork_urls.py` | Declare its scope: `docs/architecture/api-scopes.md` |
| A filter or sort for a list-shaped page | `src/app/library_query/filters.py` or `src/app/library_query/sorts.py` | "Adding a filter or sort" in `docs/architecture/library-query.md`, which also lists the tests and surfaces to update |
| A cache for a page | Key builder and registration in `src/app/cache_utils.py` | Clear it through `clear_library_view_caches`: `docs/architecture/view-caches.md` |
| A background task | `src/app/tasks_<subject>.py` (or the owning app's `tasks`) | Default `celery` queue unless a page waits on it: `AGENTS.md`, Local Commands |
| A call to a metadata provider, including its image lookups | `src/app/providers/<provider>.py`. The one exception: game logos and backdrops from art-only services go in `src/app/game_artwork/` | Pages never wait on it (rule 2): fetch in a task or on save, render what is stored. Outbound rules: `docs/architecture/outbound-fetch.md`; credentials: `docs/architecture/provider-credential-consumers.md` |
| An import or scheduled sync | `src/integrations/imports/<source>.py` | A run that finds no change writes nothing (see the rules below) |
| A new media type | Follow the playbook | `docs/agents/media_type_integration.md` |
| A value helper (parse, format, coerce) | The module of the subject it serves: a parser for one provider's response stays in that provider's module, a Statistics formatter in `src/app/stats_utils.py`. Only a helper that several subjects need goes in `src/app/helpers.py` | Search first (below) |
| A shared UI component | `frontend/ui/` | `docs/architecture/ui-modernization.md`; register it in `docs/ui/component-register.md` |
| A test | `src/<app>/tests/test_<subject>.py` | Tag benchmarks and browser tests `slow`: `AGENTS.md`, Testing |

## Four rules the code holds to

Each rule has tests that fail when it breaks.

1. **A page hydrates one page of rows**, whatever its filters and sort need.
   `api.tests.test_media_list_page_cost`, `app.tests.test_media_list_projection`,
   `app.tests.test_library_query`.
2. **A page renders what is stored** and does not wait for background or
   provider work, or for the database writer.
   `app.tests.test_read_pages_do_not_write`, `app.tests.test_statistics_cache`
   (serving makes no provider call).
3. **One function clears view caches:** `clear_library_view_caches` in
   `src/app/cache_utils.py`. `app.tests.test_view_cache_invalidation`.
4. **A recurring task with nothing to do costs milliseconds** and writes
   nothing. `integrations.tests.test_recurring_sync_unchanged`,
   `app.tests.test_backfill_day_keys`, `app.tests.test_runtime_backfill_requeue`.

## Before adding a helper, module or abstraction

Run these three searches. Each one alone has found an existing copy that a
new helper would have duplicated. The example is a helper that reads a year;
replace the words with your own.

```bash
# 1. By name: the verbs and noun you would have used, with or without a leading underscore
rg -n "def _?(get|parse|coerce|extract)_\w*year" src --glob '!**/tests/**'

# 2. In the declared homes for small helpers
rg -n "^def " src/app/helpers.py src/app/*_utils.py src/app/*_helpers.py src/*/helpers.py

# 3. By what it wraps: the expression at its core
rg -n "\.year\b|\[:4\]|fromisoformat\(|parse_date\(" src --glob '!**/tests/**'
```

If a copy exists, use it, or make the existing one public in its subject
module. If several private copies exist, consolidate them in one subject
module rather than adding another.
