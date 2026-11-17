// History's first view: only about two screens of cards mount with the page,
// and a waiting day loads itself once when it comes near the screen. Renders
// the real shared HistoryFeed with react-dom over a minimal DOM.
import assert from 'node:assert/strict';
import { readFile, mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { installFakeDom } from './test-support/fakeDom.mjs';

const root = resolve(import.meta.dirname, '..');
const support = join(root, 'scripts', 'test-support');

// Counts each MediaCard render by title, without changing the component.
const countRenders = {
  name: 'count-media-card-renders',
  setup(b) {
    b.onLoad({ filter: /frontend\/ui\/MediaCard\.jsx$/ }, async args => {
      const source = await readFile(args.path, 'utf8');
      const start = source.indexOf('export const MediaCard = React.memo(function MediaCard(');
      assert.ok(start >= 0, 'MediaCard is memoized');
      const body = source.indexOf('}) {\n', start) + '}) {\n'.length;
      return {
        contents: `${source.slice(0, body)}  globalThis.__cardRenders?.push(model?.title);\n${source.slice(body)}`,
        loader: 'jsx',
      };
    });
  },
};

async function load() {
  const dir = await mkdtemp(join(tmpdir(), 'history-first-view-'));
  const entry = join(dir, 'entry.jsx');
  await writeFile(entry, `
    export { default as React, act } from 'react';
    export { createRoot } from 'react-dom/client';
    export { HistoryFeed } from ${JSON.stringify(join(root, 'frontend/ui/HistoryFeed.jsx'))};
    export { firstViewDayCount, HISTORY_FIRST_VIEW_ENTRIES } from ${JSON.stringify(join(root, 'frontend/ui/historyFirstView.js'))};
    export * as anchors from ${JSON.stringify(join(root, 'frontend/ui/historyScrollAnchor.web.js'))};
  `);
  const outfile = join(dir, 'out.mjs');
  await build({
    entryPoints: [entry], bundle: true, format: 'esm', platform: 'node', outfile, jsx: 'automatic',
    nodePaths: [join(root, 'node_modules')],
    resolveExtensions: ['.native.jsx', '.native.js', '.jsx', '.js', '.mjs'],
    alias: {
      'react-native': join(support, 'reactNativeStub.mjs'),
      'react-native-svg': join(support, 'reactNativeSvgStub.mjs'),
      'expo-image': join(support, 'expoImageStub.mjs'),
    },
    define: { 'process.env.NODE_ENV': '"development"' },
    plugins: [countRenders],
    logLevel: 'error',
  });
  const mod = await import(pathToFileURL(outfile).href);
  await rm(dir, { recursive: true, force: true });
  return mod;
}

const doc = installFakeDom();
const { React, act, createRoot, HistoryFeed, firstViewDayCount, HISTORY_FIRST_VIEW_ENTRIES, anchors } = await load();
const tick = () => new Promise(done => setTimeout(done, 10));

const entry = (day, index) => ({
  key: `${day}-${index}`, mediaType: 'movie', title: `${day}-${index}`, href: `/m/${day}/${index}`,
  image: { src: null, fallback: '', source: 'primary' }, canViewActivity: true,
});
const day = (key, count, extra = {}) => ({
  key, dateLabel: key, weekday: 'Mon', entryCount: count,
  entries: extra.lazy ? [] : Array.from({ length: count }, (_, i) => entry(key, i)), ...extra,
});

async function mount(element) {
  const container = doc.createElement('div');
  doc.body.appendChild(container);
  const view = createRoot(container);
  await act(async () => { view.render(element); await tick(); });
  return view;
}

// A watcher the test drives by hand: it remembers each watched day's callback.
function manualWatcher() {
  const watched = new Map();
  const watchDay = (key, node, onChange) => {
    watched.set(key, onChange);
    return () => watched.delete(key);
  };
  return { watchDay, watched };
}

test('first view keeps whole days within about two screens', () => {
  assert.equal(HISTORY_FIRST_VIEW_ENTRIES, 36);
  assert.equal(firstViewDayCount([]), 0);
  assert.equal(firstViewDayCount([30, 30, 30]), 1, 'a full first day renders alone');
  assert.equal(firstViewDayCount([50]), 1, 'the first day always renders');
  assert.equal(firstViewDayCount([10, 10, 10, 10]), 3);
  assert.equal(firstViewDayCount([12, 12, 12, 1]), 3, 'exactly the budget still fits');
  assert.equal(firstViewDayCount([10, 30, 1, 1]), 1, 'days after a day that waits also wait');
  assert.equal(firstViewDayCount([3, 3, 3, 3, 3, 3, 3]), 7);
});

test('waiting days mount no cards and load once when near the screen', async () => {
  globalThis.__cardRenders = [];
  const { watchDay, watched } = manualWatcher();
  const loads = [];
  const model = {
    mode: 'activity',
    days: [day('d1', 4), day('d2', 30, { lazy: true }), day('d3', 30, { lazy: true }), day('d4', 2, { lazy: true, error: true })],
  };
  const view = await mount(React.createElement(HistoryFeed, {
    model, chrome: false, watchDay, onLoadDay: next => loads.push(next.key),
  }));
  assert.equal(globalThis.__cardRenders.length, 4, 'only the first day mounts its cards');
  assert.deepEqual([...watched.keys()].sort(), ['d2', 'd3', 'd4'], 'every waiting day is watched');

  await act(async () => { watched.get('d3')('far'); await tick(); });
  assert.deepEqual(loads, [], 'a far day waits');
  await act(async () => { watched.get('d2')('near'); await tick(); });
  await act(async () => { watched.get('d2')('visible'); await tick(); });
  assert.deepEqual(loads, ['d2'], 'a near day loads once');
  await act(async () => { watched.get('d4')('visible'); await tick(); });
  assert.deepEqual(loads, ['d2'], 'a failed day waits for its Retry button');
  await act(async () => view.unmount());
  assert.equal(watched.size, 0, 'unmounting stops watching');
});

test('a loaded day that waits again may load itself again', async () => {
  const { watchDay, watched } = manualWatcher();
  const loads = [];
  const render = lazy => React.createElement(HistoryFeed, {
    model: { mode: 'activity', days: [day('d1', 2), day('d2', 3, { lazy })] },
    chrome: false, watchDay, onLoadDay: next => loads.push(next.key),
  });
  const view = await mount(render(true));
  await act(async () => { watched.get('d2')('near'); await tick(); });
  await act(async () => { view.render(render(false)); await tick(); });
  assert.equal(watched.has('d2'), false, 'a loaded day is no longer watched');
  await act(async () => { view.render(render(true)); await tick(); });
  await act(async () => { watched.get('d2')('near'); await tick(); });
  assert.deepEqual(loads, ['d2', 'd2']);
  await act(async () => view.unmount());
});

test('without a watcher a waiting day keeps its button and loads nothing', async () => {
  const loads = [];
  const view = await mount(React.createElement(HistoryFeed, {
    model: { mode: 'activity', days: [day('d1', 2), day('d2', 3, { lazy: true })] },
    chrome: false, onLoadDay: next => loads.push(next.key),
  }));
  assert.deepEqual(loads, []);
  await act(async () => view.unmount());
});

// Back to History: the browser adapter remembers the top visible day per URL.
function browserPage({ url, navigation = 'navigate', scrollY = 0, days = [] }) {
  const store = new Map();
  globalThis.sessionStorage = {
    getItem: key => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => store.set(key, String(value)),
    removeItem: key => store.delete(key),
  };
  globalThis.location = { href: url };
  globalThis.scrollY = scrollY;
  globalThis.performance = { ...globalThis.performance, getEntriesByType: () => [{ type: navigation, name: url }] };
  const nodes = days.map(([key, top, bottom]) => ({ id: `history-day-${key}`, getBoundingClientRect: () => ({ top, bottom }) }));
  const host = { isConnected: true, querySelectorAll: () => nodes };
  return { host, store };
}

test('Back returns to the saved day; a fresh visit starts at the top', () => {
  const url = 'http://floppy.test/history?year=2024&m=12';
  const { host } = browserPage({ url, scrollY: 9100, days: [['20241229', -3000, -400], ['20241228', -400, 2600]] });
  anchors.saveHistoryAnchor(host, url);
  globalThis.performance.getEntriesByType = () => [{ type: 'back_forward', name: url }];
  const saved = anchors.takeHistoryAnchor();
  assert.equal(saved.key, '20241228');
  assert.equal(saved.top, -400);
  assert.equal(anchors.takeHistoryAnchor(), null, 'an anchor is read once');

  anchors.saveHistoryAnchor(host, url);
  globalThis.performance.getEntriesByType = () => [{ type: 'navigate', name: url }];
  assert.equal(anchors.takeHistoryAnchor(), null, 'a fresh visit ignores and clears the anchor');
  globalThis.performance.getEntriesByType = () => [{ type: 'back_forward', name: url }];
  assert.equal(anchors.takeHistoryAnchor(), null);
});

test('one month saving its place does not clear another month', () => {
  const december = 'http://floppy.test/history?year=2024&m=12';
  const november = 'http://floppy.test/history?year=2024&m=11';
  const { host } = browserPage({ url: december, scrollY: 15400, days: [['20241225', -1363, 900]] });
  anchors.saveHistoryAnchor(host, december);
  // On Back the address bar already shows December while November, at its
  // top, saves under its own address.
  globalThis.scrollY = 0;
  anchors.saveHistoryAnchor(host, november);
  globalThis.performance.getEntriesByType = () => [{ type: 'reload', name: december }];
  assert.equal(anchors.takeHistoryAnchor()?.key, '20241225');
});

test('a page swapped out by a boosted link saves nothing', () => {
  const url = 'http://floppy.test/history';
  const { host, store } = browserPage({ url, scrollY: 500, days: [['20241231', -10, 800]] });
  host.isConnected = false;
  anchors.saveHistoryAnchor(host, url);
  assert.equal(store.size, 0);
});
