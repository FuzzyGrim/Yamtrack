# API token scopes

Scopes constrain what an `IntegrationToken` may reach. This is the contract a
third-party client (Nuvio, Stremio, Kodi, a scrobbler) is written against.

## Where the rules live

| Concern | Location |
|---|---|
| Scope vocabulary and descriptions | `src/api/scopes.py` (`SCOPE_DESCRIPTIONS`) |
| Which scope each endpoint needs | `src/api/scopes.py` (`VIEW_SCOPES`) |
| Enforcement | `api.authentication.HasScope`, installed globally in `REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"]` |
| Published per-operation scope | `x-required-scope` in `src/api/contracts/openapi.yaml` |
| Coverage guarantee | `api.tests.test_fork_scope_enforcement.ScopeMapCoverageTests` |

The coverage test has already paid for itself: the watched-state sync endpoints
landed unmapped, and unmapped means denied, so the change feed was unreachable
by exactly the clients it exists for until they were added here.

## Credential kinds

| Credential | `request.auth` | Access |
|---|---|---|
| Session login | `None` | Everything the user can reach |
| Legacy `User.token` | `None` | Everything except session-only credential actions |
| `IntegrationToken` | the token | Only what its scopes name |
| Floppy app device token with full access | the token | Everything except session-only credential actions |

Legacy account tokens predate scopes and retain access to ordinary integration
operations. Credential disclosure and issuance require a session, so no bearer
credential can mint access that survives its own revocation.

`SessionAuthentication` is in `REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"]`,
so the table above holds for every endpoint rather than only the ones that opt
in with a mixin. DRF enforces CSRF on unsafe methods for sessions; the native
client sends `X-CSRFToken` and refreshes it from `/accounts/login/` when the
server rotates it. Token clients are unaffected: they authenticate before the
session class is reached.

### The Floppy app's full-access token

People who sign in with a social or OIDC account cannot give the app a
password, so the app signs them in through the OAuth device flow. It uses the
server-defined client id `flp_oauth_floppy_app`
(`integrations.oauth_scope_info.FIRST_PARTY_CLIENT_ID`). Registration issues
`flp_oauth_` plus 32 random characters, so no registered application can hold
that id. For that client only, the device-authorization step adds the marker
scope `floppy:account` to the grant, and the approval page says plainly that
approving signs the Floppy app in with full access. No client can request the
marker, and the settings token form cannot store it, because it is in neither
`OAUTH_ALLOWED_SCOPES` nor `ALL_SCOPES`. A refresh keeps the marker only when
the original grant held it and the client is the first-party app. It never adds
the marker.

`has_full_account_access(token)` requires that client id, the marker and the
`FLOPPY_APP_FULL_ACCESS` setting. `HasScope` and `CanWriteBoundList` then treat
the token like a session for ordinary access, so `NEVER` endpoints pass. The
second tier, `SESSION_ONLY`, refuses every bearer credential: reading or
regenerating the legacy account token (including calendar/RSS subscription URLs)
and creating app tokens or catalog grants require a session (password sign-in
in the native app). OAuth device approval and admin account creation, role/active
changes and password reset use the same tier. Account password, authenticator
and recovery-code actions are session-only within the account endpoint;
bearer reads neither generate nor expose pending authenticator secrets. Profile
username changes and account deletion retain their existing full-access rules.
Provider details retain configuration but withhold token-bearing webhook/addon
guides and copy URLs from every bearer credential, including existing catalog
install secrets; `guidesSessionRequired` describes that state. Listing and revoking
remain available with first-party full access. OAuth client registration is a
public protocol endpoint; it registers an unauthorised client, grants no account
access and still needs interactive approval. Everything that does not
come from the scope map still applies: the user's own data only, superuser
checks, the demo-account guards, throttles, token expiry and revocation. CSRF
does not apply because a bearer token is not a cookie. Revoke the token like any
other application, under Settings > Access & Tokens, where it is listed as
"Floppy app (full access)". Set `FLOPPY_APP_FULL_ACCESS=False` to withdraw full
access from every live token at once. New sign-ins then get a scoped token.

Every other token, including one from a registered application that asks for
the same scopes, keeps the restrictions described below.

