import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { StyleSheet, Text, useWindowDimensions, View } from 'react-native';
import { Button } from '../../../frontend/ui/Button';
import { HomeView } from '../../../frontend/pages/HomeView';
import { Icon } from '../../../frontend/ui/Icon';
import { shelfCardWidth } from '../../../frontend/ui/HomeShelves';
import WindowedRail from '../surfaces/WindowedRail';
import {
  createHomeTransport,
  homeScreenFromPayload,
  mergeHomeRowPage,
} from '../data/homeTransport';
import { createMeasuredRowWatcher, measureInWindow } from '../data/homeRowWatcherCore';
import { routeCardHref } from '../data/serverCardCore';
import { getActiveAuthSession, subscribeAuthSession } from '../data/authSession';
import { useMobileGridLayout } from '../data/useMobileGridLayout';
import { palette, radius, spacing } from '../theme';
import { Screen } from '../components/Screen';
import PlaybackSlot from '../surfaces/PlaybackSlot';
import { playbackDetailItem } from '../data/playbackTransport';
import { libraryParamsForRowUrl } from '../data/homeRowDestinationCore';

export const surface = {
  id: 'HOME',
  status: 'ported',
  title: 'Home shelves, deferred groups and cover refresh',
  where: 'Home screen shelves, media-type sections, deferred rows and coalesced artwork polling',
  note: 'The native Home screen composes the Now Playing card and the shared HomeShelves rails. Shelf cards use the shared MediaCard presentation and the reviewed detail navigation. The recovered presentation has no separate per-card host, progress row or floating settings control. Signed-out users never reach this screen (the app shows the sign-in screen), so it carries no sample content. Loading, empty, error with retry, deferred-skeleton, sort, load-more and cover refresh states come from homeTransport. Platform verification is recorded in the UI register.',
};

const PAGE_LIMIT = 14;
// Waiting Home rows take the height loaded rows had earlier in this app session.
const rememberedRowHeights = new Map();
const rowHeightMemory = {
  get: key => rememberedRowHeights.get(key),
  set: (key, height) => rememberedRowHeights.set(key, height),
};

const emptyModel = () => ({
  groups: [],
  deferredGroups: [],
  showMediaTypeHeaders: false,
  readOnly: true,
  labels: {},
});

export default function HomeScreen({ navigate }) {
  const { width, height } = useWindowDimensions();
  // Shelves below the screen load as scrolling brings them within reach.
  const [rowWatcher] = useState(() => createMeasuredRowWatcher({ measure: measureInWindow, viewportHeight: height }));
  useEffect(() => { rowWatcher.setViewportHeight(height); }, [height, rowWatcher]);
  // The same breakpoint helper the web mount uses, so a card is never a
  // different size on one surface.
  const mobileGridLayout = useMobileGridLayout();
  const itemWidth = shelfCardWidth(width, mobileGridLayout);
  const [session, setSession] = useState(() => getActiveAuthSession());
  useEffect(() => subscribeAuthSession(setSession), []);
  const transport = useMemo(
    () => createHomeTransport(session ? { auth: session } : {}),
    [session],
  );
  const serverMode = transport.mode === 'server';
  const [model, setModel] = useState(emptyModel);
  const [loading, setLoading] = useState(serverMode);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!serverMode) {
      setModel(emptyModel());
      setLoading(false);
      setError('');
      return undefined;
    }
    let cancelled = false;
    setLoading(true);
    setError('');
    transport.load({ limit: PAGE_LIMIT }).then(payload => {
      if (cancelled) return;
      setModel(homeScreenFromPayload(payload));
      setLoading(false);
    }).catch(cause => {
      if (cancelled) return;
      setError(cause?.message || 'Home could not be loaded from the server.');
      setModel(emptyModel());
      setLoading(false);
    });
    return () => { cancelled = true; };
  }, [attempt, serverMode, transport]);

  const loadRow = useCallback(rowId => transport.loadRow({ rowId, limit: PAGE_LIMIT }), [transport]);

  const loadMore = useCallback(async currentRow => {
    try {
      const page = await transport.loadRow({
        rowId: currentRow.rowId,
        offset: currentRow.loadedCount || 0,
        seed: currentRow.seed,
        limit: PAGE_LIMIT,
      });
      return page ? mergeHomeRowPage(currentRow, page) : null;
    } catch {
      return null;
    }
  }, [transport]);

  const sortRow = useCallback(async currentRow => {
    try {
      return await transport.sortRow({ rowId: currentRow.rowId });
    } catch {
      return null;
    }
  }, [transport]);

  const refreshCovers = useCallback(
    (ids = [], pending = 0) => transport.refreshCovers({ ids, pending }),
    [transport],
  );

  const openCard = useCallback(card => {
    if (!routeCardHref(card?.href, navigate)) navigate?.('Detail', { item: card });
  }, [navigate]);

  // Restore the reviewed Expo composition: shared shelf cards own their sizing.
  // Web retains its existing in-place controls; the approved native shelf opens Detail.
  return <Screen chrome={false} onScroll={rowWatcher.check} onContentSizeChange={rowWatcher.check}>
    <HomeView
      notice={serverMode ? null : <View style={[styles.signedOut, { borderColor: palette.border }]}>
        <Text style={[styles.signedOutText, { color: palette.textSecondary }]}>Sign in to your Floppy server to see Home.</Text>
        <Button label="Connect server" tone="surface" palette={palette}
          onPress={() => navigate?.('Auth')} />
      </View>}
      playback={serverMode ? <PlaybackSlot onOpenDetail={card => navigate?.('Detail', { item: playbackDetailItem(card) })} /> : null}
      groups={model.groups}
      deferredGroups={model.deferredGroups}
      showMediaTypeHeaders={model.showMediaTypeHeaders}
      labels={model.labels}
      palette={palette}
      readOnly={model.readOnly}
      itemWidth={itemWidth}
      railComponent={WindowedRail}
      renderActionIcon={action => action.icon ? <Icon spec={action.icon} color={palette.accentContrast} size={18} /> : null}
      loading={loading}
      error={error}
      onRetry={() => setAttempt(current => current + 1)}
      onOpenCard={item => openCard(item?.card)}
      onOpenLink={(_href, item) => openCard(item?.card)}
      onAction={(_action, item) => openCard(item?.card)}
      canOpenRowTitle={href => !!libraryParamsForRowUrl(href)}
      onOpenRowTitle={(href, _row) => {
        const target = libraryParamsForRowUrl(href);
        if (target) navigate?.('Library', target);
      }}
      onLoadRow={loadRow}
      watchRow={rowWatcher.watchRow}
      rowHeights={rowHeightMemory}
      onLoadMore={loadMore}
      onSortRow={sortRow}
      onRefreshCovers={refreshCovers}
    />
  </Screen>;
}

const styles = StyleSheet.create({
  signedOut: {
    borderWidth: 1,
    borderRadius: radius.md,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    gap: spacing.sm,
    alignItems: 'flex-start',
  },
  signedOutText: { fontSize: 13, lineHeight: 18 },
});
