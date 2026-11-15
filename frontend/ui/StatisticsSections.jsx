import { Modal } from './Modal';
import React, { useState } from 'react';
import {
  Image,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  useWindowDimensions,
  View,
} from 'react-native';
import { StatisticsState } from './StatisticsSummary';
import { StatisticsHoursChart } from './StatisticsCharts';

const DEFAULT_PALETTE = {
  background: '#181b2e',
  surface: 'rgba(23, 27, 49, 0.72)',
  surfaceStrong: '#252b49',
  surfaceMuted: '#292f4c',
  border: '#343c5e',
  text: '#f8f9ff',
  textSecondary: '#d7dcf2',
  textMuted: '#aab3d1',
  accent: '#6366f1',
  accentStrong: '#4f46e5',
  accentContrast: '#ffffff',
  link: '#67e8f9',
  danger: '#ef4444',
  warning: '#f59e0b',
  success: '#10b981',
  radius: 18,
  radiusSmall: 6,
  // Role, era and menu colours: themes may override these palette keys.
  roleActors: '#60a5fa',
  roleActresses: '#c084fc',
  roleDirectors: '#fb923c',
  roleWriters: '#34d399',
  eraAccent: '#f59e0b',
  eraIcon: '#fbbf24',
  ratingStar: '#facc15',
  menuSurface: '#11162a',
  menuBorder: '#4b5563',
};

const DEFAULT_LABELS = {
  comparisonTitle: 'Comparison',
  comparisonCopy: 'Your activity against another period.',
  compareTo: 'Compare to:',
  comparisonEmpty: 'No comparison data available for this range.',
  roleTitle: 'Role Leaders',
  roleCopy: 'Top contributors by role across your library.',
  roleEmpty: 'No cast or crew data available for this range.',
  studioTitle: 'Studio Footprint',
  studioCopy: 'Studios that appear most often.',
  studioTop: 'Top Studios',
  studioEmpty: 'No studio data available for this range.',
  tasteTitle: 'Taste Signals',
  tasteCopy: 'What your library says about your taste.',
  genresTitle: 'Top Genres',
  erasTitle: 'Era Spotlight',
  genresEmpty: 'No genre data available for this range.',
  erasEmpty: 'No release-year data available for this range.',
  overviewTitle: 'Activity Overview',
  overviewEmpty: 'No activity data available for this range.',
  noItems: 'No data available for this range.',
  noRatings: 'No ratings yet.',
  time: 'Time',
  plays: 'Plays',
  titles: 'Titles',
  selectSort: 'Sort',
};

function mergeLabels(labels) {
  return { ...DEFAULT_LABELS, ...(labels || {}) };
}

function mergePalette(palette) {
  return { ...DEFAULT_PALETTE, ...(palette || {}) };
}

function text(value) {
  return value === null || value === undefined ? '' : String(value);
}

function plural(count, one, many) {
  return Number(count) === 1 ? one : many;
}

export function SortMenu({ options = [], selected, palette, onChange, label, bare = false }) {
  const [open, setOpen] = useState(false);
  if (!options.length) return null;
  const current = options.find(option => option.value === selected) || options[0];
  return <View style={styles.sortWrap}>
    <Pressable accessibilityRole="button"
      accessibilityLabel={`${label}: ${current.label}`}
      accessibilityState={{ expanded: open }}
      onPress={() => setOpen(true)}
      style={({ pressed }) => [styles.sortTrigger, bare ? styles.sortBare : {
        backgroundColor: palette.menuSurface,
        borderColor: palette.menuBorder,
        borderRadius: palette.radius,
      }, pressed && styles.pressed]}>
      <Text style={[styles.sortTriggerText, { color: bare ? palette.text : palette.textSecondary }, bare && styles.sortBareText]}>{current.label}</Text>
      <Text style={[styles.sortChevron, { color: palette.textMuted }]}>{'\u25BE'}</Text>
    </Pressable>
    <Modal visible={open} transparent animationType="fade" onRequestClose={() => setOpen(false)}>
      <View style={styles.modalOverlay}>
        <Pressable accessibilityRole="button" accessibilityLabel={label}
          onPress={() => setOpen(false)} style={styles.modalBackdrop} />
        <View style={[styles.menuPanel, {
          backgroundColor: palette.menuSurface, borderColor: palette.menuBorder, borderRadius: 8,
        }]}>
          {options.map(option => <Pressable key={option.value}
            accessibilityRole="button"
            accessibilityState={{ selected: option.value === selected }}
            onPress={() => {
              setOpen(false);
              if (option.value !== selected) onChange?.(option.value);
            }}
            style={({ pressed }) => [styles.menuItem, pressed && { backgroundColor: palette.surfaceStrong }]}>
            <Text style={[styles.menuItemText, {
              color: option.value === selected ? palette.text : palette.textSecondary,
            }]}>{option.label}</Text>
          </Pressable>)}
        </View>
      </View>
    </Modal>
  </View>;
}

