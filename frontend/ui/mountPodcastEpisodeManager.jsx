import React, { useState } from 'react';
import { mountRoot } from '../settings/root';
import { PodcastEpisodeManager } from './PodcastEpisodeManager';
import { rootStyle } from './rootStyle.web';

function readPalette() {
  const css = rootStyle();
  const token = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
  return {
    surface: token('--color-surface', '#27272a'),
    surfaceStrong: token('--color-surface-strong', '#3f3f46'),
    surfaceMuted: token('--color-surface-muted', '#52525b'),
    border: token('--color-surface-border', '#3f3f46'),
    text: token('--color-text', '#fafafa'),
    textSecondary: token('--color-text-secondary', '#d4d4d8'),
    textMuted: token('--color-text-muted', '#a1a1aa'),
    accent: token('--color-accent', '#6366f1'),
    accentContrast: token('--color-accent-contrast', '#ffffff'),
    danger: token('--color-danger', '#f87171'),
    radius: 8,
    focusStyle: {},
  };
}

async function readJson(response) {
  try {
    return await response.json();
  } catch {
    return {};
  }
}

function formBody(values) {
  const body = new FormData();
  Object.entries(values).forEach(([key, value]) => {
    body.append(key, value ?? '');
  });
  body.append('format', 'json');
  return body;
}

function ManagerAdapter({ props }) {
  const [palette] = useState(readPalette);
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState('idle');
  const [episodes, setEpisodes] = useState([]);
  const [loadError, setLoadError] = useState('');

  async function load() {
    setStatus('loading');
    setLoadError('');
    try {
      const response = await fetch(props.listUrl, {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) throw new Error(String(response.status));
      const data = await readJson(response);
      setEpisodes(Array.isArray(data.episodes) ? data.episodes : []);
      setStatus('ready');
    } catch {
      setLoadError(props.labels?.loadFailed || '');
      setStatus('error');
    }
  }

  function openManager() {
    setOpen(true);
    if (status !== 'ready' && status !== 'loading') load();
  }

  async function save(values, episode) {
    try {
      const response = await fetch(episode?.editUrl || props.addUrl, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'X-CSRFToken': props.csrfToken || '', Accept: 'application/json' },
        body: formBody(values),
      });
      const data = await readJson(response);
      if (response.ok && data.ok) {
        window.location.reload();
        return { ok: true };
      }
      return {
        ok: false,
        errors: data.errors || {},
        error: props.labels?.saveFailed || '',
      };
    } catch {
      return { ok: false, error: props.labels?.saveFailed || '' };
    }
  }

  async function remove(episode) {
    try {
      const body = new FormData();
      body.append('format', 'json');
      const response = await fetch(episode.deleteUrl, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'X-CSRFToken': props.csrfToken || '', Accept: 'application/json' },
        body,
      });
      const data = await readJson(response);
      if (response.ok && data.ok) {
        window.location.reload();
        return { ok: true };
      }
      return { ok: false, error: props.labels?.deleteFailed || '' };
    } catch {
      return { ok: false, error: props.labels?.deleteFailed || '' };
    }
  }

  return <PodcastEpisodeManager
    open={open}
    status={status}
    episodes={episodes}
    loadError={loadError}
    labels={props.labels}
    palette={palette}
    onOpen={openManager}
    onClose={() => setOpen(false)}
    onRetry={load}
    onSave={save}
    onDelete={remove}
  />;
}

export function mountPodcastEpisodeManager(host, props) {
  return mountRoot(host, <ManagerAdapter props={props} />);
}
