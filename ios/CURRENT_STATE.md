# Spine iOS Current State

## Screens Done

- App shell: Search, Library, Diary, Profile tabs.
- Media detail: dark production detail page with bleeding poster/accent hero, lowered safe-area-aware poster placement, real provider/Spine rating chips, expandable synopsis, tracked-state summary, media-specific metadata, reviews, seasons/episodes, cast/crew/authors, live related sections, poster customization for TMDB movies/TV, and a page-only custom bottom nav overlay.
- Media log: the media detail LOG action now opens a full-screen native log page with poster-led layout, 5-star half-step ratings saved as 0-10 API values, review title/body, tags with live suggestions, liked/repeat/spoiler/visibility controls, TV season selection, mark-only actions, and progress-only mode for books/manga/comics/games/board games.
- Poster customization: the media detail ellipsis opens a small bottom menu, then a live poster picker with language filtering, current selection, save, and immediate poster/blur background update.

## Live API

- The app always uses live API repositories via `AppRepositories.current()`.
- Media detail uses `GET /api/v1/media/{source}/{media_type}/{media_id}/`.
- Poster customization uses `GET /api/v1/media/{source}/{media_type}/{media_id}/posters/` and `PUT /api/v1/media/{source}/{media_type}/{media_id}/poster/`.
- Live media detail now consumes normalized `cast`, `crew`, `seasons`, `episodes`, `related_sections`, `external_ratings`, `details`, custom posters, backdrop images, and `community.rating_distribution` from the API.
- Live review fetch still uses `GET /api/v1/media/{source}/{media_type}/{media_id}/reviews/` and renders available review cards.
- Media log uses `POST /api/v1/diary/`, `GET /api/v1/diary/tags/`, `PATCH /api/v1/tracking/{source}/{media_type}/{media_id}/`, `POST /api/v1/tracking/{source}/{media_type}/{media_id}/actions/consume/`, `POST /api/v1/tracking/{source}/tv/{media_id}/seasons/{season_number}/watch/`, `POST /api/v1/tracking/{source}/book/{media_id}/progress/`, and `POST /api/v1/tracking/{source}/book/{media_id}/complete/`.
- No production-facing fake rating distributions, Goodreads labels, reading progress, author counts, or recommendation placeholders are rendered.
- Movie detail rating chips hide TMDB for now and use bundled IMDb, Letterboxd, and Rotten Tomatoes logo assets when those sources are present.

## Known Gaps

- Poster customization is currently limited to TMDB movies and top-level TV shows; seasons/books/games are not wired in iOS yet.
- Log editing is new-entry only; editing or deleting existing diary entries remains in the Diary feature.
- Episode-level logging is not included in the iOS log page yet.
- External ratings, credits, related sections, episodes, seasons, and custom poster URL are typed on iOS; live usefulness depends on provider metadata availability.
- The bottom nav replica on media detail is visual/page-local and does not change global app tab routing.
- Comments and social notifications remain out of scope.

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
