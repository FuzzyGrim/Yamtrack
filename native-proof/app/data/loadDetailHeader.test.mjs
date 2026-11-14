import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { loadDetailHeader } from './loadDetailHeader.mjs';

test('starts all header reads together and reveals their results together', async () => {
  const pending = new Map();
  const loaders = Object.fromEntries(['tracking', 'progress', 'scores', 'checkin'].map(key =>
    [key, () => new Promise(resolve => pending.set(key, resolve))]));
  const result = loadDetailHeader({ mediaType: 'tv' }, loaders, { tracked: false });
  await Promise.resolve();
  assert.deepEqual([...pending.keys()], ['tracking', 'progress', 'scores', 'checkin']);
  pending.get('tracking')({ tracked: true });
  pending.get('progress')({ card: 'progress' });
  pending.get('scores')({ chips: [] });
  pending.get('checkin')({ actions: [{ key: 'play' }] });
  assert.deepEqual((await result).checkin, [{ key: 'play' }]);
});

test('failed optional reads do not prevent the header from loading', async () => {
  const fail = async () => { throw new Error('unavailable'); };
  const loaders = { tracking: fail, progress: fail, scores: fail, checkin: fail, seerr: fail };
  const result = await loadDetailHeader({}, loaders, { tracked: false });
  assert.equal(result.tracking.tracked, false);
  assert.equal(result.tracking.unavailable, true);
  assert.equal(result.progress, null);
  assert.deepEqual(result.checkin, []);
  assert.deepEqual(result.scores.chips, []);
});

test('a stalled read releases the page without presenting tracking as untracked', async () => {
  const loaders = {
    tracking: () => new Promise(() => {}), progress: async () => null,
    scores: async () => ({ chips: [] }), checkin: async () => null,
  };
  const result = await loadDetailHeader({}, loaders, { tracked: false }, 5);
  assert.equal(result.tracking.unavailable, true);
});
