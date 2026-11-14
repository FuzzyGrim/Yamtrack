import { test } from 'node:test';
import { deepStrictEqual } from 'node:assert';
import { resolvePose } from '../../../frontend/ui/devicePose.js';
import { menuGeometry } from './menuGeometry.mjs';

const pose = (width, height, hinge) => resolvePose({ width, height, hinge, hasHinge: Boolean(hinge) });

test('folded and compact windows use the available content width', () => {
  deepStrictEqual(menuGeometry(360, pose(360, 780, 'closed')), { full: true, menuWidth: 360, gap: 0 });
  deepStrictEqual(menuGeometry(390, pose(390, 844)), { full: true, menuWidth: 390, gap: 0 });
});

test('a phone in landscape keeps its side menu: only a closed hinge or a narrow window is full width', () => {
  deepStrictEqual(menuGeometry(844, pose(844, 390)), { full: false, menuWidth: 288, gap: 0 });
});

test('partially open uses the measured division, not a guessed midpoint', () => {
  deepStrictEqual(menuGeometry(900, pose(900, 800, 'partiallyOpen'), { x: 410, width: 18 }),
    { full: false, menuWidth: 410, gap: 18 });
});

test('open and missing geometry fall back to a bounded sidebar', () => {
  deepStrictEqual(menuGeometry(900, pose(900, 800, 'fullyOpen')), { full: false, menuWidth: 288, gap: 0 });
  deepStrictEqual(menuGeometry(900, pose(900, 800, 'partiallyOpen')), { full: false, menuWidth: 288, gap: 0 });
});
