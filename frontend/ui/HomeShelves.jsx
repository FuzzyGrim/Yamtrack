import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Image, Pressable, StyleSheet, Text, View, useWindowDimensions } from 'react-native';
import { MediaCard } from './MediaCard.jsx';
import { Rail } from './Rail';
import { createFetchGate } from './fetchGate.js';
import { ROW_WATCH_FALLBACK_MS, rowPriority, rowsToStart } from './homeRowLoading.js';
import { sectionSpacing } from './sectionSpacing.js';

// Web and native shelves must pick the same card width. Web computes it from
// the phone breakpoint in mountHome; native passes the same value from here.
export const SHELF_BREAKPOINTS = { phone: 640 };
export const SHELF_CARD_WIDTHS = { phone: 110, default: 176 };
// `mobileGridLayout` is the person's Comfortable (two across) or Compact (three across)
// preference. It only changes phone-width shelves, and Compact keeps the width Home always had.
export const SHELF_COMFORTABLE_MAX_WIDTH = 168;
export function shelfCardWidth(viewportWidth, mobileGridLayout = 'compact') {
  if (viewportWidth >= SHELF_BREAKPOINTS.phone) return SHELF_CARD_WIDTHS.default;
  if (mobileGridLayout === 'comfortable') {
    return Math.min(SHELF_COMFORTABLE_MAX_WIDTH, Math.floor((viewportWidth - 32 - 12) / 2));
  }
  return SHELF_CARD_WIDTHS.phone;
}

const DEFAULT_PALETTE = {
  text: '#fafafa',
  textSecondary: '#d4d4d8',
  textMuted: '#a1a1aa',
  surface: '#27272a',
  surfaceStrong: '#3f3f46',
  surfaceHover: '#3f3f46',
  border: '#3f3f46',
  accent: '#6366f1',
  arrow: '#d4d4d8',
  arrowDisabled: '#52525b',
  loading: '#6366f1',
  focus: '#6366f1',
};

const DEFAULT_LABELS = {
  scrollLeft: 'Scroll left',
  scrollRight: 'Scroll right',
  loadingMore: 'Loading more',
  artworkRefreshing: 'Refreshing artwork...',
  unableToLoad: 'Unable to load this row.',
  retry: 'Try again',
  emptyTitle: 'Nothing is configured for Home yet',
  emptyHint: 'Add or enable rows from the Home Screen settings page to populate this view.',
  openSettings: 'Open Home Screen Settings',
};

function mergePalette(palette) {
  return { ...DEFAULT_PALETTE, ...(palette || {}) };
}

function mergeLabels(labels) {
  return { ...DEFAULT_LABELS, ...(labels || {}) };
}

const ROW_LOAD_RETRIES = 3;
// The server has a handful of threads shared with every other request, so only
// a few row loads run at a time and the rest start as those finish, rows on
// screen first. Shared component: web and native behave the same.
const ROW_FETCH_CONCURRENCY = 3;

// Image loading is first-come on every platform, so prefetches queue ahead of the
// covers a person is waiting for (measured on both web and Expo). A card loads
// its own cover when it is shown; the only early fetch is a small budget of the
// covers a sort flip will show, started after the visible ones have had their turn.
const FLIP_PREFETCH_BUDGET = 16;
const FLIP_PREFETCH_PER_ROW = 4;
const FLIP_PREFETCH_DELAY_MS = 3000;

function prefetchUrl(src) {
  if (!src) return;
  try {
    Promise.resolve(Image.prefetch?.(src)).catch(() => {});
  } catch (error) {
    // Prefetching is only an optimisation.
  }
}

// `budget` is { left } shared by one Home screen; `flip` is false for a row that
// just came back from a sort, whose flipped covers are the ones already shown.
function prefetchRowArtwork(row, budget, { flip = true } = {}) {
  if (!flip) return;
  const flipped = (row?.oppositeArtwork || []).slice(0, Math.min(FLIP_PREFETCH_PER_ROW, budget.left));
  budget.left -= flipped.length;
  if (flipped.length) {
    setTimeout(() => flipped.forEach(prefetchUrl), FLIP_PREFETCH_DELAY_MS);
  }
}

function rowKey(row) {
  return String(row?.rowId ?? row?.row_id ?? '');
}

