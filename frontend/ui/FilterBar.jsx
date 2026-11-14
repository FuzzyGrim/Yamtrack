import { Modal, ModalCloseButton } from './Modal';
import { DropdownBackItem, DropdownField, DropdownHeading, DropdownItem, DropdownMenu, DropdownSeparator } from './DropdownMenu';
import React, { useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  useWindowDimensions,
  View,
} from 'react-native';

const defaultPalette = {
  background: '#18181b',
  surface: '#27272a',
  surfaceStrong: '#3f3f46',
  surfaceHover: '#52525b',
  surfaceMuted: '#52525b',
  border: '#52525b',
  text: '#fafafa',
  textSecondary: '#d4d4d8',
  textMuted: '#a1a1aa',
  accent: '#6366f1',
  accentStrong: '#4f46e5',
  accentText: '#ffffff',
  danger: '#ef4444',
  disabled: '#71717a',
  overlay: 'rgba(0, 0, 0, 0.55)',
};

const defaultLabels = {
  filters: 'Filters',
  allFilters: 'All',
  allTypes: 'All types',
  types: 'types',
  status: 'Status',
  allStatuses: 'All statuses',
  statuses: 'statuses',
  sort: 'Sort',
  view: 'View',
  savedViews: 'Saved views',
  clear: 'Clear',
  clearAll: 'Clear all filters',
  clearStatus: 'Clear status filter',
  direction: 'Direction',
  apply: 'Apply',
  clearRange: 'Clear',
  close: 'Close',
  mediaType: 'Media type',
  date: 'Date',
  addedDate: 'Added date',
  completedDate: 'Completed date',
  releaseDate: 'Release date',
  releaseStatus: 'Release status',
  startDate: 'Start date',
  airDate: 'Air date',
  statusHeading: 'Status',
  range: 'Range',
  ascending: 'ascending',
  descending: 'descending',
  updating: 'Updating results',
  items: 'items',
  any: 'Any',
  from: 'From',
  to: 'To',
  within: 'In the last',
  change: 'Change',
  all: 'All',
  searchOptions: 'Search options',
  noOptions: 'No options found',
};

function mergeLabels(labels) {
  return { ...defaultLabels, ...(labels || {}) };
}

function typeSummary(filters, options, copy = defaultLabels) {
  const selected = filters.mediaTypes || [];
  if (!selected.length || selected.length === availableValues(options.mediaTypes).length) {
    return copy.allTypes;
  }
  if (selected.length === 1) {
    const match = availableValues(options.mediaTypes).find((item) => item.value === selected[0]);
    return match ? match.label : selected[0];
  }
  return `${selected.length} ${copy.types}`;
}

function statusSummary(filters, options, copy = defaultLabels) {
  const selected = filters.statuses || [];
  if (!selected.length) {
    return copy.allStatuses;
  }
  if (selected.length === 1) {
    const match = (options.statuses || []).find((item) => item.value === selected[0]);
    return match ? match.label : selected[0];
  }
  return `${selected.length} ${copy.statuses}`;
}

function rangeSummary(range, value) {
  if (!value) {
    return 'Any';
  }
  if (value.within) {
    const unit = (range.units || []).find((item) => item.value === value.unit);
    return `Last ${value.within} ${unit ? unit.label.toLowerCase() : value.unit || 'days'}`;
  }
  if (value.from && value.to) {
    return `${value.from} – ${value.to}`;
  }
  if (value.from) {
    return `From ${value.from}`;
  }
  if (value.to) {
    return `To ${value.to}`;
  }
  return 'Any';
}

function isRangeActive(value) {
  return Boolean(value && (value.from || value.to || value.within));
}

function sectionValue(filters, section) {
  return (filters.fields || {})[section.key];
}

function sectionClearValue(section) {
  return section.clearValue === undefined ? '' : section.clearValue;
}

function sectionIsActive(section, value) {
  if (section.type === 'multi') {
    return Array.isArray(value) && value.length > 0;
  }
  return Boolean(value) && String(value) !== String(sectionClearValue(section));
}

function sectionSummary(section, filters) {
  const value = sectionValue(filters, section);
  if (!sectionIsActive(section, value)) {
    return '';
  }
  const labels = (section.options || []);
  if (section.type === 'multi') {
    const mode = (filters.modes || {})[section.key] || section.modeValue;
    const selected = (value || []).map((item) => {
      const match = labels.find((option) => String(option.value) === String(item));
      return match ? match.label : String(item);
    });
    const prefix = mode === 'not' ? '−' : '';
    const separator = mode === 'and' ? ' & ' : ', ';
    return prefix + selected.join(separator);
  }
  const match = labels.find((option) => String(option.value) === String(value));
  return match ? match.label : String(value);
}

function sectionCount(filters, sections) {
  return (sections || []).filter((section) =>
    section.countAsFilter !== false && sectionIsActive(section, sectionValue(filters, section))).length;
}

function sectionSummaryText(filters, sections) {
  const parts = [];
  for (const section of sections || []) {
    const summary = sectionSummary(section, filters);
    if (summary) {
      parts.push(summary);
    }
  }
  return parts;
}

function activeFilterCount(filters, options) {
  let count = sectionCount(filters, options.sections);
  if ((filters.mediaTypes || []).length) {
    count += 1;
  }
  if ((filters.statuses || []).length) {
    count += 1;
  }
  for (const range of options.ranges || []) {
    if (isRangeActive((filters.ranges || {})[range.key])) {
      count += 1;
    }
  }
  return count;
}

function availableValues(options) {
  return (options || []).filter((item) => !item.disabled);
}

function filterSummary(filters, options, copy = defaultLabels) {
  const parts = [];
  if ((filters.mediaTypes || []).length) {
    parts.push(typeSummary(filters, options, copy));
  }
  parts.push(...sectionSummaryText(filters, options.sections));
  for (const range of options.ranges || []) {
    const value = (filters.ranges || {})[range.key];
    if (isRangeActive(value)) {
      parts.push(`${range.label}: ${rangeSummary(range, value)}`);
    }
  }
  if (!parts.length) {
    return 'All';
  }
  return parts.length === 1 ? parts[0] : `${parts[0]} +${parts.length - 1}`;
}

// The sorts that order by a date (date added, release date, start, end and air dates).
const isDateSort = (option) => /date/.test(String(option.value));

