import assert from 'node:assert/strict';
import test from 'node:test';
import { createFetchGate } from '../frontend/ui/fetchGate.js';
import { rowPriority, rowProximity, rowsToStart } from '../frontend/ui/homeRowLoading.js';

const view = { top: 0, height: 800 };

test('a row on screen is visible, one within one and a half screens is near, the rest far', () => {
  assert.equal(rowProximity({ top: 100, bottom: 400 }, view), 'visible');
  assert.equal(rowProximity({ top: 790, bottom: 1100 }, view), 'visible');
  assert.equal(rowProximity({ top: 800, bottom: 1100 }, view), 'near');
  assert.equal(rowProximity({ top: 1990, bottom: 2300 }, view), 'near');
  assert.equal(rowProximity({ top: 2000, bottom: 2300 }, view), 'far');
  // Rows above the screen (restored scroll) count the same way.
  assert.equal(rowProximity({ top: -1000, bottom: -700 }, view), 'near');
  assert.equal(rowProximity({ top: -2400, bottom: -2100 }, view), 'far');
});

test('no usable size means far, never a crash', () => {
  assert.equal(rowProximity({ top: 0, bottom: 10 }, { height: 0 }), 'far');
  assert.equal(rowProximity(null, view), 'far');
  assert.equal(rowProximity({ top: 'x', bottom: 1 }, view), 'far');
});

test('visible rows start before near rows, each in page order; far and started rows wait', () => {
  const proximity = new Map([
    ['1', 'near'], ['2', 'visible'], ['3', 'far'], ['4', 'visible'], ['5', 'near'],
  ]);
  assert.deepEqual(rowsToStart([1, 2, 3, 4, 5, 6], proximity, new Set()), [2, 4, 1, 5]);
  assert.deepEqual(rowsToStart([1, 2, 3, 4, 5], proximity, new Set(['2', '5'])), [4, 1]);
  assert.deepEqual(rowsToStart([1, 2], new Map(), new Set()), []);
});

test('priority puts visible rows ahead of near ones and unknown rows last', () => {
  assert.ok(rowPriority('visible') < rowPriority('near'));
  assert.ok(rowPriority('near') < rowPriority('far'));
  assert.equal(rowPriority(undefined), rowPriority('far'));
});

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

test('a queued row that scrolls into view takes the next free slot', async () => {
  const run = createFetchGate(1);
  const levels = new Map([['a', 'near'], ['b', 'near'], ['c', 'near']]);
  const order = [];
  const blocker = deferred();
  const first = run(() => blocker.promise);
  const queued = ['a', 'b', 'c'].map(key => run(async () => { order.push(key); },
    () => rowPriority(levels.get(key))));
  // While all three wait, the person scrolls to row c.
  levels.set('c', 'visible');
  blocker.resolve();
  await Promise.all([first, ...queued]);
  assert.deepEqual(order, ['c', 'a', 'b']);
});

test('without a priority the gate stays first-come first-served and never drops a task', async () => {
  const run = createFetchGate(2);
  const order = [];
  const blocker = deferred();
  const held = [run(() => blocker.promise), run(() => blocker.promise)];
  const queued = [1, 2, 3, 4].map(n => run(async () => { order.push(n); }));
  blocker.resolve();
  await Promise.all([...held, ...queued]);
  assert.deepEqual(order, [1, 2, 3, 4]);
});
