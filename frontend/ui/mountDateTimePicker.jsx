import React, { useCallback, useEffect, useRef, useState } from 'react';
import { mountRoot } from '../settings/root';
import { DateTimePicker } from './DateTimePicker';
import {
  formatValue,
  normalizeValue,
  nowParts,
  parseDateValue,
  valueFromDate,
} from './dateTime';
import { rootStyle } from './rootStyle.web';

const canvas = document.createElement('canvas');
canvas.width = canvas.height = 1;
const ctx = canvas.getContext('2d');

function resolveColor(value, fallback) {
  if (!value) return fallback;
  if (/^(?:#|rgba?\()/i.test(value.trim())) return value.trim();
  if (!ctx) return fallback;
  ctx.clearRect(0, 0, 1, 1);
  ctx.fillStyle = fallback;
  ctx.fillStyle = value;
  ctx.fillRect(0, 0, 1, 1);
  const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data;
  return `rgba(${r}, ${g}, ${b}, ${a / 255})`;
}

function readPalette() {
  const css = rootStyle();
  const token = (name, fallback) => resolveColor(css.getPropertyValue(name).trim(), fallback);
  return {
    text: token('--color-text', '#fafafa'),
    textSecondary: token('--color-text-secondary', '#d4d4d8'),
    textMuted: token('--color-text-muted', '#a1a1aa'),
    surface: token('--color-surface', '#27272a'),
    surfaceStrong: token('--color-surface-strong', '#3f3f46'),
    surfaceMuted: token('--color-surface-muted', '#52525b'),
    border: token('--color-border', '#3f3f46'),
    accent: token('--color-accent', '#6366f1'),
    accentContrast: token('--color-accent-contrast', '#ffffff'),
    link: token('--color-link', '#818cf8'),
    danger: token('--color-danger', '#ef4444'),
    radius: 10,
    radiusSmall: 6,
    fontSize: 14,
    smallFontSize: 12,
  };
}

function findHidden(host, props) {
  const byId = props.fieldId ? document.getElementById(props.fieldId) : null;
  if (byId) return byId;
  return host.parentElement?.querySelector('input[type="hidden"]') || null;
}

function Adapter({ host, props }) {
  const input = useRef(null);
  const defaultApplied = useRef(false);
  const [value, setValue] = useState(() => {
    const hidden = findHidden(host, props);
    input.current = hidden;
    return normalizeValue(hidden?.value || props.initialValue || '', props.trackTime);
  });
  const [open, setOpen] = useState(false);
  const [copyValue, setCopyValue] = useState('');
  const [palette, setPalette] = useState(readPalette);

  const trackTime = Boolean(props.trackTime);
  const use12Hour = Boolean(props.use12Hour);
  const fieldName = props.fieldName || '';

  function form() {
    return input.current?.closest('form') || null;
  }

  function dispatch() {
    if (!input.current) return;
    window.trackModalClearAutoFilledField?.(form(), fieldName);
    if (fieldName === 'start_date' && form() && window.Alpine) {
      try {
        window.Alpine.$data(form()).manualStartDate = true;
      } catch {
        // Ignore Alpine lookup failures.
      }
    }
    window.trackModalDispatchInputEvents?.(input.current);
  }

  function commit(next, source) {
    if (value === next && input.current?.value === next) {
      if (source === 'suggestion') backfillStartDateIfNeeded(next);
      return;
    }
    setValue(next);
    if (input.current) input.current.value = next;
    dispatch();
    if (source === 'suggestion') backfillStartDateIfNeeded(next);
  }

  function copySourceValue() {
    if (!props.copyFrom) return '';
    return form()?.querySelector(`[name="${props.copyFrom}"]`)?.value || '';
  }

  function setStatusForPreset(status) {
    const formElement = form();
    const statusField = formElement?.querySelector('[name="status"]');
    if (!statusField || statusField.value === status) return;
    let mediaForm = null;
    if (window.Alpine) {
      try {
        mediaForm = window.Alpine.$data(formElement);
      } catch {
        // Ignore Alpine lookup failures and still update the status field.
      }
    }
    if (mediaForm && 'suppressStatusDateAutofill' in mediaForm) {
      mediaForm.suppressStatusDateAutofill = true;
    }
    try {
      statusField.value = status;
      statusField.dispatchEvent(new Event('change', { bubbles: true }));
    } finally {
      if (mediaForm && 'suppressStatusDateAutofill' in mediaForm) {
        mediaForm.suppressStatusDateAutofill = false;
      }
    }
  }

  function commitPairedValue(newValue) {
    const pairedFieldName = fieldName === 'start_date'
      ? 'end_date'
      : fieldName === 'end_date'
        ? 'start_date'
        : '';
    if (!pairedFieldName) return;
    const pairedInput = form()?.querySelector(`[name="${pairedFieldName}"]`);
    if (!pairedInput || pairedInput === input.current || pairedInput.value) return;
    try {
      const picker = window.Alpine?.$data(pairedInput.closest('[x-data]'));
      if (picker?.commit && !pairedInput.closest('[data-date-time-picker]')) {
        picker.commit(newValue);
        return;
      }
    } catch {
      // Fall through to updating the paired hidden input directly.
    }
    pairedInput.value = newValue;
    window.trackModalClearAutoFilledField?.(form(), pairedFieldName);
    window.trackModalDispatchInputEvents?.(pairedInput);
  }

  function applyNow() {
    commit(valueFromDate(new Date(), trackTime), 'now');
  }

  function clearValue() {
    const formElement = form();
    if (fieldName === 'end_date') window.trackModalMarkEndCleared?.(formElement);
    setOpen(false);
    setValue('');
    if (input.current) input.current.value = '';
    window.trackModalClearAutoFilledField?.(formElement, fieldName);
    if (fieldName === 'start_date' && formElement) {
      if (window.Alpine) {
        try {
          window.Alpine.$data(formElement).manualStartDate = false;
        } catch {
          // Ignore Alpine lookup failures.
        }
      }
      const sentinel = formElement.querySelector('[name="start_date_cleared"]');
      if (sentinel) sentinel.value = '1';
    }
    window.trackModalDispatchInputEvents?.(input.current);
  }

  function applySuggestion(date) {
    const iso = date || props.suggestionDate;
    const parts = parseDateValue(iso);
    if (!parts) return;
    const base = parseDateValue(value) || nowParts(new Date());
    commit(formatValue({
      ...parts,
      hour: base.hour,
      minute: base.minute,
      second: base.second,
    }, trackTime), 'suggestion');
  }

  function applyCopy() {
    const parts = parseDateValue(copySourceValue());
    if (!parts) return;
    commit(formatValue(parts, trackTime), 'copy');
  }

  function backfillStartDateIfNeeded(endValue) {
    const runtimeMinutes = Number.parseInt(
      String(props.suggestionRuntimeMinutes || props.runtimeMinutes || ''),
      10,
    );
    if (fieldName !== 'end_date' || !Number.isFinite(runtimeMinutes) || runtimeMinutes <= 0 || !trackTime) {
      return;
    }
    const startInput = form()?.querySelector('[name="start_date"]');
    if (!startInput || startInput === input.current) return;
    if (window.Alpine) {
      try {
        if (window.Alpine.$data(form()).manualStartDate) return;
      } catch {
        // Ignore Alpine lookup failures.
      }
    }
    const parts = parseDateValue(endValue);
    if (!parts) return;
    const endDateTime = new Date(
      parts.year,
      parts.month,
      parts.day,
      parts.hour,
      parts.minute,
      parts.second,
    );
    const startDateTime = new Date(endDateTime.getTime() - runtimeMinutes * 60000);
    startInput.value = valueFromDate(startDateTime, trackTime);
    window.trackModalClearAutoFilledField?.(form(), 'start_date');
    window.trackModalDispatchInputEvents?.(startInput);
  }

  function applyQuickAction(action) {
    if (action === 'start-now') {
      setStatusForPreset('In progress');
      const now = new Date();
      const nowValue = valueFromDate(now, trackTime);
      const runtimeMinutes = Number.parseInt(String(props.runtimeMinutes || ''), 10);
      const endValue = valueFromDate(
        new Date(now.getTime() + (Number.isFinite(runtimeMinutes) ? runtimeMinutes : 0) * 60000),
        trackTime,
      );
      if (fieldName === 'start_date') {
        commit(nowValue, 'quick');
        commitPairedValue(endValue);
      } else if (fieldName === 'end_date') {
        commit(endValue, 'quick');
        commitPairedValue(nowValue);
      } else {
        commit(nowValue, 'quick');
      }
      return;
    }
    if (action === 'just-finished') {
      setStatusForPreset('Completed');
      const now = new Date();
      const nowValue = valueFromDate(now, trackTime);
      const runtimeMinutes = Number.parseInt(String(props.runtimeMinutes || ''), 10);
      const startValue = valueFromDate(
        new Date(now.getTime() - (Number.isFinite(runtimeMinutes) ? runtimeMinutes : 0) * 60000),
        trackTime,
      );
      if (fieldName === 'start_date') {
        commit(startValue, 'quick');
        commitPairedValue(nowValue);
      } else if (fieldName === 'end_date') {
        commit(nowValue, 'quick');
        commitPairedValue(startValue);
      } else {
        commit(nowValue, 'quick');
      }
      return;
    }
    if (action === 'release-date') {
      if (!props.suggestionDate) return;
      setStatusForPreset('Completed');
      applySuggestion();
    }
  }

  function onChange(next, meta) {
    if (meta?.source === 'clear') {
      clearValue();
      return;
    }
    commit(next, meta?.source);
  }

  function onOpenChange(next) {
    setOpen(next);
    if (next) setCopyValue(copySourceValue());
  }

  useEffect(() => {
    const update = () => setPalette(readPalette());
    update();
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'style'] });
    const scheme = window.matchMedia('(prefers-color-scheme: dark)');
    scheme.addEventListener('change', update);
    return () => {
      observer.disconnect();
      scheme.removeEventListener('change', update);
    };
  }, []);

  useEffect(() => {
    const hidden = input.current || findHidden(host, props);
    input.current = hidden;
    if (!hidden) return undefined;
    const sync = () => setValue(normalizeValue(hidden.value, trackTime));
    hidden.addEventListener('input', sync);
    const normalized = normalizeValue(hidden.value, trackTime);
    if (hidden.value && normalized !== hidden.value) {
      hidden.value = normalized;
      setValue(normalized);
    }
    if (!defaultApplied.current && props.defaultNow && !hidden.value) {
      defaultApplied.current = true;
      applyNow();
    }
    return () => hidden.removeEventListener('input', sync);
    // The hidden input is stable for the life of the island.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const panelTestID = `date-time-panel-${props.fieldId || props.fieldName || 'field'}`;

  useEffect(() => {
    if (!open) return undefined;
    let timer;
    let panel = null;
    const focusPanel = () => {
      panel = document.querySelector(`[data-testid="${panelTestID}"]`);
      const first = panel?.querySelector('[role="button"], input');
      first?.focus?.();
    };
    timer = window.setTimeout(focusPanel, 0);
    const onKeyDown = event => {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        setOpen(false);
        return;
      }
      if (event.key !== 'Tab' || !panel) return;
      const focusable = Array.from(
        panel.querySelectorAll('[role="button"], input, [tabindex]:not([tabindex="-1"])'),
      ).filter(element => element.offsetParent !== null && !element.disabled);
      if (!focusable.length) return;
      const firstElement = focusable[0];
      const lastElement = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === firstElement) {
        event.preventDefault();
        lastElement.focus();
      } else if (!event.shiftKey && document.activeElement === lastElement) {
        event.preventDefault();
        firstElement.focus();
      }
    };
    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener('keydown', onKeyDown, true);
    };
  }, [open, host, panelTestID]);

  const previousOpen = useRef(false);
  useEffect(() => {
    if (previousOpen.current && !open) {
      const trigger = host.parentElement?.querySelector('[role="button"]');
      trigger?.focus?.();
    }
    previousOpen.current = open;
  }, [open, host]);

  return <DateTimePicker
    value={value}
    onChange={onChange}
    trackTime={trackTime}
    use12Hour={use12Hour}
    weekStart={props.weekStart || 'monday'}
    locale={props.locale}
    labels={props.labels}
    quickActions={props.quickActions}
    suggestionDate={props.suggestionDate}
    suggestionLabel={props.suggestionLabel}
    suggestionExtras={props.suggestionExtras}
    runtimeMinutes={props.runtimeMinutes}
    copyValue={copyValue}
    copyLabel={props.copyLabel}
    copyEmptyMessage={props.copyEmptyMessage}
    onQuickAction={applyQuickAction}
    open={open}
    onOpenChange={onOpenChange}
    panelTestID={panelTestID}
    palette={palette} />;
}

