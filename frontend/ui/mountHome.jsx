import React, { useEffect, useMemo, useState } from 'react';
import { mountRoot } from '../settings/root';
import { HomeView } from '../pages/HomeView';
import { useShelfCardWidth } from './shelfWidth.web';
import { MediaCard } from './MediaCard.web';
import { ProgressChanger, progressControlHeight } from './ProgressChangerTheme.web';
import { createViewportRowWatcher } from './homeRowWatcher.web';

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
    surfaceHover: color('--color-surface-strong', '#3f3f46'),
    border: color('--color-surface-border', '#3f3f46'),
    text: color('--color-text', '#fafafa'),
    textSecondary: color('--color-text-secondary', '#d4d4d8'),
    textMuted: color('--color-text-muted', '#a1a1aa'),
    accent: color('--color-accent', '#6366f1'),
    accentStrong: color('--color-accent-strong', '#4f46e5'),
    fill: color('--color-accent', '#6366f1'),
    link: color('--color-link', '#818cf8'),
    danger: color('--color-danger', '#ef4444'),
    warning: '#f59e0b',
    success: '#10b981',
    arrow: color('--color-text-muted', '#a1a1aa'),
    arrowDisabled: color('--color-surface-muted', '#52525b'),
    loading: color('--color-accent', '#6366f1'),
    focus: color('--color-accent', '#6366f1'),
    card: color('--color-surface', '#27272a'),
    star: '#facc15',
    chipBackground: 'rgba(24, 24, 27, 0.92)',
    chipText: '#ffffff',
    placeholder: color('--color-surface-strong', '#3f3f46'),
    progressTrack: 'rgba(24, 24, 27, 0.6)',
    progressFill: color('--color-accent', '#6366f1'),
    progressPaused: 'rgba(99, 102, 241, 0.4)',
  };
}

// Waiting Home rows take the height loaded rows had on this browser last time.
const ROW_HEIGHTS_KEY = 'floppy.home.rowHeights';
let rememberedHeights = null;
function loadRememberedHeights() {
  if (rememberedHeights) return rememberedHeights;
  try {
    rememberedHeights = JSON.parse(window.localStorage.getItem(ROW_HEIGHTS_KEY) || '{}') || {};
  } catch (error) {
    rememberedHeights = {};
  }
  return rememberedHeights;
}
const rowHeightMemory = {
  get: key => loadRememberedHeights()[key],
  set(key, height) {
    loadRememberedHeights()[key] = height;
    try {
      window.localStorage.setItem(ROW_HEIGHTS_KEY, JSON.stringify(rememberedHeights));
    } catch (error) {
      // Private windows and blocked storage just estimate again next visit.
    }
  },
};

function assign(url) {
  if (url) window.location.assign(url);
}

function mergeRowPage(row, page) {
  if (!page) return row;
  return {
    ...row,
    ...page,
    items: [...(row.items || []), ...(page.items || [])],
    loadedCount: page.loadedCount ?? (row.items || []).length + (page.items || []).length,
    total: page.total ?? row.total,
    seed: page.seed ?? row.seed,
    pollForCovers: !!(row.pollForCovers || page.pollForCovers),
  };
}

function Adapter({ host, props }) {
  const [palette, setPalette] = useState(readPalette);
  const itemWidth = useShelfCardWidth();
  // Rows below the fold load as they come within reach of the viewport.
  const [watchRow] = useState(() => createViewportRowWatcher());
  // The progress control's height follows the same breakpoint as card width.
  const progressHeight = useMemo(() => progressControlHeight(host), [host, itemWidth]);

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

  async function requestJson(url, options = {}) {
    const response = await window.fetch(url, {
      credentials: 'same-origin',
      ...options,
      headers: {
        Accept: 'application/json',
        ...(options.body ? { 'Content-Type': 'application/json' } : {}),
        ...(options.method === 'POST' ? { 'X-CSRFToken': props.csrfToken || '' } : {}),
        ...(options.headers || {}),
      },
    });
    if (!response.ok) throw new Error(`Home request failed: ${response.status}`);
    return response.json();
  }

  async function loadRow(rowId, offset = 0, seed = '') {
    const params = new URLSearchParams();
    params.set('offset', String(offset));
    if (seed !== '' && seed != null) params.set('seed', String(seed));
    if (props.itemsLimit) params.set('limit', String(props.itemsLimit));
    const payload = await requestJson(`/api/v1/home/row/${encodeURIComponent(rowId)}/?${params}`);
    return payload.row || null;
  }

  async function loadMore(row) {
    const page = await loadRow(row.rowId, row.loadedCount || 0, row.seed ?? '');
    return page ? mergeRowPage(row, page) : null;
  }

  async function sortRow(row) {
    const payload = await requestJson(
      `/api/v1/home/row/${encodeURIComponent(row.rowId)}/sort/`,
      { method: 'POST', body: '{}' },
    );
    return payload.row || null;
  }

  function refreshCovers(ids, pending) {
    const params = new URLSearchParams();
    params.set('ids', ids.join(','));
    params.set('pending', String(pending));
    return requestJson(`/api/v1/home/artwork/?${params}`);
  }

  function renderCard(item) {
    return <MediaCard model={item.card} labels={props.labels} host={host} standalone
      variant={item.card?.shape === 'square' ? 'grid-square' : 'grid'} />;
  }

  function renderProgress(progress) {
    return <ProgressChanger host={host} props={progress} />;
  }

  return <HomeView
    groups={props.groups || []}
    deferredGroups={props.deferredGroups || []}
    showMediaTypeHeaders={!!props.showMediaTypeHeaders}
    labels={props.labels}
    palette={palette}
    readOnly={!!props.readOnly}
    itemWidth={itemWidth}
    renderCard={renderCard}
    renderProgress={renderProgress}
    onOpenCard={item => assign(item.card.href)}
    onOpenLink={href => assign(href)}
    onOpenRowTitle={href => assign(href)}
    onLoadRow={id => loadRow(id)}
    watchRow={watchRow}
    rowHeights={rowHeightMemory}
    progressHeight={progressHeight}
    onLoadMore={loadMore}
    onSortRow={sortRow}
    onRefreshCovers={refreshCovers}
    onOpenSettings={() => assign(props.settingsUrl || '/settings/home-screen')} />;
}

export function mountHome(host, props) {
  return mountRoot(host, <Adapter host={host} props={props} />);
}
