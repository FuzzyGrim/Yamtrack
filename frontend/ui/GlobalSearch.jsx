import React, { useEffect, useRef, useState } from 'react';
import {
  Image,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
  useWindowDimensions,
} from 'react-native';
import { DROPDOWN } from './dropdownMetrics';
import { DropdownItem } from './DropdownMenu';
import { Spinner } from './Spinner';
import { SearchIcon as SearchGlyph } from './Icon';

const MIN_QUERY_LENGTH = 2;

const defaultPalette = {
  text: '#fafafa',
  textSecondary: '#d1d5db',
  textMuted: '#adb5bd',
  panel: '#272c31',
  surface: '#2a2f35',
  surfaceMuted: '#343a40',
  surfaceAlt: '#3e454d',
  border: '#343a40',
  accent: '#4a9eff',
  errorSurface: '#2a2f35',
  errorBorder: '#4b5563',
  focusStyle: {},
  radius: 6,
  inputHeight: 36,
  iconSize: 20,
  fontSize: 14,
};

// The suggestions scroll in a window above a pinned "Search online" row. The window is as many
// rows as fit between the search bar and the bottom of what is visible (the keyboard is not
// visible), never fewer than two nor more than five. A row is its thumbnail plus padding.
const THUMB_HEIGHT = 40;
const SUGGESTION_ROW_HEIGHT = THUMB_HEIGHT + DROPDOWN.itemPaddingVertical * 2;
const SUGGESTION_FOOTER_HEIGHT = 44;
const SUGGESTION_PANEL_GAP = 4;
// A little air above the keyboard or the window edge; the window takes every whole row that fits.
const SUGGESTION_BOTTOM_AIR = 8;
const BLUR_CLOSE_MS = 250;
const SUGGESTION_ROWS_MIN = 2;
const SUGGESTION_ROWS_MAX = 5;

// A platform adapter supplies how far the keyboard covers the bottom of the window (the
// native keyboard, or the web visual viewport); without one nothing is covered.
function useSuggestionRows(open, anchorRef, useKeyboardOverlap) {
  const { height: windowHeight } = useWindowDimensions();
  const keyboardOverlap = useKeyboardOverlap();
  const [anchorBottom, setAnchorBottom] = useState(0);

  useEffect(() => {
    if (!open || !anchorRef.current?.measureInWindow) return;
    anchorRef.current.measureInWindow((_x, y, _width, height) => setAnchorBottom(y + height));
  }, [open, windowHeight, keyboardOverlap, anchorRef]);

  const bottom = windowHeight - keyboardOverlap;
  const room = bottom - anchorBottom - SUGGESTION_PANEL_GAP - SUGGESTION_FOOTER_HEIGHT - SUGGESTION_BOTTOM_AIR;
  const rows = Math.max(SUGGESTION_ROWS_MIN, Math.min(SUGGESTION_ROWS_MAX, Math.floor(room / SUGGESTION_ROW_HEIGHT)));
  return rows;
}

const noKeyboardOverlap = () => 0;

const label = (value, fallback) => (
  typeof value === 'string' && value.length ? value : fallback
);

function fill(template, values) {
  return String(template).replace(/%\((\w+)\)s/g, (match, key) => (
    Object.prototype.hasOwnProperty.call(values, key) ? String(values[key]) : match
  ));
}

function CameraGlyph({ color, size = 18 }) {
  const bodyHeight = Math.round(size * 0.7);
  const lens = Math.round(size * 0.36);
  return <View aria-hidden style={{ width: size, height: size }}>
    <View style={{
      position: 'absolute',
      left: 1,
      bottom: 1,
      width: size - 2,
      height: bodyHeight,
      borderRadius: 3,
      borderWidth: 2,
      borderColor: color,
    }} />
    <View style={{
      position: 'absolute',
      top: 0,
      left: Math.round(size * 0.28),
      width: Math.round(size * 0.3),
      height: 3,
      borderTopLeftRadius: 2,
      borderTopRightRadius: 2,
      backgroundColor: color,
    }} />
    <View style={{
      position: 'absolute',
      left: Math.round((size - lens) / 2),
      bottom: Math.round((bodyHeight - lens) / 2) + 1,
      width: lens,
      height: lens,
      borderRadius: lens / 2,
      borderWidth: 2,
      borderColor: color,
    }} />
  </View>;
}

