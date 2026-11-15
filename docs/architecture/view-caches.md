# Per-user view caches

Three short-lived caches make the media list, Home and the TV time-left sort fast
on a repeat visit. They are cleared through one function so every write path
agrees on what a change can make stale.

## The caches

| View | What it holds | Lifetime | Registry |
|------|---------------|----------|----------|
| Media list | Rendered list pages and their filter summaries | 60 s (filters 300 s) | `register_media_list_cache_key` |
| Home rows | Built shelves; ranked shelf order; Up Next membership | 60 s; order 1800 s; Up Next 300 s | `register_home_row_cache_key` |
| Time left | The TV list's time-left order | 300 s | `register_time_left_cache_key` |

Each entry is registered per user when it is written, and a clear removes
everything in that user's registry (`src/app/cache_utils.py`).

## Keys

The media-list and time-left keys are built from the view's effective
`MediaListFilters` through `MediaListFilters.cache_token()`, plus a few view
settings the filters do not hold (sort, anime library mode, title display for
the A to Z rail, entry grouping). A new filter field reaches every key on its
own. Each key's deliberate omissions are named in `cache_utils`
(`FILTER_MENU_IGNORED_FILTERS`, `TIME_LEFT_IGNORED_FILTERS`), and
`app.tests.views.test_media_list_cache_keys` pins which requests share an entry.

## Clearing

Call `cache_utils.clear_library_view_caches(user_id, change, media_types=...)`
and name what happened. The table in `cache_utils` decides which views that can
affect:

| Change | Clears | Used by |
|--------|--------|---------|
| `LIBRARY_EDIT` (default; pass `media_types`) | Media list and Home, plus Time left when a TV, season or episode changed | Model signals, TV model methods, saves, bulk tracking, check-in, migrations |
| `TAGS` | Media list, plus Home when the user has an enabled Home row filtered by tag | Tag views |
| `LIST_SETTING` | Media list | List display preferences |
| `HOME_SETTING` | Home | Enabled media types (web and API) |
| `EPISODE_DETAILS` | Time left and media list | Provider runtime and air-date refreshes |

`cache_utils.clear_show_view_caches(media_id, source, change)` applies a change
to every user tracking a show or one of its seasons.

Inside a transaction the function clears immediately and again on commit,
because a concurrent reader can re-cache the rows from before the write until the
write commits. Outside a transaction it clears once.

`EPISODE_DETAILS` leaves Home out on purpose. A provider refresh touches every
tracker of a show, and rebuilding Home costs a library scan. Home's order and
Up Next caches pick these changes up when they expire.

## When a write path must call it

Model signals call it for single-row saves and deletes of tracked media. Call it
yourself only after writes that skip signals: `QuerySet.update`, `bulk_create`,
`bulk_update`, and blocks inside `suppress_media_change_side_effects()` that do
not end in `flush_media_change_side_effects`.

## Adding a cache or a change

- A new per-user view cache gets a registry and a column in `_VIEWS_BY_CHANGE`.
  Callers do not change.
- A new kind of change is added only when an existing one would clear something
  it cannot affect, or miss something it can.

## Tests

`app.tests.test_view_cache_invalidation` pins what each change and each family
of write paths clears. It also fails if any module outside `cache_utils.py`
calls the per-view clear functions directly.

## Not covered here

History (`app.history_cache`), Statistics (`docs/architecture/statistics-sync.md`)
and Discover (`app.discover_tab_cache`) have their own invalidation contracts.
