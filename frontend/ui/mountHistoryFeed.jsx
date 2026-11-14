import React, { useEffect, useState } from 'react';
import { mountRoot } from '../settings/root';
import { HistoryActivityDialog } from './HistoryFeed';
import { HistoryView } from '../pages/HistoryView';
import { openModal } from './ModalHost';

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
  const css = getComputedStyle(document.documentElement);
  const token = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
  const color = (name, fallback) => resolveColor(token(name, fallback), fallback);
  return {
    background: color('--color-page-bg', '#18181b'),
    surface: color('--color-surface', '#27272a'),
    surfaceStrong: color('--color-surface-strong', '#3f3f46'),
    surfaceMuted: color('--color-surface-muted', '#52525b'),
    border: color('--color-surface-border', '#3f3f46'),
    text: color('--color-text', '#fafafa'),
    textSecondary: color('--color-text-secondary', '#d4d4d8'),
    textMuted: color('--color-text-muted', '#a1a1aa'),
    accent: color('--color-accent', '#6366f1'),
    accentStrong: color('--color-accent-strong', '#4f46e5'),
    accentContrast: '#ffffff',
    link: color('--color-link', '#818cf8'),
    danger: color('--color-danger', '#ef4444'),
    warning: '#f59e0b',
    success: '#10b981',
    radius: 8,
    radiusSmall: 6,
    mediaCard: {
      card: color('--color-surface', '#27272a'),
      text: color('--color-text', '#fafafa'),
      textSecondary: color('--color-text-secondary', '#d4d4d8'),
      textMuted: color('--color-text-muted', '#a1a1aa'),
      border: color('--color-surface-border', '#3f3f46'),
      link: color('--color-link', '#818cf8'),
      star: '#facc15',
      chipBackground: 'rgba(24, 24, 27, 0.92)',
      chipText: '#ffffff',
      placeholder: color('--color-surface-strong', '#3f3f46'),
      progressTrack: 'rgba(24, 24, 27, 0.6)',
      progressFill: color('--color-accent', '#6366f1'),
      progressPaused: 'rgba(99, 102, 241, 0.4)',
    },
  };
}

function assign(url) {
  if (url) window.location.assign(url);
}

const MANAGED_QUERY_KEYS = [
  'start-date', 'end-date', 'media_type', 'genre', 'implied_genre',
  'history_mode', 'page', 'entry_offset',
];

function filterList(state, key, legacyKey) {
  if (Array.isArray(state[key])) return state[key];
  if (Array.isArray(state[legacyKey])) return state[legacyKey];
  const legacy = state[legacyKey];
  return legacy ? [legacy] : [];
}

function filterQuery(state) {
  const params = new URLSearchParams();
  if (state.from) params.set('start-date', state.from);
  if (state.to) params.set('end-date', state.to);
  if (state.mediaTypes?.length) params.set('media_type', state.mediaTypes.join(','));
  const genres = filterList(state, 'genres', 'genre');
  const impliedGenres = filterList(state, 'impliedGenres', 'impliedGenre');
  if (genres.length) params.set('genre', genres.join(','));
  if (impliedGenres.length) params.set('implied_genre', impliedGenres.join(','));
  if (state.mode && state.mode !== 'activity') params.set('history_mode', state.mode);
  return params;
}

function historyUrl(state, props) {
  const params = new URLSearchParams(window.location.search);
  MANAGED_QUERY_KEYS.forEach(key => params.delete(key));
  filterQuery(state).forEach((value, key) => params.set(key, value));
  const query = params.toString();
  return `${props.baseUrl || window.location.pathname}${query ? `?${query}` : ''}`;
}

function replaceDay(days, key, update) {
  return days.map(day => (day.key === key ? update(day) : day));
}

// The server renders the release card for every day before React mounts, and
// each card keeps its JSON model in a script tag. Reuse that model so the
// browser feed shows the same status chip, play-count line and hover actions
// without a second request. Later day pages (load more, lazy days) arrive as
// JSON and fall back to the fields the day endpoint sends.
function legacyCardModels(host) {
  const models = [];
  host.querySelectorAll('.history-day script[id^="media-card-"][type="application/json"]').forEach(script => {
    try {
      const payload = JSON.parse(script.textContent);
      if (payload?.model?.title) models.push(payload.model);
    } catch (error) {
      // A malformed island only costs that card its extra metadata.
    }
  });
  return models;
}

function withLegacyCards(model, cards) {
  const days = model.days || [];
  if (!cards.length || days.some(day => (day.entries || []).some(entry => entry.card))) {
    return model;
  }
  const byKey = new Map();
  cards.forEach(card => {
    const key = `${card.href || ''}|${card.title || ''}`;
    if (!byKey.has(key)) byKey.set(key, card);
  });
  return {
    ...model,
    days: days.map(day => ({
      ...day,
      entries: (day.entries || []).map(entry => (entry.card ? entry : {
        ...entry,
        card: byKey.get(`${entry.href || ''}|${entry.title || ''}`) || null,
      })),
    })),
  };
}

