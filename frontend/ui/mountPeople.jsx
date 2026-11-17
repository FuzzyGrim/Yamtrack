import React, { useEffect, useState } from 'react';
import { mountRoot } from '../settings/root';
import { PeopleView } from '../pages/PeopleView';
import { MediaCard } from './MediaCard.web';
import { shelfCardWidth } from './HomeShelves';
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
    background: color('--color-page-bg', '#18181b'),
    surface: color('--color-surface', '#27272a'),
    surfaceStrong: color('--color-surface-strong', '#3f3f46'),
    surfaceMuted: color('--color-surface-muted', '#52525b'),
    border: color('--color-surface-border', '#3f3f46'),
    text: color('--color-text', '#fafafa'),
    textSecondary: color('--color-text-secondary', '#d4d4d8'),
    textMuted: color('--color-text-muted', '#a1a1aa'),
    accent: color('--color-accent', '#6366f1'),
    accentStrong: color('--color-accent-strong', '#4f46e5'),
    accentContrast: '#ffffff',
    link: color('--color-link', '#818cf8'),
    star: '#facc15',
    placeholder: color('--color-surface-strong', '#3f3f46'),
    chipBackground: 'rgba(24, 24, 27, 0.92)',
    chipText: '#ffffff',
    radius: 8,
    radiusSmall: 6,
  };
}

const personActionPalette = {
  radius: 6,
  primary: {
    background: 'var(--color-amber-600)',
    pressed: 'var(--color-amber-700)',
    hovered: 'var(--color-amber-700)',
    text: '#ffffff',
    border: 'transparent',
  },
};

function openExternal(url) {
  if (url) window.open(url, '_blank', 'noopener,noreferrer');
}

function openInternal(url) {
  if (url) window.location.assign(url);
}

function readCollapsed(key) {
  if (!key) return false;
  try {
    return window.localStorage.getItem(key) === 'false';
  } catch (_) {
    return false;
  }
}


function CastRail({ props, palette }) {
  const [collapsed, setCollapsed] = useState(() => readCollapsed(props.collapseKey));
  const [width, setWidth] = useState(() => shelfCardWidth(window.innerWidth, document.body.dataset.mobileGrid));
  useEffect(() => {
    const update = () => setWidth(shelfCardWidth(window.innerWidth, document.body.dataset.mobileGrid));
    window.addEventListener('resize', update);
    const observer = new MutationObserver(update);
    observer.observe(document.body, { attributes: true, attributeFilter: ['data-mobile-grid'] });
    return () => { window.removeEventListener('resize', update); observer.disconnect(); };
  }, []);
  useEffect(() => {
    if (!props.collapseKey) return;
    try {
      window.localStorage.setItem(props.collapseKey, collapsed ? 'false' : 'true');
    } catch (_) {}
  }, [props.collapseKey, collapsed]);
  const viewAll = props.viewAll ? {
    label: props.viewAll.label,
    onPress: () => openInternal(props.viewAll.href),
  } : undefined;
  return <PeopleView
    part="cast"
    items={props.items}
    title={props.title}
    labels={props.labels}
    palette={palette}
    web
    castWidth={width}
    renderPersonCard={person => <MediaCard variant="grid" model={{ title: person.name, href: person.href, recommend: !person.href,
      shape: 'poster', image: { src: person.imageUrl || person.imageFallback },
      lines: person.cardLines ? person.cardLines.map(line => ({ parts: [{ text: line.text }], dormant: line.dormant || person.subtitleAlways }))
        : [person.subtitle || person.role].filter(Boolean).map(text => ({ parts: [{ text }] })) }} />}
    viewAll={viewAll}
    collapsible={Boolean(props.collapseKey)}
    collapsed={props.collapseKey ? collapsed : undefined}
    onCollapsedChange={props.collapseKey ? setCollapsed : undefined}
    onOpenPerson={entry => openInternal(entry.href)} />;
}

function Adapter({ props }) {
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

  if (props.kind === 'cast') {
    return <CastRail props={props} palette={palette} />;
  }

  if (props.kind === 'extras') {
    return <PeopleView
      part="extras"
      gameLengths={props.gameLengths}
      youtubeWatch={props.youtubeWatch}
      labels={props.labels}
      palette={palette}
      onOpenSource={openExternal} />;
  }

  if (props.kind === 'studio') {
    return <PeopleView
      part="hero"
      kind="studio"
      studio={props.studio}
      counts={props.counts}
      labels={props.labels}
      palette={palette}
      headingLevel={props.headingLevel}
      sourcePlacement={props.sourcePlacement}
      showBiography={props.showBiography}
      onOpenWebsite={openExternal} />;
  }

  return <PeopleView
    part="hero"
    kind="person"
    person={props.person}
    counts={props.counts}
    labels={props.labels}
    palette={palette}
    headingLevel={props.headingLevel}
    showBiography={props.showBiography}
    canViewHistory={props.canViewHistory}
    actionPalette={personActionPalette}
    onOpenSource={openExternal}
    onViewHistory={props.historyUrl ? () => window.location.assign(props.historyUrl) : undefined} />;
}

export function mountPeople(host, props) {
  return mountRoot(host, <Adapter props={props} />);
}