function rowLoaded(row) {
  const total = Number(row?.total);
  const loaded = Number(row?.loadedCount ?? row?.loaded_count);
  if (Number.isFinite(total) && Number.isFinite(loaded)) return loaded >= total;
  return !row?.hasMore;
}

function sortLabel(row, labels) {
  const title = row?.titleMain || row?.title || '';
  const detail = row?.titleDetail ? ` • ${row.titleDetail}` : '';
  return `${labels.toggleSort || 'Toggle sort direction for'} ${title}${detail}`;
}

const RailRow = React.memo(function RailRow({
  row,
  labels,
  palette,
  readOnly,
  itemWidth,
  loadingMore,
  sorting,
  renderCard,
  renderProgress,
  renderRating,
  renderActionIcon,
  railComponent,
  onOpenCard,
  onOpenLink,
  onOpenRowTitle,
  canOpenRowTitle,
  onAction,
  onRate,
  onSortRow,
  onLoadMore,
  RailComponent = Rail,
}) {
  const items = row.items || [];
  const hasMore = !rowLoaded(row);
  // Card callbacks read the latest row and handlers through a ref, so their
  // identity never changes and a memoized card re-renders only for its own item.
  const latest = useRef(null);
  latest.current = { row, onOpenCard, onOpenLink, onAction, onRate };
  const handlers = useMemo(() => ({
    open: item => latest.current.onOpenCard?.(item, latest.current.row),
    openLink: (href, item) => latest.current.onOpenLink?.(href, item, latest.current.row),
    action: (action, item) => latest.current.onAction?.(action, item, latest.current.row),
    rate: (score, item) => latest.current.onRate?.(score, item, latest.current.row),
  }), []);
  return <RailComponent
    title={row.titleMain || row.title}
    titleDetail={row.titleDetail}
    summaryInline={row.summaryInline}
    direction={row.direction}
    total={row.total}
    loadedCount={row.loadedCount}
    hasMore={hasMore}
    loadingMore={loadingMore}
    sorting={sorting}
    itemWidth={itemWidth}
    items={items}
    viewAll={row.viewAll ? { label: row.viewAll.label, onPress: () => onOpenLink?.(row.viewAll.href, null, row) } : undefined}
    onTitlePress={row.url && onOpenRowTitle && (canOpenRowTitle ? canOpenRowTitle(row.url) : true) ? () => onOpenRowTitle(row.url, row) : undefined}
    onSort={onSortRow ? () => onSortRow(row) : undefined}
    sortLabel={sortLabel(row, labels)}
    onLoadMore={onLoadMore ? () => onLoadMore(row) : undefined}
    loadingLabel={labels.loadingMore}
    emptyLabel={labels.rowEmpty || 'Nothing here yet.'}
    accessibilityLabel={row.titleDetail ? `${row.titleMain || row.title} • ${row.titleDetail}` : (row.titleMain || row.title)}
    scrollLeftLabel={labels.scrollLeft}
    scrollRightLabel={labels.scrollRight}
    palette={palette}
    renderItem={item => <View style={styles.cardStack}>
      {renderCard
        ? renderCard(item, row)
        : <ShelfCard item={item} handlers={handlers} labels={labels} palette={palette}
          width={itemWidth} readOnly={readOnly}
          renderRating={renderRating} renderActionIcon={renderActionIcon} />}
      {item.progress && renderProgress ? <View nativeID={item.progress.target}
        style={styles.progress}>
        {renderProgress(item.progress, item, row)}
      </View> : null}
    </View>} />;
});

// One shelf card. Memoized with callbacks bound to its item, so a shelf
// re-render (load more, a sort, a cover arriving) skips unchanged cards.
const ShelfCard = React.memo(function ShelfCard({
  item, handlers, labels, palette, width, readOnly, renderRating, renderActionIcon,
}) {
  const onOpen = useCallback(() => handlers.open(item), [handlers, item]);
  const onOpenLink = useCallback(href => handlers.openLink(href, item), [handlers, item]);
  const onAction = useCallback(action => handlers.action(action, item), [handlers, item]);
  const onRate = useCallback(score => handlers.rate(score, item), [handlers, item]);
  return <MediaCard model={item.card} labels={labels} palette={palette}
    width={width} readOnly={readOnly}
    onOpen={onOpen} onOpenLink={onOpenLink} onAction={onAction} onRate={onRate}
    renderRating={renderRating} renderActionIcon={renderActionIcon} />;
});

