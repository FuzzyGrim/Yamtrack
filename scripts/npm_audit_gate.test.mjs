import assert from 'node:assert/strict';
import test from 'node:test';
import { auditGate } from './npm_audit_gate.mjs';

const via = (ghsa, severity, title = 'x') => ({ source: 1, url: `https://github.com/advisories/${ghsa}`, severity, title });
const report = {
  vulnerabilities: {
    picomatch: { via: [via('GHSA-fc5q-39xc-vjpf', 'high')] },
    '@parcel/watcher': { via: ['picomatch'] },
    minimatch: { via: [via('GHSA-g3hv-75wg-m249', 'moderate')] },
  },
};
const exception = { advisory: 'GHSA-fc5q-39xc-vjpf', package: 'picomatch', expires: '2026-12-17', reason: 'build tools only' };

test('a listed advisory is allowed until its expiry', () => {
  const result = auditGate(report, [exception], { level: 'high', today: '2026-11-17' });
  assert.deepEqual(result.blocking, []);
  assert.deepEqual(result.excused.map(item => item.id), ['GHSA-fc5q-39xc-vjpf']);
});

test('an expired exception fails the gate', () => {
  const result = auditGate(report, [exception], { level: 'high', today: '2026-12-18' });
  assert.equal(result.expired.length, 1);
  assert.deepEqual(result.blocking.map(item => item.id), ['GHSA-fc5q-39xc-vjpf']);
});

test('the level threshold still applies and unlisted advisories block', () => {
  const atHigh = auditGate(report, [], { level: 'high', today: '2026-11-17' });
  assert.deepEqual(atHigh.blocking.map(item => item.id), ['GHSA-fc5q-39xc-vjpf']);
  const atLow = auditGate(report, [exception], { level: 'low', today: '2026-11-17' });
  assert.deepEqual(atLow.blocking.map(item => item.id), ['GHSA-g3hv-75wg-m249']);
});

test('an exception without a reason or date is ignored', () => {
  const result = auditGate(report, [{ advisory: 'GHSA-fc5q-39xc-vjpf' }], { level: 'high', today: '2026-11-17' });
  assert.equal(result.blocking.length, 1);
});