function SectionHeader({ title, copy, palette, headerRight }) {
  return <View style={styles.sectionHeader}>
    <View style={styles.sectionHeaderCopy}>
      <Text accessibilityRole="header" style={[styles.sectionTitle, { color: palette.text }]}>{title}</Text>
      {copy ? <Text style={[styles.sectionCopy, { color: palette.textMuted }]}>{copy}</Text> : null}
    </View>
    {headerRight}
  </View>;
}

function SectionCard({ title, copy, palette, headerRight, children, padding }) {
  return <View style={[styles.card, {
    backgroundColor: palette.surface,
    borderRadius: palette.radius,
    padding,
  }]}>
    <View style={styles.cardHeaderSpacing}>
      <SectionHeader title={title} copy={copy} palette={palette} headerRight={headerRight} />
    </View>
    {children}
  </View>;
}

function SubHeader({ icon, iconColor, label, palette }) {
  return <View style={styles.subHeader}>
    <Text style={[styles.subHeaderIcon, { color: iconColor }]}>{icon}</Text>
    <Text style={[styles.subHeaderLabel, { color: palette.text }]}>{label}</Text>
  </View>;
}

function RankedBarRow({
  rank, label, labelWidth, percent, color, value, valueWidth, palette,
}) {
  return <View accessible accessibilityLabel={`${label} ${value} ${percent}%`} style={styles.rankedRow}>
    <Text style={[styles.rank, { color: palette.textMuted }]}>{rank}</Text>
    <Text numberOfLines={1} style={[styles.rankedName, { color: palette.text, width: labelWidth }]}>{label}</Text>
    <View style={[styles.metricTrack, { backgroundColor: 'rgba(255,255,255,0.07)' }]}>
      <View style={[styles.metricFill, { width: `${Math.max(0, Math.min(100, percent))}%`, backgroundColor: color }]} />
    </View>
    <Text numberOfLines={1} style={[styles.rankedValue, { color: palette.text, minWidth: valueWidth }]}>{value}</Text>
    <Text style={[styles.rankedPercent, { color: palette.textMuted }]}>{percent}%</Text>
  </View>;
}

export function StatisticsComparison({
  model = {},
  labels,
  palette,
  onCompareMode,
}) {
  const copy = mergeLabels(labels);
  const colors = mergePalette(palette);
  const { width } = useWindowDimensions();
  const rows = model.rows || [];
  const options = model.options || [];
  const summary = rows.map(row => (
    `${row.label}: ${row.currentLabel || row.current || ''}`
    + (row.comparisonLabel ? ` versus ${row.comparisonLabel}` : '')
    + (row.deltaLabel ? `, ${row.deltaLabel}` : '')
  )).join('. ');
  if (model.loading || model.error || model.empty) {
    return <StatisticsState kind={model.error ? 'error' : model.loading ? 'loading' : 'empty'}
      labels={model.stateLabels} palette={colors} onRetry={model.onRetry} />;
  }
  return <SectionCard title={model.title || copy.comparisonTitle}
    copy={model.copy || copy.comparisonCopy} palette={colors}
    padding={width >= 640 ? 24 : 16}
    headerRight={<View style={styles.compareControls}>
      {options.length ? <Text style={[styles.compareLabel, { color: colors.textMuted }]}>{copy.compareTo}</Text> : null}
      <SortMenu options={options} selected={model.selected} palette={colors}
        onChange={onCompareMode} label={copy.comparisonTitle} />
    </View>}>
    {!rows.length
      ? <Text style={[styles.emptyCopy, { color: colors.textMuted }]}>{model.emptyCopy || copy.comparisonEmpty}</Text>
      : <View accessible accessibilityLabel={summary} style={styles.comparisonRows}>
        {rows.map(row => <View key={row.key || row.label} style={[styles.comparisonRow, { borderBottomColor: colors.menuBorder }]}>
          <View style={styles.comparisonLabelWrap}>
            <View style={[styles.comparisonDot, { backgroundColor: row.color || colors.accent }]} />
            <Text numberOfLines={1} style={[styles.comparisonLabel, { color: colors.text }]}>{row.label}</Text>
          </View>
          <Text style={[styles.comparisonValue, { color: colors.text }]}>{row.currentLabel || row.current}</Text>
          <Text style={[styles.comparisonValue, { color: colors.textMuted }]}>{row.comparisonLabel || '--'}</Text>
          <Text style={[styles.comparisonDelta, {
            color: row.state === 'up' ? colors.success : row.state === 'down' ? colors.danger : colors.textMuted,
          }]}>{row.deltaLabel || '--'}</Text>
        </View>)}
      </View>}
  </SectionCard>;
}

