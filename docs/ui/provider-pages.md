# React provider pages

The Providers directory is also React-owned through
`frontend/providers/Directory.jsx`. It consumes shared catalog cards/grid and
filter toolbar controls. Its authenticated directory URL supports `format=json`
with explicit presentation fields only; filtering remains server-authoritative.
The 300ms debounce cancels stale requests, updates query/capability/media in the
URL, and restores filters/results on Back. Failed requests offer retry, and failed
bundle loads preserve a native GET form and server-rendered cards. See the
[shared component contracts](react-components.md) for container breakpoints.

The Connection filter lists providers with Connection settings and links directly
to that tab. A shared status icon distinguishes available credentials/saved accounts,
missing setup and warnings. Availability includes bundled metadata credentials;
its tooltip explicitly identifies bundled defaults. This describes local setup,
not a live remote health check. The credential fields show masked previews and
identify bundled fallbacks, so an available default cannot look like an unset key.
Editable inputs remain empty: previews are hints, never values submitted by a form.
Saved key previews stay in server-rendered credential fields and are excluded from
React presentation JSON.

Every provider uses the same React `ProviderPage` for its header, media chips,
tabs and Overview. `users.provider_ui.provider_page` supplies one presentation
contract; `overviewCards` describes supported capabilities and state. There are no
provider-specific Overview layouts. AniList additionally has React-owned Connection,
Import and Activity bodies. All providers' Metadata bodies also use one shared
React component. The application shell and settings navigation still
come from Django. Unported tab bodies retain their existing owners; do not infer
support for their metadata, tracking or outgoing-sync controls from AniList.

## Component and framework ownership

`frontend/providers/ProviderPage.jsx` composes tab components and the shared
`SettingsCard` and the [app-wide controls](react-components.md) from
`frontend/ui/controls.jsx`: fields, selects, buttons, chips, tabs, card grids and
modal dialogs. `web.jsx` only supplies provider context and a thin form adapter.
These shared controls contain no provider logic. They are React-owned DOM controls,
not server HTML inserted into React. The card's React Native presentation remains
reusable; the complete page and its browser controls are not a native implementation.

The provider route serializes a narrow view model from `users/provider_ui.py`
through `json_script`. `provider_detail.html` exposes one `data-provider-root`;
the common loader lazily mounts it through `frontend/providers/mount.jsx`.
For `contentOwner: react`, React owns the entire provider region, including all
Overview pages and AniList's implemented tab bodies. Nested fallback card-header
roots are excluded from mounting. For `contentOwner: server`, the root owns only
the shared header/tabs; `content_legacy.html` is a separate sibling outside that
root, retaining Alpine/HTMX forms and partial React card headings. Its props script
lives beside the header root inside the same scoped container. No server HTML is
passed into React and no active legacy control is nested inside a React root.

Legacy markup is a loading/error fallback, not a second running UI. The root has
`x-ignore` and `hx-disable` so Alpine/HTMX cannot initialize its descendants before
or after React takes ownership. Fallback HTML is never a React prop or hydration
source. It preserves readable navigation and Overview plus traditional credential
and public-import submissions; scripted preference editing, help and activity need
the React bundle. Fallback public imports use saved preferences. Drafts typed while
the lazy module loads are captured locally before replacement; entered credential
values stay in current-page browser memory and are never added to serialized server
props, logs or persistent browser storage.

The partial Django card composition remains for other providers and uses the same
runtime, palette and lifecycle. Its server body slot stays outside its header root.
Port those consumers when their complete controls are in scope; do not create a
parallel framework owner inside either kind of root.

## Overview is actual state

Overview cards are data-driven, follow the provider tab order, and use one card/grid renderer for every provider.
Overview and the directory use the same `CatalogGrid` container breakpoints.
Only supported tabs get cards: AniList has Connection, Automatic import and Last
active import; BoardGameGeek has Connection and Metadata. Bundled credential sources
are identified explicitly. Existing capability state supplies summaries for tabs
whose richer Overview content has not yet been designed.

The generic import adapter uses the existing source aliases and owned activity
query, excludes one-off schedules and combines recent task state with persisted
import runs. It passes `refresh_results=False`, so opening Overview does not refresh
Celery results or write task state. AniList retains its established full-history
read adapter. Both produce the same card contract, without UI branches per provider.