function optionLabel(options, value) {
  const match = (options || []).find((item) => item.value === value);
  return match ? match.label : value;
}

export function FilterBar({
  filters = {},
  options = {},
  labels,
  palette,
  busy = false,
  disabled = false,
  resultCount,
  variant = 'full',
  showClear = true,
  trailing = null,
  onChange,
  onClear,
}) {
  const toolbar = variant === 'toolbar';
  const { width } = useWindowDimensions();
  const compact = width < 720;
  const copy = mergeLabels(labels);
  const colors = { ...defaultPalette, ...(palette || {}) };
  const styles = useMemo(() => createStyles(colors, compact), [colors, compact]);
  const menuPalette = { ...colors, surfaceMuted: colors.surfaceMuted || colors.surfaceHover };
  const [openSheet, setOpenSheet] = useState(null);
  // The toolbar's three dropdowns open as the app's standard anchored dropdown; the sheet stays for the
  // drill-ins (a section's options, a range) and for the full variant.
  const [menu, setMenu] = useState(null);
  const [panelSearch, setPanelSearch] = useState('');
  const triggerNodes = { filters: useRef(null), status: useRef(null), sort: useRef(null) };
  const [rangeDraft, setRangeDraft] = useState(null);
  const ranges = options.ranges || [];
  const savedViews = options.savedViews || [];
  const sections = options.sections || [];
  const views = options.views || [];
  const count = activeFilterCount(filters, options);
  const isDisabled = disabled || (busy && !toolbar);
  const directionLabel = filters.direction === 'asc' ? '↑' : '↓';
  const sortValue = `${optionLabel(options.sorts, filters.sort)} ${directionLabel}`;
  const viewValue = optionLabel(options.views, filters.view);

  function emit(intent) {
    if (isDisabled) {
      return;
    }
    onChange?.(intent);
  }

  function closeSheet() {
    setOpenSheet(null);
    setMenu(null);
    setRangeDraft(null);
  }

  // A press outside the open dropdown closes it. When that press lands on another trigger, that trigger's
  // dropdown opens in the same press, so moving between them takes one click, not two.
  function closeMenu(event) {
    const openKey = menu?.key;
    const point = event?.nativeEvent;
    setMenu(null);
    if (point?.pageX == null || point?.pageY == null) return;
    for (const key of ['filters', 'status', 'sort']) {
      if (key === openKey) continue;
      triggerNodes[key].current?.measureInWindow?.((x, y, width, height) => {
        if (point.pageX >= x && point.pageX <= x + width && point.pageY >= y && point.pageY <= y + height) {
          pressTrigger(key, { x, y, width, height });
        }
      });
    }
  }

  // A trigger opens the dropdown under itself (toolbar) or the sheet (full variant); a second press closes it.
  function pressTrigger(key, anchor) {
    if (toolbar) setMenu(current => (current?.key === key ? null : { key, anchor }));
    else setOpenSheet(openSheet === key ? null : key);
  }

  function openRange(range, parent = null) {
    const current = (filters.ranges || {})[range.key] || {};
    setRangeDraft({
      from: current.from || '',
      to: current.to || '',
      within: current.within || '',
      unit: current.unit || (range.units && range.units[0] ? range.units[0].value : 'days'),
    });
    if (toolbar) {
      setMenu(current => ({ ...(current || { key: 'filters', anchor: null }), panel: `range:${range.key}`, parent }));
    } else {
      setOpenSheet(`range:${range.key}`);
    }
  }

  function openSection(section, parent = null) {
    setRangeDraft(null);
    setPanelSearch('');
    if (toolbar) {
      setMenu(current => ({ ...(current || { key: 'filters', anchor: null }), panel: `section:${section.key}`, parent }));
    } else {
      setOpenSheet(`section:${section.key}`);
    }
  }

  function updateDraft(key, value) {
    setRangeDraft((current) => ({ ...(current || {}), [key]: value }));
  }

  function applyRange(range) {
    emit({ type: 'range', key: range.key, value: rangeDraft || {} });
    closeSheet();
  }

  function clearRange(range) {
    emit({ type: 'range', key: range.key, value: { from: '', to: '', within: '', unit: rangeDraft?.unit || 'days' } });
    closeSheet();
  }

  function clearAll() {
    emit({ type: 'clear' });
    onClear?.();
    setMenu(null);
    closeSheet();
  }

  function toggleMulti(key, value) {
    emit({ type: 'toggle', key, value });
  }

  function chooseSingle(key, value) {
    emit({ type: 'set', key, value });
    closeSheet();
  }

  function chooseSavedView(id) {
    emit({ type: 'savedView', value: id });
    closeSheet();
  }

  const activeSection = openSheet && openSheet.startsWith('section:')
    ? sections.find((section) => `section:${section.key}` === openSheet)
    : null;
  const sheetTitle = openSheet === 'filters' ? copy.filters
    : openSheet === 'status' ? copy.status
      : openSheet === 'sort' ? copy.sort
        : openSheet === 'view' ? copy.view
          : activeSection ? activeSection.label
            : openSheet && openSheet.startsWith('range:') ? (ranges.find((range) => `range:${range.key}` === openSheet)?.label || copy.range)
              : '';

  const activeRange = openSheet && openSheet.startsWith('range:')
    ? ranges.find((range) => `range:${range.key}` === openSheet)
    : null;

  const panel = menu?.panel || '';
  const panelSection = panel.startsWith('section:') ? sections.find((section) => `section:${section.key}` === panel) : null;
  const panelRange = panel.startsWith('range:') ? ranges.find((range) => `range:${range.key}` === panel) : null;
  // A level opened from the Date level goes back to it; every other level goes back to the root list.
  const goBack = () => { setRangeDraft(null); setMenu(current => ({ ...current, panel: current?.parent || null, parent: null })); };

  // The date options live under one Date row: release state, release date (range and year together),
  // completed date and date added. Everything else keeps its own row.
  const yearSection = sections.find((section) => section.key === 'year');
  const releaseSection = sections.find((section) => section.key === 'release');
  const releaseRange = ranges.find((range) => range.key === 'releaseDate');
  const completedRange = ranges.find((range) => range.key === 'completedDate');
  const addedRange = ranges.find((range) => range.key === 'dateAdded');
  const hasDate = Boolean(yearSection || releaseSection || releaseRange || completedRange || addedRange);
  const rangeText = (range) => (range && isRangeActive((filters.ranges || {})[range.key])
    ? rangeSummary(range, (filters.ranges || {})[range.key]) : '');
  const sectionText = (section) => (section ? sectionSummary(section, filters) : '');
  const releaseDateText = rangeText(releaseRange) || sectionText(yearSection);
  const dateParts = [sectionText(releaseSection), releaseDateText, rangeText(completedRange), rangeText(addedRange)].filter(Boolean);
  const dateDetail = dateParts.length ? (dateParts.length === 1 ? dateParts[0] : `${dateParts[0]} +${dateParts.length - 1}`) : copy.any;
  const dateKeys = ['year', 'release'];
  const dateRangeNames = { dateAdded: copy.addedDate, completedDate: copy.completedDate, releaseDate: copy.releaseDate };
  // The sort Date level uses the same names and order as the filter one: added, completed, release, start, air.
  const dateSortNames = { date_added: copy.addedDate, end_date: copy.completedDate, release_date: copy.releaseDate,
    start_date: copy.startDate, next_episode_air_date: copy.airDate };
  const dateSortOrder = Object.keys(dateSortNames);
  const dateSorts = (options.sorts || []).filter(isDateSort)
    .map((option) => ({ ...option, label: dateSortNames[option.value] || option.label }))
    .sort((a, b) => {
      const rank = (option) => (dateSortOrder.includes(option.value) ? dateSortOrder.indexOf(option.value) : dateSortOrder.length);
      return rank(a) - rank(b);
    });
  const rootRanges = ranges.filter((range) => !['releaseDate', 'completedDate', 'dateAdded'].includes(range.key));

  // The rows of a section's options: any/all modes, a search field when long, a clear row, then the options.
  const sectionRows = (section) => {
    const isMulti = section.type === 'multi';
    const value = sectionValue(filters, section);
    const mode = (filters.modes || {})[section.key] || section.modeValue;
    const clearValue = sectionClearValue(section);
    const all = section.options || [];
    const term = panelSearch.trim().toLowerCase();
    const visible = term ? all.filter((option) => String(option.label).toLowerCase().includes(term)) : all;
    const hasClearOption = all.some((option) => String(option.value) === String(clearValue));
    const selectedValues = isMulti ? (Array.isArray(value) ? value : []) : [];
    const clear = () => {
      emit({ type: 'clearField', key: section.key });
      if (!isMulti) setMenu(null);
    };
    return <>
      {(section.modes || []).map((item) => <DropdownItem key={item.value} accessibilityRole="radio"
        label={item.label} checked={mode === item.value} palette={menuPalette}
        onPress={() => emit({ type: 'fieldMode', key: section.key, value: item.value })} />)}
      {section.modes?.length ? <DropdownSeparator palette={menuPalette} /> : null}
      {section.searchable && all.length > 8 ? <DropdownField label={copy.searchOptions} value={panelSearch}
        onChangeText={setPanelSearch} placeholder={copy.searchOptions} palette={menuPalette} /> : null}
      {isMulti ? <DropdownItem accessibilityRole="checkbox" label={copy.all} checked={selectedValues.length === 0}
        palette={menuPalette} onPress={clear} /> : !hasClearOption ? <DropdownItem accessibilityRole="radio"
        label={copy.any} checked={!sectionIsActive(section, value)} palette={menuPalette} onPress={clear} /> : null}
      {visible.map((option) => <DropdownItem key={option.value} accessibilityRole={isMulti ? 'checkbox' : 'radio'}
        label={option.label} disabled={option.disabled} palette={menuPalette}
        checked={isMulti ? selectedValues.some((item) => String(item) === String(option.value)) : String(value) === String(option.value)}
        onPress={() => {
          emit({ type: isMulti ? 'toggleField' : 'field', key: section.key, value: option.value });
          if (!isMulti) setMenu(null);
        }} />)}
      {!visible.length ? <DropdownItem label={copy.noOptions} disabled palette={menuPalette} /> : null}
    </>;
  };

  // The fields of a range: how recent, a unit, From and To, then Apply and Clear.
  const rangeRows = (range) => <>
    {range.relative ? <>
      <DropdownField label={copy.within} value={String(rangeDraft.within || '')} keyboardType="number-pad" placeholder="7"
        accessibilityLabel={`${range.label} ${copy.within}`} onChangeText={(value) => updateDraft('within', value)}
        palette={menuPalette} />
      {(range.units || []).map((unit) => <DropdownItem key={unit.value} accessibilityRole="radio" label={unit.label}
        checked={rangeDraft.unit === unit.value} palette={menuPalette} onPress={() => updateDraft('unit', unit.value)} />)}
      <DropdownSeparator palette={menuPalette} />
    </> : null}
    <DropdownField label={copy.from} value={String(rangeDraft.from || '')} placeholder={range.placeholder || ''}
      inputMode={range.type === 'number' ? 'decimal' : 'text'} accessibilityLabel={`${range.label} ${copy.from}`}
      onChangeText={(value) => updateDraft('from', value)} palette={menuPalette} />
    <DropdownField label={copy.to} value={String(rangeDraft.to || '')} placeholder={range.placeholder || ''}
      inputMode={range.type === 'number' ? 'decimal' : 'text'} accessibilityLabel={`${range.label} ${copy.to}`}
      onChangeText={(value) => updateDraft('to', value)} palette={menuPalette} />
    <DropdownSeparator palette={menuPalette} />
    <DropdownItem label={copy.apply} accessibilityRole="button" palette={menuPalette} onPress={() => applyRange(range)} />
    <DropdownItem label={copy.clearRange} accessibilityRole="button" palette={menuPalette} onPress={() => clearRange(range)} />
  </>;

  return <View style={styles.bar}>
    <View style={styles.triggers}>
      <Trigger
        label={`${copy.filters}: ${filterSummary(filters, options, copy)}`}
        value={filterSummary(filters, options, copy)}
        expanded={toolbar ? menu?.key === 'filters' : openSheet === 'filters'}
        plain={toolbar}
        disabled={isDisabled}
        onPress={(anchor) => pressTrigger('filters', anchor)}
        nodeRef={triggerNodes.filters}
        styles={styles} />

      <Trigger
        label={`${copy.status}: ${statusSummary(filters, options, copy)}`}
        value={statusSummary(filters, options, copy)}
        expanded={toolbar ? menu?.key === 'status' : openSheet === 'status'}
        plain={toolbar}
        disabled={isDisabled}
        onPress={(anchor) => pressTrigger('status', anchor)}
        nodeRef={triggerNodes.status}
        styles={styles} />

      {toolbar ? <View style={styles.sortControl}>
        <Trigger
          label={`${copy.sort}: ${optionLabel(options.sorts, filters.sort)}`}
          value={optionLabel(options.sorts, filters.sort)}
          expanded={menu?.key === 'sort'}
          plain
          caret={false}
          disabled={isDisabled}
          onPress={(anchor) => pressTrigger('sort', anchor)}
          nodeRef={triggerNodes.sort}
          styles={styles} />
        {/* The arrow is its own control: it flips the direction, so the dropdown has no direction rows. */}
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={`${copy.sort} ${filters.direction === 'asc' ? copy.descending : copy.ascending}`}
          accessibilityState={{ disabled: isDisabled }}
          disabled={isDisabled}
          onPress={() => emit({ type: 'set', key: 'direction', value: filters.direction === 'asc' ? 'desc' : 'asc' })}
          style={({ pressed }) => [styles.sortArrow, pressed && styles.pressed]}>
          <View style={filters.direction === 'asc' ? styles.caretUp : styles.caret} />
        </Pressable>
      </View> : <Trigger
        label={`${copy.sort}: ${optionLabel(options.sorts, filters.sort)} ${filters.direction === 'asc' ? copy.ascending : copy.descending}`}
        value={sortValue}
        expanded={openSheet === 'sort'}
        disabled={isDisabled}
        onPress={(anchor) => pressTrigger('sort', anchor)}
        styles={styles} />}

      {!toolbar && (views.length || savedViews.length) ? <Trigger
        label={`${copy.view}: ${viewValue || copy.savedViews}`}
        value={viewValue || copy.savedViews}
        expanded={openSheet === 'view'}
        disabled={isDisabled}
        onPress={() => setOpenSheet(openSheet === 'view' ? null : 'view')}
        styles={styles} /> : null}

      {!toolbar && ranges.length > 0 ? <Trigger
        label={`${copy.range}: ${ranges.map((range) => `${range.label} ${rangeSummary(range, (filters.ranges || {})[range.key])}`).join(', ')}`}
        value={copy.range}
        expanded={Boolean(activeRange)}
        disabled={isDisabled}
        onPress={() => openRange(ranges[0])}
        styles={styles} /> : null}

      {showClear && count > 0 ? <Pressable
        accessibilityRole="button"
        accessibilityLabel={copy.clearAll}
        accessibilityState={{ disabled: isDisabled }}
        disabled={isDisabled}
        onPress={clearAll}
        style={({ pressed }) => [styles.clear, pressed && styles.pressed]}>
        <Text style={styles.clearText}>{copy.clear}</Text>
      </Pressable> : null}

      {trailing ? <View style={styles.trailing}>{trailing}</View> : null}
    </View>

    {toolbar ? null : <View style={styles.meta} accessibilityLiveRegion="polite">
      {busy ? <><ActivityIndicator size="small" color={colors.accent} />
        <Text style={styles.metaText}>{copy.updating}</Text></> : null}
      {typeof resultCount === 'number' && !busy ? <Text style={styles.metaText}>
        {resultCount} {copy.items}
      </Text> : null}
    </View>}

    {toolbar ? <DropdownMenu open={Boolean(menu)} anchor={menu?.anchor} align="start" palette={menuPalette} closeLabel={copy.close}
      estimatedHeight={420} onClose={closeMenu}>
      <ScrollView keyboardShouldPersistTaps="handled">
        {menu?.key === 'filters' && panel === 'mediaTypes' ? <>
          <DropdownBackItem label={copy.mediaType} palette={menuPalette} onPress={goBack} />
          <DropdownSeparator palette={menuPalette} />
          {(options.mediaTypes || []).map((option) => <DropdownItem key={option.value} accessibilityRole="checkbox"
            label={option.label} checked={(filters.mediaTypes || []).includes(option.value)} disabled={option.disabled}
            palette={menuPalette} onPress={() => toggleMulti('mediaTypes', option.value)} />)}
        </> : null}
        {menu?.key === 'filters' && !panel ? <>
          {(options.mediaTypes || []).length ? <DropdownItem label={copy.mediaType} chevron palette={menuPalette}
            detail={typeSummary(filters, options, copy)}
            onPress={() => setMenu(current => ({ ...current, panel: 'mediaTypes' }))} /> : null}
          {(options.mediaTypes || []).length && (sections.length || hasDate) ? <DropdownSeparator palette={menuPalette} /> : null}
          {(() => {
            let dateShown = false;
            const rows = [];
            const dateRow = <DropdownItem key="date" label={copy.date} chevron palette={menuPalette} detail={dateDetail}
              onPress={() => setMenu(current => ({ ...current, panel: 'date', parent: null }))} />;
            sections.forEach((section) => {
              if (dateKeys.includes(section.key)) {
                if (hasDate && !dateShown) { rows.push(dateRow); dateShown = true; }
                return;
              }
              rows.push(<DropdownItem key={section.key} label={section.label} palette={menuPalette}
                detail={sectionSummary(section, filters) || section.idleLabel || copy.any}
                chevron onPress={() => openSection(section)} />);
            });
            if (hasDate && !dateShown) rows.push(dateRow);
            return rows;
          })()}
          {rootRanges.length ? <DropdownSeparator palette={menuPalette} /> : null}
          {rootRanges.map((range) => <DropdownItem key={range.key} palette={menuPalette}
            label={sections.some((section) => section.label === range.label) ? `${range.label} range` : range.label}
            chevron detail={rangeSummary(range, (filters.ranges || {})[range.key])} onPress={() => openRange(range)} />)}
          <DropdownSeparator palette={menuPalette} />
          <DropdownItem label={copy.clearAll} palette={menuPalette} onPress={clearAll} />
        </> : null}
        {menu?.key === 'filters' && panel === 'date' ? <>
          <DropdownBackItem label={copy.date} palette={menuPalette} onPress={goBack} />
          <DropdownSeparator palette={menuPalette} />
          {addedRange ? <DropdownItem label={copy.addedDate} chevron palette={menuPalette}
            detail={rangeText(addedRange) || copy.any} onPress={() => openRange(addedRange, 'date')} /> : null}
          {completedRange ? <DropdownItem label={copy.completedDate} chevron palette={menuPalette}
            detail={rangeText(completedRange) || copy.any} onPress={() => openRange(completedRange, 'date')} /> : null}
          {releaseRange || yearSection ? <DropdownItem label={copy.releaseDate} chevron palette={menuPalette}
            detail={releaseDateText || copy.any}
            onPress={() => (releaseRange ? openRange(releaseRange, 'date') : openSection(yearSection, 'date'))} /> : null}
          {releaseSection ? <DropdownItem label={copy.releaseStatus} chevron palette={menuPalette}
            detail={sectionText(releaseSection) || copy.any} onPress={() => openSection(releaseSection, 'date')} /> : null}
        </> : null}
        {menu?.key === 'filters' && panelSection ? <>
          <DropdownBackItem label={panelSection.key === 'year' ? copy.releaseDate : panelSection.key === 'release' ? copy.releaseStatus : panelSection.label} palette={menuPalette}
            onPress={goBack} />
          <DropdownSeparator palette={menuPalette} />
          {sectionRows(panelSection)}
        </> : null}
        {menu?.key === 'filters' && panelRange && rangeDraft ? <>
          <DropdownBackItem label={dateRangeNames[panelRange.key] || panelRange.label} palette={menuPalette}
            onPress={goBack} />
          <DropdownSeparator palette={menuPalette} />
          {rangeRows(panelRange)}
          {panelRange.key === 'releaseDate' && yearSection ? <>
            <DropdownSeparator palette={menuPalette} />
            <DropdownHeading palette={menuPalette}>{yearSection.label}</DropdownHeading>
            {sectionRows(yearSection)}
          </> : null}
        </> : null}
        {menu?.key === 'status' ? <>
          {(options.statuses || []).map((option) => <DropdownItem key={option.value} accessibilityRole="checkbox"
            label={option.label} checked={(filters.statuses || []).includes(option.value)} disabled={option.disabled}
            palette={menuPalette} onPress={() => toggleMulti('statuses', option.value)} />)}
          <DropdownSeparator palette={menuPalette} />
          <DropdownItem label={copy.clearStatus} palette={menuPalette}
            onPress={() => { emit({ type: 'set', key: 'statuses', value: [] }); setMenu(null); }} />
        </> : null}
        {menu?.key === 'sort' && panel === 'sortDate' ? <>
          <DropdownBackItem label={copy.date} palette={menuPalette} onPress={goBack} />
          <DropdownSeparator palette={menuPalette} />
          {dateSorts.map((option) => <DropdownItem key={option.value} accessibilityRole="radio" label={option.label}
            checked={filters.sort === option.value} disabled={option.disabled} palette={menuPalette}
            onPress={() => { chooseSingle('sort', option.value); setMenu(null); }} />)}
        </> : null}
        {menu?.key === 'sort' && !panel ? (() => {
          // The date-based sorts sit under one Date row, in the place of the first of them.
          let dateShown = false;
          const rows = [];
          (options.sorts || []).forEach((option) => {
            if (isDateSort(option)) {
              if (!dateShown) {
                dateShown = true;
                rows.push(<DropdownItem key="date" label={copy.date} chevron palette={menuPalette}
                  detail={dateSorts.find((item) => item.value === filters.sort)?.label}
                  onPress={() => setMenu(current => ({ ...current, panel: 'sortDate' }))} />);
              }
              return;
            }
            rows.push(<DropdownItem key={option.value} accessibilityRole="radio" label={option.label}
              checked={filters.sort === option.value} disabled={option.disabled} palette={menuPalette}
              onPress={() => { chooseSingle('sort', option.value); setMenu(null); }} />);
          });
          return rows;
        })() : null}
      </ScrollView>
    </DropdownMenu> : null}

    <Modal
      visible={Boolean(openSheet)}
      transparent
      animationType="fade"
      onRequestClose={closeSheet}>
      <View style={styles.overlay}>
        <Pressable accessibilityRole="button" accessibilityLabel={copy.close} style={styles.backdrop} onPress={closeSheet} />
        <View style={styles.sheet} accessibilityViewIsModal>
          <View style={styles.sheetHeader}>
            <Text style={styles.sheetTitle}>{sheetTitle}</Text>
            <ModalCloseButton
              accessibilityRole="button"
              accessibilityLabel={copy.close}
              onPress={closeSheet}
              style={({ pressed }) => [styles.close, pressed && styles.pressed]}>
              <Text style={styles.closeText}>×</Text>
            </ModalCloseButton>
          </View>
          <ScrollView style={styles.sheetBody} contentContainerStyle={styles.sheetContent} keyboardShouldPersistTaps="handled">
            {openSheet === 'filters' ? <FiltersSheet
              filters={filters}
              options={options}
              copy={copy}
              styles={styles}
              sections={sections}
              onToggleType={(value) => toggleMulti('mediaTypes', value)}
              onOpenRange={openRange}
              onOpenSection={(section) => {
                setOpenSheet(`section:${section.key}`);
                setRangeDraft(null);
              }}
              onClear={clearAll} /> : null}

            {openSheet === 'status' ? <StatusSheet
              filters={filters}
              options={options}
              copy={copy}
              styles={styles}
              onToggle={(value) => toggleMulti('statuses', value)}
              onClear={() => emit({ type: 'set', key: 'statuses', value: [] })} /> : null}

            {openSheet === 'sort' ? <SortSheet
              filters={filters}
              options={options}
              copy={copy}
              styles={styles}
              onChoose={(value) => chooseSingle('sort', value)}
              onDirection={(value) => emit({ type: 'set', key: 'direction', value })} /> : null}

            {openSheet === 'view' ? <ViewSheet
              filters={filters}
              options={options}
              copy={copy}
              styles={styles}
              savedViews={savedViews}
              onChoose={(value) => chooseSingle('view', value)}
              onSavedView={chooseSavedView} /> : null}

            {activeSection ? <SectionSheet
              section={activeSection}
              value={sectionValue(filters, activeSection)}
              mode={(filters.modes || {})[activeSection.key] || activeSection.modeValue}
              copy={copy}
              styles={styles}
              placeholderColor={colors.textMuted}
              onSelect={(option) => {
                emit({ type: activeSection.type === 'multi' ? 'toggleField' : 'field', key: activeSection.key, value: option.value });
                if (activeSection.type !== 'multi') closeSheet();
              }}
              onClear={() => {
                emit({ type: 'clearField', key: activeSection.key });
                if (activeSection.type !== 'multi') closeSheet();
              }}
              onMode={(mode) => emit({ type: 'fieldMode', key: activeSection.key, value: mode })} /> : null}

            {activeRange && rangeDraft ? <RangeSheet
              range={activeRange}
              draft={rangeDraft}
              copy={copy}
              styles={styles}
              placeholderColor={colors.textMuted}
              onChange={updateDraft}
              onApply={() => applyRange(activeRange)}
              onClear={() => clearRange(activeRange)} /> : null}
          </ScrollView>
        </View>
      </View>
    </Modal>
  </View>;
}

