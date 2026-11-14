import { Modal, ModalCloseButton } from './Modal';
import React, { useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, TextInput, View, useWindowDimensions } from 'react-native';
import { actionIcons } from './actionIcons';
import { Badge } from './Badge';
import { Button } from './Button';
import { DateTimePicker } from './DateTimePicker';
import { Icon } from './Icon';
import { MediaCard } from './MediaCard';
import { libraryGridLayout } from './mediaCardSizing';
import { RatingStars } from './RatingStars';
import { RefreshNotice } from './RefreshNotice';
import { SelectField } from './SelectField';

const DEFAULT_PALETTE = {
  background: '#18181b',
  surface: '#27272a',
  surfaceStrong: '#3f3f46',
  surfaceMuted: '#52525b',
  border: '#3f3f46',
  text: '#fafafa',
  textSecondary: '#d4d4d8',
  textMuted: '#a1a1aa',
  accent: '#6366f1',
  accentStrong: '#4f46e5',
  accentContrast: '#ffffff',
  link: '#818cf8',
  danger: '#ef4444',
  warning: '#f59e0b',
  success: '#10b981',
  radius: 8,
  radiusSmall: 6,
  mediaCard: {
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
    progressTrack: 'rgba(24, 24, 27, 0.6)',
    progressFill: '#6366f1',
    progressPaused: 'rgba(99, 102, 241, 0.4)',
  },
};

const DEFAULT_LABELS = {
  history: 'History',
  previousYear: 'Go back one year',
  previousMonth: 'Go to previous month',
  nextMonth: 'Go to next month',
  currentMonth: 'Go to current month',
  filters: 'Filter history',
  filterTitle: 'Filter History',
  hideFilters: 'Hide filters',
  clearFilters: 'Clear filters',
  saveView: 'Save view',
  saveViewName: 'Name this view',
  apply: 'Apply',
  allTypes: 'All types',
  editTracking: 'Edit tracking',
  addToLists: 'Add to custom lists',
  viewYourActivity: 'View your activity history',
  mediaType: 'Media type',
  viewMode: 'View mode',
  activity: 'Activity (watched)',
  release: 'Release dates',
  releaseOne: 'release',
  genre: 'Genre',
  allGenres: 'All genres',
  impliedGenre: 'Implied genre',
  allImpliedGenres: 'All implied genres',
  from: 'From',
  to: 'To',
  dateRange: 'Date range',
  refreshing: 'Refreshing history in background...',
  dismiss: 'Dismiss refresh notice',
  refreshFailed: 'History could not be refreshed. Showing saved activity.',
  retryRefresh: 'Try again',
  loadMore: 'Load more',
  loadDay: 'Load this day',
  loadingDay: 'Loading this day...',
  loadDayError: 'This day could not be loaded.',
  retryDay: 'Retry day',
  noHistory: 'No watch history yet',
  noHistoryCopy: 'Start tracking movies and episodes to see them here.',
  loadError: 'History could not be loaded.',
  retry: 'Try again',
  loading: 'Loading history...',
  edit: 'Edit',
  delete: 'Delete',
  showMore: 'Show more',
  showLess: 'Show less',
  status: 'Status',
  rate: 'Rate',
  score: 'Score',
  close: 'Close',
  viewActivity: 'Activity',
  activityHistory: 'Activity history',
  activityCopy: 'Every session and repeat for this item.',
  jumpToDay: 'Jump to a session day',
  allDays: 'All days',
  noSessions: 'No sessions recorded for this entry.',
  sessions: 'Sessions',
  firstSession: 'First session',
  lastSession: 'Last session',
  averageSession: 'Average session',
  editHistory: 'Edit history entry',
  editHistoryCopy: 'Change the date, score or status for this entry.',
  editDate: 'Date',
  deleteHistory: 'Delete history entry',
  deleteConfirm: 'Delete this entry from your history? This cannot be undone.',
  save: 'Save',
  cancel: 'Cancel',
  clearDay: 'Show all days',
  altTitle: 'Show alternative title',
  nextEpisode: 'Jump to next episode',
  noStatus: 'No Status',
  open: 'Open',
  items: 'items',
  item: 'item',
  releases: 'releases',
  activeDays: 'Active days',
  entries: 'Activity entries',
  trackedTime: 'Tracked time',
  averagePerDay: 'Average per active day',
  averageGap: 'Average gap',
  longestStreak: 'Longest streak',
  mostActiveWeekday: 'Most active weekday',
  previousPage: 'Previous page',
  nextPage: 'Next page',
  page: 'Page',
  clear: 'Clear',
};

const DEFAULT_MEDIA_TYPES = [
  { value: 'tv', label: 'TV Show' },
  { value: 'movie', label: 'Movie' },
  { value: 'anime', label: 'Anime' },
  { value: 'game', label: 'Game' },
  { value: 'music', label: 'Music' },
  { value: 'book', label: 'Book' },
  { value: 'manga', label: 'Manga' },
  { value: 'comic', label: 'Comic' },
  { value: 'podcast', label: 'Podcast' },
  { value: 'video', label: 'Video' },
  { value: 'boardgame', label: 'Board Game' },
];

function mergeLabels(labels) {
  return { ...DEFAULT_LABELS, ...(labels || {}) };
}

function mergePalette(palette) {
  return {
    ...DEFAULT_PALETTE,
    ...(palette || {}),
    mediaCard: { ...DEFAULT_PALETTE.mediaCard, ...(palette?.mediaCard || {}) },
  };
}

function buttonPalette(palette) {
  return {
    radius: palette.radiusSmall,
    primary: {
      background: palette.accentStrong,
      pressed: palette.accentStrong,
      hovered: palette.accent,
      text: palette.accentContrast,
      border: 'transparent',
    },
    surface: {
      background: palette.surfaceStrong,
      pressed: palette.surfaceMuted,
      hovered: palette.surfaceMuted,
      text: palette.text,
      border: palette.border,
    },
    'ghost-danger': {
      background: 'transparent',
      pressed: palette.danger,
      hovered: palette.danger,
      text: palette.danger,
      border: 'transparent',
    },
    focusStyle: {},
  };
}

