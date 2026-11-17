import React, { useEffect, useState } from 'react';
import { mountRoot } from '../settings/root';
import { RatingChip } from './RatingChip';
import { RatingStarIcon } from './RatingStarIcon.web';
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

function readPalette(host) {
  const css = rootStyle();
  const token = (name, fallback) => resolveColor(css.getPropertyValue(name).trim(), fallback);
  const rem = Number.parseFloat(css.fontSize) || 16;
  const mobile = window.matchMedia('(max-width: 639.98px)').matches;
  const cardShell = host?.closest?.('.detail-score-card-shell');
  const cardHeight = Number.parseFloat(
    cardShell ? getComputedStyle(cardShell).getPropertyValue('--detail-card-height') : '',
  ) || undefined;
  return {
    lit: '#facc15',
    unlit: '#4b5563',
    cardUnlit: token('--color-text-muted', '#a1a1aa'),
    muted: token('--color-text-muted', '#a1a1aa'),
    text: token('--color-text', '#fafafa'),
    textSecondary: token('--color-text-secondary', '#d4d4d8'),
    surface: token('--color-surface', '#27272a'),
    border: token('--color-gray-700', '#3f3f46'),
    chipBackground: 'rgba(245, 158, 11, 0.07)',
    chipBorder: 'rgba(251, 191, 36, 0.18)',
    chipHoverBackground: 'rgba(245, 158, 11, 0.11)',
    chipHoverBorder: 'rgba(252, 211, 77, 0.28)',
    starBadgeBackground: 'rgba(245, 158, 11, 0.14)',
    starBadgeBorder: 'rgba(251, 191, 36, 0.28)',
    cardBackground: 'rgba(251, 191, 36, 0.10)',
    cardBorder: 'rgba(251, 191, 36, 0.26)',
    cardHeight,
    compactUnratedBorder: 'rgba(156, 163, 175, 0.28)',
    compactUnratedBackground: 'rgba(107, 114, 128, 0.14)',
    compactUnratedHoverBackground: 'rgba(107, 114, 128, 0.20)',
    compactRatedBorder: 'rgba(251, 191, 36, 0.28)',
    compactRatedBackground: 'rgba(245, 158, 11, 0.14)',
    compactRatedHoverBackground: 'rgba(245, 158, 11, 0.20)',
    compactValue: '#fcd34d',
    compactFontSize: 12,
    compactIconSize: 14,
    focusStyle: {
      outlineStyle: 'solid',
      outlineWidth: 1,
      outlineColor: token('--color-accent', '#6366f1'),
    },
    fontSize: rem * (mobile ? 0.75 : 0.875),
    chipHeight: mobile ? 30 : 32,
    starBadgeSize: mobile ? 30 : 32,
    iconSize: mobile ? 14 : 16,
    paddingRight: mobile ? 6 : 12,
    gap: mobile ? 2 : 8,
    popupWidth: 250,
    popupRadius: 8,
    popupPadding: 12,
  };
}

function readLayout(host) {
  const detail = host.closest('.detail-carousel-grid, .detail-flat-top');
  if (!detail) return 'pill';
  if (window.innerWidth < 768) return 'card';
  const carousel = host.closest('.detail-carousel-grid')?.querySelector('#detail-carousel-wrap');
  return carousel && carousel.childElementCount > 0 ? 'card' : 'pill';
}

function readAnchor(host, layout) {
  const trigger = host.querySelector('[role="button"]') || host;
  const rect = trigger.getBoundingClientRect();
  const width = 250;
  const preferred = layout === 'compact' ? rect.right - width : rect.left;
  const left = Math.max(8, Math.min(preferred, window.innerWidth - width - 8));
  const top = rect.bottom + 92 > window.innerHeight
    ? Math.max(8, rect.top - 88)
    : rect.bottom + 8;
  return { left, top };
}

function star(lit, size = 20) {
  return RatingStarIcon(lit, size);
}

// The server keeps the legacy chip as the fallback; React replaces it on mount.
// The write stays on htmx so the server's out-of-band chip and card swaps apply.
function Adapter({ host, props }) {
  const [value, setValue] = useState(props.value ?? null);
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState(null);
  const [palette, setPalette] = useState(() => readPalette(host));
  const [layout, setLayout] = useState(() => props.layout || readLayout(host));
  const wrapper = host.parentElement;

  useEffect(() => {
    const button = wrapper?.querySelector(':scope > button');
    const card = wrapper?.querySelector(':scope > .detail-personal-score-card');
    const hidden = [button, card].filter(Boolean).map(element => [element, element.style.display]);
    hidden.forEach(([element]) => { element.style.display = 'none'; });
    return () => hidden.forEach(([element, display]) => { element.style.display = display; });
  }, [wrapper]);

  useEffect(() => {
    const update = () => { setPalette(readPalette(host)); setLayout(props.layout || readLayout(host)); };
    update();
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'style'] });
    const carousel = host.closest('.detail-carousel-grid')?.querySelector('#detail-carousel-wrap');
    if (carousel) observer.observe(carousel, { childList: true });
    const scheme = window.matchMedia('(prefers-color-scheme: dark)');
    const mobile = window.matchMedia('(max-width: 639.98px)');
    const narrow = window.matchMedia('(max-width: 767.98px)');
    scheme.addEventListener('change', update);
    mobile.addEventListener('change', update);
    narrow.addEventListener('change', update);
    return () => {
      observer.disconnect();
      scheme.removeEventListener('change', update);
      mobile.removeEventListener('change', update);
      narrow.removeEventListener('change', update);
    };
  }, [host, props.layout]);

  useEffect(() => {
    setValue(props.value ?? null);
  }, [props.value]);

  useEffect(() => {
    if (!open) return undefined;
    const dismiss = () => setOpen(false);
    window.addEventListener('resize', dismiss);
    window.addEventListener('detail-score-row-scroll', dismiss);
    return () => {
      window.removeEventListener('resize', dismiss);
      window.removeEventListener('detail-score-row-scroll', dismiss);
    };
  }, [open]);

  function onOpenChange(next) {
    if (next) setAnchor(readAnchor(host, layout));
    setOpen(next);
  }

  function onSelect(score) {
    setValue(score);
    window.htmx?.ajax('POST', props.postUrl, {
      source: host, swap: 'none',
      headers: { 'X-CSRFToken': props.csrfToken },
      values: { score: score ?? value, toggle: true },
    });
  }

  return <RatingChip
    value={value}
    scaleMax={props.scaleMax}
    displayValue={props.displayValue}
    cardDisplayValue={props.cardDisplayValue}
    derived={props.derived}
    layout={layout}
    labels={props.labels}
    renderStar={star}
    onSelect={onSelect}
    palette={palette}
    open={open}
    onOpenChange={onOpenChange}
    anchor={anchor} />;
}

export function mountRatingChip(host, props) {
  return mountRoot(host, <Adapter host={host} props={props} />);
}