function Trigger({ label, value, expanded, disabled, onPress, styles, plain = false, caret = true, nodeRef }) {
  const ref = useRef(null);
  // The plain (toolbar) triggers report their window rectangle so the dropdown opens under them.
  const press = () => {
    if (plain && ref.current?.measureInWindow) {
      ref.current.measureInWindow((x, y, width, height) => onPress({ x, y, width, height }));
    } else {
      onPress(null);
    }
  };
  return <Pressable
    ref={(node) => { ref.current = node; if (nodeRef) nodeRef.current = node; }}
    accessibilityRole="button"
    accessibilityLabel={label}
    accessibilityState={{ expanded, disabled }}
    disabled={disabled}
    onPress={press}
    style={({ pressed }) => [styles.trigger, plain && styles.triggerPlain, expanded && !plain && styles.triggerOpen, pressed && styles.pressed]}>
    <Text numberOfLines={1} style={[styles.triggerText, plain && styles.triggerTextPlain]}>{value}</Text>
    {plain ? (caret ? <View style={styles.caret} /> : null) : <Text style={styles.chevron}>▾</Text>}
  </Pressable>;
}

function Section({ title, children, styles }) {
  return <View style={styles.section}>
    <Text style={styles.sectionTitle}>{title}</Text>
    {children}
  </View>;
}

