import React, { useEffect, useState } from 'react';
import { ProgressBreakdown } from './ProgressBreakdown';
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
    text: color('--color-text', '#fafafa'),
    textSecondary: color('--color-text-secondary', '#d4d4d8'),
    textMuted: color('--color-text-muted', '#a1a1aa'),
    surface: color('--color-surface', '#27272a'),
    surfaceStrong: color('--color-surface-strong', '#3f3f46'),
    surfaceBorder: color('--color-surface-border', '#3f3f46'),
    surfaceMuted: color('--color-surface-muted', '#52525b'),
    surfaceHover: color('--color-surface-hover', '#3f3f46'),
    fill: '#10b981',
    glow: '#a7f3d0',
    glowShadow: '#6ee7b7',
    watchedBorder: 'rgba(16, 185, 129, 0.4)',
    watchedBg: 'rgba(16, 185, 129, 0.1)',
    watchedText: '#10b981',
    upcomingBorder: color('--color-surface-border', '#3f3f46'),
    droppedBackground: color('--color-surface-muted', '#52525b'),
    droppedText: color('--color-text-muted', '#a1a1aa'),
    focusStyle: {
      outlineStyle: 'solid',
      outlineWidth: 2,
      outlineColor: color('--color-accent', '#6366f1'),
      outlineOffset: 2,
    },
  };
}

function Adapter({ host, props }) {
  const [palette, setPalette] = useState(readPalette);

  useEffect(() => {
    const fallback = host.parentElement?.querySelector(':scope > [data-progress-breakdown-fallback]');
    if (!fallback) return undefined;
    const display = fallback.style.display;
    fallback.style.display = 'none';
    return () => { fallback.style.display = display; };
  }, [host]);

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

  function open(url) {
    if (url) window.location.assign(url);
  }

  return <ProgressBreakdown model={props.model}
    palette={palette}
    onOpenSeason={open}
    onOpenEpisode={open} />;
}

// The server keeps the legacy Alpine breakdown as the fallback. React replaces
// it on mount while htmx still owns writes and out-of-band card refreshes.
export function ProgressBreakdownTheme({ host, props }) {
  return <Adapter host={host} props={props} />;
}
