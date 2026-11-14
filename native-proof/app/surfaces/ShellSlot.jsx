import React, { useEffect, useState } from 'react';
import { Platform, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useDevicePose } from '../data/useDevicePose';
import { useVerticalBarEdgeIOS } from 'expo-status-bar';
import { getActiveAuthSession, subscribeAuthSession } from '../data/authSession';
import { configureSidebarListPins, getSidebarListPins, removeSidebarListPin, subscribeSidebarListPins } from '../data/sidebarListPins';
import { getAuthState, signOut } from '../data/authTransport';
import { createOnboardingTransport, onboardingOffer } from '../data/onboardingTransport';
import { errorPageFor } from '../data/errorPages';
import { Button } from '../../../frontend/ui/Button';
import { ErrorBoundary } from '../screens/ErrorDocsScreen';
import OnboardingScreen from '../screens/OnboardingScreen';
import { Shell } from '../../../frontend/ui/Shell';
import { Sidebar } from '../../../frontend/ui/Sidebar';
import { renderNativeIcon } from '../iconRenderer';
import SearchSlot from './SearchSlot';
import { getShellPageType, subscribeShellPageType, ShellLayoutContext } from './shellContext';
import NativeShell from './NativeShell';
import { horizontalContentPadding } from '../safeAreaSpacing.mjs';
import { palette, radius } from '../theme';

export const surface = {
  id: 'SHELL',
  title: 'Application shell and navigation',
  where: 'Signed-in top bar, public header and mobile navigation',
  status: 'in_progress',
  note: 'The native shell mirrors the web compact layout: a top bar with the menu button and global search, and a navigation drawer built from /api/v1/user/navigation/, the same sidebar model the web template renders, including icons, order and saved views. While it loads the drawer shows a loading state, and a failed load shows the matching error with Retry; there is no placeholder navigation. The setup wizard follows the web onboarding state without adding a row to the reviewed drawer. The persistent desktop sidebar is not ported.',
};

const labels = {
  menu: 'Toggle navigation menu',
  closeMenu: 'Close navigation menu',
  navigation: 'Navigation',
  toggleTheme: 'Toggle theme',
  logo: 'Floppy',
};

const shellPalette = {
  text: palette.text,
  textSecondary: palette.textSecondary,
  textMuted: palette.textMuted,
  background: palette.background,
  sidebar: palette.sidebar,
  surface: palette.surface,
  surfaceStrong: palette.surfaceStrong,
  surfaceMuted: palette.surfaceStrong,
  surfaceHover: palette.surfaceStrong,
  hoverDim: 'rgba(255, 255, 255, 0.05)',
  border: palette.border,
  accent: palette.accent,
  accentStrong: palette.accentStrong,
  link: '#818cf8',
  danger: palette.danger,
  overlay: 'rgba(0, 0, 0, 0.55)',
  radius: radius.sm,
  focusStyle: {},
};

const SIDEBAR_LABELS = {
  logo: 'Floppy',
  explore: 'Explore',
  logout: 'Logout',
  pinnedLists: 'Pinned lists',
  navOptions: 'Navigation options',
  pin: 'Pin to sidebar',
  unpin: 'Unpin from sidebar',
  listOptions: 'List options',
  reorder: 'Reorder',
  delete: 'Delete',
  done: 'Done',
  closeMenu: 'Close menu',
  navigation: 'Navigation',
  savedViews: 'Saved %(name)s views',
  viewOptions: 'Options for %(name)s',
  confirmDelete: 'Delete the saved view "%(name)s"?',
};

// If an icon spec cannot render, a one-character fallback keeps the row
// readable instead of showing an identical bullet everywhere.
const GLYPHS = {
  Home: '\u2302', Discover: '\u25a6', 'TV Shows': '\u25a4', Movies: '\u25b6',
  Anime: '\u2726', Manga: '\u25a4', Games: '\u25c6', Books: '\u25a4',
  Comics: '\u25a4', 'Board Games': '\u25c6', Music: '\u266b', Podcasts: '\u25c9',
  'TV Seasons': '\u25a4', Videos: '\u25b6', Calendar: '\u25a6', Collection: '\u25a3',
  Custom: '\uff0b', History: '\u25f7', Lists: '\u2630', Statistics: '\u25a5',
  Tags: '#', Appearance: '\u263e', Settings: '\u2699', Logout: '\u21e5',
};

function withGlyphs(model) {
  const add = item => ({ ...item, glyph: item.glyph || GLYPHS[item.label] || undefined });
  return {
    ...model,
    primary: (model.primary || []).map(add),
    secondary: (model.secondary || []).map(add),
    bottom: model.bottom ? {
      ...model.bottom,
      appearance: add(model.bottom.appearance),
      settings: add(model.bottom.settings),
    } : model.bottom,
  };
}

function markActive(model, activeHref) {
  if (!activeHref) return model;
  const active = href => Boolean(href) && (href === activeHref
    || (href !== '/' && activeHref.startsWith(href)));
  const mark = item => ({
    ...item,
    active: active(item.href),
    views: (item.views || []).map(view => ({ ...view, current: view.href === activeHref })),
  });
  return {
    ...model,
    primary: (model.primary || []).map(mark),
    secondary: (model.secondary || []).map(mark),
    bottom: model.bottom ? {
      ...model.bottom,
      appearance: mark(model.bottom.appearance),
      settings: mark(model.bottom.settings),
    } : model.bottom,
  };
}

