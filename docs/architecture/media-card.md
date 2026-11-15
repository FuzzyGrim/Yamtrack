# Media card

Every poster grid of media items is one card, `src/templates/app/components/media_card.html`.
The page it appears on is its *surface*, and the only differences between surfaces are
declared in `src/app/card_surfaces.py`. This page explains the contract; if the two
disagree, the module wins.

## The rule

A template renders the card with the tag, never with `{% include %}`:

```django
{% media_card "collection" item=entry.item media=entry.media collection_completeness=entry.completeness %}
```

- **`surface`** picks a row of `SURFACES`. The row sets the card's flags (status chip,
  hover actions, next-event subtitle, and so on).
- **Card values** (`item`, `media`, `title`, `subtitle_override`, ...) belong to this one
  card. Any you do not pass are cleared, so a value from the page (the detail page's own
  item, another card in the loop) cannot leak in.
- **Page values** (`public_view`, `enable_bulk_select`, `home_row_id`, ...) belong to the
  page. Any you do not pass are inherited from it. This is how a public detail page hides
  the hover actions on its related cards.
- A value not declared in either list raises `TypeError`, and an undeclared surface raises
  `ValueError`. A new flag goes into the module with its reason, not onto one call site.

## Who draws the card

The shared `MediaCard` import resolves to `frontend/ui/MediaCard.web.jsx` on the
browser and to the React Native implementation through
`frontend/ui/MediaCard.native.jsx` on native. The browser implementation draws every
browser surface — Home, library/search/discover/history/detail/list and the collection
browser — with artwork, chips, status, rating, hover buttons, popups and the
bulk-select checkbox. The title, rating and subtitle lines overlay the poster's bottom
edge over the faded `bg-card-body` gradient, so the text is part of the card rather
than a separate block below it. It keeps the established DOM classes so themes, title
settings and hover rules still apply. The server sends the `.media-card` wrapper as
the React host, a props `<script>` built by `app/media_card_model.py`, and a first-paint
poster and title link that React replaces on mount. Keeping the wrapper as the host
means the established `search-result-card`/poster first-child rules still size the
artwork and place the body over it. The card's popups open in the shared host: the
Add-to-lists form is React (a JSON form model from `lists_modal?format=json`), and the
Track popup opens the shared Track dialog for every available tab, with Metadata,
Collection and Episode plays still server-owned inside it (see POPUP-HOST in
`docs/ui/component-register.md`). Tests that care about a chip, rating or
button read the model with `app.tests.card_props.card_model`, not the markup.
`MediaCardInventoryTest` fails if a surface opts out, the template regrows a
legacy body, or a template draws `media-card` markup that is neither the tag nor
listed in its `DOCUMENTED_EXCEPTIONS`.

## Native card

`frontend/ui/MediaCard.jsx` is the shared React Native card for native and the
shared React Native surfaces over the same `card_model`: poster, square, landscape
and row layouts, artwork fallback, status
and media-type chips, corner chips, progress, title and alternative title,
subtitle lines, score and the rating affordance, plus read-only and selectable
states. Poster layouts overlay the title, rating and subtitle lines on the
artwork's bottom edge; the row layout keeps metadata beside the artwork.
`frontend/ui/MediaCardActions.jsx` renders the action overlay, row buttons
and list-reorder controls from the same action specs, with explicit `onAction`,
`onReorder` and reveal callbacks; touch reveals the buttons and hover reveals them
where the platform reports hover. It never posts, fetches or opens a popup itself.
`frontend/ui/MediaCardActionHost.jsx` is the native popup shell for modal actions,
while the browser keeps `ModalHost.jsx`. `native-proof/app/surfaces/MediaCardSlot.jsx`
renders the card and the callback contract on Home, Discover and Library with demo
library items.

`MediaCard.web.jsx` is the browser implementation and `mountMediaCard.jsx` is its
server adapter; both keep web consumers, themes, popups and server writes. Every
browser route must resolve this implementation through the shared `MediaCard` import;
using the React Native shell on the browser regresses the established hover action
overlay, faded metadata gradient, rating popup and chip appearance. The native action
presentation owns Track and Add-to-lists form bodies, the song and episode Track
variants, Discover plan/hide, list reorder and the shared card-change event. Link
actions route through the platform navigation callback instead of opening an empty
popup. The list reorder drag handle starts a native `PanResponder` gesture and moves
the touched card over the nearest card centre; the same handle exposes an
`adjustable` position and hint, increment/decrement accessibility actions, and the
step buttons remain the keyboard and screen-reader equivalent. Reorder results and
failures are announced through the native accessibility API. Drag drops persist
through the full-order `PUT /api/v1/lists/{id}/items/order/` endpoint, with the
existing first/back/next/last endpoint as the fallback when only one item id is
available. Discover planning and dismiss send the complete candidate payload and
remove the candidate from the native Discover grid; the Track Discover tab
hide/unhide action publishes the same event. Bulk status changes arrive through the
shared card-change bus and update matching cards without a page reload. Read-only
cards suppress actions, selection and rating writes, while selectable cards expose
the shared checkbox state for bulk surfaces.

