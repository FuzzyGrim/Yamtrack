import React, { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, TextInput, View, useWindowDimensions } from 'react-native';
import { Button } from './Button';
import { Icon } from './Icon';
import { ModalCloseButton } from './Modal';
import { DateTimePicker } from './DateTimePicker';
import {
  FormError,
  FormField,
  FormHeader,
  FormStatus,
  FormTextField,
} from './MediaCardFormControls';
import { MarkdownNotes } from './MarkdownNotes';
import { RatingStars } from './RatingStars';
import { SelectField } from './SelectField';
import { TrackTabs } from './TrackFormTabs';
import { validateTrackValues } from './mediaCardForms';
import { valueFromDate } from './dateTime';

const DEFAULT_LABELS = {
  title: 'Tracking',
  close: 'Close',
  loading: 'Loading',
  loadFailed: "Couldn't load this form.",
  retry: 'Try again',
  save: 'Save',
  saving: 'Saving',
  delete: 'Delete',
  required: '%s is required.',
  invalidNumber: 'Enter a number.',
  minimum: 'Must be at least %s.',
  maximum: 'Must be at most %s.',
  dateOrder: 'End date must be on or after the start date.',
  dateTime: {},
};

const DEFAULT_PALETTE = {
  text: '#fafafa',
  textSecondary: '#d4d4d8',
  textMuted: '#a1a1aa',
  surface: '#27272a',
  surfaceDim: '#18181b',
  surfaceStrong: '#3f3f46',
  border: '#3f3f46',
  inputBorder: '#4b5563',
  accent: '#6366f1',
  accentContrast: '#ffffff',
  danger: '#f87171',
  radius: 8,
  focusStyle: {},
  dateTime: {},
  rating: {
    lit: '#f59e0b',
    unlit: '#52525b',
    muted: '#a1a1aa',
    focusStyle: {},
  },
};

