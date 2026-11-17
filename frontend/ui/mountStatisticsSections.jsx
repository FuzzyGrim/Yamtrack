import React, { useEffect, useMemo, useRef, useState } from 'react';
import { mountRoot } from '../settings/root';
import {
  StatisticsMediaTypeOverview,
  StatisticsStudioFootprint,
  StatisticsTasteSignals,
} from './StatisticsSections';
import { rootStyle } from './rootStyle.web';

const CSRF_COOKIE = 'csrftoken';

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

function readCookie(name) {
  const row = document.cookie
    .split('; ')
    .find(cookie => cookie.startsWith(`${name}=`));
  return row ? decodeURIComponent(row.slice(name.length + 1)) : '';
}

function mediaTypeParam() {
  return new URLSearchParams(window.location.search).get('media-type') || '';
}

async function postSection(url, fields, query) {
  const body = new URLSearchParams();
  Object.entries({ ...query, ...fields }).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      body.set(key, value);
    }
  });
  const response = await fetch(url, {
    method: 'POST',
    credentials: 'same-origin',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/x-www-form-urlencoded',
      'X-CSRFToken': readCookie(CSRF_COOKIE),
      'X-Requested-With': 'XMLHttpRequest',
    },
    body: body.toString(),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok || !data?.success) {
    throw new Error(data?.error || 'The server could not refresh this section.');
  }
  return data;
}

function Adapter({ host, props }) {
  const [palette, setPalette] = useState(readPalette);
  const [model, setModel] = useState(props);

  useEffect(() => {
    const update = () => setPalette(readPalette());
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['class', 'style'],
    });
    const scheme = window.matchMedia('(prefers-color-scheme: dark)');
    scheme.addEventListener('change', update);
    return () => {
      observer.disconnect();
      scheme.removeEventListener('change', update);
    };
  }, []);

  const section = host.dataset.section || 'overview';
  const updateUrl = host.dataset.updateUrl || '';
  const query = useMemo(() => ({
    range_name: host.dataset.rangeName || '',
    start_date: host.dataset.startDate || '',
    end_date: host.dataset.endDate || '',
  }), [host]);

  const selectedSort = useRef(model.selected);
  useEffect(() => {
    selectedSort.current = model.selected;
  }, [model.selected]);
  useEffect(() => setModel(props), [props]);

  useEffect(() => {
    if (!updateUrl || (section !== 'studioFootprint' && section !== 'tasteSignals')) {
      return undefined;
    }
    let cancelled = false;
    const refreshForMediaTypes = async () => {
      try {
        const data = await postSection(
          updateUrl,
          { sort_by: selectedSort.current, media_type: mediaTypeParam() },
          query,
        );
        if (cancelled) return;
        const next = section === 'studioFootprint'
          ? data.studio_footprint
          : data.taste_signals;
        if (next) setModel(next);
      } catch (error) {
        console.error('Statistics section could not refresh', error);
      }
    };
    window.addEventListener('stats-media-type-changed', refreshForMediaTypes);
    return () => {
      cancelled = true;
      window.removeEventListener('stats-media-type-changed', refreshForMediaTypes);
    };
  }, [query, section, updateUrl]);

  function openItem(item) {
    if (item?.href) window.location.assign(item.href);
  }

  async function changeSort(sortBy) {
    if (!updateUrl) return;
    const previous = model;
    setModel({ ...model, selected: sortBy });
    try {
      const data = await postSection(
        updateUrl,
        { sort_by: sortBy, media_type: mediaTypeParam() },
        query,
      );
      const next = section === 'studioFootprint'
        ? data.studio_footprint
        : data.taste_signals;
      if (next) setModel(next);
    } catch (error) {
      setModel(previous);
      console.error('Statistics section sort could not be saved', error);
    }
  }

  if (section === 'studioFootprint') {
    return <StatisticsStudioFootprint
      model={model}
      palette={palette}
      onSortChange={changeSort} bare />;
  }
  if (section === 'tasteSignals') {
    return <StatisticsTasteSignals
      model={model}
      palette={palette}
      onSortChange={changeSort} bare />;
  }
  return <StatisticsMediaTypeOverview
    model={model}
    palette={palette}
    onSelectItem={openItem} />;
}

export function mountStatisticsSections(host, props) {
  // The release page has no standalone comparison card; the header dropdown
  // owns compare mode. Older markup still ships an empty host here.
  if (host.dataset.section === 'comparison') {
    host.style.display = 'none';
    return null;
  }
  return mountRoot(host, <Adapter host={host} props={props} />);
}