The web Track General form is now shared presentation through
`frontend/ui/mountTrackForm.jsx`; the remaining Track tabs and the song/episode
variants keep the server-rendered form as the progressive fallback. A live account,
paged and tagged list loading, and a full browser/device/screen-reader interaction
pass remain. The iOS 17.2 simulator and Android 15 Pixel 7 AVD rendered the shared
cards earlier; Android demo images did not load and no action overlay, drag, popup,
bulk refresh or screen-reader pass was exercised. Runtime evidence for the finish
pass is unverified even though the component is otherwise complete.

## What `media` must be

`media` is the viewer's tracking row for the item, or `None` when they do not track it.
The rating and the status chip read from it, and they are never a per-surface choice: a
tracked item shows its rating on every surface. Load it with
`app.media_list_filters.media_list_entries_for_items(user, items)`, the library's own
lookup, which aggregates repeat viewings. It costs one query per media type on the page.

#1271 was a surface that passed `media=None` for items the user tracks. Collection and
the list recommendations queue now use the shared lookup.

## Video artwork

Video-only library grids and video Home shelves use 16:9 cards. All Media Home
shelves align poster, square and landscape cards at the poster height, retaining
their native aspect ratios and allowing their widths to grow. Other mixed card
surfaces retain the poster-sized video card, containing rather than cropping
video artwork. History groups video plays in a separate
16:9 row after its poster and square rows. These dedicated video rows use wider
cards than posters and the comfortable phone card size even in compact mode.
All three use the existing card interactions and lazy artwork loading.

For standard YouTube `hqdefault.jpg`, `mqdefault.jpg`, or `sddefault.jpg`
artwork, these cards display YouTube's
high-resolution `maxresdefault.jpg` variant and fall back to the stored image
if the high-resolution thumbnail is missing. The
stored image is unchanged, including client-supplied or user-selected artwork.
Thumbnail dimensions do not classify a video as a Short.

## Rating from the card

A tracked card's rating is a button: the filled star and score, or, when unrated, an empty star
that shows while the card is hovered. It opens the shared star row (`RatingStars.jsx`) in a popup
teleported to `<body>` because the card clips its overflow. Public pages, modal search cards and
untracked items keep the plain read-only rating. The score and save responses send a
`floppyCardUpdate` event that refreshes the card's rating and status chip.

## Surfaces

| Surface | Differs from the default | Why |
|---|---|---|
| `library`, `collection`, `related`, `seasons`, `list_recommendations` | Nothing | |
| `home` | Next-event chip and subtitle | Upcoming shelves lead with the next release; episode cards supply show and S01E02 identity as their subtitle |
| `up_next` | Queue chip and always-visible subtitle | Next episode and backlog/countdown must be readable without hover, including on touch screens |
| `list` | S01E02 subtitle on episodes | A list can hold single episodes |
| `credit` | Role line and watched-runtime chip from the `credit` value | A person's or studio's titles show what that person did on each one |
| `tier` | No status chip, no hover actions, no release-year placeholder | A tile on a list's tier board: the board owns clicks and drags |
| `group` | No status chip, no hover actions, no release-year placeholder | A game franchise/series tile shows a group, not one tracked game |
| `search` | No release-year placeholder | Provider results are not saved items |
| `search_modal` | Click previews, no hover actions, darker surface | Picking an item inside a modal |
| `discover` | No status chip, Discover hover actions | Candidates are untracked by definition |
| `discover_hidden` | No status chip | The subtitle is the date it was hidden |

The library alone shows a "No Status" chip on untracked entries. That comes from its
own entry wrapper (`media_list_views.MediaListEntry.is_statusless`), which serves the
library's status filter. Every other surface shows no chip for an untracked item.

## Adding a surface

1. Add a row to `SURFACES`, with a comment for each difference from the default.
2. Load `media` with `media_list_entries_for_items`.
3. Render with `{% media_card "<surface>" ... %}`.

`app.tests.test_media_card_contract` renders every surface. It fails if a template
includes the card directly, names an undeclared surface, or loses a rating or status
chip that the table says should show.

## Not this card

These cards are built separately, on purpose, because they show a different kind of
thing or use a different layout: list cards (`lists/components/list_grid.html`),
history day cards, the Now Playing server-rendered fallback, person and cast cards, and statistics highlights. The React-owned Now Playing card uses this shared MediaCard.
A person's or studio's list of titles is not one of them: it uses the `credit` surface, with
`people_views.attach_card_media` loading the viewer's tracking row for each title. The Statistics
repeat-player strip is still the older hand-built `person_filmography_card.html`.
Do not copy the `media-card-*` classes into a new card for a media item; use the tag.

The library's album and artist grids (`artist_grid_items.html`,
`album_list_grid_items.html`) are also separate. The card needs an `Item`, and albums
and artists only get one through Home's placeholder rows, which are written to the
database. The library does not write while it reads, so these cards stay separate. They
use the shared rating partial, `media_card_rating.html`, so a rating reads the same.

Known remaining duplication:

- Home builds its tracking rows in `users/home_screen._media_lookup_for_items`, which has
  season-status and artwork fix-ups the shared lookup lacks.
