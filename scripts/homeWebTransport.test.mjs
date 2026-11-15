import assert from 'node:assert/strict';
import test from 'node:test';
import { createRemoteHomeActions } from '../frontend/ui/homeTransportCore.js';
import { createWebRequest, webQueryString } from '../frontend/ui/webRequest.web.js';
import { createHomeTransport } from '../native-proof/app/data/homeTransport.js';
import { createAuthTransport } from '../native-proof/app/data/authTransportCore.js';

function reply(body, status = 200) {
  const text = typeof body === 'string' ? body : JSON.stringify(body);
  return {
    status,
    ok: status >= 200 && status < 300,
    headers: { get: () => '', getSetCookie: () => [] },
    text: async () => text,
  };
}

function fakeFetch(answer = () => reply({ row: { rowId: 7 } })) {
  const calls = [];
  const fetchImpl = async (url, init = {}) => {
    calls.push({ url, ...init });
    return answer(url, init);
  };
  return { fetchImpl, calls };
}

// What mountHome.jsx does with each Home action.
async function driveWebHome(home, itemsLimit = 14) {
  const limit = itemsLimit || undefined;
  await home.loadRow({ rowId: 7, limit });
  await home.loadRow({ rowId: 7, offset: 14, seed: 4242, limit });
  await home.sortRow({ rowId: 7 });
  await home.refreshCovers({ ids: [7, 9], pending: 3 });
  await home.refreshCovers({ ids: [7], pending: 0 });
}

// Requests the web Home adapter made before it used the shared core
// (recorded from its own requestJson). Two entries changed on purpose:
// sort now sends limit=14 like native (the server always builds 14), and
// pending is sent as 1/0 like native (the server reads any digit above 0 as yes).
const BEFORE = [
  { url: '/api/v1/home/row/7/?offset=0&limit=14', method: 'GET' },
  { url: '/api/v1/home/row/7/?offset=14&seed=4242&limit=14', method: 'GET' },
  { url: '/api/v1/home/row/7/sort/', method: 'POST', body: '{}' },
  { url: '/api/v1/home/artwork/?ids=7%2C9&pending=3', method: 'GET' },
  { url: '/api/v1/home/artwork/?ids=7&pending=0', method: 'GET' },
];
const AFTER = [
  BEFORE[0],
  BEFORE[1],
  { url: '/api/v1/home/row/7/sort/?limit=14', method: 'POST', body: '{}' },
  { url: '/api/v1/home/artwork/?ids=7%2C9&pending=1', method: 'GET' },
  BEFORE[4],
];

test('web Home sends the recorded requests, apart from the two unified ones', async () => {
  const { fetchImpl, calls } = fakeFetch();
  await driveWebHome(createRemoteHomeActions(createWebRequest({ csrfToken: 'tok', fetchImpl })));
  assert.deepEqual(calls.map(({ url, method, body }) => ({ url, method, ...(body !== undefined ? { body } : {}) })), AFTER);
  const changed = AFTER.map((entry, index) => JSON.stringify(entry) !== JSON.stringify(BEFORE[index]));
  assert.deepEqual(changed, [false, false, true, true, false]);
  calls.forEach(call => assert.equal(call.credentials, 'same-origin'));
  assert.deepEqual(calls[0].headers, { Accept: 'application/json' });
  assert.deepEqual(calls[2].headers, { Accept: 'application/json', 'Content-Type': 'application/json', 'X-CSRFToken': 'tok' });
});

test('web Home without an items limit leaves limit to the server, as before', async () => {
  const { fetchImpl, calls } = fakeFetch();
  await createRemoteHomeActions(createWebRequest({ fetchImpl })).loadRow({ rowId: 7, limit: undefined });
  assert.equal(calls[0].url, '/api/v1/home/row/7/?offset=0');
});

test('web and Expo Home issue identical requests for the four actions', async () => {
  const web = fakeFetch();
  await driveWebHome(createRemoteHomeActions(createWebRequest({ csrfToken: 'tok', fetchImpl: web.fetchImpl })));

  const native = fakeFetch(url => reply(url.endsWith('/api/v1/user/sidebar/') ? { media_types: {} } : { row: { rowId: 7 } }));
  const storage = new Map();
  const auth = createAuthTransport({
    storage: {
      getItem: async key => storage.get(key) ?? null,
      setItem: async (key, value) => { storage.set(key, value); },
      removeItem: async key => { storage.delete(key); },
    },
    fetchImpl: native.fetchImpl,
  });
  await auth.signInWithToken({ serverUrl: 'https://floppy.example', token: 'test-token' });
  const signInCalls = native.calls.length;
  // HomeScreen passes PAGE_LIMIT (14) on row loads and nothing on sort.
  await driveWebHome(createHomeTransport({ auth }), 14);

  const strip = calls => calls.map(({ url, method, body }) => ({
    url: url.replace('https://floppy.example', ''),
    method,
    body: body ?? null,
  }));
  assert.deepEqual(strip(native.calls.slice(signInCalls)), strip(web.calls));
});

