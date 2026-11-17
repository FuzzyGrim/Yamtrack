import React, { useEffect, useState } from 'react';
import { Spinner as SharedSpinner } from './Spinner';
import { rootStyle } from './rootStyle.web';

// React Native Web does not parse all CSS color syntaxes (including Tailwind's
// oklch). Canvas resolves them to concrete channels without inserting DOM nodes.
const canvas = document.createElement('canvas');
canvas.width = canvas.height = 1;
const context = canvas.getContext('2d');
function resolveColor(value, fallback) {
  if (!context) return fallback;
  context.clearRect(0, 0, 1, 1);
  context.fillStyle = fallback;
  context.fillStyle = value;
  context.fillRect(0, 0, 1, 1);
  const [r, g, b, a] = context.getImageData(0, 0, 1, 1).data;
  return `rgba(${r}, ${g}, ${b}, ${a / 255})`;
}

// Translate the existing document theme into the concrete native color contract.
export function Spinner({ size = 16, tone = 'accent' }) {
  const readColor = () => {
    if (tone === 'white') return '#fff';
    const fallback = tone === 'muted' ? '#adb5bd' : '#6366f1';
    const value = rootStyle().getPropertyValue(tone === 'muted' ? '--color-text-muted' : '--color-indigo-500').trim();
    return resolveColor(value, fallback);
  };
  const [color, setColor] = useState(readColor);
  useEffect(() => {
    const update = () => setColor(readColor());
    update();
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'style'] });
    const scheme = window.matchMedia('(prefers-color-scheme: dark)');
    scheme.addEventListener('change', update);
    return () => { observer.disconnect(); scheme.removeEventListener('change', update); };
  }, [tone]);
  return <SharedSpinner size={size} color={color} />;
}