function OptionRow({ label, selected, disabled, role = 'radio', onPress, styles }) {
  return <Pressable
    accessibilityRole={role}
    accessibilityLabel={label}
    accessibilityState={{ checked: selected, disabled }}
    disabled={disabled}
    onPress={onPress}
    style={({ pressed }) => [styles.option, selected && styles.optionSelected, pressed && styles.pressed]}>
    <Text style={[styles.optionText, disabled && styles.disabledText]}>{label}</Text>
    {selected ? <Text style={styles.check}>✓</Text> : null}
  </Pressable>;
}

function FiltersSheet({ filters, options, copy, styles, sections, onToggleType, onOpenRange, onOpenSection, onClear }) {
  return <View style={styles.sheetStack}>
    <Section title={copy.mediaType} styles={styles}>
      {(options.mediaTypes || []).map((option) => <OptionRow
        key={option.value}
        label={option.label}
        role="checkbox"
        selected={(filters.mediaTypes || []).includes(option.value)}
        disabled={option.disabled}
        onPress={() => onToggleType(option.value)}
        styles={styles} />)}
    </Section>

    {(sections || []).length ? <View style={styles.sheetStack}>
      {(sections || []).map((section) => {
        const summary = sectionSummary(section, filters);
        return <Pressable
          key={section.key}
          accessibilityRole="button"
          accessibilityLabel={`${section.label}: ${summary || section.idleLabel || copy.any}`}
          onPress={() => onOpenSection(section)}
          style={({ pressed }) => [styles.option, pressed && styles.pressed]}>
          <Text style={styles.optionText}>{section.label}</Text>
          <Text style={styles.optionValue}>{summary || section.idleLabel || copy.any}</Text>
          <Text style={styles.chevron}>›</Text>
        </Pressable>;
      })}
    </View> : null}

    {(options.ranges || []).length ? <Section title={copy.range} styles={styles}>
      {(options.ranges || []).map((range) => <Pressable
        key={range.key}
        accessibilityRole="button"
        accessibilityLabel={`${range.label}: ${rangeSummary(range, (filters.ranges || {})[range.key])}`}
        onPress={() => onOpenRange(range)}
        style={({ pressed }) => [styles.option, pressed && styles.pressed]}>
        <Text style={styles.optionText}>{range.label}</Text>
        <Text style={styles.optionValue}>{rangeSummary(range, (filters.ranges || {})[range.key])}</Text>
        <Text style={styles.chevron}>›</Text>
      </Pressable>)}
    </Section> : null}

    <Pressable
      accessibilityRole="button"
      accessibilityLabel={copy.clearAll}
      onPress={onClear}
      style={({ pressed }) => [styles.clearAll, pressed && styles.pressed]}>
      <Text style={styles.clearAllText}>{copy.clearAll}</Text>
    </Pressable>
  </View>;
}