function chipPalette(palette) {
  return {
    indigo: {
      color: palette.accentContrast,
      backgroundColor: palette.accentStrong,
    },
    outline: {
      color: palette.textSecondary,
      borderColor: palette.border,
      borderWidth: 1,
    },
  };
}

function dateTimePalette(palette) {
  return {
    text: palette.text,
    textSecondary: palette.textSecondary,
    textMuted: palette.textMuted,
    surface: palette.surface,
    surfaceStrong: palette.surfaceStrong,
    surfaceMuted: palette.surfaceMuted,
    border: palette.border,
    accent: palette.accent,
    accentContrast: palette.accentContrast,
    link: palette.link,
    danger: palette.danger,
    radius: palette.radius,
    radiusSmall: palette.radiusSmall,
    fontSize: 14,
    smallFontSize: 12,
  };
}

function mediaTypeLabel(options, value) {
  const match = (options || []).find(item => item.value === value);
  return match ? match.label : value;
}

function minutesFromRuntime(value) {
  if (typeof value === 'number') return value;
  const match = /^(?:(\d+)h)?\s*(?:(\d+)min)?$/.exec(String(value || '').trim());
  if (!match) return 0;
  return (Number(match[1]) || 0) * 60 + (Number(match[2]) || 0);
}

function durationLabel(minutes) {
  if (!minutes) return '';
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (!hours) return `${rest}min`;
  return rest ? `${hours}h ${rest}min` : `${hours}h`;
}

const SQUARE_TYPES = new Set(['music', 'podcast']);

// The compact poster card the web feed renders for each entry. Activity, edit
// and delete reveal as the card's own action buttons, like the web hover row.
function cardModel(entry, copy, webActions) {
  const parts = [];
  if (entry.verb) parts.push({ text: entry.verb, link: true });
  if (entry.when) parts.push({ text: `${parts.length ? ' ' : ''}${entry.when}`, muted: true });
  const items = [];
  if (webActions) {
    // The web card's overlay: Edit tracking, Add to custom lists, View your
    // activity history. Delete stays in the edit and activity dialogs.
    if (entry.canEdit) items.push({ key: 'edit', title: copy.editTracking, icon: actionIcons.edit, glyph: 'E', tone: 'indigo' });
    if (entry.item) items.push({ key: 'lists', title: copy.addToLists, icon: actionIcons.listAdd, glyph: 'L', tone: 'emerald' });
    if (entry.canViewActivity !== false) items.push({ key: 'activity', title: copy.viewYourActivity, icon: actionIcons.history, glyph: 'H', tone: 'amber' });
  } else {
    if (entry.canViewActivity !== false) items.push({ key: 'activity', title: copy.viewActivity, icon: actionIcons.history, glyph: 'H', tone: 'amber' });
    if (entry.canEdit) items.push({ key: 'edit', title: copy.edit, icon: actionIcons.edit, glyph: 'E', tone: 'indigo' });
    if (entry.canDelete) items.push({ key: 'delete', title: copy.delete, icon: actionIcons.trash, glyph: 'D', tone: 'rose' });
  }
  return {
    mediaType: entry.mediaType,
    shape: entry.mediaType === 'video' ? 'landscape' : SQUARE_TYPES.has(entry.mediaType) ? 'square' : 'poster',
    video: entry.mediaType === 'video',
    href: entry.href || '',
    image: entry.image || { src: null, fallback: '', source: 'primary' },
    title: entry.title,
    altTitle: entry.altTitle || null,
    statusChip: entry.status ? { kind: 'status-native', status: entry.status, label: entry.status } : null,
    cornerChips: entry.runtime ? [{ kind: 'runtime-top', text: entry.runtime }] : [],
    titleLinks: [],
    progressBar: null,
    scoreText: entry.score == null ? null : String(entry.score),
    matched: entry.subtitle ? { text: entry.subtitle, title: entry.subtitle } : null,
    lines: parts.length ? [{ join: 'plain', parts }] : [],
    actions: items.length ? { items } : null,
  };
}

export function HistoryDaySummary({ day, mode = 'activity', labels, palette }) {
  const copy = mergeLabels(labels);
  const colors = mergePalette(palette);
  const count = day.entryCount ?? day.entries?.length ?? 0;
  let text;
  if (mode === 'release') {
    text = `${day.releaseCount ?? count} ${day.releaseCount === 1 ? copy.releaseOne : copy.releases}`;
  } else {
    const noun = count === 1 ? copy.item : copy.items;
    text = `${count} ${noun}${day.runtimeLabel ? ` · ${day.runtimeLabel}` : ''}`;
  }
  return <Text accessibilityLabel={text} style={[styles.daySummary, { color: colors.textMuted }]}>{text}</Text>;
}

export function HistoryEntryCard({
  entry,
  labels,
  palette,
  width,
  onOpen,
  onOpenActivity,
  onEdit,
  onDelete,
  onAddToList,
  webActions = false,
}) {
  const copy = mergeLabels(labels);
  const colors = mergePalette(palette);
  function act(action) {
    if (action.key === 'lists') onAddToList?.(entry);
    else if (action.key === 'activity') onOpenActivity?.(entry);
    else if (action.key === 'edit') onEdit?.(entry);
    else if (action.key === 'delete') onDelete?.(entry);
  }
  return <MediaCard
    model={cardModel(entry, copy, webActions)}
    labels={copy}
    palette={colors.mediaCard}
    width={width}
    onOpen={onOpen ? () => onOpen(entry) : undefined}
    onAction={act}
    renderActionIcon={action => (action.icon
      ? <Icon spec={action.icon} color={colors.accentContrast} size={16} /> : null)} />;
}

function DayGrid({ entries, layout, ...cardProps }) {
  if (!entries.length) return null;
  return <View style={[styles.grid, { gap: layout.gap }]}>
    {entries.map(entry => <HistoryEntryCard key={entry.key} entry={entry} width={layout.width} {...cardProps} />)}
  </View>;
}