test('web request sends CSRF only on unsafe methods and stays same-origin', async () => {
  const { fetchImpl, calls } = fakeFetch(() => reply({}));
  const request = createWebRequest({ csrfToken: 'abc', fetchImpl });
  await request('/a/');
  await request('/b/', { method: 'post', body: { x: 1 } });
  await request('/c/', { method: 'DELETE' });
  assert.equal(calls[0].headers['X-CSRFToken'], undefined);
  assert.equal(calls[1].method, 'POST');
  assert.equal(calls[1].headers['X-CSRFToken'], 'abc');
  assert.equal(calls[1].body, '{"x":1}');
  assert.equal(calls[2].headers['X-CSRFToken'], 'abc');
  assert.equal(calls[2].headers['Content-Type'], undefined);
  calls.forEach(call => assert.equal(call.credentials, 'same-origin'));
});

test('web query encoding matches the native transport', () => {
  assert.equal(webQueryString({}), '');
  assert.equal(webQueryString(undefined), '');
  assert.equal(webQueryString({ a: 0, b: '', c: null, d: undefined, e: 'x y&z', f: false }), '?a=0&e=x%20y%26z&f=false');
});

test('web request rejects non-2xx answers with the status', async () => {
  const { fetchImpl } = fakeFetch(() => reply('<h1>nope</h1>', 403));
  const request = createWebRequest({ fetchImpl });
  await assert.rejects(request('/x/'), error => error.status === 403 && /403/.test(error.message));
});

test('identical GETs in flight share one request; nothing is cached after', async () => {
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const { fetchImpl, calls } = fakeFetch(async () => { await gate; return reply({ n: calls.length }); });
  const request = createWebRequest({ fetchImpl });
  const first = request('/row/', { query: { offset: 0 } });
  const second = request('/row/', { query: { offset: 0 } });
  const other = request('/row/', { query: { offset: 14 } });
  const post = request('/row/', { method: 'POST', query: { offset: 0 } });
  assert.equal(first, second);
  assert.notEqual(first, other);
  release();
  await Promise.all([first, other, post]);
  assert.equal(calls.length, 3);
  await request('/row/', { query: { offset: 0 } });
  assert.equal(calls.length, 4);
});

test('a failed shared GET frees the slot for the next try', async () => {
  let fail = true;
  const { fetchImpl, calls } = fakeFetch(() => (fail ? reply({}, 503) : reply({ ok: 1 })));
  const request = createWebRequest({ fetchImpl });
  await assert.rejects(request('/x/'));
  fail = false;
  assert.deepEqual(await request('/x/'), { ok: 1 });
  assert.equal(calls.length, 2);
});

test('a cancellable request passes its signal and is not shared', async () => {
  const { fetchImpl, calls } = fakeFetch(() => reply({}));
  const request = createWebRequest({ fetchImpl });
  const controller = new AbortController();
  const plain = request('/x/');
  const cancellable = request('/x/', { signal: controller.signal });
  assert.notEqual(plain, cancellable);
  await Promise.all([plain, cancellable]);
  assert.equal(calls[1].signal, controller.signal);
  assert.equal(calls[0].signal, undefined);
});

test('onTiming reports path, method, time and bytes only when supplied', async () => {
  const timings = [];
  const { fetchImpl } = fakeFetch(() => reply({ title: 'é' }));
  await createWebRequest({ fetchImpl, onTiming: entry => timings.push(entry) })('/t/', { query: { a: 1 } });
  assert.equal(timings.length, 1);
  assert.equal(timings[0].path, '/t/');
  assert.equal(timings[0].method, 'GET');
  assert.equal(timings[0].bytes, Buffer.byteLength('{"title":"é"}'));
  assert.ok(timings[0].ms >= 0);
  await createWebRequest({ fetchImpl })('/t/');
});
