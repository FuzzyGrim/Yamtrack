import test from 'node:test';
import assert from 'node:assert/strict';
import { dropdownPlacement, toggleDropdownSelection } from '../../../frontend/ui/dropdownMetrics.js';

test('positions the menu relative to its sheet and flips near the bottom', () => {
  const bounds = { x: 100, y: 300, width: 400, height: 500 };
  assert.deepEqual(dropdownPlacement({ x: 130, y: 350, width: 250, height: 40 }, bounds, 100), { right: 120, top: 94 });
  assert.deepEqual(dropdownPlacement({ x: 130, y: 730, width: 250, height: 40 }, bounds, 100), { right: 120, bottom: 74 });
});

test('keeps a menu opened from a trigger near the left edge on screen', () => {
  const bounds = { x: 0, y: 0, width: 400, height: 800 };
  // A 60pt trigger at the left: right-aligning a 208pt menu would start at -100.
  assert.deepEqual(dropdownPlacement({ x: 24, y: 100, width: 60, height: 36 }, bounds, 100), { left: 24, top: 140 });
  // Near the right edge the left-aligned menu is pulled back so it ends inside the screen.
  assert.deepEqual(dropdownPlacement({ x: 380, y: 100, width: 10, height: 36 }, { ...bounds, width: 300 }, 100), { right: 8, top: 140 });
  assert.deepEqual(dropdownPlacement({ x: 150, y: 100, width: 60, height: 36 }, { ...bounds, width: 300 }, 100), { left: 84, top: 140 });
});

test('a toolbar menu lines up with its trigger and only slides left at the right edge', () => {
  const bounds = { x: 0, y: 0, width: 400, height: 800 };
  const trigger = (x, width = 80) => ({ x, y: 100, width, height: 36 });
  assert.deepEqual(dropdownPlacement(trigger(150), bounds, 100, 208, 'start'), { left: 150, top: 140 });
  assert.deepEqual(dropdownPlacement(trigger(280), bounds, 100, 208, 'start'), { left: 184, top: 140 });
  assert.deepEqual(dropdownPlacement(trigger(2), bounds, 100, 208, 'start'), { left: 8, top: 140 });
});

test('a menu stays out of the safe area, such as the side rail', () => {
  const bounds = { x: 0, y: 0, width: 400, height: 800 };
  const rail = { top: 0, right: 96, bottom: 0, left: 0 };
  const trigger = { x: 180, y: 100, width: 80, height: 36 };
  // Left-aligned it would end at 388, under the rail; it slides left to end where the rail begins, 8 clear.
  assert.deepEqual(dropdownPlacement(trigger, bounds, 100, 208, 'start', rail), { left: 88, top: 140 });
  // A trigger with room keeps its own left edge.
  assert.deepEqual(dropdownPlacement({ ...trigger, x: 60 }, bounds, 100, 208, 'start', rail), { left: 60, top: 140 });
  // Right-aligned menus keep clear of the rail as well.
  assert.deepEqual(dropdownPlacement({ x: 320, y: 100, width: 60, height: 36 }, bounds, 100, 208, 'end', rail), { right: 104, top: 140 });
});

test('multi-select adds and removes choices without mutating the original', () => {
  const selected = ['one'];
  assert.deepEqual(toggleDropdownSelection(selected, 'two'), ['one', 'two']);
  assert.deepEqual(toggleDropdownSelection(selected, 'one'), []);
  assert.deepEqual(selected, ['one']);
});
