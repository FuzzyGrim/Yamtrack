import { rowProximity } from '../../../frontend/ui/homeRowLoading.js';

// Platform difference: native Home is a plain ScrollView with no intersection
// API, so waiting rows are measured on scroll and layout changes; the browser
// uses IntersectionObserver (frontend/ui/homeRowWatcher.web.js). Both feed the
// same shared load decision in HomeShelves.
//
// `measure(node)` resolves to { y, width, height } in window coordinates, or
// null. A node that has not been laid out yet (zero size) is skipped until the
// next check. A measure that never answers (a view torn down mid-measure)
// counts as null after `measureTimeoutMs`, so one lost answer cannot stall
// every later check.
export const MEASURE_TIMEOUT_MS = 500;

function withTimeout(promise, ms) {
  let timer;
  return Promise.race([
    promise,
    new Promise(resolve => { timer = setTimeout(() => resolve(null), ms); }),
  ]).finally(() => clearTimeout(timer));
}

export function createMeasuredRowWatcher({ measure, viewportHeight = 0, measureTimeoutMs = MEASURE_TIMEOUT_MS }) {
  const rows = new Map();
  let height = viewportHeight;
  let pass = null;
  let again = false;

  async function runPass() {
    do {
      again = false;
      const entries = [...rows.values()];
      const rects = await Promise.all(entries.map(entry => withTimeout(
        Promise.resolve().then(() => measure(entry.node)).catch(() => null),
        measureTimeoutMs,
      )));
      // Report the whole batch together so rows on screen are started first.
      entries.forEach((entry, index) => {
        const rect = rects[index];
        if (!rect || rows.get(entry.node) !== entry) return;
        if (!(rect.width > 0) && !(rect.height > 0)) return;
        const level = rowProximity({ top: rect.y, bottom: rect.y + rect.height }, { top: 0, height });
        if (level === entry.level) return;
        entry.level = level;
        entry.onChange(level);
      });
    } while (again);
  }

  function check() {
    if (pass) {
      again = true;
      return pass;
    }
    pass = runPass().finally(() => { pass = null; });
    return pass;
  }

  return {
    watchRow(_id, node, onChange) {
      if (!node) return undefined;
      const entry = { node, onChange, level: null };
      rows.set(node, entry);
      check();
      return () => {
        if (rows.get(node) === entry) rows.delete(node);
      };
    },
    check,
    setViewportHeight(next) {
      if (!(next > 0) || next === height) return;
      height = next;
      check();
    },
    get size() {
      return rows.size;
    },
  };
}

export function measureInWindow(node) {
  return new Promise(resolve => {
    if (typeof node?.measureInWindow !== 'function') {
      resolve(null);
      return;
    }
    node.measureInWindow((x, y, width, height) => resolve({ x, y, width, height }));
  });
}