// Role colours come from the palette so every theme keeps them readable.
const roleColor = (palette, bucket) => ({
  actors: palette.roleActors,
  actresses: palette.roleActresses,
  directors: palette.roleDirectors,
  writers: palette.roleWriters,
}[bucket]);
const ROLE_ICONS = {
  actors: '\u25C9',
  actresses: '\u25CE',
  directors: '\u25A3',
  writers: '\u270E',
};

export function StatisticsRoleLeaders({
  model = {},
  labels,
  palette,
  onSortChange,
  onSelectPerson,
}) {
  const copy = mergeLabels(labels);
  const colors = mergePalette(palette);
  const { width } = useWindowDimensions();
  const columns = model.columns || [];
  const options = model.sortOptions || [
    { value: 'plays', label: copy.plays },
    { value: 'time', label: copy.time },
    { value: 'titles', label: copy.titles },
  ];
  const columnWidth = width >= 1024 ? '23%' : width >= 640 ? '48%' : '100%';
  return <SectionCard title={model.title || copy.roleTitle}
    copy={model.copy || copy.roleCopy} palette={colors}
    padding={width >= 640 ? 24 : 16}
    headerRight={<SortMenu options={options} selected={model.selected} palette={colors}
      onChange={onSortChange} label={copy.roleTitle} />}>
    {!columns.length
      ? <Text style={[styles.emptyCopy, { color: colors.textMuted }]}>{model.emptyCopy || copy.roleEmpty}</Text>
      : <View style={styles.leaderColumns}>
        {columns.map(column => <View key={column.bucket || column.label} style={[styles.leaderColumn, { width: columnWidth }]}>
          <View style={styles.leaderTitleRow}>
            <Text style={[styles.leaderIcon, { color: column.color || roleColor(colors, column.bucket) || colors.textSecondary }]}>
              {ROLE_ICONS[column.bucket] || '\u25C9'}
            </Text>
            <Text style={[styles.leaderTitle, { color: column.color || roleColor(colors, column.bucket) || colors.textSecondary }]}>
              {column.label}
            </Text>
          </View>
          <ScrollView style={styles.leaderScroll} showsVerticalScrollIndicator={false}>
            {(column.entries || []).map((entry, index) => <Pressable key={`${entry.source || ''}:${entry.personId || entry.name}`}
              accessibilityRole="button"
              accessibilityLabel={`${entry.name}, ${entry.displayMetric || ''}`.trim()}
              accessibilityState={{ selected: Boolean(entry.selected) }}
              onPress={() => onSelectPerson?.(entry)}
              style={({ pressed }) => [styles.leaderRow, pressed && styles.pressed]}>
              <Text style={[styles.leaderRank, { color: colors.textMuted }]}>{entry.rank || index + 1}</Text>
              <Text numberOfLines={1} style={[styles.leaderName, {
                color: entry.selected ? colors.link : colors.text,
              }]}>{entry.name}</Text>
              <Text style={[styles.leaderMetric, { color: colors.textMuted }]}>{entry.displayMetric}</Text>
            </Pressable>)}
          </ScrollView>
        </View>)}
      </View>}
  </SectionCard>;
}

