import { Modal, ModalCloseButton } from './Modal';
import { Icon } from './Icon';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
  useWindowDimensions,
} from 'react-native';
import {
  dateKey,
  displayHour,
  formatDisplayText,
  formatValue,
  hour24FromDisplay,
  isMarkedDay,
  isSameDay,
  isSelectableDay,
  isToday,
  meridiem,
  monthLabel,
  monthShortLabel,
  monthWeeks,
  nowParts,
  parseDateValue,
  stepHour,
  stepMinute,
  stepSecond,
  valueFromDate,
  weekdayLabels,
  yearPage,
} from './dateTime';

const DEFAULT_LABELS = {
  field: '',
  notSet: 'Not set',
  openPicker: 'Open date picker',
  picker: 'Date picker',
  quickActions: 'Quick actions',
  clear: 'Clear date',
  close: 'Close date picker',
  previousMonth: 'Previous month',
  nextMonth: 'Next month',
  selectMonth: 'Select month',
  previousYear: 'Previous year',
  nextYear: 'Next year',
  selectYear: 'Select year',
  previousYears: 'Previous years',
  nextYears: 'Next years',
  year: 'Year',
  back: 'Back',
  time: 'Time',
  now: 'Now',
  none: 'None',
  copy: 'Copy from',
  suggestedDate: 'Suggested date',
  hour: 'Hour',
  minute: 'Minute',
  second: 'Second',
  earlierHour: 'Earlier hour',
  laterHour: 'Later hour',
  earlierMinute: 'Earlier minute',
  laterMinute: 'Later minute',
  earlierSecond: 'Earlier second',
  laterSecond: 'Later second',
  activity: 'View activity for',
  activityIntensity: '{activity} {date}, {level} {dots}, {value} {measure}',
  dot: 'dot',
  dots: 'dots',
  minutes: 'minutes',
  entries: 'entries',
};

const DEFAULT_PALETTE = {
  text: '#fafafa',
  textSecondary: '#d4d4d8',
  textMuted: '#a1a1aa',
  surface: '#27272a',
  surfaceStrong: '#3f3f46',
  surfaceMuted: '#52525b',
  border: '#3f3f46',
  accent: '#6366f1',
  accentContrast: '#ffffff',
  link: '#818cf8',
  danger: '#ef4444',
  radius: 10,
  radiusSmall: 6,
  fontSize: 14,
  smallFontSize: 12,
};

function useNow(now) {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (now) return undefined;
    const timer = setInterval(() => setTick(value => value + 1), 30000);
    return () => clearInterval(timer);
  }, [now]);
  return useMemo(() => {
    if (now) return typeof now === 'function' ? now() : now;
    return new Date();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [now, tick]);
}

function chunks(items, size) {
  const result = [];
  for (let index = 0; index < items.length; index += size) {
    result.push(items.slice(index, index + size));
  }
  return result;
}

function Stepper({ label, value, onStep, palette, earlierLabel, laterLabel, style }) {
  return (
    <View style={[styles.stepper, style]}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={earlierLabel}
        onPress={() => onStep(-1)}
        style={({ pressed }) => [
          styles.stepperButton,
          { borderColor: palette.border, backgroundColor: pressed ? palette.surfaceMuted : palette.surface },
        ]}
      >
        <Text style={[styles.stepperGlyph, { color: palette.textSecondary }]}>-</Text>
      </Pressable>
      <View style={styles.stepperValue}>
        <Text style={[styles.stepperLabel, { color: palette.textMuted }]}>{label}</Text>
        <Text style={[styles.stepperNumber, { color: palette.text }]}>{value}</Text>
      </View>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={laterLabel}
        onPress={() => onStep(1)}
        style={({ pressed }) => [
          styles.stepperButton,
          { borderColor: palette.border, backgroundColor: pressed ? palette.surfaceMuted : palette.surface },
        ]}
      >
        <Text style={[styles.stepperGlyph, { color: palette.textSecondary }]}>+</Text>
      </Pressable>
    </View>
  );
}

