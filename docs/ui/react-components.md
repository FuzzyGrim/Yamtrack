# Shared React web components

Every component created or edited by an agent, including copy or styling edits,
must follow the [cross-platform contract](../architecture/ui-modernization.md)
with visual and behavioral parity. Modernize by reusable component, keeping
untouched siblings in place. The `frontend/ui/controls.jsx` contracts below
describe legacy web consumers and guide a native-compatible replacement;
universal code must not import that web-only file. Feature adapters supply domain
data and actions and own requests, permissions and platform integration.

Structural rules for page parity:

- Page titles, subtitles and section framing belong to the shared page view.
  Platform chrome may add only navigation affordances the platform requires
  (a native back button, safe-area insets), never a second page title.
- Breakpoints and card sizes come from shared helpers (for example
  `shelfCardWidth` in `frontend/ui/HomeShelves.jsx`), not per-surface numbers.
- Icons are parsed shapes rendered by `IconRenderer.web.jsx` and
  `IconRenderer.native.jsx`; a surface must not invent its own icon set.
- A platform-only control is allowed only when it is unavoidable and explicit
  (the native server-address field). Everything else must be the same
  composition on web and native.
- Themes resolve through the shared theme contract; do not hardcode a palette
  per screen.

## Keyboard-aware overlays

Use `KeyboardOverlapContext` for shared overlays that must fit above the keyboard.
The web root supplies visual-viewport overlap; `ThemeRoot` supplies the native
`useKeyboardOverlap` adapter. Call the supplied hook with `true` for an animated
overlap value and use it in an `Animated.View` layout style, as `DropdownMenuContent`
does. Keep the subscription inside the hosted modal content so its layout updates
reach the visible host directly. A hook without that option returns a numeric overlap.

On iOS, the native adapter starts from keyboard frame notifications and uses the
reported duration with an ease-out quartic curve. This is an approximation validated
by the user on a physical iPhone Duo, not the system's exact curve. Height animation
uses the JavaScript driver because the native driver does not animate layout sizes.
Android reports the settled keyboard frame and applies it immediately; web uses the
visual viewport. Verify opening and dismissal on each applicable platform, including
short and scrollable content, and report runtime evidence separately.

## Writing a component so it can reach native

