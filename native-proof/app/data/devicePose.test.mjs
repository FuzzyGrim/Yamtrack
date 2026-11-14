import test from 'node:test';
import assert from 'node:assert/strict';
import { POSES, isPose, poseValue, resolvePose } from '../../../frontend/ui/devicePose.js';

test('a hinge decides the fold and the window decides the orientation', () => {
  assert.equal(resolvePose({ width: 390, height: 844, hinge: 'closed' }).key, 'closed-vertical');
  assert.equal(resolvePose({ width: 844, height: 390, hinge: 'closed' }).key, 'closed-horizontal');
  assert.equal(resolvePose({ width: 800, height: 844, hinge: 'fullyOpen' }).key, 'open-vertical');
  assert.equal(resolvePose({ width: 1100, height: 800, hinge: 'fullyOpen' }).key, 'open-horizontal');
  assert.equal(resolvePose({ width: 390, height: 844, hinge: 'partiallyOpen', angle: 110 }).key, 'partial-vertical');
  assert.equal(resolvePose({ width: 844, height: 390, hinge: 'partiallyOpen' }).key, 'partial-horizontal');
  assert.equal(resolvePose({ width: 390, height: 844, hinge: 'partiallyOpen', angle: 110 }).angle, 110);
});

test('every pose is reachable', () => {
  assert.deepEqual(POSES, ['closed-vertical', 'closed-horizontal', 'open-vertical', 'open-horizontal',
    'partial-vertical', 'partial-horizontal']);
});

test('a device with no hinge is a closed pose when it is one small screen and an open pose when it is not', () => {
  // An iPhone, either way up, is a closed Duo.
  assert.equal(resolvePose({ width: 390, height: 844 }).key, 'closed-vertical');
  assert.equal(resolvePose({ width: 844, height: 390 }).key, 'closed-horizontal');
  // A tablet or a wide PWA window has room for two screens, so it is an open Duo.
  assert.equal(resolvePose({ width: 820, height: 1180 }).key, 'open-vertical');
  assert.equal(resolvePose({ width: 1440, height: 900 }).key, 'open-horizontal');
  assert.equal(resolvePose({ width: 390, height: 844 }).hinged, false);
  // An unknown hinge is no hinge.
  assert.equal(resolvePose({ width: 390, height: 844, hinge: 'unknown' }).hinged, false);
});

test('hasHinge says a hinge is reported at all, even with an unknown status', () => {
  assert.equal(resolvePose({ width: 390, height: 844, hinge: 'unknown', hasHinge: true }).hasHinge, true);
  assert.equal(resolvePose({ width: 390, height: 844, hinge: 'unknown', hasHinge: true }).hinged, false);
  assert.equal(resolvePose({ width: 390, height: 844, hinge: 'closed' }).hasHinge, true);
  assert.equal(resolvePose({ width: 390, height: 844 }).hasHinge, false);
});

test('poseValue picks the most specific match and falls back to default', () => {
  const values = { default: 'a', open: 'b', horizontal: 'c', 'open-horizontal': 'd' };
  assert.equal(poseValue(resolvePose({ width: 1440, height: 900 }), values), 'd');
  assert.equal(poseValue(resolvePose({ width: 820, height: 1180 }), values), 'b');
  assert.equal(poseValue(resolvePose({ width: 844, height: 390 }), values), 'c');
  assert.equal(poseValue(resolvePose({ width: 390, height: 844 }), values), 'a');
  assert.equal(poseValue(null, values), 'a');
});

test('isPose accepts a whole pose, a fold or an orientation', () => {
  const pose = resolvePose({ width: 390, height: 844, hinge: 'partiallyOpen' });
  assert.ok(isPose(pose, 'partial-vertical') && isPose(pose, 'partial') && isPose(pose, 'vertical'));
  assert.ok(!isPose(pose, 'closed') && !isPose(pose, 'horizontal'));
});