export function HistoryDayGroup({
  day,
  mode = 'activity',
  labels,
  palette,
  onOpenEntry,
  onOpenActivity,
  onEditEntry,
  onDeleteEntry,
  onAddToList,
  webActions = false,
  onLoadMore,
  onLoadDay,
  onRetryDay,
}) {
  const copy = mergeLabels(labels);
  const colors = mergePalette(palette);
  const entries = day.entries || [];
  const { width: viewportWidth } = useWindowDimensions();
  const layout = libraryGridLayout(viewportWidth);
  const gridProps = {
    labels: copy, palette: colors, layout, onOpen: onOpenEntry, onOpenActivity,
    onEdit: onEditEntry, onDelete: onDeleteEntry, onAddToList, webActions,
  };
  return <View style={styles.dayGroup}>
    <View style={[styles.dayHeader, { borderBottomColor: colors.border }]}>
      <View style={styles.dayDate}>
        {day.weekday ? <Text style={[styles.weekday, { color: colors.text }]}>{day.weekday}</Text> : null}
        <Text style={[styles.dateLabel, { color: colors.text }, !day.weekday && styles.dateLabelOnly]}>{day.dateLabel}</Text>
      </View>
      <HistoryDaySummary day={day} mode={mode} labels={copy} palette={colors} />
    </View>
    {day.lazy ? <View style={[styles.lazyDay, { borderColor: colors.border }]}>
      {day.error ? <>
        <Text style={[styles.lazyText, { color: colors.danger }]}>{copy.loadDayError}</Text>
        <Button label={copy.retryDay} size="sm" tone="surface" palette={buttonPalette(colors)}
          onPress={() => onRetryDay?.(day)} />
      </> : <>
        {day.loading ? <Text accessibilityLiveRegion="polite" style={[styles.lazyText, { color: colors.textMuted }]}>
          {copy.loadingDay}
        </Text> : null}
        <Button label={copy.loadDay} size="sm" tone="surface" palette={buttonPalette(colors)}
          disabled={day.loading} busy={day.loading} onPress={() => onLoadDay?.(day)} />
      </>}
    </View> : <>
      <DayGrid entries={entries.filter(entry => !SQUARE_TYPES.has(entry.mediaType) && entry.mediaType !== 'video')}
        {...gridProps} />
      <DayGrid entries={entries.filter(entry => SQUARE_TYPES.has(entry.mediaType))} {...gridProps} />
      <DayGrid entries={entries.filter(entry => entry.mediaType === 'video')} {...gridProps} />
      {day.hasMore ? <View style={styles.loadMore}>
        <Button label={day.remaining ? `${copy.loadMore} (${day.remaining})` : copy.loadMore}
          size="sm" tone="surface" palette={buttonPalette(colors)} disabled={day.loadingMore}
          busy={day.loadingMore} onPress={() => onLoadMore?.(day)} />
      </View> : null}
    </>}
  </View>;
}

export function HistoryActivitySummary({ stats, labels, palette }) {
  const copy = mergeLabels(labels);
  const colors = mergePalette(palette);
  if (!stats?.length) return null;
  return <View style={[styles.summary, { borderColor: colors.border, backgroundColor: colors.surface }]}>
    {stats.map(stat => <View key={stat.label} style={styles.summaryItem}>
      <Text style={[styles.summaryValue, { color: colors.text }]}>{stat.value}</Text>
      <Text style={[styles.summaryLabel, { color: colors.textMuted }]}>{stat.label}</Text>
    </View>)}
  </View>;
}

function filterList(filters, key, legacyKey) {
  if (Array.isArray(filters[key])) return [...filters[key]];
  if (Array.isArray(filters[legacyKey])) return [...filters[legacyKey]];
  const legacy = filters[legacyKey];
  return legacy ? [legacy] : [];
}

function toggleValue(values, value) {
  return values.includes(value)
    ? values.filter(item => item !== value)
    : [...values, value];
}

