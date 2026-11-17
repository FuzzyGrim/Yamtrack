import React, { useEffect, useMemo, useState } from 'react';
import { mountRoot } from '../settings/root';
import { StatisticsView } from '../pages/StatisticsView';
import { StatisticsActivityOverview } from './StatisticsSummary';
import { rootStyle } from './rootStyle.web';

const WEEKDAY_NAMES = [
  'Monday',
  'Tuesday',
  'Wednesday',
  'Thursday',
  'Friday',
  'Saturday',
  'Sunday',
];
const DAY_MS = 86400000;

function themeRadius(css) {
  const value = parseFloat(css.getPropertyValue('--theme-radius'));
  return Number.isFinite(value) && value > 0 ? value : 18;
}

function surfaceColor(css, token) {
  const base = token('--color-surface', '#27272a');
  const opacity = parseFloat(css.getPropertyValue('--theme-surface-opacity'));
  const percent = Number.isFinite(opacity) ? opacity : 100;
  const hex = base.replace('#', '');
  if (percent >= 100 || !/^[0-9a-fA-F]{6}$/.test(hex)) return base;
  const r = parseInt(hex.slice(0, 2), 16);
  const g = parseInt(hex.slice(2, 4), 16);
  const b = parseInt(hex.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${percent / 100})`;
}

function readPalette() {
  const css = rootStyle();
  const token = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
  return {
    background: token('--color-page-bg', '#18181b'),
    surface: surfaceColor(css, token),
    surfaceStrong: token('--color-surface-strong', '#3f3f46'),
    surfaceMuted: token('--color-surface-muted', '#52525b'),
    border: token('--color-surface-border', token('--color-border', '#3f3f46')),
    text: token('--color-text', '#fafafa'),
    textSecondary: token('--color-text-secondary', '#d4d4d8'),
    textMuted: token('--color-text-muted', '#a1a1aa'),
    accent: token('--color-accent', '#6366f1'),
    accentStrong: token('--color-accent-strong', '#4f46e5'),
    accentContrast: '#ffffff',
    link: token('--color-link', '#818cf8'),
    danger: token('--color-danger', '#ef4444'),
    warning: token('--color-warning', '#f59e0b'),
    success: token('--color-success', '#10b981'),
    radius: themeRadius(css),
    radiusSmall: 6,
  };
}

function readSelectedTypes(validTypes) {
  const params = new URLSearchParams(window.location.search);
  const wanted = new Set(
    String(params.get('media-type') || '')
      .split(',')
      .map(value => value.trim()),
  );
  const selected = validTypes.filter(value => wanted.has(value));
  return selected.length === validTypes.length ? [] : selected;
}

function epochDayToIso(day) {
  return new Date(day * DAY_MS).toISOString().slice(0, 10);
}

function streaksFromDays(daySet, endDay) {
  const days = [...daySet].sort((a, b) => a - b);
  if (!days.length) {
    return { current: 0, longest: 0, longestStart: null, longestEnd: null };
  }
  let longest = 1;
  let longestStart = days[0];
  let longestEnd = days[0];
  let streakStart = days[0];
  let prev = days[0];
  const closeRun = () => {
    const length = prev - streakStart + 1;
    if (length > longest || (length === longest && prev > longestEnd)) {
      longest = length;
      longestStart = streakStart;
      longestEnd = prev;
    }
  };
  for (let index = 1; index < days.length; index += 1) {
    if (days[index] - prev === 1) {
      prev = days[index];
      continue;
    }
    closeRun();
    streakStart = days[index];
    prev = days[index];
  }
  closeRun();

  let current = 0;
  if (endDay !== null && daySet.has(endDay)) {
    current = 1;
    while (daySet.has(endDay - current)) current += 1;
  }
  return { current, longest, longestStart, longestEnd };
}

function mergeSummary(rows) {
  if (!rows.length) return {};
  let completed = 0;
  let total = 0;
  let totalMinutes = 0;
  let scoreSum = 0;
  let scoreCount = 0;
  const weekday = [0, 0, 0, 0, 0, 0, 0];
  const activeDays = new Set();
  let endDay;
  rows.forEach((row) => {
    completed += row.completed || 0;
    total += row.total || 0;
    totalMinutes += row.totalMinutes || 0;
    scoreSum += row.scoreSum || 0;
    scoreCount += row.scoreCount || 0;
    (row.weekdayMinutes || []).forEach((minutes, index) => {
      weekday[index] += minutes;
    });
    (row.activeRuns || []).forEach(([start, length]) => {
      for (let offset = 0; offset < length; offset += 1) {
        activeDays.add(start + offset);
      }
    });
    if (row.streakEndDay !== undefined && row.streakEndDay !== null) {
      endDay = row.streakEndDay;
    }
  });

  const weekdayTotal = weekday.reduce((sum, minutes) => sum + minutes, 0);
  let mostActiveDay = '';
  let mostActivePercentage = 0;
  if (weekdayTotal > 0) {
    const top = weekday.indexOf(Math.max(...weekday));
    mostActiveDay = WEEKDAY_NAMES[top];
    mostActivePercentage = Math.round((weekday[top] / weekdayTotal) * 100);
  }
  const streaks = streaksFromDays(activeDays, endDay);
  const start = streaks.longestStart === null ? '' : epochDayToIso(streaks.longestStart);
  const end = streaks.longestEnd === null ? '' : epochDayToIso(streaks.longestEnd);

  return {
    completed,
    total,
    totalMinutes,
    totalHours: Math.round(totalMinutes / 60),
    totalDays: Math.floor(totalMinutes / 60 / 24),
    averageScore: scoreCount ? Math.round((scoreSum / scoreCount) * 100) / 100 : null,
    hasScore: scoreCount > 0,
    mostActiveDay,
    mostActiveDayLabel: mostActiveDay,
    mostActiveDayPercentage: mostActivePercentage,
    currentStreak: streaks.current,
    longestStreak: streaks.longest,
    longestStreakStart: start,
    longestStreakDates: start && start !== end ? `${start} – ${end}` : start,
    typeLabel: 'titles',
    typeFlavor: 'stories, ideas, and worlds',
  };
}

function Adapter({ props }) {
  const [palette, setPalette] = useState(readPalette);
  const validTypes = useMemo(() => Object.keys(props.types || {}), [props.types]);
  const [selectedTypes, setSelectedTypes] = useState(() => readSelectedTypes(validTypes));

  useEffect(() => {
    const updatePalette = () => setPalette(readPalette());
    const observer = new MutationObserver(updatePalette);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['class', 'style'],
    });
    const scheme = window.matchMedia('(prefers-color-scheme: dark)');
    scheme.addEventListener('change', updatePalette);

    const updateSelection = () => setSelectedTypes(readSelectedTypes(validTypes));
    window.addEventListener('stats-media-type-changed', updateSelection);
    window.addEventListener('popstate', updateSelection);
    return () => {
      observer.disconnect();
      scheme.removeEventListener('change', updatePalette);
      window.removeEventListener('stats-media-type-changed', updateSelection);
      window.removeEventListener('popstate', updateSelection);
    };
  }, [validTypes]);

  const model = useMemo(() => {
    const single = selectedTypes.length === 1 ? selectedTypes[0] : null;
    const summary = single
      ? props.types?.[single] || props.all
      : selectedTypes.length > 1
        ? mergeSummary(selectedTypes.map(type => props.types?.[type]).filter(Boolean))
        : props.all;
    const highlights = single
      ? props.highlights?.types?.[single] || props.highlights?.all || []
      : props.highlights?.all || [];
    return {
      summary: summary || {},
      highlights,
      scaleMax: props.scaleMax || 10,
      updatedLabel: props.updatedLabel || '',
      loading: false,
      error: false,
    };
  }, [props, selectedTypes]);

  function openHighlight(item) {
    if (item?.href) window.location.assign(item.href);
  }

  return <StatisticsView
    title=""
    subtitle=""
    chrome={false}
    sections={['summary']}
    palette={palette}
    summary={<StatisticsActivityOverview
      model={model}
      palette={palette}
      onSelectHighlight={openHighlight} />} />;
}

export function mountStatisticsSummary(host, props) {
  return mountRoot(host, <Adapter props={props} />);
}
