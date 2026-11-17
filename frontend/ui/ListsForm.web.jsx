import React, { useEffect, useRef, useState } from 'react';
import { ListsForm as ListsFormView } from './ListsFormView';
import {
  ALL_LISTS,
  applyListToggle,
  listsMembership,
  normalizeListsModel,
} from './mediaCardForms';
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
  const token = (name, fallback) => resolveColor(css.getPropertyValue(name).trim(), fallback);
  const accent = token('--color-accent', '#6366f1');
  return {
    text: token('--color-text', '#fafafa'),
    textSecondary: token('--color-text-secondary', '#d4d4d8'),
    textMuted: token('--color-text-muted', '#a1a1aa'),
    surface: token('--color-surface', '#27272a'),
    surfaceStrong: token('--color-surface-strong', '#3f3f46'),
    border: token('--color-border', '#3f3f46'),
    danger: token('--color-danger', '#f87171'),
    accent,
    accentContrast: token('--color-accent-contrast', '#ffffff'),
    radius: 8,
    focusStyle: { outlineStyle: 'solid', outlineWidth: 2, outlineColor: accent, outlineOffset: 2 },
  };
}

function text(labels, key, name = '') {
  return String(labels[key] || '').replace('%s', name);
}

export function ListsForm({ url, vals, labels = {}, targetId, onClose }) {
  const [model, setModel] = useState(null);
  const [status, setStatus] = useState('loading');
  const [tag, setTag] = useState(ALL_LISTS);
  const [membership, setMembership] = useState({});
  const [pending, setPending] = useState({});
  const [errors, setErrors] = useState({});
  const [attempt, setAttempt] = useState(0);
  const [palette] = useState(readPalette);
  const closeButton = useRef(null);

  useEffect(() => {
    const controller = new AbortController();
    setStatus('loading');
    const query = new URLSearchParams({ ...(vals || {}), format: 'json' });
    fetch(`${url}?${query}`, {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    })
      .then(response => {
        if (!response.ok) throw new Error(String(response.status));
        return response.json();
      })
      .then(data => {
        const next = normalizeListsModel(data);
        setModel(next);
        setMembership(listsMembership(next));
        setStatus('ready');
      })
      .catch(error => {
        if (error.name !== 'AbortError') setStatus('error');
      });
    return () => controller.abort();
  }, [url, attempt]);

  useEffect(() => {
    closeButton.current?.focus?.();
  }, []);

  function retry() {
    setModel(null);
    setAttempt(value => value + 1);
  }

  async function toggle(list) {
    if (pending[list.id] || !model) return;
    setPending(current => ({ ...current, [list.id]: true }));
    setErrors(current => ({ ...current, [list.id]: '' }));
    let message = '';
    try {
      const body = new URLSearchParams({ custom_list_id: list.id, item_id: model.itemId, format: 'json' });
      const response = await fetch(model.toggleUrl, {
        method: 'POST',
        credentials: 'same-origin',
        body,
        headers: { 'X-CSRFToken': model.csrfToken, Accept: 'application/json' },
      });
      const data = await response.json().catch(() => ({}));
      if (response.ok && data.success) {
        setMembership(current => applyListToggle(current, list.id, data.hasItem));
        window.dispatchEvent(new CustomEvent('floppy-list-toggled', { detail: { listId: list.id, hasItem: data.hasItem } }));
      } else {
        message = data.error || text(labels, 'toggleFailed');
      }
    } catch {
      message = text(labels, 'toggleFailed');
    }
    setPending(current => ({ ...current, [list.id]: false }));
    setErrors(current => ({ ...current, [list.id]: message }));
  }

  return <ListsFormView
    model={model}
    membership={membership}
    tag={tag}
    status={status}
    pending={pending}
    errors={errors}
    labels={labels}
    palette={palette}
    onTagChange={setTag}
    onToggle={toggle}
    onRetry={retry}
    onClose={onClose}
    closeRef={closeButton}
    nativeID={targetId}
  />;
}
