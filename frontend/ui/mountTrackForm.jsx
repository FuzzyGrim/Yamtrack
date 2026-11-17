import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { View } from 'react-native';
import { mountRoot } from '../settings/root';
import { applyPreviewRefresh, readTags } from './mountTags';
import { TagAssignmentDialog } from './TagManagement';
import { TrackDialog } from './TrackDialog';
import { initialTrackValues, normalizeTrackForm } from './mediaCardForms';
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
  const palette = {
    background: color('--color-page-bg', '#18181b'),
    text: color('--color-text', '#fafafa'),
    textSecondary: color('--color-text-secondary', '#d4d4d8'),
    textMuted: color('--color-text-muted', '#a1a1aa'),
    surface: color('--color-surface', '#27272a'),
    surfaceDim: color('--color-surface-dim', '#262a2f'),
    tabActive: color('--color-border', '#2c3136'),
    hoverDim: color('--color-hover-dim', '#23272b'),
    inputBorder: '#4b5563',
    surfaceStrong: color('--color-surface-strong', '#3f3f46'),
    surfaceMuted: color('--color-surface-muted', '#52525b'),
    border: color('--color-surface-border', '#3f3f46'),
    accent: color('--color-accent', '#6366f1'),
    accentStrong: color('--color-accent-strong', '#4f46e5'),
    accentContrast: '#ffffff',
    link: color('--color-link', '#818cf8'),
    danger: color('--color-danger', '#ef4444'),
    success: color('--color-status-clean', '#10b981'),
    warning: color('--color-warning-text', '#f59e0b'),
    overlay: 'rgba(0, 0, 0, 0.55)',
    radius: 8,
    radiusSmall: 6,
    focusStyle: {},
  };
  palette.dateTime = {
    ...palette,
    radius: 8,
    radiusSmall: 6,
  };
  palette.rating = {
    lit: '#f59e0b',
    unlit: palette.surfaceMuted,
    muted: palette.textMuted,
    focusStyle: {},
  };
  return palette;
}

function body(values) {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      params.append(key, value);
    }
  });
  return params;
}

function ServerTabBody({ host, selector }) {
  const ref = useRef(null);
  useLayoutEffect(() => {
    const root = host.closest('[data-track-popup]') || host.parentElement;
    const section = root?.querySelector(`[data-track-tab-section="${selector}"]`);
    if (!section || !ref.current) return undefined;
    const parent = section.parentElement;
    const next = section.nextSibling;
    const hidden = section.style.display;
    const show = section.getAttribute('x-show');
    const cloak = section.hasAttribute('x-cloak');
    section.removeAttribute('x-show');
    section.removeAttribute('x-cloak');
    section.style.display = '';
    ref.current.appendChild(section);
    return () => {
      if (!parent?.isConnected) return;
      if (show !== null) section.setAttribute('x-show', show);
      if (cloak) section.setAttribute('x-cloak', '');
      section.style.display = hidden;
      parent.insertBefore(section, next?.isConnected ? next : null);
    };
  }, [host, selector]);
  return <View ref={ref} style={{ width: '100%' }} />;
}

