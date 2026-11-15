import React, { useState } from 'react';
import { StyleSheet, View, useWindowDimensions } from 'react-native';
import { MediaCard } from './MediaCard';

const PALETTE = {
  card: '#27272a',
  text: '#fafafa',
  textSecondary: '#d4d4d8',
  textMuted: '#a1a1aa',
  border: '#3f3f46',
  link: '#818cf8',
  star: '#facc15',
  chipBackground: 'rgba(24, 24, 27, 0.92)',
  chipText: '#ffffff',
  placeholder: '#3f3f46',
  statusColors: {
    Completed: '#10b981',
    'In progress': '#6366f1',
    Paused: '#f59e0b',
    Planning: '#38bdf8',
    Dropped: '#ef4444',
  },
  rating: { lit: '#f59e0b', unlit: '#52525b', muted: '#a1a1aa', focusStyle: {} },
};

const DEFAULT_LABELS = {
  former: 'Former',
  rate: 'Rate',
  close: 'Close',
};

function relationModel(relation, labels) {
  return {
    mediaType: 'music',
    shape: 'poster',
    href: relation.href,
    image: { src: relation.image, fallback: '', source: 'primary' },
    title: relation.name,
    altTitle: null,
    statusChip: relation.status ? { status: relation.status, label: relation.status } : null,
    cornerChips: relation.current ? [] : [{ kind: 'label', text: labels.former }],
    lines: relation.role
      ? [{ join: 'plain', parts: [{ text: relation.role, muted: true }] }]
      : [],
    rating: relation.score == null ? null : {
      label: String(relation.score),
      value: relation.score,
      scaleMax: 10,
      rateLabel: labels.rate,
    },
    ratingEditable: false,
  };
}

function mergePalette(palette) {
  return {
    ...PALETTE,
    ...(palette || {}),
    statusColors: { ...PALETTE.statusColors, ...(palette?.statusColors || {}) },
    rating: { ...PALETTE.rating, ...(palette?.rating || {}) },
  };
}

export function MusicRelationCard({ relation, width, labels, palette, onOpen }) {
  const copy = { ...DEFAULT_LABELS, ...(labels || {}) };
  return <MediaCard
    model={relationModel(relation, copy)}
    labels={{ rate: copy.rate, close: copy.close }}
    palette={mergePalette(palette)}
    width={width}
    onOpen={onOpen ? () => onOpen(relation) : undefined} />;
}

export function MusicRelationGrid({ relations, labels, palette, onOpen, minWidth = 120, maxWidth = 190 }) {
  const { width } = useWindowDimensions();
  const [availableWidth, setAvailableWidth] = useState(0);
  const columns = width >= 1024 ? 6 : width >= 768 ? 4 : width >= 640 ? 3 : 2;
  const gridWidth = availableWidth || width - 32;
  const cardWidth = Math.floor(Math.min(maxWidth,
    Math.max(0, (gridWidth - (columns - 1) * 16) / columns)));
  return <View style={styles.grid} onLayout={event => setAvailableWidth(event.nativeEvent.layout.width)}>
    {relations.map(relation => <View key={relation.id} style={{ width: cardWidth }}>
      <MusicRelationCard relation={relation} width={cardWidth} labels={labels} palette={palette} onOpen={onOpen} />
    </View>)}
  </View>;
}

const styles = StyleSheet.create({
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 16 },
});
