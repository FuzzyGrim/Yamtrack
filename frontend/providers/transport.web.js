// Browser transport for the universal provider views. The universal components
// never touch the DOM, so this adapter owns POSTs, redirects, clipboard access
// and confirmations. Native screens supply their own transport instead.

import { rootStyle } from '../ui/rootStyle.web';

function appendValues(form, values) {
  Object.entries(values || {}).forEach(([name, value]) => {
    if (value === undefined || value === null) return;
    if (typeof value === 'boolean') {
      if (!value) return;
      const input = document.createElement('input');
      input.type = 'hidden';
      input.name = name;
      input.value = 'on';
      form.appendChild(input);
      return;
    }
    const list = Array.isArray(value) || (typeof value === 'object' && typeof value.length === 'number')
      ? Array.from(value)
      : [value];
    list.forEach(entry => {
      if (entry === undefined || entry === null) return;
      if (typeof File !== 'undefined' && entry instanceof File) {
        const input = document.createElement('input');
        input.type = 'file';
        input.name = name;
        const transfer = new DataTransfer();
        transfer.items.add(entry);
        input.files = transfer.files;
        form.appendChild(input);
        return;
      }
      const input = document.createElement('input');
      input.type = 'hidden';
      input.name = name;
      input.value = String(entry);
      form.appendChild(input);
    });
  });
}

/**
 * Post the collected universal-form values through a real browser form. This
 * keeps the server's redirect, flash message and full-page refresh behavior,
 * and it is the only place provider credentials touch a POST body.
 */
export function submitNativeForm({ action, csrf, returnTo, values, encType }) {
  if (!action) throw new Error('This action is not configured.');
  const form = document.createElement('form');
  form.method = 'post';
  form.action = action;
  form.style.display = 'none';
  if (encType) form.enctype = encType;
  if (csrf) {
    const token = document.createElement('input');
    token.type = 'hidden';
    token.name = 'csrfmiddlewaretoken';
    token.value = csrf;
    form.appendChild(token);
  }
  if (returnTo) {
    const next = document.createElement('input');
    next.type = 'hidden';
    next.name = 'next';
    next.value = returnTo;
    form.appendChild(next);
  }
  appendValues(form, values);
  document.body.appendChild(form);
  form.submit();
  // Navigation takes over; keep the caller's pending state until it does.
  return new Promise(() => {});
}

/** Save import preferences the way the legacy card did: 204, no navigation. */
export async function savePreferenceValues({ url, csrf, values }) {
  if (!url) throw new Error('This action is not configured.');
  const body = new URLSearchParams({
    import_frequency: values.frequency || '',
    import_time: values.time || '',
    import_mode: values.mode || '',
    watchlist_status: values.watchlistStatus || '',
    watchlist_tag: values.watchlistTag || '',
    csrfmiddlewaretoken: csrf || '',
  });
  if (values.clzWishlistStatus !== undefined) body.set('clz_wishlist_status', values.clzWishlistStatus || '');
  if (values.clzWishlistTag !== undefined) body.set('clz_wishlist_tag', values.clzWishlistTag || '');
  const response = await fetch(url, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'X-CSRFToken': csrf || '', Accept: 'application/json' },
    body,
  });
  if (response.status !== 204 && !response.ok) throw new Error('Could not save preferences. Try again.');
}

export async function copyText(text) {
  if (!text) return;
  if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
  await navigator.clipboard.writeText(text);
}

export function confirmAction(message) {
  return window.confirm(message);
}

/** Browser-only picker, invoked from the active user gesture. */
export function pickBrowserFiles({ accept = '', multiple = false } = {}) {
  return new Promise(resolve => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = accept;
    input.multiple = Boolean(multiple);
    input.setAttribute('aria-hidden', 'true');
    Object.assign(input.style, { position: 'fixed', left: '-9999px', width: '1px', height: '1px' });
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      const files = Array.from(input.files || []);
      input.remove();
      resolve({ files });
    };
    input.addEventListener('change', finish, { once: true });
    input.addEventListener('cancel', finish, { once: true });
    document.body.appendChild(input);
    input.click();
  });
}

/** Read the page's theme tokens so universal components follow the browser theme. */
export function readThemePalette() {
  const css = rootStyle();
  const token = (name, fallback) => (css.getPropertyValue(name) || '').trim() || fallback;
  return {
    background: token('--color-surface', '#27272a'),
    surface: token('--color-surface', '#27272a'),
    surfaceStrong: token('--color-surface-strong', '#3f3f46'),
    surfaceMuted: token('--color-surface-muted', '#52525b'),
    border: token('--color-surface-border', '#3f3f46'),
    text: token('--color-text', '#fafafa'),
    textSecondary: token('--color-text-secondary', '#d4d4d8'),
    textMuted: token('--color-text-muted', '#a1a1aa'),
    link: token('--color-link', '#818cf8'),
    accent: token('--color-accent', '#6366f1'),
    accentStrong: token('--color-accent', '#4f46e5'),
    accentText: token('--color-accent-contrast', '#ffffff'),
    danger: token('--color-error-text', '#ef4444'),
    success: token('--color-success-text', '#10b981'),
    warning: token('--color-warning-text', '#f59e0b'),
    focus: token('--color-accent', '#6366f1'),
    infoBadgeText: token('--color-info-text', '#c7d2fe'),
    // Chips and status badges read these; without them a neutral badge drew a black border.
    badgeBorder: token('--color-surface-border', '#52525b'),
    badgeBackground: token('--color-surface-strong', '#3f3f46'),
    badgeText: token('--color-text-secondary', '#d4d4d8'),
    badgeHover: token('--color-surface-muted', '#52525b'),
    radius: 'var(--theme-radius, 18px)',
    focusStyle: {},
  };
}

/** Call the app's own JSON API with the signed-in session, as the device-code link flow does. */
export async function linkRequest({ url, method = 'POST', csrf }) {
  const response = await fetch(url, {
    method,
    credentials: 'same-origin',
    headers: { 'X-CSRFToken': csrf || '', Accept: 'application/json', 'Content-Type': 'application/json' },
    body: method === 'GET' ? undefined : '{}',
  });
  let body = {};
  try { body = await response.json(); } catch { /* an empty reply is handled below */ }
  if (!response.ok) throw new Error(body.detail || body.error || 'Something went wrong. Try again.');
  return body;
}

export function openExternal(url) {
  if (url) window.open(url, '_blank', 'noopener,noreferrer');
}
