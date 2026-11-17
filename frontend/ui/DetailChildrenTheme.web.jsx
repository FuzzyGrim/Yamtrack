import React, { useEffect, useState } from 'react';
import { DetailChildren } from './DetailChildren';
import { RatingStarIcon } from './RatingStarIcon.web';
import { RatingStars } from './RatingStars';
import { openModal } from './ModalHost';
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
    surface: color('--color-surface', '#27272a'),
    surfaceStrong: color('--color-surface-strong', '#3f3f46'),
    surfaceMuted: color('--color-surface-muted', '#52525b'),
    surfaceBorder: color('--color-surface-border', '#3f3f46'),
    text: color('--color-text', '#fafafa'),
    textSecondary: color('--color-text-secondary', '#d4d4d8'),
    textMuted: color('--color-text-muted', '#a1a1aa'),
    accent: color('--color-accent', '#6366f1'),
    accentStrong: color('--color-accent-strong', '#4f46e5'),
    accentContrast: '#ffffff',
    link: color('--color-link', '#818cf8'),
    danger: color('--color-danger', '#f87171'),
    success: color('--color-success', '#10b981'),
    warning: color('--color-warning', '#f59e0b'),
    rating: {
      lit: color('--color-warning', '#facc15'),
      unlit: color('--color-surface-muted', '#52525b'),
      muted: color('--color-text-muted', '#a1a1aa'),
      focusStyle: {},
    },
  };
}

function usePalette() {
  const [palette, setPalette] = useState(readPalette);
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
  return palette;
}

function legacyRow(legacy, item) {
  if (!legacy) return null;
  return [...legacy.querySelectorAll('[data-detail-child-key]')]
    .find(row => row.dataset.detailChildKey === String(item?.key)) || null;
}

function clickAction(legacy, item, key) {
  const row = legacyRow(legacy, item);
  const button = row?.querySelector(`[data-children-action="${key}"]`);
  if (!button) return false;
  if (key === 'track') {
    // The fallback row is hidden once React owns it. Its non-teleported
    // comic popup cannot become visible through that hidden ancestor, so
    // reuse the shared web modal host with a distinct target instead.
    const url = button.getAttribute('hx-get');
    const target = button.getAttribute('hx-target');
    if (url && target) {
      let vals;
      try { vals = JSON.parse(button.getAttribute('hx-vals') || '{}'); } catch { vals = {}; }
      openModal({ modal: 'track', url, target: `${target}-react-child`, vals });
      return true;
    }
  }
  button.click();
  return true;
}

function Adapter({ host, props }) {
  const palette = usePalette();
  const [model, setModel] = useState(props.model);
  const [listView, setListView] = useState(() => {
    try {
      return window.localStorage.getItem('ep-layout') === 'list';
    } catch {
      return false;
    }
  });
  const legacy = host.querySelector('[data-detail-children-legacy]');
  const labels = props.labels || {};

  useEffect(() => {
    setModel(props.model);
  }, [props.model]);

  useEffect(() => {
    if (!legacy) return undefined;
    legacy.style.display = listView ? '' : 'none';
    return () => { legacy.style.display = ''; };
  }, [legacy, listView]);

  useEffect(() => {
    const onClick = event => {
      const button = event.target?.closest?.('[data-children-layout]');
      if (button) setListView(button.dataset.childrenLayout === 'list');
    };
    document.addEventListener('click', onClick);
    return () => document.removeEventListener('click', onClick);
  }, []);

  function openChild(item) {
    if (item?.href) window.location.assign(item.href);
  }

  function openHistory(item) {
    clickAction(legacy, item, 'history');
  }

  function act(key, item) {
    if (key === 'history') {
      openHistory(item);
      return;
    }
    clickAction(legacy, item, key);
  }

  function rate(item, score) {
    const url = item?.rateUrl;
    if (!url) return;
    const applyScore = next => setModel(current => ({
      ...current,
      issues: (current.issues || []).map(issue =>
        issue.key === item.key ? { ...issue, score: next } : issue),
      episodes: (current.episodes || []).map(episode =>
        episode.key === item.key ? { ...episode, score: next } : episode),
    }));
    applyScore(score == null ? null : score);
    window.fetch(url, {
      method: 'POST',
      headers: {
        'X-CSRFToken': props.csrfToken || '',
        'Content-Type': 'application/x-www-form-urlencoded',
      },
      body: new URLSearchParams({ score: score == null ? '' : String(score), toggle: 'true' }),
    }).then(response => response.json())
      .then(data => applyScore(data?.score == null ? null : data.score))
      .catch(() => {});
  }

  function renderRating({ value, label, scaleMax, palette: ratingPalette, onSelect }) {
    return <RatingStars scaleMax={scaleMax} value={value} label={label}
      palette={ratingPalette} onSelect={onSelect}
      renderStar={lit => RatingStarIcon(lit)} />;
  }

  if (listView) return null;
  return <DetailChildren model={model} labels={labels} palette={palette}
    variant="detail" showHeading={false}
    onOpenChild={openChild} onOpenHistory={openHistory}
    onRate={rate} onAction={act} renderRating={renderRating} />;
}

export function DetailChildrenTheme({ host, props }) {
  return <Adapter host={host} props={props} />;
}