export function mountDateTimePicker(host, props) {
  return mountRoot(host, props.activityCalendar
    ? <ActivityCalendarAdapter host={host} props={props} />
    : <Adapter host={host} props={props} />);
}

function ActivityCalendarAdapter({ host, props }) {
  const dialog = host.closest('[aria-labelledby="session-history-title"]');
  const [value, setValue] = useState(() => {
    return (dialog && window.Alpine?.$data(dialog)?.selectedDate) || '';
  });
  const [palette, setPalette] = useState(readPalette);
  const onViewMonthChange = useCallback(detail => {
    const state = dialog && window.Alpine?.$data(dialog);
    if (state) { state.viewYear = detail.year; state.viewMonth = detail.month; }
  }, [dialog]);
  useEffect(() => {
    const sync = event => setValue(event.detail);
    const theme = () => setPalette(readPalette());
    host.addEventListener('activity-calendar-sync', sync);
    const observer = new MutationObserver(theme);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'style', 'data-theme'] });
    return () => { host.removeEventListener('activity-calendar-sync', sync); observer.disconnect(); };
  }, [host]);
  return <DateTimePicker inline value={value} initialViewDate={`${props.initialMonth}-01`}
    markerDays={Object.keys(props.activityIntensity || {})} activityIntensity={props.activityIntensity}
    selectableDays={props.selectableDays} weekStart={props.weekStart} labels={props.labels} palette={{ ...palette, accentContrast: '#ffffff' }}
    onViewMonthChange={onViewMonthChange}
    onChange={next => {
      setValue(next);
      const state = dialog && window.Alpine?.$data(dialog);
      if (state) { state.selectedDate = next; state.scrollToDay(next); }
    }} />;
}
