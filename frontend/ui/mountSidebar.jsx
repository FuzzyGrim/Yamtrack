import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { View } from 'react-native';
import { mountRoot } from '../settings/root';
import { Icon } from './Icon';
import { Sidebar } from './Sidebar';

// Browser integration for the shared sidebar: theme tokens, HTMX boost, the
// localStorage pin/view keys, Sortable drag, navigation forms and the mobile
// open state.

const canvas = document.createElement('canvas');
canvas.width = canvas.height = 1;
const context = canvas.getContext('2d');

function resolveColor(value, fallback) {
  if (!value) return fallback;
  if (/^(?:#|rgba?\()/i.test(value.trim())) return value.trim();
  if (!context) return fallback;
  context.clearRect(0, 0, 1, 1);
  context.fillStyle = fallback;
  context.fillStyle = value;
  context.fillRect(0, 0, 1, 1);
  const [red, green, blue, alpha] = context.getImageData(0, 0, 1, 1).data;
  return `rgba(${red}, ${green}, ${blue}, ${alpha / 255})`;
}

function readPalette() {
  const css = getComputedStyle(document.documentElement);
  const token = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
  const color = (name, fallback) => resolveColor(token(name, fallback), fallback);
  const accent = color('--color-accent', '#6366f1');
  const radius = Number.parseFloat(css.getPropertyValue('--theme-radius')) || 6;
  return {
    text: color('--color-text', '#fafafa'),
    textSecondary: color('--color-text-secondary', '#d1d5db'),
    textMuted: color('--color-text-muted', '#adb5bd'),
    surface: color('--color-surface', '#2a2f35'),
    surfaceStrong: color('--color-surface-strong', '#343a40'),
    surfaceMuted: color('--color-surface-muted', '#343a40'),
    surfaceHover: color('--color-surface-hover', '#3f3f46'),
    hoverDim: color('--color-hover-dim', 'rgba(255, 255, 255, 0.05)'),
    border: color('--color-border', '#343a40'),
    link: color('--color-link', '#818cf8'),
    danger: color('--color-danger', '#ef4444'),
    sidebar: color('--color-sidebar', '#212529'),
    overlay: 'rgba(0, 0, 0, 0.5)',
    focusStyle: { outlineStyle: 'solid', outlineWidth: 2, outlineColor: accent, outlineOffset: 2 },
    radius,
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

function readJson(key, fallback) {
  try {
    const value = JSON.parse(localStorage.getItem(key) || 'null');
    return value ?? fallback;
  } catch { return fallback; }
}
const writeJson = (key, value) => {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* Works without storage. */ }
};
const readOpen = key => {
  try { return localStorage.getItem(`floppy:saved-views-open:${key}`); } catch { return null; }
};
const writeOpen = (key, open) => {
  try { localStorage.setItem(`floppy:saved-views-open:${key}`, open ? '1' : '0'); } catch { /* Same. */ }
};
const format = (template, name) => (template || '').replace('%(name)s', name);

function submitForm(action, fields) {
  if (!action) return;
  const form = document.createElement('form');
  form.method = 'post';
  form.action = action;
  for (const [name, value] of Object.entries(fields)) {
    if (value == null) continue;
    const input = document.createElement('input');
    input.type = 'hidden';
    input.name = name;
    input.value = value;
    form.appendChild(input);
  }
  document.body.appendChild(form);
  form.submit();
}

function reorderViews(url, csrfToken, mediaType, ids) {
  if (!url) return;
  const body = new URLSearchParams();
  body.append('media_type', mediaType);
  ids.forEach(id => body.append('ids', id));
  fetch(url, { method: 'POST', headers: { 'X-CSRFToken': csrfToken }, body })
    .catch(() => { /* The server keeps the previous order. */ });
}

function enableSortable(container, onMove) {
  if (!window.Sortable || !container?.nodeType) return undefined;
  const sortable = window.Sortable.create(container, {
    animation: 150,
    draggable: '[data-testid^="saved-view-row-"]',
    handle: '[data-testid="saved-view-handle"]',
    onEnd: event => {
      const { item: row, from, oldIndex, newIndex } = event;
      if (oldIndex === newIndex) return;
      from.removeChild(row);
      from.insertBefore(row, from.children[oldIndex] || null);
      onMove(oldIndex, newIndex);
    },
  });
  return () => sortable.destroy();
}

function MobileState({ host }) {
  useEffect(() => {
    const aside = host.closest?.('aside');
    if (!aside) return undefined;
    const apply = open => {
      aside.style.transform = open ? 'translateX(0)' : '';
      aside.style.visibility = open ? 'visible' : '';
      aside.dataset.floppyMobileOpen = open ? 'true' : 'false';
    };
    const onMenu = event => apply(Boolean(event.detail?.open));
    window.addEventListener('floppy:mobile-menu', onMenu);
    return () => window.removeEventListener('floppy:mobile-menu', onMenu);
  }, [host]);
  return null;
}

function HtmxBridge({ host }) {
  useEffect(() => {
    host.setAttribute('hx-boost', 'true');
    const process = () => window.htmx?.process?.(host);
    process();
    const observer = new MutationObserver(process);
    observer.observe(host, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [host]);
  return null;
}

function useFinePointer() {
  const [fine, setFine] = useState(() => window.matchMedia('(pointer: fine)').matches);
  useEffect(() => {
    const query = window.matchMedia('(pointer: fine)');
    const update = () => setFine(query.matches);
    query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, []);
  return fine;
}

function readPins() {
  if (window.floppySidebarPins) return window.floppySidebarPins;
  try {
    const data = JSON.parse(document.getElementById('sidebar-pins-data')?.textContent || '{}');
    return { nav: data?.nav || [], lists: data?.lists || [] };
  } catch { return { nav: [], lists: [] }; }
}

// Lets sidebar.css lift the row-options layer above the phone drawer.
const MENU_LAYER_PROPS = { dataSet: { menuDropdown: '1' } };

function SidebarView({ host, props }) {
  const palette = usePalette();
  const finePointer = useFinePointer();
  const [pins, setPins] = useState(readPins);
  const pinned = pins.nav.filter(key => props.pinKeys.includes(key));
  const rawLists = pins.lists;
  const [pathname] = useState(() => window.location.pathname);

  useEffect(() => {
    const onPins = () => setPins(readPins());
    window.addEventListener('sidebar-pins-changed', onPins);
    return () => window.removeEventListener('sidebar-pins-changed', onPins);
  }, []);

  const savePins = useCallback(next => {
    window.floppySidebarPins = next;
    setPins(next);
    window.dispatchEvent(new Event('sidebar-pins-changed'));
    fetch(props.pinsUrl, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': props.csrfToken },
      body: JSON.stringify(next),
    }).catch(() => { /* The pins still apply to this page. */ });
  }, [props.pinsUrl, props.csrfToken]);

  const togglePin = useCallback(key => {
    const nav = pinned.includes(key) ? pinned.filter(entry => entry !== key) : [...pinned, key];
    savePins({ nav, lists: rawLists });
  }, [pinned, rawLists, savePins]);

  const unpinList = useCallback(id => {
    savePins({ nav: pins.nav, lists: rawLists.filter(entry => entry.id !== id) });
  }, [pins.nav, rawLists, savePins]);

  const decoratedLists = useMemo(() => rawLists.map(entry => ({
    ...entry,
    platformProps: { href: entry.url },
  })), [rawLists]);

  const pinModel = useMemo(() => ({
    pinned,
    pinnedLists: decoratedLists,
    isPinned: key => pinned.includes(key),
    toggle: togglePin,
    unpinList,
  }), [pinned, decoratedLists, togglePin, unpinList]);

  const model = useMemo(() => {
    const decorate = item => ({
      ...item,
      open: item.type === 'group'
        ? item.views.some(view => view.current) || readOpen(item.key) === '1'
        : item.type === 'lists'
          ? readOpen('lists') === null
            ? rawLists.length > 0
            : readOpen('lists') === '1' || rawLists.some(entry => entry.url === pathname)
          : undefined,
      platformProps: item.href ? { href: item.href } : undefined,
      views: item.views?.map(view => ({ ...view, platformProps: { href: view.href } })),
    });
    return {
      ...props,
      primary: props.primary.map(decorate),
      secondary: props.secondary.map(decorate),
    };
  }, [props, rawLists, pathname]);

  const renderIcon = useCallback(spec => <Icon spec={spec} />, []);

  const onDeleteView = useCallback(view => {
    if (!window.confirm(format(props.labels.confirmDelete, view.name))) return;
    submitForm(view.deleteUrl, {
      csrfmiddlewaretoken: props.csrfToken,
      next: props.currentUrl,
    });
  }, [props]);

  const onFinishReorder = useCallback((key, views) => {
    reorderViews(props.reorderUrl, props.csrfToken, key, views.map(view => view.id));
  }, [props]);

  const onLogout = useCallback(() => {
    submitForm(props.bottom.logoutAction, { csrfmiddlewaretoken: props.csrfToken });
  }, [props]);

  const onNavigate = useCallback(item => {
    if (item?.href) window.location.assign(item.href);
  }, []);

  return <React.Fragment>
    <MobileState host={host} />
    <HtmxBridge host={host} />
    <Sidebar model={model} pins={pinModel} palette={palette} labels={props.labels}
      renderIcon={renderIcon} rowActions={finePointer ? 'hover' : 'always'} enableSortable={enableSortable}
      onNavigate={onNavigate} onToggleGroup={writeOpen} onToggleLists={open => writeOpen('lists', open)}
      onUnpinList={unpinList} onDeleteView={onDeleteView} onFinishReorder={onFinishReorder}
      onLogout={onLogout} menuLayerProps={MENU_LAYER_PROPS} />
  </React.Fragment>;
}

export function mountSidebar(host, props) {
  return mountRoot(host, <SidebarView host={host} props={props} />);
}