export function StatisticsStudioFootprint({
  model = {},
  labels,
  palette,
  onSortChange,
  bare = false,
}) {
  const copy = mergeLabels(labels);
  const colors = mergePalette(palette);
  const { width } = useWindowDimensions();
  const studios = model.studios || [];
  const options = model.sortOptions || [
    { value: 'plays', label: copy.plays },
    { value: 'time', label: copy.time },
  ];
  const summary = studios.map(studio => `${studio.name} ${studio.pct} percent, ${studio.metricLabel}`).join('. ');
  return <View style={[styles.card, !bare && {
    backgroundColor: colors.surface,
    borderRadius: colors.radius,
    padding: width >= 640 ? 24 : 16,
  }]}>
    <View style={styles.cardHeaderSpacing}>
      <SectionHeader title={model.title || copy.studioTitle}
        copy={model.copy || copy.studioCopy} palette={colors}
        headerRight={<SortMenu options={options} selected={model.selected} palette={colors}
          onChange={onSortChange} label={copy.studioTitle} />} />
    </View>
    {model.delta ? <View style={styles.studioDelta}>
      <Text style={[styles.studioDeltaText, {
        color: model.delta.state === 'down' ? colors.danger : colors.success,
      }]}>{model.delta.label}</Text>
      {model.delta.copy ? <Text style={[styles.studioDeltaCopy, { color: colors.textMuted }]}>{model.delta.copy}</Text> : null}
    </View> : null}
    <SubHeader icon={'\u25A3'} iconColor={colors.link} label={copy.studioTop} palette={colors} />
    {!studios.length
      ? <Text style={[styles.emptyCopy, { color: colors.textMuted }]}>{model.emptyCopy || copy.studioEmpty}</Text>
      : <View accessibilityLabel={summary} style={styles.rankedList}>
        {studios.map((studio, index) => <RankedBarRow key={studio.name || index}
          rank={index + 1} label={studio.name} labelWidth={160} percent={studio.pct}
          color={studio.color || colors.accent} value={studio.metricLabel}
          valueWidth={64} palette={colors} />)}
      </View>}
  </View>;
}

function TastePanel({ title, icon, iconColor, rows, palette, emptyCopy, emptyCentered }) {
  const summary = (rows || []).map(row => (
    `${row.name || row.label} ${row.formattedDuration || `${row.plays} ${plural(row.plays, 'play', 'plays')}`}, ${row.pct} percent`
  )).join('. ');
  return <View style={styles.tastePanel}>
    <SubHeader icon={icon} iconColor={iconColor} label={title} palette={palette} />
    {!(rows || []).length
      ? <Text style={[styles.tasteEmpty, { color: palette.textMuted }, emptyCentered && styles.centerText]}>{emptyCopy}</Text>
      : <ScrollView style={styles.tasteScroll} showsVerticalScrollIndicator={false}>
        <View accessibilityLabel={summary} style={styles.rankedList}>
          {(rows || []).map((row, index) => <RankedBarRow key={`${row.name || row.label}-${index}`}
            rank={index + 1}
            label={row.name || row.label}
            labelWidth={88}
            percent={row.pct}
            color={row.color || (title === DEFAULT_LABELS.erasTitle ? palette.eraAccent : palette.accent)}
            value={row.formattedDuration || `${row.plays} ${plural(row.plays, 'play', 'plays')}`}
            valueWidth={60}
            palette={palette} />)}
        </View>
      </ScrollView>}
  </View>;
}

