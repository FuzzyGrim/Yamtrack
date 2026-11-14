import { createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { flushSync } from 'react-dom';
import { KeyboardOverlapContext } from '../ui/keyboardOverlapContext';
import { useKeyboardOverlap } from '../ui/keyboardOverlap.web';

// React Native Web gives every Text and TextInput its own system font stack
// (-apple-system, Segoe UI, Roboto...). The release pages inherit the Tailwind
// body stack (ui-sans-serif, system-ui...), so React text looked different on
// Windows and Linux. Point RNW's default rules at the page font; explicit
// fontFamily styles (monospace code, for example) use their own classes and win.
const INHERIT_FONT_RULES = new Set(['.css-146c3p1', '.css-11aywtz']);
const patchedFontRules = new Set();

function inheritPageFont() {
  if (patchedFontRules.size === INHERIT_FONT_RULES.size) return;
  const sheet = document.getElementById('react-native-stylesheet')?.sheet;
  if (!sheet) return;
  for (const rule of sheet.cssRules) {
    if (INHERIT_FONT_RULES.has(rule.selectorText) && !patchedFontRules.has(rule.selectorText)) {
      rule.style.fontFamily = 'inherit';
      patchedFontRules.add(rule.selectorText);
    }
  }
}

export function mountRoot(host, element) {
  const fallback = [...host.childNodes].map((node) => node.cloneNode(true));
  const root = createRoot(host);
  try {
    flushSync(() => root.render(createElement(KeyboardOverlapContext.Provider, { value: useKeyboardOverlap }, element)));
  } catch (error) {
    root.unmount();
    host.replaceChildren(...fallback);
    throw error;
  }
  inheritPageFont();
  host.dataset.reactMounted = 'true';
  return () => {
    root.unmount();
    host.replaceChildren(...fallback);
    delete host.dataset.reactMounted;
  };
}
