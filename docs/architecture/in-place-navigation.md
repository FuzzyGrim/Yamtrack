# In-place navigation (`hx-boost`)

Moving between two server-rendered pages normally throws away the whole document:
sidebar, top bar, scripts, scroll position and every React island are rebuilt, and the
page flashes. Adding `hx-boost="true"` to a link makes htmx fetch the target page and
swap the `<body>` in place instead, then push the new URL. The sidebar already works
this way. History's month and year links use it too (`src/templates/app/history.html`).

Use it for links that stay inside the signed-in app and go to another full page that
shares the same layout: month and year steppers, previous and next, tab-like page
switches, "open this item" links.

## How to use it

Put the attribute on the link, or on a container whose links should all be boosted:

```html
<a hx-boost="true" href="?year=2025&m=11">November</a>
<nav hx-boost="true">...</nav>
```

Nothing else is needed on the link. The server returns the normal full page; htmx swaps
the body and updates the address bar.

## What the app already guarantees

All of this is set up once in `src/templates/base.html`; do not repeat it per page.

- **Full template for boosted requests.** A boosted request sends `HX-Request` like any
  htmx call but needs the whole page. Views use `app.helpers.is_htmx_fragment(request)`,
  which is false for boosted requests and history restores, so only genuine in-page htmx
  requests get a fragment. Never branch on `HX-Request` alone.
- **Sign-in redirects navigate.** If a boosted response turns out to be the login or
  sign-up page (`<meta name="floppy-page" content="entrance">`), the swap is cancelled
  and the browser does a normal navigation to it.
- **CSRF** is sent as a header on every htmx request.
- **No stale snapshots.** `htmx.config.historyCacheSize = 0` and
  `refreshOnHistoryMiss = true`, so the Back and Forward buttons do a normal full load
  of the previous URL. That is intended: htmx's saved copy would capture DOM already
  changed by Alpine and Select2. A boosted click is in place; Back is not.
- **Cache-busting** query parameters are added to boosted GETs by htmx's
  `getCacheBusterParam`.

## What a page must do to be safe to swap

A swap replaces the body but not the `<head>`, and it does not reload the window. So:

- **Clean up in `destroy()`.** Alpine components are destroyed and rebuilt on a swap.
  Stop timers, pollers and `window` or `document` listeners there (History stops its
  cache poller and removes its refresh listeners).
- **Bind global listeners once.** Inline scripts in the body run again on every swap.
  Guard anything that attaches to `window`, `document` or `document.body` so it is not
  added twice.
- **Guard long-lived work against leaving the page.** A timer started by the old page
  survives the swap. `cache-updater.js` records its page path and stops itself if the
  path changes.
- **Do not rely on `<head>`.** Styles and scripts that a page needs must already be in
  `base.html`, because a swapped page cannot add to `<head>`.
- **React islands remount** from their `data-props-id` roots after the swap. Keep their
  props in the page's JSON script tags, as the existing islands do.
- **Indicators must not outlive their cause.** If a page shows a spinner or banner for
  background work, clear it from the worker's own stop hook, not only its success path.
  History's refresh spinner is cleared by `CacheUpdater`'s `onStop` option.

## When not to use it

- Links that leave the app, download a file, or open in a new tab.
- Pages with a different layout or a different `<head>` (public list pages, sign-in).
- Form submissions that should do a normal post-and-redirect.
- Anything where the full reload is the point, such as picking up a changed bundle.

## Checking a boosted link

1. Open the page and set a marker: `window.__marker = 'same-document'`.
2. Click the link.
3. The marker should still be set, the URL should have changed, and the new content
   should be showing. If the marker is gone, the click did a full load. Check that the
   link carries `hx-boost` and is not intercepted by script that calls
   `window.location.assign`.
4. Check the swapped page still works: its Alpine state, its React islands, and any
   polling it starts.

## Native apps

Expo screens move between months, pages and tabs in client state and never reload a
document, so there is nothing to port. When a web link gains `hx-boost`, check that the
Expo screen for the same page already navigates in app state. History does. Record the
change in `docs/ui/component-register.md`.

## Worked example: History

`history.html` boosts the four month and year links (previous year, previous month,
next month, current month). Before, each click rebuilt the whole page, resetting scroll
and flashing the layout; the report was that it "felt like a full page reload". Now only
the body changes. The page's cache poller stops on `destroy()` and on path change, and
clears the refresh spinner through `onStop`. The React feed on this page renders cards
only (`chrome={false}`), so the month header stays server-rendered and boosted.
