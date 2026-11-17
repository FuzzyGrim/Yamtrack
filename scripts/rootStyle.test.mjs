import assert from 'node:assert/strict';
import test from 'node:test';

// A minimal browser: counts getComputedStyle(<html>) calls (each one is a
// full-page style recalculation when the DOM changed) and lets the test fire
// the mutations and colour-scheme changes the cache listens to.
const observers = [];
const schemeListeners = [];
let tokens = { '--color-text': '#fafafa' };
let computeCalls = 0;
globalThis.document = { documentElement: { tag: 'html' }, head: { tag: 'head' } };
globalThis.window = {
  matchMedia: () => ({ addEventListener: (_type, listener) => schemeListeners.push(listener) }),
};
globalThis.MutationObserver = class {
  constructor(callback) { this.callback = callback; }
  observe(target) { observers.push({ target, callback: this.callback }); }
};
globalThis.getComputedStyle = (element) => {
  assert.equal(element, document.documentElement);
  computeCalls += 1;
  return {
    getPropertyValue: name => tokens[name] || '',
    get fontSize() { return tokens.fontSize || '16px'; },
    get fontFamily() { return 'system-ui'; },
  };
};

const { rootStyle } = await import('../frontend/ui/rootStyle.web.js');
const nextTask = () => new Promise(resolve => setTimeout(resolve, 0));
const mutate = target => observers.filter(o => o.target === target).forEach(o => o.callback([]));

test('islands mounting in one task share one style read', async () => {
  await nextTask();
  computeCalls = 0;
  // Fifteen islands, each reading its palette (the Statistics page).
  for (let island = 0; island < 15; island += 1) {
    assert.equal(rootStyle().getPropertyValue('--color-text'), '#fafafa');
    assert.equal(rootStyle().fontSize, '16px');
  }
  assert.equal(computeCalls, 1);
});

test('the next task reads fresh values', async () => {
  rootStyle().getPropertyValue('--color-text');
  await nextTask();
  tokens = { ...tokens, '--color-text': '#111111' };
  computeCalls = 0;
  assert.equal(rootStyle().getPropertyValue('--color-text'), '#111111');
  assert.equal(computeCalls, 1);
});

test('a theme change on <html> within the same task is seen at once', async () => {
  await nextTask();
  tokens = { ...tokens, '--color-text': '#222222' };
  assert.equal(rootStyle().getPropertyValue('--color-text'), '#222222');
  tokens = { ...tokens, '--color-text': '#333333', fontSize: '20px' };
  mutate(document.documentElement);
  assert.equal(rootStyle().getPropertyValue('--color-text'), '#333333');
  assert.equal(rootStyle().fontSize, '20px');
});

test('a stylesheet change in <head> or a colour-scheme change is seen at once', async () => {
  await nextTask();
  rootStyle().getPropertyValue('--color-text');
  tokens = { ...tokens, '--color-text': '#444444' };
  mutate(document.head);
  assert.equal(rootStyle().getPropertyValue('--color-text'), '#444444');
  tokens = { ...tokens, '--color-text': '#555555' };
  schemeListeners.forEach(listener => listener());
  assert.equal(rootStyle().getPropertyValue('--color-text'), '#555555');
});

test('observers are installed once', () => {
  const count = observers.length;
  rootStyle();
  rootStyle();
  assert.equal(observers.length, count);
  assert.equal(observers.filter(o => o.target === document.documentElement).length, 1);
});
