import React, { useMemo, useRef } from 'react';
import { FlatList, StyleSheet, Text, View, useWindowDimensions } from 'react-native';
import { MediaCard } from '../../../frontend/ui/MediaCard';
import { mediaCardPalette, spacing } from '../theme';
import { cardActionsFor, mediaCardModelFromRow } from '../data/libraryTransportCore';
import MediaCardSlot from './MediaCardSlot';
import { useMobileGridLayout } from '../data/useMobileGridLayout';

const NO_ROWS = [];

// `header`/`footer` (native Library): the grid is the page's scrolling list
// and draws the rest of the page above and below it.
export default function LibraryGrid({
  rows = NO_ROWS, onOpen, onOpenHistory, onRate, selected = NO_ROWS, selecting = false, onToggle, groupBy = 'item',
  header, footer, onEndReached, scrollProps,
}) {
  const { width } = useWindowDimensions();
  const mobileGridLayout = useMobileGridLayout();
  const scrolls = header !== undefined || footer !== undefined;
  const itemCards = groupBy === 'item' && !rows.some(row => row.identity?.entityType);
  // Built once per row set, so appending a page keeps the existing card models
  // (and their memoized cards) instead of rebuilding every one.
  const built = useRef(new WeakMap());
  const items = useMemo(() => (itemCards ? rows.map(row => {
    let item = built.current.get(row);
    if (!item) {
      const model = mediaCardModelFromRow(row.media || row);
      item = { key: String(row.id), row, model: { ...model, identity: row.identity, actions: cardActionsFor(model.title, row.identity) } };
      built.current.set(row, item);
    }
    return item;
  }) : NO_ROWS), [rows, itemCards]);
  // Tracked items carry the web card's own actions (tracking, lists, history, rating),
  // so they are handled in place. Artists, albums and shows keep the plain card.
  if (itemCards) {
    return <MediaCardSlot
      items={items}
      gridLayout="library"
      mobileGridLayout={mobileGridLayout}
      selectionMode={selecting}
      selectedKeys={selected}
      onToggleSelection={onToggle}
      onOpenDetail={onOpen}
      onOpenLink={href => { if (String(href) === '/history') onOpenHistory?.(); }}
      virtualized={scrolls}
      header={header}
      footer={footer}
      onEndReached={onEndReached}
      scrollProps={scrollProps} />;
  }
  const columns = width < 560 ? 2 : Math.min(5, Math.max(3, Math.floor((width + spacing.md) / 196)));
  const horizontalPadding = spacing.lg * 2;
  const cardWidth = (width - horizontalPadding - (columns - 1) * spacing.md) / columns;

  const buckets = new Map();
  rows.forEach(row => {
    const item = row.media?.item || {};
    const groupName = groupBy === 'series' ? item.provider_collection_name
      : groupBy === 'franchise' ? item.provider_franchise_name : '';
    const key = groupName || 'Ungrouped';
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(row);
  });
  const groups = [...buckets.entries()];

  const grouped = <View style={styles.content}>
    {groups.map(([groupName, groupRows]) => <View key={groupName} style={styles.section}>
      {groupBy !== 'item' ? <Text style={styles.groupTitle}>{groupName}</Text> : null}
      <View style={styles.grid}>{groupRows.map(row => {
      const model = { ...mediaCardModelFromRow(row.media || row), identity: row.identity,
        ...(row.identity?.entityType === 'show' ? { ratingEditable: false } : {}) };
      const key = String(row.id);
      return <View key={key} style={{ width: cardWidth }}>
        <MediaCard
          model={model}
          labels={{ noStatus: 'No Status', rate: 'Rate' }}
          palette={mediaCardPalette}
          width={cardWidth}
          onOpen={() => selecting ? onToggle?.(key) : onOpen?.(row)}
          onRate={score => onRate?.(row, score)}
          selection={selecting ? {
            active: true,
            selected: selected.includes(key),
            label: `Select ${model.title}`,
            toggle: () => onToggle?.(key),
          } : undefined}
        />
      </View>;
      })}</View>
    </View>)}
  </View>;
  if (!scrolls) return grouped;
  // Grouped sections and artist/album cards stay fully mounted (sections are
  // short), inside the same scrolling list so the page above keeps its state.
  return <FlatList data={NO_ROWS} renderItem={null} {...scrollProps}
    style={[styles.list, scrollProps?.style]}
    contentContainerStyle={[scrollProps?.contentContainerStyle, styles.listContent]}
    ListHeaderComponent={<>{header}{grouped}</>} ListFooterComponent={footer} />;
}

const styles = StyleSheet.create({
  content: { gap: spacing.lg },
  list: { flex: 1 },
  listContent: { gap: 0, rowGap: 0 },
  section: { gap: spacing.sm },
  groupTitle: { color: mediaCardPalette.text, fontSize: 17, fontWeight: '700' },
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.md },
});