- Lists build theirs in `lists/views_helpers._attach_media_with_aggregation`, which also
  loads episodes' seasons and annotates `max_progress`, and serves the owner's data on
  public lists.
- Moving either onto the shared lookup needs its own change, with query-count checks.

## Artwork loading

Poster cards put the artwork URL directly in `src` and use native `loading="lazy"`.
This lets the browser reuse cached artwork without waiting for JavaScript to replace
a placeholder on each navigation, including HTMX swaps. Keep the aspect ratio so
offscreen cards can stay deferred without shifting the grid.

`artwork-loading.js`, loaded by both base templates, remembers up to 256 successfully
loaded poster URLs and prepares them during HTMX swaps.
Returning artwork uses eager loading and synchronous decoding so a cached image does
not wait for another lazy-load visibility pass. URLs survive normal document
navigation in session storage; a parser observer promotes them before the initial
visibility pass and disconnects at DOMContentLoaded. The first eight images in each
document or response load eagerly; unseen artwork beyond them stays lazy. Table
thumbnails and other hand-built artwork use `data-media-artwork` to share this policy.
Recently used URLs stay at the end of the bounded history rather than being evicted
just because they were first encountered early in the session.

Warm images carry `data-artwork-ready="true"`, which suppresses the containing page's
and grid card's entrance animations. Otherwise an already-cached poster still fades
in with a stagger on every navigation or appended page. No image fetch or decode is
awaited before swapping the page. The login-response guard
checks an actual meta element; matching raw response text would match the guard's
own script and turn each boosted navigation into a second full page request.

The list index has a separate cold-cover lookup for provider backdrops. Resolved URLs
are cached for one hour and rendered directly on repeat visits. The key includes the
first item's ID and image, so changing that item or its artwork bypasses the old entry.
Only a cold cover uses the per-card HTMX request; page rendering never fetches providers.
The first eight resolved index covers are eager even on a full document load.

## Card artwork size

A card asks the provider for a file sized for it, not the stored original. One server
function, `card_image_url` in `src/app/artwork_sizes.py`, rewrites the provider's own
size token: IGDB `t_original` becomes `t_cover_big_2x` (528 px wide, about a tenth of
the bytes) and TMDB `original` becomes `w780`. TMDB `w500` posters are already the right
size for a 2x card and stay as they are. Sizes are only made smaller; other hosts, local
covers and data URLs pass through unchanged. `card_model`, `history_card_model`,
`row_card_model`, detail related cards, Home's sort-flip prefetch and the API item's
`card_image` field (the native Library card) all use it, so web and native load the same
file. `image` itself, Detail heroes and backdrops keep the stored size. Add a provider
only after checking its stored URLs follow a size scheme.

With the image cache on, the card size goes through the proxy like any other URL, so
the proxy keeps a second, much smaller file for a picture that is also shown full size.
On a sample of IGDB covers the card file averaged 64 KB against 730 KB for the original,
so a picture opened in Detail costs about 9% more disk, and one only seen on cards
about 90% less.

On native, `ArtworkImage.native.jsx` draws through `expo-image` (memory and disk cache,
no fade-in) behind the shared `ArtworkImage` fallback chain, so a card that scrolls
back or a grid opened again paints without a new download. The browser keeps its
`<img>`, which the browser already caches.

## Render cost

`MediaCard` is memoized on its props on web and native. A list re-render (a Home
shelf arriving, a Library page appended) skips cards whose model and callbacks are
unchanged, so parents pass stable props: `HomeShelves` and `MediaCardSlot` bind card
callbacks through a ref, and card models are built once per row. A changed model still
re-renders its card. `npm run test:ui-render` checks both. The native Library grid is
a windowed `FlatList`; the web keeps server paging and document scroll.

## Card profiles

Subtitle lines come from `User.card_metadata`, one profile per media type plus `person`.
`src/users/card_metadata.py` is the registry. `card_context` resolves the profile for the
shared card. The picker's properties are generated per type: a card-only field declares
its types, and a field that reads item metadata is offered for every type whose
`app.custom_metadata.MEDIA_TYPE_FIELD_ORDER` entry contains that metadata field. Platform
is therefore a game card property without a second per-type list to maintain.
Hand-rolled cards call `{% card_lines %}`. `display` is `hover` or `always`.
Rows (episode rows, calendar rows) have no hover state, so their hover lines are always
visible. Hand-rolled poster cards add the saved title treatment with `{% card_title_classes %}`,
and only once the user has changed a title option, so unchanged cards keep their own clamps.
The poster progress bar follows the `progress` field. A zero score is hidden per type
via `options.rating.hide_zero`.

Cards that read the profile: the shared card, history, music grids, search music results,
`media_card_list`, episode rows, person and cast cards, stats highlights, active playback,
and calendar rows. `list_grid.html` is a list index. It always shows the item count and
does not read a profile.

`GET /api/v1/user/preferences/` still accepts the old names `media_card_subtitle_display`,
`progress_bar`, and `hide_zero_rating`. A name is omitted when types disagree. `PATCH`
of one name writes every type. The columns are gone.