The Connection card answers "is my account linked?" for every provider that has
an account (`provider["state"]["account_linkable"]`): "Account linked", "Account not
linked", "Waiting for you to approve" or "Account needs attention", with the
credentials in use as a second line ("Bundled credentials active"). Credentials alone
never turn the badge on. Providers without an account keep the credentials headline.

## Keys that belong to Floppy

A provider whose key is issued to the app, not to its users, is registered with
`bundled_only=True` in `app.providers.credentials`. That one flag removes the key
from every user-facing place: no credential forms, no "Get a key" link, no
Settings > Metadata entry, no credential line on Overview, and the save, clear and
personal-key routes answer 404. Only the environment (a rebrand or public host with
its own key) can replace it. WeTrakr is the first. Its Connection tab is the shared
account card; sign-in is the shared `ProviderLinkCard` device-code flow, which the
web adapter (`mountAccountConnection`) and the native app both use, and a code
started before a reload resumes.

Each card is a native, keyboard-accessible link to its owning tab.
There are no separate manage/configure/view footers. All three status subtitles
use the same muted typography and omit terminal periods. Tabs retain the active
bottom-border indicator without adding text underlines on hover. The grid responds to available
container width, and shared card headings wrap badges instead of spilling outside.
Hover/focus outlines appear immediately in their theme color; they are excluded
from color/background transitions to avoid a white default-outline flash.


- Connection: effective personal or instance credentials, or neither. All required
  fields must resolve. Personal fields may use instance fallback; this is stated
  in a hover tooltip. The inactive explanation is also a tooltip, keeping the card
  body to one status line. No provider network probe or OAuth login is implied by configuration.
- Automatic import: enabled, recurring AniList schedules owned by the current user.
  A saved frequency preference, disabled schedule or one-time job is not an active
  recurring import. The next date uses the existing schedule helper and user format.
- Last active import: the latest AniList run or newer queued task for this user.
  Runs older than the seven-day Activity window remain eligible. Lost pending tasks
  older than 30 minutes are excluded consistently with existing import history.

These decisions remain in Python. The React components consume explicit values;
they do not inspect credentials, parse database kwargs or recreate scheduling rules.

## Existing writes and safe data

Connection posts to the existing instance/personal save and instance-clear handlers.
Superuser checks and environment locks remain authoritative on the server. No saved
credential values or masked previews are serialized. The view model carries only
field metadata, presence/lock flags and safe presentation text; new values are entered
directly into React-owned browser forms. The web boundary supplies CSRF and safe
local return URLs. Blank values retain/remove the same fallbacks as before.

Import preserves the user's global frequency/time/mode preferences. Auto-save writes
are serialized so a slow earlier request cannot overwrite a newer selection. Failed
saves are visible and retryable. Import submissions carry the currently selected
values and use the existing public username or private OAuth action. Public imports
work without Connection; only private imports require configured client credentials.
Unsupported modes remain disabled. Unmount aborts pending preference work.

The web-only activity contract is the existing authenticated provider URL with
`tab=activity&format=json&page=1`. It is not a new public/native API. It returns
current-user AniList schedules, seven-day task history in pages of 50, the latest
ten runs, and scoped imported-media counts/actions. Paging is bounded to 1,000
pages; other tabs reject this format. Responses are private/no-store. No task kwargs,
OAuth tokens, stored credential values or prepared-import payloads are serialized;
error details pass through the existing redactor.

The existing import-activity reader accepts an optional source/page scope; its
valid legacy formats remain supported. SQL text checks are only prefilters;
reads/actions verify the parsed owner field, including compact JSON and Python-repr
kwargs, so embedded text cannot make user 10's task appear to belong to user 1. Cancel, schedule deletion,
undo and source cleanup keep their existing ownership checks, confirmations and
server-side restrictions. Supplied local `next` destinations return to Activity;
unsafe return URLs fall back to the existing destination.

Activity fetches on entry and polls every five seconds while work remains active.
Opening diagnostic details aborts/pauses refresh, retaining expanded text and copy
controls. Closing the last details section reloads the snapshot. Requests, timers
and copy feedback are disposed on unmount. Loading, failure/retry, empty, progress,
success and cancellation states remain visible. Provider-specific match review and
watched-state sync are not AniList capabilities.

## Build and verification

Use the [shared build and lifecycle commands](settings-cards.md). There is one
React/RNW runtime, separate lazy header/page modules, and committed self-hosted
assets. Provider CSS is shared with the small global settings stylesheet. The
expanded loader/CSS budget is 3 KiB gzip; the full page plus shared runtime remains
under the pilot's 110 KiB gzip JavaScript budget. Pages without islands do not fetch
React. Neither Node at runtime nor a second router/dev server is required.

