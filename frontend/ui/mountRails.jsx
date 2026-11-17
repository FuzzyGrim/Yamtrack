import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { View } from 'react-native';
import { mountRoot } from '../settings/root';
import { Rail } from './Rail';
import { rootStyle } from './rootStyle.web';

const pendingScroll = new Map();
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
    surfaceHover: color('--color-surface-strong', '#3f3f46'),
    border: color('--color-surface-border', '#3f3f46'),
    accent: color('--color-accent', '#6366f1'),
    arrow: color('--color-text-secondary', '#d4d4d8'),
    arrowDisabled: color('--color-surface-muted', '#52525b'),
    loading: color('--color-accent', '#6366f1'),
    focus: color('--color-accent', '#6366f1'),
  };
}

function scrollNode(ref) {
  const value = ref.current;
  if (!value) return null;
  if (typeof value.getScrollableNode === 'function') return value.getScrollableNode();
  if (typeof value.getNativeScrollRef === 'function') return value.getNativeScrollRef();
  return value.nodeType === 1 ? value : null;
}

function isSentinel(node) {
  return node.hasAttribute('data-home-row-sentinel');
}

function captureRow(host, rowId) {
  const row = host.querySelector('[data-home-row="true"]');
  if (!row) return { nodes: [], width: 176, gap: 16, scrollLeft: 0 };
  const nodes = [...row.children].filter(node => !isSentinel(node));
  const first = nodes[0];
  const width = first ? Math.round(first.getBoundingClientRect().width) || 176 : 176;
  const gapValue = Number.parseFloat(getComputedStyle(row).columnGap || '');
  const scrollLeft = pendingScroll.has(rowId) ? pendingScroll.get(rowId) : row.scrollLeft;
  pendingScroll.delete(rowId);
  return { nodes, width, gap: Number.isFinite(gapValue) ? gapValue : 16, scrollLeft };
}

function activateNode(node) {
  if (!node) return;
  try { window.htmx?.process?.(node); } catch (_) {}
  try { window.Alpine?.initTree?.(node); } catch (_) {}
  try {
    document.dispatchEvent(new CustomEvent('htmx:load', { detail: { elt: node } }));
  } catch (_) {}
}

function NodeSlot({ node }) {
  const ref = useRef(null);
  useLayoutEffect(() => {
    if (!node || !ref.current) return;
    if (node.parentNode !== ref.current) ref.current.replaceChildren(node);
  }, [node]);
  return <View ref={ref} style={slotStyle} />;
}

const slotStyle = { width: '100%' };

function readCollapsed(key, fallback) {
  if (!key) return false;
  try {
    const stored = window.localStorage.getItem(key);
    return !(stored !== fallback);
  } catch (_) {
    return fallback === 'true';
  }
}

function fragmentUrl(hxUrl, offset, seed) {
  const url = new URL(hxUrl, window.location.origin);
  url.searchParams.set('offset', String(offset));
  if (seed) url.searchParams.set('seed', String(seed));
  else url.searchParams.delete('seed');
  return url.toString();
}