## Enforcement rules

All token authentication requires an active account. Disabling an account
also disables its catalog grants and webhook credentials; queued webhooks
recheck account activity before processing.

1. An endpoint is denied unless `VIEW_SCOPES` names it for that HTTP method.
   Unmapped means denied, not allowed — a new route cannot silently become
   reachable by every scoped token.
2. `ANY_SCOPE` means any valid token passes. It is for endpoints that carry no
   user data of their own, or that a client needs before it knows its grants
   (health, info, task status, ListenBrainz token validation).
3. `NEVER` refuses ordinary scoped tokens, including `"*"`. Sessions, legacy
   account tokens and the Floppy app's full-access token pass.
4. `SESSION_ONLY` requires `SessionAuthentication`, before any token bypass.
   Account-token reads/rotation, calendar and RSS subscription URLs, app-token
   and catalog-grant issuance, OAuth device approval and admin account credential
   mutations use this tier. Even first-party full access and legacy tokens fail.
   Mixed account/provider endpoints additionally withhold credential actions and
   disclosure while preserving ordinary profile and configuration access.
5. `"*"` grants every mapped ordinary scope. It defeats neither `NEVER` nor
   `SESSION_ONLY`.

`GET /api/v1/media/{media_type}/{source}/{media_id}/detail/` is public metadata
for anonymous readers. Authenticated requests also receive only that viewer's
tracking row and require `watchlist:read`; `?public_view=1` suppresses personal
state and action capabilities even when the request is authenticated.

`ScopeMapCoverageTests` fails the build when a routed view or method is missing
from the map, when a map entry no longer matches a route, or when a mapped scope
is not in the vocabulary. Adding an endpoint therefore requires deciding its
scope; forgetting is not one of the outcomes.

The Expo library uses `user:read`/`user:write` for saved-view CRUD, `lists:write`
for bulk list membership, and `watchlist:read`/`watchlist:write` for bulk
collection and its user-owned Add Plays preview/status and write operations.
Bulk operations validate their inputs, return `400` for invalid input, `404` when
a saved view or job is not owned by the caller, and `409` when another Add Plays
write is running.

## What the map does not cover

Scopes apply to the DRF API (`/api/v1/`, `/apis/listenbrainz/1/`). They do not
apply to the non-DRF integration surfaces, which authenticate differently:

| Surface | Credential | Status |
|---|---|---|
| Stremio addon (`/stremio-addon/<token>/...`) | legacy `User.token` in the URL path | Replaced by catalog grants in Nuvio programme B1 |
| Webhooks (Plex, Jellyfin, Emby, Kodi, ...) | per-integration shared secret | Out of scope; each owns its own check |
| gPodder API | account credentials | Out of scope |

Do not describe Floppy as fully scope-gated until those are addressed.

## Vocabulary

| Scope | Grants |
|---|---|
| `scrobble:write` | Submit playback events |
| `progress:read` | Read resume positions and now-playing |
| `progress:write` | Create, update, and clear resume positions |
| `watchlist:read` | Read saved items, watched state, consumption history |
| `watchlist:write` | Change saved items, watched state, tags, history |
| `catalog:read` | Discover, home, search, calendar |
| `catalog:write` | Refresh Discover and the calendar; hide Discover items |
| `metadata:read` | Provider preferences and item metadata |
| `metadata:write` | Change item metadata, artwork, provider preferences; provider sync |
| `lists:read` | Lists, list items, collaborators, activity |
| `lists:write` | Change lists, membership, ordering, recommendations |
| `music:read` / `music:write` | Artists, albums, tracks, plays |
| `podcasts:read` / `podcasts:write` | Shows, episodes, play state |
| `statistics:read` / `statistics:write` | Read statistics; trigger a recompute |
| `sync:read` / `sync:write` | Read sync connections, the change feed, and conflicts; resolve a conflict |
| `imports:read` / `imports:write` | Import activity; start imports |
| `exports:read` | Download exports |
| `user:read` / `user:write` | Preferences, saved library views, sidebar, notification settings |

## Creating a token

Settings → Integrations → **App tokens** → *Create an app token*.

