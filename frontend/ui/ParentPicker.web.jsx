import React, { useEffect, useRef, useState } from 'react';
import { ParentPicker as SharedParentPicker } from './ParentPicker.jsx';
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
    border: color('--color-surface-border', '#3f3f46'),
    accent: color('--color-accent', '#6366f1'),
    accentText: '#ffffff',
    danger: color('--color-danger', '#ef4444'),
    radius: 8,
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

function interpolate(text, values) {
  return String(text || '').replace(/%\((\w+)\)s/g, (match, key) => (
    Object.prototype.hasOwnProperty.call(values, key) ? String(values[key]) : match
  ));
}

function requestRows(url, query) {
  return new Promise((resolve, reject) => {
    const htmx = window.htmx;
    if (!htmx?.ajax) {
      reject(new Error('htmx is unavailable'));
      return;
    }
    const endpoint = `${url}?q=${encodeURIComponent(query)}&format=json`;
    htmx.ajax('GET', endpoint, {
      target: document.body,
      swap: 'none',
      handler: (element, info) => {
        const xhr = info.xhr;
        if (xhr.status < 200 || xhr.status >= 300) {
          reject(new Error(`Parent search failed with ${xhr.status}`));
          return;
        }
        try {
          const payload = JSON.parse(xhr.responseText);
          resolve(Array.isArray(payload?.results) ? payload.results : []);
        } catch (error) {
          reject(error);
        }
      },
    }).catch(reject);
  });
}

export function ParentPicker({ props }) {
  const palette = usePalette();
  const labels = props.labels || {};
  const selectedLabel = useRef(null);
  const requestSequence = useRef(0);
  const [query, setQuery] = useState('');
  const [rows, setRows] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [state, setState] = useState('idle');
  const [retryToken, setRetryToken] = useState(0);

  const needle = query.trim();

  useEffect(() => {
    if (needle === selectedLabel.current) {
      requestSequence.current += 1;
      setRows([]);
      setState('idle');
      return undefined;
    }
    selectedLabel.current = null;
    if (needle.length < 2) {
      requestSequence.current += 1;
      setRows([]);
      setState('idle');
      return undefined;
    }
    setState('loading');
    const timer = setTimeout(() => {
      const sequence = requestSequence.current + 1;
      requestSequence.current = sequence;
      requestRows(props.url, needle)
        .then(results => {
          if (sequence !== requestSequence.current) return;
          setRows(results);
          setState('ready');
        })
        .catch(() => {
          if (sequence !== requestSequence.current) return;
          setRows([]);
          setState('error');
        });
    }, 250);
    return () => clearTimeout(timer);
  }, [needle, props.url, retryToken]);

  function select(row) {
    const label = row.label || row.title;
    selectedLabel.current = label;
    setSelectedId(row.id);
    setQuery(label);
    setRows([]);
    setState('idle');
    const hidden = document.getElementById(props.hiddenInputId);
    if (hidden) {
      hidden.value = row.id;
      hidden.dispatchEvent(new Event('change', { bubbles: true }));
    }
  }

  const copy = {
    ...labels,
    empty: interpolate(labels.empty, { query: needle }),
  };

  return <SharedParentPicker
    plain
    resultsID={`parent-${props.kind}-results`}
    query={query}
    placeholder={props.placeholder}
    results={rows}
    selectedId={selectedId}
    state={state}
    labels={copy}
    palette={palette}
    onQueryChange={setQuery}
    onSelect={select}
    onRetry={() => setRetryToken(value => value + 1)}
  />;
}
