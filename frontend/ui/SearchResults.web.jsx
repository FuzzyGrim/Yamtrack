import React, { useCallback, useEffect, useState } from 'react';
import { MediaCard } from './MediaCard.web';
import { SearchResults as SharedSearchResults } from './SearchResults.jsx';
import { RatingStarIcon } from './RatingStarIcon.web';
import { RatingStars } from './RatingStars';
import { useShelfCardWidth } from './shelfWidth.web';
import { mergeSearchRowPage, onlineRowParams, searchRowUrl } from './searchRows';
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
    surfaceMuted: color('--color-surface-muted', '#52525b'),
    border: color('--color-surface-border', '#3f3f46'),
    accent: color('--color-accent', '#6366f1'),
    accentText: '#ffffff',
    link: color('--color-link', '#818cf8'),
    danger: color('--color-danger', '#ef4444'),
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

function navigate(href) {
  if (!href) return;
  window.location.assign(href);
}

export function SearchResults({ props }) {
  const palette = usePalette();
  const itemWidth = useShelfCardWidth();

  const onOpenCard = useCallback(item => navigate(item?.card?.href), []);
  const onOpenLink = useCallback(href => navigate(href), []);

  // Home's cards render themselves on the web: links, tracking modals and htmx posts.
  const renderCard = useCallback(item => <MediaCard model={item.card} labels={props.labels}
    standalone variant={item.card?.shape === 'square' ? 'grid-square' : 'grid'} />, [props.labels]);

  const onLoadMore = useCallback(async row => {
    const response = await window.fetch(searchRowUrl(row, props), {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    });
    if (!response.ok) throw new Error(`Search request failed: ${response.status}`);
    const payload = await response.json();
    return payload.row ? mergeSearchRowPage(row, payload.row) : null;
  }, [props]);

  const onLoadOnlineRow = useCallback(async source => {
    const params = new URLSearchParams(onlineRowParams(source, props));
    const response = await window.fetch(`/search/results?${params}`, {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    });
    if (!response.ok) throw new Error(`Search request failed: ${response.status}`);
    return response.json();
  }, [props.query, props.mediaType]);

  const renderRating = useCallback(({ value, label, scaleMax, palette: ratingPalette, onSelect }) => (
    <RatingStars
      scaleMax={scaleMax}
      value={value}
      label={label}
      onSelect={onSelect}
      palette={ratingPalette}
      renderStar={lit => RatingStarIcon(lit)}
    />
  ), []);

  return <SharedSearchResults
    {...props}
    palette={{ ...palette, ...(props.palette || {}) }}
    itemWidth={itemWidth}
    renderCard={renderCard}
    renderRating={renderRating}
    onOpenCard={onOpenCard}
    onOpenLink={onOpenLink}
    onLoadMore={onLoadMore}
    onLoadOnlineRow={onLoadOnlineRow}
  />;
}
