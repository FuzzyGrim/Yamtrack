import React, { useEffect, useRef, useState } from 'react';
import { ProgressChanger as SharedProgressChanger } from './ProgressChanger';

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

// Resolve the existing document theme and responsive card rules to native values.
function readAppearance(host, unit) {
  const css = getComputedStyle(document.documentElement);
  const token = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
  const color = (name, fallback) => resolveColor(token(name, fallback), fallback);
  const rem = Number.parseFloat(css.fontSize) || 16;
  const fontSize = rem * 0.75;
  const family = css.fontFamily || 'sans-serif';
  if (ctx) ctx.font = `500 ${fontSize}px ${family}`;
  const ch = ctx ? ctx.measureText('0').width : fontSize * 0.6;
  const quick = host.closest('[data-quick-season-mobile]')?.getAttribute('data-quick-season-mobile')
    ?? document.body.dataset.quickSeasonMobile;
  const mobileGrid = host.closest('[data-mobile-grid]')?.getAttribute('data-mobile-grid')
    ?? document.body.dataset.mobileGrid;
  const mobile = window.matchMedia('(max-width: 768px)').matches;
  return {
    text: color('--color-info-text', '#adb5bd'),
    icon: color('--color-text', '#fafafa'),
    disabledIcon: color('--color-text', '#fafafa'),
    button: color('--color-surface-dim', '#3f3f46'),
    buttonHover: color('--color-surface-hover', '#52525b'),
    flash: resolveColor(token('--color-indigo-500', '#6366f1'), '#6366f1').replace(/[\d.]+\)$/, '0.55)'),
    focusStyle: {
      outlineStyle: 'solid',
      outlineWidth: 2,
      outlineColor: color('--color-accent', '#6366f1'),
      outlineOffset: 2,
    },
    buttonSize: 24,
    radius: 6,
    iconSize: 12,
    fontSize,
    minWidth: ch * 4,
    compact: window.innerWidth < 600,
    showButtons: !mobile || quick === 'true',
    unit: mobile && mobileGrid === 'compact' ? '' : unit,
  };
}

// The height this control takes under a card, so Home's waiting rows can
// reserve it before their cards arrive.
export function progressControlHeight(host = document.body) {
  const appearance = readAppearance(host, '');
  return appearance.showButtons ? appearance.buttonSize : Math.ceil(appearance.fontSize * 1.25);
}

export function ProgressChanger({ host, props }) {
  const [appearance, setAppearance] = useState(() => readAppearance(host, props.unit));
  const [busy, setBusy] = useState(null);
  const [flash, setFlash] = useState(null);
  const target = useRef(null);
  useEffect(() => {
    const update = () => setAppearance(readAppearance(host, props.unit));
    update();
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'style'] });
    observer.observe(document.body, { attributes: true, attributeFilter: ['data-quick-season-mobile', 'data-mobile-grid'] });
    const scheme = window.matchMedia('(prefers-color-scheme: dark)');
    const mobile = window.matchMedia('(max-width: 768px)');
    scheme.addEventListener('change', update);
    mobile.addEventListener('change', update);
    return () => {
      observer.disconnect();
      scheme.removeEventListener('change', update);
      mobile.removeEventListener('change', update);
    };
  }, [host, props.unit]);
  useEffect(() => {
    target.current = document.querySelector(`#${props.target}`);
    const operation = target.current?.dataset.lastOperation;
    if (!operation) return;
    delete target.current.dataset.lastOperation;
    setFlash(operation);
    const timer = setTimeout(() => setFlash(null), 600);
    return () => clearTimeout(timer);
  }, [props.target]);
  function onAdjust(operation) {
    setBusy(operation);
    target.current = document.querySelector(`#${props.target}`);
    if (target.current) target.current.dataset.lastOperation = operation;
    const values = { operation };
    if (operation === 'increase' && props.watchOperationId) values.watch_operation_id = props.watchOperationId;
    const request = window.htmx?.ajax('POST', props.url, {
      target: `#${props.target}`, swap: 'innerHTML',
      headers: { 'X-CSRFToken': props.csrfToken }, values,
    });
    Promise.resolve(request).then(() => setBusy(null), () => setBusy(null));
  }
  return <SharedProgressChanger
    value={props.value}
    max={props.max}
    unit={appearance.unit}
    showMax={props.showMax}
    canDecrease={props.canDecrease}
    canIncrease={props.canIncrease}
    busyOperation={busy}
    flashOperation={flash}
    decreaseLabel={props.decreaseLabel}
    increaseLabel={props.increaseLabel}
    onAdjust={onAdjust}
    palette={appearance} />;
}
