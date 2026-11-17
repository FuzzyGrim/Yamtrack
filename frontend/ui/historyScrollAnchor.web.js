// Browser-only: Back to History returns to the day the reader left.
//
// Days below History's first view are short placeholders until they load, so
// the browser's own scroll restore (a pixel offset) lands on the wrong day. The
// page remembers the top visible day and how far it sat from the top of the
// screen, per URL, and restores to that day once the days above it have loaded.
// Expo's History screen remounts at the top on Back, before and after this.

const STORAGE_KEY = 'floppy-history-anchor';
const DAY_ID_PREFIX = 'history-day-';

function storage() {
  try {
    return window.sessionStorage;
  } catch (error) {
    return null;
  }
}

// The first day still on screen and where its top sits, or null at the top.
export function topDayAnchor(host) {
  if (!host?.isConnected || window.scrollY < 1) return null;
  for (const day of host.querySelectorAll(`[id^="${DAY_ID_PREFIX}"]`)) {
    const rect = day.getBoundingClientRect();
    if (rect.bottom > 0) return { key: day.id.slice(DAY_ID_PREFIX.length), top: Math.round(rect.top) };
  }
  return null;
}

// One anchor per History URL (the most recent few), so a boosted hop to
// another month does not clear the month the reader will come back to.
const MAX_ANCHORS = 10;

function readAnchors(store) {
  try {
    const saved = JSON.parse(store.getItem(STORAGE_KEY) || '{}');
    return saved && typeof saved === 'object' && !Array.isArray(saved) ? saved : {};
  } catch (error) {
    return {};
  }
}

function writeAnchors(store, anchors) {
  const urls = Object.keys(anchors);
  const kept = urls.length > MAX_ANCHORS
    ? Object.fromEntries(urls.sort((a, b) => (anchors[b].at || 0) - (anchors[a].at || 0))
      .slice(0, MAX_ANCHORS).map(url => [url, anchors[url]]))
    : anchors;
  try {
    store.setItem(STORAGE_KEY, JSON.stringify(kept));
  } catch (error) {
    // Storage full or blocked: Back keeps the browser's own restore.
  }
}

// `url` is the address the page was opened with: on Back the address bar
// already shows the page being returned to while this one is still saving.
export function saveHistoryAnchor(host, url = window.location.href) {
  // A page swapped out by a boosted link keeps the listener; ignore it.
  if (!host?.isConnected) return;
  const store = storage();
  if (!store) return;
  const anchors = readAnchors(store);
  const anchor = topDayAnchor(host);
  if (anchor) anchors[url] = { ...anchor, at: Date.now() };
  else delete anchors[url];
  writeAnchors(store, anchors);
}

// The saved anchor, only when this document was opened by Back/Forward or a
// reload of the same URL. A fresh visit starts at the top. Read once.
export function takeHistoryAnchor() {
  const store = storage();
  if (!store) return null;
  const url = window.location.href;
  const anchors = readAnchors(store);
  const saved = anchors[url];
  if (!saved) return null;
  delete anchors[url];
  writeAnchors(store, anchors);
  if (!saved.key) return null;
  const entry = window.performance?.getEntriesByType?.('navigation')?.[0];
  if (!entry || entry.name !== url) return null;
  if (entry.type !== 'back_forward' && entry.type !== 'reload') return null;
  return saved;
}

export function scrollToHistoryAnchor(anchor) {
  const day = document.getElementById(`${DAY_ID_PREFIX}${anchor.key}`);
  if (!day) return false;
  window.scrollTo(0, day.getBoundingClientRect().top + window.scrollY - (Number(anchor.top) || 0));
  return true;
}
