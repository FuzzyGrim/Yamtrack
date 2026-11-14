import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Platform, useWindowDimensions } from 'react-native';
import { GlobalSearch } from '../../../frontend/ui/GlobalSearch.jsx';
import { useKeyboardOverlap } from '../data/useKeyboardOverlap';
import { BarcodeScannerModal } from '../components/BarcodeScannerModal';
import { createSearchTransport, suggestionsFromPayload } from '../data/searchTransport';
import { getActiveAuthSession, subscribeAuthSession } from '../data/authSession';
import { palette, radius } from '../theme';

export const surface = {
  id: 'SEARCH',
  title: 'Global search and suggestions',
  where: 'Header on Home, Discover and Library, and the Search screen',
  status: 'in_progress',
  note: 'The shared React Native search field renders here with the reviewed media-type menu, barcode types and copy, two-character suggestions from /api/v1/search/suggestions/ (saved titles, collected and tagged items, matched titles), the search-all/search-online footer, loading/empty/error rows and the search, suggestion and barcode callbacks. Book, Manga, Comic and Comic Issue scopes open the Expo camera/photo/manual-ISBN sheet. Signed-out suggestions are empty; there is no sample content. Platform verification is recorded in the UI register.',
};

const mediaTypes = [
  { display: 'All', value: 'all' },
  { display: 'TV Shows', value: 'tv' },
  { display: 'Movies', value: 'movie' },
  { display: 'Anime', value: 'anime' },
  { display: 'Manga', value: 'manga' },
  { display: 'Games', value: 'game' },
  { display: 'Books', value: 'book' },
  { display: 'Comics', value: 'comic' },
  { display: 'Board Games', value: 'boardgame' },
  { display: 'Music', value: 'music' },
  { display: 'Podcasts', value: 'podcast' },
  { display: 'Videos', value: 'video' },
];

const labels = {
  inputLabel: 'Search your library or online',
  search: 'Search',
  scan: 'Scan barcode',
  dismiss: 'Dismiss error',
  placeholderAll: 'Search your whole library...',
  placeholderShort: 'Search %(media)s...',
  placeholderLong: 'Search %(media)s in your library or online...',
  suggestions: 'Search suggestions',
  suggestionsLoading: 'Loading suggestions…',
  suggestionsError: 'Suggestions could not load. Keep typing or press Enter.',
  suggestionsSeeAll: 'See all library results for “%(query)s”',
  suggestionsSearchOnline: 'Search online for “%(query)s”',
  suggestionsEmptyAll: 'Nothing in your library matches “%(query)s”. Pick a media type to search online.',
  suggestionsEmptyType: 'Nothing saved matches “%(query)s”. Keep typing and press Enter to search online.',
  barcodeUnavailable: 'Barcode scanning is not available on this device. Enter an ISBN instead.',
  barcodeUnreadable: 'Could not read a barcode from that image. Try again or type the ISBN.',
  barcodeFailed: 'Error scanning barcode: ',
  tryAgain: 'Please try again.',
  barcodeTitle: 'Scan a book barcode',
  barcodeHelp: 'Point the camera at the ISBN barcode, or choose a photo. You can also type the ISBN.',
  barcodePermissionTitle: 'Camera access needed',
  barcodePermissionBody: 'Allow camera access to scan the barcode, or choose a photo or type the ISBN below.',
  barcodeAllowCamera: 'Allow camera',
  barcodeOpenSettings: 'Open Settings',
  barcodeChoosePhoto: 'Choose photo',
  barcodeManualLabel: 'ISBN',
  barcodeManualPlaceholder: 'Type an ISBN-10 or ISBN-13',
  barcodeManualAction: 'Use ISBN',
  barcodeCancel: 'Cancel',
  barcodeChecking: 'Reading barcode…',
  barcodeInvalidManual: 'Enter a valid ISBN-10 or ISBN-13.',
  barcodeNotIsbn: 'That barcode is not an ISBN. Try a book barcode.',
  barcodePhotoUnreadable: 'Could not read a barcode from that photo. Try again or type the ISBN.',
  barcodeCameraError: 'The camera could not start. Choose a photo or type the ISBN.',
};