Only `AlphabetBar`, `RatingStars`, `SettingsCard` and `Chip` (in `frontend/settings`)
are written with `react-native` primitives today. Everything else in this file is
React DOM with CSS classes and will need its presentation rewritten before it can run
on iOS or Android. Do not extend that debt: write new and edited shared components
with primitives and theme tokens, props only and no browser API, put browser behavior
in a `mount*.jsx` or `*.web.jsx` adapter, and record a `Native readiness` line in the
register. The rules, the measured state and the proposed native proof are in
[From React on the web to native
apps](../architecture/ui-modernization.md#from-react-on-the-web-to-native-apps).

| Component | Contract |
| --- | --- |
| `Card`, `CardGrid` | Shared settings frame/header, optional header action, two-column or automatically fitting layout; all card badges stay at the top right while titles wrap |
| `CatalogCard`, `CatalogGrid` | Linked service/resource cards with optional logo and attention state; container-width columns with a maximum of four, retaining two columns at intermediate widths |
| `FilterToolbar` | Search/filter form layout; uses `Field` and `SelectField` with optional visually hidden labels, retaining accessible names |
| `Chip` | Passive, button or link badge with consistent highlight and no underline |
| `AlphabetBar`, `AlphabetEmpty` | Shared RNW Pressable/Text alphabet navigation and filtered empty state in `frontend/ui/AlphabetBar.jsx`; surface adapters supply translated options, active state, URLs and navigation. The slim rail starts at the first poster, fits every entry without scrolling, puts # before A, and displays an actual other-bucket glyph. The web adapter centers glyph ink using font metrics; the empty state reuses Chip |
| `RatingStars` | Star row with hover preview and toggle-to-clear; `scaleMax`, `value`, `label`, `renderStar`, `onSelect`. The web adapter (`mountRatingStars.jsx`) owns the htmx write, CSRF and popup events. `RatingStarIcon.web.jsx` / `.native.jsx` render the star glyph on each platform |
| `ArtworkImage` | Poster/backdrop image with a primary/fallback source chain, loading placeholder, unavailable state, optional stale marker and provenance input; the browser implementation keeps lazy/deferred loading and the remembered-artwork and error hooks in the existing scripts, and the native implementation uses the platform image component |
| `RefreshNotice` | Cache/refresh notice with loading, stale, error and complete states, dismiss/retry intents, retry/offline notes and a polite live region; browser pollers and lifecycle stay in the existing adapters |
| `PlaybackCard`, `CheckInState` | Home Now Playing card and the running detail check-in state. Callers supply artwork, status, offset/duration/update time, stale window, labels, palette, token-owned stop/cancel intents and ordered check-in actions. The web adapters (`mountPlaybackCard.jsx`, `mountCheckInState.jsx`) keep fragment polling, htmx writes and the server fallbacks |
| `RatingChip` | Personal score chip and rating popup for detail, music artist/album, comic issue, standalone episode and compact track/issue rows; `pill`, `card` and `compact` layouts, viewer scale, empty/edit states, derived-TV score and coverage. The web adapter (`mountRatingChip.jsx`) owns the htmx write, CSRF, popup anchor, dismissal and theme; native callers supply the same props |
| `DetailComposition`, `DetailHeader`, `DetailSynopsis`, `DetailActions`, `DetailSection` | Native detail hero and composition: artwork, title/subtitle, facts, expandable synopsis, primary actions, ordered sections and public/read-only states. Callers supply concrete palette values and action callbacks; the browser adapter mounts the shared synopsis over the server fallback and leaves htmx title/actions/carousel sections server-owned |
| `ProgressBar`, `ProgressCard`, `ProgressBreakdown`, `ProgressDialog` | Watched-once bar with a current-pass glow, the detail “Your Progress” card, the expandable season/episode breakdown and the detail dialog shell. Callers supply percentages, season/episode view models, labels, palette, loading state and navigation/cancel/close intents. `ProgressCard.web.jsx` is the browser card implementation that keeps the detail-chip classes and teleported tip. The detail adapter (`mountProgressDetail.jsx`) loads the existing htmx breakdown fragment, keeps the cancel write and out-of-band card refresh, and leaves the Alpine card/modal as the server fallback; `mountProgressBreakdown.jsx` still serves that fallback modal |
| `DateTimePicker` | Tracking date/time field with quick actions, optional 12/24-hour time, locale/month/year navigation, six-week grid, marked/selectable days and the hidden value contract; the browser adapter (`mountDateTimePicker.jsx`) owns the hidden input, htmx events, status/paired-date autofill, copy and clear |
| `ToggleChip` | Controlled two-state chip; `value`, `onChange`, `offLabel`, `onLabel`; stable accessible label and `aria-pressed` |
| `Field`, `SelectField` | Labeled input and preference dropdown; semantic panel/border/focus styling, inset arrow and custom option menu matching Preferences; caller supplies IDs, values/options and handlers |
| `FilterDropdown` | Same dropdown interaction with Lists hub styling: funnel icon, filled trigger/menu, purple selected option; visually hidden accessible label; use in filter toolbars rather than preference forms |
| `SearchResults` | Search results surface: media-type/source scope, grid/list layout, local and online sections, matched titles, empty/loading/error states, bounded paging or load more and the online-search action. Callers supply resolved card models, labels, palette and navigation/action/rating callbacks; the browser adapter keeps URLs, card popups and rating writes |
| `ParentPicker` | Parent selection panel for TV shows, seasons and music albums: optional title/description, search field, ready/loading/empty/error states, selected row and select/retry callbacks. Callers supply plain row descriptors, labels and palette; the web adapter (`ParentPicker.web.jsx`) owns the htmx fetch, hidden input and fallback select |
| `ListCard`, `ListHeader` | Lists hub card and list-detail header: cover, visibility/smart state and member/role metadata in the card body, item/completion counts, search/tag/empty states and header actions. Metadata stays out of artwork corner chips; callers supply list records, palette and navigation/action callbacks |
| `HistoryFeed` | Bounded History feed: day groups and summaries, watched/read/played rows, month and filter controls, edit/delete, lazy days, load more and loading/empty/error states. Callers supply the feed model and navigation/load/filter/save intents; the browser adapter maps those intents to existing History URLs and fragments |
| `StatisticsSummary`, `StatisticsCharts` | Statistics summary, highlights, summary cards, range/media-type controls and native chart families: activity rhythm and heatmap, hours by media type/month/weekday/time, status composition, rating distribution, status breakdown, completion and featured-repeat ring. Callers supply server-shaped values, labels and the shared theme; charts use React Native primitives with accessible summaries and loading/empty/error states, never Chart.js, a WebView or client-side statistics |
| `FilterBar` | Universal filter toolbar: media-type and status multi-select, data-driven attribute sections, sort with direction, layout and saved-view selection, relative/absolute ranges, clear/default, selected/disabled/busy states and responsive sheets. `mountFilterBar.jsx` mirrors Alpine state and reuses the existing form/htmx refresh on the library and custom-list toolbars |
| `Tooltip` | Accessible help trigger with a viewport-clamped portal; usable from a legacy island with a title fallback |
| `ActivityList` | Keyboard-scrollable bounded activity list for long histories |
| `MediaIcon` | Shared media-type glyphs for provider and library rows |
| `StatusIcon` | Accessible success/warning/neutral icon with a descriptive tooltip; feature code supplies the actual meaning |
| `FeatureList` | Shared feature rows with `key`, `label`, optional `note`, `status` and semantic `tone`; leading accessible status icons replace repeated Active text, with other states labeled and no dividers |
| `Button` | Shared React Native `Pressable`/`Text` action with primary, surface, solid danger and ghost danger tones; the browser platform implementation keeps real button/anchor semantics, form submission, links and htmx attributes |
| `Form` | Native POST with explicit action, CSRF and optional local return destination/confirmation |
| `Dialog` | Native modal dialog, Escape/backdrop dismissal and focus restoration; caller supplies translated title/close label |
| `Tabs` | Native navigation links with current-page state and no hover underline |

```jsx
import { Card, Field, SelectField, ToggleChip } from '../ui/controls';

<Card title={t('Import options')} headerAction={
  <ToggleChip value={privateImport} onChange={setPrivateImport}
    offLabel={t('Public')} onLabel={t('Private')} />
}>
  <Field id="profile-name" label={t('Username')} value={username}
    onChange={event => setUsername(event.target.value)} required />
</Card>
```

The chip expresses a choice; it does not grant access or implement authentication.
AniList owns the public-username/private-OAuth distinction and credential gate.
Another import can reuse the chip while keeping its own authentication rules.
Dropdowns share click-away, Escape, Tab, arrow/Home/End navigation and type-to-select
behavior. `SelectField` is for preferences; `FilterDropdown` is for filtering
results. Preserve their distinct appearances when adopting them elsewhere.
Provider `web.jsx` only adapts the shared form to its page's CSRF and return URL.
Do not put provider-specific branches in the shared controls.

`frontend/ui/controls.css` owns web control styling. Use existing semantic surface,
border and text tokens; the established purple action role remains separate from
the theme's general accent. Focus rings must not animate through a white outline.
Interactive chips use `:focus-visible`: keyboard navigation shows a ring; pointer
clicks retain normal focus without leaving a ring behind.
Shared card/chip presentation uses React Native Web; DOM form controls, navigation
and dialogs are explicit browser adapters. No iOS/Android verification is implied.

For adoption on a legacy screen, mount only the component's owned subtree using
the [existing loader and lifecycle](settings-cards.md); keep one React runtime and
one framework owner per subtree. A component port does not require converting the
whole page. Preserve backend action contracts and a useful server fallback. Update
the [register](component-register.md) and [source map](source-map.md) with consumers,
verification and remaining legacy implementations whenever ownership changes.

Layout and breakpoint redesigns require adopting the affected shared React
components in the same change. The Providers directory demonstrates this pattern:
its cards, grid and toolbar are shared controls; `frontend/providers/Directory.jsx`
owns server filtering, request cancellation, retry and URL/Back navigation. The grid
uses container width, with two columns from 24rem, three from 38rem and four from
56rem. Navigation columns therefore cannot force four compressed cards merely
because the viewport crosses a desktop breakpoint. A native GET form and server
cards remain available if the lazy module fails.

Import preferences use `frontend/providers/ImportPreferences.jsx` in both React provider pages and a bounded legacy island. The provider retains its action and authentication logic; the shared controls save existing import mode values and user watchlist preferences. File-only and other one-time imports show a disabled preference dropdown with a separate accessible tooltip, without saving the locked value over the user’s general frequency.

Account-backed connections use `frontend/providers/AccountConnection.jsx`: the provider adapter supplies field metadata, state, messages and existing action URLs. The same module renders manual import actions using shared cards, fields, forms and buttons. Stored credentials and URLs do not enter React props; the server fallback remains usable if the island fails.
