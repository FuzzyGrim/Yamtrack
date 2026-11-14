import { Modal } from './Modal';
import React, { useEffect, useRef, useState } from 'react';
import { Pressable, StyleSheet, View, useWindowDimensions } from 'react-native';
import { TrackForm } from './TrackForm';

const DEFAULT_PALETTE = {
  background: '#18181b',
  surface: '#27272a',
  border: '#3f3f46',
  text: '#fafafa',
  textMuted: '#a1a1aa',
  backdrop: 'rgba(0, 0, 0, 0.65)',
};

function pad2(value) {
  return String(value).padStart(2, '0');
}

function formatDateValue(date, trackTime) {
  const datePart = `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
  if (!trackTime) return datePart;
  return `${datePart}T${pad2(date.getHours())}:${pad2(date.getMinutes())}`;
}

function parseDateValue(value) {
  const text = String(value || '');
  if (!text) return null;
  const datePart = text.includes('T') ? text.split('T')[0] : text;
  const [year, month, day] = datePart.split('-').map(Number);
  if ([year, month, day].some(Number.isNaN)) return null;
  const timePart = text.includes('T') ? text.split('T')[1] : '00:00';
  const [hours, minutes] = timePart.split(':').map(Number);
  if ([hours, minutes].some(Number.isNaN)) return null;
  return new Date(year, month - 1, day, hours, minutes, 0, 0);
}

export function parseTrackDurationMinutes(value) {
  const text = String(value || '').trim().toLowerCase();
  if (!text) return null;
  if (/^\d+(?:\.\d+)?$/.test(text)) {
    const amount = Number.parseFloat(text);
    return Number.isFinite(amount) && amount >= 0 ? Math.trunc(amount * 60) : null;
  }
  if (text.includes(':')) {
    const chunks = text.split(':');
    if (chunks.length !== 2 || !/^\d+$/.test(chunks[0]) || !/^\d+$/.test(chunks[1])) return null;
    const minutes = Number.parseInt(chunks[1], 10);
    if (minutes > 59) return null;
    return Number.parseInt(chunks[0], 10) * 60 + minutes;
  }
  const unitPattern = /(\d+(?:\.\d+)?)\s*(hours?|hrs?|hr|h|minutes?|mins?|min)/g;
  const matches = [];
  let match = unitPattern.exec(text);
  while (match) {
    matches.push(match);
    match = unitPattern.exec(text);
  }
  if (!matches.length || text.replace(unitPattern, '').trim()) return null;
  let total = 0;
  for (const entry of matches) {
    const amount = Number.parseFloat(entry[1]);
    if (!Number.isFinite(amount)) return null;
    total += entry[2].startsWith('h') ? amount * 60 : amount;
  }
  return Math.trunc(total);
}

function progressField(model) {
  return (model?.fields || []).find(field => field.name === 'progress') || null;
}

function createCouplingState(model, values) {
  const field = progressField(model);
  return {
    isNew: model?.isNew === undefined ? !model?.instanceId : Boolean(model.isNew),
    originalStatus: values?.status ?? null,
    autoStart: false,
    autoEnd: false,
    manualStart: Boolean(values?.start_date),
    endCleared: false,
    durationProgress: Boolean(field && !field.options?.length && field.type !== 'number' && field.type !== 'rating'),
    formKey: model?.formId || model?.instanceId || model?.title || '',
  };
}

function shouldDefaultEnd(next, state) {
  if (state.endCleared) return false;
  if (next.status !== 'Completed' && next.status !== 'In progress') return false;
  return state.isNew || next.status !== state.originalStatus;
}

function syncStartFromProgress(next, state, model, now) {
  if (!state.durationProgress || state.manualStart) return;
  if (next.start_date && !state.autoStart) return;
  const minutes = parseTrackDurationMinutes(next.progress);
  if (!minutes) {
    if (state.autoStart) {
      next.start_date = '';
      state.autoStart = false;
    }
    return;
  }
  if (!next.end_date) {
    if (!shouldDefaultEnd(next, state)) return;
    next.end_date = formatDateValue(now(), Boolean(model?.trackTime));
    state.autoEnd = true;
  }
  const end = parseDateValue(next.end_date);
  if (!end) return;
  next.start_date = formatDateValue(new Date(end.getTime() - minutes * 60000), Boolean(model?.trackTime));
  state.autoStart = true;
}

function repeatActionsFor(model, onSaveAsNew, onRewatch, onSecondaryAction) {
  const repeat = model?.repeatIdentity || {};
  const actions = [];
  if (repeat.canSaveAsNew) {
    actions.push({
      key: 'save-as-new',
      tone: 'primary',
      label: repeat.saveAsNewLabel || 'Save as new entry',
      onPress: values => onSaveAsNew?.(values),
    });
  }
  if (repeat.rewatchLabel) {
    actions.push({
      key: 'rewatch',
      label: repeat.rewatchLabel,
      tone: 'surface',
      needsSince: repeat.rewatchAction === 'start',
      onPress: values => onRewatch?.(repeat.rewatchAction, values),
    });
  }
  for (const action of model?.secondaryActions || []) {
    actions.push({
      key: action.key,
      label: action.label,
      tone: action.tone || 'surface',
      onPress: values => onSecondaryAction?.(action, values),
    });
  }
  return actions;
}

export function TrackDialog({
  open = false,
  model,
  values = {},
  errors = {},
  status = 'ready',
  serverError = '',
  busy = false,
  labels = {},
  palette: suppliedPalette,
  tabs = [],
  generalPrefix,
  onChange,
  onSubmit,
  onDelete,
  onRetry,
  onClose,
  onRetryTabs,
  onTagToggle,
  onTagCreate,
  onArtworkSave,
  onDiscoverToggle,
  onSaveAsNew,
  onRewatch,
  onSecondaryAction,
  embedded = false,
  maxWidth = 720,
  showHeader,
  now = () => new Date(),
}) {
  const palette = { ...DEFAULT_PALETTE, ...(suppliedPalette || {}) };
  const { height } = useWindowDimensions();
  const [activeTab, setActiveTab] = useState('general');
  const coupling = useRef(createCouplingState(model, values));

  useEffect(() => {
    if (!open) return;
    setActiveTab('general');
    const state = createCouplingState(model, values);
    coupling.current = state;
    if (state.isNew && values.status === 'In progress') {
      const next = { ...values };
      syncStartFromProgress(next, state, model, now);
      if (next.start_date !== values.start_date) onChange?.('start_date', next.start_date);
      if (next.end_date !== values.end_date) onChange?.('end_date', next.end_date);
    }
    // Reset coupling state when a different form is opened.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, model?.formId, model?.instanceId, model?.title]);

  function submit(nextValues) {
    const next = { ...nextValues };
    if (!next.end_date && shouldDefaultEnd({ ...next, status: next.status }, coupling.current)) {
      next.end_date = formatDateValue(now(), Boolean(model?.trackTime));
      onChange?.('end_date', next.end_date);
    }
    onSubmit?.(next);
  }

  function change(name, value) {
    const next = { ...values, [name]: value };
    const state = coupling.current;

    if (name === 'start_date') {
      state.autoStart = false;
      state.manualStart = Boolean(value);
    }
    if (name === 'end_date') {
      state.autoEnd = false;
      state.endCleared = !value;
    }
    if (name === 'progress' || name === 'end_date') {
      syncStartFromProgress(next, state, model, now);
    }
    if (name === 'status') {
      if (state.autoStart) {
        next.start_date = '';
        state.autoStart = false;
      }
      if (state.autoEnd) {
        next.end_date = '';
        state.autoEnd = false;
      }
      state.endCleared = false;
      if (value === 'Completed' || value === 'In progress') {
        syncStartFromProgress(next, state, model, now);
      }
    }

    const changed = new Set([name]);
    for (const key of ['start_date', 'end_date']) {
      if (next[key] !== values[key]) changed.add(key);
    }
    changed.forEach(key => onChange?.(key, next[key] ?? ''));
  }

  const repeatActions = repeatActionsFor(
    model, onSaveAsNew, onRewatch, onSecondaryAction,
  );

  const form = <TrackForm
    model={model}
    values={values}
    errors={errors}
    status={status}
    serverError={serverError}
    busy={busy}
    labels={labels}
    palette={palette}
    tabs={tabs}
    generalPrefix={generalPrefix}
    activeTab={activeTab}
    onTabChange={setActiveTab}
    onChange={change}
    onSubmit={submit}
    onDelete={onDelete}
    onRetry={onRetry}
    onClose={onClose}
    onRetryTabs={onRetryTabs}
    onTagToggle={onTagToggle}
    onTagCreate={onTagCreate}
    onArtworkSave={onArtworkSave}
    onDiscoverToggle={onDiscoverToggle}
    secondaryActions={repeatActions}
    showHeader={showHeader === undefined ? !embedded : showHeader}
    embedded={embedded} />;

  if (embedded) return form;

  return <Modal visible={Boolean(open)} transparent animationType="fade" onRequestClose={onClose}>
    <View style={styles.root}>
      <Pressable accessible={false} accessibilityLabel={labels.close || 'Close'} onPress={onClose}
        style={[StyleSheet.absoluteFill, { backgroundColor: palette.backdrop }]} />
      <View accessibilityViewIsModal role="dialog" accessibilityLabel={model?.title || labels.title || 'Tracking'}
        style={[styles.dialog, { maxWidth, maxHeight: height * 0.92 }]}>
        {form}
      </View>
    </View>
  </Modal>;
}

const styles = StyleSheet.create({
  root: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 12 },
  dialog: { width: '100%', maxWidth: 720 },
});
