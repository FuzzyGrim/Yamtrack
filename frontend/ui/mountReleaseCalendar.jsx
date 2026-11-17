import React, { useState } from 'react';
import { mountRoot } from '../settings/root';
import { CalendarView } from '../pages/CalendarView';
import { ReleaseCalendar } from './ReleaseCalendar';
import { rootStyle } from './rootStyle.web';

function post(url, csrf, fields) {
  return fetch(url, {
    method: 'POST',
    headers: { 'X-CSRFToken': csrf },
    body: new URLSearchParams(fields),
  });
}

function withAlpha(value, alpha) {
  const hex = String(value || '').trim();
  const match = /^#([0-9a-f]{6})$/i.exec(hex);
  if (!match) return value;
  const [red, green, blue] = match[1].match(/.{2}/g).map((part) => parseInt(part, 16));
  return `rgba(${red}, ${green}, ${blue}, ${alpha})`;
}

function readPalette() {
  const css = rootStyle();
  const token = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
  const surfaceMuted = token('--color-surface-muted', '#52525b');
  return {
    background: token('--color-background', '#18181b'),
    surface: token('--color-surface', '#27272a'),
    surfaceStrong: token('--color-surface-strong', '#3f3f46'),
    surfaceMuted,
    surfaceMutedSoft: withAlpha(surfaceMuted, 0.4),
    surfaceHover: token('--color-surface-hover', '#3f3f46'),
    border: token('--color-border', '#3f3f46'),
    text: token('--color-text', '#fafafa'),
    textSecondary: token('--color-text-secondary', '#d4d4d8'),
    textMuted: token('--color-text-muted', '#a1a1aa'),
    accent: token('--color-accent', '#6366f1'),
    accentStrong: token('--color-accent-strong', '#4f46e5'),
    accentText: '#ffffff',
    link: token('--color-link', '#818cf8'),
    danger: token('--color-danger', '#ef4444'),
    overlay: 'rgba(24, 24, 27, 0.78)',
  };
}

function validValues(items) {
  return new Set((items || []).map((item) => item.value));
}

function savedFilters(props) {
  const defaults = props.initialFilters || {};
  let saved = {};
  try {
    saved = JSON.parse(window.localStorage.getItem(props.filterStorageKey)) || {};
  } catch (error) {
    return defaults;
  }

  const types = validValues(props.mediaTypes);
  const releaseTypes = validValues(props.releaseTypes);
  const statusesByType = props.statusesByType || {};
  const next = { ...defaults };

  const savedTypes = saved.selectedTypes || saved.selectedMediaTypes;
  if (Array.isArray(savedTypes)) {
    next.selectedTypes = savedTypes.filter((value) => types.has(value));
  }
  if (Array.isArray(saved.selectedReleaseTypes)) {
    next.selectedReleaseTypes = saved.selectedReleaseTypes.filter((value) =>
      releaseTypes.has(value),
    );
  }
  if (
    saved.selectedStatusesByType &&
    typeof saved.selectedStatusesByType === 'object'
  ) {
    const merged = {};
    Object.entries(defaults.selectedStatusesByType || {}).forEach(
      ([type, fallback]) => {
        const available = new Set(
          (statusesByType[type] || []).map((status) => status.value),
        );
        const stored = saved.selectedStatusesByType[type];
        merged[type] = Array.isArray(stored)
          ? stored.filter((status) => available.has(status))
          : fallback;
      },
    );
    next.selectedStatusesByType = merged;
  }
  return next;
}

function Adapter({ props }) {
  const [palette] = useState(readPalette);
  const [initialFilters] = useState(() => savedFilters(props));

  function persistFilters(filters) {
    try {
      window.localStorage.setItem(
        props.filterStorageKey,
        JSON.stringify(filters),
      );
    } catch (error) {
      // Ignore storage failures (private mode or disabled storage).
    }
  }

  function navigate(url) {
    if (url) {
      window.location.assign(url);
    }
  }

  function menuAction({ key, value }) {
    const menu = props.menu || {};
    if (key === 'fetch') {
      const form = Object.assign(document.createElement('form'), {
        method: 'post',
        action: menu.urls?.fetch,
      });
      form.append(Object.assign(document.createElement('input'), {
        type: 'hidden',
        name: 'csrfmiddlewaretoken',
        value: menu.csrf,
      }));
      document.body.append(form);
      form.submit();
      return;
    }
    if (key === 'today') {
      navigate(menu.urls?.today);
      return;
    }
    if (key === 'download') {
      navigate(menu.urls?.download);
      return;
    }
    if (key === 'weekStart' && menu.weekStart !== value) {
      post(menu.urls.preferences, menu.csrf, {
        week_start_day: value,
      }).then(() => window.location.reload());
    }
  }

  return <CalendarView
    title=""
    subtitle=""
    chrome={false}
    sections={['calendar']}
    palette={palette}
    calendar={<ReleaseCalendar
      {...props}
      palette={palette}
      initialFilters={initialFilters}
      onNavigate={navigate}
      onOpenRelease={(release) => navigate(release.href)}
      onViewChange={(view) => navigate(props.navigation?.views?.[view])}
      onFiltersChange={persistFilters}
      onMenuAction={menuAction} />} />;
}

export function mountReleaseCalendar(host, props) {
  return mountRoot(host, <Adapter props={props} />);
}