export function StatisticsTasteSignals({
  model = {},
  labels,
  palette,
  onSortChange,
  bare = false,
}) {
  const copy = mergeLabels(labels);
  const colors = mergePalette(palette);
  const { width } = useWindowDimensions();
  const options = model.sortOptions || [
    { value: 'time', label: copy.time },
    { value: 'plays', label: copy.plays },
  ];
  return <View style={[styles.card, !bare && {
    backgroundColor: colors.surface,
    borderRadius: colors.radius,
    padding: width >= 640 ? 24 : 16,
  }]}>
    <View style={styles.cardHeaderSpacing}>
      <SectionHeader title={model.title || copy.tasteTitle}
        copy={model.copy || copy.tasteCopy} palette={colors}
        headerRight={<SortMenu options={options} selected={model.selected} palette={colors}
          onChange={onSortChange} label={copy.tasteTitle} />} />
    </View>
    <View style={[styles.tasteGrid, width < 640 && styles.tasteGridStacked]}>
      <View style={styles.tasteColumn}>
        <TastePanel title={model.genresTitle || copy.genresTitle} icon={'\u25C7'} iconColor={colors.link}
          rows={model.genres} palette={colors} emptyCopy={model.genresEmpty || copy.genresEmpty} />
      </View>
      <View style={styles.tasteColumn}>
        <TastePanel title={model.erasTitle || copy.erasTitle} icon={'\u25D4'} iconColor={colors.eraIcon}
          rows={model.eras} palette={colors} emptyCopy={model.erasEmpty || copy.erasEmpty} />
      </View>
    </View>
  </View>;
}

export function StatisticsTalentSection({
  model = {},
  labels,
  palette,
  onRoleSortChange,
  onStudioSortChange,
  onTasteSortChange,
  onSelectPerson,
}) {
  const colors = mergePalette(palette);
  const { width } = useWindowDimensions();
  const primaryWidth = width >= 1024 ? '60%' : '100%';
  const secondaryWidth = width >= 1024 ? '38.5%' : '100%';
  return <View style={styles.talentGrid}>
    <View style={{ width: primaryWidth }}>
      <StatisticsRoleLeaders model={model.roleLeaders} labels={labels} palette={colors}
        onSortChange={onRoleSortChange} onSelectPerson={onSelectPerson} />
    </View>
    <View style={{ width: secondaryWidth }}>
      <StatisticsStudioFootprint model={model.studioFootprint} labels={labels}
        palette={colors} onSortChange={onStudioSortChange} />
    </View>
    <View style={styles.talentFull}>
      <StatisticsTasteSignals model={model.tasteSignals} labels={labels}
        palette={colors} onSortChange={onTasteSortChange} />
    </View>
  </View>;
}

function OverviewPanel({ panel, palette, onSelectItem }) {
  const items = panel.items || [];
  const summary = items.map(item => `${item.title} ${item.subtitle || item.value || ''}`.trim()).join('. ');
  return <View testID="statistics-overview-panel" style={[styles.overviewPanel, { backgroundColor: palette.surfaceStrong, borderRadius: palette.radius }]}>
    <Text style={[styles.overviewPanelTitle, { color: palette.textSecondary }]}>{panel.title}</Text>
    {!items.length
      ? <View style={styles.overviewPanelEmpty}>
        <Text style={[styles.overviewPanelEmptyText, { color: palette.textMuted }]}>
          {panel.empty || DEFAULT_LABELS.noRatings}
        </Text>
      </View>
      : <ScrollView style={styles.overviewPanelScroll} showsVerticalScrollIndicator={false}>
        <View accessibilityLabel={summary}>
          {items.map((item, index) => {
            const body = <View style={styles.overviewRow}>
              <Text style={[styles.overviewRank, { color: palette.textMuted }]}>{item.rank || index + 1}</Text>
              {item.image
                ? <Image source={{ uri: item.image }} accessible accessibilityRole="image"
                  accessibilityLabel={item.title}
                  style={[styles.overviewThumb, panel.roundImages && styles.overviewThumbRound,
                    { backgroundColor: palette.surface }]} />
                : <View style={[styles.overviewThumb, panel.roundImages && styles.overviewThumbRound,
                  { backgroundColor: palette.surface, alignItems: 'center', justifyContent: 'center' }]}>
                  <Text style={[styles.overviewInitial, { color: palette.textMuted }]}>
                    {text(item.title).slice(0, 1).toUpperCase() || '?'}
                  </Text>
                </View>}
              <View style={styles.overviewCopy}>
                <Text numberOfLines={1} style={[styles.overviewTitle, { color: palette.text }]}>{item.title}</Text>
                {item.value
                  ? <Text style={[styles.overviewMeta, { color: palette.textMuted }]}>
                    <Text style={{ color: palette.ratingStar }}>{'\u2605'} </Text>{item.value}
                  </Text>
                  : item.subtitle
                    ? <Text numberOfLines={1} style={[styles.overviewMeta, { color: palette.textMuted }]}>{item.subtitle}</Text>
                    : null}
              </View>
            </View>;
            if (onSelectItem && (item.href || item.onPress)) {
              return <Pressable key={`${item.key || item.title}:${index}`}
                accessibilityRole={item.href ? 'link' : 'button'}
                accessibilityLabel={`${item.title}, ${item.subtitle || item.value || ''}`.trim()}
                onPress={() => onSelectItem(item)}
                style={({ pressed }) => pressed && styles.pressed}>
                {body}
              </Pressable>;
            }
            return <View key={`${item.key || item.title}:${index}`} accessible
              accessibilityLabel={`${item.title}, ${item.subtitle || item.value || ''}`.trim()}>
              {body}
            </View>;
          })}
        </View>
      </ScrollView>}
  </View>;
}