The form takes a name, an optional expiry, and a permission set pre-ticked with
the tracking preset. The secret is shown once on the redirect and never again —
Floppy stores only the SHA-256 digest, so there is nothing to show later. The
list afterwards identifies each token by its `flp_` prefix, its permissions,
when it was created, and when it was last used.

Revoking one token leaves the others working. That is the point of named
tokens: the account token at the top of the same page is all-or-nothing, and
rotating it breaks every webhook and integration at once.

An expired token stays in the list, labelled `Expired`, rather than
disappearing — otherwise an app stops working with no visible reason.

## The tracking preset

A token minted without an explicit scope list gets
`scopes.TRACKING_PRESET`, which is what a tracking client needs and no more:

```text
scrobble:write
progress:read
progress:write
watchlist:read
watchlist:write
catalog:read
sync:read
```

That preset can scrobble, sync resume positions, read and change saved items and
watched state, and browse Discover. It cannot touch lists, music, podcasts,
imports, exports, user settings, or metadata. `TRACKING_PRESET` and
`integrations.models.DEFAULT_INTEGRATION_SCOPES` are asserted equal by test, so
the documented preset and the minted default cannot drift apart.

## List write bindings

`lists:write` lets a token change lists. `IntegrationToken.writable_list_ids`
narrows that further:

- empty means every list the user owns, which is what `lists:write` meant
  before bindings existed
- a populated list is an exact allowlist of `CustomList` ids

Smart lists are read-only to every external token, bound or not: a computed
list's contents come from its rules, so an external write would be silently
recomputed away.

Enforced centrally by `api.authentication.CanWriteBoundList`, installed in
`DEFAULT_PERMISSION_CLASSES` alongside `HasScope`. Thirteen endpoints write
lists across two modules; a per-view opt-in is a control that gets forgotten.

## Last use

`IntegrationToken.last_used_at` is written at most once per
`api.authentication.LAST_USED_WRITE_INTERVAL` (5 minutes), via a filtered
`UPDATE` rather than `save()`. A scrobbling client would otherwise write a row
on every request, and concurrent requests would race on the same row.

Treat it as "seen recently", not as a request log.

## Endpoint map (fork API v1)

Scope each endpoint needs, as enforced by `VIEW_SCOPES`. `NEVER` endpoints carry
account secrets or administer accounts. They accept sessions, legacy account tokens
and the built-in Floppy app's full-access device token; other scoped tokens are refused.
`SESSION_ONLY` credential disclosure and issuance accepts sessions only, refusing
legacy and first-party tokens too.
Every routed fork view is listed, generated from the URL resolver and
`VIEW_SCOPES`; `VIEW_SCOPES` stays the source of truth. The account endpoint
additionally requires a session for password/authenticator/recovery actions and
omits pending credentials for bearer reads. Provider detail responses omit
protected guide/copy URLs for bearer credentials without denying configuration.

