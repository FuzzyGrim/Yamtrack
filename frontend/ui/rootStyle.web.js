// Theme tokens are CSS custom properties on <html>. Reading one through
// getComputedStyle makes the browser recompute styles for the whole page if the
// DOM changed since the last read. Islands mount one after another in the same
// task, each inserting nodes and then reading the palette, so every island paid
// a full-page style recalculation (Statistics mounts fifteen islands; that was
// over half of its mount time).
//
// rootStyle() returns the same values as getComputedStyle(document.documentElement)
// for the properties the theme adapters read, remembered until the theme can
// have changed: the end of the current task, any attribute change on <html>
// (theme class, inline style, font size), a stylesheet added or removed in
// <head>, or a colour-scheme change.

let cached = null;
let watching = false;

function forget() {
  cached = null;
}

function watch() {
  if (watching) return;
  watching = true;
  if (typeof MutationObserver === 'function') {
    new MutationObserver(forget).observe(document.documentElement, { attributes: true });
    if (document.head) {
      new MutationObserver(forget).observe(document.head, {
        childList: true,
        subtree: true,
        characterData: true,
        attributes: true,
      });
    }
  }
  window.matchMedia?.('(prefers-color-scheme: dark)')?.addEventListener?.('change', forget);
}

export function rootStyle() {
  watch();
  if (cached) return cached;
  const live = getComputedStyle(document.documentElement);
  const values = new Map();
  const read = (name, get) => {
    if (!values.has(name)) values.set(name, get());
    return values.get(name);
  };
  cached = {
    getPropertyValue: name => read(name, () => live.getPropertyValue(name)),
    get fontSize() { return read(' fontSize', () => live.fontSize); },
    get fontFamily() { return read(' fontFamily', () => live.fontFamily); },
  };
  // Whatever happens after this task (a theme toggle, a late stylesheet) is
  // read fresh; only reads within one burst of mounting share a snapshot.
  const snapshot = cached;
  setTimeout(() => {
    if (cached === snapshot) cached = null;
  }, 0);
  return cached;
}
