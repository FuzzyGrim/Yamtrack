import React, { useEffect, useState } from 'react';
import { mountRoot } from '../settings/root';
import { MusicView } from '../pages/MusicView';
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
  const token = (name, fallback) => resolveColor(css.getPropertyValue(name).trim(), fallback);
  return {
    card: token('--color-surface', '#27272a'),
    text: token('--color-text', '#fafafa'),
    textSecondary: token('--color-text-secondary', '#d4d4d8'),
    textMuted: token('--color-text-muted', '#a1a1aa'),
    border: token('--color-surface-border', '#3f3f46'),
    link: token('--color-link', '#818cf8'),
    star: '#facc15',
    placeholder: token('--color-surface-strong', '#3f3f46'),
    chipBackground: 'rgba(24, 24, 27, 0.92)',
    chipText: '#ffffff',
    statusColors: {
      Completed: '#10b981',
      'In progress': token('--color-accent', '#6366f1'),
      Paused: '#f59e0b',
      Planning: '#38bdf8',
      Dropped: token('--color-danger', '#ef4444'),
    },
    rating: { lit: '#facc15', unlit: '#52525b', muted: token('--color-text-muted', '#a1a1aa'), focusStyle: {} },
  };
}

function openRelation(relation) {
  if (relation.href) window.location.assign(relation.href);
}

// The web card renders an img even when the server has no artwork. Keep the
// release's person placeholder until a relation image is available.
const placeholder = `data:image/svg+xml,${encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 180"><g transform="translate(42 72) scale(1.5)" fill="none" stroke="#4b5563" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></g></svg>')}`;

function Grid({ props }) {
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
  return <MusicView part="relations" relations={props.relations.map(relation => ({ ...relation, image: relation.image || placeholder }))} labels={props.labels}
    palette={palette} onOpenRelation={openRelation} />;
}

export function mountMusicRelations(host, props) {
  return mountRoot(host, <Grid props={props} />);
}