// A waiting row takes the height its loaded row will have, so rows arriving
// do not push the page around. The height is measured from loaded rows of the
// same media type and card width (and remembered by the platform between
// visits when it can); until one has loaded it is estimated from the rail's
// header, the card and the platform's progress control.
const RAIL_HEADER_HEIGHT = 28;
const RAIL_HEADER_GAP = 8;
const RAIL_LIST_PADDING = 8;
const PROGRESS_MARGIN = 8;
const POSTER_ASPECT = 1.5;

export function estimatedRowHeight(itemWidth, progressHeight = 0) {
  return RAIL_HEADER_HEIGHT + RAIL_HEADER_GAP + Math.round(itemWidth * POSTER_ASPECT)
    + (progressHeight > 0 ? PROGRESS_MARGIN + progressHeight : 0) + RAIL_LIST_PADDING;
}

export function rowHeightKey(mediaType, itemWidth) {
  return `${mediaType || ''}:${Math.round(itemWidth)}`;
}

function DeferredRow({ labels, palette, itemWidth = 176, rowRef, height }) {
  return <View ref={rowRef} style={[styles.skeleton, height ? { height } : null]}
    accessibilityRole="progressbar" accessibilityLabel={labels.loadingRow || 'Loading row'}>
    <View style={styles.skeletonHeader}>
      <View style={[styles.skeletonTitle, { backgroundColor: palette.surfaceStrong }]} />
    </View>
    <View style={styles.skeletonCards}>
      {[0, 1, 2, 3].map(index => <View key={index}
        style={[styles.skeletonCard, { backgroundColor: palette.surface, width: itemWidth }]} />)}
    </View>
  </View>;
}