function absoluteLogo(model, serverUrl) {
  const src = model?.logo?.src;
  if (!src || !serverUrl || /^https?:\/\//.test(src)) return model;
  return { ...model, logo: { ...model.logo, src: `${serverUrl}${src}` } };
}

export default function ShellSlot({
  standalone = false,
  overlaySearch = false,
  active = 'home',
  activeHref = '/',
  canGoBack = false,
  onBack,
  onNavigate,
  onOpenHref,
  onOpenSearch,
  onOpenSuggestion,
  children,
}) {
  const [session, setSession] = useState(() => getActiveAuthSession());
  const [serverModel, setServerModel] = useState(null);
  const [modelSession, setModelSession] = useState(null);
  const [listPins, setListPins] = useState(() => getSidebarListPins().pins);
  const [navStatus, setNavStatus] = useState('loading');
  const [navError, setNavError] = useState(null);
  const [navAttempt, setNavAttempt] = useState(0);
  const [signOutError, setSignOutError] = useState('');
  const [, setOnboarding] = useState(null);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [open, setOpen] = useState(false);
  const [pageType, setPageType] = useState(getShellPageType);
  const [dismissSignal, setDismissSignal] = useState(0);
  // The web remembers the last searched type for pages with no type of their own.
  const [lastSubmittedType, setLastSubmittedType] = useState(null);
  const [pinned, setPinned] = useState([]);
  const [detailViewport, setDetailViewport] = useState({ width: 0, height: 0 });
  const [detailHeaderHeight, setDetailHeaderHeight] = useState(64);
  const pose = useDevicePose();
  const railEdge = useVerticalBarEdgeIOS();
  const insets = useSafeAreaInsets();

  useEffect(() => subscribeAuthSession(setSession), []);
  useEffect(() => subscribeSidebarListPins(state => setListPins(state.pins)), []);
  useEffect(() => {
    configureSidebarListPins(modelSession === session && serverModel?.userId
      ? { serverUrl: session.serverUrl, userId: serverModel.userId } : null);
    return () => { configureSidebarListPins(null); };
  }, [session, serverModel?.userId, modelSession]);
  useEffect(() => subscribeShellPageType(setPageType), []);
  // The suggestion list closes whenever the page changes.
  useEffect(() => { setDismissSignal(value => value + 1); }, [activeHref, pageType]);

  useEffect(() => {
    const auth = session;
    if (!auth || typeof auth.request !== 'function') {
      setServerModel(null);
      setNavStatus('loading');
      setPinned([]);
      return undefined;
    }
    let cancelled = false;
    // Saved views change on other devices, so read them again each time the menu opens;
    // a menu that is already drawn stays up while it refreshes.
    setNavStatus(current => (current === 'ready' ? current : 'loading'));
    auth.request('/api/v1/user/navigation/')
      .then(model => {
        if (cancelled) return;
        // A host that is not a Floppy server (for example Metro's own dev page) answers with HTML.
        if (!model || !Array.isArray(model.primary) || !Array.isArray(model.pinKeys)) {
          throw new Error('This address did not return a Floppy navigation menu.');
        }
        setServerModel(absoluteLogo(model, auth.serverUrl));
        setModelSession(auth);
        setNavError(null);
        setNavStatus('ready');
      })
      .catch(error => {
        if (cancelled) return;
        setNavError(error);
        setNavStatus('error');
      });
    return () => { cancelled = true; };
  }, [session, navAttempt, open]);

  // Guided setup: the same state the web uses for its "Finish setting up" chip.
  // It opens by itself only right after sign-up, which the auth state remembers.
  useEffect(() => {
    if (!session || typeof session.request !== 'function') return undefined;
    let cancelled = false;
    createOnboardingTransport({ auth: session }).load()
      .then(state => {
        if (cancelled || !state || state.local) return;
        setOnboarding(state);
        if (onboardingOffer(state, { justSignedUp: getAuthState().redirectTo === '/setup/' }).open) setWizardOpen(true);
      })
      .catch(() => { if (!cancelled) setOnboarding(null); });
    return () => { cancelled = true; };
  }, [session, open]);

  const closeWizard = () => {
    setWizardOpen(false);
    setOnboarding(current => (current ? { ...current, status: 'completed' } : current));
    // Media types may have changed, so the drawer is read again.
    setNavAttempt(value => value + 1);
    if (session && typeof session.request === 'function') {
      createOnboardingTransport({ auth: session }).load().then(setOnboarding).catch(() => {});
    }
  };

  const model = serverModel ? markActive(withGlyphs(serverModel), activeHref) : null;
  const routeMediaType = activeHref.match(/^\/medialist\/([^/?#]+)/)?.[1];
  // The type the user last picked wins on every screen (as on web); the page's own type is only the fallback.
  const searchType = lastSubmittedType || serverModel?.lastSearchType || pageType || routeMediaType || 'all';
  const pins = {
    pinned,
    pinnedLists: listPins,
    isPinned: key => pinned.includes(key),
    toggle: key => setPinned(current => (
      current.includes(key) ? current.filter(entry => entry !== key) : [...current, key]
    )),
    unpinList: id => {
      const entry = listPins.find(item => String(item.id) === String(id));
      if (entry) removeSidebarListPin(entry).catch(() => {});
    },
  };

  const openItem = item => {
    setOpen(false);
    if (item?.href) onOpenHref?.(item.href, item);
  };

  const navigationPadding = horizontalContentPadding(insets, 16);
  const adaptivePalette = Platform.OS === 'ios' && (pose.hasHinge || railEdge)
    ? { ...shellPalette, navRowHeight: 44, navContentPaddingTop: 25,
      navContentPaddingLeft: navigationPadding.paddingLeft,
      navContentPaddingRight: navigationPadding.paddingRight,
      navFontSize: 17, navIconSize: 22 }
    : shellPalette;
  const navPage = navError ? errorPageFor(navError) : null;
  // Keep Sidebar directly in the drawer so its flex layout fills the reviewed canvas.
  const sidebar = <>
    {navStatus === 'loading' ? (
      <Text accessibilityLiveRegion="polite" style={styles.navStatus}>Loading navigation…</Text>
    ) : null}
    {navStatus === 'error' ? (
      <View style={styles.navError} accessibilityRole="alert">
        <Text style={styles.navErrorTitle}>{navPage.heading}</Text>
        <Text style={styles.navErrorText}>{navPage.message}</Text>
        <Button label="Retry" tone="surface" onPress={() => setNavAttempt(value => value + 1)} />
      </View>
    ) : null}
    {model ? <Sidebar model={model} pins={pins} palette={adaptivePalette}
      labels={SIDEBAR_LABELS}
      renderIcon={renderNativeIcon}
      onNavigate={openItem}
      // Carve-out: web reveals the saved-views arrow on hover or on the current library; touch has no
      // hover, so native always shows it.
      rowActions="always"
      onLogout={() => {
        setOpen(false);
        setSignOutError('');
        signOut().catch(error => setSignOutError(error?.message || 'Could not sign out. Try again.'));
      }}
      showLogo={false} /> : null}
    {signOutError ? <Text accessibilityRole="alert" style={styles.navErrorText}>{signOutError}</Text> : null}
  </>;
  const content = wizardOpen
    ? <OnboardingScreen onFinish={closeWizard} onBack={closeWizard} />
    : <ErrorBoundary resetKey={activeHref} onGoHome={() => onNavigate?.('home')}>{children}</ErrorBoundary>;
  const search = <SearchSlot
    key={searchType}
    expanded
    initialMediaType={searchType}
    onTypeChange={setLastSubmittedType}
    dismissSignal={dismissSignal}
    onOpenSearch={params => {
      setDismissSignal(value => value + 1);
      if (params?.mediaType) setLastSubmittedType(params.mediaType);
      onOpenSearch?.(params);
    }}
    onOpenSuggestion={item => { setDismissSignal(value => value + 1); onOpenSuggestion?.(item); }}
  />;

  if (wizardOpen || standalone) return content;

  if (Platform.OS === 'ios') {
    return <NativeShell open={open} onToggle={() => setOpen(value => !value)} overlaySearch={overlaySearch}
      sidebar={sidebar} search={search} labels={labels} palette={shellPalette}
      canGoBack={canGoBack} onBack={() => { setOpen(false); onBack?.(); }}>
      {content}
    </NativeShell>;
  }

  const shell = <Shell
    open={open}
    onToggleMenu={() => setOpen(value => !value)}
    onCloseMenu={() => setOpen(false)}
    links={[]}
    variant="app"
    showMenu
    showSearch
    showThemeButton={false}
    search={search}
    labels={labels}
    palette={shellPalette}
  >
    {sidebar}
  </Shell>;
  if (!overlaySearch) return <>{shell}{content}</>;
  // Android also lets detail artwork continue under search; other pages keep the reviewed shell.
  return <View style={{ flex: 1 }} onLayout={event => setDetailViewport(event.nativeEvent.layout)}>
    <ShellLayoutContext.Provider value={{ ...detailViewport, insets, headerHeight: detailHeaderHeight }}>
      <View style={{ position: 'absolute', top: 0, left: 0, right: 0, zIndex: 30 }}
        onLayout={event => setDetailHeaderHeight(event.nativeEvent.layout.height)}>{shell}</View>
      {content}
    </ShellLayoutContext.Provider>
  </View>;
}

const styles = StyleSheet.create({
  navStatus: { color: palette.textMuted, fontSize: 13, lineHeight: 19 },
  navError: {
    borderWidth: 1,
    borderRadius: radius.md,
    padding: 12,
    gap: 6,
    borderColor: palette.danger,
  },
  navErrorTitle: { color: palette.danger, fontSize: 13, fontWeight: '700' },
  navErrorText: { color: palette.textSecondary, fontSize: 12, lineHeight: 17 },
});