function SectionSheet({ section, value, mode, copy, styles, placeholderColor, onSelect, onClear, onMode }) {
  const [search, setSearch] = useState('');
  const options = section.options || [];
  const clearValue = sectionClearValue(section);
  const isMulti = section.type === 'multi';
  const term = search.trim().toLowerCase();
  const visible = term
    ? options.filter((option) => String(option.label).toLowerCase().includes(term))
    : options;
  const hasClearOption = options.some((option) => String(option.value) === String(clearValue));
  const selectedValues = isMulti ? (Array.isArray(value) ? value : []) : [];
  return <View style={styles.sheetStack}>
    {section.modes?.length ? <View style={styles.segments}>
      {section.modes.map((item) => <Pressable
        key={item.value}
        accessibilityRole="button"
        accessibilityLabel={item.label}
        accessibilityState={{ selected: mode === item.value }}
        onPress={() => onMode(item.value)}
        style={({ pressed }) => [styles.segment, mode === item.value && styles.segmentSelected, pressed && styles.pressed]}>
        <Text style={[styles.segmentText, mode === item.value && styles.segmentTextSelected]}>{item.label}</Text>
      </Pressable>)}
    </View> : null}

    {section.searchable && options.length > 8 ? <TextInput
      accessibilityLabel={copy.searchOptions}
      value={search}
      onChangeText={setSearch}
      placeholder={copy.searchOptions}
      placeholderTextColor={placeholderColor}
      style={styles.input} /> : null}

    {isMulti ? <OptionRow
      label={copy.all}
      role="checkbox"
      selected={selectedValues.length === 0}
      onPress={onClear}
      styles={styles} /> : !hasClearOption ? <OptionRow
        label={copy.any}
        role="radio"
        selected={!sectionIsActive(section, value)}
        onPress={onClear}
        styles={styles} /> : null}

    {visible.map((option) => {
      const selected = isMulti
        ? selectedValues.some((item) => String(item) === String(option.value))
        : String(value) === String(option.value);
      return <OptionRow
        key={option.value}
        label={option.label}
        role={isMulti ? 'checkbox' : 'radio'}
        selected={selected}
        disabled={option.disabled}
        onPress={() => onSelect(option)}
        styles={styles} />;
    })}

    {!visible.length ? <Text style={styles.metaText}>{copy.noOptions}</Text> : null}

    {isMulti && selectedValues.length ? <Pressable
      accessibilityRole="button"
      accessibilityLabel={`${copy.clear} ${section.label}`}
      onPress={onClear}
      style={({ pressed }) => [styles.clearAll, pressed && styles.pressed]}>
      <Text style={styles.clearAllText}>{copy.clear}</Text>
    </Pressable> : null}
  </View>;
}

