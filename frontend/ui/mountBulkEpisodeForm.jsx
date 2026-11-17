import React, { useEffect, useMemo, useState } from 'react';
import { mountRoot } from '../settings/root';
import { BulkEpisodeForm } from './BulkEpisodeForm';
import { normalizeBulkEpisodeForm } from './mediaCardForms';
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
    text: color('--color-text', '#fafafa'),
    textSecondary: color('--color-text-secondary', '#d4d4d8'),
    textMuted: color('--color-text-muted', '#a1a1aa'),
    surface: color('--color-surface', '#27272a'),
    surfaceStrong: color('--color-surface-strong', '#3f3f46'),
    surfaceMuted: color('--color-surface-muted', '#52525b'),
    border: color('--color-surface-border', '#3f3f46'),
    accent: color('--color-accent', '#6366f1'),
    accentContrast: '#ffffff',
    danger: color('--color-danger', '#ef4444'),
    warning: color('--color-warning-text', '#fbbf24'),
    radius: 8,
    focusStyle: {},
  };
  palette.dateTime = { ...palette };
  return palette;
}

function Adapter({ host, props }) {
  const model = useMemo(() => normalizeBulkEpisodeForm(props), [props]);
  const [values, setValues] = useState(model.values);
  const [busy, setBusy] = useState(false);
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

  const legacyForm = () => document.getElementById(model.formId);

  useEffect(() => {
    const form = legacyForm();
    if (!form) return undefined;
    const previous = form.style.display;
    form.style.display = 'none';
    return () => { form.style.display = previous; };
  }, [model.formId]);

  function change(name, value) {
    setValues(current => (name && typeof name === 'object'
      ? { ...current, ...name }
      : { ...current, [name]: value }));
  }

  function submit(next) {
    const ajax = window.htmx?.ajax;
    if (!ajax || !model.action) return;
    setBusy(true);
    const request = ajax('POST', model.action, {
      values: {
        ...(model.hidden || {}),
        ...next,
        csrfmiddlewaretoken: model.csrfToken || '',
      },
      target: legacyForm() || host,
      swap: 'none',
    });
    if (request?.finally) request.finally(() => setBusy(false));
    else setBusy(false);
  }

  return <BulkEpisodeForm
    model={model}
    values={values}
    errors={model.errors}
    busy={busy}
    labels={model.labels}
    palette={palette}
    onChange={change}
    onSubmit={submit} />;
}

export function mountBulkEpisodeForm(host, props) {
  if (!window.htmx?.ajax) return () => {};
  return mountRoot(host, <Adapter host={host} props={props} />);
}