const RATED_OVERVIEW_TYPES = ['tv', 'anime', 'movie'];
const EMPTY_PLAYED_COPY = {
  tv: 'No TV show data available.',
  anime: 'No anime data available.',
  movie: 'No movie data available.',
};

export function StatisticsMediaTypeOverview({
  model = {},
  labels,
  palette,
  onSelectItem,
  bare = false,
}) {
  const copy = mergeLabels(labels);
  const colors = mergePalette(palette);
  const { width } = useWindowDimensions();
  const charts = model.charts || [];
  const providedPanels = model.panels || [];
  const panels = providedPanels.slice();
  // Release always shows the Most Played / Top Rated pair for rated types.
  if (RATED_OVERVIEW_TYPES.includes(model.typeValue)
    && !panels.some(panel => panel.key === `${model.typeValue}-top-rated`)) {
    panels.push({
      key: `${model.typeValue}-top-rated`,
      title: 'Top Rated',
      items: [],
      empty: copy.noRatings,
    });
  }
  if (RATED_OVERVIEW_TYPES.includes(model.typeValue)
    && !panels.some(panel => panel.key === `${model.typeValue}-most-played`)) {
    panels.unshift({
      key: `${model.typeValue}-most-played`,
      title: 'Most Played',
      items: [],
      empty: EMPTY_PLAYED_COPY[model.typeValue] || copy.overviewEmpty,
    });
  }
  const facts = model.facts || [];
  const hasContent = charts.some(chart => (chart.labels || []).length)
    || panels.some(panel => (panel.items || []).length)
    || facts.length;
  const stacked = width < 768;
  return <View style={[styles.overviewCard, !bare && {
    backgroundColor: colors.surface,
    borderRadius: colors.radius,
    padding: width >= 640 ? 24 : 16,
  }]}>
    <View style={styles.overviewHeader}>
      <Text style={[styles.overviewEyebrow, { color: colors.textMuted }]}>{model.typeLabel}</Text>
      <Text accessibilityRole="header" style={[styles.overviewHeading, { color: colors.text }]}>{model.title || copy.overviewTitle}</Text>
      {model.rangeLabel ? <Text style={[styles.overviewRange, { color: colors.textMuted }]}>{model.rangeLabel}</Text> : null}
    </View>
    {!hasContent
      ? <Text style={[styles.emptyCopy, { color: colors.textMuted }]}>{model.emptyCopy || copy.overviewEmpty}</Text>
      : <View style={styles.overviewBody}>
        {facts.length ? <View style={[styles.factGrid, stacked && styles.factGridStacked]}>
          {facts.map(fact => <View key={fact.key || fact.label}
            accessible accessibilityLabel={`${fact.label}: ${fact.value}`}
            style={[styles.factCard, { backgroundColor: colors.surfaceStrong, borderRadius: colors.radius }]}>
            <Text style={[styles.factLabel, { color: colors.textSecondary }]}>{fact.label}</Text>
            <Text style={[styles.factValue, { color: colors.text }]}>{fact.value}</Text>
            {fact.copy ? <Text style={[styles.factCopy, { color: colors.textMuted }]}>{fact.copy}</Text> : null}
          </View>)}
        </View> : null}
        {charts.map(chart => <View key={chart.key || chart.title}
          style={[styles.overviewChartPanel, { backgroundColor: colors.surfaceStrong, borderRadius: colors.radius }]}>
          <Text style={[styles.overviewPanelTitle, { color: colors.textSecondary }]}>{chart.title}</Text>
          <StatisticsHoursChart chart={chart} labels={labels} palette={colors} flat />
        </View>)}
        {panels.length ? <View style={[styles.panelGrid, stacked && styles.panelGridStacked]}>
          {panels.map(panel => <View key={panel.key || panel.title} style={[styles.panelCell, stacked && styles.panelCellStacked]}>
            <OverviewPanel panel={panel} palette={colors} onSelectItem={onSelectItem} />
          </View>)}
        </View> : null}
      </View>}
  </View>;
}

