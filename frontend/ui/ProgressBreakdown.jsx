import React, { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { ProgressBar, progressFallbackPalette } from './ProgressBar';
import { Spinner } from './Spinner';

const defaultLabels = {
  watched: 'Watched',
  of: 'of',
  episodes: 'episodes',
  left: 'left',
  expandAll: 'Expand all',
  collapseAll: 'Collapse all',
  toggleSeason: 'Toggle',
  dropped: 'Dropped',
  notAired: 'Not aired yet',
  episodeWatched: 'episode watched',
  episodesWatched: 'episodes watched',
  rewatched: 'Rewatched',
  rewatching: 'Rewatching',
  watchedAgain: 'Watched again',
  watchedLabel: 'Watched',
  notWatched: 'Not watched',
  notAiredYet: 'Not aired yet',
  noEpisodes: 'No episodes to show yet.',
  catalogueUnavailable: 'The episode list could not be loaded, so only the episodes you have watched are shown.',
  cancelRewatch: 'Cancel rewatch',
  cancelling: 'Cancelling…',
  loading: 'Loading…',
  loadError: 'Your progress could not be loaded. Close this and try again.',
  yourProgress: 'Your progress',
};

function initialOpen(model, seasons) {
  if (model?.open) return { ...model.open };
  const first = seasons.find(season => !season.catalogueMissing && season.watched < season.total);
  return Object.fromEntries(seasons.map(season => [String(season.number), season.number === first?.number]));
}

function episodeCode(season, episode) {
  return `S${String(season.number).padStart(2, '0')}E${String(episode.number).padStart(2, '0')}`;
}

function seasonCount(season, labels) {
  if (season.catalogueMissing) {
    return `${season.watched} ${season.watched === 1 ? labels.episodeWatched : labels.episodesWatched}`;
  }
  if (season.total) return `${season.watched}/${season.total}`;
  return labels.notAired;
}

function seasonRewatchText(season, labels) {
  const state = season.rewatchPercent === 100 ? labels.rewatched : labels.rewatching;
  return `${state} · ${season.rewatchWatched}/${season.total} · ${season.rewatchPercent}%`;
}

/**
 * The season and episode breakdown behind the detail progress card.
 *
 * Presentation only: the caller owns navigation, cancellation, the theme and
 * whether a rewatch is open. Expansion is local interaction state.
 */
export function ProgressBreakdown({ model, palette, status = 'ready', cancelling = false, onOpenSeason, onOpenEpisode, onCancelRewatch }) {
  const colors = { ...progressFallbackPalette, ...palette };
  const labels = { ...defaultLabels, ...(model?.labels || {}) };
  const seasons = model?.seasons || [];
  const summary = model?.summary || {};
  const [open, setOpen] = useState(() => initialOpen(model, seasons));
  const setAll = value => setOpen(Object.fromEntries(seasons.map(season => [String(season.number), value])));
  const toggle = season => setOpen(current => ({ ...current, [String(season.number)]: !current[String(season.number)] }));

  if (status === 'loading') {
    return <View style={styles.stateRow}>
      <Spinner size={16} color={colors.fill} />
      <Text style={[styles.stateText, { color: colors.textMuted }]}>{labels.loading}</Text>
    </View>;
  }
  if (status === 'error') {
    return <Text style={[styles.stateText, styles.errorText, { color: colors.textMuted }]}>{labels.loadError}</Text>;
  }

  const rewatch = summary.rewatch || null;
  const leftText = summary.leftText || (summary.left != null
    ? `${summary.left} ${labels.left}${summary.minutesLeftLabel ? ` (${summary.minutesLeftLabel})` : ''}`
    : '');

  return <View style={styles.root}>
    {model?.catalogueUnavailable
      ? <Text style={[styles.notice, { color: colors.textMuted, borderColor: colors.surfaceBorder }]}>{labels.catalogueUnavailable}</Text>
      : null}

    {summary.total
      ? <View style={styles.summary}>
        <View style={styles.summaryRow}>
          <Text style={[styles.summaryText, { color: colors.textSecondary }]}>
            {labels.watched} <Text style={[styles.strong, { color: colors.text }]}>{summary.watched}</Text> {labels.of} <Text style={[styles.strong, { color: colors.text }]}>{summary.total}</Text> {labels.episodes}
            {leftText ? <Text> · {leftText}</Text> : null}
          </Text>
          {summary.percent != null ? <Text style={[styles.percent, { color: colors.text }]}>{summary.percent}%</Text> : null}
        </View>
        <ProgressBar percent={summary.percent} rewatch={rewatch} height={8} palette={colors} label={labels.yourProgress} />
        {rewatch ? <Text style={[styles.rewatchText, { color: colors.textSecondary }]}>{rewatch.label}</Text> : null}
      </View>
      : null}

    {model?.runKey && onCancelRewatch
      ? <Pressable accessibilityRole="button"
        accessibilityState={{ busy: cancelling, disabled: cancelling }}
        disabled={cancelling}
        onPress={() => onCancelRewatch()}
        style={({ focused }) => [styles.cancel, focused && colors.focusStyle]}>
        <Text style={[styles.cancelText, { color: cancelling ? colors.textMuted : colors.textSecondary }]}>
          {cancelling ? labels.cancelling : labels.cancelRewatch}
        </Text>
      </Pressable>
      : null}

    {seasons.length > 1
      ? <View style={styles.controls}>
        <Pressable accessibilityRole="button" onPress={() => setAll(true)}
          style={({ focused }) => [styles.control, focused && colors.focusStyle]}>
          <Text style={[styles.controlText, { color: colors.textMuted }]}>{labels.expandAll}</Text>
        </Pressable>
        <Pressable accessibilityRole="button" onPress={() => setAll(false)}
          style={({ focused }) => [styles.control, focused && colors.focusStyle]}>
          <Text style={[styles.controlText, { color: colors.textMuted }]}>{labels.collapseAll}</Text>
        </Pressable>
      </View>
      : null}

    {seasons.length
      ? <View style={styles.seasons}>
        {seasons.map(season => {
          const isOpen = Boolean(open[String(season.number)]);
          const rewatchWidth = season.rewatchPercent || 0;
          return <View key={season.number} style={[styles.season, { backgroundColor: colors.surface, borderColor: colors.surfaceBorder }]}>
            <View style={styles.seasonHeader}>
              <Pressable accessibilityRole="button"
                accessibilityLabel={`${labels.toggleSeason} ${season.title}`}
                accessibilityState={{ expanded: isOpen }}
                onPress={() => toggle(season)}
                style={({ focused }) => [styles.chevronButton, focused && colors.focusStyle]}>
                <Text style={[styles.chevron, { color: colors.textMuted }]}>{isOpen ? '▾' : '▸'}</Text>
              </Pressable>
              <Pressable accessibilityRole={onOpenSeason ? 'link' : undefined}
                onPress={() => onOpenSeason?.(season.url)}
                style={({ focused }) => [styles.seasonTitleButton, focused && colors.focusStyle]}>
                <Text numberOfLines={1} style={[styles.seasonTitle, { color: colors.text }]}>{season.title}</Text>
              </Pressable>
              {season.dropped
                ? <View style={[styles.dropped, { backgroundColor: colors.droppedBackground }]}>
                  <Text style={[styles.droppedText, { color: colors.droppedText }]}>{labels.dropped}</Text>
                </View>
                : null}
              <Text style={[styles.seasonCount, { color: colors.textMuted }]}>{seasonCount(season, labels)}</Text>
              {season.percent != null ? <Text style={[styles.seasonPercent, { color: colors.text }]}>{season.percent}%</Text> : null}
            </View>
            {season.percent != null
              ? <ProgressBar percent={season.percent}
                rewatch={rewatchWidth ? { offset: 0, width: rewatchWidth } : null}
                height={6} palette={colors} label={season.title} />
              : null}
            {rewatchWidth
              ? <Text style={[styles.rewatchText, { color: colors.textSecondary }]}>{seasonRewatchText(season, labels)}</Text>
              : null}
            {isOpen
              ? <View style={styles.episodes}>
                {(season.episodes || []).map(episode => {
                  const watched = episode.state === 'watched';
                  const upcoming = episode.state === 'upcoming';
                  const rewatched = watched && episode.rewatched && season.rewatching;
                  const stateLabel = watched
                    ? rewatched ? labels.watchedAgain : labels.watchedLabel
                    : upcoming ? labels.notAiredYet : labels.notWatched;
                  const code = episodeCode(season, episode);
                  const details = [code, episode.title, stateLabel].filter(Boolean).join(', ');
                  return <Pressable key={episode.number}
                    accessibilityRole={onOpenEpisode ? 'link' : undefined}
                    accessibilityLabel={details}
                    onPress={() => onOpenEpisode?.(episode.url)}
                    style={({ pressed, focused }) => [styles.episode, {
                      borderColor: watched ? colors.watchedBorder : colors.upcomingBorder,
                      backgroundColor: watched ? colors.watchedBg : 'transparent',
                      borderStyle: upcoming ? 'dashed' : 'solid',
                      opacity: upcoming ? 0.7 : pressed ? 0.8 : 1,
                    }, rewatched && {
                      borderColor: colors.glow,
                      shadowColor: colors.glowShadow,
                      shadowOpacity: 0.7,
                      shadowRadius: 6,
                      shadowOffset: { width: 0, height: 0 },
                      elevation: 2,
                    }, focused && colors.focusStyle]}>
                    <Text style={[styles.episodeGlyph, { color: watched ? colors.watchedText : colors.textMuted }]}>
                      {watched ? '✓' : upcoming ? '' : '×'}
                    </Text>
                    <Text style={[styles.episodeCode, { color: watched ? colors.watchedText : upcoming ? colors.textMuted : colors.textSecondary }]}>
                      {code}
                    </Text>
                  </Pressable>;
                })}
              </View>
              : null}
          </View>;
        })}
      </View>
      : <Text style={[styles.empty, { color: colors.textMuted, borderColor: colors.surfaceBorder }]}>{labels.noEpisodes}</Text>}
  </View>;
}

const styles = StyleSheet.create({
  root: { gap: 12 },
  stateRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, paddingVertical: 24 },
  stateText: { fontSize: 13, textAlign: 'center' },
  errorText: { paddingVertical: 24 },
  notice: { borderWidth: 1, borderStyle: 'dashed', borderRadius: 8, paddingHorizontal: 12, paddingVertical: 8, fontSize: 12 },
  summary: { gap: 4 },
  summaryRow: { flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between', gap: 12 },
  summaryText: { flexShrink: 1, fontSize: 13 },
  strong: { fontWeight: '700' },
  percent: { fontSize: 18, fontWeight: '700' },
  rewatchText: { fontSize: 12 },
  cancel: { alignSelf: 'flex-end', paddingVertical: 4 },
  cancelText: { fontSize: 12, textDecorationLine: 'underline' },
  controls: { flexDirection: 'row', justifyContent: 'flex-end', gap: 12 },
  control: { paddingVertical: 2 },
  controlText: { fontSize: 12 },
  seasons: { gap: 8 },
  season: { borderWidth: 1, borderRadius: 8, paddingHorizontal: 12, paddingVertical: 8, gap: 4 },
  seasonHeader: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  chevronButton: { paddingVertical: 2 },
  chevron: { fontSize: 12 },
  seasonTitleButton: { flexShrink: 1 },
  seasonTitle: { fontSize: 13, fontWeight: '500' },
  dropped: { borderRadius: 9999, paddingHorizontal: 8, paddingVertical: 2 },
  droppedText: { fontSize: 10, textTransform: 'uppercase' },
  seasonCount: { marginLeft: 'auto', fontSize: 12 },
  seasonPercent: { width: 40, textAlign: 'right', fontSize: 13, fontWeight: '600' },
  episodes: { flexDirection: 'row', flexWrap: 'wrap', gap: 4, marginTop: 8 },
  episode: { flexDirection: 'row', alignItems: 'center', gap: 4, borderWidth: 1, borderRadius: 6, paddingHorizontal: 6, paddingVertical: 2 },
  episodeGlyph: { fontSize: 11 },
  episodeCode: { fontSize: 11 },
  empty: { borderWidth: 1, borderStyle: 'dashed', borderRadius: 8, paddingHorizontal: 16, paddingVertical: 32, textAlign: 'center', fontSize: 13 },
});