function Chevron({ color, size = 14 }) {
  return <View aria-hidden style={{
    width: size * 0.55,
    height: size * 0.55,
    borderRightWidth: 2,
    borderBottomWidth: 2,
    borderColor: color,
    transform: [{ rotate: '45deg' }],
    marginTop: -3,
  }} />;
}

export function GlobalSearch({
  query: initialQuery = '',
  selectedType,
  mediaTypes = [],
  barcodeTypes = [],
  labels = {},
  palette: paletteOverrides,
  compact = false,
  expanded = false,
  dismissSignal = 0,
  onLoadSuggestions,
  useKeyboardOverlap = noKeyboardOverlap,
  onSubmit,
  onTypeChange,
  onSelectSuggestion,
  onBarcodeRequest,
}) {
  const palette = { ...defaultPalette, ...paletteOverrides };
  const { height: windowHeight } = useWindowDimensions();
  const barRef = useRef(null);
  const listRef = useRef(null);
  const listScrollY = useRef(0);
  const [query, setQuery] = useState(initialQuery);
  const [userTyped, setUserTyped] = useState(false);
  const [type, setType] = useState(selectedType || mediaTypes[0] || { display: 'All', value: 'all' });
  const [menuOpen, setMenuOpen] = useState(false);
  const [focused, setFocused] = useState(false);
  const blurTimer = useRef(null);
  useEffect(() => () => clearTimeout(blurTimer.current), []);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [panel, setPanel] = useState({ status: 'idle', suggestions: [] });
  const visibleRows = useSuggestionRows(panel.status !== 'idle', barRef, useKeyboardOverlap);
  const [barcodeBusy, setBarcodeBusy] = useState(false);
  const [barcodeError, setBarcodeError] = useState(null);
  const loader = useRef(onLoadSuggestions);
  const trimmed = query.trim();
  const isBarcodeType = barcodeTypes.includes(type.value);

  useEffect(() => { loader.current = onLoadSuggestions; }, [onLoadSuggestions]);
  useEffect(() => { setQuery(initialQuery || ''); setUserTyped(false); }, [initialQuery]);
  useEffect(() => {
    setMenuOpen(false);
    setPanel({ status: 'idle', suggestions: [] });
    setActiveIndex(-1);
  }, [dismissSignal]);

  useEffect(() => {
    setActiveIndex(-1);
    if (!userTyped || trimmed.length < MIN_QUERY_LENGTH || !loader.current) {
      setPanel({ status: 'idle', suggestions: [] });
      return undefined;
    }
    let cancelled = false;
    setPanel({ status: 'loading', suggestions: [] });
    const timer = setTimeout(() => {
      Promise.resolve()
        .then(() => loader.current({ query: trimmed, mediaType: type.value }))
        .then(result => {
          if (cancelled) return;
          const suggestions = Array.isArray(result?.suggestions) ? result.suggestions : [];
          setPanel({ status: suggestions.length ? 'ready' : 'empty', suggestions });
        })
        .catch(() => {
          if (!cancelled) setPanel({ status: 'error', suggestions: [] });
        });
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [trimmed, type.value, userTyped]);

  const footerOption = panel.status === 'ready' && panel.suggestions.length
    ? {
      key: 'online',
      id: 'global-search-suggestion-online',
      kind: 'footer',
      label: type.value === 'all'
        ? fill(label(labels.suggestionsSeeAll, 'See all library results for “%(query)s”'), { query: trimmed })
        : fill(label(labels.suggestionsSearchOnline, 'Search online for “%(query)s”'), { query: trimmed }),
    }
    : null;
  const suggestionOptions = panel.status === 'ready'
    ? panel.suggestions.map((suggestion, index) => ({
      key: suggestion.id ?? `${index}`,
      id: `global-search-suggestion-${suggestion.id ?? index}`,
      kind: 'suggestion',
      suggestion,
    }))
    : [];
  const options = footerOption ? [...suggestionOptions, footerOption] : suggestionOptions;
  const activeOption = activeIndex >= 0 ? options[activeIndex] : null;

  useEffect(() => {
    // The pinned footer never scrolls; a suggestion row is brought into the visible window.
    if (!activeOption || activeOption.kind !== 'suggestion' || !listRef.current) return;
    const top = activeIndex * SUGGESTION_ROW_HEIGHT;
    const view = visibleRows * SUGGESTION_ROW_HEIGHT;
    if (top < listScrollY.current) listRef.current.scrollTo({ y: top, animated: false });
    else if (top + SUGGESTION_ROW_HEIGHT > listScrollY.current + view) {
      listRef.current.scrollTo({ y: top + SUGGESTION_ROW_HEIGHT - view, animated: false });
    }
  }, [activeIndex, activeOption, visibleRows]);

  function placeholder() {
    if (type.value === 'all') return label(labels.placeholderAll, 'Search your whole library...');
    const template = compact
      ? label(labels.placeholderShort, 'Search %(media)s...')
      : label(labels.placeholderLong, 'Search %(media)s in your library or online...');
    return fill(template, { media: type.display });
  }

  function clearPanel() {
    // A request still in flight must not reopen the panel after Enter.
    setUserTyped(false);
    setPanel({ status: 'idle', suggestions: [] });
    setActiveIndex(-1);
  }

  function submit(value = query, mediaType = type.value) {
    const next = String(value || '').trim();
    if (!next) return;
    // Cancel any pending suggestion request so it cannot reopen the panel after the search runs.
    setUserTyped(false);
    clearPanel();
    onSubmit?.({ query: next, mediaType });
  }

  function choose(option) {
    if (!option) return;
    if (option.kind === 'footer') {
      submit();
      return;
    }
    setUserTyped(false);
    clearPanel();
    onSelectSuggestion?.(option.suggestion);
  }

  function selectType(next) {
    setType(next);
    onTypeChange?.(next.value);
    setMenuOpen(false);
    setActiveIndex(-1);
    if (trimmed) submit(query, next.value);
  }

  function handleKeyPress(event) {
    const key = event?.nativeEvent?.key ?? event?.key;
    if (key === 'ArrowDown') {
      if (!options.length) return;
      event.preventDefault?.();
      setActiveIndex(current => Math.min(current + 1, options.length - 1));
    } else if (key === 'ArrowUp') {
      if (!options.length) return;
      event.preventDefault?.();
      setActiveIndex(current => Math.max(current - 1, -1));
    } else if (key === 'Escape') {
      if (options.length || panel.status !== 'idle') {
        event.preventDefault?.();
        clearPanel();
      } else if (menuOpen) {
        event.preventDefault?.();
        setMenuOpen(false);
      }
    }
  }

  function handleSubmitEditing() {
    if (activeOption) choose(activeOption);
    else submit();
  }

  async function requestBarcode() {
    if (!onBarcodeRequest || barcodeBusy) return;
    setBarcodeBusy(true);
    setBarcodeError(null);
    try {
      const result = await onBarcodeRequest();
      if (!result || result.status === 'cancelled') return;
      if (result.status === 'decoded' && result.isbn) {
        setQuery(result.isbn);
        submit(result.isbn);
      } else if (result.status === 'unavailable') {
        setBarcodeError(label(labels.barcodeUnavailable, 'Barcode scanning is not available on this device. Enter an ISBN instead.'));
      } else {
        setBarcodeError(label(labels.barcodeUnreadable, 'Could not read barcode from image. Please try again or enter ISBN manually.'));
      }
    } catch (error) {
      setBarcodeError(label(labels.barcodeFailed, 'Error scanning barcode: ')
        + (error?.message || label(labels.tryAgain, 'Please try again.')));
    } finally {
      setBarcodeBusy(false);
    }
  }

  function dismissBarcodeError() {
    setBarcodeError(null);
  }

  const showPanel = panel.status !== 'idle';

  return <View style={[styles.root, expanded && styles.expanded]}>
    <View style={styles.row}>
      <View ref={barRef} style={styles.inputWrap}>
        <Pressable accessibilityRole="button" accessibilityLabel={label(labels.search, 'Search')}
          onPress={() => submit()} style={[styles.searchButton, palette.searchTouchWidth && {
            left: 0, width: palette.searchTouchWidth, alignItems: 'center',
          }]}>
          <SearchGlyph color={palette.textMuted} size={palette.iconSize} />
        </Pressable>
        <TextInput
          nativeID="global-search"
          accessibilityLabel={label(labels.inputLabel, 'Search your library or online')}
          value={query}
          onChangeText={value => { setUserTyped(true); setQuery(value); }}
          placeholder={placeholder()}
          placeholderTextColor={palette.textMuted}
          autoComplete="off"
          autoCorrect={false}
          autoCapitalize="none"
          role="combobox"
          aria-autocomplete="list"
          aria-expanded={options.length > 0}
          aria-controls="global-search-suggestions"
          aria-activedescendant={activeOption?.id}
          returnKeyType="search"
          onFocus={() => { clearTimeout(blurTimer.current); setFocused(true); }}
          onBlur={() => {
            setFocused(false);
            // Leaving the field closes the suggestions, after a beat so a tap on one still lands.
            blurTimer.current = setTimeout(clearPanel, BLUR_CLOSE_MS);
          }}
          onKeyPress={handleKeyPress}
          onSubmitEditing={handleSubmitEditing}
          style={[
            styles.input,
            {
              color: palette.textSecondary,
              minHeight: palette.inputHeight,
              paddingLeft: palette.searchTouchWidth || 40,
              backgroundColor: palette.panel,
              borderTopLeftRadius: palette.radius,
              borderBottomLeftRadius: palette.radius,
              borderTopRightRadius: 0,
              borderBottomRightRadius: 0,
              paddingRight: isBarcodeType ? 40 : 16,
              // No focus ring: the caret and the open suggestions show where typing goes.
              outlineStyle: 'none',
            },
          ]}
        />
        {isBarcodeType ? <Pressable accessibilityRole="button"
          accessibilityLabel={label(labels.scan, 'Scan barcode')}
          accessibilityState={{ disabled: barcodeBusy }}
          disabled={barcodeBusy}
          onPress={requestBarcode}
          style={[styles.barcodeButton, barcodeBusy && styles.disabled]}>
          {barcodeBusy
            ? <Spinner size={20} color={palette.textMuted} />
            : <CameraGlyph color={palette.textMuted} size={palette.iconSize} />}
        </Pressable> : null}
        {showPanel ? <View nativeID="global-search-suggestions" role="listbox"
          accessibilityLabel={label(labels.suggestions, 'Search suggestions')}
          style={{
            position: 'absolute',
            top: palette.inputHeight,
            left: 0,
            right: 0,
            zIndex: 50,
            marginTop: 4,
            borderWidth: 1,
            borderColor: palette.border,
            borderRadius: DROPDOWN.radius,
            paddingVertical: DROPDOWN.paddingVertical,
            backgroundColor: palette.surface,
            elevation: 8,
            shadowColor: '#000',
            shadowOpacity: 0.25,
            shadowRadius: 12,
            shadowOffset: { width: 0, height: 6 },
          }}>
          <ScrollView ref={listRef} keyboardShouldPersistTaps="handled" nestedScrollEnabled
            style={{ maxHeight: visibleRows * SUGGESTION_ROW_HEIGHT }} scrollEventThrottle={16}
            onScroll={event => { listScrollY.current = event.nativeEvent.contentOffset.y; }}>
            {panel.status === 'loading' ? <View style={styles.stateRow}>
              <Spinner size={16} color={palette.textMuted} />
              <Text accessibilityLiveRegion="polite" style={[styles.stateText, { color: palette.textMuted }]}>
                {label(labels.suggestionsLoading, 'Loading suggestions…')}
              </Text>
            </View> : null}
            {panel.status === 'error' ? <View style={styles.stateRow}>
              <Text accessibilityLiveRegion="polite" style={[styles.stateText, { color: palette.textMuted }]}>
                {label(labels.suggestionsError, 'Suggestions could not load. Keep typing or press Enter.')}
              </Text>
            </View> : null}
            {panel.status === 'empty' ? <View style={styles.stateRow}>
              <Text accessibilityLiveRegion="polite" style={[styles.stateText, { color: palette.textMuted }]}>
                {type.value === 'all'
                  ? fill(label(labels.suggestionsEmptyAll, 'Nothing in your library matches “%(query)s”. Pick a media type to search online.'), { query: trimmed })
                  : fill(label(labels.suggestionsEmptyType, 'Nothing saved matches “%(query)s”. Keep typing and press Enter to search online.'), { query: trimmed })}
              </Text>
            </View> : null}
            {options.map((option, index) => {
              const isActive = index === activeIndex;
              if (option.kind === 'footer') return null;
              const suggestion = option.suggestion;
              return <Pressable key={option.key} nativeID={option.id}
                role="option" aria-selected={isActive}
                accessibilityRole="link"
                accessibilityLabel={[suggestion.title, suggestion.subtitle].filter(Boolean).join(', ')}
                onPress={() => choose(option)}
                style={({ hovered, pressed }) => [styles.option,
                  (hovered || pressed || isActive) && { backgroundColor: palette.surfaceMuted }]}>
                {suggestion.image
                  ? <Image accessible={false} source={{ uri: suggestion.image }}
                    resizeMode="cover" style={[styles.thumb, { backgroundColor: palette.surfaceAlt }]} />
                  : <View style={[styles.thumb, styles.thumbFallback, { backgroundColor: palette.surfaceAlt }]}>
                    <SearchGlyph color={palette.textMuted} size={14} />
                  </View>}
                <View style={styles.optionText}>
                  <Text numberOfLines={1} style={[styles.optionTitle, { color: palette.textSecondary }]}>{suggestion.title}</Text>
                  {suggestion.subtitle ? <Text numberOfLines={1}
                    style={[styles.optionSubtitle, { color: palette.textMuted }]}>{suggestion.subtitle}</Text> : null}
                </View>
              </Pressable>;
            })}
          </ScrollView>
          {footerOption ? <Pressable nativeID={footerOption.id}
            role="option" aria-selected={activeIndex === options.length - 1}
            accessibilityRole="button"
            accessibilityLabel={footerOption.label}
            onPress={() => choose(footerOption)}
            style={({ hovered }) => [styles.footerRow,
              { borderTopColor: palette.border },
              (hovered || activeIndex === options.length - 1) && { backgroundColor: palette.surfaceMuted }]}>
            <SearchGlyph color={palette.textMuted} size={14} />
            <Text numberOfLines={1} style={[styles.footerText, { color: palette.textMuted }]}>{footerOption.label}</Text>
            <Text style={[styles.keycap, { color: palette.textMuted, borderColor: palette.border }]}>Enter</Text>
          </Pressable> : null}
        </View> : null}
      </View>
      <Pressable testID="search-media-type-menu" accessibilityRole="button" accessibilityLabel={type.display}
        aria-haspopup="listbox" aria-expanded={menuOpen}
        onPress={() => setMenuOpen(open => !open)}
        style={({ hovered, pressed }) => [styles.typeButton, {
          backgroundColor: (hovered || pressed) ? palette.surface : palette.panel,
          borderTopRightRadius: palette.radius,
          borderBottomRightRadius: palette.radius,
          borderTopLeftRadius: 0,
          borderBottomLeftRadius: 0,
        }]}>
        <Text numberOfLines={1} style={[styles.typeText, { color: palette.textSecondary }]}>{type.display}</Text>
        <Chevron color={palette.textMuted} size={14} />
      </Pressable>
      {menuOpen ? <View style={[styles.menu, {
        top: palette.inputHeight + 12,
        // Same size as the sidebar row options; scrolls when the window is too short for every type.
        width: palette.menuWidth ?? DROPDOWN.width,
        maxHeight: Math.max(windowHeight - palette.inputHeight - 36, DROPDOWN.itemPaddingVertical * 6),
        backgroundColor: palette.surface,
        borderColor: palette.border,
        borderRadius: DROPDOWN.radius,
      }]}>
        <ScrollView keyboardShouldPersistTaps="handled" nestedScrollEnabled>
          {mediaTypes.map(option => <DropdownItem key={option.value} accessibilityRole="button" label={option.display}
            numberOfLines={1} onPress={() => selectType(option)} selected={option.value === type.value}
            palette={palette} />)}
        </ScrollView>
      </View> : null}
    </View>
    {barcodeError ? <View style={[styles.toast, {
      backgroundColor: palette.errorSurface,
      borderColor: palette.errorBorder,
      borderRadius: palette.radius,
    }]}>
      <Text accessibilityLiveRegion="polite" role="alert"
        style={[styles.toastText, { color: palette.textSecondary }]}>{barcodeError}</Text>
      <Pressable accessibilityRole="button" accessibilityLabel={label(labels.dismiss, 'Dismiss error')}
        onPress={dismissBarcodeError} style={styles.toastDismiss}>
        <Text aria-hidden style={[styles.toastClose, { color: palette.textMuted }]}>×</Text>
      </Pressable>
    </View> : null}
  </View>;
}

const styles = StyleSheet.create({
  root: { position: 'relative', zIndex: 30, maxWidth: 672 },
  expanded: { width: '100%', maxWidth: undefined },
  row: { flexDirection: 'row', alignItems: 'stretch', position: 'relative' },
  inputWrap: { flex: 1, minWidth: 0, position: 'relative' },
  input: { minHeight: 36, paddingTop: 8, paddingBottom: 8, paddingLeft: 40, fontSize: 14 },
  searchButton: { position: 'absolute', left: 8, top: 0, bottom: 0, zIndex: 2, justifyContent: 'center' },
  barcodeButton: { position: 'absolute', right: 8, top: 0, bottom: 0, zIndex: 2, justifyContent: 'center' },
  disabled: { opacity: 0.5 },
  typeButton: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 16, paddingVertical: 8 },
  typeText: { fontSize: 14, flexShrink: 1 },
  menu: { position: 'absolute', top: 48, right: 0, zIndex: 60, borderWidth: 1, paddingVertical: 4, elevation: 6, shadowColor: '#000', shadowOpacity: 0.3, shadowRadius: 8, shadowOffset: { width: 0, height: 4 } },
  option: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: DROPDOWN.itemPaddingHorizontal, paddingVertical: DROPDOWN.itemPaddingVertical },
  optionText: { flex: 1, minWidth: 0 },
  optionTitle: { fontSize: 14 },
  optionSubtitle: { fontSize: 12, marginTop: 1 },
  thumb: { width: 32, height: THUMB_HEIGHT, borderRadius: 4, flexShrink: 0 },
  thumbFallback: { alignItems: 'center', justifyContent: 'center' },
  footerRow: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: DROPDOWN.itemPaddingHorizontal, paddingVertical: DROPDOWN.itemPaddingVertical, borderTopWidth: 1 },
  footerText: { flex: 1, minWidth: 0, fontSize: 12 },
  keycap: { fontSize: 11, borderWidth: 1, borderRadius: 3, paddingHorizontal: 6, paddingVertical: 1 },
  stateRow: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 12, paddingVertical: 12 },
  stateText: { flex: 1, fontSize: 14, lineHeight: 20 },
  toast: { flexDirection: 'row', alignItems: 'center', gap: 8, borderWidth: 1, paddingHorizontal: 12, paddingVertical: 8, marginTop: 8, maxWidth: 672, elevation: 6, shadowColor: '#000', shadowOpacity: 0.3, shadowRadius: 8, shadowOffset: { width: 0, height: 4 } },
  toastText: { flex: 1, fontSize: 13, fontWeight: '500' },
  toastDismiss: { padding: 2 },
  toastClose: { fontSize: 18, lineHeight: 18 },
});