function TagsTab({ tab, itemTitle, palette, labels, onChange }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function post(url, values) {
    const response = await window.fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'X-CSRFToken': tab.csrfToken || '' },
      body: body({
        item_id: tab.itemId,
        tag_surface: 'track',
        preview_genres_json: tab.previewGenresJson,
        preview_implied_genres_json: tab.previewImpliedGenresJson,
        ...values,
      }),
    });
    if (!response.ok) throw new Error('Request failed');
    const parsed = applyPreviewRefresh(await response.text());
    return { tags: readTags(parsed), parsed };
  }

  function change(next) {
    onChange(next);
  }

  function toggle(tag) {
    if (busy) return;
    setBusy(true);
    setError('');
    post(tab.urls.toggle, { tag_id: tag.id }).then(() => {
      change(tab.tags.map(entry => entry.id === tag.id
        ? { ...entry, hasTag: !entry.hasTag }
        : entry));
    }).catch(() => {
      setError(labels.tagFailed || "Couldn't update this tag.");
    }).finally(() => setBusy(false));
  }

  function create(name) {
    if (busy) return false;
    setBusy(true);
    setError('');
    post(tab.urls.create, { name }).then(({ tags }) => {
      if (!tags.some(tag => tag.name.toLowerCase() === name.toLowerCase())) {
        setError(`Tag "${name}" already exists.`);
        return;
      }
      change(tags);
    }).catch(() => {
      setError(labels.tagFailed || "Couldn't update this tag.");
    }).finally(() => setBusy(false));
    return true;
  }

  function remove(tag) {
    if (busy) return;
    setBusy(true);
    setError('');
    post(tab.urls.delete, { tag_id: tag.id }).then(({ tags }) => {
      change(tags);
    }).catch(() => {
      setError(labels.tagFailed || "Couldn't update this tag.");
    }).finally(() => setBusy(false));
  }

  return <TagAssignmentDialog
    open
    embedded
    plain
    showClose={false}
    item={{ id: tab.itemId, title: itemTitle }}
    tags={tab.tags}
    palette={palette}
    busy={busy}
    error={error}
    onToggle={toggle}
    onCreate={create}
    onDelete={remove} />;
}

// The popup's open state is the Alpine `x-show` overlay around the React root. The server
// modal root inside it is hidden on purpose once React takes over, so it can't be observed.
function findPopup(host) {
  return host.closest('[data-track-popup]')?.closest('[x-show]')
    || host.closest('[data-track-modal-root]')
    || null;
}

function useLegacyPopupOpen(host) {
  // The server wrapper owns the popup's open state through Alpine (`x-show`).
  // React renders the dialog in a portal, so without mirroring that state the
  // dialog escapes the hidden wrapper and covers the page from first paint.
  const popup = findPopup(host);
  const [open, setOpen] = useState(() => !popup || getComputedStyle(popup).display !== 'none');
  useLayoutEffect(() => {
    if (!popup) return undefined;
    const sync = () => setOpen(getComputedStyle(popup).display !== 'none');
    sync();
    const observer = new MutationObserver(sync);
    observer.observe(popup, { attributes: true, attributeFilter: ['style', 'class', 'hidden'] });
    return () => observer.disconnect();
  }, [popup]);
  const [flagOpen, setFlagOpen] = useState(true);
  useLayoutEffect(() => {
    // The card's Alpine flag is the record of open or closed. Episode tracking
    // swaps this host into the card with no modal root above it, and a save
    // swaps the popup out of the page, which a style observer can no longer see.
    return window.watchTrackModalOpen?.(host, setFlagOpen) || undefined;
  }, [host]);
  return open && flagOpen;
}