const searchPalette = {
  text: palette.text,
  textSecondary: palette.textSecondary,
  textMuted: palette.textMuted,
  panel: palette.surface,
  surface: palette.surface,
  surfaceMuted: palette.surfaceStrong,
  surfaceAlt: palette.surfaceStrong,
  border: palette.border,
  accent: palette.accent,
  errorSurface: palette.surface,
  errorBorder: palette.surfaceMuted,
  radius: radius.sm,
  focusStyle: Platform.OS === 'ios' ? { outlineColor: 'transparent' } : {},
  ...(Platform.OS === 'ios' ? { inputHeight: 44, searchTouchWidth: 44 } : {}),
};

export default function SearchSlot({
  expanded = false,
  initialQuery = '',
  initialMediaType = 'all',
  onOpenSearch,
  onOpenSuggestion,
  onSubmit,
  onTypeChange,
  dismissSignal = 0,
}) {
  const { width } = useWindowDimensions();
  const [session, setSession] = useState(() => getActiveAuthSession());
  useEffect(() => subscribeAuthSession(setSession), []);
  const transport = useMemo(
    () => createSearchTransport(session ? { auth: session } : {}),
    [session],
  );
  const compact = width < 640;
  const [scannerVisible, setScannerVisible] = useState(false);
  const pendingBarcode = useRef(null);

  const selectedType = mediaTypes.find(option => option.value === initialMediaType) || mediaTypes[0];

  const requestBarcode = useCallback(() => new Promise(resolve => {
    pendingBarcode.current = resolve;
    setScannerVisible(true);
  }), []);

  const closeScanner = useCallback((result) => {
    const resolve = pendingBarcode.current;
    pendingBarcode.current = null;
    setScannerVisible(false);
    resolve?.(result);
  }, []);

  const loadSuggestions = useCallback(async ({ query, mediaType }) => {
    if (transport.mode !== 'server') return { suggestions: [] };
    // A session sign-in uses the web typeahead; a token sign-in reads the same one from the API.
    if (transport.searchSuggestions) {
      return { suggestions: await transport.searchSuggestions({ search: query, mediaType }) };
    }
    const payload = await transport.loadSuggestions({ query, mediaType });
    return { suggestions: suggestionsFromPayload(payload, session?.serverUrl || '') };
  }, [transport, session]);

  // Remember the picked type on the server (as the web does) and in the shell.
  function changeType(mediaType) {
    onTypeChange?.(mediaType);
    transport.saveType?.({ mediaType })?.catch?.(() => {});
  }

  function openSuggestion(suggestion) {
    // Typeahead rows without an item identity (artists, podcast shows) open as a search.
    const typeaheadWithoutItem = Boolean(suggestion.href) && !suggestion.identity;
    if (onOpenSuggestion && !(typeaheadWithoutItem && onOpenSearch)) onOpenSuggestion(suggestion);
    else onOpenSearch?.({ query: suggestion.title, mediaType: suggestion.mediaType || initialMediaType });
  }

  return <>
    <GlobalSearch
      useKeyboardOverlap={useKeyboardOverlap}
      expanded={expanded}
      dismissSignal={dismissSignal}
      compact={compact}
      query={initialQuery}
      selectedType={selectedType}
      onTypeChange={changeType}
      mediaTypes={mediaTypes}
      barcodeTypes={['all', 'book', 'manga', 'comic', 'comicissue']}
      labels={labels}
      palette={searchPalette}
      onLoadSuggestions={loadSuggestions}
      onSubmit={onSubmit || onOpenSearch}
      onSelectSuggestion={openSuggestion}
      onBarcodeRequest={requestBarcode}
    />
    <BarcodeScannerModal
      visible={scannerVisible}
      labels={{
        title: labels.barcodeTitle,
        help: labels.barcodeHelp,
        cameraPermissionTitle: labels.barcodePermissionTitle,
        cameraPermissionBody: labels.barcodePermissionBody,
        allowCamera: labels.barcodeAllowCamera,
        openSettings: labels.barcodeOpenSettings,
        choosePhoto: labels.barcodeChoosePhoto,
        manualLabel: labels.barcodeManualLabel,
        manualPlaceholder: labels.barcodeManualPlaceholder,
        manualAction: labels.barcodeManualAction,
        cancel: labels.barcodeCancel,
        checking: labels.barcodeChecking,
        invalidManual: labels.barcodeInvalidManual,
        notIsbn: labels.barcodeNotIsbn,
        unreadable: labels.barcodePhotoUnreadable,
        cameraError: labels.barcodeCameraError,
      }}
      onCancel={() => closeScanner({ status: 'cancelled' })}
      onDecoded={isbn => closeScanner({ status: 'decoded', isbn })}
    />
  </>;
}
