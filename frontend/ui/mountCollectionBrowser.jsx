import React, { useEffect, useRef, useState } from 'react';
import { mountRoot } from '../settings/root';
import { CollectionView } from '../pages/CollectionView';
import { MediaCard } from './MediaCard.web';
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

function Adapter({ host, props }) {
  const [palette, setPalette] = useState(readPalette);
  const [model, setModel] = useState(() => props.model || props || { items: [] });
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadingGroups, setLoadingGroups] = useState({});
  const loading = useRef(false);

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

  async function requestJson(url) {
    const response = await window.fetch(url, {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    });
    if (!response.ok) throw new Error(`Collection page failed: ${response.status}`);
    return response.json();
  }

  async function loadMore() {
    if (!model.nextUrl || loading.current) return;
    loading.current = true;
    setLoadingMore(true);
    try {
      const payload = await requestJson(model.nextUrl);
      setModel(current => ({
        ...current,
        items: [...(current.items || []), ...(payload.items || [])],
        nextUrl: payload.nextUrl || '',
        resultCount: payload.resultCount ?? current.resultCount,
      }));
    } catch (error) {
      assign(model.nextUrl);
    } finally {
      loading.current = false;
      setLoadingMore(false);
    }
  }

  async function loadMoreGroup(group) {
    if (!group?.nextUrl) return;
    const key = group.key || group.type;
    setLoadingGroups(current => ({ ...current, [key]: true }));
    try {
      const payload = await requestJson(group.nextUrl);
      setModel(current => ({
        ...current,
        groups: (current.groups || []).map(entry => (entry.key || entry.type) === key
          ? {
            ...entry,
            items: [...(entry.items || []), ...(payload.items || [])],
            loadedCount: (entry.loadedCount ?? (entry.items || []).length) + (payload.items || []).length,
            nextUrl: payload.nextUrl || '',
            total: payload.resultCount ?? entry.total,
          }
          : entry),
      }));
    } catch (error) {
      assign(group.nextUrl);
    } finally {
      setLoadingGroups(current => {
        const next = { ...current };
        delete next[key];
        return next;
      });
    }
  }

  const groups = (model.groups || []).map(group => ({
    ...group,
    loadingMore: !!loadingGroups[group.key || group.type],
  }));

  useEffect(() => {
    if (!model.nextUrl || typeof IntersectionObserver === 'undefined') return undefined;
    let observer;
    let frame = window.requestAnimationFrame(() => {
      const sentinel = host.querySelector('[data-testid="collection-load-more"]');
      if (!sentinel) return;
      observer = new IntersectionObserver(entries => {
        if (entries.some(entry => entry.isIntersecting)) loadMore();
      }, { rootMargin: '400px' });
      observer.observe(sentinel);
    });
    return () => {
      window.cancelAnimationFrame(frame);
      observer?.disconnect();
    };
  }, [model.nextUrl, model.items.length]);

  return <CollectionView
    title=""
    subtitle=""
    items={model.items || []}
    groups={model.groups ? groups : null}
    filters={model.filters || {}}
    options={model.options || {}}
    labels={props.labels}
    palette={palette}
    chrome={false}
    resultCount={model.resultCount}
    hasMore={!!model.nextUrl}
    loadingMore={loadingMore}
    tableColumns={model.tableColumns}
    onOpenCopy={item => assign(item.href)}
    onLoadMore={loadMore}
    onLoadMoreGroup={loadMoreGroup}
    renderCard={item => <MediaCard model={item.card} labels={props.labels} host={host} standalone
      variant={item.card?.shape === 'square' ? 'grid-square' : 'grid'} />} />;
}

export function mountCollectionBrowser(host, props) {
  return mountRoot(host, <Adapter host={host} props={props} />);
}