function Adapter({ host, props }) {
  const model = useMemo(() => normalizeTrackForm(props?.model || props || {}), [props]);
  const initialValues = useMemo(() => initialTrackValues(model), [model]);
  const [values, setValues] = useState(initialValues);
  const [tabs, setTabs] = useState(() => props?.tabs || model.tabs || []);
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState('');
  const [palette, setPalette] = useState(readPalette);
  const popupOpen = useLegacyPopupOpen(host);
  const startCleared = useRef(false);

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

  const legacyForm = () => document.getElementById(model.formId);

  useEffect(() => {
    const nodes = [legacyForm(), ...(model.legacyIds || []).map(id => document.getElementById(id))]
      .filter(Boolean);
    const previous = nodes.map(node => node.style.display);
    nodes.forEach(node => { node.style.display = 'none'; });
    return () => { nodes.forEach((node, index) => { node.style.display = previous[index]; }); };
  }, [model.formId, model.legacyIds]);

  useEffect(() => {
    const root = host.closest('[data-track-popup]') || host.parentElement;
    const fallback = root?.querySelector('[data-track-fallback]');
    if (!fallback) return undefined;
    const previous = fallback.style.display;
    const background = root.style.backgroundColor;
    const pointerEvents = root.style.pointerEvents;
    fallback.style.display = 'none';
    if (model.variant === 'song') {
      root.style.backgroundColor = 'transparent';
      root.style.pointerEvents = 'none';
    }
    return () => {
      fallback.style.display = previous;
      root.style.backgroundColor = background;
      root.style.pointerEvents = pointerEvents;
    };
  }, [host, model.variant]);

  function change(name, value) {
    setValues(current => ({ ...current, [name]: value }));
    if (name === 'start_date') {
      startCleared.current = !value && Boolean(values.start_date);
      if (value) startCleared.current = false;
    }
  }

  function send(url, extra = {}, submitted = values) {
    const ajax = window.htmx?.ajax;
    if (!ajax || !url) return;
    const payload = {
      ...(model.hidden || {}),
      ...submitted,
      csrfmiddlewaretoken: model.csrfToken || '',
      ...extra,
    };
    if (startCleared.current && 'start_date_cleared' in payload) {
      payload.start_date_cleared = '1';
    }
    const target = (model.target && document.querySelector(model.target))
      || legacyForm()
      || host;
    setBusy(true);
    setServerError('');
    const request = ajax('POST', url, {
      values: payload,
      target,
      swap: model.swap || 'none',
    });
    if (request?.finally) request.finally(() => setBusy(false));
    else setBusy(false);
  }

  function updateTagTab(nextTags) {
    setTabs(current => current.map(tab => tab.id === 'tags'
      ? { ...tab, tags: nextTags }
      : tab));
  }

  function toggleDiscover(hidden, itemId) {
    const tab = tabs.find(entry => entry.id === 'discover');
    const ajax = window.htmx?.ajax;
    if (!tab || !ajax) return Promise.reject(new Error('Discover is unavailable.'));
    const root = host.closest('[data-track-popup]') || host.parentElement;
    const section = root?.querySelector('[data-track-tab-section="discover"]');
    const target = section?.querySelector('#discover-tab-content') || section;
    return Promise.resolve(ajax('POST', tab.url, {
      values: { item_id: itemId, action: hidden ? 'hide' : 'unhide' },
      target,
      swap: 'outerHTML',
    })).then(() => {
      setTabs(current => current.map(entry => entry.id === 'discover'
        ? { ...entry, hidden }
        : entry));
    });
  }

  const labels = model.labels || {};
  const decorated = tabs.map(tab => {
    if (tab.id === 'tags') {
      return {
        ...tab,
        content: <TagsTab key="tags" tab={tab} itemTitle={model.title} palette={palette}
          labels={labels} onChange={updateTagTab} />,
      };
    }
    if (tab.kind === 'server') {
      return {
        ...tab,
        content: <ServerTabBody key={tab.id} host={host} selector={tab.id} />,
      };
    }
    return tab;
  });

  if (!popupOpen) return null;

  return <TrackDialog
    open
    maxWidth={model.variant === 'song' ? 400 : 1152}
    showHeader
    model={model}
    values={values}
    errors={props?.errors || {}}
    status="ready"
    serverError={serverError}
    busy={busy}
    labels={labels}
    palette={palette}
    tabs={decorated}
    generalPrefix={model.variant === 'song' ? <ServerTabBody host={host} selector="song-details" /> : null}
    onChange={change}
    onSubmit={next => send(model.action, {}, next)}
    onDelete={next => send(model.deleteAction, {}, next)}
    onRewatch={(action, next) => send(model.repeatIdentity?.rewatchUrl, {}, next)}
    onSaveAsNew={next => send(model.action, { save_as_new_entry: '1' }, next)}
    onDiscoverToggle={toggleDiscover}
    onSecondaryAction={(action, next) => send(action.url, action.body || {}, next)}
    onRetry={() => setServerError('')}
    onClose={() => window.closeTrackModal?.(findPopup(host) || legacyForm() || host)} />;
}

export function mountTrackForm(host, props) {
  if (!window.htmx?.ajax) return () => {};
  return mountRoot(host, <Adapter host={host} props={props} />);
}
