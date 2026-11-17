import React, { useEffect, useState } from 'react';
import { CheckInState, PlaybackCard } from './PlaybackCard';
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
    surfaceHover: color('--color-surface-hover', '#3f3f46'),
    surfaceMuted: color('--color-surface-muted', '#52525b'),
    border: color('--color-surface-border', '#3f3f46'),
    text: color('--color-text', '#fafafa'),
    textSecondary: color('--color-text-secondary', '#d4d4d8'),
    textMuted: color('--color-text-muted', '#a1a1aa'),
    link: color('--color-link', '#818cf8'),
    accent: color('--color-accent', '#6366f1'),
    warning: color('--color-warning', '#f59e0b'),
    danger: color('--color-danger', '#ef4444'),
    placeholder: color('--color-surface-alt', '#3f3f46'),
    progressFill: color('--color-accent', '#6366f1'),
    pausedFill: color('--color-warning', '#f59e0b'),
    focusStyle: {
      outlineStyle: 'solid',
      outlineWidth: 2,
      outlineColor: color('--color-accent', '#6366f1'),
      outlineOffset: 2,
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

function Adapter({ props }) {
  const palette = usePalette();
  const [busy, setBusy] = useState(null);

  function post(control, key) {
    if (!control?.url || !window.htmx) return;
    setBusy(key);
    window.htmx.ajax('POST', control.url, {
      target: '#active-playback-container',
      swap: 'innerHTML',
      values: {
        action: control.action,
        from_now_playing: '1',
        checkin_token: control.token,
      },
    });
    setTimeout(() => setBusy(current => current === key ? null : current), 8000);
  }

  return <PlaybackCard
    model={props.model}
    labels={props.labels}
    palette={palette}
    onOpenDetails={url => window.location.assign(url)}
    onPause={props.control?.pause ? () => post(props.control.pause, 'pause') : undefined}
    onStop={props.control?.stop ? () => post(props.control.stop, 'stop') : undefined}
    onCancel={props.control?.cancel ? () => post(props.control.cancel, 'cancel') : undefined}
    busyAction={busy} />;
}

export function PlaybackCardTheme({ props }) {
  return <Adapter props={props} />;
}

function CheckInAdapter({ host, props }) {
  const palette = usePalette();
  const [busy, setBusy] = useState(null);

  useEffect(() => {
    const legacy = host.parentElement?.querySelector('[data-track-split-legacy]');
    if (!legacy) return undefined;
    const display = legacy.style.display;
    legacy.style.display = 'none';
    return () => { legacy.style.display = display; };
  }, [host]);

  function run(action) {
    const scope = host.parentElement;
    const button = scope?.querySelector(`[data-track-action-key="${action.key}"]`);
    if (!button) return;
    setBusy(action.key);
    button.click();
    setTimeout(() => setBusy(current => current === action.key ? null : current), 4000);
  }

  return <CheckInState
    model={props.model}
    actions={props.actions}
    labels={props.labels}
    palette={palette}
    onAction={run}
    busyKey={busy} />;
}

export function CheckInStateTheme({ host, props }) {
  return <CheckInAdapter host={host} props={props} />;
}