export function HistoryFilterPanel({
  filters = {},
  options = {},
  labels,
  palette,
  onApply,
  onClear,
  onSaveView,
  variant = 'chips',
}) {
  const copy = mergeLabels(labels);
  const colors = mergePalette(palette);
  const initial = () => ({
    from: filters.from || '',
    to: filters.to || '',
    mediaTypes: [...(filters.mediaTypes || [])],
    genres: filterList(filters, 'genres', 'genre'),
    impliedGenres: filterList(filters, 'impliedGenres', 'impliedGenre'),
    mode: filters.mode || 'activity',
  });
  const [state, setState] = useState(initial);
  const [naming, setNaming] = useState(false);
  const [viewName, setViewName] = useState('');
  const mediaTypes = options.mediaTypes?.length ? options.mediaTypes : DEFAULT_MEDIA_TYPES;

  useEffect(() => {
    setState(initial());
  }, [filters.from, filters.to, filters.mode, filters.genre, filters.genres, filters.impliedGenre, filters.impliedGenres, JSON.stringify(filters.mediaTypes || [])]);

  function toggleMediaType(value) {
    setState(current => ({
      ...current,
      mediaTypes: toggleValue(current.mediaTypes, value),
    }));
  }

  function toggleGenre(value) {
    setState(current => ({ ...current, genres: toggleValue(current.genres, value) }));
  }

  function toggleImpliedGenre(value) {
    setState(current => ({
      ...current,
      impliedGenres: toggleValue(current.impliedGenres, value),
    }));
  }

  const chip = (label, selected, onPress, key) => <Badge key={key} label={label} size="sm"
    tone={selected ? 'indigo' : 'outline'} palette={chipPalette(colors)} onPress={onPress}
    accessibilityLabel={label} />;

  return <View style={styles.filterPanel}>
    <Text style={[styles.filterHeading, { color: colors.text }]}>{copy.dateRange}</Text>
    <View style={styles.rangeRow}>
      <View style={styles.rangeField}>
        <Text style={[styles.filterLabel, { color: colors.textMuted }]}>{copy.from}</Text>
        <DateTimePicker value={state.from} trackTime={false} labels={{ field: copy.from }}
          palette={dateTimePalette(colors)}
          onChange={next => setState(current => ({ ...current, from: next }))} />
      </View>
      <View style={styles.rangeField}>
        <Text style={[styles.filterLabel, { color: colors.textMuted }]}>{copy.to}</Text>
        <DateTimePicker value={state.to} trackTime={false} labels={{ field: copy.to }}
          palette={dateTimePalette(colors)}
          onChange={next => setState(current => ({ ...current, to: next }))} />
      </View>
    </View>
    {variant === 'select' ? <>
      {/* The web's dropdowns: one choice each, "All" clears it. */}
      <SelectField label={copy.mediaType} name="mediaType" palette={colors}
        options={[{ value: '', label: copy.allTypes }, ...mediaTypes.map(item => ({ value: item.value, label: item.label }))]}
        value={state.mediaTypes[0] || ''}
        onChange={event => setState(current => ({ ...current, mediaTypes: event.target.value ? [event.target.value] : [] }))} />
      <SelectField label={copy.viewMode} name="mode" palette={colors}
        options={[{ value: 'activity', label: copy.activity }, { value: 'release', label: copy.release }]}
        value={state.mode}
        onChange={event => setState(current => ({ ...current, mode: event.target.value }))} />
      <SelectField label={copy.genre} name="genre" palette={colors}
        options={[{ value: '', label: copy.allGenres }, ...(options.genres || []).map(genre => ({ value: genre, label: genre }))]}
        value={state.genres[0] || ''}
        onChange={event => setState(current => ({ ...current, genres: event.target.value ? [event.target.value] : [] }))} />
      <SelectField label={copy.impliedGenre} name="impliedGenre" palette={colors}
        options={[{ value: '', label: copy.allImpliedGenres }, ...(options.impliedGenres || []).map(genre => ({ value: genre, label: genre }))]}
        value={state.impliedGenres[0] || ''}
        onChange={event => setState(current => ({ ...current, impliedGenres: event.target.value ? [event.target.value] : [] }))} />
    </> : <>
    <Text style={[styles.filterHeading, { color: colors.text }]}>{copy.mediaType}</Text>
    <View style={styles.chipRow}>
      {mediaTypes.map(item => chip(item.label, state.mediaTypes.includes(item.value),
        () => toggleMediaType(item.value), item.value))}
    </View>
    <Text style={[styles.filterHeading, { color: colors.text }]}>{copy.viewMode}</Text>
    <View style={styles.chipRow}>
      {chip(copy.activity, state.mode === 'activity', () => setState(current => ({ ...current, mode: 'activity' })), 'activity')}
      {chip(copy.release, state.mode === 'release', () => setState(current => ({ ...current, mode: 'release' })), 'release')}
    </View>
    {options.genres?.length ? <>
      <Text style={[styles.filterHeading, { color: colors.text }]}>{copy.genre}</Text>
      <View style={styles.chipRow}>
        {options.genres.map(genre => chip(genre, state.genres.includes(genre),
          () => toggleGenre(genre), `genre-${genre}`))}
      </View>
    </> : null}
    {options.impliedGenres?.length ? <>
      <Text style={[styles.filterHeading, { color: colors.text }]}>{copy.impliedGenre}</Text>
      <View style={styles.chipRow}>
        {options.impliedGenres.map(genre => chip(genre, state.impliedGenres.includes(genre),
          () => toggleImpliedGenre(genre), `implied-${genre}`))}
      </View>
    </> : null}
    </>}
    <View style={styles.filterActions}>
      <Button label={copy.clearFilters} size="sm" tone="surface" palette={buttonPalette(colors)}
        onPress={() => { onClear?.(); }} />
      {onSaveView ? <Button label={copy.saveView} size="sm" tone="surface" palette={buttonPalette(colors)}
        onPress={() => setNaming(current => !current)} /> : null}
      <Button label={copy.apply} size="sm" palette={buttonPalette(colors)}
        onPress={() => { onApply?.(state); }} />
    </View>
    {naming && onSaveView ? <View style={styles.saveViewRow}>
      <TextInput value={viewName} onChangeText={setViewName} placeholder={copy.saveViewName}
        accessibilityLabel={copy.saveViewName} placeholderTextColor={colors.textMuted}
        maxLength={100}
        style={[styles.saveViewInput, { color: colors.text, borderColor: colors.border }]} />
      <Button label={copy.save} size="sm" palette={buttonPalette(colors)}
        disabled={!viewName.trim()}
        onPress={() => {
          onSaveView(state, viewName.trim());
          setNaming(false);
          setViewName('');
        }} />
    </View> : null}
  </View>;
}

export function HistoryState({ kind = 'empty', labels, palette, onRetry, onPress }) {
  const copy = mergeLabels(labels);
  const colors = mergePalette(palette);
  const content = {
    loading: { glyph: '◷', title: copy.loading, copy: '' },
    empty: { glyph: '◷', title: copy.noHistory, copy: copy.noHistoryCopy },
    error: { glyph: '!', title: copy.loadError, copy: '' },
  }[kind] || { glyph: '◷', title: copy.noHistory, copy: copy.noHistoryCopy };
  const retry = kind === 'error' ? onRetry : onPress;
  return <View style={[styles.state, { backgroundColor: colors.surface }]}>
    <View style={[styles.stateGlyph, { backgroundColor: colors.surfaceStrong }]}>
      <Text style={[styles.stateGlyphText, { color: colors.textMuted }]}>{content.glyph}</Text>
    </View>
    <Text style={[styles.stateTitle, { color: colors.text }]}>{content.title}</Text>
    {content.copy ? <Text style={[styles.stateCopy, { color: colors.textMuted }]}>{content.copy}</Text> : null}
    {retry ? <View style={styles.stateAction}>
      <Button label={kind === 'error' ? copy.retry : copy.open} size="sm" tone="surface"
        palette={buttonPalette(colors)} onPress={retry} />
    </View> : null}
  </View>;
}

