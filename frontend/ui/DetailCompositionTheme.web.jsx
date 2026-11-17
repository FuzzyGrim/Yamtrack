import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { DetailView } from '../pages/DetailView';
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
    border: color('--color-surface-border', '#3f3f46'),
    text: color('--color-text', '#fafafa'),
    textSecondary: color('--color-text-secondary', '#d4d4d8'),
    textMuted: color('--color-text-muted', '#a1a1aa'),
    accent: color('--color-accent', '#6366f1'),
    accentStrong: color('--color-accent-strong', '#4f46e5'),
    accentContrast: '#ffffff',
    link: color('--color-link', '#818cf8'),
    danger: color('--color-danger', '#ef4444'),
    placeholder: color('--color-surface-strong', '#3f3f46'),
  };
}

const DETAIL_CSS = `
  #detail-secondary-content .detail-sidebar-genres a {
    display: inline-flex;
    align-items: flex-start;
    line-height: 0;
  }
  #detail-secondary-content .detail-media-grid .media-card > .media-card-visual {
    position: relative;
    height: auto;
  }
  #detail-secondary-content .detail-media-grid .media-card-poster {
    height: auto !important;
    aspect-ratio: auto !important;
  }
`;

const SYNOPSIS_SUFFIX = ' Read more';

function collapsed(value) {
  return (value || '').replace(/\s+/g, ' ').trim();
}

// Browser synopsis keeps the release typography and the inline phone
// "Read more" preview: measure the full text at the rendered width so the
// visible excerpt ends where the link fits.
function WebSynopsis({ text, previewLines = 3, labels, mobile }) {
  const body = text || '';
  const [expanded, setExpanded] = useState(false);
  const [measured, setMeasured] = useState({ clamped: true, preview: collapsed(body) });
  const rootRef = useRef(null);
  const measureRef = useRef(null);

  useLayoutEffect(() => {
    const root = rootRef.current;
    const measure = measureRef.current;
    if (!root || !measure || !body) return undefined;
    let frame = 0;

    const compute = () => {
      const full = mobile ? collapsed(body) : body;
      measure.style.whiteSpace = mobile ? 'normal' : 'pre-line';
      measure.textContent = full;
      const lineHeight = Number.parseFloat(window.getComputedStyle(measure).lineHeight) || 24;
      const maxHeight = Math.ceil(lineHeight * previewLines);
      const clamped = measure.scrollHeight > maxHeight + 1;
      let preview = full;
      if (clamped && mobile) {
        const source = collapsed(body);
        let low = 0;
        let high = source.length;
        let best = '';
        while (low <= high) {
          const mid = Math.floor((low + high) / 2);
          let candidate = source.slice(0, mid).trimEnd();
          const lastSpace = candidate.lastIndexOf(' ');
          if (lastSpace > 0) candidate = candidate.slice(0, lastSpace).trimEnd();
          measure.textContent = candidate ? `${candidate}...${SYNOPSIS_SUFFIX}` : source;
          if (measure.scrollHeight <= maxHeight) {
            best = candidate;
            low = mid + 1;
          } else {
            high = mid - 1;
          }
        }
        preview = best ? best.replace(/[\s.,;:!?-]+$/u, '') : source;
        measure.textContent = full;
      }
      setMeasured(current => (
        current.clamped === clamped && current.preview === preview
          ? current
          : { clamped, preview }
      ));
    };

    frame = window.requestAnimationFrame(compute);
    const observer = new ResizeObserver(compute);
    observer.observe(root);
    return () => {
      window.cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [body, mobile, previewLines]);

  if (!body) return null;

  const toggleClass = 'text-[13px] font-medium text-[var(--color-link)] hover:text-[var(--color-link-hover)] transition-colors cursor-pointer';
  return <div ref={rootRef} className="mb-6">
    <div ref={measureRef} aria-hidden="true"
      className="pointer-events-none invisible absolute inset-x-0 top-0"
      style={{ fontSize: 16, lineHeight: '24px', textWrap: 'pretty' }} />
    {mobile ? <>
      <p className={`text-[var(--color-text-secondary)] text-pretty${!expanded ? ' line-clamp-3' : ''}`}
        style={{ fontSize: 16, lineHeight: '24px' }}>
        {expanded ? collapsed(body) : measured.preview}
        {!expanded && measured.clamped
          ? <>... <button type="button" onClick={() => setExpanded(true)}
            className={`inline whitespace-nowrap align-baseline ml-1 ${toggleClass}`}>
            {labels.readMore || 'Read more'}
          </button></>
          : null}
      </p>
      {expanded && measured.clamped ? <button type="button" onClick={() => setExpanded(false)}
        className={`mt-2 inline-flex ${toggleClass}`}>
        {labels.showLess || 'Show less'}
      </button> : null}
    </> : <>
      <p className={`text-[var(--color-text-secondary)] text-pretty${measured.clamped && !expanded ? ' line-clamp-3' : ''}`}
        style={{ fontSize: 16, lineHeight: '24px', whiteSpace: 'pre-line' }}>{body}</p>
      {measured.clamped ? <button type="button" onClick={() => setExpanded(value => !value)}
        className="mt-2 inline-flex text-sm font-medium text-[var(--color-link)] hover:text-[var(--color-link-hover)] transition-colors cursor-pointer">
        {expanded ? labels.showLess || 'Show less' : labels.readMore || 'Read more'}
      </button> : null}
    </>}
  </div>;
}

function scopeFor(host) {
  return host.closest('.detail-flat-top, .detail-carousel-grid') || host.parentElement;
}

function Adapter({ host, props }) {
  const [palette, setPalette] = useState(readPalette);
  const [mobile, setMobile] = useState(() => window.matchMedia('(max-width: 639.98px)').matches);

  useLayoutEffect(() => {
    const part = props.part === 'header' ? 'header' : 'synopsis';
    const scope = scopeFor(host);
    const legacy = scope ? scope.querySelectorAll(`[data-detail-composition-legacy="${part}"]`) : [];
    const hidden = [...legacy].map(element => [element, element.style.display]);
    hidden.forEach(([element]) => { element.style.display = 'none'; });
    return () => hidden.forEach(([element, display]) => { element.style.display = display; });
  }, [host, props.part]);

  useEffect(() => {
    const update = () => setPalette(readPalette());
    update();
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'style'] });
    const scheme = window.matchMedia('(prefers-color-scheme: dark)');
    const narrow = window.matchMedia('(max-width: 639.98px)');
    const updateWidth = () => setMobile(narrow.matches);
    scheme.addEventListener('change', update);
    narrow.addEventListener('change', updateWidth);
    return () => {
      observer.disconnect();
      scheme.removeEventListener('change', update);
      narrow.removeEventListener('change', updateWidth);
    };
  }, []);

  if (props.part === 'header') {
    return <DetailView part="header" model={props} labels={props.labels} palette={palette} showArtwork={false} />;
  }
  return <>
    <style>{DETAIL_CSS}</style>
    <DetailView part="synopsis" model={props} labels={props.labels} palette={palette}
      renderSynopsis={({ model }) => <WebSynopsis
        text={model.synopsis?.text}
        previewLines={model.synopsis?.previewLines}
        labels={model.labels}
        mobile={mobile} />} />
  </>;
}

export function DetailCompositionTheme({ host, props }) {
  return <Adapter host={host} props={props} />;
}