function RailAdapter({ host, props, initialItems, itemWidth, itemGap, initialScrollLeft }) {
  const scrollRef = useRef(null);
  const alive = useRef(true);
  const seed = useRef(props.seed || 0);
  const [palette, setPalette] = useState(readPalette);
  const [items, setItems] = useState(initialItems);
  const [loadedCount, setLoadedCount] = useState(props.loadedCount || initialItems.length);
  const [total, setTotal] = useState(props.total || initialItems.length);
  const [hasMore, setHasMore] = useState(Boolean(props.hasMore && props.hxUrl));
  const [loadingMore, setLoadingMore] = useState(false);
  const [collapsed, setCollapsed] = useState(() => readCollapsed(props.collapseKey, props.collapseDefault));

  useEffect(() => () => { alive.current = false; }, []);

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

  useEffect(() => {
    const node = scrollNode(scrollRef);
    if (node && initialScrollLeft) node.scrollLeft = initialScrollLeft;
  }, [initialScrollLeft]);

  useEffect(() => {
    const refresh = () => {
      const node = scrollNode(scrollRef);
      pendingScroll.set(props.rowId, node ? node.scrollLeft : 0);
      if (!window.htmx) {
        window.location.reload();
        return;
      }
      window.htmx.ajax('GET', fragmentUrl(props.hxUrl, 0, 0), { target: host, swap: 'outerHTML' });
    };
    host.addEventListener('railsRowRefresh', refresh);
    return () => host.removeEventListener('railsRowRefresh', refresh);
  }, [host, props.rowId, props.hxUrl]);

  const loadMore = useCallback(async () => {
    if (!props.hxUrl || loadingMore || !hasMore) return;
    setLoadingMore(true);
    try {
      const response = await fetch(fragmentUrl(props.hxUrl, loadedCount, seed.current), {
        headers: { 'HX-Request': 'true' },
        credentials: 'same-origin',
      });
      if (!response.ok) {
        setHasMore(false);
        return;
      }
      const html = await response.text();
      const freshSeed = response.headers.get('X-Home-Row-Seed');
      const freshTotal = response.headers.get('X-Home-Row-Total');
      if (freshSeed != null) seed.current = Number(freshSeed) || 0;
      const doc = new DOMParser().parseFromString(html, 'text/html');
      const row = doc.querySelector(`#media-grid-row-${CSS.escape(String(props.rowId))}`) || doc.body;
      const nodes = [...row.children].filter(node => !isSentinel(node));
      if (!nodes.length) {
        setHasMore(false);
        return;
      }
      const nextLoaded = loadedCount + nodes.length;
      const nextTotal = freshTotal != null ? Number(freshTotal) : total;
      setItems(current => [
        ...current,
        ...nodes.map((node, index) => ({ id: `more-${loadedCount + index}`, node })),
      ]);
      setLoadedCount(nextLoaded);
      if (Number.isFinite(nextTotal)) setTotal(nextTotal);
      setHasMore(Number.isFinite(nextTotal) ? nextLoaded < nextTotal : true);
      window.requestAnimationFrame(() => {
        if (alive.current) nodes.forEach(activateNode);
      });
    } catch (_) {
      setHasMore(false);
    } finally {
      if (alive.current) setLoadingMore(false);
    }
  }, [hasMore, loadedCount, loadingMore, props.hxUrl, props.rowId, total]);

  const sort = props.sortUrl && window.htmx ? () => {
    window.htmx.ajax('POST', props.sortUrl, {
      target: host,
      swap: 'outerHTML',
      headers: { 'X-CSRFToken': props.csrfToken },
    });
  } : undefined;

  const viewAll = props.viewAll ? {
    label: props.viewAll.label,
    external: props.viewAll.external,
    onPress: () => (props.viewAll.external
      ? window.open(props.viewAll.href, '_blank', 'noopener,noreferrer')
      : window.location.assign(props.viewAll.href)),
  } : undefined;

  function toggleCollapsed(next) {
    setCollapsed(next);
    if (!props.collapseKey) return;
    try {
      window.localStorage.setItem(props.collapseKey, next ? 'false' : 'true');
    } catch (_) {}
  }

  const labels = props.labels || {};
  return <Rail
    items={items}
    renderItem={item => <NodeSlot node={item.node} />}
    title={props.title}
    titleDetail={props.titleDetail}
    summary={props.summary}
    summaryInline={props.summaryInline}
    direction={props.direction}
    total={total}
    loadedCount={loadedCount}
    hasMore={hasMore}
    loadingMore={loadingMore}
    itemWidth={itemWidth}
    itemGap={itemGap}
    loadMoreThreshold={1152}
    onLoadMore={props.hxUrl ? loadMore : undefined}
    onSort={sort}
    sortLabel={props.sortLabel}
    onTitlePress={props.titleHref ? () => window.location.assign(props.titleHref) : undefined}
    viewAll={viewAll}
    collapsible={Boolean(props.collapseKey)}
    collapsed={props.collapseKey ? collapsed : undefined}
    onCollapsedChange={props.collapseKey ? toggleCollapsed : undefined}
    loadingLabel={labels.loadingMore}
    collapseLabel={labels.toggleSection}
    scrollLeftLabel={labels.scrollLeft}
    scrollRightLabel={labels.scrollRight}
    accessibilityLabel={props.titleDetail ? `${props.title} • ${props.titleDetail}` : props.title}
    palette={palette}
    scrollRef={scrollRef}
    testID={`rails-row-${props.rowId}`} />;
}

export function mountRails(host, props) {
  const { nodes, width, gap, scrollLeft } = captureRow(host, props.rowId);
  const items = nodes.map((node, index) => ({ id: `item-${index}`, node }));
  const dispose = mountRoot(host, <RailAdapter host={host} props={props} initialItems={items}
    itemWidth={width} itemGap={gap} initialScrollLeft={scrollLeft} />);
  nodes.forEach(activateNode);
  return dispose;
}
