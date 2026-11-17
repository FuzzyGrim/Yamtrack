import React, { useEffect, useMemo, useState } from 'react';
import { mountRoot } from '../settings/root';
import { StatisticsCharts } from './StatisticsCharts';
import { rootStyle } from './rootStyle.web';

const LEVEL_THRESHOLDS = [0, 3, 6, 9];

function themeRadius(css) {
  const value = parseFloat(css.getPropertyValue('--theme-radius'));
  return Number.isFinite(value) && value > 0 ? value : 18;
}

function surfaceColor(css, token) {
  const base = token('--color-surface', '#27272a');
  const opacity = parseFloat(css.getPropertyValue('--theme-surface-opacity'));
  const percent = Number.isFinite(opacity) ? opacity : 100;
  const hex = base.replace('#', '');
  if (percent >= 100 || !/^[0-9a-fA-F]{6}$/.test(hex)) return base;
  const r = parseInt(hex.slice(0, 2), 16);
  const g = parseInt(hex.slice(2, 4), 16);
  const b = parseInt(hex.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${percent / 100})`;
}

function readPalette() {
  const css = rootStyle();
  const token = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
  return {
    background: token('--color-page-bg', '#18181b'),
    surface: surfaceColor(css, token),
    surfaceStrong: token('--color-surface-strong', '#3f3f46'),
    surfaceMuted: token('--color-surface-muted', '#52525b'),
    border: token('--color-surface-border', token('--color-border', '#3f3f46')),
    text: token('--color-text', '#fafafa'),
    textSecondary: token('--color-text-secondary', '#d4d4d8'),
    textMuted: token('--color-text-muted', '#a1a1aa'),
    accent: token('--color-accent', '#6366f1'),
    accentStrong: token('--color-accent-strong', '#4f46e5'),
    accentContrast: token('--color-accent-contrast', '#ffffff'),
    link: token('--color-link', '#818cf8'),
    danger: token('--color-danger', '#ef4444'),
    warning: token('--color-warning', '#f59e0b'),
    success: token('--color-success', '#10b981'),
    radius: themeRadius(css),
    radiusSmall: 6,
  };
}

function readSelectedTypes(validTypes) {
  const params = new URLSearchParams(window.location.search);
  const wanted = new Set(
    String(params.get('media-type') || '')
      .split(',')
      .map(value => value.trim()),
  );
  const selected = validTypes.filter(value => wanted.has(value));
  return selected.length === validTypes.length ? [] : selected;
}

function levelFor(count) {
  for (let index = 0; index < LEVEL_THRESHOLDS.length; index += 1) {
    if (count <= LEVEL_THRESHOLDS[index]) return index;
  }
  return 4;
}

function sumSeries(models, fallback) {
  const base = fallback || models.find(model => model?.labels?.length) || {};
  const labels = base.labels || [];
  const data = labels.map((_, index) => models.reduce(
    (sum, model) => sum + Number((model?.data || [])[index] || 0),
    0,
  ));
  return { labels, data, color: base.color };
}

function mergeRhythm(models, fallback) {
  const matrix = (fallback?.matrix || []).map(row => row.map(() => 0));
  models.forEach((model) => {
    (model?.matrix || []).forEach((row, rowIndex) => {
      row.forEach((value, columnIndex) => {
        if (matrix[rowIndex]?.[columnIndex] !== undefined) {
          matrix[rowIndex][columnIndex] += Number(value || 0);
        }
      });
    });
  });
  return {
    matrix,
    weekdayLabels: fallback?.weekdayLabels || [
      'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun',
    ],
  };
}

function mergeHeatmap(models, fallback) {
  const base = fallback || {};
  return {
    ...base,
    weeks: (base.weeks || []).map((week, weekIndex) => ({
      days: (week.days || []).map((day, dayIndex) => {
        if (day.future) return day;
        const count = models.reduce(
          (sum, model) => sum + Number(
            model?.weeks?.[weekIndex]?.days?.[dayIndex]?.count || 0,
          ),
          0,
        );
        return { ...day, count, level: levelFor(count) };
      }),
    })),
  };
}

function mergeStatusComposition(models, fallback) {
  const labels = fallback?.labels || [];
  const colors = fallback?.colors || [];
  return {
    labels,
    colors,
    data: labels.map((label, index) => models.reduce((sum, model) => {
      const position = (model?.labels || []).indexOf(label);
      return sum + Number(position >= 0 ? model.data[position] : 0);
    }, 0)),
  };
}

function mergeRating(models, fallback) {
  const labels = fallback?.labels || [];
  return {
    labels,
    scaleMax: fallback?.scaleMax || 10,
    data: labels.map((_, index) => models.reduce(
      (sum, model) => sum + Number((model?.data || [])[index] || 0),
      0,
    )),
  };
}

function mergeCharts(allModel, typeModels) {
  if (!typeModels.length) return allModel;
  const hours = key => sumSeries(
    typeModels.map(model => model?.[key]),
    allModel?.[key],
  );
  return {
    ...allModel,
    rhythm: mergeRhythm(typeModels.map(model => model?.rhythm), allModel?.rhythm),
    heatmap: mergeHeatmap(typeModels.map(model => model?.heatmap), allModel?.heatmap),
    mediaTypeHours: typeModels.reduce((merged, model) => {
      const chart = model?.mediaTypeHours || {};
      return {
        labels: [...merged.labels, ...(chart.labels || [])],
        data: [...merged.data, ...(chart.data || [])],
        colors: [...merged.colors, ...(chart.colors || [])],
      };
    }, { labels: [], data: [], colors: [] }),
    hoursByMonth: hours('hoursByMonth'),
    hoursByWeekday: hours('hoursByWeekday'),
    hoursByTimeOfDay: hours('hoursByTimeOfDay'),
    statusComposition: mergeStatusComposition(
      typeModels.map(model => model?.statusComposition),
      allModel?.statusComposition,
    ),
    ratingDistribution: mergeRating(
      typeModels.map(model => model?.ratingDistribution),
      allModel?.ratingDistribution,
    ),
    statusBreakdown: {
      columns: allModel?.statusBreakdown?.columns || [],
      rows: typeModels.flatMap(model => model?.statusBreakdown?.rows || []),
    },
    completion: {
      rows: typeModels.flatMap(model => model?.completion?.rows || []),
    },
  };
}

function Adapter({ props }) {
  const [palette, setPalette] = useState(readPalette);
  const validTypes = useMemo(() => Object.keys(props.types || {}), [props.types]);
  const [selectedTypes, setSelectedTypes] = useState(() => readSelectedTypes(validTypes));

  useEffect(() => {
    const updatePalette = () => setPalette(readPalette());
    const observer = new MutationObserver(updatePalette);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['class', 'style'],
    });
    const scheme = window.matchMedia('(prefers-color-scheme: dark)');
    scheme.addEventListener('change', updatePalette);

    const updateSelection = () => setSelectedTypes(readSelectedTypes(validTypes));
    window.addEventListener('stats-media-type-changed', updateSelection);
    window.addEventListener('popstate', updateSelection);
    return () => {
      observer.disconnect();
      scheme.removeEventListener('change', updatePalette);
      window.removeEventListener('stats-media-type-changed', updateSelection);
      window.removeEventListener('popstate', updateSelection);
    };
  }, [validTypes]);

  const model = useMemo(() => {
    if (selectedTypes.length === 0) return props.all;
    const typeModels = selectedTypes
      .map(type => props.types?.[type])
      .filter(Boolean);
    if (selectedTypes.length === 1) return typeModels[0] || props.all;
    return mergeCharts(props.all, typeModels);
  }, [props, selectedTypes]);

  return <StatisticsCharts
    model={model || {}}
    palette={palette}
    groups={[props.group]}
    showFeatured={false} />;
}

export function mountStatisticsCharts(host, props) {
  // Release spaces these stacked cards 1rem apart; the island host would
  // otherwise inherit the page's larger section gap.
  if (props.group && props.group !== 'overview') host.style.marginBottom = '16px';
  return mountRoot(host, <Adapter props={props} />);
}
