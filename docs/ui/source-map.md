# UI source ownership map

[Component contracts and statuses](component-register.md) explain each stable ID.
Each path has one primary owner; cross-family consumption is described in the
register. Ownership does not mean every member can share one native renderer.
Inline scripts/styles belong to their owning template.

This map covers application HTML/text templates, the frontend component/build sources, JavaScript modules/selfchecks,
vendored scripts/localizations, and CSS. Template SVG icons, fonts, logos, favicons
and the web manifest belong to `TOKENS`/`WEB-UTILITIES` as assets, not independent
components. Translation catalogs, generated staticfiles, third-party admin templates
and uploaded media are outside this application-source map. Vendored library source
was identified by dependency/use, not audited internally.

Update a file's owner here when adding, moving or removing it, and update its affected
contract. Shared includes are owned once; record consumers in the register. Add an ID
only for an independently meaningful behavioral contract.

## Consistency check

Run from the repository root after any inventory, path or status change:

```bash
python3 scripts/check_ui_docs.py
```

It checks that every register entry has one status from the register's vocabulary, that
every backticked path in the register exists, that this map has one section per register
entry with the same `Status:` line, that every template, script, stylesheet and frontend
source file is mapped exactly once, and that no mapped path is missing. It cannot judge
whether a status is true; the register header says how to check that, and
`python3 scripts/ui_migration_metrics.py` measures the migration.
`config.tests.test_ui_docs` runs the same check in the test suite.

## SHELL

Restored adapter evidence: **web — Runtime**, PWA Home and Sidebar inspected beside Expo; browser source is unchanged. **iOS — Runtime**, Home, Sidebar, Search and populated library/provider Search Results inspected in Expo Go on the Duo. **Android — Runtime for Search preview only**, screenshot inspected; full Home, Sidebar and Search Results rendering remains unverified, with platform bundle and transport checks only. These checks do not establish complete interaction, theme or accessibility parity.

`native-proof/app/surfaces/ShellSlot.jsx` → shared `Sidebar` and native drawer; direct flex layout, guided setup separate from navigation. The Expo adapter mounts the shared `Sidebar` directly in the drawer, preserving its full-height flex layout and the reviewed navigation spacing. Guided setup remains separate from the drawer; no setup banner is inserted above its rows. Account-backed pins, loading/retry, sign-out errors and onboarding remain adapter-owned. Shared Sidebar ownership stays fully React-owned with the existing coexisting web fallback and retirement conditions.

Sidebar retains the accepted text metrics and anchored menu presentation while platform keyboard/menu-layer behavior stays in adapters. Account-backed pins and the legacy local-storage adoption path remain. Ownership and fallback retirement conditions are unchanged; the integration does not establish new iOS or Android runtime acceptance.

The saved-view/sidebar fallback adapter loads before Alpine and also registers immediately when Alpine is already running, preserving its once guard. Browser regression coverage checks script order, late real-fallback insertion and Explore toggling without page errors. Shared Sidebar remains React-owned with the same coexisting server fallback and retirement conditions; actual iOS/Android runtime was not reverified by this browser bootstrap fix.

The Django head conditionally initializes the development toolbar when its root exists. Provider browser coverage exercises responses without that root. Shell ownership and existing fallback retirement conditions are unchanged; iOS and Android do not consume this browser adapter and were not verified for it.

Native screen adapters omit empty headers and read heading/section colors at render time. Settings pages share `SettingsSurface`, and changing its active page resets the scroll container. Setup renders outside the signed-in header. Browser file pickers keep a hidden file input attached until selection, cancellation or failure, then remove it; file access stays in the web adapter. Browser verification is recorded per surface; iOS is not verified, and Android is not verified. Native readiness remains `Primitives`.

Status: `Partial / coexisting`

- `src/static/js/libraries/alpinejs-3.14.9.min.js`
- `src/static/js/libraries/htmx-2.0.4.min.js`
- `src/static/js/safari-rail.js`
- `src/templates/app/components/sidebar_navigation_items.html`
- `src/templates/app/components/sidebar_pin_menu.html`
- `src/templates/base.html`
- `frontend/ui/GlobalSearch.jsx`, `frontend/ui/mountGlobalSearch.jsx`, `frontend/ui/global-search.css` (header search form inside `base.html`; suggestions stay a server fragment)
- `frontend/ui/Sidebar.jsx`, `frontend/ui/mountSidebar.jsx`, `frontend/ui/sidebar.css`, `frontend/ui/dropdownMetrics.js`, `src/app/sidebar_model.py` (signed-in sidebar inside `base.html` and Expo; Explore overlays the media-type rows at its content height, capped to the sidebar viewport, scrolls its options when needed and closes when the last option is pinned; native adapter supplies 44-point rows, a 25-point top inset on iOS side-rail layouts, margins accounting for each safe-area inset, and hides Appearance; the onboarding banner stays a server slot)
- `native-proof/app/surfaces/ShellSlot.jsx`, `native-proof/app/surfaces/NativeShell.ios.jsx`, `native-proof/app/surfaces/NativeShell.jsx`, `native-proof/app/surfaces/NativeRoot.ios.jsx`, `native-proof/app/surfaces/NativeRoot.jsx`, `native-proof/app/safeAreaSpacing.mjs`, `native-proof/app/surfaces/menuGeometry.mjs`, `native-proof/app/routes/_layout.jsx`, `native-proof/app/routes/index.jsx`, `native-proof/App.js` (Expo navigation model, native toolbar and drawer/canvas transition; iOS animates drawer geometry in both fold directions unless Reduce Motion is enabled; an iOS screen-scoped safe-area provider supplies live horizontal insets while the navigator supplies vertical insets; the foreground shell reserves those insets and shared spacing subtracts them from the usual Search and sidebar margins; `useVerticalBarEdgeIOS` supplies the rail trait and reserved regions remain for fold layout; Android and web retain the shared modal path)
- `frontend/ui/Icon.jsx`, `src/app/icon_specs.py` (shared icon shapes for React; used by the sidebar and the card hover buttons)
- `src/app/card_events.py` (the floppyCardUpdate event sent by `media_save` and the score views so React cards refresh their status chip and rating)
- `frontend/ui/mountFilterChips.jsx`, `ScrollRow` and `FilterChips` in `frontend/ui/controls` (Discover media-type row; `Tabs` shares `ScrollRow`)
- `src/templates/base_public.html`

