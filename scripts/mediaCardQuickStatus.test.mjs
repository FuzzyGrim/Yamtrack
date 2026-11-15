import test from 'node:test';
import assert from 'node:assert/strict';
import { saveQuickStatus } from '../frontend/ui/mediaCardQuickStatus.web.js';

const identity = { mediaType: 'movie', source: 'tmdb', mediaId: '12', instanceId: 34 };
const statuses = ['Planning', 'In progress', 'Paused', 'Completed', 'Dropped'];

test('updates only status on the existing consumption for each status', async () => {
  for (const status of statuses) {
    const calls = [];
    const fetcher = async (url, options) => {
      calls.push({ url, options });
      return calls.length === 1 ? { ok: true, json: async () => ({ tracked: true }) } : { ok: true };
    };
    await saveQuickStatus({ identity }, status, 'csrf', fetcher);
    assert.equal(calls[1].url, '/api/v1/media/movie/tmdb/12/history/34/');
    assert.equal(calls[1].options.method, 'PATCH');
    assert.deepEqual(JSON.parse(calls[1].options.body), { status });
    assert.equal(calls[1].options.headers['X-CSRFToken'], 'csrf');
  }
});

test('creates an untracked season using its provider coordinates', async () => {
  const calls = [];
  const fetcher = async (url, options) => {
    calls.push({ url, options });
    return calls.length === 1 ? { ok: true, json: async () => ({ tracked: false }) } : { ok: true };
  };
  await saveQuickStatus({ identity: { ...identity, mediaType: 'season', seasonNumber: 2 } }, 'Planning', '', fetcher);
  assert.equal(calls[0].url, '/api/v1/media/tv/tmdb/12/2/state/');
  assert.equal(calls[1].url, '/api/v1/media/season/');
  assert.deepEqual(JSON.parse(calls[1].options.body), { source: 'tmdb', media_id: '12', season_number: 2, status: 'Planning' });
});

test('failed state reads and manual untracked items never issue a write', async () => {
  let count = 0;
  await assert.rejects(saveQuickStatus({ identity }, 'Paused', '', async () => { count++; return { ok: false }; }));
  assert.equal(count, 1);
  count = 0;
  await assert.rejects(saveQuickStatus({ identity: { ...identity, source: 'manual' } }, 'Paused', '', async () => {
    count++; return { ok: true, json: async () => ({ tracked: false }) };
  }));
  assert.equal(count, 1);
});

test('reports failed writes instead of claiming success', async () => {
  let count = 0;
  await assert.rejects(saveQuickStatus({ identity }, 'Dropped', '', async () => {
    count++;
    return count === 1 ? { ok: true, json: async () => ({ tracked: true }) } : { ok: false };
  }), /Could not change status/);
});
