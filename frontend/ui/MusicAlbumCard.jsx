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
  rate: 'Rate',
  close: 'Close',
  track: 'track',
  tracks: 'tracks',
  play: 'play',
  plays: 'plays',
};

function countLabel(count, singular, plural) {
  return count === 1 ? `1 ${singular}` : `${count} ${plural}`;
}

function mergePalette(palette) {
  return {
    ...PALETTE,
    ...(palette || {}),
    statusColors: { ...PALETTE.statusColors, ...(palette?.statusColors || {}) },
    rating: { ...PALETTE.rating, ...(palette?.rating || {}) },
  };
}

function albumModel(album, labels) {
  const trackCount = album.trackCount ?? (album.tracks ? album.tracks.length : null);
  const parts = [];
  if (album.releaseType) parts.push({ text: album.releaseType });
  if (album.year) parts.push({ text: String(album.year), muted: true });
  if (trackCount != null) {
    parts.push({ text: countLabel(trackCount, labels.track, labels.tracks), muted: true });
  }
  return {
    mediaType: 'music',
    shape: 'square',
    href: album.href,
    image: { src: album.image, fallback: '', source: 'primary' },
    title: album.title,
    altTitle: null,
    cornerChips: album.plays
      ? [{ kind: 'runtime', text: countLabel(album.plays, labels.play, labels.plays) }]
      : [],
    lines: album.lines && album.lines.length
      ? album.lines
      : [{ join: 'dot', parts }],
    rating: album.score == null ? null : {
      label: String(album.score),
      value: album.score,
      scaleMax: 10,
      rateLabel: labels.rate,
    },
    ratingEditable: false,
  };
}

export function MusicAlbumCard({ album, width, labels, palette, onOpen }) {
  const copy = { ...DEFAULT_LABELS, ...(labels || {}) };
  return <MediaCard
    model={albumModel(album, copy)}
    labels={{ rate: copy.rate, close: copy.close }}
    palette={mergePalette(palette)}
    width={width}
    onOpen={onOpen ? () => onOpen(album) : undefined} />;
}

export function MusicAlbumGrid({ albums, labels, palette, onOpen, minWidth = 150, maxWidth = 220 }) {
  const { width } = useWindowDimensions();
  const [availableWidth, setAvailableWidth] = useState(0);
  const gridWidth = availableWidth || width - 32;
  const columns = Math.max(1, Math.floor((gridWidth + 16) / (minWidth + 16)));
  const cardWidth = Math.floor(Math.min(maxWidth,
    (gridWidth - (columns - 1) * 16) / columns));
  return <View style={styles.grid} onLayout={event => setAvailableWidth(event.nativeEvent.layout.width)}>
    {albums.map(album => <View key={album.id} style={{ width: cardWidth }}>
      <MusicAlbumCard album={album} width={cardWidth} labels={labels} palette={palette} onOpen={onOpen} />
    </View>)}
  </View>;
}

const styles = StyleSheet.create({
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 16 },
});