function DialogShell({ title, closeLabel, onClose, colors, sheet = false, children }) {
  return <Modal visible transparent animationType={sheet ? 'slide' : 'fade'} onRequestClose={onClose}>
    <View style={[styles.dialogBackdrop, sheet && styles.sheetBackdrop]}>
      <View accessibilityViewIsModal role="dialog" accessibilityLabel={title}
        style={[styles.dialog, { backgroundColor: colors.surface, borderColor: colors.border }, sheet && styles.sheet]}>
        <View style={styles.dialogHeader}>
          <Text accessibilityRole="header" style={[styles.dialogTitle, { color: colors.text }]}>{title}</Text>
          <ModalCloseButton accessibilityRole="button" accessibilityLabel={closeLabel} onPress={onClose}
            style={styles.dialogClose}>
            <Icon spec={actionIcons.x} size={18} color={colors.textMuted} />
          </ModalCloseButton>
        </View>
        {children}
      </View>
    </View>
  </Modal>;
}

function ratingPalette(palette) {
  return {
    lit: palette.warning,
    unlit: palette.surfaceMuted,
    muted: palette.textMuted,
    text: palette.text,
    textSecondary: palette.textSecondary,
    surface: palette.surface,
    border: palette.border,
    focusStyle: {},
  };
}

function ScoreStars({ entry, copy, colors, onSelect }) {
  const value = entry.score === null || entry.score === undefined || entry.score === '' ? null : Number(entry.score);
  return <View style={styles.scoreBlock}>
    <Text style={[styles.dialogLabel, { color: colors.textMuted }]}>{copy.score}</Text>
    <RatingStars scaleMax={10} value={value} label={copy.rate}
      palette={ratingPalette(colors)}
      renderStar={lit => <Text style={[styles.scoreStar, { color: lit ? colors.warning : colors.surfaceMuted }]}>★</Text>}
      onSelect={onSelect} />
  </View>;
}

export function HistoryActivityDialog({
  entry,
  labels,
  palette,
  onClose,
  onEdit,
  onDelete,
  onSelectScore,
}) {
  const copy = mergeLabels(labels);
  const colors = mergePalette(palette);
  const [selectedDay, setSelectedDay] = useState('');
  useEffect(() => { setSelectedDay(''); }, [entry?.key]);
  if (!entry) return null;
  const sessions = entry.sessions?.length
    ? entry.sessions
    : [{ date: entry.date, when: entry.when, runtime: entry.runtime, note: entry.note, score: entry.score }];
  const markerDays = sessions.map(session => session.date).filter(Boolean);
  const visible = selectedDay ? sessions.filter(session => session.date === selectedDay) : sessions;
  const totalMinutes = sessions.reduce((sum, session) => sum + minutesFromRuntime(session.runtimeMinutes ?? session.runtime), 0);
  const stats = [
    { label: copy.sessions, value: String(sessions.length) },
    { label: copy.trackedTime, value: durationLabel(totalMinutes) || 'None' },
    { label: copy.firstSession, value: sessions[0]?.when || sessions[0]?.date || 'None' },
    { label: copy.lastSession, value: sessions[sessions.length - 1]?.when || sessions[sessions.length - 1]?.date || 'None' },
    { label: copy.averageSession, value: durationLabel(Math.round(totalMinutes / Math.max(sessions.length, 1))) || 'None' },
  ];
  return <DialogShell title={copy.activityHistory} closeLabel={copy.close} onClose={onClose} colors={colors}>
    <ScrollView contentContainerStyle={styles.dialogBody} style={styles.dialogScroll}>
      <View style={styles.dialogItem}>
        <Text style={[styles.dialogItemTitle, { color: colors.text }]}>{entry.title}</Text>
        <Text style={[styles.dialogItemCopy, { color: colors.textMuted }]}>
          {[entry.subtitle, entry.verb, entry.runtime].filter(Boolean).join(' · ')}
        </Text>
      </View>
      <HistoryActivitySummary stats={stats} labels={copy} palette={colors} />
      {entry.canEdit ? <ScoreStars entry={entry} copy={copy} colors={colors} onSelect={onSelectScore} /> : null}
      <View>
        <Text style={[styles.dialogLabel, { color: colors.textMuted }]}>{copy.jumpToDay}</Text>
        <View style={styles.dayPickerRow}>
          <View style={styles.dayPickerField}>
            <DateTimePicker value={selectedDay} trackTime={false} markerDays={markerDays}
              labels={{ field: copy.jumpToDay }} palette={dateTimePalette(colors)}
              onChange={next => setSelectedDay(next || '')} />
          </View>
          {selectedDay ? <Button label={copy.clearDay} size="sm" tone="surface"
            palette={buttonPalette(colors)} onPress={() => setSelectedDay('')} /> : null}
        </View>
      </View>
      <View style={styles.sessionList}>
        {visible.length ? visible.map((session, index) => <View key={session.key || `${session.date}-${index}`}
          style={[styles.sessionRow, { borderColor: colors.border }]}>
          <View style={styles.sessionText}>
            <Text style={[styles.sessionWhen, { color: colors.text }]}>{session.when || session.date}</Text>
            <Text style={[styles.sessionMeta, { color: colors.textMuted }]}>
              {[session.label, session.runtime].filter(Boolean).join(' · ') || copy.sessions}
            </Text>
            {session.note ? <Text style={[styles.sessionNote, { color: colors.textSecondary }]}>{session.note}</Text> : null}
          </View>
          {session.score !== null && session.score !== undefined
            ? <Text style={[styles.sessionScore, { color: colors.warning }]}>★ {session.score}</Text>
            : null}
        </View>) : <Text style={[styles.dialogEmpty, { color: colors.textMuted }]}>{copy.noSessions}</Text>}
      </View>
      {entry.canEdit || entry.canDelete ? <View style={styles.dialogActions}>
        {entry.canDelete ? <Button label={copy.delete} size="sm" tone="ghost-danger"
          palette={buttonPalette(colors)} onPress={() => onDelete?.(entry)} /> : null}
        {entry.canEdit ? <Button label={copy.edit} size="sm" tone="surface"
          palette={buttonPalette(colors)} onPress={() => onEdit?.(entry)} /> : null}
        <View style={styles.dialogSpacer} />
        <Button label={copy.close} size="sm" palette={buttonPalette(colors)} onPress={onClose} />
      </View> : null}
    </ScrollView>
  </DialogShell>;
}