// Same shapes as templates/app/icons/calendar.svg.
const CALENDAR_ICON = {
  viewBox: '0 0 24 24',
  root: { fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' },
  children: [
    { tag: 'path', attrs: { d: 'M8 2v4' }, children: [] },
    { tag: 'path', attrs: { d: 'M16 2v4' }, children: [] },
    { tag: 'rect', attrs: { width: '18', height: '18', x: '3', y: '4', rx: '2' }, children: [] },
    { tag: 'path', attrs: { d: 'M3 10h18' }, children: [] },
  ],
};

// Drawn from plain views so the same control renders on web and native.
function ChevronIcon({ color }) {
  return <View accessible={false} style={{ width: 8, height: 8, marginTop: -3, borderColor: color,
    borderRightWidth: 2, borderBottomWidth: 2, transform: [{ rotate: '45deg' }] }} />;
}

function CloseIcon({ color }) {
  const bar = { position: 'absolute', width: 14, height: 2, borderRadius: 1, backgroundColor: color };
  return <View accessible={false} style={{ width: 14, height: 14, alignItems: 'center', justifyContent: 'center' }}>
    <View style={[bar, { transform: [{ rotate: '45deg' }] }]} />
    <View style={[bar, { transform: [{ rotate: '-45deg' }] }]} />
  </View>;
}

export function DateTimePicker({
  value = '',
  onChange,
  trackTime = false,
  use12Hour = false,
  weekStart = 'monday',
  locale,
  now,
  labels: suppliedLabels,
  quickActions = [],
  suggestionDate = '',
  suggestionLabel = '',
  suggestionExtras = [],
  runtimeMinutes = '',
  copyValue = '',
  copyLabel = '',
  copyEmptyMessage = '',
  markerDays = [],
  activityIntensity = {},
  inline = false,
  initialViewDate = '',
  selectableDays = null,
  onQuickAction,
  onOpenChange,
  onViewMonthChange,
  open: controlledOpen,
  defaultOpen = false,
  palette: suppliedPalette,
  panelTestID = 'date-time-panel',
  testID,
}) {
  const labels = useMemo(() => ({ ...DEFAULT_LABELS, ...suppliedLabels }), [suppliedLabels]);
  const palette = useMemo(() => ({ ...DEFAULT_PALETTE, ...suppliedPalette }), [suppliedPalette]);
  const today = useNow(now);
  const { width, height } = useWindowDimensions();
  const sheet = width < 640;
  const [internalOpen, setInternalOpen] = useState(defaultOpen);
  const open = controlledOpen === undefined ? internalOpen : controlledOpen;
  const [view, setView] = useState('days');
  const valueParts = useMemo(() => parseDateValue(value), [value]);
  const initialParts = parseDateValue(initialViewDate) || valueParts || nowParts(today);
  const [viewYear, setViewYear] = useState(() => initialParts.year);
  const [viewMonth, setViewMonth] = useState(() => initialParts.month);
  const [focus, setFocus] = useState(() => initialParts);
  const [yearInput, setYearInput] = useState(String(initialParts.year));

  useEffect(() => {
    onViewMonthChange?.({ year: viewYear, month: viewMonth });
  }, [viewYear, viewMonth, onViewMonthChange]);

  useEffect(() => {
    if (!valueParts) return;
    setViewYear(valueParts.year);
    setViewMonth(valueParts.month);
    setFocus(valueParts);
    setYearInput(String(valueParts.year));
  }, [valueParts?.year, valueParts?.month, valueParts?.day]);

  function setOpen(next) {
    if (controlledOpen === undefined) setInternalOpen(next);
    onOpenChange?.(next);
    if (next) {
      const base = valueParts || nowParts(today);
      setView('days');
      setViewYear(base.year);
      setViewMonth(base.month);
      setFocus(base);
      setYearInput(String(base.year));
    }
  }

  function close() {
    setOpen(false);
  }

  function commit(next, source, detail) {
    onChange?.(next, { source, ...detail });
  }

  function timeBase() {
    if (valueParts) return valueParts;
    const current = nowParts(today);
    return { ...current, year: viewYear, month: viewMonth };
  }

  function selectDay(cell) {
    if (!isSelectableDay(selectableDays, cell)) return;
    const base = timeBase();
    commit(formatValue({ ...base, ...cell }, trackTime), 'day', { cell });
    setFocus(cell);
    if (!cell.inMonth) {
      setViewYear(cell.year);
      setViewMonth(cell.month);
    }
  }

  function changeTime(patch) {
    const base = timeBase();
    commit(formatValue({ ...base, ...patch }, trackTime), 'time');
  }

  function applyNow() {
    commit(valueFromDate(typeof now === 'function' ? now() : now || new Date(), trackTime), 'now');
  }

  function clearValue() {
    commit('', 'clear');
  }

  function applySuggestion(date) {
    const iso = date || suggestionDate;
    const parts = parseDateValue(iso);
    if (!parts) return;
    const base = timeBase();
    commit(formatValue({ ...parts, hour: base.hour, minute: base.minute, second: base.second }, trackTime), 'suggestion');
  }

  function applyCopy() {
    const parts = parseDateValue(copyValue);
    if (!parts) return;
    commit(formatValue(parts, trackTime), 'copy');
  }

  function moveMonth(amount) {
    const date = new Date(viewYear, viewMonth + amount, 1);
    setViewYear(date.getFullYear());
    setViewMonth(date.getMonth());
    setYearInput(String(date.getFullYear()));
  }

  function moveYear(amount) {
    setViewYear(viewYear + amount);
    setYearInput(String(viewYear + amount));
  }

  function moveYearPage(amount) {
    const next = viewYear + amount * 20;
    setViewYear(next);
    setYearInput(String(next));
  }

  function changeYearInput(text) {
    const digits = text.replace(/\D/g, '').slice(0, 4);
    setYearInput(digits);
    if (digits.length === 4) {
      const year = Number(digits);
      if (year >= 1000 && year <= 9999) setViewYear(year);
    }
  }

  function applyYearInput() {
    const year = Number(yearInput);
    if (Number.isInteger(year) && year >= 1000 && year <= 9999) {
      setViewYear(year);
    } else {
      setYearInput(String(viewYear));
    }
    setView('months');
  }

  const weeks = useMemo(() => monthWeeks(viewYear, viewMonth, weekStart), [viewYear, viewMonth, weekStart]);
  const weekdays = useMemo(() => weekdayLabels(weekStart, locale), [weekStart, locale]);
  const years = useMemo(() => yearPage(viewYear), [viewYear]);
  const hasValue = Boolean(value);
  const displayText = formatDisplayText(valueParts, trackTime, use12Hour, locale);
  const baseTime = timeBase();

  const quickActionButtons = quickActions.filter(action => !action.requiresSuggestion || suggestionDate);

  return (
    <View style={styles.wrapper} testID={testID}>
      {!inline ? <View
        style={[
          styles.field,
          {
            borderColor: palette.border,
            backgroundColor: palette.surfaceStrong,
            borderRadius: palette.radius,
          },
        ]}
      >
        {sheet && labels.mobileLabel ? (
          <Text style={[styles.fieldLabel, { color: palette.textMuted }]}>{labels.mobileLabel}</Text>
        ) : null}
        {labels.mobileLabel ? null : <View style={styles.fieldIcon}>
          <Icon spec={CALENDAR_ICON} color={palette.textMuted} size={16} />
        </View>}
        {hasValue ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={`${labels.picker}: ${displayText}`}
            accessibilityHint={labels.openPicker}
            onPress={() => setOpen(true)}
            style={styles.fieldValue}
          >
            <Text numberOfLines={1} style={[styles.fieldValueText, { color: palette.text }]}>
              {displayText}
            </Text>
          </Pressable>
        ) : (
          <View style={styles.quickRow}>
            <Text style={[styles.notSet, { color: palette.textMuted }]}>{labels.notSet}</Text>
            {quickActionButtons.map(action => (
              <Pressable
                key={action.id}
                accessibilityRole="button"
                accessibilityLabel={action.label}
                onPress={() => onQuickAction?.(action.id)}
                style={({ pressed }) => [
                  styles.quickAction,
                  {
                    borderColor: palette.border,
                    backgroundColor: pressed ? palette.surfaceMuted : 'transparent',
                  },
                ]}
              >
                <Text style={[styles.quickActionText, { color: palette.text }]}>{action.label}</Text>
              </Pressable>
            ))}
          </View>
        )}
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={labels.openPicker}
          onPress={() => setOpen(true)}
          style={({ pressed }) => [
            styles.iconButton,
            { backgroundColor: pressed ? palette.surfaceMuted : 'transparent' },
          ]}
        >
          <ChevronIcon color={palette.textMuted} />
        </Pressable>
        {hasValue ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={labels.clear}
            onPress={clearValue}
            style={({ pressed }) => [
              styles.iconButton,
              { backgroundColor: pressed ? palette.surfaceMuted : 'transparent' },
            ]}
          >
            <CloseIcon color={palette.textMuted} />
          </Pressable>
        ) : null}
      </View> : null}

      <PickerShell inline={inline} open={open} close={close}>
        <View style={inline ? undefined : styles.overlay}>
          {!inline ? <Pressable
            accessibilityRole="button"
            accessibilityLabel={labels.close}
            onPress={close}
            style={StyleSheet.absoluteFill}
          /> : null}
          <View
            accessibilityViewIsModal={!inline}
            style={[
              !inline && styles.panel,
              !inline && (sheet ? styles.panelSheet : styles.panelFloating),
              {
                backgroundColor: inline ? 'transparent' : palette.surface,
                borderColor: palette.border,
                borderRadius: sheet ? 16 : palette.radius,
                maxHeight: inline ? undefined : sheet ? height * 0.85 : height - 48,
              },
            ]}
            testID={panelTestID}
          >
            {sheet && !inline ? (
              <View style={styles.panelHeader}>
                <Text style={[styles.panelTitle, { color: palette.textSecondary }]}>
                  {labels.field || labels.picker}
                </Text>
                <ModalCloseButton
                  accessibilityRole="button"
                  accessibilityLabel={labels.close}
                  onPress={close}
                  style={({ pressed }) => [
                    styles.iconButton,
                    { backgroundColor: pressed ? palette.surfaceMuted : 'transparent' },
                  ]}
                >
                  <CloseIcon color={palette.textMuted} />
                </ModalCloseButton>
              </View>
            ) : null}
            <ScrollView
              keyboardShouldPersistTaps="handled"
              contentContainerStyle={inline ? undefined : styles.panelContent}
            >
              {view === 'days' ? (
                <View>
                  <View style={styles.navRow}>
                    <Pressable
                      accessibilityRole="button"
                      accessibilityLabel={labels.previousMonth}
                      onPress={() => moveMonth(-1)}
                      style={({ pressed }) => [
                        styles.navButton,
                        { backgroundColor: pressed ? palette.surfaceMuted : 'transparent' },
                      ]}
                    >
                      <Text style={[styles.navGlyph, { color: palette.textMuted }]}>{'<'}</Text>
                    </Pressable>
                    <Pressable
                      accessibilityRole="button"
                      accessibilityLabel={labels.selectMonth}
                      onPress={() => setView('months')}
                      style={styles.navLabel}
                    >
                      <Text style={[styles.navLabelText, { color: palette.text }]}>
                        {monthLabel(viewYear, viewMonth, locale)}
                      </Text>
                    </Pressable>
                    <Pressable
                      accessibilityRole="button"
                      accessibilityLabel={labels.nextMonth}
                      onPress={() => moveMonth(1)}
                      style={({ pressed }) => [
                        styles.navButton,
                        { backgroundColor: pressed ? palette.surfaceMuted : 'transparent' },
                      ]}
                    >
                      <Text style={[styles.navGlyph, { color: palette.textMuted }]}>{'>'}</Text>
                    </Pressable>
                  </View>

                  <View style={styles.weekRow}>
                    {weekdays.map(label => (
                      <Text
                        key={label}
                        style={[styles.weekday, { color: palette.textMuted }]}
                      >
                        {label}
                      </Text>
                    ))}
                  </View>

                  {weeks.map((week, weekIndex) => (
                    <View key={weekIndex} style={styles.weekRow}>
                      {week.map(cell => {
                        const key = dateKey(cell);
                        const selected = isSameDay(valueParts, cell);
                        const disabled = !isSelectableDay(selectableDays, cell);
                        const marked = isMarkedDay(markerDays, cell);
                        const intensity = activityIntensity[key];
                        const current = isToday(cell, today);
                        const color = selected
                          ? palette.accentContrast
                          : !cell.inMonth
                            ? palette.textMuted
                            : current
                              ? palette.accent
                              : palette.text;
                        return (
                          <Pressable
                            key={key}
                            accessibilityRole="button"
                            accessibilityLabel={
                              intensity ? labels.activityIntensity.replace('{activity}', labels.activity).replace('{date}', key)
                                .replace('{level}', String(intensity.level)).replace('{dots}', intensity.level === 1 ? labels.dot : labels.dots)
                                .replace('{value}', String(intensity.value)).replace('{measure}', labels[intensity.measure] || intensity.measure)
                                : marked ? `${labels.activity} ${key}` : key
                            }
                            accessibilityState={{ selected, disabled }}
                            aria-selected={selected}
                            disabled={disabled}
                            onPress={() => selectDay(cell)}
                            style={({ pressed }) => [
                              styles.dayCell,
                              {
                                backgroundColor: !selected && pressed && !disabled
                                    ? palette.surfaceMuted
                                    : 'transparent',
                                opacity: disabled ? 0.35 : !cell.inMonth ? 0.5 : 1,
                              },
                            ]}
                            testID={`date-time-day-${key}`}
                          >
                            {selected ? <View pointerEvents="none" accessible={false}
                              style={[styles.selectedDayBackground, { backgroundColor: palette.accent }]} /> : null}
                            <Text
                              style={[
                                styles.dayText,
                                {
                                  color,
                                  fontWeight: selected || current ? '600' : '400',
                                },
                              ]}
                            >
                              {cell.day}
                            </Text>
                            {marked || intensity ? (
                              <View style={styles.markerRow} accessible={false}>
                                {Array.from({ length: intensity?.level || 1 }, (_, index) => <View
                                  key={index}
                                  style={[styles.marker, { backgroundColor: selected ? palette.accentContrast : palette.accent }]}
                                />)}
                              </View>
                            ) : null}
                          </Pressable>
                        );
                      })}
                    </View>
                  ))}
                </View>
              ) : null}

              {view === 'months' ? (
                <View>
                  <View style={styles.navRow}>
                    <Pressable
                      accessibilityRole="button"
                      accessibilityLabel={labels.previousYear}
                      onPress={() => moveYear(-1)}
                      style={({ pressed }) => [
                        styles.navButton,
                        { backgroundColor: pressed ? palette.surfaceMuted : 'transparent' },
                      ]}
                    >
                      <Text style={[styles.navGlyph, { color: palette.textMuted }]}>{'<'}</Text>
                    </Pressable>
                    <Pressable
                      accessibilityRole="button"
                      accessibilityLabel={labels.selectYear}
                      onPress={() => setView('years')}
                      style={styles.navLabel}
                    >
                      <Text style={[styles.navLabelText, { color: palette.text }]}>{viewYear}</Text>
                    </Pressable>
                    <Pressable
                      accessibilityRole="button"
                      accessibilityLabel={labels.nextYear}
                      onPress={() => moveYear(1)}
                      style={({ pressed }) => [
                        styles.navButton,
                        { backgroundColor: pressed ? palette.surfaceMuted : 'transparent' },
                      ]}
                    >
                      <Text style={[styles.navGlyph, { color: palette.textMuted }]}>{'>'}</Text>
                    </Pressable>
                  </View>
                  {chunks(Array.from({ length: 12 }, (_, index) => index), 3).map(row => (
                    <View key={row[0]} style={styles.weekRow}>
                      {row.map(month => (
                        <Pressable
                          key={month}
                          accessibilityRole="button"
                          accessibilityState={{ selected: month === viewMonth }}
                          onPress={() => {
                            setViewMonth(month);
                            setView('days');
                          }}
                          style={({ pressed }) => [
                            styles.monthCell,
                            {
                              backgroundColor:
                                month === viewMonth
                                  ? palette.accent
                                  : pressed
                                    ? palette.surfaceMuted
                                    : 'transparent',
                            },
                          ]}
                        >
                          <Text
                            style={[
                              styles.monthText,
                              {
                                color:
                                  month === viewMonth
                                    ? palette.accentContrast
                                    : palette.text,
                              },
                            ]}
                          >
                            {monthShortLabel(viewYear, month, locale)}
                          </Text>
                        </Pressable>
                      ))}
                    </View>
                  ))}
                  <View style={styles.backRow}>
                    <Pressable accessibilityRole="button" onPress={() => setView('days')}>
                      <Text style={[styles.linkText, { color: palette.link }]}>{labels.back}</Text>
                    </Pressable>
                  </View>
                </View>
              ) : null}

              {view === 'years' ? (
                <View>
                  <View style={styles.navRow}>
                    <Pressable
                      accessibilityRole="button"
                      accessibilityLabel={labels.previousYears}
                      onPress={() => moveYearPage(-1)}
                      style={({ pressed }) => [
                        styles.navButton,
                        { backgroundColor: pressed ? palette.surfaceMuted : 'transparent' },
                      ]}
                    >
                      <Text style={[styles.navGlyph, { color: palette.textMuted }]}>{'<'}</Text>
                    </Pressable>
                    <TextInput
                      accessibilityLabel={labels.year}
                      keyboardType="number-pad"
                      maxLength={4}
                      onChangeText={changeYearInput}
                      onSubmitEditing={applyYearInput}
                      value={yearInput}
                      style={[
                        styles.yearInput,
                        {
                          color: palette.text,
                          borderColor: palette.border,
                          backgroundColor: palette.surfaceStrong,
                        },
                      ]}
                    />
                    <Pressable
                      accessibilityRole="button"
                      accessibilityLabel={labels.nextYears}
                      onPress={() => moveYearPage(1)}
                      style={({ pressed }) => [
                        styles.navButton,
                        { backgroundColor: pressed ? palette.surfaceMuted : 'transparent' },
                      ]}
                    >
                      <Text style={[styles.navGlyph, { color: palette.textMuted }]}>{'>'}</Text>
                    </Pressable>
                  </View>
                  {chunks(years, 4).map(row => (
                    <View key={row[0]} style={styles.weekRow}>
                      {row.map(year => (
                        <Pressable
                          key={year}
                          accessibilityRole="button"
                          accessibilityState={{ selected: year === viewYear }}
                          onPress={() => {
                            setViewYear(year);
                            setYearInput(String(year));
                            setView('months');
                          }}
                          style={({ pressed }) => [
                            styles.monthCell,
                            {
                              backgroundColor:
                                year === viewYear
                                  ? palette.accent
                                  : pressed
                                    ? palette.surfaceMuted
                                    : 'transparent',
                            },
                          ]}
                        >
                          <Text
                            style={[
                              styles.monthText,
                              {
                                color:
                                  year === viewYear ? palette.accentContrast : palette.text,
                              },
                            ]}
                          >
                            {year}
                          </Text>
                        </Pressable>
                      ))}
                    </View>
                  ))}
                  <View style={styles.backRow}>
                    <Pressable accessibilityRole="button" onPress={() => setView('months')}>
                      <Text style={[styles.linkText, { color: palette.link }]}>{labels.back}</Text>
                    </Pressable>
                  </View>
                </View>
              ) : null}

              {trackTime ? (
                <View style={[styles.section, { borderTopColor: palette.border }]}>
                  <Text style={[styles.sectionLabel, { color: palette.textMuted }]}>
                    {labels.time}
                  </Text>
                  <View style={styles.timeRow}>
                    <Stepper
                      label={labels.hour}
                      value={displayHour(baseTime.hour, use12Hour)}
                      earlierLabel={labels.earlierHour}
                      laterLabel={labels.laterHour}
                      palette={palette}
                      onStep={amount =>
                        changeTime({ hour: stepHour(baseTime.hour, amount, use12Hour) })
                      }
                    />
                    <Text style={[styles.timeColon, { color: palette.textMuted }]}>:</Text>
                    <Stepper
                      label={labels.minute}
                      value={String(baseTime.minute).padStart(2, '0')}
                      earlierLabel={labels.earlierMinute}
                      laterLabel={labels.laterMinute}
                      palette={palette}
                      onStep={amount => changeTime({ minute: stepMinute(baseTime.minute, amount) })}
                    />
                    <Text style={[styles.timeColon, { color: palette.textMuted }]}>:</Text>
                    <Stepper
                      label={labels.second}
                      value={String(baseTime.second).padStart(2, '0')}
                      earlierLabel={labels.earlierSecond}
                      laterLabel={labels.laterSecond}
                      palette={palette}
                      onStep={amount => changeTime({ second: stepSecond(baseTime.second, amount) })}
                    />
                    {use12Hour ? (
                      <Pressable
                        accessibilityRole="button"
                        accessibilityLabel={meridiem(baseTime.hour)}
                        onPress={() =>
                          changeTime({ hour: hour24FromDisplay(displayHour(baseTime.hour, true), baseTime.hour < 12) })
                        }
                        style={({ pressed }) => [
                          styles.meridiem,
                          {
                            borderColor: palette.border,
                            backgroundColor: pressed ? palette.surfaceMuted : palette.surface,
                          },
                        ]}
                      >
                        <Text style={[styles.meridiemText, { color: palette.textSecondary }]}>
                          {meridiem(baseTime.hour)}
                        </Text>
                      </Pressable>
                    ) : null}
                  </View>
                </View>
              ) : null}

              {!inline ? <View style={[styles.section, { borderTopColor: palette.border }]}>
                <View style={styles.commonRow}>
                  <Pressable
                    accessibilityRole="button"
                    onPress={applyNow}
                    style={({ pressed }) => [
                      styles.commonButton,
                      {
                        borderColor: palette.border,
                        backgroundColor: pressed ? palette.surfaceMuted : 'transparent',
                      },
                    ]}
                  >
                    <Text style={[styles.commonText, { color: palette.textSecondary }]}>
                      {labels.now}
                    </Text>
                  </Pressable>
                  <Pressable
                    accessibilityRole="button"
                    onPress={clearValue}
                    style={({ pressed }) => [
                      styles.commonButton,
                      {
                        borderColor: palette.border,
                        backgroundColor: pressed ? palette.surfaceMuted : 'transparent',
                      },
                    ]}
                  >
                    <Text style={[styles.commonText, { color: palette.textSecondary }]}>
                      {labels.none}
                    </Text>
                  </Pressable>
                  {suggestionDate ? (
                    <Pressable
                      accessibilityRole="button"
                      onPress={() => applySuggestion()}
                      style={({ pressed }) => [
                        styles.commonButton,
                        {
                          borderColor: palette.border,
                          backgroundColor: pressed ? palette.surfaceMuted : 'transparent',
                        },
                      ]}
                    >
                      <Text style={[styles.commonText, { color: palette.textSecondary }]}>
                        {suggestionLabel || labels.suggestedDate}
                      </Text>
                    </Pressable>
                  ) : null}
                  {suggestionExtras.map(extra => (
                    <Pressable
                      key={`${extra.date}-${extra.label}`}
                      accessibilityRole="button"
                      onPress={() => applySuggestion(extra.date)}
                      style={({ pressed }) => [
                        styles.commonButton,
                        {
                          borderColor: palette.border,
                          backgroundColor: pressed ? palette.surfaceMuted : 'transparent',
                        },
                      ]}
                    >
                      <Text style={[styles.commonText, { color: palette.textSecondary }]}>
                        {extra.label}
                      </Text>
                    </Pressable>
                  ))}
                  {copyLabel ? (
                    <Pressable
                      accessibilityRole="button"
                      accessibilityState={{ disabled: !copyValue }}
                      disabled={!copyValue}
                      onPress={applyCopy}
                      style={({ pressed }) => [
                        styles.commonButton,
                        {
                          borderColor: palette.border,
                          backgroundColor: pressed ? palette.surfaceMuted : 'transparent',
                          opacity: copyValue ? 1 : 0.45,
                        },
                      ]}
                    >
                      <Text style={[styles.commonText, { color: palette.textSecondary }]}>
                        {copyLabel}
                      </Text>
                    </Pressable>
                  ) : null}
                </View>
                {copyLabel && !copyValue && copyEmptyMessage ? (
                  <Text style={[styles.copyHint, { color: palette.textMuted }]}>
                    {copyEmptyMessage}
                  </Text>
                ) : null}
              </View> : null}
            </ScrollView>
          </View>
        </View>
      </PickerShell>
    </View>
  );
}

