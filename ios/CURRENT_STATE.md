# Spine iOS Current State

## Screens Done

- App shell: Search, Library, Diary, Profile tabs.
- Media detail: dark production detail page with bleeding poster/accent hero, lowered safe-area-aware poster placement, real provider/Spine rating chips, expandable synopsis, tracked-state summary, media-specific metadata, reviews, seasons/episodes, cast/crew/authors, live related sections, and a page-only custom bottom nav overlay.

## Mock vs Live

- Default app environment is `.live` in `AppEnvironment.swift`.
- Media detail mock data remains available in `Core/Networking/Mock/MockRepositories.swift` for previews/tests.
- Live media detail still uses `GET /api/v1/media/{source}/{media_type}/{media_id}/`.
- Live media detail now consumes normalized `related_sections` and `community.rating_distribution` from the API.
- Live review fetch still uses `GET /api/v1/media/{source}/{media_type}/{media_id}/reviews/` and renders available review cards.
- No production-facing fake rating distributions, Goodreads labels, reading progress, author counts, or recommendation placeholders are rendered.
- Movie detail rating chips hide TMDB for now and use bundled IMDb, Letterboxd, and Rotten Tomatoes logo assets when those sources are present.

## Known Gaps

- Poster/cover customization is display-only; native picker UI and endpoint wiring are deferred.
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
