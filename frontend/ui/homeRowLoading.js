// Which Home rows to request, and in what order. Pure logic shared by web and
// native: each platform only reports where a waiting row sits on screen.

// A row is requested once it is within this many screen heights of the visible
// area, so scrolling finds it already loaded instead of showing a skeleton.
export const ROW_LOOKAHEAD_SCREENS = 1.5;

// If the platform never reports a position (an old browser, a test runner, a
// screen that is never laid out), every row still loads after this delay.
export const ROW_WATCH_FALLBACK_MS = 3000;

const PRIORITY = { visible: 0, near: 1, far: 2 };

export function rowPriority(level) {
  return PRIORITY[level] ?? PRIORITY.far;
}

// `rect` and `view` share one coordinate space; `view.height` is the visible height.
export function rowProximity(rect, view, lookahead = ROW_LOOKAHEAD_SCREENS) {
  const height = Number(view?.height);
  const top = Number(rect?.top);
  const bottom = Number(rect?.bottom);
  if (!(height > 0) || !Number.isFinite(top) || !Number.isFinite(bottom)) return 'far';
  const viewTop = Number(view?.top) || 0;
  const viewBottom = viewTop + height;
  if (bottom > viewTop && top < viewBottom) return 'visible';
  const margin = height * lookahead;
  if (bottom > viewTop - margin && top < viewBottom + margin) return 'near';
  return 'far';
}

// Rows to request now: visible rows first, then near ones, each in page order.
// Rows already started are never repeated, and far rows wait.
export function rowsToStart(rowIds, proximity, started) {
  const byLevel = { visible: [], near: [] };
  (rowIds || []).forEach(id => {
    const key = String(id);
    if (started?.has(key)) return;
    const level = proximity?.get(key);
    if (level === 'visible' || level === 'near') byLevel[level].push(id);
  });
  return [...byLevel.visible, ...byLevel.near];
}
