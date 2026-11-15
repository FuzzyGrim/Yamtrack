import { Appearance } from 'react-native';
import { setSystemScheme } from './themeState';

// Android and any platform without a dynamic colour: values are literals.
// Screens copy them into their styles when they first load, before ThemeRoot
// mounts, so start from the device's own light or dark setting instead of
// the dark default. Otherwise a phone in light mode keeps dark backgrounds
// behind light-mode text.
setSystemScheme(Appearance.getColorScheme?.());

export function liveKeysFor() {
  return [];
}

export function themeColor(key, resolved) {
  return resolved.tokens[key];
}

// Keeps native chrome (status bar, keyboards, alerts) in the chosen scheme.
export function applyPlatformTheme(resolved) {
  if (typeof Appearance.setColorScheme === 'function') Appearance.setColorScheme(resolved.override);
}