export function StatisticsMediaTypeOverviews({
  model = {},
  labels,
  palette,
  onSelectItem,
}) {
  const overviews = model.items || [];
  if (!overviews.length) {
    return <StatisticsState kind="empty" labels={model.stateLabels} palette={mergePalette(palette)} />;
  }
  return <View style={styles.overviewList}>
    {overviews.map(overview => <StatisticsMediaTypeOverview key={overview.id || overview.typeLabel}
      model={overview} labels={labels} palette={palette} onSelectItem={onSelectItem} />)}
  </View>;
}

const styles = StyleSheet.create({
  card: { width: '100%' },
  cardHeaderSpacing: { marginBottom: 16 },
  sectionHeader: { flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 },
  sectionHeaderCopy: { flexShrink: 1, minWidth: 0, gap: 4 },
  sectionTitle: { fontSize: 20, lineHeight: 28, fontWeight: '600' },
  sectionCopy: { fontSize: 14, lineHeight: 20 },
  sortBare: { borderWidth: 0, backgroundColor: 'transparent', paddingHorizontal: 0 },
  sortBareText: { fontSize: 16, fontWeight: '700' },
  sortWrap: { flexShrink: 0 },
  sortTrigger: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 8,
    minWidth: 128,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderWidth: 1,
  },
  sortTriggerText: { fontSize: 14, lineHeight: 20 },
  sortChevron: { fontSize: 12, marginLeft: 4 },
  modalOverlay: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  modalBackdrop: { position: 'absolute', top: 0, right: 0, bottom: 0, left: 0, backgroundColor: 'rgba(0,0,0,0.35)' },
  menuPanel: { minWidth: 160, paddingVertical: 4, borderWidth: 1 },
  menuItem: { paddingHorizontal: 12, paddingVertical: 8 },
  menuItemText: { fontSize: 14, lineHeight: 20 },
  subHeader: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 12 },
  subHeaderIcon: { fontSize: 14, fontWeight: '700' },
  subHeaderLabel: { fontSize: 14, lineHeight: 20, fontWeight: '600' },
  rankedList: { gap: 12 },
  rankedRow: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  rank: { width: 16, fontSize: 14, lineHeight: 20 },
  rankedName: { flexShrink: 0, fontSize: 14, lineHeight: 20, fontWeight: '500' },
  metricTrack: { flexGrow: 1, flexShrink: 1, height: 10, borderRadius: 5, overflow: 'hidden', minWidth: 30 },
  metricFill: { height: 10, borderRadius: 5 },
  rankedValue: { flexShrink: 0, fontSize: 14, lineHeight: 20, textAlign: 'right' },
  rankedPercent: { width: 48, fontSize: 12, lineHeight: 16, textAlign: 'right' },
  emptyCopy: { fontSize: 14, lineHeight: 20, textAlign: 'center', paddingVertical: 32 },
  tasteGrid: { flexDirection: 'row', gap: 24 },
  tasteGridStacked: { flexDirection: 'column', gap: 16 },
  tasteColumn: { flex: 1, minWidth: 0 },
  tastePanel: { minWidth: 0 },
  tasteScroll: { maxHeight: 152 },
  tasteEmpty: { fontSize: 14, lineHeight: 20, paddingVertical: 16 },
  centerText: { textAlign: 'center' },
  compareControls: { flexDirection: 'row', alignItems: 'center', gap: 8, flexShrink: 1, flexWrap: 'wrap' },
  compareLabel: { fontSize: 12, fontWeight: '600', textTransform: 'uppercase', letterSpacing: 0.6 },
  comparisonRows: { width: '100%' },
  comparisonRow: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 9, borderBottomWidth: 1, flexWrap: 'wrap' },
  comparisonLabelWrap: { flexDirection: 'row', alignItems: 'center', gap: 8, minWidth: 120, flexGrow: 1 },
  comparisonDot: { width: 8, height: 8, borderRadius: 4 },
  comparisonLabel: { fontSize: 14, fontWeight: '600', flexShrink: 1 },
  comparisonValue: { width: 76, fontSize: 14, textAlign: 'right' },
  comparisonDelta: { width: 64, fontSize: 14, fontWeight: '600', textAlign: 'right' },
  leaderColumns: { flexDirection: 'row', flexWrap: 'wrap', gap: 12 },
  leaderColumn: { minWidth: 0 },
  leaderTitleRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 12 },
  leaderIcon: { fontSize: 13 },
  leaderTitle: { fontSize: 14, lineHeight: 20, fontWeight: '600' },
  leaderScroll: { maxHeight: 152 },
  leaderRow: { flexDirection: 'row', alignItems: 'center', gap: 4, paddingVertical: 2 },
  leaderRank: { width: 20, fontSize: 12, lineHeight: 15 },
  leaderName: { flexGrow: 1, flexShrink: 1, fontSize: 12, lineHeight: 15, fontWeight: '500' },
  leaderMetric: { flexShrink: 0, fontSize: 12, lineHeight: 15 },
  studioDelta: { flexDirection: 'row', alignItems: 'center', gap: 6, flexWrap: 'wrap', marginBottom: 8 },
  studioDeltaText: { fontSize: 14, fontWeight: '700' },
  studioDeltaCopy: { fontSize: 12 },
  talentGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 12, width: '100%' },
  talentFull: { flexBasis: '100%', flexGrow: 1 },
  overviewList: { gap: 16, width: '100%' },
  overviewCard: { width: '100%' },
  overviewHeader: { gap: 4, marginBottom: 24 },
  overviewEyebrow: { fontSize: 14, lineHeight: 20, letterSpacing: 1.4, textTransform: 'uppercase' },
  overviewHeading: { fontSize: 24, lineHeight: 32, fontWeight: '600' },
  overviewRange: { fontSize: 14, lineHeight: 20 },
  overviewBody: { gap: 16 },
  factGrid: { flexDirection: 'row', gap: 16 },
  factGridStacked: { flexDirection: 'column' },
  factCard: { flexGrow: 1, flexBasis: 180, padding: 12, gap: 2 },
  factLabel: { fontSize: 12, fontWeight: '600', textTransform: 'uppercase', letterSpacing: 0.6 },
  factValue: { fontSize: 20, fontWeight: '700' },
  factCopy: { fontSize: 12 },
  overviewChartPanel: { padding: 16 },
  overviewPanel: { padding: 16, flexGrow: 1 },
  overviewPanelTitle: {
    fontSize: 14,
    lineHeight: 20,
    fontWeight: '500',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    marginBottom: 16,
  },
  overviewPanelEmpty: { alignItems: 'center', justifyContent: 'center', paddingVertical: 32 },
  overviewPanelEmptyText: { fontSize: 14, lineHeight: 20 },
  overviewPanelScroll: { maxHeight: 240 },
  overviewRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    padding: 4,
    marginHorizontal: -4,
    borderRadius: 6,
  },
  overviewRank: { width: 16, fontSize: 14, lineHeight: 20, fontWeight: '500' },
  overviewThumb: { width: 40, height: 40, borderRadius: 6 },
  overviewThumbRound: { borderRadius: 20 },
  overviewInitial: { fontSize: 14, fontWeight: '700' },
  overviewCopy: { flexGrow: 1, flexShrink: 1, minWidth: 0 },
  overviewTitle: { fontSize: 14, lineHeight: 20, fontWeight: '500' },
  overviewMeta: { fontSize: 12, lineHeight: 16 },
  panelGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 16, alignItems: 'stretch' },
  panelGridStacked: { flexDirection: 'column' },
  panelCell: { flexGrow: 1, flexBasis: '45%', minWidth: 240 },
  panelCellStacked: { width: '100%', minWidth: 0, flexBasis: 'auto' },
  pressed: { opacity: 0.75 },
});
