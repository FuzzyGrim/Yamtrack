import React, { useEffect, useState } from 'react';
import { DetailRelated } from './DetailRelated';
import { Icon } from './Icon';
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
    card: color('--color-surface', '#27272a'),
    text: color('--color-text', '#fafafa'),
    textSecondary: color('--color-text-secondary', '#d4d4d8'),
    textMuted: color('--color-text-muted', '#a1a1aa'),
    border: color('--color-surface-border', '#3f3f46'),
    link: color('--color-link', '#818cf8'),
    star: color('--color-warning', '#facc15'),
    chipBackground: 'rgba(24, 24, 27, 0.92)',
    chipText: '#ffffff',
    placeholder: color('--color-surface-strong', '#3f3f46'),
    progressTrack: 'rgba(24, 24, 27, 0.6)',
    progressFill: color('--color-accent', '#6366f1'),
    progressPaused: 'rgba(99, 102, 241, 0.4)',
    rating: {
      lit: color('--color-warning', '#facc15'),
      unlit: color('--color-surface-muted', '#52525b'),
      muted: color('--color-text-muted', '#a1a1aa'),
      focusStyle: {},
    },
  };
}

const RELATED_CSS = `
  [data-detail-related-root] [data-detail-related-active="true"] {
    border-radius: 10px;
    outline: 2px solid #4338ca;
    outline-offset: 0;
  }
  [data-detail-related-root] .media-card-year {
    max-height: 0;
    margin-top: 0;
    overflow: hidden;
    opacity: 0;
    color: rgba(255, 255, 255, 0.82);
    font-weight: 400;
    text-shadow: 0 1px 2px rgba(0, 0, 0, 0.9);
    transition: max-height 0.2s ease, margin-top 0.2s ease, opacity 0.2s ease;
  }
  [data-detail-related-root] .media-card:hover .media-card-year {
    max-height: 2lh;
    margin-top: 0.25rem;
    opacity: 1;
  }
  [data-detail-related-root] .media-card .media-card-visual {
    aspect-ratio: 2 / 3;
  }
  [data-detail-related-root] .media-card .media-card-poster {
    height: auto !important;
    aspect-ratio: auto !important;
  }
  [data-detail-related-root] .media-card .media-card-body {
    position: absolute;
    left: 0;
    right: 0;
    bottom: 0;
    z-index: 10;
  }
  [data-detail-related-root] .media-card-title {
    display: block;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
    -webkit-line-clamp: unset;
    line-clamp: unset;
  }
  #detail-secondary-content [data-detail-section="recommendations"] ul {
    margin-top: 0;
    margin-bottom: 0;
  }
  #detail-secondary-content [data-detail-section="recommendations"] h3 {
    line-height: 28px;
  }
`;

function runAction(model, action) {
  if (!action) return;
  if (action.kind === 'link') {
    if (action.href) window.location.assign(action.href);
    return;
  }
  if (action.kind === 'post') {
    window.htmx?.ajax('POST', action.url, {
      target: action.target,
      swap: action.swap,
      values: action.vals,
      headers: { 'X-CSRFToken': model?.csrfToken || model?.actions?.csrfToken || '' },
    });
    return;
  }
  openModal(action);
}

function rate(model, score) {
  const url = model?.rating?.rateUrl;
  if (!url) return;
  window.htmx?.ajax('POST', url, {
    swap: 'none',
    headers: { 'X-CSRFToken': model.csrfToken || '' },
    values: { score: score ?? model.rating.value, toggle: true },
  });
}

function Adapter({ props }) {
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

  return <>
    <style>{RELATED_CSS}</style>
    <DetailRelated
      model={props.model}
      labels={props.labels}
      palette={palette}
      web
      onOpen={model => { if (model?.href) window.location.assign(model.href); }}
      onOpenLink={href => { if (href) window.location.assign(href); }}
      onOpenExternal={url => { if (url) window.open(url, '_blank', 'noopener,noreferrer'); }}
      onAction={runAction}
      onRate={rate}
      renderActionIcon={action => <Icon spec={action.icon} />} />
  </>;
}

export function DetailRelatedTheme({ host, props }) {
  return <Adapter host={host} props={props} />;
}
