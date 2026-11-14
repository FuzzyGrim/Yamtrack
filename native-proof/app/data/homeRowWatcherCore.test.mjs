import assert from 'node:assert/strict';
import test from 'node:test';
import { createMeasuredRowWatcher, measureInWindow } from './homeRowWatcherCore.js';

function fakeRows(positions) {
  // positions: node -> { y, height }; scrolling subtracts from y.
  let scroll = 0;
  return {
    measure: node => {
      const rect = positions.get(node);
      return rect ? { y: rect.y - scroll, width: 300, height: rect.height } : null;
    },
    scrollTo(value) { scroll = value; },
  };
}

test('reports each row once per change, visible and near at launch, far until scrolled', async () => {
  const nodes = ['a', 'b', 'c'].map(name => ({ name }));
  const positions = new Map([
    [nodes[0], { y: 200, height: 300 }],
    [nodes[1], { y: 1200, height: 300 }],
    [nodes[2], { y: 4000, height: 300 }],
  ]);
  const page = fakeRows(positions);
  const watcher = createMeasuredRowWatcher({ measure: page.measure, viewportHeight: 800 });
  const seen = [];
  nodes.forEach(node => watcher.watchRow(node.name, node, level => seen.push([node.name, level])));
  await watcher.check();
  assert.deepEqual(seen, [['a', 'visible'], ['b', 'near'], ['c', 'far']]);

  seen.length = 0;
  await watcher.check();
  assert.deepEqual(seen, [], 'an unchanged position is not reported again');

  page.scrollTo(3500);
  await watcher.check();
  assert.deepEqual(seen, [['a', 'far'], ['b', 'far'], ['c', 'visible']]);
});

test('a row not laid out yet is skipped until a later check', async () => {
  const node = {};
  let rect = { y: 0, width: 0, height: 0 };
  const watcher = createMeasuredRowWatcher({ measure: () => rect, viewportHeight: 800 });
  const seen = [];
  watcher.watchRow('x', node, level => seen.push(level));
  await watcher.check();
  assert.deepEqual(seen, []);
  rect = { y: 100, width: 300, height: 200 };
  await watcher.check();
  assert.deepEqual(seen, ['visible']);
});

test('a stopped row is never reported, and a failed measure is ignored', async () => {
  const good = {};
  const bad = {};
  const watcher = createMeasuredRowWatcher({
    measure: node => {
      if (node === bad) throw new Error('detached');
      return { y: 100, width: 300, height: 200 };
    },
    viewportHeight: 800,
  });
  const seen = [];
  const stop = watcher.watchRow('g', good, level => seen.push(['g', level]));
  watcher.watchRow('b', bad, level => seen.push(['b', level]));
  stop();
  await watcher.check();
  assert.deepEqual(seen, []);
  assert.equal(watcher.size, 1);
});

test('checks during a pass are folded into one more pass', async () => {
  let measures = 0;
  const watcher = createMeasuredRowWatcher({
    measure: () => { measures += 1; return { y: 0, width: 1, height: 1 }; },
    viewportHeight: 800,
  });
  watcher.watchRow('x', {}, () => {});
  await Promise.all([watcher.check(), watcher.check(), watcher.check()]);
  assert.ok(measures <= 3, `measured ${measures} times`);
});

test('measureInWindow reads the native callback and tolerates nodes without it', async () => {
  assert.equal(await measureInWindow({}), null);
  const rect = await measureInWindow({ measureInWindow: callback => callback(1, 2, 3, 4) });
  assert.deepEqual(rect, { x: 1, y: 2, width: 3, height: 4 });
});

test('a measure that never answers does not stall later checks', async () => {
  const stuck = {};
  const fine = {};
  const watcher = createMeasuredRowWatcher({
    measure: node => (node === stuck ? new Promise(() => {}) : { y: 100, width: 300, height: 200 }),
    viewportHeight: 800,
    measureTimeoutMs: 20,
  });
  const seen = [];
  watcher.watchRow('s', stuck, level => seen.push(['s', level]));
  watcher.watchRow('f', fine, level => seen.push(['f', level]));
  await watcher.check();
  assert.deepEqual(seen, [['f', 'visible']]);
  const late = {};
  watcher.watchRow('l', late, level => seen.push(['l', level]));
  await watcher.check();
  assert.deepEqual(seen, [['f', 'visible'], ['l', 'visible']]);
});