Relevant checks include `users.tests.views.test_provider_react`, the existing
credential/import/model/OAuth/action tests, and
`users.tests.test_settings_cards_browser` (tagged `slow`, `playwright`). Browser
checks exercise one root across all four tabs, private/public import behavior,
CSRF submissions, Overview state, activity details/actions, HTMX cleanup,
back/forward lifecycle events (retaining drafts in persisted bfcache documents), fallback and responsive theme states. Existing
provider-specific integration tests remain authoritative for importer behavior.

iOS/Android, native forms/transport/secure storage, real provider OAuth and physical
mobile assistive technology are not verified by this browser port. Keep those
limits separate from the accepted React web implementation.

## Inline Metadata settings

`frontend/providers/Metadata.jsx` serves every Metadata tab using shared cards,
fields, preference dropdowns, forms and chips. `metadata_page` supplies only
presentation, default-source choices, readiness and action URLs. Credentials and
stored proxy URLs are never serialized. The server fallback uses inline native
forms, without requiring a modal or Alpine initialization.

There is one primary source per library, not a user-editable provider priority
order. TV, anime, books and comics have switchable per-user defaults; other
libraries explain their fixed source. Supplemental providers keep their separate
artwork/score roles. Overview distinguishes active primary/supplemental metadata,
missing credentials and an available provider that is not selected.

A full-width Library sources section below the Metadata cards shows one row per relevant media type, with its current provider and an inline Configure control, including relevant library rows for supplemental providers. The expanded row shows selectable sources where supported, fixed defaults, and alternatives with readiness and Connection links. The Metadata sources card describes the current provider’s contributions; unselected features identify effective sources when known. The right card holds provider-specific settings, currently
TMDB's existing proxy form, or explains that there are no additional options.
The Sources header leads with activity, not edit permissions. A shared `FeatureList`
describes the outputs Floppy consumes and their Active, Needs setup, Not selected
or Not provided states. Game profiles distinguish search/details, covers, title
logos, backgrounds and any additional gallery/score/trailer outputs; unsupported
core artwork/search outputs remain visible for comparison. Search results and title
details are separate outputs. These are local setup
states, not guarantees that every title has every image. Artwork providers retain
their game/platform and fallback restrictions.

Connection and Overview represent public access independently of API credentials.
`provider_catalog.PUBLIC_ACCESS` declares each public capability and its explanation;
the shared settings card marks Instance credentials with a Public API badge. A public
feature can be active while another capability still requires a key. Metadata uses
the capability state and does not repeat the Connection disclosure. Steam artwork
uses the public CDN even without an account API key. ScreenScraper
artwork requires instance developer access; its optional member credentials do not
replace that requirement. Source activity and Overview use those same requirements.
Provider settings without controls use one plain sentence, without a duplicate
heading or Connection action. Source alternatives still link to Connection when
their credentials are missing.
All selections post to the existing authenticated handlers, including personal
credential availability and demo restrictions. Anime library format remains part
of the source form. Existing-library migration counts, warnings and explicit
conversion actions remain separate from changing the default; no automatic move
or conversion is introduced.

Provider directory pins are private per-user preferences and sort ahead of unpinned results, including filtered results.

Import preferences share a React card across provider pages, with a server fallback. History maps its override to the existing new/overwrite import modes; Watchlist and Collection keep their existing modes. Watchlist status and an optional user-owned tag are applied to Plex watchlist imports, without changing existing items' statuses. Providers that do not support a mode retain their existing import guard. Fixed schedules remain read-only. Long activity lists scroll within their cards.

Audiobookshelf provides metadata for imported items; book searches use the chosen book provider and podcast searches use the fixed Pocket Casts default. Imported titles retain their source. Audiobookshelf Connection uses the reusable account-connection island with a Django fallback. Its adapter supplies server URL and API token fields without serializing saved values; a separate card links to Activity and explains that connecting schedules and starts imports, and disconnected or broken accounts are directed to Connection. Import uses the shared account action for manual sync. Other account providers can adopt the same presentation with their own fields, status text and existing action handlers. Its detailed last error remains server-rendered rather than entering React props.

Providers declared in `FILE_ONLY_IMPORTS` show file-only status on Overview and a locked one-time Import Frequency with help explaining why scheduling is unavailable. One-time API imports use their own explanation. These displayed values do not overwrite the user's general import preferences.
