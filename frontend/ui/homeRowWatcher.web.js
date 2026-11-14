import { ROW_LOOKAHEAD_SCREENS } from './homeRowLoading.js';

// Platform difference: the browser reports row positions with
// IntersectionObserver; native measures rows on scroll (native-proof
// homeRowWatcherCore.js). Both feed the same shared load decision in HomeShelves.

function scrollParent(node) {
  for (let current = node?.parentElement; current; current = current.parentElement) {
    const { overflowY } = getComputedStyle(current);
    if ((overflowY === 'auto' || overflowY === 'scroll') && current.scrollHeight > current.clientHeight) {
      return current;
    }
  }
  return null;
}

// The visible height the look-ahead is measured in: the scroll panel's, or the window's.
function viewHeight(root) {
  return root ? root.clientHeight : window.innerHeight;
}

// IntersectionObserver resolves a percentage margin against the root's width,
// so a 1.5-screen look-ahead has to be a pixel margin taken from its height.
function lookaheadMargin(root, lookahead) {
  return `${Math.round(viewHeight(root) * lookahead)}px 0px`;
}

// Returns `watchRow(id, node, onChange)` for HomeShelves, or undefined when the
// browser has no IntersectionObserver (HomeShelves then loads every row).
export function createViewportRowWatcher({ lookahead = ROW_LOOKAHEAD_SCREENS } = {}) {
  if (typeof window === 'undefined' || typeof window.IntersectionObserver !== 'function') return undefined;
  const observers = new Map();
  const watched = new Map();

  function report(node) {
    const entry = watched.get(node);
    if (!entry) return;
    const level = entry.visible ? 'visible' : entry.near ? 'near' : 'far';
    if (level === entry.level) return;
    entry.level = level;
    entry.onChange(level);
  }

  const update = flag => entries => entries.forEach(item => {
    const entry = watched.get(item.target);
    if (!entry) return;
    entry[flag] = item.isIntersecting;
    report(item.target);
  });

  function makeNear(root) {
    return new window.IntersectionObserver(update('near'), {
      root,
      rootMargin: lookaheadMargin(root, lookahead),
    });
  }

  function observersFor(root) {
    if (observers.has(root)) return observers.get(root);
    const pair = {
      visible: new window.IntersectionObserver(update('visible'), { root }),
      near: makeNear(root),
      height: viewHeight(root),
      nodes: new Set(),
    };
    observers.set(root, pair);
    return pair;
  }

  // A resize (rotation, window drag, the phone's address bar) changes the
  // height the margin is measured in; rebuild the near observers to match.
  let resizeTimer = null;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      observers.forEach((pair, root) => {
        const height = viewHeight(root);
        if (Math.abs(height - pair.height) < 1) return;
        pair.height = height;
        pair.near.disconnect();
        pair.near = makeNear(root);
        pair.nodes.forEach(node => pair.near.observe(node));
      });
    }, 150);
  });

  return function watchRow(_id, node, onChange) {
    if (!node || typeof node.getBoundingClientRect !== 'function') return undefined;
    // Home scrolls with the page today; if a layout puts it in a scrolling
    // panel, the look-ahead margin must apply to that panel.
    const pair = observersFor(scrollParent(node));
    watched.set(node, { onChange, visible: false, near: false, level: null });
    pair.nodes.add(node);
    pair.near.observe(node);
    pair.visible.observe(node);
    return () => {
      pair.nodes.delete(node);
      pair.near.unobserve(node);
      pair.visible.unobserve(node);
      watched.delete(node);
    };
  };
}