const EDIT_STATUSES = ['Completed', 'In progress', 'Paused', 'Planning', 'Dropped'];

export function HistoryEditDialog({ entry, labels, palette, onClose, onSave, onDelete }) {
  const copy = mergeLabels(labels);
  const colors = mergePalette(palette);
  const [value, setValue] = useState('');
  const [score, setScore] = useState(null);
  const [status, setStatus] = useState('');
  useEffect(() => {
    setValue(entry?.whenValue || '');
    setScore(entry?.score === null || entry?.score === undefined || entry?.score === '' ? null : Number(entry.score));
    setStatus(entry?.status || '');
  }, [entry?.key]);
  if (!entry) return null;
  const hasField = name => !entry.editFields || entry.editFields.includes(name);
  return <DialogShell title={copy.editHistory} closeLabel={copy.close} onClose={onClose} colors={colors}>
    <ScrollView contentContainerStyle={styles.dialogBody} style={styles.dialogScroll}>
      <View style={styles.dialogItem}>
        <Text style={[styles.dialogItemTitle, { color: colors.text }]}>{entry.title}</Text>
        <Text style={[styles.dialogItemCopy, { color: colors.textMuted }]}>{copy.editHistoryCopy}</Text>
      </View>
      <View>
        <Text style={[styles.dialogLabel, { color: colors.textMuted }]}>{copy.editDate}</Text>
        <DateTimePicker value={value} trackTime labels={{ field: copy.editDate }}
          palette={dateTimePalette(colors)} onChange={next => setValue(next)} />
      </View>
      {hasField('score') ? <ScoreStars entry={{ ...entry, score }} copy={copy} colors={colors} onSelect={setScore} /> : null}
      {hasField('status') ? <View>
        <Text style={[styles.dialogLabel, { color: colors.textMuted }]}>{copy.status}</Text>
        <View style={styles.chipRow}>
          {EDIT_STATUSES.map(item => <Badge key={item} label={item} size="sm"
            tone={status === item ? 'indigo' : 'outline'} palette={chipPalette(colors)}
            accessibilityLabel={item}
            onPress={() => setStatus(current => current === item ? '' : item)} />)}
        </View>
      </View> : null}
      <View style={styles.dialogActions}>
        {entry.canDelete ? <Button label={copy.delete} size="sm" tone="ghost-danger"
          palette={buttonPalette(colors)} onPress={() => onDelete?.(entry)} /> : null}
        <View style={styles.dialogSpacer} />
        <Button label={copy.cancel} size="sm" tone="surface" palette={buttonPalette(colors)} onPress={onClose} />
        <Button label={copy.save} size="sm" palette={buttonPalette(colors)}
          onPress={() => onSave?.(entry, { value, score, status })} />
      </View>
    </ScrollView>
  </DialogShell>;
}

export function HistoryDeleteDialog({ entry, labels, palette, onClose, onConfirm }) {
  const copy = mergeLabels(labels);
  const colors = mergePalette(palette);
  if (!entry) return null;
  return <DialogShell title={copy.deleteHistory} closeLabel={copy.close} onClose={onClose} colors={colors}>
    <View style={styles.dialogBody}>
      <View style={styles.dialogItem}>
        <Text style={[styles.dialogItemTitle, { color: colors.text }]}>{entry.title}</Text>
        <Text style={[styles.dialogItemCopy, { color: colors.textMuted }]}>{copy.deleteConfirm}</Text>
      </View>
      <View style={styles.dialogActions}>
        <Button label={copy.cancel} size="sm" tone="surface" palette={buttonPalette(colors)} onPress={onClose} />
        <Button label={copy.delete} size="sm" tone="danger-solid" palette={buttonPalette(colors)}
          onPress={() => onConfirm?.(entry)} />
      </View>
    </View>
  </DialogShell>;
}

function MonthButton({ label, icon, trailing = false, disabled, colors, onPress, accessibilityLabel }) {
  const tint = disabled ? colors.textMuted : colors.text;
  const glyph = <Icon spec={icon} color={tint} size={14} />;
  return <Pressable accessibilityRole="button" accessibilityLabel={accessibilityLabel || label}
    accessibilityState={{ disabled: !!disabled }} disabled={disabled} onPress={onPress}
    style={({ pressed, hovered }) => [styles.monthButton, {
      backgroundColor: disabled ? colors.surfaceMuted : pressed || hovered ? colors.surfaceStrong : colors.surface,
    }]}>
    {trailing ? null : glyph}
    <Text style={[styles.monthButtonText, { color: tint }]}>{label}</Text>
    {trailing ? glyph : null}
  </Pressable>;
}

// The round filter button the web pins to the bottom right of History.
export function HistoryFilterFab({ count = 0, label, onPress, palette }) {
  const copy = mergeLabels({ filters: label });
  const colors = mergePalette(palette);
  return <Pressable accessibilityRole="button" accessibilityLabel={copy.filters} onPress={onPress}
    style={[styles.fab, { backgroundColor: colors.accentStrong }]}>
    <Icon spec={actionIcons.funnel} color={colors.accentContrast} size={20} />
    {count > 0 ? <View style={[styles.fabBadge, { backgroundColor: colors.danger }]}>
      <Text style={[styles.fabBadgeText, { color: colors.accentContrast }]}>{String(count)}</Text>
    </View> : null}
  </Pressable>;
}

