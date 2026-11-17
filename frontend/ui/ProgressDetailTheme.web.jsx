import React, { useEffect, useRef, useState } from 'react';
import { ProgressCard } from './ProgressCard.web';
import { ProgressDialog } from './ProgressDialog';
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

function readModel(html) {
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const root = doc.querySelector('[data-progress-breakdown-root]');
  const script = root ? doc.getElementById(root.getAttribute('data-props-id')) : null;
  if (!script) return null;
  try {
    return JSON.parse(script.textContent)?.model || null;
  } catch (_) {
    return null;
  }
}

function cardFrom(model, current) {
  const summary = model?.summary || {};
  return {
    percent: summary.percent ?? current.percent,
    caughtUp: Boolean(summary.caughtUp),
    runKey: model?.runKey || '',
    rewatch: summary.rewatch || null,
  };
}

let requestSequence = 0;

function usePalette() {
  const [palette, setPalette] = useState(readPalette);
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
  return palette;
}

function Adapter({ host, props }) {
  const palette = usePalette();
  const [card, setCard] = useState(() => props.card || {});
  const [open, setOpen] = useState(false);
  const [model, setModel] = useState(null);
  const [status, setStatus] = useState('idle');
  const [cancelling, setCancelling] = useState(false);
  const alive = useRef(true);
  const pending = useRef(new Set());

  useEffect(() => () => {
    alive.current = false;
    pending.current.forEach(handler => document.body.removeEventListener('htmx:afterRequest', handler));
    pending.current.clear();
  }, []);

  useEffect(() => {
    const fallback = host.parentElement?.querySelector(':scope > [data-progress-detail-fallback]');
    if (!fallback) return undefined;
    const display = fallback.style.display;
    fallback.style.display = 'none';
    return () => { fallback.style.display = display; };
  }, [host]);

  function ask(verb, url, values) {
    return new Promise((resolve, reject) => {
      if (!window.htmx || !url) { reject(new Error('htmx unavailable')); return; }
      const id = `progress-detail-${++requestSequence}`;
      const handler = event => {
        const config = event.detail?.requestConfig;
        if (event.detail?.elt !== host && config?.headers?.['X-Progress-Request'] !== id) return;
        document.body.removeEventListener('htmx:afterRequest', handler);
        pending.current.delete(handler);
        const xhr = event.detail.xhr;
        if (!(xhr.status >= 200 && xhr.status < 300)) { reject(new Error('request failed')); return; }
        resolve(xhr.responseText);
      };
      pending.current.add(handler);
      document.body.addEventListener('htmx:afterRequest', handler);
      try {
        window.htmx.ajax(verb, url, {
          source: host,
          target: host,
          swap: 'none',
          values,
          headers: { 'X-Progress-Request': id },
        });
      } catch (error) {
        document.body.removeEventListener('htmx:afterRequest', handler);
        pending.current.delete(handler);
        reject(error);
      }
    });
  }

  async function load() {
    setStatus('loading');
    setModel(null);
    try {
      const next = readModel(await ask('GET', props.control?.openUrl));
      if (!alive.current) return;
      if (!next) { setStatus('error'); return; }
      setModel(next);
      setCard(current => cardFrom(next, current));
      setStatus('ready');
    } catch (_) {
      if (alive.current) setStatus('error');
    }
  }

  async function cancel() {
    if (!model?.runKey) return;
    setCancelling(true);
    try {
      const next = readModel(await ask('POST', props.control?.cancelUrl, {
        media_type: props.control?.mediaType,
        instance_id: props.control?.instanceId,
        run_key: model.runKey,
      }));
      if (!alive.current) return;
      if (next) {
        setModel(next);
        setCard(current => cardFrom(next, current));
      }
    } catch (_) {
      // The server toast and the next refresh report a stale or failed run.
    } finally {
      if (alive.current) setCancelling(false);
    }
  }

  function openDialog() {
    setOpen(true);
    load();
  }

  useEffect(() => {
    const updated = event => {
      const detail = event.detail || {};
      const sameShow = Number(detail.tv_id) === Number(props.control?.tvId);
      const sameSeason = props.control?.mediaType === 'season' && Number(detail.id) === Number(props.control?.instanceId);
      if (!sameShow && !sameSeason) return;
      if (open) load();
    };
    const resolved = event => {
      const detail = event.detail || {};
      if (Number(detail.id) !== Number(props.control?.instanceId) || detail.media_type !== props.control?.mediaType) return;
      setCard(current => ({
        percent: detail.percent ?? current.percent,
        caughtUp: Boolean(detail.caught_up),
        runKey: detail.run_key || '',
        rewatch: detail.rewatch === null || detail.rewatch === undefined ? null : {
          percent: detail.rewatch,
          offset: detail.offset,
          width: detail.width,
          label: detail.label,
        },
      }));
    };
    window.addEventListener('detail-progress-updated', updated);
    window.addEventListener('rewatch-progress-resolved', resolved);
    return () => {
      window.removeEventListener('detail-progress-updated', updated);
      window.removeEventListener('rewatch-progress-resolved', resolved);
    };
  }, [open, props.control?.tvId, props.control?.instanceId, props.control?.mediaType]);

  const labels = { ...(props.labels || {}), ...(model?.labels || {}) };
  const openExternal = url => { if (url) window.location.assign(url); };

  return <>
    <ProgressCard model={card} labels={props.labels} onOpen={openDialog} />
    {open
      ? <ProgressDialog visible
        title={`${labels.yourProgress || props.labels?.yourProgress || ''} · ${props.control?.title || ''}`}
        model={model}
        labels={labels}
        palette={palette}
        status={status}
        cancelling={cancelling}
        onClose={() => setOpen(false)}
        onCancelRewatch={cancel}
        onOpenSeason={openExternal}
        onOpenEpisode={openExternal} />
      : null}
  </>;
}

// The server keeps the legacy Alpine card and modal as the fallback. React
// mounts over it and keeps the htmx fragment endpoint, cancel write, card
// out-of-band refresh and rewatch events.
export function ProgressDetailTheme({ host, props }) {
  return <Adapter host={host} props={props} />;
}
