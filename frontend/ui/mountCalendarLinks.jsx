import React, { useState } from 'react';
import { mountRoot } from '../settings/root';
import { CalendarView } from '../pages/CalendarView';
import { CalendarLinks } from './CalendarLinks';
import { rootStyle } from './rootStyle.web';

function readPalette() {
  const css = rootStyle();
  const token = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
  return {
    surface: token('--color-surface', '#27272a'),
    surfaceStrong: token('--color-surface-strong', '#3f3f46'),
    surfaceDim: token('--color-surface-dim', '#1f1f23'),
    border: token('--color-border', '#3f3f46'),
    text: token('--color-text', '#fafafa'),
    textSecondary: token('--color-text-secondary', '#d4d4d8'),
    textMuted: token('--color-text-muted', '#a1a1aa'),
    accent: token('--color-accent', '#6366f1'),
    accentStrong: token('--color-accent-strong', '#4f46e5'),
    accentText: '#ffffff',
    link: token('--color-link', '#818cf8'),
  };
}

function Adapter({ props }) {
  const [palette] = useState(readPalette);

  async function copyLink(url) {
    if (!navigator.clipboard?.writeText) {
      throw new Error('Clipboard is unavailable');
    }
    await navigator.clipboard.writeText(url);
  }

  function download(url) {
    if (url) {
      window.location.assign(url);
    }
  }

  return <CalendarView
    title=""
    subtitle=""
    chrome={false}
    sections={['links']}
    labels={{ linksTitle: '', linksCopy: '' }}
    palette={palette}
    links={<CalendarLinks
      {...props}
      embedded
      palette={palette}
      onCopyLink={copyLink}
      onDownload={download} />} />;
}

export function mountCalendarLinks(host, props) {
  return mountRoot(host, <Adapter props={props} />);
}
