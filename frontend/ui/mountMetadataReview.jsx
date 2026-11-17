import React, { useState } from 'react';
import { mountRoot } from '../settings/root';
import {
  EpisodeOrdering,
  MetadataMatchResults,
  TvProviderSwitch,
} from './MetadataReview';
import { rootStyle } from './rootStyle.web';

function readPalette() {
  const css = rootStyle();
  const token = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
  return {
    background: token('--color-page-bg', '#18181b'),
    surface: token('--color-surface', '#27272a'),
    surfaceStrong: token('--color-surface-strong', '#3f3f46'),
    surfaceMuted: token('--color-surface-muted', '#52525b'),
    border: token('--color-surface-border', '#3f3f46'),
    text: token('--color-text', '#fafafa'),
    textSecondary: token('--color-text-secondary', '#d4d4d8'),
    textMuted: token('--color-text-muted', '#a1a1aa'),
    accent: token('--color-accent', '#6366f1'),
    accentStrong: token('--color-accent-strong', '#4f46e5'),
    accentText: token('--color-accent-contrast', '#ffffff'),
    success: token('--color-status-clean', '#34d399'),
    successSoft: 'rgba(52, 211, 153, 0.2)',
    warning: token('--color-warning', '#fbbf24'),
    warningSoft: 'rgba(251, 191, 36, 0.2)',
    accentSoft: 'rgba(99, 102, 241, 0.2)',
    danger: token('--color-danger', '#f87171'),
    dangerSoft: 'rgba(248, 113, 113, 0.2)',
    radius: 8,
    focusStyle: {},
  };
}

function submitForm(action, csrf, fields) {
  const form = document.createElement('form');
  form.method = 'post';
  form.action = action;
  form.style.display = 'none';
  const add = (name, value) => {
    const input = document.createElement('input');
    input.type = 'hidden';
    input.name = name;
    input.value = value;
    form.append(input);
  };
  add('csrfmiddlewaretoken', csrf || '');
  Object.entries(fields).forEach(([name, value]) => {
    (Array.isArray(value) ? value : [value]).forEach(item => {
      if (item !== undefined && item !== null) add(name, item);
    });
  });
  document.body.append(form);
  form.submit();
}

function navigate(url) {
  if (url) window.location.assign(url);
}

function EpisodeOrderingAdapter({ props }) {
  const [palette] = useState(readPalette);

  function review(choice) {
    submitForm(props.actions.review, props.csrf, {
      provider: choice.provider,
      key: choice.key,
    });
  }

  function revert() {
    submitForm(props.actions.revert, props.csrf, { action: 'revert' });
  }

  function apply({ orderId, token, rows }) {
    const fields = { action: 'apply', order_id: orderId, token };
    rows.forEach(row => {
      fields[`episodes_${row.id}`] = row.selected;
      fields[`combine_${row.id}`] = row.combine;
      if (row.archive) fields[`archive_${row.id}`] = 'on';
    });
    submitForm(props.actions.apply, props.csrf, fields);
  }

  return <EpisodeOrdering
    model={props.model}
    labels={props.labels}
    palette={palette}
    onOpenItem={() => navigate(props.model?.itemUrl)}
    onReview={review}
    onRevert={revert}
    onApply={apply}
    onCancel={() => navigate(props.model?.cancelUrl)} />;
}

function TvProviderSwitchAdapter({ props }) {
  const [palette] = useState(readPalette);
  return <TvProviderSwitch
    model={props.model}
    labels={props.labels}
    palette={palette}
    onMove={() => submitForm(props.actions.move, props.csrf, {})}
    onCancel={() => navigate(props.model?.cancelUrl)}
    onBack={() => navigate(props.model?.backUrl)} />;
}

function MetadataMatchResultsAdapter({ host, props }) {
  const [palette] = useState(readPalette);
  const [busyId, setBusyId] = useState(null);
  const [error, setError] = useState('');

  function choose(result) {
    if (busyId) return;
    setBusyId(result.id);
    setError('');
    if (props.model?.variant === 'libraryMove') {
      const values = {
        target_media_type: props.model.targetMediaType,
        target_source: props.model.targetSource,
        target_media_id: result.id,
        return_url: props.model.returnUrl || '',
      };
      if (window.htmx?.ajax) {
        window.htmx.ajax('POST', props.actions.choose, {
          target: props.model.target || host.parentElement,
          swap: 'innerHTML',
          values,
          headers: { 'X-CSRFToken': props.csrf || '' },
        });
        return;
      }
      submitForm(props.actions.choose, props.csrf, values);
      return;
    }
    submitForm(props.actions.choose, props.csrf, {
      provider: props.model?.provider,
      provider_media_id: result.id,
      return_url: props.model?.returnUrl || '',
    });
  }

  return <MetadataMatchResults
    model={props.model}
    labels={props.labels}
    palette={palette}
    busyId={busyId}
    error={error}
    onChoose={choose} />;
}

export function mountEpisodeOrdering(host, props) {
  return mountRoot(host, <EpisodeOrderingAdapter props={props} />);
}

export function mountTvProviderSwitch(host, props) {
  return mountRoot(host, <TvProviderSwitchAdapter props={props} />);
}

export function mountMetadataMatchResults(host, props) {
  return mountRoot(host, <MetadataMatchResultsAdapter host={host} props={props} />);
}

