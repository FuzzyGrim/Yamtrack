// Render cost of card grids: a change to one shelf or one card re-renders only
// that shelf's or that card's MediaCard, and a changed card still re-renders.
// Renders the real shared components with react-dom over a minimal DOM.
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
  const dir = await mkdtemp(join(tmpdir(), 'card-render-'));
  const entry = join(dir, 'entry.jsx');
  await writeFile(entry, `
    export { default as React, act } from 'react';
    export { createRoot } from 'react-dom/client';
    export { HomeShelves } from ${JSON.stringify(join(root, 'frontend/ui/HomeShelves.jsx'))};
    export { MediaCard } from ${JSON.stringify(join(root, 'frontend/ui/MediaCard.jsx'))};
    export { ArtworkImage } from ${JSON.stringify(join(root, 'frontend/ui/ArtworkImage.native.jsx'))};
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
const { React, act, createRoot, HomeShelves, MediaCard, ArtworkImage } = await load();
const card = title => ({ title, href: `/x/${title}`, image: { src: `https://example.test/${title}.jpg` }, shape: 'poster' });
const row = (id, count) => ({
  rowId: id, title: `Row ${id}`, total: count, loadedCount: count,
  items: Array.from({ length: count }, (_, i) => ({ id: `${id}${i}`, card: card(`${id}${i}`) })),
});
const tick = () => new Promise(done => setTimeout(done, 10));

async function mount(element) {
  const container = doc.createElement('div');
  doc.body.appendChild(container);
  const view = createRoot(container);
  await act(async () => { view.render(element); await tick(); });
  return view;
}

function counting() {
  globalThis.__cardRenders = [];
  return globalThis.__cardRenders;
}

test('a Home shelf arriving renders only its own cards', async () => {
  const pending = {};
  const onLoadRow = id => new Promise(done => { pending[id] = done; });
  const renders = counting();
  const view = await mount(React.createElement(HomeShelves, {
    groups: [{ mediaType: 'movie', rows: [row('a', 5), row('b', 5)] }],
    deferredGroups: [{ mediaType: 'tv', rowIds: ['c', 'd'] }],
    onLoadRow, deferredDelayMs: 0,
  }));
  assert.equal(renders.length, 10, 'each built card renders once on mount');
  for (let wait = 0; wait < 50 && !(pending.c && pending.d); wait += 1) await act(tick);
  assert.ok(pending.c && pending.d, 'both deferred rows were requested');
  renders.length = 0;
  await act(async () => { pending.c(row('c', 4)); await tick(); });
  assert.deepEqual([...renders].sort(), ['c0', 'c1', 'c2', 'c3']);
  renders.length = 0;
  await act(async () => { pending.d(row('d', 3)); await tick(); });
  assert.deepEqual([...renders].sort(), ['d0', 'd1', 'd2']);
  await act(async () => view.unmount());
});

test('a changed card re-renders and its neighbours do not', async () => {
  const shelf = row('a', 4);
  const renders = counting();
  const props = { groups: [{ mediaType: 'movie', rows: [shelf] }] };
  const view = await mount(React.createElement(HomeShelves, props));
  renders.length = 0;
  // A rating saved or a cover arriving gives that card a new model.
  const items = shelf.items.map((item, index) => (index === 2
    ? { ...item, card: { ...item.card, title: 'a2 rated', image: { src: 'https://example.test/late.jpg' } } }
    : item));
  await act(async () => {
    view.render(React.createElement(HomeShelves, { groups: [{ mediaType: 'movie', rows: [{ ...shelf, items }] }] }));
    await tick();
  });
  assert.deepEqual(renders, ['a2 rated']);
  await act(async () => view.unmount());
});

test('MediaCard skips identical props and renders a new model', async () => {
  const renders = counting();
  const model = card('solo');
  const onOpen = () => {};
  const view = await mount(React.createElement(MediaCard, { model, onOpen }));
  await act(async () => { view.render(React.createElement(MediaCard, { model, onOpen })); await tick(); });
  assert.deepEqual(renders, ['solo']);
  await act(async () => { view.render(React.createElement(MediaCard, { model: { ...model, title: 'solo 2' }, onOpen })); await tick(); });
  assert.deepEqual(renders, ['solo', 'solo 2']);
  await act(async () => view.unmount());
});

test('native artwork falls back when the card-size image is missing', async () => {
  const container = doc.createElement('div');
  doc.body.appendChild(container);
  const view = createRoot(container);
  await act(async () => {
    view.render(React.createElement(ArtworkImage, {
      src: 'https://images.igdb.com/igdb/image/upload/t_cover_big_2x/missing.jpg',
      fallback: 'https://example.test/fallback.jpg',
      title: 'Fallback Game',
    }));
  });
  for (let step = 0; step < 4; step += 1) await act(tick);
  const img = container.childNodes[0].childNodes.find(node => node.tagName === 'IMG');
  assert.equal(img?.getAttribute('data-src'), 'https://example.test/fallback.jpg');
  assert.ok(!container.textContent.includes('Fallback Game'), 'the loaded fallback replaces the placeholder title');
  await act(async () => view.unmount());
});

test('native artwork shows the unavailable state when every source fails', async () => {
  const container = doc.createElement('div');
  doc.body.appendChild(container);
  const view = createRoot(container);
  await act(async () => {
    view.render(React.createElement(ArtworkImage, { src: 'https://example.test/missing.jpg' }));
  });
  for (let step = 0; step < 4; step += 1) await act(tick);
  assert.ok(container.textContent.includes('Artwork unavailable'));
  await act(async () => view.unmount());
});