export function HistoryFeed({
  model = {},
  labels,
  palette,
  chrome = true,
  onNavigate,
  onLoadMore,
  onLoadDay,
  onOpenEntry,
  onOpenActivity,
  onEditEntry,
  onDeleteEntry,
  onApplyFilters,
  onClearFilters,
  onSaveView,
  onRequestOptions,
  onRetry,
  onRetryDay,
  onRefresh,
  filtersOpen: controlledOpen,
  onFiltersOpenChange,
  filterVariant = 'chips',
  webActions = false,
  onAddToList,
}) {
  const copy = mergeLabels(labels);
  const colors = mergePalette(palette);
  const { width: viewportWidth } = useWindowDimensions();
  const [ownOpen, setOwnOpen] = useState(false);
  const filtersOpen = controlledOpen ?? ownOpen;
  const setFiltersOpen = onFiltersOpenChange || setOwnOpen;
  // The web template hides the year jumps below 24rem.
  const showYears = viewportWidth >= 384;
  const mode = model.mode || 'activity';
  const days = model.days || [];
  const nav = model.nav || {};
  const filters = model.filters || {};
  const options = model.options || {};
  const activeCount = filters.activeCount ?? 0;
  // A background refresh shows as a small spinner by the month, not a banner.
  const showRefresh = chrome && model.refreshing && days.length > 0;

  return <View style={styles.feed}>
    {chrome && model.refreshError ? <RefreshNotice state="error" message={copy.refreshFailed}
      labels={{ retry: copy.retryRefresh }} palette={colors} background={colors.surface}
      border={colors.danger} color={colors.text} onRetry={() => onRefresh?.()} /> : null}

    {chrome && nav.monthLabel ? <View style={styles.monthNav}>
      <View style={styles.monthNavSide}>
        {showYears && nav.previousYear ? <MonthButton label={nav.previousYear.label || copy.previousYear}
          icon={actionIcons.chevronsLeft} colors={colors} disabled={nav.previousYear.disabled}
          accessibilityLabel={copy.previousYear}
          onPress={() => onNavigate?.({ kind: 'year', direction: -1, url: nav.previousYear.url })} /> : null}
        {nav.previousMonth ? <MonthButton label={nav.previousMonth.label || copy.previousMonth}
          icon={actionIcons.chevronLeft} colors={colors} disabled={nav.previousMonth.disabled}
          onPress={() => onNavigate?.({ kind: 'month', direction: -1, url: nav.previousMonth.url })} /> : null}
      </View>
      <View style={styles.monthTitle}>
        <Text accessibilityRole="header" numberOfLines={1} style={[styles.monthLabel, { color: colors.text }]}>{nav.monthLabel}</Text>
        {showRefresh ? <ActivityIndicator size="small" color={colors.textMuted} accessibilityLabel={copy.refreshing} /> : null}
      </View>
      <View style={styles.monthNavSide}>
        {nav.nextMonth ? <MonthButton label={nav.nextMonth.label || copy.nextMonth} trailing
          icon={actionIcons.chevronRight} colors={colors} disabled={nav.nextMonth.disabled}
          onPress={() => onNavigate?.({ kind: 'month', direction: 1, url: nav.nextMonth.url })} /> : null}
        {showYears && nav.currentMonth ? <MonthButton label={nav.currentMonth.label || copy.currentMonth} trailing
          icon={actionIcons.chevronsRight} colors={colors} disabled={nav.currentMonth.disabled}
          accessibilityLabel={copy.currentMonth}
          onPress={() => onNavigate?.({ kind: 'current', url: nav.currentMonth.url })} /> : null}
      </View>
    </View> : null}

    {chrome && filtersOpen ? <DialogShell title={copy.filterTitle} closeLabel={copy.close} sheet
      onClose={() => setFiltersOpen(false)} colors={colors}>
      <ScrollView style={styles.dialogScroll} contentContainerStyle={styles.dialogBody}>
        <HistoryFilterPanel filters={filters} options={options} labels={copy}
          palette={colors} variant={filterVariant}
          onApply={state => { setFiltersOpen(false); onApplyFilters?.(state); }}
          onClear={() => { setFiltersOpen(false); onClearFilters?.(); }} onSaveView={onSaveView} />
      </ScrollView>
    </DialogShell> : null}

    {chrome && model.summary?.length ? <HistoryActivitySummary stats={model.summary} labels={copy} palette={colors} /> : null}

    {model.error ? <HistoryState kind="error" labels={copy} palette={colors} onRetry={onRetry} /> : null}
    {!model.error && model.loading && !days.length
      ? <HistoryState kind="loading" labels={copy} palette={colors} />
      : null}
    {!model.error && !model.loading && !days.length
      ? <HistoryState kind="empty" labels={copy} palette={colors} onPress={onRetry} />
      : null}

    {days.map(day => <HistoryDayGroup key={day.key} day={day} mode={mode} labels={copy}
      palette={colors} onOpenEntry={onOpenEntry} onOpenActivity={onOpenActivity}
      webActions={webActions} onAddToList={onAddToList}
      onEditEntry={onEditEntry} onDeleteEntry={onDeleteEntry} onLoadMore={onLoadMore}
      onLoadDay={onLoadDay} onRetryDay={onRetryDay} />)}

    {chrome && model.pagination && model.pagination.pageCount > 1 ? <View style={styles.pagination}>
      <Button label={copy.previousPage} size="sm" tone="surface" palette={buttonPalette(colors)}
        disabled={!model.pagination.previousUrl}
        onPress={() => onNavigate?.({ kind: 'page', direction: -1, url: model.pagination.previousUrl })} />
      {model.pagination.pages?.length
        ? model.pagination.pages.map((page, index) => page
          ? <Button key={page.number} label={String(page.number)} size="sm"
            tone={page.current ? undefined : 'surface'} disabled={page.current}
            accessibilityLabel={`${copy.page} ${page.number}`}
            palette={buttonPalette(colors)}
            onPress={() => onNavigate?.({ kind: 'page', url: page.url })} />
          : <Text key={`gap-${index}`} style={[styles.pageLabel, { color: colors.textMuted }]}>…</Text>)
        : <Text style={[styles.pageLabel, { color: colors.textMuted }]}>
          {`${copy.page} ${model.pagination.page} / ${model.pagination.pageCount}`}
        </Text>}
      <Button label={copy.nextPage} size="sm" tone="surface" palette={buttonPalette(colors)}
        disabled={!model.pagination.nextUrl}
        onPress={() => onNavigate?.({ kind: 'page', direction: 1, url: model.pagination.nextUrl })} />
    </View> : null}
  </View>;
}

