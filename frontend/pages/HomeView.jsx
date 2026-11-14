import React from 'react';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';
import { Button } from '../ui/Button';
import { HomeShelves } from '../ui/HomeShelves';

const defaultLabels = {
  loading: 'Loading Home…',
  retry: 'Try again',
};

// One Home composition for web and native. Platform chrome (the browser top
// bar, search field and live playback host) arrives through slots; the shelves,
// empty/loading/error states and action contract stay shared.
export function HomeView({
  notice,
  playback,
  groups = [],
  deferredGroups = [],
  showMediaTypeHeaders = false,
  labels = {},
  palette = {},
  readOnly = false,
  itemWidth,
  loading = false,
  error = '',
  onRetry,
  onOpenCard,
  onOpenLink,
  onOpenRowTitle,
  canOpenRowTitle,
  onAction,
  onRate,
  renderCard,
  renderProgress,
  renderRating,
  renderActionIcon,
  railComponent,
  onLoadRow,
  watchRow,
  rowHeights,
  progressHeight,
  onLoadMore,
  onSortRow,
  onRefreshCovers,
  onOpenSettings,
}) {
  const copy = { ...defaultLabels, ...(labels || {}) };
  return <View style={styles.root}>
    {notice || null}
    {playback || null}
    {error ? <View style={[styles.notice, { borderColor: palette.danger || '#ef4444' }]}>
      <Text accessibilityLiveRegion="polite" style={[styles.noticeText, { color: palette.danger || '#ef4444' }]}>{error}</Text>
      {onRetry ? <Button label={copy.retry} tone="surface" onPress={onRetry} palette={palette} /> : null}
    </View> : null}
    {loading ? <View accessibilityLiveRegion="polite" style={styles.loading}>
      <ActivityIndicator color={palette.accent || '#6366f1'} />
      <Text style={[styles.loadingText, { color: palette.textMuted || '#a1a1aa' }]}>{copy.loading}</Text>
    </View> : error && !groups.length && !deferredGroups.length ? null : <HomeShelves
      groups={groups}
      deferredGroups={deferredGroups}
      showMediaTypeHeaders={showMediaTypeHeaders}
      labels={labels}
      palette={palette}
      readOnly={readOnly}
      itemWidth={itemWidth}
      onOpenCard={onOpenCard}
      onOpenLink={onOpenLink}
      onOpenRowTitle={onOpenRowTitle}
      canOpenRowTitle={canOpenRowTitle}
      onAction={onAction}
      onRate={onRate}
      renderCard={renderCard}
      renderProgress={renderProgress}
      renderRating={renderRating}
      renderActionIcon={renderActionIcon}
      railComponent={railComponent}
      onLoadRow={onLoadRow}
      watchRow={watchRow}
      rowHeights={rowHeights}
      progressHeight={progressHeight}
      onLoadMore={onLoadMore}
      onSortRow={onSortRow}
      onRefreshCovers={onRefreshCovers}
      onOpenSettings={onOpenSettings} />}
  </View>;
}

const styles = StyleSheet.create({
  root: { gap: 16, minWidth: 0 },
  notice: { gap: 8, borderWidth: 1, borderRadius: 8, padding: 12, alignItems: 'flex-start' },
  noticeText: { fontSize: 13, lineHeight: 18 },
  loading: { alignItems: 'center', justifyContent: 'center', gap: 10, paddingVertical: 48 },
  loadingText: { fontSize: 13 },
});