function StatusSheet({ filters, options, copy, styles, onToggle, onClear }) {
  return <View style={styles.sheetStack}>
    {(options.statuses || []).map((option) => <OptionRow
      key={option.value}
      label={option.label}
      role="checkbox"
      selected={(filters.statuses || []).includes(option.value)}
      disabled={option.disabled}
      onPress={() => onToggle(option.value)}
      styles={styles} />)}
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={copy.clearStatus}
      onPress={onClear}
      style={({ pressed }) => [styles.clearAll, pressed && styles.pressed]}>
      <Text style={styles.clearAllText}>{copy.clearStatus}</Text>
    </Pressable>
  </View>;
}

function SortSheet({ filters, options, copy, styles, onChoose, onDirection }) {
  return <View style={styles.sheetStack}>
    {(options.sorts || []).map((option) => <OptionRow
      key={option.value}
      label={option.label}
      role="radio"
      selected={filters.sort === option.value}
      disabled={option.disabled}
      onPress={() => onChoose(option.value)}
      styles={styles} />)}
    <Section title={copy.direction} styles={styles}>
      <View style={styles.segments}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={`Sort ${copy.ascending}`}
          accessibilityState={{ selected: filters.direction === 'asc' }}
          onPress={() => onDirection('asc')}
          style={({ pressed }) => [styles.segment, filters.direction === 'asc' && styles.segmentSelected, pressed && styles.pressed]}>
          <Text style={[styles.segmentText, filters.direction === 'asc' && styles.segmentTextSelected]}>A–Z</Text>
        </Pressable>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={`Sort ${copy.descending}`}
          accessibilityState={{ selected: filters.direction !== 'asc' }}
          onPress={() => onDirection('desc')}
          style={({ pressed }) => [styles.segment, filters.direction !== 'asc' && styles.segmentSelected, pressed && styles.pressed]}>
          <Text style={[styles.segmentText, filters.direction !== 'asc' && styles.segmentTextSelected]}>Z–A</Text>
        </Pressable>
      </View>
    </Section>
  </View>;
}