const styles = StyleSheet.create({
  feed: { gap: 16 },
  monthNav: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 8 },
  monthNavSide: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  monthButton: { flexDirection: 'row', alignItems: 'center', gap: 4, paddingHorizontal: 6, paddingVertical: 6, borderRadius: 6 },
  monthButtonText: { fontSize: 14 },
  monthTitle: { flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8 },
  monthLabel: { flexShrink: 1, textAlign: 'center', fontSize: 16, fontWeight: '600' },
  fab: { position: 'absolute', right: 24, bottom: 24, width: 56, height: 56, borderRadius: 28, alignItems: 'center', justifyContent: 'center', elevation: 6, shadowColor: 'black', shadowOpacity: 0.3, shadowRadius: 8, shadowOffset: { width: 0, height: 4 } },
  fabBadge: { position: 'absolute', top: -4, right: -4, width: 20, height: 20, borderRadius: 10, alignItems: 'center', justifyContent: 'center' },
  fabBadgeText: { fontSize: 12, fontWeight: '700' },
  filterPanel: { gap: 12 },
  filterHeading: { fontSize: 14, fontWeight: '500' },
  filterLabel: { fontSize: 11, marginBottom: 2 },
  rangeRow: { flexDirection: 'row', gap: 12 },
  rangeField: { flex: 1, minWidth: 0 },
  chipRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  filterActions: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 4 },
  summary: { flexDirection: 'row', flexWrap: 'wrap', borderWidth: 1, borderRadius: 8, padding: 12, gap: 12 },
  summaryItem: { minWidth: 96, flexGrow: 1 },
  summaryValue: { fontSize: 16, fontWeight: '600' },
  summaryLabel: { fontSize: 11, marginTop: 2 },
  dayGroup: { gap: 16 },
  dayHeader: { flexDirection: 'row', alignItems: 'flex-end', justifyContent: 'space-between', gap: 12, borderBottomWidth: 1, paddingBottom: 12 },
  dayDate: { flexDirection: 'row', alignItems: 'baseline', gap: 6, flexWrap: 'wrap', flex: 1 },
  weekday: { fontSize: 20, fontWeight: '600' },
  dateLabel: { fontSize: 20 },
  dateLabelOnly: { fontWeight: '600' },
  daySummary: { fontSize: 14 },
  grid: { flexDirection: 'row', flexWrap: 'wrap' },
  saveViewRow: { flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' },
  saveViewInput: { flex: 1, minWidth: 140, borderWidth: 1, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 6, fontSize: 13 },
  lazyDay: { borderWidth: 1, borderStyle: 'dashed', borderRadius: 8, alignItems: 'center', gap: 8, paddingVertical: 20 },
  lazyText: { fontSize: 12 },
  loadMore: { alignItems: 'center', paddingTop: 4 },
  state: { borderRadius: 8, alignItems: 'center', paddingVertical: 32, paddingHorizontal: 16, gap: 6 },
  stateGlyph: { width: 48, height: 48, borderRadius: 24, alignItems: 'center', justifyContent: 'center', marginBottom: 4 },
  stateGlyphText: { fontSize: 20, fontWeight: '700' },
  stateTitle: { fontSize: 16, fontWeight: '600' },
  stateCopy: { fontSize: 12, textAlign: 'center' },
  stateAction: { marginTop: 6 },
  pagination: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 12, paddingTop: 4 },
  pageLabel: { fontSize: 12 },
  dialogBackdrop: { flex: 1, backgroundColor: 'rgba(0, 0, 0, 0.6)', alignItems: 'center', justifyContent: 'center', padding: 16 },
  dialog: { width: '100%', maxWidth: 520, maxHeight: '90%', borderWidth: 1, borderRadius: 12, padding: 16, gap: 12 },
  sheetBackdrop: { padding: 0, justifyContent: 'flex-end', alignItems: 'stretch' },
  sheet: { maxWidth: '100%', borderBottomLeftRadius: 0, borderBottomRightRadius: 0, borderTopLeftRadius: 16, borderTopRightRadius: 16, borderWidth: 0, padding: 24, gap: 20 },
  dialogHeader: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  dialogTitle: { flex: 1, fontSize: 18, fontWeight: '700' },
  dialogClose: { padding: 4 },
  dialogCloseText: { fontSize: 16, fontWeight: '700' },
  dialogScroll: { flexGrow: 0 },
  dialogBody: { gap: 14, paddingBottom: 4 },
  dialogItem: { gap: 2 },
  dialogItemTitle: { fontSize: 16, fontWeight: '600' },
  dialogItemCopy: { fontSize: 12 },
  dialogLabel: { fontSize: 11, marginBottom: 4 },
  dialogActions: { flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap', paddingTop: 4 },
  dialogSpacer: { flex: 1 },
  dialogEmpty: { fontSize: 12, paddingVertical: 8 },
  scoreBlock: { gap: 2 },
  scoreStar: { fontSize: 20 },
  dayPickerRow: { flexDirection: 'row', alignItems: 'flex-end', gap: 8 },
  dayPickerField: { flex: 1, minWidth: 0 },
  sessionList: { gap: 8 },
  sessionRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 12, borderWidth: 1, borderRadius: 8, padding: 10 },
  sessionText: { flex: 1, gap: 2 },
  sessionWhen: { fontSize: 13, fontWeight: '600' },
  sessionMeta: { fontSize: 12 },
  sessionNote: { fontSize: 12, lineHeight: 18 },
  sessionScore: { fontSize: 12, fontWeight: '600' },
});
