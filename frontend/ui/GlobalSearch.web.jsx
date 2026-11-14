import React, { useCallback, useEffect, useState } from 'react';
import { GlobalSearch as SharedGlobalSearch } from './GlobalSearch.jsx';
import { useKeyboardOverlap } from './keyboardOverlap.web';
import { requestBarcode } from './barcodeEntry.web';

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
  const css = getComputedStyle(document.documentElement);
  const token = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
  const color = (name, fallback) => resolveColor(token(name, fallback), fallback);
  return {
    text: color('--color-text', '#fafafa'),
    textSecondary: color('--color-text-secondary', '#d1d5db'),
    textMuted: color('--color-text-muted', '#adb5bd'),
    panel: color('--color-panel', '#272c31'),
    surface: color('--color-surface', '#2a2f35'),
    surfaceMuted: color('--color-surface-muted', '#343a40'),
    surfaceAlt: color('--color-surface-alt', '#3e454d'),
    border: color('--color-surface-muted', '#343a40'),
    accent: color('--color-accent', '#4a9eff'),
    errorSurface: color('--color-surface', '#2a2f35'),
    errorBorder: color('--color-surface-muted', '#343a40'),
    focusStyle: {},
  };
}

function useCompact() {
  const [compact, setCompact] = useState(() => window.matchMedia('(max-width: 639px)').matches);
  useEffect(() => {
    const query = window.matchMedia('(max-width: 639px)');
    const update = () => setCompact(query.matches);
    update();
    query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, []);
  return compact;
}

export function GlobalSearch({ host, props }) {
  const [palette, setPalette] = useState(readPalette);
  const [dismissSignal, setDismissSignal] = useState(0);
  const compact = useCompact();

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

  useEffect(() => {
    if (!host) return undefined;
    const onPointer = (event) => {
      if (event.target && host.contains(event.target)) return;
      setDismissSignal(value => value + 1);
    };
    const onKey = (event) => {
      if (event.key === 'Escape') setDismissSignal(value => value + 1);
    };
    document.addEventListener('click', onPointer);
    window.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('click', onPointer);
      window.removeEventListener('keydown', onKey);
    };
  }, [host]);

  const onLoadSuggestions = useCallback(async ({ query, mediaType }) => {
    const url = new URL(props.suggestionsUrl, window.location.origin);
    url.searchParams.set('q', query);
    url.searchParams.set('media_type', mediaType);
    url.searchParams.set('format', 'json');
    const response = await fetch(url, {
      headers: { Accept: 'application/json' },
      credentials: 'same-origin',
    });
    if (!response.ok) throw new Error(`Suggestions request failed: ${response.status}`);
    const data = await response.json();
    const suggestions = Array.isArray(data.suggestions) ? data.suggestions : [];
    return {
      suggestions: suggestions.map((suggestion, index) => ({
        id: String(suggestion.id ?? index),
        title: suggestion.title,
        subtitle: suggestion.subtitle,
        image: suggestion.image ? new URL(suggestion.image, window.location.origin).toString() : null,
        url: suggestion.url,
      })),
    };
  }, [props.suggestionsUrl]);

  const onSubmit = useCallback(({ query, mediaType }) => {
    const url = new URL(props.action, window.location.origin);
    url.searchParams.set('q', query);
    url.searchParams.set('media_type', mediaType);
    window.location.assign(url.toString());
  }, [props.action]);

  // Remember the picked type on the server so the next page opens with it.
  const onTypeChange = useCallback((mediaType) => {
    const csrf = document.cookie.match(/csrftoken=([^;]+)/)?.[1] || '';
    fetch(new URL(props.typeUrl, window.location.origin), {
      method: 'PUT',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
      body: JSON.stringify({ media_type: mediaType }),
    }).catch(() => {});
  }, [props.typeUrl]);

  const onSelectSuggestion = useCallback((suggestion) => {
    if (!suggestion?.url) return;
    window.location.assign(new URL(suggestion.url, window.location.origin).toString());
  }, []);

  const onBarcodeRequest = useCallback(
    () => requestBarcode({ labels: props.labels }),
    [props.labels],
  );

  return <SharedGlobalSearch
    useKeyboardOverlap={useKeyboardOverlap}
    query={props.query}
    selectedType={props.selectedType}
    mediaTypes={props.mediaTypes}
    barcodeTypes={props.barcodeTypes}
    labels={props.labels}
    palette={palette}
    compact={compact}
    dismissSignal={dismissSignal}
    onLoadSuggestions={onLoadSuggestions}
    onSubmit={onSubmit}
    onTypeChange={props.typeUrl ? onTypeChange : undefined}
    onSelectSuggestion={onSelectSuggestion}
    onBarcodeRequest={onBarcodeRequest}
  />;
}