function ViewSheet({ filters, options, copy, styles, savedViews, onChoose, onSavedView }) {
  return <View style={styles.sheetStack}>
    {(options.views || []).map((option) => <OptionRow
      key={option.value}
      label={option.label}
      role="radio"
      selected={filters.view === option.value}
      disabled={option.disabled}
      onPress={() => onChoose(option.value)}
      styles={styles} />)}
    {savedViews.length ? <Section title={copy.savedViews} styles={styles}>
      {savedViews.map((view) => <OptionRow
        key={view.id}
        label={view.label}
        role="radio"
        selected={view.active}
        disabled={view.disabled}
        onPress={() => onSavedView(view.id)}
        styles={styles} />)}
    </Section> : null}
  </View>;
}

function RangeSheet({ range, draft, copy, styles, placeholderColor, onChange, onApply, onClear }) {
  return <View style={styles.sheetStack}>
    {range.relative ? <View style={styles.field}>
      <Text style={styles.fieldLabel}>{copy.within}</Text>
      <TextInput
        accessibilityLabel={`${range.label} ${copy.within}`}
        value={String(draft.within || '')}
        onChangeText={(value) => onChange('within', value)}
        keyboardType="number-pad"
        placeholder="7"
        placeholderTextColor={placeholderColor}
        style={styles.input} />
      <View style={styles.segments}>
        {(range.units || []).map((unit) => <Pressable
          key={unit.value}
          accessibilityRole="button"
          accessibilityLabel={unit.label}
          accessibilityState={{ selected: draft.unit === unit.value }}
          onPress={() => onChange('unit', unit.value)}
          style={({ pressed }) => [styles.segment, draft.unit === unit.value && styles.segmentSelected, pressed && styles.pressed]}>
          <Text style={[styles.segmentText, draft.unit === unit.value && styles.segmentTextSelected]}>{unit.label}</Text>
        </Pressable>)}
      </View>
    </View> : null}

    <View style={styles.field}>
      <Text style={styles.fieldLabel}>{copy.from}</Text>
      <TextInput
        accessibilityLabel={`${range.label} ${copy.from}`}
        value={String(draft.from || '')}
        onChangeText={(value) => onChange('from', value)}
        inputMode={range.type === 'number' ? 'decimal' : 'text'}
        placeholder={range.placeholder || ''}
        placeholderTextColor={placeholderColor}
        style={styles.input} />
    </View>

    <View style={styles.field}>
      <Text style={styles.fieldLabel}>{copy.to}</Text>
      <TextInput
        accessibilityLabel={`${range.label} ${copy.to}`}
        value={String(draft.to || '')}
        onChangeText={(value) => onChange('to', value)}
        inputMode={range.type === 'number' ? 'decimal' : 'text'}
        placeholder={range.placeholder || ''}
        placeholderTextColor={placeholderColor}
        style={styles.input} />
    </View>

    <View style={styles.rangeActions}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`${copy.apply} ${range.label}`}
        onPress={onApply}
        style={({ pressed }) => [styles.apply, pressed && styles.pressed]}>
        <Text style={styles.applyText}>{copy.apply}</Text>
      </Pressable>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`${copy.clearRange} ${range.label}`}
        onPress={onClear}
        style={({ pressed }) => [styles.clearRange, pressed && styles.pressed]}>
        <Text style={styles.clearRangeText}>{copy.clearRange}</Text>
      </Pressable>
    </View>
  </View>;
}

