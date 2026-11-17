import React, { useEffect, useRef, useState } from 'react';
import { ArtworkPicker as SharedArtworkPicker, EditionPicker as SharedEditionPicker } from './ArtworkPicker.jsx';
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
  const accent = color('--color-accent', '#6366f1');
  return {
    text: color('--color-text', '#fafafa'),
    textSecondary: color('--color-text-secondary', '#d4d4d8'),
    textMuted: color('--color-text-muted', '#a1a1aa'),
    surface: color('--color-surface', '#27272a'),
    surfaceStrong: color('--color-surface-strong', '#3f3f46'),
    surfaceMuted: color('--color-surface-muted', '#52525b'),
    border: color('--color-surface-border', '#3f3f46'),
    accent,
    accentStrong: accent,
    accentText: '#ffffff',
    danger: color('--color-danger', '#ef4444'),
    radius: 8,
    radiusSmall: 6,
    button: {
      radius: 6,
      primary: { background: accent, pressed: accent, hovered: accent, text: '#ffffff', border: 'transparent' },
      surface: {
        background: color('--color-surface-strong', '#3f3f46'),
        pressed: color('--color-surface-muted', '#52525b'),
        hovered: color('--color-surface-muted', '#52525b'),
        text: color('--color-text', '#fafafa'),
        border: color('--color-surface-border', '#3f3f46'),
      },
      focusStyle: {},
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

function hideFallback(id) {
  const fallback = id ? document.getElementById(id) : null;
  if (!fallback) return () => {};
  const previous = fallback.hidden;
  fallback.hidden = true;
  return () => { fallback.hidden = previous; };
}

function setCandidateRows(payload) {
  const rows = Array.isArray(payload) ? payload : payload?.candidates || payload?.results || [];
  return rows.map(row => ({
    id: row.id ?? row.image,
    uri: row.uri ?? row.image,
    title: row.title || '',
  })).filter(row => row.uri);
}

function EditionRows(payload, pickerType) {
  const rows = Array.isArray(payload?.results) ? payload.results : [];
  if (pickerType === 'music') {
    return rows.map(release => {
      const meta = [];
      const first = [release.release_date || 'Date unknown', release.country, release.status]
        .filter(Boolean).join(' · ');
      if (first) meta.push(first);
      const second = [
        release.format || 'Format unknown',
        release.packaging,
        release.label,
        release.track_count ? `${release.track_count} tracks` : '',
      ].filter(Boolean).join(' · ');
      if (second) meta.push(second);
      if (release.barcode) meta.push(`Barcode: ${release.barcode}`);
      return {
        id: release.release_id,
        uri: release.image,
        title: release.title || '',
        meta,
      };
    });
  }
  return rows.map(edition => {
    const meta = [[edition.language, edition.format].filter(Boolean).join(' · ')].filter(Boolean);
    return {
      id: edition.id,
      uri: edition.image,
      title: edition.title || '',
      meta,
    };
  });
}

export function ArtworkPicker({ host, props }) {
  const palette = usePalette();
  const [selected, setSelected] = useState(null);
  const [url, setUrl] = useState('');
  const [showUrl, setShowUrl] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [candidates, setCandidates] = useState([]);
  const [candidatesState, setCandidatesState] = useState('loading');
  const [retryToken, setRetryToken] = useState(0);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const objectUrl = useRef('');
  const droppedFile = useRef(null);
  const form = () => document.getElementById(props.formId);

  useEffect(() => hideFallback(props.fallbackId), [props.fallbackId]);

  useEffect(() => {
    const sequence = { cancelled: false };
    setCandidatesState('loading');
    fetch(`${props.candidatesUrl}?format=json`, {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    })
      .then(response => {
        if (!response.ok) throw new Error(`Candidates failed with ${response.status}`);
        return response.json();
      })
      .then(payload => {
        if (sequence.cancelled) return;
        setCandidates(setCandidateRows(payload));
        setCandidatesState('ready');
      })
      .catch(() => {
        if (!sequence.cancelled) setCandidatesState('error');
      });
    return () => { sequence.cancelled = true; };
  }, [props.candidatesUrl, retryToken]);

  useEffect(() => () => {
    if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
  }, []);

  useEffect(() => {
    const fileInput = form()?.querySelector('input[type="file"]');
    if (!fileInput) return undefined;
    const onChange = () => {
      const file = fileInput.files?.[0];
      if (!file) return;
      if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
      objectUrl.current = URL.createObjectURL(file);
      droppedFile.current = null;
      setSelected({ kind: 'file', uri: objectUrl.current, title: file.name });
    };
    fileInput.addEventListener('change', onChange);
    return () => fileInput.removeEventListener('change', onChange);
  }, [props.formId]);

  useEffect(() => {
    const onDragOver = event => {
      event.preventDefault();
      setDragActive(true);
    };
    const onDragLeave = event => {
      if (event.target === host) setDragActive(false);
    };
    const onDrop = event => {
      event.preventDefault();
      setDragActive(false);
      const file = event.dataTransfer?.files?.[0];
      if (!file) return;
      droppedFile.current = file;
      const fileInput = form()?.querySelector('input[type="file"]');
      if (fileInput) {
        try {
          const transfer = new DataTransfer();
          transfer.items.add(file);
          fileInput.files = transfer.files;
        } catch {
          fileInput.value = '';
        }
      }
      if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
      objectUrl.current = URL.createObjectURL(file);
      setSelected({ kind: 'file', uri: objectUrl.current, title: file.name });
    };
    host.addEventListener('dragover', onDragOver);
    host.addEventListener('dragleave', onDragLeave);
    host.addEventListener('drop', onDrop);
    return () => {
      host.removeEventListener('dragover', onDragOver);
      host.removeEventListener('dragleave', onDragLeave);
      host.removeEventListener('drop', onDrop);
    };
  }, [host, props.formId]);

  function reset() {
    const fileInput = form()?.querySelector('input[type="file"]');
    if (fileInput) fileInput.value = '';
    if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
    objectUrl.current = '';
    droppedFile.current = null;
    setUrl('');
    setSelected(null);
    setError('');
  }

  function pickCandidate(candidate) {
    const fileInput = form()?.querySelector('input[type="file"]');
    if (fileInput) fileInput.value = '';
    droppedFile.current = null;
    if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
    objectUrl.current = '';
    setUrl('');
    setError('');
    setSelected({ kind: 'candidate', id: candidate.id, uri: candidate.uri, title: candidate.title });
  }

  function changeUrl(value) {
    setUrl(value);
    const trimmed = value.trim();
    const fileInput = form()?.querySelector('input[type="file"]');
    if (fileInput) fileInput.value = '';
    droppedFile.current = null;
    if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
    objectUrl.current = '';
    setError('');
    setSelected(trimmed ? { kind: 'url', uri: trimmed, title: '' } : null);
  }

  function chooseFile() {
    form()?.querySelector('input[type="file"]')?.click();
  }

  function save() {
    if (!selected) return;
    const tokenInput = form()?.querySelector('input[name="csrfmiddlewaretoken"]');
    const nextInput = form()?.querySelector('input[name="next"]');
    if (droppedFile.current) {
      const data = new FormData();
      data.append('csrfmiddlewaretoken', tokenInput?.value || '');
      data.append('next', nextInput?.value || props.nextUrl || '');
      data.append('cover_url', '');
      data.append('cover_file', droppedFile.current, droppedFile.current.name);
      setSaving(true);
      fetch(props.saveUrl, { method: 'POST', body: data, credentials: 'same-origin' })
        .then(response => { window.location.assign(response.url || nextInput?.value || props.nextUrl || '/'); })
        .catch(() => {
          setSaving(false);
          setError(props.labels?.saveError || 'Could not save that cover.');
        });
      return;
    }
    const coverUrl = form()?.querySelector('input[name="cover_url"]');
    if (coverUrl) coverUrl.value = selected.uri;
    form()?.requestSubmit();
  }

  return <SharedArtworkPicker
    title={props.title}
    current={props.current}
    candidates={candidates}
    candidatesState={candidatesState}
    selected={selected}
    url={url}
    showUrl={showUrl}
    dragActive={dragActive}
    accepted={props.accepted}
    labels={props.labels}
    palette={palette}
    saving={saving}
    error={error}
    onChooseFile={chooseFile}
    onToggleUrl={() => setShowUrl(value => !value)}
    onUrlChange={changeUrl}
    onPickCandidate={pickCandidate}
    onReset={reset}
    onSave={save}
    onRetryCandidates={() => setRetryToken(value => value + 1)}
  />;
}

export function EditionPicker({ host, props }) {
  const palette = usePalette();
  const [query, setQuery] = useState('');
  const [rows, setRows] = useState([]);
  const [state, setState] = useState('idle');
  const [open, setOpen] = useState(false);
  const loadedQuery = useRef(null);
  const sequence = useRef(0);
  const timer = useRef(null);

  useEffect(() => hideFallback(props.fallbackId), [props.fallbackId]);

  function load(needle) {
    const current = sequence.current + 1;
    sequence.current = current;
    setState('loading');
    setOpen(true);
    const params = new URLSearchParams({
      q: needle,
      return_url: props.returnUrl || '',
      format: 'json',
    });
    fetch(`${props.listUrl}?${params}`, {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    })
      .then(response => {
        if (!response.ok) throw new Error(`Editions failed with ${response.status}`);
        return response.json();
      })
      .then(payload => {
        if (current !== sequence.current) return;
        loadedQuery.current = needle;
        setRows(EditionRows(payload, props.pickerType));
        setState('ready');
      })
      .catch(() => {
        if (current !== sequence.current) return;
        setState('error');
      });
  }

  useEffect(() => {
    if (!open) return undefined;
    const dismiss = event => {
      if (host.contains(event.target) || formContains(event.target)) return;
      setOpen(false);
    };
    document.addEventListener('pointerdown', dismiss);
    return () => document.removeEventListener('pointerdown', dismiss);
  }, [host, open, props.formId]);

  function formContains(target) {
    const form = document.getElementById(props.formId);
    return Boolean(form && form.contains(target));
  }

  function changeQuery(value) {
    setQuery(value);
    const needle = value.trim();
    if (loadedQuery.current === needle) return;
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => load(needle), 250);
  }

  useEffect(() => () => window.clearTimeout(timer.current), []);

  function focus() {
    setOpen(true);
    if (state === 'idle') load(query.trim());
  }

  function select(row) {
    const form = document.getElementById(props.formId);
    if (!form) return;
    const field = form.querySelector(`input[name="${props.pickerType === 'music' ? 'release_id' : 'edition_id'}"]`);
    if (field) field.value = row.id;
    form.requestSubmit();
  }

  return <SharedEditionPicker
    title={props.title}
    description={props.description}
    provider={props.provider}
    query={query}
    placeholder={props.placeholder}
    results={rows}
    selected={props.selected}
    selectedId={props.selectedId}
    state={state}
    open={open}
    overlay
    labels={props.labels}
    palette={palette}
    onQueryChange={changeQuery}
    onFocus={focus}
    onSelect={select}
    onRetry={() => { loadedQuery.current = null; load(query.trim()); }}
  />;
}