export function HomeShelves({
  groups = [],
  deferredGroups = [],
  showMediaTypeHeaders = false,
  labels: suppliedLabels,
  palette: suppliedPalette,
  readOnly = false,
  itemWidth = 176,
  refreshDelayMs = 1000,
  refreshIntervalMs = 30000,
  deferredDelayMs = 120,
  renderCard,
  renderProgress,
  renderRating,
  renderActionIcon,
  railComponent,
  onOpenCard,
  onOpenLink,
  onOpenRowTitle,
  canOpenRowTitle,
  onAction,
  onRate,
  onSortRow,
  onLoadMore,
  onLoadRow,
  // `watchRow(id, node, onChange)` is the platform's report of where a waiting
  // row sits: it calls `onChange('visible' | 'near' | 'far')` and returns a
  // function that stops watching. Web uses IntersectionObserver, native measures
  // on scroll. Without it every row loads, as before.
  watchRow,
  // Waiting-row sizes. `rowHeights` ({ get(key), set(key, height) }) lets the
  // platform remember measured heights between visits; `progressHeight` is
  // the height of the control its `renderProgress` draws under a card.
  rowHeights,
  progressHeight = 0,
  onRefreshCovers,
  onOpenSettings,
  testID,
}) {
  // Stable merged objects, so a row arriving does not hand every shelf a new
  // palette and re-render its cards.
  const labels = useMemo(() => mergeLabels(suppliedLabels), [suppliedLabels]);
  const palette = useMemo(() => mergePalette(suppliedPalette), [suppliedPalette]);
  // Release separates media-type groups by 1rem on phones and 2rem from `sm`.
  const viewport = useWindowDimensions();
  const sectionGap = sectionSpacing(viewport.width);
  const [loadedRows, setLoadedRows] = useState({});
  const [rowPatches, setRowPatches] = useState({});
  const [pendingRows, setPendingRows] = useState({});
  const [rowErrors, setRowErrors] = useState({});
  const [loadingMore, setLoadingMore] = useState({});
  const [sorting, setSorting] = useState({});
  const sortStateRef = useRef({ sorting: {}, rowPatches: {} });
  const flipBudget = useRef({ left: FLIP_PREFETCH_BUDGET });
  const startedRows = useRef(new Set());
  const rowGate = useRef(null);
  if (!rowGate.current) rowGate.current = createFetchGate(ROW_FETCH_CONCURRENCY);
  const firstPoll = useRef(true);
  const loadRowRef = useRef(null);
  const pollRowsRef = useRef([]);
  const lazy = typeof watchRow === 'function';
  const watchRowRef = useRef(watchRow);
  watchRowRef.current = watchRow;
  // Where each waiting row sits ('visible' | 'near' | 'far'), as last reported.
  const proximity = useRef(new Map());
  const watchReported = useRef(false);
  const rowRefs = useRef(new Map());
  const flushTimer = useRef(null);

  // Measured heights of loaded rows, by media type and card width.
  const [measuredHeights, setMeasuredHeights] = useState({});
  const rowHeightsRef = useRef(rowHeights);
  rowHeightsRef.current = rowHeights;
  const noteRowHeight = useCallback((mediaType, height) => {
    if (!(height > 0)) return;
    const key = rowHeightKey(mediaType, itemWidth);
    const rounded = Math.round(height);
    setMeasuredHeights(current => (current[key] === rounded ? current : { ...current, [key]: rounded }));
    try {
      if (rowHeightsRef.current?.get?.(key) !== rounded) rowHeightsRef.current?.set?.(key, rounded);
    } catch (error) {
      // Remembering sizes is only a convenience.
    }
  }, [itemWidth]);
  const placeholderHeight = mediaType => {
    const key = rowHeightKey(mediaType, itemWidth);
    if (measuredHeights[key]) return measuredHeights[key];
    let remembered;
    try {
      remembered = Number(rowHeightsRef.current?.get?.(key));
    } catch (error) {
      remembered = 0;
    }
    if (remembered > 0) return remembered;
    return estimatedRowHeight(itemWidth, renderProgress ? progressHeight : 0);
  };

  const deferredKey = useMemo(
    () => deferredGroups.map(group => `${group.mediaType}:${(group.rowIds || []).join(',')}`).join('|'),
    [deferredGroups],
  );
  const deferredIds = useMemo(
    () => deferredGroups.flatMap(group => group.rowIds || []),
    [deferredKey],
  );
  const deferredIdsRef = useRef(deferredIds);
  deferredIdsRef.current = deferredIds;

  // A new set of rows (a reload) may be requested again. Not on mount: native
  // can report row positions before this component's effects run, and a reset
  // then would drop those reports.
  const seenDeferredKey = useRef(deferredKey);
  const flushStartsRef = useRef(null);
  useEffect(() => {
    if (seenDeferredKey.current === deferredKey) return;
    seenDeferredKey.current = deferredKey;
    startedRows.current = new Set();
    flushStartsRef.current?.();
  }, [deferredKey]);

  useEffect(() => {
    groups.forEach(group => (group.rows || []).forEach(row => prefetchRowArtwork(row, flipBudget.current)));
  }, [groups]);

  const effectiveRow = useCallback(row => {
    const key = rowKey(row);
    return rowPatches[key] || row;
  }, [rowPatches]);

  const allRows = useMemo(() => [
    ...groups.flatMap(group => group.rows || []),
    ...deferredGroups.flatMap(group => (group.rowIds || [])
      .map(id => loadedRows[String(id)])
      .filter(Boolean)),
  ], [groups, deferredGroups, loadedRows]);

  const pollRows = useMemo(
    () => allRows.map(effectiveRow).filter(row => row && row.pollForCovers),
    [allRows, effectiveRow],
  );
  pollRowsRef.current = pollRows;
  const pollIdsKey = pollRows.map(row => rowKey(row)).join(',');
  // Rows still to arrive keep the artwork poll alive. With lazy rows only rows
  // already requested count, so rows nobody scrolled to start no polling.
  const unresolved = deferredIds
    .filter(id => !Object.prototype.hasOwnProperty.call(loadedRows, String(id))
      && !rowErrors[String(id)]
      && (!lazy || pendingRows[String(id)])).length;

  const loadRow = useCallback(async id => {
    const key = String(id);
    if (!onLoadRow || pendingRows[key]) return;
    setPendingRows(current => ({ ...current, [key]: true }));
    setRowErrors(current => {
      if (!current[key]) return current;
      const next = { ...current };
      delete next[key];
      return next;
    });
    try {
      // A busy or briefly unreachable server should not leave a row asking for a tap.
      let row;
      for (let attempt = 0; ; attempt += 1) {
        try {
          // Read when a slot frees, so a queued row scrolled into view goes next.
          row = await rowGate.current(() => onLoadRow(id), () => rowPriority(proximity.current.get(key)));
          break;
        } catch (error) {
          if (attempt >= ROW_LOAD_RETRIES) throw error;
          await new Promise(resolve => setTimeout(resolve, 800 * (attempt + 1)));
        }
      }
      prefetchRowArtwork(row, flipBudget.current);
      setLoadedRows(current => ({ ...current, [key]: row || null }));
    } catch (error) {
      startedRows.current.delete(key);
      // A failed row waits for its Try again button, not the next scroll.
      proximity.current.delete(key);
      setRowErrors(current => ({ ...current, [key]: true }));
    } finally {
      setPendingRows(current => {
        const next = { ...current };
        delete next[key];
        return next;
      });
    }
  }, [onLoadRow, pendingRows]);

  loadRowRef.current = loadRow;

  const startRow = useCallback(id => {
    const key = String(id);
    if (startedRows.current.has(key)) return;
    startedRows.current.add(key);
    loadRowRef.current?.(id);
  }, []);

  // Without a position report every row is requested, a little apart.
  useEffect(() => {
    if (!onLoadRow || lazy) return undefined;
    const timers = [];
    deferredIds.forEach((id, index) => {
      const key = String(id);
      if (startedRows.current.has(key)) return;
      startedRows.current.add(key);
      timers.push(setTimeout(() => { loadRowRef.current?.(id); }, index * deferredDelayMs));
    });
    return () => timers.forEach(timer => clearTimeout(timer));
  }, [deferredKey, deferredDelayMs, onLoadRow, lazy]);

  // Lazy rows: if the platform never reports a position, load them all anyway.
  // Keyed on whether a loader exists, not its identity, so re-renders do not
  // keep pushing the deadline back.
  const hasLoadRow = !!onLoadRow;
  useEffect(() => {
    if (!hasLoadRow || !lazy || !deferredIds.length) return undefined;
    const timer = setTimeout(() => {
      if (!watchReported.current) deferredIdsRef.current.forEach(startRow);
    }, ROW_WATCH_FALLBACK_MS);
    return () => clearTimeout(timer);
  }, [deferredKey, hasLoadRow, lazy, startRow]);

  useEffect(() => () => { if (flushTimer.current) clearTimeout(flushTimer.current); }, []);

  // Position reports arrive one row at a time; start the batch together so
  // rows on screen take the first request slots, then nearby ones.
  const flushStarts = useCallback(() => {
    if (!lazy || flushTimer.current) return;
    flushTimer.current = setTimeout(() => {
      flushTimer.current = null;
      rowsToStart(deferredIdsRef.current, proximity.current, startedRows.current).forEach(startRow);
    }, 0);
  }, [lazy, startRow]);
  flushStartsRef.current = flushStarts;

  const reportProximity = useCallback((key, level) => {
    watchReported.current = true;
    proximity.current.set(key, level);
    if (level !== 'far') flushStarts();
  }, [flushStarts]);

  // One stable ref callback per waiting row, so a re-render does not re-watch it.
  const rowRefFor = useCallback(key => {
    let entry = rowRefs.current.get(key);
    if (entry) return entry.ref;
    entry = { stop: null };
    entry.ref = node => {
      if (entry.stop) {
        entry.stop();
        entry.stop = null;
      }
      const watch = watchRowRef.current;
      if (!node || typeof watch !== 'function') return;
      entry.stop = watch(key, node, level => reportProximity(key, level)) || null;
    };
    rowRefs.current.set(key, entry);
    return entry.ref;
  }, [reportProximity]);

  const applyRow = useCallback(row => {
    if (!row) return;
    const key = rowKey(row);
    setRowPatches(current => ({ ...current, [key]: row }));
    setLoadedRows(current => (Object.prototype.hasOwnProperty.call(current, key)
      ? { ...current, [key]: row }
      : current));
  }, []);

  const loadingMoreRef = useRef(loadingMore);
  loadingMoreRef.current = loadingMore;
  // Stable identity: one row loading more must not re-render every other row.
  const loadMore = useCallback(async row => {
    const key = rowKey(row);
    if (!onLoadMore || loadingMoreRef.current[key] || rowLoaded(row)) return;
    setLoadingMore(current => ({ ...current, [key]: true }));
    try {
      const next = await onLoadMore(row);
      if (next) applyRow(next);
    } finally {
      setLoadingMore(current => {
        const next = { ...current };
        delete next[key];
        return next;
      });
    }
  }, [applyRow, onLoadMore]);

  sortStateRef.current = { sorting, rowPatches };

  // Stable identity: a sort tap updates state several times and must not re-render every row.
  const sortRow = useCallback(async row => {
    const key = rowKey(row);
    if (!onSortRow || sortStateRef.current.sorting[key]) return;
    // Flip the arrow now; the row dims until the server's reordered row arrives.
    const before = sortStateRef.current.rowPatches[key] || row;
    setSorting(current => ({ ...current, [key]: true }));
    setRowPatches(current => ({
      ...current,
      [key]: { ...before, direction: before.direction === 'asc' ? 'desc' : 'asc' },
    }));
    try {
      const next = await onSortRow(row);
      if (next) {
        prefetchRowArtwork(next, flipBudget.current, { flip: false });
        applyRow(next);
      } else setRowPatches(current => ({ ...current, [key]: before }));
    } catch (error) {
      setRowPatches(current => ({ ...current, [key]: before }));
    } finally {
      setSorting(current => {
        const next = { ...current };
        delete next[key];
        return next;
      });
    }
  }, [applyRow, onSortRow]);

  const pollReady = pollRows.length > 0 || unresolved > 0;
  useEffect(() => {
    if (!onRefreshCovers || !pollReady) return undefined;
    let cancelled = false;
    let timer = null;
    const delay = firstPoll.current ? refreshDelayMs : refreshIntervalMs;
    firstPoll.current = false;
    async function poll() {
      try {
        const result = await onRefreshCovers(pollRowsRef.current.map(row => rowKey(row)), unresolved);
        if (!cancelled && result) {
          (result.rows || []).forEach(applyRow);
          if (result.pollRowIds) {
            const next = new Set(result.pollRowIds.map(String));
            setRowPatches(current => {
              const updates = {};
              Object.entries(current).forEach(([key, row]) => {
                if (row && next.has(key) !== !!row.pollForCovers) {
                  updates[key] = { ...row, pollForCovers: next.has(key) };
                }
              });
              return Object.keys(updates).length ? { ...current, ...updates } : current;
            });
          }
        }
      } catch (error) {
        // A failed poll must not stop cover refreshes; the next interval retries.
      }
      if (!cancelled) timer = setTimeout(poll, refreshIntervalMs);
    }
    timer = setTimeout(poll, delay);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [applyRow, onRefreshCovers, pollIdsKey, pollReady, refreshDelayMs, refreshIntervalMs, unresolved]);

  function renderBuiltRows(rows, mediaType) {
    return (rows || []).map(row => <View key={rowKey(row)}
      onLayout={event => noteRowHeight(mediaType || row.mediaType, event.nativeEvent.layout.height)}>
      <RailRow row={effectiveRow(row)}
      labels={labels} palette={palette} readOnly={readOnly} itemWidth={itemWidth}
      loadingMore={!!loadingMore[rowKey(row)]}
      sorting={!!sorting[rowKey(row)]}
      renderCard={renderCard} renderProgress={renderProgress}
      renderRating={renderRating} renderActionIcon={renderActionIcon}
      RailComponent={railComponent}
      onOpenCard={onOpenCard} onOpenLink={onOpenLink} onOpenRowTitle={onOpenRowTitle} canOpenRowTitle={canOpenRowTitle} onAction={onAction} onRate={onRate}
      onSortRow={onSortRow ? sortRow : undefined}
      onLoadMore={onLoadMore ? loadMore : undefined} />
    </View>);
  }

  function renderDeferredRow(group, id) {
    const key = String(id);
    if (Object.prototype.hasOwnProperty.call(loadedRows, key)) {
      const row = loadedRows[key];
      return row ? renderBuiltRows([row], group.mediaType)[0] : null;
    }
    if (rowErrors[key]) {
      return <View key={`error-${key}`} style={[styles.error, { borderColor: palette.border }]}>
        <Text style={[styles.errorText, { color: palette.textMuted }]}>{labels.unableToLoad}</Text>
        <Pressable accessibilityRole="button" accessibilityLabel={labels.retry}
          onPress={() => { startedRows.current.delete(key); loadRow(id); }}
          style={({ pressed }) => [styles.retry, {
            backgroundColor: palette.surfaceStrong,
            opacity: pressed ? 0.7 : 1,
          }]}>
          <Text style={[styles.retryText, { color: palette.text }]}>{labels.retry}</Text>
        </Pressable>
      </View>;
    }
    return <DeferredRow key={`pending-${key}`} group={group} rowId={id} labels={labels}
      palette={palette} itemWidth={itemWidth} rowRef={lazy ? rowRefFor(key) : undefined}
      height={placeholderHeight(group.mediaType)} />;
  }

  function sectionHeader(group) {
    if (!showMediaTypeHeaders) return null;
    return <View style={[styles.groupHeader, { borderColor: palette.border }]}>
      <Text role="heading" aria-level={2} style={[styles.groupTitle, { color: palette.text }]}>
        {group.label}
      </Text>
    </View>;
  }

  const hasContent = groups.some(group => (group.rows || []).length)
    || deferredGroups.some(group => (group.rowIds || []).length);
  if (!hasContent) {
    return <View style={[styles.empty, { backgroundColor: palette.surface, borderColor: palette.border }]}
      testID={testID}>
      <Text style={[styles.emptyTitle, { color: palette.text }]}>{labels.emptyTitle}</Text>
      <Text style={[styles.emptyHint, { color: palette.textMuted }]}>{labels.emptyHint}</Text>
      {onOpenSettings ? <Pressable accessibilityRole="button" accessibilityLabel={labels.openSettings}
        onPress={onOpenSettings} style={({ pressed }) => [styles.settingsButton, {
          backgroundColor: palette.accent,
          opacity: pressed ? 0.8 : 1,
        }]}>
        <Text style={styles.settingsButtonText}>{labels.openSettings}</Text>
      </Pressable> : null}
    </View>;
  }

  return <View style={[styles.stack, { gap: sectionGap }]} testID={testID}>
    {groups.map(group => <View key={group.mediaType} style={styles.group}>
      {sectionHeader(group)}
      {renderBuiltRows(group.rows, group.mediaType)}
    </View>)}
    {deferredGroups.map(group => {
      const ids = group.rowIds || [];
      const hasRenderable = ids.some(id => {
        const key = String(id);
        return !Object.prototype.hasOwnProperty.call(loadedRows, key)
          || loadedRows[key] || rowErrors[key];
      });
      if (!hasRenderable) return null;
      return <View key={`deferred-${group.mediaType || group.label}`} style={styles.group}>
        {sectionHeader(group)}
        {ids.map(id => renderDeferredRow(group, id))}
      </View>;
    })}
    {pollRows.length ? <Text accessibilityLiveRegion="polite"
      style={[styles.polling, { color: palette.textMuted }]}>{labels.artworkRefreshing}</Text> : null}
  </View>;
}

const styles = StyleSheet.create({
  stack: { gap: 24 },
  group: { gap: 12 },
  groupHeader: { borderBottomWidth: 1, paddingBottom: 10 },
  groupTitle: { fontSize: 24, fontWeight: '600', lineHeight: 30 },
  skeleton: { gap: RAIL_HEADER_GAP, overflow: 'hidden' },
  skeletonHeader: { height: RAIL_HEADER_HEIGHT, justifyContent: 'center' },
  skeletonTitle: { width: 160, height: 18, borderRadius: 4 },
  skeletonCards: { flexDirection: 'row', gap: 16, overflow: 'hidden' },
  cardStack: { gap: 0 },
  progress: { marginTop: PROGRESS_MARGIN, alignItems: 'center', justifyContent: 'center' },
  skeletonCard: { width: 176, aspectRatio: 2 / 3, borderRadius: 8 },
  error: { borderWidth: 1, borderRadius: 8, padding: 12, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 },
  errorText: { flex: 1, fontSize: 13 },
  retry: { borderRadius: 6, paddingHorizontal: 12, paddingVertical: 7 },
  retryText: { fontSize: 12, fontWeight: '600' },
  polling: { fontSize: 12 },
  empty: { borderWidth: 1, borderRadius: 10, padding: 28, alignItems: 'center', gap: 8 },
  emptyTitle: { fontSize: 19, fontWeight: '600', textAlign: 'center' },
  emptyHint: { fontSize: 13, textAlign: 'center', lineHeight: 18 },
  settingsButton: { borderRadius: 6, paddingHorizontal: 16, paddingVertical: 9, marginTop: 6 },
  settingsButtonText: { color: '#ffffff', fontSize: 13, fontWeight: '600' },
});
