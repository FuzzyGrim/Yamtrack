import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { prepareDetailArtwork } from './prepareDetailArtwork.mjs';

test('prepares both images and supplies their dimensions before the hero is shown', async () => {
  const calls = [];
  const image = {
    getSize: async uri => uri === 'backdrop' ? { width: 1600, height: 900 } : { width: 600, height: 100 },
    prefetch: async uri => { calls.push(uri); return true; },
  };
  const model = { artwork: { backdrop: 'backdrop', logo: 'logo' } };
  const result = await prepareDetailArtwork(model, image);
  assert.equal(result.artwork.backdropAspect, 16 / 9);
  assert.equal(result.artwork.logoAspect, 6);
  assert.deepEqual(calls.sort(), ['backdrop', 'logo']);
});

test('failed or stalled artwork never prevents the page from loading', async () => {
  const model = { artwork: { backdrop: 'broken', logo: 'stalled' } };
  const image = {
    getSize: uri => uri === 'broken' ? Promise.reject(new Error('failed')) : new Promise(() => {}),
    prefetch: async () => true,
  };
  assert.equal(await prepareDetailArtwork(model, image, 5), model);
  const failed = await prepareDetailArtwork({ artwork: { backdrop: 'broken' } }, image, 50);
  assert.equal(failed.artwork.backdropAspect, null);
});

test('starts both artwork downloads and dimension reads without a serial wait', async () => {
  const started = [];
  const waiting = [];
  const image = Object.fromEntries(['getSize', 'prefetch'].map(method => [method, uri => {
    started.push(`${method}:${uri}`);
    return new Promise(resolve => waiting.push(() => resolve(method === 'getSize' ? { width: 16, height: 9 } : true)));
  }]));
  const result = prepareDetailArtwork({ artwork: { backdrop: 'b', logo: 'l' } }, image);
  await Promise.resolve();
  assert.deepEqual(started.sort(), ['getSize:b', 'getSize:l', 'prefetch:b', 'prefetch:l']);
  waiting.forEach(resolve => resolve());
  await result;
});