| Path | Methods | Scope |
|---|---|---|
| `/api/v1/admin/users/` | GET POST | GET NEVER; POST SESSION_ONLY |
| `/api/v1/admin/users/{user_id}/` | DELETE PATCH | DELETE NEVER; PATCH SESSION_ONLY |
| `/api/v1/admin/users/{user_id}/password/` | POST | SESSION_ONLY |
| `/api/v1/calendar/links/` | GET | SESSION_ONLY |
| `/api/v1/calendar/statuses/` | GET | `catalog:read` |
| `/api/v1/collection/` | GET POST | GET `watchlist:read`; POST `watchlist:write` |
| `/api/v1/collection/fields/` | GET PUT | GET `watchlist:read`; PUT `user:write` |
| `/api/v1/collection/seasons/{season_item_id}/` | DELETE | `watchlist:write` |
| `/api/v1/collection/status/{item_id}/` | GET | `watchlist:read` |
| `/api/v1/collection/{entry_id}/` | DELETE GET PATCH | DELETE `watchlist:write`; GET `watchlist:read`; PATCH `watchlist:write` |
| `/api/v1/connections/` | GET | NEVER |
| `/api/v1/connections/device/` | GET POST | GET NEVER; POST SESSION_ONLY |
| `/api/v1/connections/{binding_id}/direction/` | POST | NEVER |
| `/api/v1/connections/{binding_id}/pause/` | POST | NEVER |
| `/api/v1/consumptions/batch/` | POST | `watchlist:write` |
| `/api/v1/consumptions/batch/{operation_id}/` | GET | `watchlist:read` |
| `/api/v1/discover/` | GET | `catalog:read` |
| `/api/v1/discover/hidden/` | GET POST | GET `catalog:read`; POST `catalog:write` |
| `/api/v1/discover/refresh/` | POST | `catalog:write` |
| `/api/v1/discover/status/` | GET | `catalog:read` |
| `/api/v1/export/csv/` | GET | `exports:read` |
| `/api/v1/export/letterboxd/` | GET | `exports:read` |
| `/api/v1/export/template/` | GET | `exports:read` |
| `/api/v1/exports/backup-status/` | GET | NEVER |
| `/api/v1/exports/history/` | GET | `exports:read` |
| `/api/v1/exports/schedules/` | DELETE GET POST | DELETE NEVER; GET `exports:read`; POST NEVER |
| `/api/v1/history/` | GET | `watchlist:read` |
| `/api/v1/history/days/{day_key}/` | GET | `watchlist:read` |
| `/api/v1/history/entries/{media_type}/{instance_id}/` | DELETE PATCH | `watchlist:write` |
| `/api/v1/history/genres/` | GET | `watchlist:read` |
| `/api/v1/history/release/` | GET | `watchlist:read` |
| `/api/v1/history/saved-views/` | POST | `user:write` |
| `/api/v1/history/sessions/` | GET | `watchlist:read` |
| `/api/v1/history/{media_type}/{history_id}/` | DELETE | `watchlist:write` |
| `/api/v1/home/` | GET | `catalog:read` |
| `/api/v1/home/artwork/` | GET | `catalog:read` |
| `/api/v1/home/row/{row_id}/` | GET | `catalog:read` |
| `/api/v1/home/row/{row_id}/sort/` | POST | `catalog:write` |
| `/api/v1/imports/activity/` | GET POST | GET `imports:read`; POST NEVER |
| `/api/v1/imports/{service}/` | POST | `imports:write` |
| `/api/v1/library/activity/` | GET | `watchlist:read` |
| `/api/v1/library/bulk/collection/` | POST | `watchlist:write` |
| `/api/v1/library/bulk/plays/` | POST | `watchlist:write` |
| `/api/v1/library/bulk/plays/{operation_id}/` | GET | `watchlist:read` |
| `/api/v1/library/bulk/status/` | POST | `watchlist:write` |
| `/api/v1/library/bulk/tags/` | POST | `watchlist:write` |
| `/api/v1/library/deleted/` | GET | `watchlist:read` |
| `/api/v1/library/game-groups/` | GET | `watchlist:read` |
| `/api/v1/library/preferences/{media_type}/` | GET PATCH | GET `user:read`; PATCH `user:write` |
| `/api/v1/library/saved-views/` | GET POST | GET `user:read`; POST `user:write` |
| `/api/v1/library/saved-views/reorder/` | POST | `user:write` |
| `/api/v1/library/saved-views/{view_id}/` | DELETE GET | DELETE `user:write`; GET `user:read` |
| `/api/v1/library/share-view/` | POST | `lists:write` |
| `/api/v1/lists/import/` | GET | `imports:read` |
| `/api/v1/lists/import/csv/` | POST | `imports:write` |
| `/api/v1/lists/import/mdblist/` | POST | NEVER |
| `/api/v1/lists/import/trakt/` | POST | NEVER |
| `/api/v1/lists/{list_id}/activity/` | GET | `lists:read` |
| `/api/v1/lists/{list_id}/collaborators/` | GET PUT | GET `lists:read`; PUT `lists:write` |
| `/api/v1/lists/{list_id}/export/` | GET | `lists:read` |
| `/api/v1/lists/{list_id}/items/bulk/` | POST | `lists:write` |
| `/api/v1/lists/{list_id}/items/order/` | PUT | `lists:write` |
| `/api/v1/lists/{list_id}/items/ranks/` | PUT | `lists:write` |
| `/api/v1/lists/{list_id}/items/reorder/` | POST | `lists:write` |
| `/api/v1/lists/{list_id}/recommendations/` | GET POST | GET `lists:read`; POST `lists:write` |
| `/api/v1/lists/{list_id}/recommendations/{recommendation_id}/{decision}/` | POST | `lists:write` |
| `/api/v1/lists/{list_id}/smart-rules/` | GET PUT | GET `lists:read`; PUT `lists:write` |
| `/api/v1/lists/{list_id}/smart-rules/options/` | GET | `lists:read` |
| `/api/v1/lists/{list_id}/smart-sync/` | POST | `lists:write` |
| `/api/v1/lists/{list_id}/tiers/` | GET PUT | GET `lists:read`; PUT `lists:write` |
| `/api/v1/lists/{list_id}/tiers/fill/` | POST | `lists:write` |
| `/api/v1/lists/{list_id}/tiers/fill/undo/` | POST | `lists:write` |
| `/api/v1/lists/{list_id}/tiers/move/` | POST | `lists:write` |
| `/api/v1/lists/{list_id}/viewers/` | GET PUT | GET `lists:read`; PUT `lists:write` |
| `/api/v1/media/{media_type}/filter-options/` | GET | `watchlist:read` |
| `/api/v1/media/{media_type}/item-ids/` | GET | `watchlist:read` |
| `/api/v1/media/{media_type}/{source}/{media_id}/activity-timeline/` | GET | `watchlist:read` |
| `/api/v1/media/{media_type}/{source}/{media_id}/checkin/` | GET POST | GET `watchlist:read`; POST `watchlist:write` |
| `/api/v1/media/{media_type}/{source}/{media_id}/collect/` | PUT | `watchlist:write` |
| `/api/v1/media/{media_type}/{source}/{media_id}/collection/` | DELETE PUT | `watchlist:write` |
| `/api/v1/media/{media_type}/{source}/{media_id}/collection/entries/` | GET | `watchlist:read` |
| `/api/v1/media/{media_type}/{source}/{media_id}/detail/` | GET | `watchlist:read` |
| `/api/v1/media/{media_type}/{source}/{media_id}/detail/gallery/` | GET | `metadata:read` |
| `/api/v1/media/{media_type}/{source}/{media_id}/episode-preview/` | GET | `watchlist:read` |
| `/api/v1/media/{media_type}/{source}/{media_id}/episodes/bulk/` | POST | `watchlist:write` |
| `/api/v1/media/{media_type}/{source}/{media_id}/issues/` | GET | `watchlist:read` |
| `/api/v1/media/{media_type}/{source}/{media_id}/metadata/` | GET | `metadata:read` |
| `/api/v1/media/{media_type}/{source}/{media_id}/progress-card/` | GET | `watchlist:read` |
| `/api/v1/media/{media_type}/{source}/{media_id}/progress/` | POST | `progress:write` |
| `/api/v1/media/{media_type}/{source}/{media_id}/provider-preference/` | GET PUT | GET `metadata:read`; PUT `metadata:write` |
| `/api/v1/media/{media_type}/{source}/{media_id}/request/` | GET POST | GET `catalog:read`; POST `catalog:write` |
| `/api/v1/media/{media_type}/{source}/{media_id}/scores/` | GET | `watchlist:read` |
| `/api/v1/media/{media_type}/{source}/{media_id}/state/` | GET | `watchlist:read` |
| `/api/v1/media/{media_type}/{source}/{media_id}/tags/` | GET PUT | GET `watchlist:read`; PUT `watchlist:write` |
| `/api/v1/media/{media_type}/{source}/{media_id}/watch/` | DELETE POST | `watchlist:write` |
| `/api/v1/media/{media_type}/{source}/{media_id}/watched-state/` | GET PUT | GET `watchlist:read`; PUT `watchlist:write` |
| `/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/activity-timeline/` | GET | `watchlist:read` |
| `/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/collect/` | PUT | `watchlist:write` |
| `/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/collection/entries/` | GET | `watchlist:read` |
| `/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/episodes/{episode_number}/collection/` | DELETE PUT | `watchlist:write` |
| `/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/episodes/{episode_number}/drop/` | POST | `watchlist:write` |
| `/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/episodes/{episode_number}/score/` | PATCH | `watchlist:write` |
| `/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/episodes/{episode_number}/watch/` | DELETE POST | `watchlist:write` |
| `/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/metadata/` | GET | `metadata:read` |
| `/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/progress-card/` | GET | `watchlist:read` |
| `/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/progress/` | POST | `progress:write` |
| `/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/scores/` | GET | `watchlist:read` |
| `/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/state/` | GET | `watchlist:read` |
| `/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/{episode_number}/activity-timeline/` | GET | `watchlist:read` |
| `/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/{episode_number}/collection/entries/` | GET | `watchlist:read` |
| `/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/{episode_number}/metadata/` | GET | `metadata:read` |
| `/api/v1/media/{media_type}/{source}/{media_id}/{season_number}/{episode_number}/state/` | GET | `watchlist:read` |
| `/api/v1/metadata-review/` | GET | NEVER |
| `/api/v1/metadata-review/items/{item_id}/apply/` | POST | NEVER |
| `/api/v1/metadata-review/items/{item_id}/candidates/` | GET | NEVER |
| `/api/v1/metadata-review/items/{item_id}/preview/` | POST | NEVER |
| `/api/v1/metadata-review/references/{reference_id}/` | POST | NEVER |
| `/api/v1/metadata/items/{item_id}/` | GET PATCH | GET `metadata:read`; PATCH `metadata:write` |
| `/api/v1/metadata/items/{item_id}/cover/` | GET POST | GET `metadata:read`; POST `metadata:write` |
| `/api/v1/metadata/items/{item_id}/hardcover-editions/` | GET POST | GET `metadata:read`; POST `metadata:write` |
| `/api/v1/metadata/items/{item_id}/image/` | PATCH | `metadata:write` |
| `/api/v1/music/albums/` | GET POST | GET `music:read`; POST `music:write` |
| `/api/v1/music/albums/{album_id}/` | DELETE GET PATCH | DELETE `music:write`; GET `music:read`; PATCH `music:write` |
| `/api/v1/music/albums/{album_id}/plays/` | DELETE | `music:write` |
| `/api/v1/music/albums/{album_id}/releases/` | GET POST | GET `music:read`; POST `music:write` |
| `/api/v1/music/albums/{album_id}/sync-metadata/` | POST | `music:write` |
| `/api/v1/music/albums/{album_id}/tracks/` | GET | `music:read` |
| `/api/v1/music/artists/` | GET POST | GET `music:read`; POST `music:write` |
| `/api/v1/music/artists/{artist_id}/` | DELETE GET PATCH | DELETE `music:write`; GET `music:read`; PATCH `music:write` |
| `/api/v1/music/artists/{artist_id}/plays/` | DELETE | `music:write` |
| `/api/v1/music/artists/{artist_id}/relations/` | GET | `music:read` |
| `/api/v1/music/artists/{artist_id}/sync-discography/` | POST | `music:write` |
| `/api/v1/music/artists/{artist_id}/sync-members/` | POST | `music:write` |
| `/api/v1/music/bulk-plays/` | POST | `music:write` |
| `/api/v1/music/songs/plays/` | POST | `music:write` |
| `/api/v1/music/tracks/{music_id}/score/` | PATCH | `music:write` |
| `/api/v1/music/tracks/{track_id}/` | GET | `music:read` |
| `/api/v1/music/tracks/{track_id}/notes/` | PATCH | `music:write` |
| `/api/v1/music/tracks/{track_id}/plays/` | GET | `music:read` |
| `/api/v1/music/tracks/{track_id}/plays/{history_id}/` | DELETE | `music:write` |
| `/api/v1/people/{source}/{person_id}/` | GET | `catalog:read` |
| `/api/v1/playback/home-cards/` | GET POST | NEVER |
| `/api/v1/playback/now-playing/` | GET | `progress:read` |
| `/api/v1/playback/progress/` | DELETE GET PUT | DELETE `progress:write`; GET `progress:read`; PUT `progress:write` |
| `/api/v1/podcasts/episodes/plays/` | POST | `podcasts:write` |
| `/api/v1/podcasts/episodes/plays/{consumption_id}/` | DELETE GET PATCH | DELETE `podcasts:write`; GET `podcasts:read`; PATCH `podcasts:write` |
| `/api/v1/podcasts/episodes/{episode_id}/` | DELETE PATCH | `podcasts:write` |
| `/api/v1/podcasts/episodes/{episode_id}/notes/` | PATCH | `podcasts:write` |
| `/api/v1/podcasts/episodes/{episode_id}/score/` | PATCH | `podcasts:write` |
| `/api/v1/podcasts/lookup/{itunes_id}/` | GET | `podcasts:read` |
| `/api/v1/podcasts/shows/` | GET POST | GET `podcasts:read`; POST `podcasts:write` |
| `/api/v1/podcasts/shows/{show_id}/` | DELETE GET PATCH | DELETE `podcasts:write`; GET `podcasts:read`; PATCH `podcasts:write` |
| `/api/v1/podcasts/shows/{show_id}/episodes/` | GET | `podcasts:read` |
| `/api/v1/podcasts/shows/{show_id}/episodes/manage/` | GET POST | GET `podcasts:read`; POST `podcasts:write` |
| `/api/v1/podcasts/shows/{show_id}/mark-all-played/` | POST | `podcasts:write` |
| `/api/v1/providers/` | GET | NEVER |
| `/api/v1/providers/pin/` | POST | NEVER |
| `/api/v1/providers/{slug}/` | GET | NEVER |
| `/api/v1/providers/{slug}/connect/` | POST | NEVER |
| `/api/v1/providers/{slug}/connect/cancel/` | POST | NEVER |
| `/api/v1/providers/{slug}/connect/poll/` | GET | NEVER |
| `/api/v1/providers/{slug}/connect/start/` | POST | NEVER |
| `/api/v1/providers/{slug}/credentials/` | DELETE PUT | NEVER |
| `/api/v1/providers/{slug}/disconnect/` | POST | NEVER |
| `/api/v1/providers/{slug}/file-import/` | POST | NEVER |
| `/api/v1/providers/{slug}/forms/{form_key}/` | POST | NEVER |
| `/api/v1/providers/{slug}/import/` | POST | NEVER |
| `/api/v1/providers/{slug}/metadata/` | POST | NEVER |
| `/api/v1/providers/{slug}/schedules/` | DELETE GET | NEVER |
| `/api/v1/providers/{slug}/sync/` | POST | NEVER |
| `/api/v1/providers/{slug}/test/` | POST | NEVER |
| `/api/v1/recommendations/` | GET | `catalog:read` |
| `/api/v1/scrobble/` | POST | `scrobble:write` |
| `/api/v1/search/manual-entry/` | GET POST | GET `catalog:read`; POST NEVER |
| `/api/v1/search/music/create/` | POST | `music:write` |
| `/api/v1/search/parents/` | GET | `catalog:read` |
| `/api/v1/search/results/` | GET | `catalog:read` |
| `/api/v1/search/sources/` | GET | `catalog:read` |
| `/api/v1/search/suggestions/` | GET | `catalog:read` |
| `/api/v1/search/type/` | PUT | `catalog:write` |
| `/api/v1/statistics/featured-person/` | POST | `statistics:write` |
| `/api/v1/statistics/overview/` | GET | `statistics:read` |
| `/api/v1/statistics/preferences/` | GET PATCH | GET `statistics:read`; PATCH `statistics:write` |
| `/api/v1/statistics/refresh/` | POST | `statistics:write` |
| `/api/v1/statistics/talent/` | GET | `statistics:read` |
| `/api/v1/studios/{source}/{studio_id}/` | GET | `catalog:read` |
| `/api/v1/sync/bootstrap/` | POST | `sync:read` |
| `/api/v1/sync/changes/` | GET | `sync:read` |
| `/api/v1/sync/conflicts/` | GET | `sync:read` |
| `/api/v1/sync/conflicts/{conflict_id}/resolve/` | POST | `sync:write` |
| `/api/v1/sync/connections/` | GET | `sync:read` |
| `/api/v1/sync/progress-changes/` | GET | `sync:read` |
| `/api/v1/sync/snapshot/` | GET | `sync:read` |
| `/api/v1/tags/` | GET POST | GET `watchlist:read`; POST `watchlist:write` |
| `/api/v1/tags/{tag_id}/` | DELETE PATCH | `watchlist:write` |
| `/api/v1/tasks/{task_id}/` | GET | ANY_SCOPE |
| `/api/v1/user/about/` | GET | `user:read` |
| `/api/v1/user/about/whats-new/` | GET | `user:read` |
| `/api/v1/user/account/` | GET POST | NEVER |
| `/api/v1/user/advanced/` | GET | NEVER |
| `/api/v1/user/advanced/caches/{kind}/` | DELETE | NEVER |
| `/api/v1/user/advanced/diagnostics/` | GET | NEVER |
| `/api/v1/user/advanced/image-cache/` | DELETE PUT | NEVER |
| `/api/v1/user/advanced/logs/` | GET | NEVER |
| `/api/v1/user/advanced/server-port/` | DELETE PUT | NEVER |
| `/api/v1/user/appearance/` | GET PUT | GET `user:read`; PUT `user:write` |
| `/api/v1/user/cards/` | GET PUT | GET `user:read`; PUT `user:write` |
| `/api/v1/user/cards/preview/` | POST | `user:read` |
| `/api/v1/user/catalog-grants/` | GET POST | GET NEVER; POST SESSION_ONLY |
| `/api/v1/user/catalog-grants/{grant_id}/` | DELETE | NEVER |
| `/api/v1/user/home-screen/` | GET PUT | GET `user:read`; PUT `user:write` |
| `/api/v1/user/home-screen/filter-fields/` | GET | `user:read` |
| `/api/v1/user/home-screen/lists/` | GET | `user:read` |
| `/api/v1/user/import-settings/` | GET PUT | GET `user:read`; PUT `user:write` |
| `/api/v1/user/library-maintenance/` | GET POST | GET `user:read`; POST NEVER |
| `/api/v1/user/navigation/` | GET | `user:read` |
| `/api/v1/user/notifications/` | GET PATCH | GET `user:read`; PATCH `user:write` |
| `/api/v1/user/notifications/exclusions/` | DELETE GET POST | DELETE `user:write`; GET `user:read`; POST `user:write` |
| `/api/v1/user/notifications/test/` | POST | `user:write` |
| `/api/v1/user/oauth-applications/` | GET | NEVER |
| `/api/v1/user/oauth-applications/{client_id}/` | DELETE | NEVER |
| `/api/v1/user/onboarding/` | GET POST | GET `user:read`; POST `user:write` |
| `/api/v1/user/preferences/` | GET PATCH | GET `user:read`; PATCH `user:write` |
| `/api/v1/user/preferences/choices/` | GET | `user:read` |
| `/api/v1/user/restore/` | POST | NEVER |
| `/api/v1/user/rss/` | GET | SESSION_ONLY |
| `/api/v1/user/rss/preview/` | GET | NEVER |
| `/api/v1/user/sidebar/` | GET PUT | GET `user:read`; PUT `user:write` |
| `/api/v1/user/token/` | GET | SESSION_ONLY |
| `/api/v1/user/token/regenerate/` | POST | SESSION_ONLY |
| `/api/v1/user/tokens/` | GET POST | GET NEVER; POST SESSION_ONLY |
| `/api/v1/user/tokens/{token_id}/` | DELETE | NEVER |
| `/api/v1/videos/{source}/{media_id}/game/` | DELETE GET PUT | DELETE `watchlist:write`; GET `watchlist:read`; PUT `watchlist:write` |
| `/api/v1/videos/{source}/{media_id}/plays/` | GET POST | GET `watchlist:read`; POST `watchlist:write` |
| `/api/v1/videos/{source}/{media_id}/plays/{external_id}/` | DELETE | `watchlist:write` |

## Adding an endpoint

1. Add the route and view as usual.
2. Add the `module.ClassName` entry to `VIEW_SCOPES` with a scope per method.
3. Run `scripts/test.sh api.tests.test_fork_scope_enforcement`.
4. Regenerate the verified OpenAPI artifact if the endpoint is in the verified
   subset. The command is in `AGENTS.md`.

If no existing scope fits, add one to `SCOPE_DESCRIPTIONS` and document it here.
Do not reach for `ANY_SCOPE` to avoid the decision.
