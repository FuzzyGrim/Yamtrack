# Spine iOS Current State

## Screens Done

- App shell: Search, Library, Diary, Profile tabs.
- Search: Type Worlds media lens redesigned for Search only, replacing the horizontal segmented type picker with an icon-first lens trigger inside the search field, swipeable lens wheel rail, selected-type label, soft atmospheric bleed, reusable Core/Design components, and persisted lens store ready for later screens.
- Library: dark native library surface rebuilt around paged live tracking results. The screen renders header controls immediately, uses a fixed single-row media type picker, separates Planning items from the main Tracked view, defaults to a four-column poster grid reused from tag pages, supports compact grid/list icon switching, lazy-loads additional tracking pages while scrolling, and opens media detail from every item.
- Media detail: dark production detail page with bleeding poster/accent hero, lowered safe-area-aware poster placement, real provider/Spine rating chips, expandable synopsis, tracked-state summary, media-specific metadata, reviews, TV season detail pages with explicit show + season titles and clear episode lists, seasons/episodes, cast/crew/authors, live related sections, poster customization for TMDB movies/TV, and a page-only custom bottom nav overlay.
- Media log: the media detail LOG action now opens a full-screen native log page with poster-led layout, date-only logging, large draggable 5-star half-step ratings saved as 0-10 API values, review body, tags with live suggestions, liked/repeat icon controls, spoiler control, TV season selection, mark-only actions, and progress-only mode for books/manga/comics/games/board games.
- Diary: dark native diary surface using live entries, poster-forward cards, consumed-date fallback formatting, media type chips, ratings, compact tag chips, spoiler/rewatch chips, tappable rows, and a read-only dedicated log detail page with poster/backdrop-led media context, full review display, spoiler reveal, log metadata, navigation to media detail, and tappable tag chips.
- Tags: diary-log tags open a native tag detail page with a top Diary/Grid segmented control. Diary reuses the shared diary list filtered by tag; Grid shows unique media posters from tagged logs and opens media detail on poster tap.
- Poster customization: the media detail ellipsis opens a small bottom menu, then a live poster picker with language filtering, current selection, save, and immediate poster/blur background update.
- Profile: display-first hub rebuilt on the dark native surface with a centered identity hero, compact stat chips, editable Hall of Fame favorite slots, recent diary activity preview, media-detail navigation from favorites/activity, Diary tab jump, and a read-only settings sheet with logout.

## Live API

- The app always uses live API repositories via `AppRepositories.current()`.
- Library uses paged `GET /api/v1/tracking/?media_type=<type>` results and follows the API `next` page cursor with `page=<cursor>`; no app-shipped cache or fake library data is used.
- Media detail uses `GET /api/v1/media/{source}/{media_type}/{media_id}/`.
- TV season detail uses `GET /api/v1/media/{source}/tv/{media_id}/seasons/{season_number}/` from season cards on a TV show detail page.
- Poster customization uses `GET /api/v1/media/{source}/{media_type}/{media_id}/posters/` and `PUT /api/v1/media/{source}/{media_type}/{media_id}/poster/`.
- Live media detail now consumes normalized `cast`, `crew`, `seasons`, `episodes`, `related_sections`, `external_ratings`, `details`, custom posters, backdrop images, and `community.rating_distribution` from the API. Season pages render top-level episode number, title, full date/runtime/rating, overview, and episode image when available.
- Live review fetch still uses `GET /api/v1/media/{source}/{media_type}/{media_id}/reviews/`; season pages use the TV media path with `season_number` query params and render available review cards.
- Media log uses `POST /api/v1/diary/`, `GET /api/v1/diary/tags/`, `PATCH /api/v1/tracking/{source}/{media_type}/{media_id}/`, `POST /api/v1/tracking/{source}/{media_type}/{media_id}/actions/consume/`, `POST /api/v1/tracking/{source}/tv/{media_id}/seasons/{season_number}/watch/`, `POST /api/v1/tracking/{source}/book/{media_id}/progress/`, and `POST /api/v1/tracking/{source}/book/{media_id}/complete/`.
- Diary uses `GET /api/v1/diary/` for the list and `GET /api/v1/diary/{id}/` for row detail; entry creation remains routed through media detail logging.
- Tags use `GET /api/v1/diary/?tag=<tag>` and the existing paged diary response shape. The iOS repository follows diary pages so tag diary/grid views are complete for the API response.
- Profile uses `GET /api/v1/me/` for identity, counts, preferences, and Hall of Fame slots, `PUT/DELETE /api/v1/me/hof/{media_type}/` for favorite edits, plus `GET /api/v1/diary/` for recent activity.
- No production-facing fake rating distributions, Goodreads labels, reading progress, author counts, or recommendation placeholders are rendered.
- Movie detail rating chips hide TMDB for now and use bundled IMDb, Letterboxd, and Rotten Tomatoes logo assets when those sources are present.

## Known Gaps

- Poster customization is currently limited to TMDB movies and top-level TV shows; seasons/books/games are not wired in iOS yet.
- Log editing is new-entry only; editing or deleting existing diary entries remains unwired.
- Diary entry detail is wired from Diary rows; public permalink routing and social-like actions are not included yet.
- Tag pages require backend support for exact `tag` filtering on `GET /api/v1/diary/`; the app does not client-filter incomplete diary pages.
- Episode-level logging is not included in the iOS log page yet.
- External ratings, credits, related sections, episodes, seasons, and custom poster URL are typed on iOS; live usefulness depends on provider metadata availability.
- The bottom nav replica on media detail is visual/page-local and does not change global app tab routing.
- Profile is still read-only for avatar, profile fields, and preferences; Hall of Fame favorites are editable from the Profile tab.
- Profile compact stats use `/me/` totals only; per-media log breakdown is deferred until `/api/v1/stats/me/summary/` has a documented response shape.
- Comments and social notifications remain out of scope.
- If the first `/api/v1/tracking/` page is slow from the backend, Library now keeps chrome/skeletons visible but still depends on backend/API latency for the first real items.

## How To Run

- Open `ios/Spine/Spine.xcodeproj`.
- Scheme: `Spine`.
- Default Simulator: iPhone 17 (iPhone 16 is not installed on this Mac).
- Optional live API override: set `SPINE_API_BASE_URL`.
- XcodeBuildMCP defaults: `.xcodebuildmcp/config.yaml` (project, scheme, simulator, bundle ID).
- Agents with XcodeBuildMCP: use `simulator/build-and-run` after meaningful UI changes; `session_show_defaults` to verify config loaded.
- Build command (fallback):

```bash
DEVELOPER_DIR=/Applications/Xcode-26.5.0.app/Contents/Developer \
  xcodebuild -project ios/Spine/Spine.xcodeproj -scheme Spine \
  -destination 'platform=iOS Simulator,name=iPhone 17' build
```