function PickerShell({ inline, open, close, children }) {
  if (inline) return children;
  return <Modal transparent visible={open} animationType="fade" onRequestClose={close} statusBarTranslucent>{children}</Modal>;
}

const styles = StyleSheet.create({
  wrapper: { minWidth: 0 },
  field: {
    flexDirection: 'row',
    alignItems: 'center',
    borderWidth: 1,
    minHeight: 40,
    overflow: 'hidden',
  },
  fieldLabel: { fontSize: 12, paddingLeft: 8, paddingRight: 4 },
  fieldValue: { flex: 1, minWidth: 0, paddingHorizontal: 8, paddingVertical: 8 },
  fieldValueText: { fontSize: 14 },
  quickRow: {
    flex: 1,
    minWidth: 0,
    flexDirection: 'row',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: 4,
    paddingHorizontal: 4,
    paddingVertical: 4,
  },
  notSet: { fontSize: 12, paddingHorizontal: 4 },
  quickAction: {
    borderWidth: 1,
    borderRadius: 6,
    paddingHorizontal: 8,
    paddingVertical: 4,
  },
  quickActionText: { fontSize: 12 },
  iconButton: {
    minWidth: 36,
    minHeight: 36,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 6,
  },
  fieldIcon: { paddingLeft: 10 },
  iconGlyph: { fontSize: 14, fontWeight: '600' },
  overlay: {
    flex: 1,
    backgroundColor: 'rgba(0, 0, 0, 0.4)',
    justifyContent: 'center',
    alignItems: 'center',
    padding: 12,
  },
  panel: {
    width: '100%',
    maxWidth: 360,
    borderWidth: 1,
    overflow: 'hidden',
  },
  panelFloating: { alignSelf: 'center' },
  panelSheet: { alignSelf: 'flex-end', marginBottom: 8 },
  panelHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 12,
    paddingTop: 8,
  },
  panelTitle: { fontSize: 13, fontWeight: '600' },
  panelContent: { padding: 12 },
  navRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 8,
  },
  navButton: {
    minWidth: 36,
    minHeight: 36,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 6,
  },
  navGlyph: { fontSize: 16, fontWeight: '600' },
  navLabel: { paddingHorizontal: 8, paddingVertical: 6 },
  navLabelText: { fontSize: 14, fontWeight: '600' },
  weekRow: { flexDirection: 'row', alignItems: 'stretch' },
  weekday: { flex: 1, textAlign: 'center', fontSize: 11, paddingVertical: 4 },
  dayCell: {
    flex: 1,
    minHeight: 36,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 6,
    margin: 1,
  },
  dayText: { fontSize: 14 },
  selectedDayBackground: { position: 'absolute', top: 0, right: 0, bottom: -1, left: 0, borderRadius: 6 },
  markerRow: { position: 'absolute', bottom: 4, flexDirection: 'row', gap: 2 },
  marker: {
    width: 4,
    height: 4,
    borderRadius: 2,
  },
  monthCell: {
    flex: 1,
    minHeight: 36,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 6,
    margin: 1,
  },
  monthText: { fontSize: 13 },
  backRow: { alignItems: 'flex-end', marginTop: 8 },
  linkText: { fontSize: 13, textDecorationLine: 'underline' },
  section: { borderTopWidth: 1, marginTop: 12, paddingTop: 12 },
  sectionLabel: { fontSize: 12, marginBottom: 6 },
  timeRow: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  timeColon: { fontSize: 16, fontWeight: '600' },
  stepper: {
    flex: 1,
    minWidth: 0,
    flexDirection: 'row',
    alignItems: 'center',
    borderWidth: 1,
    borderRadius: 6,
    overflow: 'hidden',
  },
  stepperButton: {
    width: 26,
    minHeight: 36,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 0,
  },
  stepperGlyph: { fontSize: 14, fontWeight: '700' },
  stepperValue: { flex: 1, alignItems: 'center', paddingVertical: 2 },
  stepperLabel: { fontSize: 9, textTransform: 'uppercase' },
  stepperNumber: { fontSize: 14, fontWeight: '600' },
  meridiem: {
    minWidth: 42,
    minHeight: 36,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderRadius: 6,
  },
  meridiemText: { fontSize: 12, fontWeight: '600' },
  commonRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  commonButton: {
    borderWidth: 1,
    borderRadius: 6,
    paddingHorizontal: 10,
    paddingVertical: 6,
  },
  commonText: { fontSize: 12 },
  copyHint: { fontSize: 11, marginTop: 6 },
});
