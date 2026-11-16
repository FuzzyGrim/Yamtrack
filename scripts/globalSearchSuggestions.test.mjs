// Search suggestions while typing: the shared search box (web and Expo) keeps at
// most one suggestion request open. A newer keystroke cancels the older request
// on the wire, and the web and native request layers pass that cancel to fetch.
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { installFakeDom } from './test-support/fakeDom.mjs';

const root = resolve(import.meta.dirname, '..');
const support = join(root, 'scripts', 'test-support');

async function load() {
  const dir = await mkdtemp(join(tmpdir(), 'global-search-'));
  const entry = join(dir, 'entry.jsx');
  await writeFile(entry, `
    export { default as React, act } from 'react';
    export { createRoot } from 'react-dom/client';
    export { GlobalSearch } from ${JSON.stringify(join(root, 'frontend/ui/GlobalSearch.jsx'))};
    export { createWebRequest } from ${JSON.stringify(join(root, 'frontend/ui/webRequest.web.js'))};
    export { createRemoteSearchActions } from ${JSON.stringify(join(root, 'native-proof/app/data/searchTransportCore.js'))};
    export { createAuthTransport } from ${JSON.stringify(join(root, 'native-proof/app/data/authTransportCore.js'))};
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
    logLevel: 'error',
  });
  const mod = await import(pathToFileURL(outfile).href);
  await rm(dir, { recursive: true, force: true });
  return mod;
}

const doc = installFakeDom();
const { React, act, createRoot, GlobalSearch, createWebRequest, createRemoteSearchActions, createAuthTransport } = await load();
const wait = ms => new Promise(done => setTimeout(done, ms));
const DEBOUNCE_MS = 250;

// Waits past the debounce until `ready()` holds, so a busy machine does not flake.
async function settle(ready) {
  await wait(DEBOUNCE_MS);
  for (let tries = 0; tries < 200 && !ready(); tries += 1) await wait(10);
}

// One keystroke: React commits it when `act` returns, then the debounce runs.
async function type(field, value, ready) {
  await act(async () => { field().onChangeText(value); });
  await act(async () => { await settle(ready); });
}

async function mountSearch(onLoadSuggestions) {
  const container = doc.createElement('div');
  doc.body.appendChild(container);
  const view = createRoot(container);
  await act(async () => {
    view.render(React.createElement(GlobalSearch, {
      mediaTypes: [{ display: 'All', value: 'all' }],
      labels: { inputLabel: 'Search' },
      onLoadSuggestions,
    }));
  });
  return { view, field: () => globalThis.__textInputs.Search };
}

test('typing a word keeps one suggestion request open and cancels each one it replaces', async () => {
  const calls = [];
  const onLoadSuggestions = ({ query, signal }) => new Promise((done, fail) => {
    const call = { query, signal, done, aborted: false };
    calls.push(call);
    signal.addEventListener('abort', () => { call.aborted = true; fail(new Error('aborted')); });
  });
  const { view, field } = await mountSearch(onLoadSuggestions);
  // Pauses longer than the debounce, the slow typing that piled up four requests in production.
  for (const [index, value] of ['st', 'sta', 'star', 'star t'].entries()) {
    await type(field, value, () => calls.length > index);
  }
  assert.deepEqual(calls.map(call => call.query), ['st', 'sta', 'star', 'star t']);
  assert.deepEqual(calls.map(call => call.aborted), [true, true, true, false]);
  assert.equal(calls.filter(call => !call.aborted).length, 1, 'one request in flight');
  // Fast typing inside the debounce sends nothing for the letters in between.
  await act(async () => { field().onChangeText('star tr'); });
  await act(async () => { field().onChangeText('star tre'); });
  await type(field, 'star trek', () => calls.length > 4);
  await act(async () => { await wait(DEBOUNCE_MS); });
  assert.deepEqual(calls.slice(4).map(call => call.query), ['star trek']);
  assert.equal(calls[3].aborted, true);
  await act(async () => view.unmount());
  assert.equal(calls.at(-1).aborted, true, 'leaving the page cancels the open request');
});

test('a cancelled request never replaces the newer answer', async () => {
  const pending = {};
  const onLoadSuggestions = ({ query }) => new Promise(done => { pending[query] = done; });
  const { view, field } = await mountSearch(onLoadSuggestions);
  await type(field, 'dun', () => pending.dun);
  await type(field, 'dune', () => pending.dune);
  await act(async () => { pending.dune({ suggestions: [{ id: '1', title: 'Dune' }] }); await wait(10); });
  // A server that ignores the cancel and answers late must not overwrite the list.
  await act(async () => { pending.dun({ suggestions: [{ id: '2', title: 'Dunkirk' }] }); await wait(10); });
  const text = doc.body.textContent;
  assert.match(text, /Dune/);
  assert.doesNotMatch(text, /Dunkirk/);
  await act(async () => view.unmount());
});

test('web: the shared request helper passes the cancel signal to fetch', async () => {
  const seen = [];
  const request = createWebRequest({
    fetchImpl: async (url, init) => { seen.push({ url, signal: init.signal }); return new Response('{"suggestions":[]}'); },
  });
  const controller = new AbortController();
  await request('/search/suggestions', { query: { q: 'st', media_type: 'all', format: 'json' }, signal: controller.signal });
  assert.equal(seen[0].url, '/search/suggestions?q=st&media_type=all&format=json');
  assert.equal(seen[0].signal, controller.signal);
});

test('Expo: suggestions pass the cancel signal through the transport to fetch', async () => {
  const seen = [];
  const storage = { getItem: async () => null, setItem: async () => {}, removeItem: async () => {} };
  const fetchImpl = async (url, init) => {
    seen.push({ url, signal: init.signal });
    if (init.signal?.aborted) throw Object.assign(new Error('The operation was aborted.'), { name: 'AbortError' });
    return new Response(JSON.stringify({ suggestions: [], access: 'a', refresh: 'r', user: { username: 'u' } }), {
      headers: { 'Content-Type': 'application/json' },
    });
  };
  const auth = createAuthTransport({ storage, fetchImpl });
  await auth.signInWithToken({ serverUrl: 'http://floppy.test', token: 'abc' }).catch(() => {});
  const session = auth.getSession();
  assert.ok(session, 'signed in with a token');
  const search = createRemoteSearchActions(session.request);
  const controller = new AbortController();
  await search.loadSuggestions({ query: 'st', mediaType: 'tv', signal: controller.signal });
  const call = seen.find(entry => entry.url.includes('/api/v1/search/suggestions/'));
  assert.equal(call.signal, controller.signal);

  // A cancelled request rejects as cancelled, not as "could not reach the server".
  controller.abort();
  await assert.rejects(
    search.loadSuggestions({ query: 'sta', mediaType: 'tv', signal: controller.signal }),
    error => error.name === 'AbortError',
  );
});
