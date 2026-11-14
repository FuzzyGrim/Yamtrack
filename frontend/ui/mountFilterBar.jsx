import React, { useEffect, useState } from 'react';
import { mountRoot } from '../settings/root';
import { FilterBar } from './FilterBar';

const rangeFields = {
  rating: { from: 'rating_min', to: 'rating_max' },
  releaseDate: {
    from: 'release_date_from',
    to: 'release_date_to',
    within: 'release_date_within',
    unit: 'release_date_within_unit',
  },
  dateAdded: {
    from: 'date_added_from',
    to: 'date_added_to',
    within: 'date_added_within',
    unit: 'date_added_within_unit',
  },
  completedDate: {
    from: 'completed_date_from',
    to: 'completed_date_to',
    within: 'completed_date_within',
    unit: 'completed_date_within_unit',
  },
};

// The Search media type menu's tokens, so the dropdowns draw like every other app dropdown.
const menuPalette = {
  background: 'var(--color-page-bg)',
  surface: 'var(--color-surface)',
  surfaceStrong: 'var(--color-surface-strong)',
  surfaceMuted: 'var(--color-surface-muted)',
  surfaceHover: 'var(--color-surface-hover)',
  border: 'var(--color-surface-muted)',
  text: 'var(--color-text)',
  textSecondary: 'var(--color-text-secondary)',
  textMuted: 'var(--color-text-muted)',
  accent: 'var(--color-accent)',
  accentStrong: 'var(--color-accent)',
  accentText: 'var(--color-accent-contrast)',
  danger: 'var(--color-danger)',
  disabled: 'var(--color-text-muted)',
  overlay: 'rgba(0, 0, 0, 0.5)',
};

function rangeState(data, fields) {
  if (!fields) {
    return { from: '', to: '', within: '', unit: 'days' };
  }
  return {
    from: data[fields.from] || '',
    to: data[fields.to] || '',
    within: fields.within ? data[fields.within] || '' : '',
    unit: fields.unit ? data[fields.unit] || 'days' : 'days',
  };
}

function readFilters(host, props) {
  const data = window.Alpine?.$data(host);
  if (!data) {
    return props.filters || {};
  }
  const ranges = {};
  for (const range of props.options?.ranges || []) {
    ranges[range.key] = rangeState(data, rangeFields[range.key]);
  }
  const fields = {};
  const modes = {};
  for (const section of props.options?.sections || []) {
    const value = data[section.state];
    fields[section.key] = section.type === 'multi'
      ? (Array.isArray(value) ? value.map(String) : [])
      : (value === undefined || value === null ? '' : String(value));
    if (section.modeState) {
      modes[section.key] = data[section.modeState] || section.modeValue || '';
    }
  }
  return {
    mediaTypes: [...(data.selectedTypes || [])],
    statuses: [...(data.statuses || [])],
    sort: data.sort || '',
    direction: data.direction || 'asc',
    view: data.layout || 'grid',
    ranges,
    fields,
    modes,
  };
}

function writeIntent(data, intent, props) {
  if (intent.type === 'toggle') {
    if (intent.key === 'mediaTypes') data.toggleMediaType?.(intent.value);
    if (intent.key === 'statuses') data.toggleStatus?.(intent.value);
    return;
  }
  if (intent.type === 'set') {
    if (intent.key === 'statuses') {
      data.statuses = intent.value;
    } else if (intent.key === 'view') {
      data.layout = intent.value;
    } else if (intent.key === 'sort') {
      if (typeof data.setSort === 'function') {
        data.setSort(intent.value);
      } else if (typeof data.toggleSort === 'function') {
        data.toggleSort(intent.value);
      } else {
        data.sort = intent.value;
      }
    } else {
      data[intent.key] = intent.value;
    }
    return;
  }
  if (intent.type === 'field' || intent.type === 'toggleField' || intent.type === 'clearField' || intent.type === 'fieldMode') {
    const section = props.options?.sections?.find((item) => item.key === intent.key);
    if (!section) {
      return;
    }
    if (intent.type === 'fieldMode') {
      if (section.modeState) {
        data[section.modeState] = intent.value;
      }
      return;
    }
    if (intent.type === 'clearField') {
      data[section.state] = section.type === 'multi'
        ? []
        : (section.clearValue === undefined ? '' : section.clearValue);
      return;
    }
    if (intent.type === 'toggleField') {
      const current = Array.isArray(data[section.state]) ? data[section.state] : [];
      data[section.state] = current.some((value) => String(value) === String(intent.value))
        ? current.filter((value) => String(value) !== String(intent.value))
        : [...current, intent.value];
      return;
    }
    data[section.state] = intent.value;
    return;
  }
  if (intent.type === 'range') {
    const fields = rangeFields[intent.key];
    if (!fields) {
      return;
    }
    data[fields.from] = intent.value?.from || '';
    data[fields.to] = intent.value?.to || '';
    if (fields.within) {
      data[fields.within] = intent.value?.within || '';
    }
    if (fields.unit) {
      data[fields.unit] = intent.value?.unit || 'days';
    }
    return;
  }
  if (intent.type === 'clear') {
    data.clearFilters?.();
    data.statuses = [];
    if (typeof data.broadMediaTypes === 'function') {
      data.selectedTypes = [...data.broadMediaTypes()];
    }
    return;
  }
  if (intent.type === 'savedView') {
    const view = props.options?.savedViews?.find((item) => item.id === intent.value);
    if (view?.url) {
      window.location.assign(view.url);
    }
  }
}

function Adapter({ host, props }) {
  const [filters, setFilters] = useState(() => readFilters(host, props));

  useEffect(() => {
    let runner = null;
    const bind = () => {
      if (!window.Alpine || runner) {
        return;
      }
      runner = window.Alpine.effect(() => setFilters(readFilters(host, props)));
    };
    if (!window.Alpine) {
      document.addEventListener('alpine:initialized', bind);
      return () => {
        document.removeEventListener('alpine:initialized', bind);
        if (runner) {
          window.Alpine?.release(runner);
        }
      };
    }
    bind();
    return () => {
      if (runner) {
        window.Alpine?.release(runner);
      }
    };
  }, [host, props]);

  function commit(intent) {
    if (intent.type === 'savedView') {
      writeIntent(null, intent, props);
      return;
    }
    const data = window.Alpine?.$data(host);
    if (!data) {
      return;
    }
    writeIntent(data, intent, props);
    if (intent.type === 'set' && intent.key === 'view' && typeof data.layoutHref === 'function') {
      window.location.assign(data.layoutHref(intent.value));
      return;
    }
    // Alpine owns the hidden filter form; wait for its flush before htmx reads it.
    window.setTimeout(() => {
      window.applyFilterChange?.(props.endpoint, props.target, props.formId || 'filter-form');
    }, 0);
  }

  return <FilterBar variant="toolbar" showClear={false} filters={filters} options={props.options} labels={props.labels}
    palette={props.palette || menuPalette} busy={props.busy} disabled={props.disabled}
    resultCount={props.resultCount} onChange={commit} />;
}

export function mountFilterBar(host, props) {
  return mountRoot(host, <Adapter host={host} props={props} />);
}
