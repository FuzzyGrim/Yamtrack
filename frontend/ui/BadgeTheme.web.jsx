import React, { useEffect, useState } from 'react';
import { Badge as SharedBadge } from './Badge';
import { rootStyle } from './rootStyle.web';

const canvas = document.createElement('canvas');
canvas.width = canvas.height = 1;
const ctx = canvas.getContext('2d');

// Resolve the existing theme contract to concrete native color and size values.
function readAppearance(size, tone, nowrap, hovered) {
  const css = rootStyle();
  const token = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
  const color = (value, fallback) => {
    if (/^(?:#|rgba?\()/i.test(value.trim())) return value.trim();
    if (!ctx) return fallback;
    ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = fallback; ctx.fillStyle = value;
    ctx.fillRect(0, 0, 1, 1);
    const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data;
    return `rgba(${r}, ${g}, ${b}, ${a / 255})`;
  };
  const rem = Number.parseFloat(css.fontSize) || 16;
  const indigo = token('--color-indigo-600', 'oklch(51.1% 0.262 276.966)');
  const hues = {
    'green-soft': token('--color-green-500', 'oklch(72.3% 0.219 149.579)'),
    'indigo-soft': token('--color-indigo-500', 'oklch(58.5% 0.233 277.117)'),
    'emerald-soft': token('--color-emerald-500', 'oklch(69.6% 0.17 162.48)'),
    'amber-soft': token('--color-amber-500', 'oklch(76.9% 0.188 70.08)'),
  };
  const hue = hues[tone];
  const backgrounds = {
    indigo, rose: token('--color-rose-600', 'oklch(58.6% 0.253 17.585)'),
    violet: token('--color-violet-600', 'oklch(54.1% 0.281 293.009)'),
    neutral: token('--color-surface-strong', 'transparent'),
    tint: `color-mix(in oklab, ${indigo} 15%, transparent)`,
    genre: token(hovered ? '--color-genre-badge-hover-bg' : '--color-genre-badge-bg', 'transparent'),
  };
  const text = {
    indigo: '#fff', rose: '#fff', violet: '#fff', neutral: token('--color-text', '#000'),
    outline: token('--color-text-secondary', '#000'), tint: token('--color-indigo-500', '#6366f1'),
    genre: token('--color-genre-badge-text', '#000'), 'green-soft': token('--color-text-secondary', '#000'),
    'indigo-soft': token('--color-link', '#6366f1'), 'emerald-soft': token('--color-emerald-300', '#6ee7b7'),
    'amber-soft': token('--color-amber-300', '#fcd34d'),
  };
  const border = tone === 'outline' ? token('--color-surface-border', 'transparent') : tone === 'genre' ? token('--color-genre-badge-border', 'transparent') : hue ? `color-mix(in oklab, ${hue} 30%, transparent)` : 'transparent';
  const background = hue ? `color-mix(in oklab, ${hue} ${tone === 'emerald-soft' || tone === 'amber-soft' ? 15 : 10}%, transparent)` : backgrounds[tone] || 'transparent';
  const sizes = { xs: [11, rem * .25, rem * .5], sm: [rem * .75, rem * .125, rem * .5], md: [rem * .875, rem * .25, rem * .75], tag: [rem * .75, 0, rem * .625] };
  const [fontSize, vertical, horizontal] = sizes[size] || sizes.sm;
  const appearance = {
    color: color(text[tone] || text.outline, '#000'), backgroundColor: color(background, 'transparent'),
    borderColor: color(border, 'transparent'), borderWidth: tone === 'outline' || tone === 'genre' || hue ? 1 : 0,
    borderRadius: 9999, paddingVertical: vertical, paddingHorizontal: horizontal,
    fontSize, fontFamily: css.fontFamily, ...(tone === 'genre' ? { fontWeight: '500' } : {}),
    ...(size === 'sm' || size === 'tag' ? { lineHeight: rem } : size === 'md' ? { lineHeight: rem * 1.25 } : {}),
    ...(size === 'tag' ? { height: rem * 1.5, display: 'inline-flex', alignItems: 'center' } : {}),
    ...(nowrap ? { flexShrink: 0, whiteSpace: 'nowrap' } : {}),
    ...(tone === 'genre' ? { transitionProperty: 'background-color', transitionDuration: '0.2s', transitionTimingFunction: 'cubic-bezier(0.4, 0, 0.2, 1)' } : {}),
  };
  return appearance;
}

export function Badge({ label, size = 'sm', tone = 'outline', href = '', nowrap = false }) {
  const [hovered, setHovered] = useState(false);
  const [appearance, setAppearance] = useState(() => readAppearance(size, tone, nowrap, hovered));
  useEffect(() => {
    const update = () => setAppearance(readAppearance(size, tone, nowrap, hovered));
    update();
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'style'] });
    const scheme = window.matchMedia('(prefers-color-scheme: dark)');
    scheme.addEventListener('change', update);
    return () => { observer.disconnect(); scheme.removeEventListener('change', update); };
  }, [size, tone, nowrap, hovered]);
  return href ? <a href={href} style={{ textDecoration: 'none' }} onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}><SharedBadge label={label} size={size} tone={tone} appearance={appearance} /></a> : <SharedBadge label={label} size={size} tone={tone} appearance={appearance} />;
}