- `native-proof/app/components/NativeStatusBar.jsx`, `native-proof/app/themeColor.js`, `native-proof/app/theme.js`, `native-proof/app/systemColor.ios.js`, `native-proof/app/systemColor.js` (iOS system semantic colors; other hosts retain the existing proof palette)
- `native-proof/app/components/Screen.jsx`, `native-proof/app/components/Screen.ios.jsx` (shared screen presentation; iOS applies the remaining 22-point page margin after the shell's safe-area inset on each side)

## SETTINGS-CARD

Status: `Web Migrated`

- `frontend/settings/Chip.jsx`

- `frontend/settings/SettingsCard.jsx` (optional headingLevel preserves native Activity's level-2 sections; default browser hierarchy/appearance and current owners remain unchanged; browser recheck pending, iOS/Android unverified)
- `frontend/settings/index.js`
- `frontend/settings/mount.jsx`
- `frontend/settings/root.web.js`
- `frontend/settings/webPalette.js`
- `frontend/settings/settings-card.css`
- `src/templates/app/components/ui_asset_refresh.html`
- `scripts/build_ui.mjs`
- `src/static/ui/*.js`
- `src/static/ui/*.css`
- `src/templates/users/components/settings_card.html`
- `src/templates/users/components/settings_link_chip.html`

The generated UI subtree belongs to this island runtime; globs cover content-hashed
chunks without renaming ownership records on every build. License notices are
build artifacts. The Python composition tag is documented in the register.

## FORM-CONTROLS

- `frontend/ui/Modal.jsx`, `frontend/ui/Modal.native.jsx`, `frontend/ui/Modal.web.jsx` (shared modal boundary and header close controls; Duo canvas host and topmost rail dismissal; existing dialog consumers retain their mapped surface ownership)
- `frontend/ui/DropdownMenu.jsx` (`FORM-CONTROLS` shared anchored presentation, consumed by Sidebar and the opt-in SelectField popover; web and Android runtime regression unverified)
- `native-proof/app/components/AdaptivePresentationSheet.jsx`, `native-proof/app/components/SheetCloseButton.jsx`, `native-proof/app/components/SheetCloseButton.ios.jsx`, `native-proof/app/components/SheetHost.jsx`, `native-proof/app/components/SheetHost.ios.jsx`, `native-proof/app/components/useSheetInsets.js`, `native-proof/app/components/useSheetInsets.ios.js` (Expo UI sheet adapter and platform close controls; iOS applies a reduced horizontal safe-area inset to content while the close control can occupy the Duo rail; Detail actions consume it, while other modal surfaces remain on `Modal` until individually migrated). The preview rendered on a physical Duo; browser and Android rendering remain unverified.
- Detail Collection uses the shared embedded `CollectionCopyDialog` inside `AdaptivePresentationSheet`; Collection screen and web dialog consumers remain. `DropdownMenu` supplies the sidebar and optional `SelectField` popover; multi-select is opt-in. The native modal host isolates registration from active-entry updates and sheets host nested dialogs locally. These remain partial/coexisting until remaining consumers migrate; Duo opened the Collection sheet and selected Format, while browser, Android and multi-select runtime remain unverified.
- `SheetCloseButton.ios.jsx` sizes the glass control to the Duo rail; the Android and web control retains its existing size. The revised iOS alignment was inspected on a physical Duo; Android and web appearance is unchanged.
- `AdaptivePresentationSheet.jsx` supplies the shared header and delegates rail close placement to its iOS host. The Duo sheet was inspected on device; Android and web rendering remain unverified.
- On an iOS vertical rail, `SheetHost.ios.jsx` uses a native navigation toolbar cancellation action to place the shared sheet's close button below system status controls; the body retains its prior edge spacing. Duo runtime inspection covered the Lists sheet at half and full height and dismissal. Expo web and Android keep their existing presentation and were not runtime inspected for this change.
- `native-proof/app/surfaces/DetailFeatures.jsx` (Expo detail action-row dialogs use the shared adaptive sheet with the sidebar background token and existing embedded tracking/tag/forms, including grouped source and external links from `DetailSlot`; web Detail remains on its existing dialogs; progress, collection and other consumers remain on `Modal`). Links, tracking, notes, tags, lists and More to Cover were inspected on a physical Duo; other actions, browser and Android runtime verification remain pending.

Status: `Partial / coexisting`

The shared React Native `TextField` keeps its large fixed-height input vertically centered without imposing a line-height; legacy browser fields remain owned by `controls.jsx` and CSS.

PR #1878: `Modal.web.jsx` closes open dialogs on Escape while the native implementation remains platform-owned. Universal tooltips provide the tooltip role, and provider credential dialogs expose their accessible name. Shared catalog pin state uses `aria-pressed`; `FeatureList` distinguishes its text body for stable geometry checks.

- `frontend/ui/controls`
- `frontend/ui/controls.css`
- `src/static/css/libraries/select2-4.1.0.min.css`
- `src/static/js/i18n/de.js`
- `src/static/js/i18n/en.js`
- `src/static/js/i18n/fr.js`
- `src/static/js/keepPopupsOnScreen.js`
- `src/static/js/libraries/jquery-3.7.1.min.js`
- `src/static/js/libraries/select2-4.1.0.min.js`
- `src/static/js/libraries/sortablejs-1.15.3.min.js`
- `src/templates/users/components/form_save_bar.html`
- `src/templates/users/components/info_tip.html`

## TOKENS

System theme resolution uses the React Native-supported `auto` override on iOS and Android, avoiding the Android null-argument startup failure while continuing to follow the operating system. Explicit light/dark, presets and custom themes retain their values. Web CSS resolution and its guarded Appearance adapter are unchanged. Theme logic tests cover both system schemes and valid platform overrides; runtime evidence is recorded separately from complete theme/state acceptance.

Status: `Existing (no UI work needed)`

Custom native surface/panel opacity and runtime radius getters are live; static module-level radii and true backdrop blur remain incomplete. Browser effect comparison pending; iOS and Android not verified.

- `frontend/ui/rootStyle.web.js` (web palette reads; one style snapshot per task)
- `src/static/css/input.css`
- `src/static/css/main.css`

## ARTWORK-LOADING

Status: `Legacy`
- `src/app/artwork_sizes.py` (card image size; consumers `src/app/media_card_model.py`, `src/api/fork_views_detail.py`, `src/api/serializers.py` `card_image`, `src/users/home_screen.py` flip prefetch). `frontend/ui/ArtworkImage.native.jsx` renders through `expo-image` (native-only cache carve-out).

- `src/static/js/artwork-loading.js`
- `src/static/js/image-fallback.js`
- `src/static/js/libraries/lazysizes-5.3.2.min.js`

## ASYNC-STATE

Status: `Legacy`

- `src/static/js/cache-updater.js`
- `src/static/js/cache-updater.selfcheck.mjs`

## SPINNER

Status: `Web Migrated`

Shared `Spinner` lives in `frontend/ui/controls` and `controls.css` (FORM-CONTROLS); its consumer templates are mapped under their own families.

- `frontend/ui/mountSpinner.jsx`
- `src/templates/users/components/spinner.html`
- Consumers (12 templates, 16 sites): `app/collection_list.html`, `app/media_list.html`, `app/person_detail.html`, `base.html` (barcode fallback), `app/components/library_move_results.html`, `app/components/media_metadata_panel.html`, `lists/add_item.html`, `lists/custom_lists.html`, `lists/list_detail.html`, `lists/recommend_item.html`, `lists/smart_list_detail.html`, `lists/components/smart_rules_builder.html`.


## BADGE

Status: `Partial / coexisting`

Shared `Badge` lives in `frontend/ui/controls` and `controls.css` (FORM-CONTROLS); its consumer templates are mapped under their own families.

- `frontend/ui/mountBadge.jsx`
- `src/templates/users/components/badge.html`
- `src/users/templatetags/ui_tags.py` (`{% badge %}`)
- Consumers: `app/episode_details.html`, `app/music_album_detail.html`, `app/components/detail_music_artist.html`, `detail_music_album.html`, `detail_music_track.html`, `detail_secondary_content.html`, `cells/media_genres_cell.html`, `media_metadata_panel.html`, `lists/components/add_item_preview_modal.html`, `recommend_preview_modal.html`, `integrations/library_panel.html`, `integrations/seerr_status.html`, `users/components/whats_new.html`, `users/onboarding/metadata.html`

## BUTTON

Status: `Partial / coexisting`

Shared `Button` lives in `frontend/ui/controls` and `controls.css` (FORM-CONTROLS); its consumer templates are mapped under their own families.

Portable Button presentation also supports opt-in palette sizing for native provider/setup consumers; explicit size wins and the web small default stays unchanged. Browser size/radius fixes await final comparison; current iOS/Android interaction verification is open. Existing adapter/legacy consumers retire with their owning forms.

- `frontend/ui/Button.jsx`

- `frontend/ui/mountButton.jsx`
- `src/templates/users/components/button.html`
- `src/users/templatetags/ui_tags.py` (`{% button %}`)
- Consumers (22 templates, 65 sites): `users/account.html`, `users/cards.html`, `users/_reading_server_modal.html`, `users/components/provider_import.html`, `provider_instance_credentials.html`, `provider_personal_credentials.html`, `tmdb_proxy.html`, `media_type_provider_modal.html`, `users/providers/integrations/jellyfin.html`, `users/providers/mal_sync.html`, `users/onboarding/media_types.html`, `metadata.html`, `import_status.html`, `components/connect_trakt.html`, `lists/custom_lists.html`, `integrations/oauth_device.html`, `integrations/match_fix.html`, `app/components/collection_modal.html`, `collection_entry_summary.html`, `music_track_notes.html`, `history_day.html`, `fill_track_song.html`.
- Remaining legacy: Alpine-bound, in-place htmx, icon and composed-attribute buttons (see the BUTTON register entry).

## WEB-UTILITIES

Status: `Legacy`

- `src/static/js/copy-item.js`
- `src/static/js/serviceworker.js`

## ERRORS-DOCS

Status: `Partial / coexisting`

- `src/templates/400.html`
- `src/templates/403.html`
- `src/templates/403_csrf.html`
- `src/templates/404.html`
- `src/templates/500.html`
- `src/templates/api/docs.html`
- `src/templates/includes/error_report_panel.html`

Interactive/native recovery uses shared ErrorRecovery and ErrorReportPanel. The native traceback wrapper explicitly selects the shared module; web keeps its existing adapter. Root error documents and API docs remain server-owned fallbacks. Native-priority dependency tests pass; native error-screen appearance remains unverified.

- `frontend/errors/ErrorReportPanel.jsx`
- `frontend/errors/ErrorReportPanel.native.jsx`
- `frontend/errors/ErrorReportPanel.web.jsx`

## MEDIA-CARD

The iOS `NativeCardMenu.ios.jsx` defines the full rounded `MediaCard` as the system context-menu preview shape for Home, Library, Search and other MediaCardSlot consumers. Android's Material menu and the Expo web dropdown have no corresponding lifted preview; browser card ownership, remaining consumers and retirement conditions are unchanged. Browser, iOS and Android runtime verification are recorded separately in the component register.

`native-proof/app/surfaces/MediaCardSlot.jsx` composes `AdaptivePresentationSheet` with the shared `TrackDialog` and `ListsForm` for card tracking and list actions, matching Detail on Expo iOS, Android and web. The browser card adapter and other modal hosts remain; their retirement conditions are unchanged. Browser, iOS and Android runtime verification is pending independently.

Quick status uses the shared MediaCard and MediaCardActions, the browser card adapter and the Expo MediaCardSlot on Home, Library and Search. Existing fallback consumers and retirement conditions are unchanged. Manual untracked items and non-generic tracking retain their existing flow. Browser search and iOS library rendered; interaction verification and Android runtime verification remain pending separately.

- `frontend/ui/mediaCardQuickStatus.web.js` (browser quick-status transport for the media card)

`frontend/ui/mediaCardQuickStatus.web.js` owns browser status transport; Expo uses `native-proof/app/surfaces/mediaCardTransport.js`. Native long presses use system context menus and iOS system feedback or Android vibration; Expo web retains the shared dropdown because system menus are native-only.

`MediaCard` consumes long presses on its artwork and title, while `MediaCardSlot` supplies the native menu and its “Update status” submenu on Home, Library, Search and other slot consumers. The Expo web adapter uses a shared dropdown submenu. Existing fallback ownership and retirement conditions remain unchanged; browser, iOS and Android interaction verification is pending separately.

`native-proof/app/surfaces/NativeCardMenu.ios.jsx` renders the iOS context menu and its menu-style Picker; `NativeCardMenu.android.jsx` supplies the Android Material menu and haptic, while `NativeCardMenu.jsx` supplies the shared dropdown on Expo web. The Picker changes the native submenu control; its iOS runtime appearance is not yet verified.

Custom-list grid and table adapters supply podcast show status and score to the existing card and table components; native list detail receives those fields through the list API, and smart-list rules resolve show trackers by provider identity. Presentation ownership and remaining consumers are unchanged. Browser: before/after grid and table captures at desktop and phone widths. iOS and Android runtime verification is pending.

Shared MediaCard preserves the accepted status-specific icon shapes and colors, with provider icons as a fallback for unknown statuses. Native action hosts and progress renderers remain supplied by adapters. Ownership and remaining server consumers are unchanged; browser and native verification are recorded independently.
- Render cost: `frontend/ui/MediaCard.jsx` and `MediaCard.web.jsx` are memoized; `native-proof/app/surfaces/MediaCardSlot.jsx` (`SlotCard`, optional windowed `virtualized` mode); test `scripts/cardRenderCost.test.mjs` with `scripts/test-support/`.

Status: `Web Migrated`

PeopleScreen now consumes canonical credit card models with an opt-in hover metadata policy and 16px grid gap; runtime/count chips wait for reveal. Final browser Person check confirms the status icon and no runtime overlap; minor title truncation and toolbar spacing remain. Studio navigation, badge, count and layout checked. iOS and Android not verified.

- `src/templates/app/components/card_lines.html`
- `src/templates/app/components/media_card.html` (shell: wrapper classes, props script and first-paint poster and title; React replaces it on mount)
- `frontend/ui/MediaCard.jsx`, `frontend/ui/MediaCard.web.jsx`, `frontend/ui/mediaCardBehavior.js`, `frontend/ui/statusChipIcons.js`, `frontend/ui/mountMediaCard.jsx`, `frontend/ui/media-card.css`, `frontend/ui/CardScrim.jsx`, `frontend/ui/CardScrim.native.jsx`, `frontend/ui/CardScrim.web.jsx`, `src/app/media_card_model.py`, `src/app/card_surfaces.py` (React Native primitives on native surfaces; native poster action reveal follows server `actions.clickable`; native scrim uses an SVG gradient; the browser adapter retains DOM links, htmx integration and web-only slots; compact Up Next cards use the localized short label)
- `src/templates/app/components/media_card_list.html`
- `src/templates/app/components/media_card_status_chip.html` (out-of-band save fragment only; no card renders it)

## POPUP-HOST

Status: `Partial / coexisting`

- `frontend/ui/ModalHost.web.jsx`, `frontend/ui/mountModalHost.jsx` (shared popup host, mounted once from `base.html`; the card hover buttons and the Add-to-lists buttons open it through `floppy-open-modal`; the Track body is still the server form, see the register; unmounting a host disposes the React islands inside it, so the Track dialog is not stranded when Escape closes the popup mid-load)
- `frontend/ui/ListsFormView.jsx`
- `frontend/ui/modal-forms.css`

## RATING

Status: `Partial / coexisting`

- `frontend/ui/RatingStars.jsx`
- `frontend/ui/mountRatingStars.jsx`
- `src/templates/app/components/rating_stars.html`
- `src/templates/app/components/derived_tv_rating_coverage.html`
- `src/templates/app/components/derived_tv_rating_detail.html`
- `src/templates/app/components/detail_episode_rating_chip.html`
- `src/templates/app/components/detail_seasons_row.html`
- `src/templates/app/components/detail_row_rating_button.html`
- `src/templates/app/components/detail_score_and_actions.html`
- `src/templates/app/components/detail_score_chip.html`
- `src/templates/app/components/detail_score_chip_slot.html`
- `src/templates/app/components/detail_score_chips.html`
- `src/templates/app/components/media_card_rate_button.html`
- `src/templates/app/components/media_card_rating.html`
- `src/templates/app/components/media_card_rating_oob.html`
- `src/templates/app/components/rating_picker.html`

## LIBRARY

Status: `Partial / coexisting`
- Native windowing: `native-proof/app/surfaces/LibraryGrid.jsx` and `MediaCardSlot.jsx` render the grid as a `FlatList` with the page as header/footer from `frontend/pages/LibraryPageView.jsx` (`renderScroll`); web paging unchanged.

- `native-proof/app/screens/LibraryScreen.jsx`; its three toolbar filter dropdowns are the `FilterBar` toolbar variant on `DropdownMenu`/`DropdownItem`)
- `native-proof/app/surfaces/LibrarySettings.jsx`, `native-proof/app/surfaces/LibrarySettings.ios.jsx`, `native-proof/app/surfaces/LibrarySettingsGear.jsx` (the media list settings menu: header gear everywhere, a system Liquid Glass bottom-toolbar menu on the iOS side rail)
- `native-proof/app/surfaces/LibraryGrid.jsx`
- `native-proof/app/surfaces/LibraryBulkActions.jsx`
- `native-proof/app/data/libraryTransport.js`
- `native-proof/app/data/libraryTransportCore.js`
- `src/api/fork_views_library.py`
- `src/api/fork_views_lists.py`

- Native `LibraryPageView` passes optional `PageHeading` title/count styles for library typography and mobile badge padding; other page compositions preserve defaults. Shared presentation remains `Partial / coexisting`, `Primitives`; final browser spacing comparison and iOS/Android runtime verification remain outstanding.

- Shared/native ownership: `native-proof/app/screens/LibraryScreen.jsx`, `frontend/pages/LibraryPageView.jsx`, `native-proof/app/data/libraryTransportCore.js`; `Partial / coexisting`, `Primitives`. Groups use server card props and group-specific ordering; entry mode preserves distinct tracking keys and item-based selection. Web templates below remain until page composition, filters and pagination migrate. Browser: group overview/drilldown/return and separate-entry preference persistence exercised at 375x812; final visual parity incomplete. iOS and Android runtime not verified.

- `src/templates/app/components/_release_year_fetch_js.html`
- `src/templates/app/components/game_grouped_items.html`
- `src/templates/app/components/game_grouped_ungrouped.html`
- `src/templates/app/components/game_grouped_ungrouped_page.html`
- `src/templates/app/components/media_grid_items.html`
- `src/templates/app/components/media_settings_menu.html`
- `src/templates/app/media_list.html`

## ALPHABET

Status: `Web Migrated`

- `frontend/ui/AlphabetBar.jsx`
- `frontend/ui/mountAlphabet.jsx`
- `frontend/ui/alphabet.css`
- `src/templates/app/components/alphabet_filter.html`
- `src/templates/app/components/alphabet_results_empty.html`
- `frontend/ui/AlphabetBar.web.jsx`
- `frontend/ui/AlphabetBar.native.jsx`
## FILTERS

Status: `Partial / coexisting`

- `frontend/ui/FilterBar.jsx`
- `frontend/ui/SettingsMenu.jsx`
- `frontend/ui/keyboardOverlapContext.js`
- `frontend/ui/devicePose.js`, `frontend/settings/pose.web.js`, `native-proof/app/data/useDevicePose.js`, `native-proof/app/components/PoseBadge.jsx` (device poses; see `docs/architecture/device-poses.md`)
- `native-proof/app/screens/LibraryScreen.jsx`
- `native-proof/app/data/libraryTransportCore.js`

- `src/static/js/libraryFilterState.js`
- `src/static/js/savedViews.js`
- `src/templates/app/components/filter_form.html`
- `src/templates/app/components/filter_menu.html`
- `src/templates/app/components/filter_range_panel.html`
- `src/templates/app/components/filter_toolbar.html`
- `src/templates/app/components/filter_toolbar_scripts.html`
- `src/templates/app/components/sidebar_saved_views.html`

## TABLES

Status: `Partial / coexisting`

- `frontend/ui/MediaTable.jsx`
- `native-proof/app/screens/LibraryScreen.jsx`
- `native-proof/app/surfaces/TableSlot.jsx`

- Shared `frontend/ui/MediaTable.jsx` distinguishes item/entry React keys while keeping item-based bulk selection. Consumer: native `LibraryScreen` through `TableSlot`; `Partial / coexisting`, `Primitives`. Legacy server tables below retire with their page migrations. Browser duplicate-entry table comparison not yet verified; iOS and Android runtime not verified.

- `src/templates/app/components/cells/album_artist_cell.html`
- `src/templates/app/components/cells/album_date_added_cell.html`
- `src/templates/app/components/cells/album_image_cell.html`
- `src/templates/app/components/cells/album_release_date_cell.html`
- `src/templates/app/components/cells/album_score_cell.html`
- `src/templates/app/components/cells/album_status_cell.html`
- `src/templates/app/components/cells/album_title_cell.html`
- `src/templates/app/components/cells/artist_date_added_cell.html`
- `src/templates/app/components/cells/artist_end_date_cell.html`
- `src/templates/app/components/cells/artist_image_cell.html`
- `src/templates/app/components/cells/artist_name_cell.html`
- `src/templates/app/components/cells/artist_release_date_cell.html`
- `src/templates/app/components/cells/artist_score_cell.html`
- `src/templates/app/components/cells/artist_start_date_cell.html`
- `src/templates/app/components/cells/artist_status_cell.html`
- `src/templates/app/components/cells/critic_rating_cell.html`
- `src/templates/app/components/cells/episodes_left_cell.html`
- `src/templates/app/components/cells/last_watched_cell.html`
- `src/templates/app/components/cells/media_author_cell.html`
- `src/templates/app/components/cells/media_date_added_cell.html`
- `src/templates/app/components/cells/media_end_date_cell.html`
- `src/templates/app/components/cells/media_entry_source_cell.html`
- `src/templates/app/components/cells/media_genres_cell.html`
- `src/templates/app/components/cells/media_image_cell.html`
- `src/templates/app/components/cells/media_media_type_cell.html`
- `src/templates/app/components/cells/media_notes_cell.html`
- `src/templates/app/components/cells/media_release_date_cell.html`
- `src/templates/app/components/cells/media_score_cell.html`
- `src/templates/app/components/cells/media_start_date_cell.html`
- `src/templates/app/components/cells/media_status_cell.html`
- `src/templates/app/components/cells/media_synopsis_cell.html`
- `src/templates/app/components/cells/media_tags_cell.html`
- `src/templates/app/components/cells/media_title_cell.html`
- `src/templates/app/components/cells/next_episode_air_date_cell.html`
- `src/templates/app/components/cells/platform_cell.html`
- `src/templates/app/components/cells/popularity_cell.html`
- `src/templates/app/components/cells/progress_cell.html`
- `src/templates/app/components/cells/runtime_cell.html`
- `src/templates/app/components/cells/time_left_cell.html`
- `src/templates/app/components/cells/time_to_beat_cell.html`
- `src/templates/app/components/cells/time_watched_cell.html`
- `src/templates/app/components/cells/user_rating_cell.html`
- `src/templates/app/components/table_column_config_scripts.html`
- `src/templates/app/components/table_column_menu.html`
- `src/templates/app/components/table_items.html`

## BULK

Status: `Partial / coexisting`

- `frontend/ui/BulkActions.jsx`
- `native-proof/app/surfaces/LibraryBulkActions.jsx`
- `native-proof/app/data/libraryTransportCore.js`

- `src/static/js/bulkSelection.js`
- `src/templates/app/components/bulk_selection_toolbar.html`
- `src/templates/lists/components/bulk_select_button.html`
- `frontend/ui/mountBulkToolbar.jsx`
- `frontend/ui/mountBulkActions.jsx` (mounted once from `base.html` for signed-in users; the job dock)
- `src/app/bulk_action_views.py`, `src/app/services/bulk_show_plays.py` (endpoints and the Add Plays job; the shared `ActionMenu` lives in `frontend/ui/controls`)

## SEARCH

`native-proof/app/surfaces/SearchSlot.jsx` → shared `GlobalSearch`; `native-proof/app/screens/SearchScreen.jsx` → shared `SearchPageView`; `native-proof/app/surfaces/SearchResultsSlot.jsx` → shared `SearchResults` → `HomeShelves` → `MediaCard`. Expo `SearchSlot` supplies the reviewed media-type menu, barcode scopes and copy to shared `GlobalSearch`; keyboard overlap, authenticated suggestions and scanner behavior remain in native adapters. `SearchScreen` adds no extra top inset to results. `SearchResultsSlot` supplies MediaCardSlot to each shared shelf card for in-place actions; token results, streaming provider rows, music routing and manual-entry navigation remain. Shared presentation remains React-owned with the existing coexisting web fallback and retirement conditions. Native shelf actions use the MediaCardSlot host; unsupported media retain their existing detail flow.

GlobalSearch and SearchResults retain the accepted menu/results geometry and theme accent. Browser keyboard overlap stays in keyboardOverlap.web.js; the native adapter supplies its own overlap hook and WindowedRail. Token results, manual-entry navigation and streaming provider rows remain. Existing ownership and fallback retirement conditions are unchanged; native runtime remains separately verified.

Suggestion requests (2026-11-16): `GlobalSearch` cancels the superseded request with an `AbortController` and passes `signal` to the platform loader; web loads through `frontend/ui/webRequest.web.js`, Expo through `searchTransportCore` and `authTransportCore` `send`. Both send a per-box `sid` (made in `GlobalSearch.web.jsx`, and per transport by `searchBoxId` in `searchTransportCore`) that `search_views.suggestion_superseded_check` uses to stop older All-media requests early.

ParentPicker's native manual-entry consumer opts into an unframed search field. The web create-entry template places each island's configuration within the host parent required by the loader, and the adapter explicitly imports shared presentation rather than resolving itself; its fallback remains. The mounted web picker retains its plain input, overlaid results and original result IDs. The TV manual-entry browser acceptance test covers parent search and selection; both web roots mounted and Expo also selected Deep Field. Ownership remains partial/coexisting and retirement still requires complete parity and web adoption. iOS and Android not verified.

- `src/templates/app/icons/magnifying-glass.svg` (connected SVG shared by GlobalSearch, CollectionBrowser, ProviderDirectoryView and remaining server-template search controls; shared SearchIcon is mapped under SHELL)

Status: `Partial / coexisting`

- `native-proof/app/surfaces/SearchSlot.jsx` (Expo search transport, scanner and iOS focus presentation; suggestion identity and destination are retained when opening detail; the shared `GlobalSearch` is mapped under SHELL)
- `src/app/search_views.py` (`_all_scope_barcode_groups`: All-scope ISBN fan-out and saved Barcode matches)
- `src/static/js/barcode-scanner.js`
- `src/static/js/barcode-scanner.selfcheck.mjs`
- `src/static/js/libraries/zxing-library-0.21.3.min.js`
- `src/static/js/searchAutocomplete.js`
- `src/static/js/searchShortcut.js`
- `src/templates/app/components/search_local_album_cards.html`
- `src/templates/app/components/search_parent_season.html`
- `src/templates/app/components/search_parent_tv.html`
- `src/templates/app/components/search_suggestions.html`
- `src/templates/app/search.html`
- `src/templates/app/components/results_heading.html`

## RAILS

`Rail` reduces the bottom space below cards by 4px for its Home, Search, detail
and other consumers on web and Expo; consecutive detail rails use Home's 12px
section gap. Existing partial/coexisting ownership and
server fallback retirement conditions are unchanged; Android runtime is unverified.

Rail retains optional per-item width and card windowing APIs. Native Home/Search use WindowedRail to mount nearby cards; web retains its established DOM/image lazy-loading behavior. This native-specific mount policy is documented beside WindowedRail and is not a visual redesign. Existing ownership, web consumers and retirement conditions remain.

Status: `Legacy`

- `src/static/js/horizontal-scroll.js`
- `src/static/js/horizontal-scroll.selfcheck.mjs`
- `src/templates/app/components/_scrollable_row.html`

## HOME

`native-proof/app/screens/HomeScreen.jsx` → shared `HomeView` → `HomeShelves` → default shared `MediaCard`; native adapter retains `WindowedRail`, row-title destinations and authenticated Home transport. The Expo Home adapter uses the default shared `HomeShelves` / `MediaCard` presentation, without an extra Library grid host, top inset, per-card progress row or floating settings button. Home row title navigation, bounded row loading, sorting, artwork refresh, signed-in transport and windowed native rails remain. Deliberate platform difference: the owner-approved native shelf opens Detail from card actions; the web adapter retains its existing in-place actions and quick progress controls. Page ownership stays partial/coexisting; server fallbacks and retirement conditions are unchanged.
- Render cost: `frontend/ui/HomeShelves.jsx` (`ShelfCard`, stable labels/palette/loadMore).

Web Home retains its existing quick progress actions. Expo uses the reviewed shared shelf-card composition described above.

HomeView forwards optional card/progress renderers and row-title callbacks; the web supplies its progress renderer and Expo uses the default reviewed cards. HomeShelves retains the accepted common card widths and the three-request FIFO fetch gate on web and Expo. Mixed/video rows do not adopt a separate widening policy. Django placeholder fallback suppression and single-flight row builds remain; native has no HTMX fallback. Existing ownership and retirement conditions remain. Final integration browser and native evidence is recorded separately.

Status: `Partial / coexisting`

- `src/templates/app/components/home_artwork_poller.html`
- `src/templates/app/components/home_deferred_groups.html`
- `src/templates/app/components/home_grid.html`
- `src/templates/app/components/home_groups.html`
- `src/templates/app/home.html`
- Web parity pass against v26.10.29: `frontend/pages/HomeView.jsx` forwards `renderCard` and `renderProgress`; `frontend/ui/HomeShelves.jsx` keeps the accepted common shelf width; `frontend/ui/Rail.jsx` retains optional per-item sizing for other consumers.
- Home row title links (#1899): `frontend/ui/HomeShelves.jsx` (`onOpenRowTitle`, `canOpenRowTitle`), `frontend/ui/mountHome.jsx`, `native-proof/app/screens/HomeScreen.jsx`, `native-proof/app/screens/LibraryScreen.jsx` (`filtersFromQuery`, `params.query`) and `native-proof/app/data/homeRowDestinationCore.js`.

- `frontend/ui/fetchGate.js` (shared Home row request concurrency).
- `frontend/ui/homeRowLoading.js` (shared Home lazy-row decision: which waiting rows are visible, near or far, and in what order they start).
- `frontend/ui/homeRowWatcher.web.js` (browser adapter: IntersectionObserver row positions for lazy Home rows; the look-ahead is a pixel margin from the viewport height, rebuilt on resize, because a percentage margin resolves against width).
- `native-proof/app/data/homeRowWatcherCore.js` (Expo adapter: measures waiting Home rows on scroll and layout for lazy loading).
- `frontend/ui/homeTransportCore.js` (shared Home endpoints: row windows, sort, cover refresh and row-page merge, over an injected request; used by `mountHome.jsx` and, through the `native-proof/app/data/homeTransportCore.js` re-export, by Expo `homeTransport.js`).
- `frontend/ui/webRequest.web.js` (web-only request for adapters that drive a shared transport core: cookie session, CSRF on unsafe methods, in-flight sharing of identical GETs, optional AbortSignal and timing hook).

## PLAYBACK

Status: `Partial / coexisting`

- `frontend/ui/PlaybackCard.jsx`, `frontend/ui/PlaybackTheme.web.jsx`, `frontend/ui/mountPlaybackCard.jsx` (shared presentation, browser adapter and server-fragment fallback; Now Playing renders a 16:9 MediaCard with its section heading and controls above it, using the standard MediaCard indigo for both playing and paused/stale progress fills)
- `native-proof/app/surfaces/PlaybackSlot.jsx`, `native-proof/app/data/playbackTransport.js`, `native-proof/app/data/settingsTransport.js`, `frontend/ui/mediaCardSizing.js` (native Now Playing follows the server's web detail destination; cards span up to three 110 pt cards plus two 16 pt gaps on compact mobile, two 168 pt cards plus a 12 pt gap on comfortable mobile, and two on desktop; narrow layouts shrink to fit)
- `src/api/fork_views_playback.py` (`/api/v1/playback/home-cards/`, session-only cards and server-resolved Pause/Stop or Stop/Cancel actions)
- `src/templates/app/components/_scrollable_row_js.html` (browser playback width measures an ordinary Home media-grid card and spans three on compact mobile, two on comfortable mobile, or two on desktop)
- `src/templates/app/components/active_playback_card.html`
- `src/templates/app/components/detail_checkin_state.html`
- `src/templates/app/components/active_playback_cards.html`

## DISCOVER

Status: `Legacy`

- `src/templates/app/components/discover_row.html`
- `src/templates/app/components/discover_row_preview.html`
- `src/templates/app/components/discover_rows.html`
- `src/templates/app/components/discover_tab_content.html`
- `src/templates/app/discover.html`

## DETAIL

Media Details uses shared `DetailSecondary`, `DetailMetadata`, `DetailWatchProviders`,
`DetailStudios`, `DetailPromotedFacts` and `DetailMediumExtras` for Expo's saved
sidebar/content layout. `DetailView` reuses `Rail`, `PersonCard`, `MediaCard` and
`DetailRelated` for people, recommendations and related grids. Expo detail cast,
crew, seasons and recommendation rails use Home's responsive card width and windowed
rail, with `MediaCard` for people as well as media. Web detail cast and crew use
the web media card; seasons and recommendations use Home's mounted rail and media
cards. Ownership remains partial/coexisting until the web detail shell migrates.
The Duo PWA and Expo Go rendered the changed rows; Android runtime is unverified.
Detail season card indicators are projected by the detail API using the Home card
rules; the web season cards use the Home card surface. Detail recommendations use
the Home card surface without the alternative-title control. The web rail owns
the item width once mounted, so its embedded cards fill the rail item.
Episode cast and crew also mount the shared people rail over a server fallback;
the episode template remains partially owned until its remaining sections migrate.
The Expo detail adapter omits its Notes slot when there are no notes, avoiding an
empty section gap. Browser and Android verification for that state is separate.
`DetailScoreRow` places the viewer rating beside provider scores; `ProgressCard` has an opt-in
detail presentation. Native metadata adapters preserve server formatting and
viewer rating preferences. `DetailBackdrop` and its layout logic anchor sharp
artwork and clearlogos to the inner content strip. Intentional native carve-out:
artwork extends underneath the search bar and into the side rail; the PWA keeps
its search divider. Search overlay and overflow are enabled only for Details.
Expo fades the sharp image in from halfway through the search overlay while retaining the blur
behind it; the PWA has a separate header and needs no equivalent offset.
For mobile clearlogos, `DetailBackdrop` reports the fade endpoint to the shared
hero or browser adapter, placing the logo bottom and subtitle top equally around that line.
Desktop remains on its separate carousel layout.
`native-proof/app/data/prepareDetailArtwork.mjs`, `native-proof/app/data/loadDetailHeader.mjs`, `native-proof/app/screens/DetailScreen.jsx`
and `native-proof/app/surfaces/DetailFeatures.jsx` start native header requests
alongside detail, prepare gallery artwork as soon as it arrives, and coordinate the initial reveal; the web hero adapter waits for its images and keeps a bounded
fallback. The PWA's cold gallery request starts immediately. The server detail
shell and deferred secondary sections remain partial/coexisting.
`ProviderLogo` renders native SVG sources in its native adapter. Shared components
remain props-only; permissions, requests and dialogs stay in feature adapters.
PWA secondary templates remain server-owned; ownership is partial/coexisting.
Retire those consumers only after shared web mounting preserves all section
preferences, responsive states and interactions. Genre/link modal contents remain
deferred. Current backdrop revision evidence: web — runtime, mobile PWA on iPhone
Duo compared before and after; desktop comparison pending. iOS — runtime, Expo on
iPhone Duo compared before and after. Android — unverified; runtime comparison
pending. These checks do not establish complete page parity.

- `native-proof/app/surfaces/detailActions.mjs`, `native-proof/app/surfaces/detailLoads.mjs` (Expo detail action state: the result of a rating or episode watched shows before the server answers, failed writes are put back, and only the changed parts reload; album, track and podcast show payloads load in parallel). Consumed by `native-proof/app/screens/DetailScreen.jsx`, `native-proof/app/surfaces/DetailFeatures.jsx` and `native-proof/app/surfaces/DetailSlot.jsx`; no shared presentation changed. Web Detail keeps its server fragments, which already update in place from the write response. Evidence: web runtime; iOS runtime (simulator); Android runtime (emulator, rating only).
- `frontend/ui/DetailBackdrop.jsx`, `frontend/ui/DetailBackdrop.native.jsx`, `frontend/ui/DetailBackdrop.web.jsx`, `frontend/ui/detailHeroLayout.js` (inner-area hero geometry and platform image gradients).
- `frontend/ui/DetailActivitySubtitle.jsx`, `frontend/ui/mountDetailActivitySubtitle.jsx`, `src/templates/app/components/detail_activity_subtitle.html` (shared activity subtitle, browser history-button adapter and server fallback); Expo receives formatted text from `native-proof/app/data/detailTransportCore.js`. The subtitle is shared React-owned; the surrounding detail page remains partial/coexisting.
- `frontend/ui/mountDetailBackdrop.jsx` mounts the shared mobile backdrop in the PWA; the server image remains its loading and no-JavaScript fallback. The surrounding hero and desktop carousel remain server-owned, so ownership is partial/coexisting.
- `frontend/ui/ProviderLogo.jsx`, `frontend/ui/ProviderLogo.native.jsx`, `frontend/ui/ProviderLogo.web.jsx` (provider logo presentation).
- `frontend/ui/DetailSecondary.jsx`, `frontend/ui/detailSecondaryModel.js` (ordered metadata and secondary-section composition).
- `scripts/detailHeroLayout.test.mjs`, `scripts/detailSecondaryModel.test.mjs` (geometry and safe projection regression checks).

Status: `Partial / coexisting` — Expo uses the shared detail composition; the browser page remains server-owned.

- Shared `DetailView` slots (`actionsSlot`, `scoreSlot`, `sectionsSlot`, `childrenSlot`, `onOpenPerson`) with `DetailActionBar`, `DetailScoreRow`, `DetailCollectionBlock`, `DetailNotesBlock`, `DetailRelatedGrid`, `DetailIssueCards`, `DetailEpisodeCards` and `DetailEpisodeHeader`: `Partial / coexisting`, `Primitives`; native `DetailScreen` owns the page payload and navigation, and `surfaces/DetailFeatures.jsx` owns the track dialog and check-in, tags, notes, lists, activity history, progress card, collection copies, cover and edition pickers, Seerr and metadata sync through `detailTransport.js`/`mediaCardTransport.js`. Web detail and episode shells below remain until their composition and interactions migrate. Browser: compared with the web pages at 375px through the Expo web build (film, show, season, episode, anime, comic, book); one check-in, note, list membership, tag and track save were written and undone. iOS and Android runtime not verified.

- `src/templates/app/components/detail_action_buttons.html`
- `src/templates/app/components/detail_activity_subtitle_slot.html`
- `src/templates/app/components/detail_mobile_meta.html`
- `src/templates/app/components/detail_notes_section.html`
- `src/templates/app/components/detail_promoted_content.html`
- `src/templates/app/components/detail_synopsis.html`
- `src/templates/app/components/detail_title_block.html`
- `src/templates/app/components/detail_watch_providers.html`
- `src/templates/app/components/series_graph_tooltip.html`
- `src/templates/app/components/trakt_series_graph_fragment.html`
- `src/templates/app/components/video_details_extra.html`
- `src/templates/app/media_details.html`
- `native-proof/app/screens/DetailScreen.jsx`
- `native-proof/app/surfaces/DetailSlot.jsx`
- `native-proof/app/surfaces/DetailFeatures.jsx`
- `native-proof/app/data/detailTransport.js`
- `native-proof/app/surfaces/detailIdentity.mjs`
- `src/api/fork_views_detail.py`
- `src/api/fork_urls.py`
- `frontend/pages/DetailView.jsx`
- `frontend/ui/DetailComposition.jsx`
- `frontend/ui/DetailPage.jsx` (action bar, score row, collection, notes, related grid, issue and episode cards; mounted by `native-proof/app/surfaces/DetailFeatures.jsx`)
- `frontend/ui/DetailEpisode.jsx` (episode title line and picker; mounted by `native-proof/app/surfaces/DetailFeatures.jsx`)

## CHILDREN

Status: `Partial / coexisting` — browser rows remain server-owned; Expo renders the shared child composition.

- `DetailChildrenTheme.web.jsx` bridges shared `DetailChildren` Track actions to the shared web modal host using a target outside hidden fallback rows. Native `DetailEpisodeCards`/`DetailIssueCards` adapt tracking/collection/history to JSON APIs with provider identity intact. Shared presentation: `Partial / coexisting`, `Primitives`; server row/form fallbacks remain until full interaction ownership migrates. Browser web Track bridge and native issue Add verified; new rows/history removed against baseline. Final native anime controls and restored comic fields pending; iOS and Android runtime not verified.

- `src/templates/app/comic_issue_details.html`
- `src/templates/app/components/_episode_scroll_restore.html`
- `src/templates/app/components/detail_episode_collection_button.html`
- `src/templates/app/components/detail_episode_hero_track_button.html`
- `src/templates/app/components/detail_episode_history_line.html`
- `src/templates/app/components/detail_episode_track_button.html`
- `src/templates/app/components/detail_row_actions.html`
- `src/templates/app/components/episode_card_rating.html`
- `src/templates/app/components/episode_card_title.html`
- `src/templates/app/components/episode_row.html`
- `src/templates/app/components/issue_row.html`
- `src/templates/app/components/seasons_grid.html`
- `src/templates/app/episode_details.html`
- `frontend/ui/DetailChildren.jsx`

## TRACKING

TrackForm keeps the accepted tabbed dialog and field composition. Date shortcuts retain runtime pairing, and mountTrackForm submits the normalized payload and watches episode popup state. SelectField retains its original filter chevron with additive style props for other consumers. Existing ownership and retirement conditions remain; browser interaction coverage and native runtime are distinct checks.

Song and podcast episode consumers in `fill_track_song.html` mount TrackDialog through `mountTrackForm.jsx`. The adapter hides the legacy shell and moves its preview and history into TrackForm while mounted; the legacy shell remains the web fallback. Native tracking uses shared form surfaces without this web adapter. Retire the fallback after preview and history actions have shared presentation. Browser: podcast open, close, save and fallback tested in Chromium. iOS: runtime unverified. Android: runtime unverified.

Shared `ManualEntryView` is consumed by native `SearchScreen` create-entry mode and the shell /create route. Search transport supplies structured options, owner-scoped parents and web-form validation. Browser manual-entry/remaining popup consumers stay server-owned until parity and web adoption. Expo searched and selected Deep Field, created season 99, and the test entry was removed and verified absent; final layout comparison pending. iOS/Android not verified, Primitives.

- `frontend/pages/ManualEntryView.jsx`

Status: `Partial / coexisting`

- `src/static/js/episodeBulkTrackForm.js`
- `src/static/js/mediaStatusDateHandler.js`
- `src/templates/app/components/album_track_modal.html`
- `src/templates/app/components/artist_track_modal.html`
- `src/templates/app/components/detail_track_action.html`
- `src/templates/app/components/detail_track_action_attrs.html`
- `src/templates/app/components/detail_track_open_attrs.html`
- `src/templates/app/components/detail_track_split.html`
- `src/templates/app/components/fill_track.html`
- `src/templates/app/components/fill_track_episode.html`
- `src/templates/app/components/fill_track_episode_bulk.html`
- `src/templates/app/components/fill_track_song.html` (album rows pass `play_count`, so the hidden fallback reads no per-track history)
- `src/templates/app/components/fill_track_song_listen_count.html`
- `src/templates/app/components/media_metadata_panel.html`
- `src/templates/app/components/podcast_show_track_modal.html`
- `src/templates/app/create_entry.html`

## DATE-TIME

Status: `Partial / coexisting`

- Activity history calendar is `Partial / coexisting`: `frontend/ui/DateTimePicker.jsx` and `mountDateTimePicker.jsx` own the web detail calendar, with `sessionHistory.js` bridging the server-owned list; `HistoryFeed.jsx` supplies `HistoryActivityCalendar` to native `DetailFeatures.jsx`, and native `HistoryScreen.jsx` uses the same picker. Remaining legacy field-picker consumers retire with their own migration. Browser: seeded detail dialog inspected and selection and month navigation exercised. iOS and Android: Expo Go intensity fixtures inspected; complete native dialog integration remains unverified.
- Shared `DateTimePicker` activity markers and selected-day background use the same spacing on web, iOS and Android. Browser, iOS and Android fixture comparisons cover the spacing adjustment; the consumers and remaining legacy ownership are unchanged.

- `src/static/js/calendar.js`
- `src/static/js/dateTimePicker.js`
- `src/templates/app/components/calendar.html`
- `src/templates/app/components/date_time_picker.html`
- `src/templates/app/components/date_time_picker_props.html`

## NOTES

Status: `Legacy`

- `src/static/js/libraries/marked-12.0.2.min.js`
- `src/templates/app/components/detail_notes_block.html`
- `src/templates/app/components/markdown_notes.html`
- `src/templates/app/components/music_track_notes.html`
- `src/templates/app/components/music_track_notes_button.html`

## PROGRESS

Status: `Legacy`

- `src/templates/app/components/detail_progress_card_slot.html`
- `src/templates/app/components/progress_breakdown.html`
- `src/templates/app/components/progress_changer.html`

## OWNERSHIP

Status: `Legacy`

Native CollectionFieldManager consumes all server media and field choices; CollectionBrowser can opt into its existing media-type rows from the sort sheet. Both expose checked state, and the native manager opens as a separate modal while web expands it inline. Web editor and Collection browser remain server-owned. Browser custom-field sorting exercised on both, grouped-row and accessibility recheck pending; iOS and Android not verified.

- `src/templates/app/collection_list.html`
- `src/templates/app/components/collection_entry_summary.html`
- `src/templates/app/components/collection_form_fields.html`
- `src/templates/app/components/collection_grid_rows.html`
- `src/templates/app/components/collection_items.html`
- `src/templates/app/components/collection_modal.html`
- `src/templates/app/components/collection_row_grid.html`
- `src/templates/app/components/collection_rows.html`
- `src/templates/app/components/collection_table_rows.html`

## CUSTOM-FIELDS

Status: `Legacy`

- `src/static/js/collectionCustomFields.js`
- `src/templates/app/components/collection_custom_field_inputs.html`
- `src/templates/app/components/collection_custom_fields.html`

## ARTWORK

Status: `Partial / coexisting`

- `src/templates/app/components/cover_change_section.html`
- `src/templates/app/components/edition_picker_result.html`
- `src/templates/app/components/edition_picker_results.html`
- `src/templates/app/components/hardcover_edition_results.html`
- `src/templates/app/components/image_picker.html`
- `src/templates/app/components/image_picker_tiles.html`

Shared ArtworkPicker and EditionPicker presentation is consumed by the browser tracking/detail mounts and native DetailFeatures. Browser adapters explicitly import the shared module to prevent self-resolution. Server fallback fragments remain until all entry points retain equivalent behavior and permissions. Browser resolution tests pass; picker actions on iOS and Android remain unverified.

- `frontend/ui/ArtworkPicker.jsx`
- `frontend/ui/ArtworkPicker.web.jsx`
- `frontend/ui/mountArtworkPicker.jsx`
- `frontend/ui/mountEditionPicker.jsx`

## MUSIC

Status: `Partial / coexisting`

The artist relation and album grids are React islands over server-rendered fallbacks (`MusicRelationGrid`, `MusicAlbumGrid`, `mountMusicRelations`, `mountMusicAlbums`), also consumed by native `ArtistPage`. Their hosts leave layout to the mounted grid or the fallback, and native artist loading joins relation data before presenting it. Chromium artist-grid rendering was checked at desktop and phone sizes; a fixture preview of the grids ran in Expo Go on iOS. The full iOS artist flow and Android rendering remain unverified. Album/track details, podcast pages and episode rows remain server-owned or Alpine; retire each remaining group when its component migration preserves the existing fallback and behavior. Native `MusicScreen` reuses shared presentation without an empty header/Back row and sets the shell Music/Podcasts context. Browser track-title navigation is verified; notes/date formatting and broader layout remain Partial. Native readiness is `Primitives`.

- `src/templates/app/components/album_grid.html`
- `src/templates/app/components/album_list_grid_items.html`
- `src/templates/app/components/artist_discography_container.html`
- `src/templates/app/components/artist_discography_groups.html`
- `src/templates/app/components/artist_grid_items.html`
- `src/templates/app/components/artist_relation_grid.html`
- `src/templates/app/components/artist_relations_container.html`
- `src/templates/app/components/detail_music_album.html` (renders the first 50 track rows; totals cover every track)
- `src/templates/app/components/detail_music_album_track_rows.html` (one page of album track rows plus the scroll trigger that loads the next page from `music_album_track_rows`; links in later pages return to the album page)
- `src/templates/app/components/music_repair_poll.html` (hidden poller on an artist, album or track page while its background repair runs; reloads the page when `music_repair_status` reports it finished, or follows a merge to the surviving album or artist)
- `src/templates/app/components/detail_music_album_activity_subtitle.html`
- `src/templates/app/components/detail_music_artist.html`
- `src/templates/app/components/detail_music_track.html`
- `src/templates/app/components/detail_music_track_actions.html`
- `src/templates/app/components/detail_music_track_button.html`
- `src/templates/app/components/detail_music_track_history_line.html`
- `src/templates/app/components/podcast_episode_list.html` (optional episode description and empty state)
- `src/templates/app/music_album_detail.html`
- `frontend/pages/MusicView.jsx`
- `frontend/ui/MusicAlbumCard.jsx`
- `frontend/ui/MusicParts.jsx`
- `frontend/ui/MusicRelationCard.jsx`
- `frontend/ui/mountMusicAlbums.jsx`
- `frontend/ui/mountMusicRelations.jsx`

## PODCAST-EPISODES

Status: `Partial / coexisting`

- `frontend/ui/PodcastEpisodeManager.jsx`
- `frontend/ui/mountPodcastEpisodeManager.jsx`
- `src/templates/app/podcast_episodes_manage.html`

## PEOPLE

Status: `Legacy`

PeopleView's native full-page grid uses canonical MediaCard models and a render-grid slot; web filmography stays server-owned until shared adoption. Pre-fix browser comparison captured person and credits; post-fix verification pending. iOS and Android not verified.

- `src/templates/app/components/detail_carousel_fragment.html`
- `src/templates/app/components/detail_cast_grid.html` (iterates `people_cast_cards` supplied by `{% people_cast %}`)
- `src/templates/app/components/game_lengths_section.html`
- `src/templates/app/components/person_card_inline.html` (pre-mount fallback for the cast/crew `PeopleView` rail on media detail and episode pages; links come from `_people_cast_props`, #1953)
- `src/templates/app/components/person_filmography_card.html`
- `src/templates/app/components/person_filmography_fragment.html`
- `src/templates/app/components/person_hero.html` (person hero island and its fallback, shared by the page and the talent fill-in)
- `src/templates/app/components/person_talent_poller.html` (asks every 3 s while play totals build)
- `src/templates/app/components/person_talent_ready.html` (out-of-band hero and credits once totals are built)
- `src/templates/app/components/person_media_strip_card.html`
- `src/templates/app/components/person_media_strip_row.html`
- `src/templates/app/components/youtube_player.html`
- `src/templates/app/components/youtube_watch_section.html`
- `src/templates/app/person_detail.html`
- `src/templates/app/studio_detail.html`
- `frontend/ui/PeopleToolbar.jsx`

## TAGS

Status: `Legacy`

- `src/templates/app/components/detail_tag_preview.html`
- `src/templates/app/components/fill_tags.html`
- `src/templates/app/components/tag_item_button.html`
- `src/templates/app/tag_index.html`
- `src/templates/users/components/media_tag_filter.html`

## METADATA-REVIEW

Status: `Legacy`

- `src/static/js/episodeOrderReview.js`
- `src/templates/app/components/library_move_results.html`
- `src/templates/app/components/remap_search_results.html`
- `src/templates/app/episode_ordering.html`
- `src/templates/app/tv_provider_switch.html`

## DETAIL-RELATED

Status: `Partial / coexisting` — Expo uses shared related cards; browser section chrome and Steam DLC cards remain server-owned.

- `src/templates/app/components/detail_secondary_content.html` (related and DLC sections, the Book "Format" and "Narrator" facts, and the manual podcast manager trigger)
- `frontend/ui/DetailRelated.jsx`

## HISTORY

Status: `Legacy`

- `src/static/js/sessionHistory.js`
- `src/templates/app/components/fill_history.html`
- `src/templates/app/components/history_card.html`
- `src/templates/app/components/history_day.html`
- `src/templates/app/components/session_history_day.html`
- `src/templates/app/components/session_history_modal.html`
- `src/templates/app/components/session_history_row.html` (also the audiobook "stopped at" and book "at page" fragments)
- `src/templates/app/history.html`
- `native-proof/app/screens/HistoryScreen.jsx`, `native-proof/app/data/historyTransportCore.js` (+ `historyTransportCore.test.mjs`) (native History screen and transport; release-date mode pages by day through `limit`/`offset` on `/api/v1/history/release/`, like the activity feed)
- `frontend/ui/historyScrollAnchor.web.js` (browser adapter: Back or reload returns History to the day the reader left; loads the waiting days down to it first).
- `frontend/ui/historyFirstView.js` (shared first-view rule: about two screens of entries, whole days; `scripts/historyFirstView.test.mjs`). The server mirrors it in `src/app/history_views.py` (`HISTORY_FIRST_VIEW_ENTRIES`, `_first_view_day_count`). Waiting days load as they come near the screen through `HistoryFeed.web.jsx`'s own observer (1.5-screen look-ahead from `homeRowLoading.js`) on web and the Home watcher `native-proof/app/data/homeRowWatcherCore.js` in `HistoryScreen.jsx` on Expo. `frontend/ui/historyScrollAnchor.web.js` (browser only) returns Back to the day the reader left.

## STATISTICS

- `StatisticsMediaTypeOverview` is shared by web overview islands and the native Statistics screen; its stacked list panels constrain their width and truncate long titles. Server overview fragments remain fallbacks. Browser phone and desktop layouts and an iOS Expo Go preview were inspected; Android capture did not respond.

Status: `Partial / coexisting` — shared React islands and native presentation coexist with the server sources below.

- `src/static/js/date-range.js`
- `src/static/js/libraries/chartjs-4.4.9.min.js`
- `src/static/js/libraries/chartjs-datalabels-2.0.0.min.js`
- `src/static/js/statistics-charts.js`
- `src/static/js/stats-media-merge.js`
- `src/static/js/stats-media-merge.selfcheck.mjs`
- `src/templates/app/components/featured_repeat_player.html`
- `src/templates/app/components/featured_repeat_player_content.html`
- `src/templates/app/components/featured_repeat_player_footer.html`
- `src/templates/app/components/role_leaders.html`
- `src/templates/app/components/role_leaders_grid.html`
- `src/templates/app/components/statistics/anime_activity_overview.html`
- `src/templates/app/components/statistics/game_activity_overview.html`
- `src/templates/app/components/statistics/highlight_set.html`
- `src/templates/app/components/statistics/movie_activity_overview.html`
- `src/templates/app/components/statistics/music_activity_overview.html`
- `src/templates/app/components/statistics/podcast_activity_overview.html`
- `src/templates/app/components/statistics/talent_section.html`
- `src/templates/app/components/statistics/tv_activity_overview.html`
- `src/templates/app/components/statistics/video_activity_overview.html`
- `src/templates/app/components/studio_footprint.html`
- `src/templates/app/components/studio_footprint_grid.html`
- `src/templates/app/components/taste_signals.html`
- `src/templates/app/components/taste_signals_decades.html`
- `src/templates/app/components/taste_signals_genres.html`
- `src/templates/app/statistics.html`
- `src/templates/app/components/reading_consumption_section.html` (Books "Listening Time" card)
- Web parity pass against v26.10.29: `frontend/ui/StatisticsSummary.jsx` caps the summary card text block at the card width.

## RELEASE-CALENDAR

Shared CalendarLinks accepts a session-required state from CalendarScreen. Calendar subscription URLs contain the permanent account token and require password-session sign-in; bearer calendar browsing remains. Defaults preserve browser/session links. Existing ownership and fallback retirement conditions remain; transport checks cover denial handling, while browser appearance and actual iOS/Android runtime for this state are not verified.

- `native-proof/app/surfaces/CalendarSlot.jsx`: passes `surfaceMutedSoft` to shared `ReleaseCalendar`; current 375x812 browser pair verifies blank-day fill, with subtitle wrapping still Partial. iOS/Android not verified, Primitives; browser ownership unchanged.

Status: `Partial / coexisting`

- `src/templates/events/calendar.html`
- `src/templates/events/components/calendar_grid.html` (pre-paint agenda: selected day's panel only, SURFACE-16)
- `src/templates/events/components/calendar_list.html`
- `frontend/ui/mountCalendarLinks.jsx`

## LISTS

Status: `Legacy`

Native ListsPageView/ListCard hide metadata through an opt-in prop, and ListDetailScreen offers desktop selection and its existing bulk bar in tier mode. The native import dialog consumes MdblistConnect with server-provided scheduling choices; Sidebar consumes per-server/user pins. Server list, import and tier consumers remain until shared adoption. Browser recheck pending; iOS and Android not verified.

- `frontend/ui/MdblistConnect.jsx`
- `native-proof/app/components/ListImportDialog.jsx`
- `native-proof/app/data/sidebarListPins.js`
- `native-proof/app/data/sidebarListPins.web.js`
- `native-proof/app/data/sidebarListPinsCore.js`

- `src/templates/lists/components/fill_lists.html`
- `src/templates/lists/components/list_cover_image.html`
- `src/templates/lists/components/list_form.html`
- `src/templates/lists/components/list_grid.html`
- `src/templates/lists/components/list_item_button.html`
- `src/templates/lists/components/list_results_summary.html`
- `src/templates/lists/components/list_table.html`
- `src/templates/lists/components/list_table_rows.html`
- `src/templates/lists/components/list_toggle_refresh_handler.html`
- `src/templates/lists/components/list_visibility_chips.html`
- `src/templates/lists/components/media_grid.html`
- `src/templates/lists/components/media_type_breakdown_button.html`
- `src/templates/lists/components/sidebar_pin_action.html`
- `src/templates/lists/custom_lists.html`
- `src/templates/lists/list_activity.html`
- `src/templates/lists/list_detail.html`
- `src/templates/lists/user_profile.html`

## SMART-RULES

Status: `Legacy`

- `src/templates/lists/components/smart_rules_builder.html`
- `src/templates/lists/components/smart_rules_summary.html`
- `src/templates/lists/smart_list_detail.html`

## LIST-CURATION

Status: `Legacy`

- `src/templates/lists/add_item.html`
- `src/templates/lists/components/add_item_preview_modal.html`
- `src/templates/lists/components/add_item_search_results.html`
- `src/templates/lists/components/recommend_preview_modal.html`
- `src/templates/lists/components/recommend_search_results.html`
- `src/templates/lists/list_recommendations.html`
- `src/templates/lists/recommend_item.html`

## TIERS

Native ListDetailScreen reuses ListBulkBar above TierBoard in selection mode; the web tier template remains authoritative and its tier tiles do not expose bulk-selection checkboxes. Browser recheck pending; iOS and Android not verified.

Status: `Legacy`

- `src/static/js/listTiers.js`
- `src/templates/lists/components/tier_board.html`
- `src/templates/lists/components/tier_tile.html`

## SET-NAV

- `frontend/ui/ReorderList.jsx`

`frontend/pages/SettingsSidebarView.jsx`, `native-proof/app/surfaces/SidebarSettingsSlot.jsx`: native measured-layout drag handles and accessibility move actions; shared also by `HomeScreenEditor`. Sidebar drag saved/restored and web confirmed at 375x812; Home drag pending; Django Sidebar remains until full parity. Native readiness Primitives; iOS/Android not verified.

Native `SettingsScreen` consumes shared `SettingsView`, `SettingsNav` and `SettingsPage`, including the shared chevron and Data-only page spacing. These are React-owned presentation; web navigation and retained server content keep their existing ownership. Browser Data geometry checked at 375x812. iOS: Not verified. Android: Not verified. Native readiness: Primitives. Remaining consumers and retirement conditions are unchanged.

Status: `Partial / coexisting`

- `src/templates/users/base.html`
- `src/templates/users/components/_media_type_order_js.html`
- `src/templates/users/components/media_type_picker.html`
- `src/templates/users/sidebar.html`

## SET-PREFERENCES

SelectField exposes optional trigger, text and label styles for the native manual-entry form; defaults preserve existing preference and browser consumers. iOS and Android not verified.

Status: `Partial / coexisting`

Expo consumes `frontend/ui/PreferencesForm.jsx`, `frontend/ui/SelectField.jsx`, `frontend/pages/SettingsPreferencesView.jsx` through `native-proof/app/surfaces/PreferencesSlot.jsx`. Native section frames, icons, dropdowns and card link remain partial parity. Currency saved/restored in browser; iOS and Android not verified. Django/Alpine remains until complete parity.

- `native-proof/app/surfaces/SettingsSaveBar.jsx` adapts shared `FormSaveBar` to the Settings viewport; consumed by `SettingsScreen`, `PreferencesSlot`, `AppearanceSettingsSlot`. Home Screen uses an inline save action. Props-only shared presentation; native readiness Primitives.

- `src/templates/users/preferences.html`

## SET-APPEARANCE

Native custom opacity and runtime radius tokens are consumed by settings palettes; static radii and real backdrop blur remain incomplete. Browser effect comparison pending; iOS and Android not verified.

Status: `Partial / coexisting`

- `src/templates/users/appearance.html`
`frontend/settings/AppearanceEditor.jsx`, `frontend/pages/SettingsAppearanceView.jsx`, `native-proof/app/surfaces/AppearanceSettingsSlot.jsx`: opt-in docked save adapter and single page heading. Browser compared; full-size swatches and footer; Light saved and System default restored with web confirmation; custom effects/scrolled editor still partial. Django editor remains until parity; iOS/Android not verified, Primitives.

## SET-CARDS

Status: `Partial / coexisting`

- `src/templates/users/cards.html`
- `src/templates/users/cards_preview.html`
- `frontend/pages/SettingsCardsView.jsx`, `frontend/settings/CardsEditor.jsx`, `native-proof/app/surfaces/CardsSettingsSlot.jsx`, `native-proof/app/data/settingsTransport.js` (native touch action setting uses the account preference API; per-type native profiles remain local demo state)

## SET-HOME

Status: `Partial / coexisting`

- `src/templates/users/home_screen.html`
`frontend/settings/HomeScreenEditor.jsx`, `frontend/pages/SettingsHomeScreenView.jsx`, `native-proof/app/surfaces/HomeSettingsSlot.jsx`: shared drag handles, parsed icons, filter summaries, color dialog, native page-card framing, inline save and Label appearance dropdown. Browser compared; Outline saved and Soft restored with web confirmation; expanded rows/drag parity incomplete. Django editor remains until parity; iOS/Android not verified, Primitives.

## SET-PROVIDERS

Shared ProviderSettingsView and ProviderDetailView receive guidesSessionRequired from the provider adapter. Bearer responses omit credential-bearing webhook/install/configure guides and show password sign-in guidance; other connection forms, settings and browser/session defaults remain. Existing partial/coexisting ownership and full-parity retirement conditions remain. Transport/server regressions cover redaction; browser appearance and actual iOS/Android runtime for this state are not verified.

TextField exposes optional input/label styles and a decimal keyboard for native manual entry; existing consumers retain defaults. iOS and Android not verified.

Status: `Partial / coexisting`

- `frontend/providers/mountAccountConnection.jsx`
- `frontend/providers/ConnectionSettings.jsx`
- `frontend/providers/ProviderDirectoryView.jsx`
- `frontend/providers/ImportSettings.jsx`
- `frontend/providers/mountImportPreferences.jsx`
- `frontend/providers/ProviderMetadataView.jsx`
- `frontend/providers/ProviderPageView.jsx`
- `frontend/providers/mount.jsx`
- `frontend/providers/provider-page.css`
- `frontend/providers/transport.web.js`
- `src/templates/users/providers/overview_fallback.html`
- `src/templates/users/providers/header_fallback.html`
- `src/templates/users/providers/content_legacy.html`
- `src/templates/users/providers/provider_legacy.html`

- `src/templates/users/components/account_connection.html`
- `src/templates/users/components/account_import_action.html`
- `src/templates/users/components/provider_card.html`
- `src/templates/users/components/provider_filter_dropdown.html`
- `src/templates/users/components/provider_results.html`
- `src/templates/users/components/provider_row.html`
- `src/templates/users/components/provider_status_badge.html`
- `src/templates/users/provider_detail.html`
- `src/templates/users/providers.html`
- `frontend/pages/ProviderDetailView.jsx`
- `frontend/providers/ProviderImportPanel.jsx`
- `frontend/providers/ProviderLinkCard.jsx`
- `native-proof/app/screens/ProvidersScreen.jsx`, `native-proof/app/screens/ProviderDetailScreen.jsx`, `native-proof/app/screens/integrationPalette.js` (native SettingsSurface consumers; directory/frame/account-order fixes, medium themed controls; browser comparison remains partial)
- `native-proof/app/data/providersTransportCore.js` (retains browser account status and binary import payloads; current iOS/Android paths unverified)

Provider account state and bundled keys: `provider_ui._connection_card` and `provider_catalog.provider_state` (`account_linkable`) split a linked account from credentials; `credentials.ProviderCredentialSpec.bundled_only` (WeTrakr) hides every key surface. `mountAccountConnection` mounts the shared `ProviderLinkCard` device-code flow for WeTrakr through `transport.web.linkRequest` (replacing the Alpine connect box in `wetrakr_sync.html`), `ProviderLinkCard.initialDevice` resumes a pending code on web and via `ProviderDetailView` on native, and `ProviderSettingsView`/`readThemePalette` give the key chip and neutral badges real colors. Web verified in Chromium; iOS and Android unverified.

ProviderDetailView consumes portable AccountSettings/ProviderSettingsView, native-only active sign-in slots and CatalogGrantsCard on Stremio Tracking. Existing Django/web adapter bodies coexist until full browser parity and migration acceptance; no platform classification is changed by these native composition edits.

## CONNECTIONS

Shared ConnectionsView.DeviceApproval accepts a session-required state from ConnectionsScreen. Creating an account-bound device grant requires password-session sign-in; existing list/revoke controls and browser defaults remain. Existing ownership and fallback retirement conditions remain; browser appearance and actual iOS/Android runtime for this state are not verified.

Status: `Partial / coexisting`

- `frontend/pages/ConnectionsView.jsx`
- `native-proof/app/screens/ConnectionsScreen.jsx` (shared SettingsSurface plus explicit Save, Pause/Resume, complete capability states and server date labels; post-fix browser controls remain pending, iOS/Android unverified)

## REVIEW-QUEUE

Status: `Partial / coexisting`

- `frontend/pages/MetadataReviewView.jsx`
- `native-proof/app/screens/MetadataReviewScreen.jsx` (shared Activity settings shell; queue/preview ownership preserved; browser acceptance and current iOS/Android runtime verification pending)

## SET-METADATA

Status: `Partial / coexisting`

- `src/templates/users/components/media_type_provider_modal.html`
- `src/templates/users/components/metadata_admin_notice.html`
- `src/templates/users/components/metadata_library_repair.html`
- `src/templates/users/components/metadata_source_card.html`
- `src/templates/users/components/provider_connection.html`
- `src/templates/users/components/provider_credential_modal.html`
- `src/templates/users/components/provider_credential_row.html`
- `src/templates/users/components/provider_credential_status.html`
- `src/templates/users/components/provider_default_shortcuts.html`
- `src/templates/users/components/provider_instance_credentials.html`
- `src/templates/users/components/provider_metadata.html`
- `src/templates/users/components/provider_personal_credentials.html`
- `src/templates/users/metadata.html`

## SET-IMPORT

Status: `Partial / coexisting`

PR #1878: Provider overview outline/focus and linked chip decoration are styled by universal React Native presentation; browser tests wait for current search-result overlays and mounted sidebar links instead of retired DOM wrappers.

PR #1878: Shared statistics presentation exposes stable RN Web test IDs for total labels, summary values, and status legend/ring geometry. Browser tests retain their substantive UI assertions across the native migration.

PR #1878: The file-import web adapter listens for preference changes on the shared page boundary, covering sibling islands with or without Alpine while retaining the universal file-picker presentation.

PR #1878: CLZ preference changes mirror their live values into fallback POST inputs regardless of Alpine ancestry. App tracking browser tests submit through the shared search island instead of a retired form.

PR #1878: The registered browser island loader discovers sidebar, global search, track form and statistics hosts consistently with its renderer dispatch list, restoring universal search mounting in browser workflows.

PR #1878: Provider overview, credential key, and import website hyperlinks receive DOM-only URL attributes from the browser mounts, while their universal JSX remains native-compatible.

PR #1878: The browser file-import mount now supplies a DOM chooser to the universal picker. Chosen `File` objects flow through the existing form transport; the server fallback remains active when the lazy module is blocked, reflects current preferences, and exposes semantic provider website links.

- `frontend/providers/ProviderFileImportView.jsx`
- `frontend/providers/mountCredentials.jsx`
- `frontend/providers/mountSetupGuide.jsx`
- `frontend/providers/ProviderSettingsFormView.jsx`
- `src/users/provider_guides.py` (includes the Jellyfin KefinTweaks rating-flag warning)
- `src/users/provider_forms.py`
- `src/templates/users/providers/setup_guide.html`
- `src/templates/users/providers/setup_guide_parts.html`
- `src/templates/users/components/file_import_fallback.html`
- `src/templates/users/components/clz_import_fallback.html`
- `src/templates/users/_reading_server_card.html`
- `src/templates/users/_reading_server_modal.html`
- `src/templates/users/components/game_sync_fields.html`
- `src/templates/users/components/import_schedule_fields.html`
- `src/templates/users/components/import_upload_badge.html`
- `src/templates/users/components/plex_library_selector.html`
- `src/templates/users/components/provider_import.html`
- `src/templates/users/import_data.html`
- `src/templates/users/components/file_import_island.html`
- `src/templates/users/components/settings_form_island.html`

## SET-ACTIVITY

Status: `Partial / coexisting`

- `src/templates/users/components/import_activity.html`
- `src/templates/users/components/import_activity_loader.html`
- `src/templates/users/components/import_source_cleanup.html`
- `src/templates/users/components/outbound_service_activity.html`
- `src/templates/users/provider_activity.html`
- `src/templates/users/providers/integration_activity.html`
- `frontend/pages/SettingsActivityView.jsx`, `native-proof/app/surfaces/ActivitySettingsSlot.jsx` (native central Activity: real MAL mapping tabs/dialog and outbound counts/results; opt-in page-card frames/icons/typography plus accessible panel/headings. Final Django/Expo populated receipt framing matched; all four empty tabs and Undo/Delete confirmation cancellation exercised. Disposable receipt/history/provenance cleaned after verification. Connected flows not exercised, iOS/Android unverified)
- `frontend/providers/ProviderActivityView.jsx` (shared by AniList WEB Activity and native central Activity; opt-in source identities/pageCards, rounded run/date/count layout, text actions and exact cleanup plural labels. Existing web defaults/details/copy/pagination remain; Django central/non-AniList consumers retire only after full browser port/acceptance.)
- `native-proof/app/data/activityTransportCore.test.mjs` (authenticated paging/action transport regression)

## SET-SIDEBAR

Status: `Partial / coexisting`

- `frontend/pages/SettingsSidebarView.jsx`, `native-proof/app/surfaces/SidebarSettingsSlot.jsx` (native page; up/down reorder)

## ACTION-ICONS

Status: `Web Migrated`

- `frontend/ui/actionIcons.js`

## SET-SYNC

Status: `Partial / coexisting`

- `src/templates/users/components/media_server_scrobbles.html`
- `src/templates/users/components/outbound_service_modal.html`
- `src/templates/users/components/outbound_service_status.html`
- `src/templates/users/components/outbound_services.html`
- `src/templates/users/components/stremio_catalog_status.html`
- `src/templates/users/components/sync_state_section.html`
- `src/templates/users/components/tmdb_proxy.html`
- `src/templates/users/components/tmdb_rating_sync.html`
- `src/templates/users/components/wetrakr_sync.html`
- `src/templates/users/integrations.html`
- `src/templates/users/mal_export.html`
- `src/templates/users/providers/integration_content.html`
- `src/templates/users/providers/integrations/emby.html`
- `src/templates/users/providers/integrations/jellyfin.html`
- `src/templates/users/providers/integrations/kodi.html`
- `src/templates/users/providers/integrations/navidrome.html`
- `src/templates/users/providers/integrations/nuvio.html`
- `src/templates/users/providers/integrations/plex.html`
- `src/templates/users/providers/integrations/seerr.html`
- `src/templates/users/providers/integrations/stremio.html`
- `src/templates/users/providers/mal_connection.html`
- `src/templates/users/providers/mal_sync.html`
- `src/templates/users/providers/outbound_sync.html`

## SET-DATA

Shared RssFeedCard accepts a session-required model from DataSettingsSlot. Bearer clients show password sign-in guidance instead of a credential-bearing RSS URL; other Data cards and browser/session defaults remain. Existing ownership and retirement conditions remain; transport checks cover denial handling, while browser appearance and actual iOS/Android runtime for this state are not verified.

`DataSettingsSlot` adapts the shared export, scheduled-export, history and library-maintenance cards, with optional title icons from `actionIcons` and the `SettingsCard` page variant. API duplicate counts determine repair visibility. Shared presentation is React-owned; web Data templates remain coexisting until lower controls reach parity. Browser Data compared at 375x812, still partial. iOS: Not verified. Android: Not verified. Native readiness: Primitives.

Status: `Partial / coexisting`

- `src/templates/users/components/export_controls.html` (backup status region mounts `frontend/settings/BackupStatus.jsx`; fallback kept)
- `src/templates/users/components/library_danger_zone.html`
- `src/templates/users/data_management.html`
- `src/templates/users/export_data.html`
- `src/templates/users/rss.html`
- `frontend/settings/BackupStatus.jsx`

## SET-ACCESS

Shared AccessTokenCard, AppTokensCard and CatalogGrantsCard receive a session-required state from AccessSettingsSlot and CatalogGrantsSlot. Bearer sign-in skips the account-token secret fetch, explains password sign-in for issuance, and preserves lists/revocation. Existing browser defaults, partial/coexisting ownership and full-parity retirement conditions remain. Transport tests cover the fetch boundary; browser appearance and actual iOS/Android runtime for this state are not verified.

Status: `Partial / coexisting`

- `native-proof/app/surfaces/CatalogGrantsSlot.jsx` (CatalogGrantsCard adapter consumed by Stremio Tracking; Access no longer consumes grants. Ref-held one-time secret survives copy failure/cancel and clears on revocation/unmount. Test-DB lifecycle covered; no live credential created/revoked. Browser final placement and iOS/Android unverified.)

Native `AccessSettingsSlot` consumes shared `AccessSettings.jsx` / `SettingsAccessView`: Access frame, collapsed token form, checkbox scopes, adapter credential slots and Authorized applications navigation. Name/expiry/scope validation browser-verified; creation awaits action-time approval. Install-placement/application geometry incomplete. Browser Access templates remain; `DeviceApproval` owns the approval island with server fallback. iOS/Android not verified, Primitives.

- `src/templates/integrations/oauth_applications.html`
- `src/templates/integrations/oauth_device.html`
- `src/templates/users/access_tokens.html`
- `src/templates/users/components/catalog_grants.html`
- `src/templates/users/components/integration_tokens.html`
- `src/templates/users/providers/access_tokens.html`

## AUTH

AuthScreen supplies the account payload's session-required state to shared PasswordCard, AuthenticatorCard and RecoveryCodesCard. Bearer profile/username access remains, with bootstrap secrets and credential actions withheld. Shared session/web defaults are unchanged. Server-rendering checks cover these states; browser appearance and actual iOS/Android runtime for this revision are not verified. Ownership remains partial/coexisting and existing server consumers retire only after full parity.

Native Settings embeds `AuthScreen` in account mode through `SettingsScreen`, using shared `SettingsAccountView` / `AccountSecurityView`. `AuthenticatorCard` accepts shared `RecoveryCodesCard` content so recovery stays in the same card; native Settings hides the separate sign-out and connection actions. Browser account remains server-owned until full parity. Browser: account route verified at 375x812; empty password/authenticator/recovery submissions rejected without credential changes. Exact visual parity remains partial. iOS: Not verified. Android: Not verified. Native readiness: Primitives.

Status: `Partial / coexisting`

Native `AuthEntrance` separates server selection from credentials, keeps manual entry, changes its suggested address with Local/Remote mode, and explains briefly that discovery is unavailable in Expo Go; browser authentication remains server-rendered.

Native device sign-in uses the first-party Floppy app client and a full-access token (`native-proof/app/data/authTransportCore.js`); account security calls `/api/v1/user/account/` for token sign-ins (`native-proof/app/data/authAccountCore.js`). Transport only, no presentation change.

- `src/templates/account/login.html`
- `src/templates/account/messages/logged_in.txt`
- `src/templates/account/messages/logged_out.txt`
- `src/templates/account/signup.html`
- `src/templates/allauth/elements/button.html`
- `src/templates/allauth/elements/button_group.html`
- `src/templates/allauth/elements/field.html`
- `src/templates/allauth/elements/fields.html`
- `src/templates/allauth/elements/form.html`
- `src/templates/allauth/elements/h1.html`
- `src/templates/allauth/elements/h2.html`
- `src/templates/allauth/elements/hr.html`
- `src/templates/allauth/elements/p.html`
- `src/templates/allauth/elements/provider.html`
- `src/templates/allauth/elements/provider_list.html`
- `src/templates/allauth/layouts/base.html`
- `src/templates/allauth/layouts/entrance.html`
- `src/templates/allauth/layouts/manage.html`
- `src/templates/socialaccount/connections.html`
- `src/templates/socialaccount/signup.html`
- `src/templates/socialaccount/snippets/login.html`
- `src/templates/socialaccount/snippets/provider_list.html`
- `src/templates/users/account.html`
- `src/templates/users/password_recover.html`
- `frontend/auth/AuthEntrance.jsx`
- `native-proof/app/screens/ListDetailScreen.jsx` (native list detail with the media type breakdown popover; consumers of `FilterBar`, `ListResults`, `BulkActionBar`, `ListMembership`)
- `native-proof/app/screens/LibraryScreen.jsx`, `native-proof/app/surfaces/LibraryBulkActions.jsx` (native Library header, settings gear `ActionMenu` and `FilterBar variant="toolbar" showClear={false}`; `showClear` is the new `FilterBar` prop that hides the inline Clear button, whose reset stays in the Filters sheet)
- `native-proof/app/surfaces/shellContext.js` (open page's media type for the header search), `native-proof/app/data/userDate.js` (+ `userDate.test.mjs`) (server time zone and user date format), `native-proof/app/data/accountDisplay.js` (display preferences from `/api/v1/user/preferences/`)
- `native-proof/app/screens/AuthScreen.jsx`
- `native-proof/app/data/serverDiscovery.native.js`
- `native-proof/app/data/serverDiscovery.web.js`
- `native-proof/app/data/serverDiscoveryCore.js`
- `native-proof/app/data/authTransportCore.js`

## SET-NOTIFICATIONS

Status: `Partial / coexisting`

Expo consumes shared `frontend/settings/NotificationSettings.jsx` cards, `frontend/ui/TextField.jsx` trailing help and `frontend/pages/SettingsNotificationsView.jsx` through `native-proof/app/surfaces/NotificationsSettingsSlot.jsx`. Native page framing, title icons, help order and save actions; Django/HTMX remains until full parity. Daily Digest saved/restored in browser; help panel opened/closed and The Matrix excluded/reincluded with web confirmation; artwork/row icons and detailed spacing still need final verification. iOS/Android not verified, Primitives.

- `src/templates/users/components/excluded_items.html`
- `src/templates/users/components/search_results.html`
- `src/templates/users/notifications.html`

## ONBOARDING

Status: `Legacy`

The browser wizard remains server-owned. Native shared presentation coexists through optional standalone/reorder/sign-in slots; retirement requires a complete browser wizard port and parity acceptance. Refreshed 375x812 comparison confirmed shell/logo/drag/icon/switch fixes; TV Shows toggled off/on with original selections restored and no Continue/save. Native title wrapping and 4px progress/card offset remain Partial. Later/connected steps and current iOS/Android runtime behavior are unverified; no provider keys entered.

- `frontend/onboarding/OnboardingWizard.jsx`
- `frontend/pages/OnboardingView.jsx`
- `native-proof/app/screens/OnboardingScreen.jsx` (standalone App/ShellSlot consumer; source icons/hero, local reorder/toggles, inline forms/uploads and provider sign-in adapter)

- `src/templates/users/onboarding/_layout.html`
- `src/templates/users/onboarding/components/connect_form.html`
- `src/templates/users/onboarding/components/connect_oauth.html`
- `src/templates/users/onboarding/components/connect_trakt.html`
- `src/templates/users/onboarding/components/connect_upload.html`
- `src/templates/users/onboarding/components/integration_plex.html`
- `src/templates/users/onboarding/import_status.html`
- `src/templates/users/onboarding/integration_setup.html`
- `src/templates/users/onboarding/media_types.html`
- `src/templates/users/onboarding/metadata.html`
- `src/templates/users/onboarding/service_setup.html`
- `src/templates/users/onboarding/services.html`
- `src/templates/users/onboarding/services_summary.html`

## SET-SYSTEM

Shared AdvancedSettings/CacheManagementCard, server advanced props and the Advanced fallback use the complete parenthesized provider-cache description. Server-rendering regression coverage verifies the copy. The island remains fully React-owned inside the coexisting server page; full page parity is required for retirement. Browser layout and actual iOS/Android runtime for this copy revision are not verified.

`SettingsAboutView` and `AboutSettings.jsx` now include native page framing, translated per-card labels/icons and shared `AboutSpecificationPreview`; `AboutSettingsSlot` owns text transport, downloads, clipboard/sharing and install platform. Browser verification pending; iOS/Android not verified, Primitives. Existing server shell/card consumers remain until full parity.

Native `AdvancedSettingsSlot` wraps shared `SettingsAdvancedView` / `AdvancedSettings` cards in the shared page-card variant; translated labels and parsed icons come from the existing account-scoped API. Checkbox selected state now exposes aria-checked. Browser: six cache confirmations opened/cancelled; invalid port rejected, original input restored; image caching enabled then disabled with hydrated web confirmation. No cache clear or port mutation executed. Browser Advanced island remains React-owned and surrounding server shell remains coexisting; retirement requires full page parity. iOS: Not verified. Android: Not verified. Native readiness: Primitives.

Status: `Legacy`

- `src/templates/users/about.html`
- `src/templates/users/advanced.html`
- `src/templates/users/components/server_port.html`
- `src/templates/users/components/update_banner.html`
- `src/templates/users/components/whats_new.html`

## MATCH-REVIEW

Status: `Legacy`

- `src/static/js/matchFixReview.js`
- `src/templates/integrations/match_fix.html`

## INTEGRATION-PANELS

Status: `Legacy`

- `src/templates/integrations/library_panel.html`
- `src/templates/integrations/seerr_request.html`
- `src/templates/integrations/seerr_status.html`

## DEVICE-CONNECT

Status: `Legacy`

- `src/templates/integrations/trakt_device_code.html`

## STREMIO

Status: `Legacy`

- `src/templates/integrations/stremio_configure.html`

## MIGRATION

Status: `Partial / coexisting`

PR #1878: the Track dialog adapter (`mountTrackForm.jsx`) posts its payload, mirrors the card's Alpine open flag when it has no legacy modal root, and `mediaCardForms.js` passes date shortcuts and runtime through to `TrackForm.jsx`; shared status options set `aria-checked`.

Track dialog layout restore: `TrackForm.jsx`, `TrackFormTabs.jsx`, `SelectField.jsx`, `DateTimePicker.jsx`, `TagManagement.jsx` and `CollectionCopies.jsx` render the v26.10.29 layout (title bar, icon sidebar, two-column General grid, dropdown selects, date chips, Delete / Update footer) from the shared React form. Browser verified; iOS and Android not verified.

- `frontend/auth/AccountSecurity.jsx`
- `frontend/discover/DiscoverScreen.jsx`
- `frontend/discover/mountDiscover.jsx`
- `frontend/errors/ApiDocs.jsx`
- `frontend/errors/ErrorRecovery.jsx`
- `frontend/errors/mountErrorRecovery.jsx`
- `frontend/pages/AuthView.jsx`
- `frontend/pages/CalendarView.jsx`
- `frontend/pages/CollectionView.jsx`
- `frontend/pages/DiscoverView.jsx`
- `frontend/pages/ErrorsView.jsx`
- `frontend/pages/HistoryView.jsx`
- `frontend/pages/HomeView.jsx`
- `frontend/pages/LibraryPageView.jsx`
- `frontend/pages/ListDetailPageView.jsx`
- `frontend/pages/ListsPageView.jsx`
- `frontend/pages/PageChrome.jsx`
- `frontend/pages/PeopleView.jsx`
- `frontend/pages/ProvidersView.jsx`
- `frontend/pages/SearchPageView.jsx`
- `frontend/pages/SettingsAboutView.jsx`
- `frontend/pages/SettingsAccessView.jsx`
- `frontend/pages/SettingsAccountView.jsx`
- `frontend/pages/SettingsAdvancedView.jsx`
- `frontend/pages/SettingsAppearanceView.jsx`
- `frontend/pages/SettingsDataView.jsx`
- `frontend/pages/SettingsHomeScreenView.jsx`
- `frontend/pages/SettingsNavLink.jsx`
- `frontend/pages/SettingsNavLink.web.jsx`
- `frontend/pages/SettingsNotificationsView.jsx`
- `frontend/pages/SettingsPreferencesView.jsx`
- `frontend/pages/SettingsUsersView.jsx`
- `frontend/pages/SettingsView.jsx`
- `frontend/pages/StatisticsView.jsx`
- `frontend/pages/TagsPageView.jsx`
- `frontend/pages/UtilitiesView.jsx` (native About > Open Utilities > Copy Server URL exercised in Expo; exact connected address copied and prior clipboard restored. WEB About static-cache utilities have a different platform scope; Partial browser parity, iOS/Android unverified.)
- `frontend/pages/registry.js`
- `frontend/providers/AccountSettings.jsx`
- `frontend/providers/ProviderContext.jsx`
- `frontend/providers/ProviderSettingsView.jsx`
- `frontend/providers/mountAccountImportAction.jsx`
- `frontend/providers/mountDirectory.jsx`
- `frontend/providers/mountFileImport.jsx`
- `frontend/providers/mountSettingsForm.jsx`
- `frontend/settings/AboutSettings.jsx`
- `frontend/settings/AccessSettings.jsx`
- `frontend/settings/AdvancedSettings.jsx`
- `frontend/settings/AppearanceEditor.jsx`
- `frontend/settings/Chip.web.jsx`
- `frontend/settings/ConfirmDialog.jsx`
- `frontend/settings/DataSettings.jsx`
- `frontend/settings/HomeScreenEditor.jsx`
- `frontend/settings/NotificationSettings.jsx`
- `frontend/settings/island-reveal.css` (the sidebar root is excluded from the hide rule; see the Shell navigation entry in the component register)
- `frontend/settings/mountAbout.jsx`
- `frontend/settings/mountAdvanced.jsx`
- `frontend/settings/mountBackupStatus.jsx`
- `frontend/settings/mountSettingsView.jsx`
- `frontend/ui/ArtworkImage.jsx`
- `frontend/ui/ArtworkImage.native.jsx`
- `frontend/ui/ArtworkImage.web.jsx`
- `frontend/ui/Badge.jsx`
- `frontend/ui/BadgeTheme.web.jsx`
- `frontend/ui/BulkActionBar.native.jsx`
- `frontend/ui/BulkActionBar.web.jsx`
- `frontend/ui/BulkEpisodeForm.jsx`
- `frontend/ui/BulkJobDock.native.jsx`
- `frontend/ui/BulkJobDock.web.jsx`
- `frontend/ui/Button.native.jsx`
- `frontend/ui/Button.web.jsx`
- `frontend/ui/CacheStatusCard.jsx`
- `frontend/ui/CalendarLinks.jsx`
- `frontend/ui/CollectionBrowser.jsx`
- `frontend/ui/CollectionCopies.jsx`
- `frontend/ui/CollectionCustomFields.jsx`
- `frontend/ui/ColumnMenu.jsx`
- `frontend/ui/CopyBlock.jsx`
- `frontend/ui/CopyField.jsx`
- `frontend/ui/DateTimePicker.jsx`
- `frontend/ui/DetailChildrenTheme.web.jsx`
- `frontend/ui/DetailCompositionTheme.native.jsx`
- `frontend/ui/DetailCompositionTheme.web.jsx`
- `frontend/ui/DetailRelatedTheme.native.jsx`
- `frontend/ui/DetailRelatedTheme.web.jsx`
- `frontend/ui/DeviceApproval.jsx`
- `frontend/ui/DeviceConnect.jsx`
- `frontend/ui/FormSaveBar.jsx`
- `frontend/ui/GlobalSearch.web.jsx`
- `frontend/ui/HistoryFeed.jsx`
- `frontend/ui/HistoryFeed.native.jsx`
- `frontend/ui/HistoryFeed.web.jsx`
- `frontend/ui/HomeShelves.jsx` (shared browser and native Home shelf rows; compact phone cards are 110 pt wide)
- `frontend/ui/IconRenderer.native.jsx`
- `frontend/ui/IconRenderer.web.jsx`
- `frontend/ui/InstallPromptCard.jsx`
- `frontend/ui/IntegrationPanels.jsx`
- `frontend/ui/ListCard.jsx`
- `frontend/ui/ListFormView.jsx`
- `frontend/ui/ListHeader.jsx`
- `frontend/ui/ListMembership.jsx`
- `frontend/ui/ListRecommendationsView.jsx`
- `frontend/ui/ListResults.jsx`
- `frontend/ui/ListSearchView.jsx`
- `frontend/ui/ListsForm.native.jsx`
- `frontend/ui/ListsForm.web.jsx`
- `frontend/ui/MarkdownNotes.jsx`
- `frontend/ui/MarkdownPreview.native.jsx`
- `frontend/ui/MarkdownPreview.jsx`
- `frontend/ui/MarkdownPreview.web.jsx`
- `frontend/ui/MediaCard.native.jsx`
- `frontend/ui/MediaCardActionHost.jsx`
- `frontend/ui/MediaCardActions.jsx`
- `frontend/ui/MediaCardFormControls.jsx`
- `frontend/ui/MetadataReview.jsx`
- `frontend/ui/ModalHost.native.jsx`
- `frontend/ui/PageSlot.web.jsx`
- `frontend/ui/ParentPicker.jsx`
- `frontend/ui/ParentPicker.web.jsx`
- `frontend/ui/People.jsx`
- `frontend/ui/PreferencesForm.jsx`
- `frontend/ui/ProgressBar.jsx`
- `frontend/ui/ProgressBreakdown.jsx`
- `frontend/ui/ProgressBreakdownTheme.web.jsx`
- `frontend/ui/ProgressCard.jsx`
- `frontend/ui/ProgressCard.native.jsx`
- `frontend/ui/ProgressCard.web.jsx`
- `frontend/ui/ProgressChanger.jsx`
- `frontend/ui/ProgressChangerTheme.web.jsx`
- `frontend/ui/ProgressDetailTheme.web.jsx`
- `frontend/ui/ProgressDialog.jsx`
- `frontend/ui/Rail.jsx`
- `frontend/ui/Rail.web.jsx`
- `frontend/ui/RatingChip.jsx`
- `frontend/ui/RatingStarIcon.native.jsx`
- `frontend/ui/RatingStarIcon.web.jsx`
- `frontend/ui/RefreshNotice.jsx`
- `frontend/ui/ReleaseCalendar.jsx` (calendar filter/menu dialogs: uniform floating corners; compact bottom-sheet corners preserved)
- `frontend/ui/Reveal.jsx`
- `frontend/ui/Reveal.native.jsx`
- `frontend/ui/Reveal.web.jsx`
- Sidebar's web Reveal clips a transform-animated panel, while its native counterpart keeps the measured-height animation. The shared Sidebar and its web and Expo consumers retain their existing ownership and fallback retirement conditions; browser regression and native runtime evidence are recorded separately in the component register.
- `frontend/ui/SearchResultCard.jsx`
- `frontend/ui/SearchResultCard.native.jsx`
- `frontend/ui/SearchResultCard.web.jsx`
- `frontend/ui/SearchResults.jsx`
- `native-proof/app/data/useMobileGridLayout.js`
- `frontend/ui/SearchResults.web.jsx`
- `frontend/ui/searchRows.js`
- `frontend/ui/shelfWidth.web.js`
- `frontend/ui/keyboardOverlap.web.js`
- `native-proof/app/data/useKeyboardOverlap.js`
- `native-proof/app/surfaces/WindowedRail.jsx`
- `frontend/ui/SelectField.jsx`
- `frontend/ui/SetupGuide.jsx`
- `frontend/ui/Shell.jsx`
- `frontend/ui/SmartRules.jsx`
- `frontend/ui/StatisticsPreferences.jsx`
- `frontend/ui/Spinner.jsx`
- `frontend/ui/SpinnerTheme.web.jsx`
- `frontend/ui/StatisticsCharts.jsx`
- `frontend/ui/StatisticsSections.jsx`
- `frontend/ui/StatisticsSummary.jsx`
- `frontend/ui/StremioConfigurator.jsx`
- `frontend/ui/TagManagement.jsx`
- `frontend/ui/TextField.jsx`
- `frontend/ui/TierBoard.jsx`
- `frontend/ui/ToggleChip.jsx`
- `frontend/ui/ViewToggle.jsx`
- `frontend/ui/TrackDialog.jsx`
- `frontend/ui/TrackForm.jsx`
- `frontend/ui/TrackFormTabs.jsx`
- `frontend/ui/barcodeEntry.native.js`
- `frontend/ui/barcodeEntry.web.js`
- `frontend/ui/clipboard.native.js`
- `frontend/ui/clipboard.web.js`
- `frontend/ui/controls/ActivityList.jsx`
- `frontend/ui/controls/Catalog.jsx`
- `frontend/ui/controls/ComboField.jsx`
- `frontend/ui/controls/Dialog.jsx`
- `frontend/ui/controls/FeatureList.jsx`
- `frontend/ui/controls/FilePicker.jsx`
- `frontend/ui/controls/FilePicker.native.jsx`
- `frontend/ui/controls/FilePicker.web.jsx`
- `frontend/ui/controls/Form.jsx`
- `frontend/ui/controls/Menu.jsx`
- `frontend/ui/controls/Navigation.jsx` (shared Tabs explicitly exposes aria-selected; native Activity supplies a named tabpanel. Empty tabs compared on both browser surfaces; semantics recheck pending, iOS/Android unverified)
- `frontend/ui/controls/Tooltip.jsx`
- `frontend/ui/dateTime.js`
- `frontend/ui/installPrompt.native.js`
- `frontend/ui/installPrompt.web.js`
- `frontend/ui/markdown.js`
- `frontend/ui/mediaCardForms.js`
- `frontend/ui/mountBulkEpisodeForm.jsx`
- `frontend/ui/mountCacheStatus.jsx`
- `frontend/ui/mountCheckInState.jsx`
- `frontend/ui/mountCollectionBrowser.jsx`
- `frontend/ui/mountCollectionCopyFields.jsx`
- `frontend/ui/mountCollectionFields.jsx`
- `frontend/ui/mountCopyField.jsx`
- `frontend/ui/mountDateTimePicker.jsx`
- `frontend/ui/mountDetailChildren.jsx`
- `frontend/ui/mountDetailComposition.jsx`
- `frontend/ui/mountDetailRelated.jsx`
- `frontend/ui/mountDeviceApproval.jsx`
- `frontend/ui/mountDeviceConnect.jsx`
- `frontend/ui/mountFilterBar.jsx`
- `src/templates/app/components/filter_bar.html` (island host for the toolbar's Filters, Status and Sort dropdowns; props from `src/app/filter_bar.py`)
- `frontend/ui/mountSettingsMenu.jsx`
- `frontend/ui/mountHistoryFeed.jsx`
- `frontend/ui/mountHome.jsx`
- `frontend/ui/mountLibraryPage.jsx`
- `frontend/ui/mountListDetailPage.jsx`
- `frontend/ui/mountListsPage.jsx`
- `frontend/ui/mountMarkdownIslands.jsx`
- `frontend/ui/mountMediaTable.jsx`
- `frontend/ui/mountMetadataReview.jsx`
- `frontend/ui/mountParentPicker.jsx`
- `frontend/ui/mountPeople.jsx`
- `frontend/ui/mountProgressBreakdown.jsx`
- `frontend/ui/mountProgressChanger.jsx`
- `frontend/ui/mountProgressDetail.jsx`
- `frontend/ui/mountRails.jsx`
- `frontend/ui/mountRatingChip.jsx`
- `frontend/ui/mountReleaseCalendar.jsx`
- `frontend/ui/mountSearchResults.jsx`
- `frontend/ui/mountShell.jsx`
- `frontend/ui/mountStatisticsCharts.jsx`
- `frontend/ui/mountStatisticsSections.jsx`
- `frontend/ui/mountStatisticsSummary.jsx`
- `frontend/ui/mountStremioConfig.jsx`
- `frontend/ui/mountTags.jsx`
- `frontend/ui/mountTrackForm.jsx`
- `frontend/ui/people-parity.css`
- `frontend/ui/serviceWorkerStatus.native.js`
- `frontend/ui/serviceWorkerStatus.web.js`
- `frontend/ui/statistics-fallback.css`
- `frontend/users/UsersManagement.jsx`
- `src/static/js/searchParentFallback.js`
- `src/templates/app/components/date_time_picker_legacy.html`
- `src/templates/app/components/date_time_picker_react.html`
- `src/templates/app/components/detail_composition.html`
- `src/templates/app/components/detail_related.html`
- `src/templates/app/components/edition_picker.html`
- `src/templates/app/components/people_hero.html`
- `src/templates/app/components/people_island.html`
- `src/templates/app/components/rating_chip.html`
- `src/templates/users/components/progress_changer.html`
- `src/templates/users/manage_users.html`

## PAGE
- `PageHeading` optional title/count styles are passed through native `LibraryPageView`; other consumers retain defaults. Status `Partial / coexisting`, readiness `Primitives`; final library browser spacing check and iOS/Android runtime verification remain outstanding. Server headings retire with their owning page migrations.

Status: `Partial / coexisting`

## SET-USERS

UsersSlot supplies UsersManagement with its credential-management capability. Bearer list/filter/delete behavior remains subject to server permissions; creation, privilege mutation and password reset display password-session guidance. Shared session/web defaults and existing fallback retirement conditions remain. Server-rendering checks cover these states; browser appearance and actual iOS/Android runtime for this revision are not verified.

Native `UsersSlot` omits the duplicate heading; shared `UsersManagement` uses the native page variant and shared chevron icons. Shared `TextField` / `SelectField` use the supplied field-border token. Browser: empty Create user rejected; current-account password dialog opened without entering a credential and cancelled. Existing credentials/roles unchanged. Browser adapter and remaining server fallback remain until full visual/control parity. iOS: Not verified. Android: Not verified. Native readiness: Primitives.

Status: `Partial / coexisting`

## BULK_ACTIONS

Status: `Partial / coexisting`

## CONTROL-KIT

Status: `Partial / coexisting`

- Collection detail Summary, CollectionEntryGroup, CollectionEpisodeList, CollectionEpisodeToggle, CollectionShowMore and CollectionSectionHeading: shared props-only `CollectionContextCard` in `CollectionCopyDialog`, used by the Expo detail sheet and the web Collection dialog through a browser adapter. Status: partial/coexisting; web retains its server-owned editing controls inside the shared group; the native Collection screen and detail sheet use the same group for copies; detail shows season audit rows and read-only episode disclosures including missing locally known episodes; web and Expo initially show five collection rows with the shared show-more control; the web footer compensates for its server-rendered final row’s different margin and keeps the collapsed and expanded spacing consistent, with a consistent divider position and season edits and removal through the collection API. Resolution breakdown, completion and the Missing row use episode coverage; the latest copy determines each episode’s resolution, and specials are excluded from show totals. A secondary disclosure groups copies by season and episode with titles and comma-separated resolutions, initially limiting the list to five rows when an item has multiple copies. PWA: earlier Summary inspected on a physical Duo; grouped entry card and edit interactions not runtime verified. Desktop browser: not verified. iOS: grouped entries, expanded episodes including a Missing row, and 68-of-69 summary inspected on a physical Duo; save and remove not runtime verified. Android: not verified.

- `frontend/ui/sectionSpacing.js`: `sectionSpacing` shares Home shelf and Collection section gaps across viewport sizes. Web, iOS and Android: pure shared layout rule; runtime verification follows its consumers.

DropdownMenu → DropdownMenuContent owns keyboard-driven layout inside the modal host, including an explicit iOS height animation on the Duo rail; Android applies the settled keyboard size; its existing web and native consumers and retirement conditions are unchanged. The user accepted the quartic animation on a physical iOS Duo; browser and Android animation remain unverified.

- Episode label formatting: `ProgressBreakdown` serves the web progress mount and Expo `ProgressDialog`; history, media cards, and tracking dialogs receive their labels from their existing server adapters. Shared presentation remains React-owned, with existing legacy web history consumers coexisting; retirement conditions are unchanged. EpisodeOrdering uses shared React presentation on web with a coexisting server fallback; native metadata review remains unported. Retirement of the fallback requires preserving its no-JavaScript behavior. Browser, iOS, and Android verification are recorded separately in the component register.