function Adapter({ host, props }) {
  const [palette, setPalette] = useState(readPalette);
  const [model, setModel] = useState(() => props.model || { days: [] });
  const [activityEntry, setActivityEntry] = useState(null);
  const [optionsLoaded, setOptionsLoaded] = useState(false);

  useEffect(() => {
    const update = () => setPalette(readPalette());
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'style'] });
    const scheme = window.matchMedia('(prefers-color-scheme: dark)');
    scheme.addEventListener('change', update);
    return () => {
      observer.disconnect();
      scheme.removeEventListener('change', update);
    };
  }, []);

  function updateDay(day, update) {
    setModel(current => ({
      ...current,
      days: replaceDay(current.days || [], day.key, currentDay => (
        typeof update === 'function' ? update(currentDay) : update
      )),
    }));
  }

  async function fetchDay(day, url, append) {
    if (!url) return;
    updateDay(day, current => ({
      ...current,
      loading: !append,
      loadingMore: append,
      error: false,
    }));
    try {
      const response = await window.fetch(url, {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) throw new Error(`History day failed: ${response.status}`);
      const payload = await response.json();
      const next = payload.day;
      if (!next) throw new Error('History day response is missing its day.');
      updateDay(day, current => (append ? {
        ...current,
        ...next,
        entries: [...(current.entries || []), ...(next.entries || [])],
        loading: false,
        loadingMore: false,
        error: false,
      } : {
        ...next,
        loading: false,
        loadingMore: false,
        error: false,
      }));
    } catch (error) {
      updateDay(day, current => ({
        ...current,
        loading: false,
        loadingMore: false,
        error: true,
      }));
    }
  }

  function navigate(intent) {
    if (intent?.url) assign(intent.url);
  }

  function loadMore(day) {
    fetchDay(day, day.nextUrl, true);
  }

  function loadDay(day) {
    fetchDay(day, day.loadUrl, false);
  }

  function openEntry(entry) {
    assign(entry.href);
  }

  async function openActivity(entry) {
    if (!entry?.activityUrl) {
      openEntry(entry);
      return;
    }
    try {
      const url = new URL(entry.activityUrl, window.location.origin);
      url.searchParams.set('format', 'json');
      const response = await window.fetch(url, {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) throw new Error(`History activity failed: ${response.status}`);
      const payload = await response.json();
      setActivityEntry({
        ...entry,
        sessions: payload.sessions || [],
        sessionStats: payload.stats || [],
      });
    } catch (error) {
      openEntry(entry);
    }
  }

  function editEntry(entry) {
    setActivityEntry(null);
    if (!entry.editUrl || !entry.editTarget) {
      openEntry(entry);
      return;
    }
    openModal({
      modal: 'track',
      url: entry.editUrl,
      target: entry.editTarget,
      vals: {
        ...(entry.editVals || {}),
        return_url: window.location.pathname + window.location.search,
      },
    });
  }

  function deleteEntry(entry) {
    if (!entry.deleteUrl) return;
    window.fetch(entry.deleteUrl, {
      method: 'DELETE',
      headers: { 'X-CSRFToken': props.csrfToken || '' },
    }).then(response => {
      if (!response.ok) throw new Error(`History delete failed: ${response.status}`);
      window.dispatchEvent(new CustomEvent('history-refresh-start'));
    }).catch(() => assign(entry.href || window.location.pathname));
  }

  function applyFilters(state) {
    assign(historyUrl(state, props));
  }

  function clearFilters() {
    const params = new URLSearchParams(window.location.search);
    MANAGED_QUERY_KEYS.forEach(key => params.delete(key));
    const query = params.toString();
    assign(`${props.baseUrl || window.location.pathname}${query ? `?${query}` : ''}`);
  }

  async function loadFilterOptions() {
    if (optionsLoaded || !props.filterOptionsUrl) return;
    setOptionsLoaded(true);
    try {
      const response = await window.fetch(props.filterOptionsUrl, {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) throw new Error(`History genres failed: ${response.status}`);
      const payload = await response.json();
      setModel(current => ({
        ...current,
        options: {
          ...(current.options || {}),
          genres: payload.genres || [],
          impliedGenres: payload.implied_genres || [],
        },
      }));
    } catch (error) {
      setOptionsLoaded(false);
    }
  }

  function saveView(state) {
    const name = window.prompt(props.saveViewPrompt || 'Name this view');
    if (!name || !name.trim()) return;
    const body = new URLSearchParams();
    body.set('media_type', 'history');
    body.set('name', name.trim());
    body.set('query', filterQuery(state).toString());
    window.fetch(props.saveViewUrl || '/saved-views/create/', {
      method: 'POST',
      headers: { 'X-CSRFToken': props.csrfToken || '' },
      body,
    }).then(response => response.json())
      .then(data => { if (data.url) window.location.assign(data.url); })
      .catch(() => {});
  }

  return <HistoryView
    title=""
    subtitle=""
    chrome={false}
    model={model}
    labels={props.labels}
    palette={palette}
    onNavigate={navigate}
    onLoadMore={loadMore}
    onLoadDay={loadDay}
    onOpenEntry={openEntry}
    onOpenActivity={openActivity}
    onEditEntry={editEntry}
    onDeleteEntry={deleteEntry}
    onApplyFilters={applyFilters}
    onClearFilters={clearFilters}
    onSaveView={saveView}
    onRequestOptions={loadFilterOptions}
    onRetry={() => assign(props.baseUrl || window.location.pathname)}
    onRetryDay={loadDay}
    onRefresh={() => assign(props.baseUrl || window.location.pathname)}>
    <HistoryActivityDialog
      entry={activityEntry}
      labels={props.labels}
      palette={palette}
      onClose={() => setActivityEntry(null)}
      onEdit={editEntry}
      onDelete={deleteEntry} />
  </HistoryView>;
}

export function mountHistoryFeed(host, props) {
  const model = withLegacyCards(props.model || { days: [] }, legacyCardModels(host));
  return mountRoot(host, <Adapter host={host} props={{ ...props, model }} />);
}