// Same two strokes as templates/app/icons/x.svg, so the close button matches the old dialog.
const X_ICON = {
  viewBox: '0 0 24 24',
  root: { fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' },
  children: [
    { tag: 'path', attrs: { d: 'M18 6 6 18' }, children: [] },
    { tag: 'path', attrs: { d: 'm6 6 12 12' }, children: [] },
  ],
};

const chevron = d => ({
  viewBox: '0 0 24 24',
  root: { fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' },
  children: [{ tag: 'path', attrs: { d }, children: [] }],
});
const CHEVRON_UP = chevron('m18 15-6-6-6 6');
const CHEVRON_DOWN = chevron('m6 9 6 6 6-6');

function stepValue(value, field, direction) {
  const step = Number(field.step) || 1;
  const current = value === '' || value == null ? null : Number(value);
  let next = (Number.isFinite(current) ? current : (direction > 0 ? (field.min ?? 0) - step : (field.max ?? 0) + step))
    + direction * step;
  if (field.min != null) next = Math.max(Number(field.min), next);
  if (field.max != null) next = Math.min(Number(field.max), next);
  const decimals = (String(step).split('.')[1] || '').length;
  return next.toFixed(decimals);
}

function NumberStepper({ field, value, onChange, error, palette, labels }) {
  const colors = { color: palette.textSecondary };
  const button = (direction, icon, name) => <Pressable accessibilityRole="button"
    accessibilityLabel={`${name} ${field.label}`} onPress={() => onChange(stepValue(value, field, direction))}
    style={({ hovered }) => [styles.stepButton, { borderColor: palette.inputBorder || palette.border,
      backgroundColor: hovered ? palette.surfaceMuted : 'transparent' }]}>
    <Icon spec={icon} color={colors.color} size={14} />
  </Pressable>;
  return <View>
    <TextInput accessibilityLabel={field.label} keyboardType="numeric" value={String(value)}
      placeholder={field.placeholder} placeholderTextColor={palette.textMuted}
      onChangeText={onChange} selectionColor={palette.accent}
      style={[styles.numberInput, { backgroundColor: palette.surfaceStrong,
        borderColor: error ? palette.danger : (palette.inputBorder || palette.border), color: palette.text }]} />
    <View style={styles.stepper}>
      {button(1, CHEVRON_UP, labels.increase || 'Increase')}
      {button(-1, CHEVRON_DOWN, labels.decrease || 'Decrease')}
    </View>
  </View>;
}

function fieldType(field) {
  if (field.options?.length) return 'select';
  if (field.type === 'date' || field.name === 'start_date' || field.name === 'end_date') return 'date';
  if (field.type === 'textarea') return 'textarea';
  if (field.type === 'number') return 'number';
  if (field.type === 'rating') return 'rating';
  return 'text';
}

function HeaderBar({ title, subtitle, closeLabel, onClose, closeRef, palette }) {
  return <View style={[styles.bar, { backgroundColor: palette.surface, borderColor: palette.border }]}>
    <View style={styles.barText}>
      <Text accessibilityRole="header" numberOfLines={2} style={[styles.barTitle, { color: palette.text }]}>{title}</Text>
      {subtitle ? <Text style={[styles.barSubtitle, { color: palette.textMuted }]}>{subtitle}</Text> : null}
    </View>
    {onClose ? <ModalCloseButton ref={closeRef} accessibilityRole="button" accessibilityLabel={closeLabel}
      onPress={onClose} style={({ focused }) => [styles.barClose, focused && palette.focusStyle]}>
      <Icon spec={X_ICON} color={palette.textMuted} size={20} />
    </ModalCloseButton> : null}
  </View>;
}

export function TrackForm({
  model,
  values = {},
  errors = {},
  status = 'ready',
  serverError = '',
  busy = false,
  labels: suppliedLabels,
  palette: suppliedPalette,
  onChange,
  onSubmit,
  onDelete,
  onRetry,
  onClose,
  showHeader = true,
  closeRef,
  nativeID,
  tabs = [],
  generalPrefix,
  activeTab,
  onTabChange,
  onRetryTabs,
  onTagToggle,
  onTagCreate,
  onArtworkSave,
  onDiscoverToggle,
  secondaryActions = [],
  embedded = false,
}) {
  const labels = { ...DEFAULT_LABELS, ...(suppliedLabels || {}) };
  const palette = { ...DEFAULT_PALETTE, ...(suppliedPalette || {}) };
  const ratingPalette = { ...DEFAULT_PALETTE.rating, ...(palette.rating || {}) };
  const { height, width } = useWindowDimensions();
  const wide = width >= 640;
  const [localErrors, setLocalErrors] = useState({});
  const [localTab, setLocalTab] = useState('general');
  const [since, setSince] = useState('');
  const fieldErrors = { ...errors, ...localErrors };
  const selectedTab = activeTab === undefined ? localTab : activeTab;

  function selectTab(id) {
    if (activeTab === undefined) setLocalTab(id);
    onTabChange?.(id);
  }

  function runtimeOf(field) {
    const minutes = Number(field.runtimeMinutes);
    return Number.isFinite(minutes) && minutes > 0 ? minutes : 0;
  }

  // Picking a release date as the end back-fills an empty start a runtime earlier.
  function changeDate(field, next, detail) {
    change(field.name, next);
    const runtime = runtimeOf(field);
    if (field.name === 'end_date' && next && runtime && !values.start_date
      && detail?.source === 'suggestion') {
      const end = new Date(String(next).includes('T') ? next : `${next}T00:00`);
      if (!Number.isNaN(end.getTime())) {
        change('start_date', valueFromDate(new Date(end.getTime() - runtime * 60000),
          Boolean(model.trackTime)));
      }
    }
  }

  function quickDate(field, action) {
    const trackTime = Boolean(model.trackTime);
    const runtime = runtimeOf(field);
    const moment = new Date();
    if (action === 'release-date') {
      change(field.name, field.suggestionDate || '');
    } else if (action === 'just-finished') {
      change('end_date', valueFromDate(moment, trackTime));
      if (runtime && !values.start_date) {
        change('start_date', valueFromDate(new Date(moment.getTime() - runtime * 60000), trackTime));
      }
    } else {
      change('start_date', valueFromDate(moment, trackTime));
      if (runtime && !values.end_date) {
        change('end_date', valueFromDate(new Date(moment.getTime() + runtime * 60000), trackTime));
      }
      if (['', 'Completed', 'Planning'].includes(values.status ?? '')) {
        change('status', 'In progress');
      }
    }
  }

  function change(name, value) {
    if (localErrors[name]) setLocalErrors(current => ({ ...current, [name]: '' }));
    onChange?.(name, value);
  }

  function validate() {
    const next = validateTrackValues(model, values, labels);
    setLocalErrors(next);
    return Object.keys(next).length === 0;
  }

  function submit() {
    if (!validate()) return;
    onSubmit?.(values);
  }

  function runSecondary(action) {
    if (!validate()) return;
    action.onPress?.(action.needsSince ? { ...values, rewatch_started_at: since } : values);
  }

  const sidebar = !embedded && tabs.length > 0;
  const inputPalette = { ...palette, border: palette.inputBorder || palette.border };
  const cells = (model?.fields || []).filter(field => field.name !== 'notes').map(field => {
    const type = fieldType(field);
    const value = values[field.name] == null ? '' : values[field.name];
    let control;
    if (type === 'select') {
      control = <FormField label={field.label} required={field.required} help={field.help}
        error={fieldErrors[field.name]} palette={palette}>
        <SelectField labelHidden filter label={field.label} accessibilityLabel={`${field.label}: ${
          (field.options.find(option => String(option.value) === String(value)) || {}).label ?? ''}`}
        value={value} options={field.options} palette={inputPalette}
        onChange={event => change(field.name, event.target.value)} />
      </FormField>;
    } else if (type === 'date') {
      control = <FormField label={field.label} required={field.required}
        help={field.help} error={fieldErrors[field.name]} palette={palette}>
        <DateTimePicker value={value}
          onChange={(next, detail) => changeDate(field, next, detail)}
          trackTime={Boolean(model.trackTime)} labels={labels.dateTime || {}}
          palette={palette.dateTime || {}}
          quickActions={field.quickActions || []}
          suggestionDate={field.suggestionDate || ''}
          suggestionLabel={field.suggestionLabel || ''}
          suggestionExtras={field.suggestionExtras || []}
          runtimeMinutes={field.runtimeMinutes || ''}
          copyValue={field.copyFrom ? (values[field.copyFrom] || '') : ''}
          copyLabel={field.copyFromLabel || ''}
          copyEmptyMessage={field.copyEmptyMessage || ''}
          onQuickAction={action => quickDate(field, action)} />
      </FormField>;
    } else if (type === 'rating') {
      const scaleMax = Math.max(1, Number(field.max) || 10);
      const numeric = value === '' ? null : Number(value);
      control = <FormField label={field.label} required={field.required}
        help={field.help} error={fieldErrors[field.name]} palette={palette}>
        <RatingStars scaleMax={scaleMax} value={Number.isFinite(numeric) ? numeric : null}
          label={field.label} palette={ratingPalette}
          onSelect={next => change(field.name, next == null ? '' : String(next))}
          renderStar={lit => <Text style={[styles.ratingStar, {
            color: lit ? ratingPalette.lit : ratingPalette.unlit,
          }]}>★</Text>} />
      </FormField>;
    } else if (type === 'number') {
      control = <FormField label={field.label} required={field.required}
        help={field.help} error={fieldErrors[field.name]} palette={palette}>
        <NumberStepper field={field} value={value} onChange={next => change(field.name, next)}
          error={fieldErrors[field.name]} palette={palette} labels={labels} />
      </FormField>;
    } else {
      control = <FormTextField label={field.label} value={value}
        onChangeText={next => change(field.name, next)} required={field.required}
        help={field.help} error={fieldErrors[field.name]} placeholder={field.placeholder}
        keyboardType={type === 'number' ? 'numeric' : 'default'}
        multiline={type === 'textarea'} palette={inputPalette} />;
    }
    return <View key={field.name} style={sidebar && wide ? styles.cell : styles.fullCell}>{control}</View>;
  });
  const notes = (model?.fields || []).filter(field => field.name === 'notes').map(field => <MarkdownNotes
    key={field.name} label={field.label} value={values[field.name] == null ? '' : values[field.name]}
    onChangeText={next => change(field.name, next)} hint={field.help}
    placeholder={labels.notesPlaceholder || 'Add any notes or comments...'}
    error={fieldErrors[field.name]} palette={inputPalette} />);
  const deleteButton = model?.deleteAction && onDelete
    ? <Button label={labels.delete} tone="danger-solid" disabled={busy}
      onPress={() => onDelete(values)} palette={palette} /> : null;
  const renderSecondary = action => <View key={action.key} style={styles.secondary}>
    <Button label={action.label} tone={action.tone || 'surface'} disabled={busy || action.disabled}
      palette={palette} onPress={() => runSecondary(action)} />
    {action.needsSince ? <View style={styles.since}>
      <Text style={[styles.sinceLabel, { color: palette.textSecondary }]}>{labels.since || 'Since'}</Text>
      <View style={styles.sinceField}>
        <DateTimePicker value={since} onChange={setSince} trackTime={false}
          labels={{ ...(labels.dateTime || {}), field: labels.since || 'Since' }}
          palette={palette.dateTime || {}} />
      </View>
    </View> : null}
  </View>;
  const secondary = secondaryActions.filter(action => action.key !== 'save-as-new').map(renderSecondary);
  const saveAsNew = secondaryActions.filter(action => action.key === 'save-as-new').map(renderSecondary);
  const submitButton = <Button label={busy ? labels.saving : (model?.submitLabel || labels.save)} busy={busy}
    disabled={busy} onPress={submit} palette={palette} />;

  const generalBody = <View style={sidebar ? styles.generalSide : styles.stack}>
    {generalPrefix}
    {sidebar ? <View style={styles.grid}>{cells}</View> : cells}
    {notes}
    {sidebar ? <View style={styles.footer}>
      <View style={styles.footerStart}>{deleteButton}{secondary}</View>
      <View style={styles.footerStart}>{saveAsNew}{submitButton}</View>
    </View> : <View style={styles.actions}>
      {submitButton}{deleteButton}{secondary}
    </View>}
  </View>;

  if (sidebar) {
    return <View nativeID={nativeID} style={[styles.shell, {
      backgroundColor: palette.surfaceDim, maxHeight: height * 0.92 }]}>
      {showHeader ? <HeaderBar title={model?.title || labels.title} subtitle={model?.subtitle}
        closeLabel={labels.close} onClose={onClose} closeRef={closeRef} palette={palette} /> : null}
      {status === 'loading' ? <FormStatus message={labels.loading} tone="loading" palette={palette} /> : null}
      {status === 'error' ? <View style={styles.stack}>
        <FormError message={labels.loadFailed} palette={palette} />
        <Button label={labels.retry} tone="surface" onPress={onRetry} palette={palette} />
      </View> : null}
      {status === 'ready' && model ? <>
        {serverError ? <View style={styles.serverError}><FormError message={serverError} palette={palette} /></View> : null}
        <TrackTabs sidebar tabs={tabs} activeId={selectedTab} onChange={selectTab}
          labels={labels} palette={palette} generalContent={generalBody}
          onRetry={onRetryTabs} onTagToggle={onTagToggle} onTagCreate={onTagCreate}
          onArtworkSave={onArtworkSave} onDiscoverToggle={onDiscoverToggle} />
      </> : null}
    </View>;
  }

  return <View nativeID={nativeID} style={[styles.card,
    embedded && styles.embedded,
    { backgroundColor: embedded ? 'transparent' : palette.surface, maxHeight: embedded ? undefined : height * 0.88 }]}>
    {showHeader ? <FormHeader title={model?.title || labels.title} closeLabel={labels.close}
      onClose={onClose} closeRef={closeRef} palette={palette} /> : null}
    {status === 'loading' ? <FormStatus message={labels.loading} tone="loading" palette={palette} /> : null}
    {status === 'error' ? <View style={styles.stack}>
      <FormError message={labels.loadFailed} palette={palette} />
      <Button label={labels.retry} tone="surface" onPress={onRetry} palette={palette} />
    </View> : null}
    {status === 'ready' && model ? <ScrollView keyboardShouldPersistTaps="handled" style={styles.scroll}
      contentContainerStyle={styles.stack}>
      {model.subtitle ? <View style={styles.subtitleWrap}>
        <FormStatus message={model.subtitle} palette={palette} />
      </View> : null}
      {serverError ? <FormError message={serverError} palette={palette} /> : null}
      {tabs.length ? <TrackTabs tabs={tabs} activeId={selectedTab} onChange={selectTab}
        labels={labels} palette={palette} generalContent={generalBody}
        onRetry={onRetryTabs} onTagToggle={onTagToggle} onTagCreate={onTagCreate}
        onArtworkSave={onArtworkSave} onDiscoverToggle={onDiscoverToggle} /> : generalBody}
    </ScrollView> : null}
  </View>;
}

const styles = StyleSheet.create({
  shell: { width: '100%', borderRadius: 12, overflow: 'hidden', flexShrink: 1 },
  bar: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12,
    paddingHorizontal: 28, paddingVertical: 16, borderBottomWidth: 1 },
  barText: { flexShrink: 1, minWidth: 0 },
  barTitle: { fontSize: 24, fontWeight: '700' },
  barSubtitle: { fontSize: 14, marginTop: 2 },
  barClose: { padding: 4, borderRadius: 6 },
  serverError: { paddingHorizontal: 24, paddingTop: 12 },
  generalSide: { gap: 20 },
  grid: { flexDirection: 'row', flexWrap: 'wrap', columnGap: 16, rowGap: 20 },
  cell: { flexBasis: '48.5%', maxWidth: '48.5%', flexGrow: 1, minWidth: 0 },
  fullCell: { width: '100%' },
  footer: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 16, marginTop: 4 },
  numberInput: { minHeight: 46, borderWidth: 1, borderRadius: 6, paddingLeft: 10, paddingRight: 44, fontSize: 14 },
  stepper: { position: 'absolute', top: 0, right: 0, bottom: 0, width: 34 },
  stepButton: { flex: 1, alignItems: 'center', justifyContent: 'center', borderWidth: 1 },
  secondary: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  since: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  sinceLabel: { fontSize: 12 },
  sinceField: { width: 150 },
  footerStart: { flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', gap: 8 },
  card: {
    width: '100%', borderRadius: 8, padding: 24, gap: 16,
    shadowColor: '#000000', shadowOpacity: 0.35, shadowRadius: 24, shadowOffset: { width: 0, height: 12 }, elevation: 16,
  },
  embedded: {
    borderRadius: 0, padding: 0, shadowOpacity: 0, shadowRadius: 0, shadowOffset: { width: 0, height: 0 }, elevation: 0,
  },
  scroll: { flexGrow: 0 },
  stack: { gap: 14 },
  subtitleWrap: { marginTop: -4 },
  ratingStar: { fontSize: 18 },
  actions: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 10, marginTop: 4 },
});