function createStyles(colors, compact) {
  return StyleSheet.create({
    bar: { gap: 8 },
    triggers: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 8 },
    trailing: { marginLeft: 'auto' },
    trigger: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 6,
      minHeight: 36,
      paddingHorizontal: 12,
      borderRadius: 8,
      borderWidth: 1,
      borderColor: colors.border,
      backgroundColor: colors.surface,
      maxWidth: compact ? '100%' : 240,
    },
    // Toolbar variant: the web's bare text dropdowns, a label and a small triangle.
    triggerPlain: { borderWidth: 0, backgroundColor: 'transparent', paddingHorizontal: 8, gap: 8 },
    triggerTextPlain: { fontSize: 16, fontWeight: '400' },
    caret: {
      width: 0,
      height: 0,
      borderLeftWidth: 5,
      borderRightWidth: 5,
      borderTopWidth: 6,
      borderLeftColor: 'transparent',
      borderRightColor: 'transparent',
      borderTopColor: colors.textSecondary,
    },
    sortControl: { flexDirection: 'row', alignItems: 'center' },
    sortArrow: { minWidth: 32, minHeight: 36, alignItems: 'center', justifyContent: 'center' },
    caretUp: {
      width: 0,
      height: 0,
      borderLeftWidth: 5,
      borderRightWidth: 5,
      borderBottomWidth: 6,
      borderLeftColor: 'transparent',
      borderRightColor: 'transparent',
      borderBottomColor: colors.textSecondary,
    },
    triggerOpen: { borderColor: colors.accent, backgroundColor: colors.surfaceStrong },
    triggerText: { color: colors.textSecondary, fontSize: 13, fontWeight: '600' },
    chevron: { color: colors.textMuted, fontSize: 12 },
    pressed: { opacity: 0.72 },
    disabledText: { color: colors.disabled },
    clear: { minHeight: 36, paddingHorizontal: 12, justifyContent: 'center' },
    clearText: { color: colors.accent, fontSize: 13, fontWeight: '600' },
    meta: { minHeight: 20, flexDirection: 'row', alignItems: 'center', gap: 8 },
    metaText: { color: colors.textMuted, fontSize: 12 },
    overlay: { flex: 1, justifyContent: compact ? 'flex-end' : 'center', alignItems: 'center', backgroundColor: colors.overlay },
    backdrop: { position: 'absolute', top: 0, right: 0, bottom: 0, left: 0 },
    sheet: {
      width: compact ? '100%' : 420,
      maxHeight: compact ? '82%' : '78%',
      backgroundColor: colors.surface,
      borderWidth: 1,
      borderColor: colors.border,
      borderTopLeftRadius: compact ? 16 : 12,
      borderTopRightRadius: compact ? 16 : 12,
      borderBottomLeftRadius: compact ? 0 : 12,
      borderBottomRightRadius: compact ? 0 : 12,
      overflow: 'hidden',
    },
    sheetHeader: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: 8,
      paddingHorizontal: 16,
      paddingVertical: 12,
      borderBottomWidth: 1,
      borderBottomColor: colors.border,
    },
    sheetTitle: { color: colors.text, fontSize: 16, fontWeight: '700' },
    close: { width: 32, height: 32, alignItems: 'center', justifyContent: 'center', borderRadius: 8, backgroundColor: colors.surfaceStrong },
    closeText: { color: colors.textSecondary, fontSize: 20, lineHeight: 22 },
    sheetBody: { flexGrow: 0 },
    sheetContent: { padding: 12, gap: 12 },
    sheetStack: { gap: 12 },
    section: { gap: 6 },
    sectionTitle: { color: colors.textMuted, fontSize: 11, fontWeight: '700', letterSpacing: 0.8, textTransform: 'uppercase' },
    option: {
      minHeight: 42,
      flexDirection: 'row',
      alignItems: 'center',
      gap: 8,
      paddingHorizontal: 10,
      borderRadius: 8,
      backgroundColor: colors.surfaceStrong,
    },
    optionSelected: { backgroundColor: colors.accentStrong },
    optionText: { flexShrink: 1, color: colors.textSecondary, fontSize: 14 },
    optionValue: { marginLeft: 'auto', color: colors.textMuted, fontSize: 12 },
    check: { marginLeft: 'auto', color: colors.accentText, fontSize: 14, fontWeight: '700' },
    clearAll: { minHeight: 42, alignItems: 'center', justifyContent: 'center', borderRadius: 8, borderWidth: 1, borderColor: colors.border },
    clearAllText: { color: colors.textSecondary, fontSize: 13, fontWeight: '600' },
    segments: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
    segment: { minHeight: 36, paddingHorizontal: 12, alignItems: 'center', justifyContent: 'center', borderRadius: 8, backgroundColor: colors.surfaceStrong },
    segmentSelected: { backgroundColor: colors.accentStrong },
    segmentText: { color: colors.textSecondary, fontSize: 13, fontWeight: '600' },
    segmentTextSelected: { color: colors.accentText },
    field: { gap: 6 },
    fieldLabel: { color: colors.textMuted, fontSize: 12, fontWeight: '600' },
    input: {
      minHeight: 40,
      paddingHorizontal: 10,
      borderRadius: 8,
      borderWidth: 1,
      borderColor: colors.border,
      backgroundColor: colors.background,
      color: colors.text,
      fontSize: 14,
    },
    rangeActions: { flexDirection: 'row', gap: 8 },
    apply: { flex: 1, minHeight: 42, alignItems: 'center', justifyContent: 'center', borderRadius: 8, backgroundColor: colors.accentStrong },
    applyText: { color: colors.accentText, fontSize: 14, fontWeight: '700' },
    clearRange: { minHeight: 42, paddingHorizontal: 16, alignItems: 'center', justifyContent: 'center', borderRadius: 8, borderWidth: 1, borderColor: colors.border },
    clearRangeText: { color: colors.textSecondary, fontSize: 14, fontWeight: '600' },
  });
}
