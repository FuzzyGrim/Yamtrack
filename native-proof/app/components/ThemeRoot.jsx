import React, { useEffect, useLayoutEffect, useState } from 'react';
import { useColorScheme } from 'react-native';
import { getActiveAuthSession, subscribeAuthSession } from '../data/authSession';
import { createSettingsTransport } from '../data/settingsTransport';
import { useKeyboardOverlap } from '../data/useKeyboardOverlap';
import PoseBadge from './PoseBadge';
import { DropdownInsetsContext, KeyboardOverlapContext } from '../../../frontend/ui/keyboardOverlapContext';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { applyPlatformTheme } from '../themeColor';
import { applyServerTheme, restoreStoredTheme } from '../themeSync';
import { getTheme, setSystemScheme, subscribeTheme } from '../themeState';

// Applies the user's theme to the running app: native chrome and CSS variables
// immediately. Colours that are literals (see themeColor*.js) reach the screens
// through a re-render of the app root (routes/index.jsx subscribes to the theme),
// not a remount, so navigation and unsaved state survive a theme change.
// Mount once, above every screen.
export default function ThemeRoot({ children }) {
  const scheme = useColorScheme();
  const insets = useSafeAreaInsets();
  const [resolved, setResolved] = useState(getTheme);

  useEffect(() => subscribeTheme(setResolved), []);
  useEffect(() => { setSystemScheme(scheme); }, [scheme]);
  useLayoutEffect(() => { applyPlatformTheme(resolved); }, [resolved]);

  useEffect(() => {
    restoreStoredTheme();
    let active = true;
    function syncFromServer(session) {
      if (!session || typeof session.request !== 'function') return;
      createSettingsTransport({ auth: session }).loadAppearance()
        .then(payload => { if (active) applyServerTheme(payload); })
        .catch(() => {});
    }
    syncFromServer(getActiveAuthSession());
    const off = subscribeAuthSession(syncFromServer);
    return () => { active = false; off(); };
  }, []);

  // Menus size themselves to what the keyboard leaves visible, like the Search suggestions.
  // Menus also stay out of the safe area, so one opened near the iOS side rail is placed beside it, not under it.
  return <KeyboardOverlapContext.Provider value={useKeyboardOverlap}>
    <DropdownInsetsContext.Provider value={insets}>{children}<PoseBadge /></DropdownInsetsContext.Provider>
  </KeyboardOverlapContext.Provider>;
}
