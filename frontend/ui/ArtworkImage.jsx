import React, { useEffect, useState } from 'react';
import { Image, StyleSheet, Text, View } from 'react-native';

const DEFAULT_PALETTE = {
  placeholder: '#3f3f46',
  textMuted: '#a1a1aa',
  staleBackground: 'rgba(245, 158, 11, 0.15)',
  staleBorder: 'rgba(245, 158, 11, 0.3)',
  staleText: '#fde68a',
};

const DEFAULT_LABELS = {
  loading: 'Loading artwork',
  unavailable: 'Artwork unavailable',
  stale: 'Artwork may be out of date',
};

function artworkSources(src, fallback) {
  return [src, fallback].filter(Boolean);
}

/** Shared poster/backdrop image with its fallback chain, loading and error
 * states, provenance label and optional stale marker. Browser observers,
 * session warmth and error hooks stay in the web implementation.
 */
export function ArtworkImage({
  src,
  fallback,
  title,
  source = 'primary',
  variant = 'media-card',
  loading = 'lazy',
  decoding = 'sync',
  fallbackOnSmall = false,
  stale = false,
  style,
  resizeMode = 'cover',
  palette = {},
  labels = {},
  children,
  // A platform image with the same props as React Native's Image (`source`,
  // `resizeMode`, `onLoad` with `nativeEvent.source.width`, `onError`). Native
  // passes a cached image; the default is React Native's Image.
  imageComponent: ImageComponent = Image,
}) {
  const colors = { ...DEFAULT_PALETTE, ...palette };
  const copy = { ...DEFAULT_LABELS, ...labels };
  const sources = artworkSources(src, fallback);
  const [attempt, setAttempt] = useState(0);
  const [status, setStatus] = useState(sources.length ? 'loading' : 'unavailable');

  useEffect(() => {
    setAttempt(0);
    setStatus(sources.length ? 'loading' : 'unavailable');
  }, [src, fallback]);

  const uri = sources[attempt] || null;

  function handleLoad(event) {
    const width = event?.nativeEvent?.source?.width;
    if (fallbackOnSmall && attempt === 0 && sources.length > 1 && width === 120) {
      setAttempt(1);
      setStatus('loading');
      return;
    }
    setStatus('loaded');
  }

  function handleError() {
    if (attempt + 1 < sources.length) {
      setAttempt(attempt + 1);
      setStatus('loading');
      return;
    }
    setStatus('unavailable');
  }

  const placeholder = status === 'loaded'
    ? null
    : title || (status === 'unavailable' ? copy.unavailable : copy.loading);
  const accessibilityHint = status === 'unavailable' ? copy.unavailable : undefined;

  return <View style={[styles.root, style, { backgroundColor: colors.placeholder }]}>
    {uri && status !== 'unavailable' ? <ImageComponent source={{ uri }} style={StyleSheet.absoluteFill} resizeMode={resizeMode}
      accessible={false} accessibilityElementsHidden importantForAccessibility="no-hide-descendants"
      onLoad={handleLoad} onError={handleError} /> : null}
    {placeholder ? <Text numberOfLines={2} accessibilityLiveRegion="polite"
      accessibilityHint={accessibilityHint}
      style={[styles.placeholder, { color: colors.textMuted }]}>{placeholder}</Text> : null}
    {stale ? <View style={[styles.stale, { backgroundColor: colors.staleBackground, borderColor: colors.staleBorder }]}>
      <Text style={[styles.staleText, { color: colors.staleText }]}>{copy.stale}</Text>
    </View> : null}
    {children}
  </View>;
}

const styles = StyleSheet.create({
  root: { overflow: 'hidden', alignItems: 'center', justifyContent: 'center' },
  placeholder: { paddingHorizontal: 8, textAlign: 'center', fontSize: 11, fontWeight: '600' },
  stale: {
    position: 'absolute',
    left: 6,
    bottom: 6,
    borderWidth: 1,
    borderRadius: 4,
    paddingHorizontal: 4,
    paddingVertical: 2,
  },
  staleText: { fontSize: 9, fontWeight: '600' },
});
