import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { detailHeroLayout, detailArtworkCrop } from '../frontend/ui/detailHeroLayout.js';

test('bleeding into either rail keeps the image center on the inner content strip', () => {
  for (const insets of [{ left: 0, right: 80 }, { left: 80, right: 0 }, { left: 0, right: 0 }]) {
    const layout = detailHeroLayout({ width: 360, height: 700, headerHeight: 96, insets });
    assert.equal(-layout.sideBleed + layout.width / 2, 180);
    assert.equal(layout.logoWidth, (360 - 44) * 0.66);
    assert.equal(layout.line + 96, 700 * 0.47);
  }
});

test('the sharp crop uses the inner image frame and does not zoom or move when bleed grows', () => {
  const base = { width: 360, height: 700, headerHeight: 96 };
  const plain = detailHeroLayout(base);
  const rail = detailHeroLayout({ ...base, insets: { right: 80 } });
  for (const aspect of [16 / 9, 4 / 3, 1]) {
    const a = detailArtworkCrop(plain, aspect);
    const b = detailArtworkCrop(rail, aspect);
    if (a.width >= rail.width) {
      assert.equal(a.width, b.width);
      assert.equal(a.height, b.height);
    }
    assert.equal(b.x - rail.sideBleed + b.width / 2, rail.innerWidth / 2);
    assert.equal(b.y, 0);
    assert.ok(b.width >= rail.width);
    assert.ok(b.height >= rail.sharpHeight * 0.8);
  }
  assert.ok(detailArtworkCrop(plain).width < plain.sharpHeight * (16 / 9));
});

test('logo intrinsic proportions change its height while its bottom stays on the hero line', () => {
  const viewport = { width: 390, height: 844, headerHeight: 96 };
  const wide = detailHeroLayout(viewport, 5);
  const tall = detailHeroLayout(viewport, 0.75);
  assert.ok(wide.logoHeight < tall.logoHeight);
  assert.equal(tall.logoHeight, 96);
  assert.equal(wide.line, tall.line);
  assert.equal((wide.line - wide.logoHeight) + wide.logoHeight, tall.line);
});

test('native artwork starts halfway through the overlay, stays centered and covers the rail', () => {
  const layout = detailHeroLayout({ width: 360, height: 700, headerHeight: 96, insets: { top: 24, right: 80 } });
  const original = detailArtworkCrop(layout);
  const crop = detailArtworkCrop(layout, 16 / 9, layout.topBleed - layout.headerHeight / 2);
  assert.equal(crop.y, 72);
  assert.ok(crop.width < original.width);
  assert.ok(crop.width >= layout.width);
  assert.equal(crop.x - layout.sideBleed + crop.width / 2, layout.innerWidth / 2);
});

test('short and unfolded viewports stay bounded and preserve the inner framing', () => {
  for (const viewport of [{ width: 700, height: 390, headerHeight: 96 }, { width: 320, height: 200, headerHeight: 96 }]) {
    const layout = detailHeroLayout(viewport);
    assert.ok(layout.innerHeight > 0);
    assert.ok(layout.line >= layout.logoHeight);
    assert.equal(layout.blurHeight - layout.topBleed * 2, layout.innerHeight);
  }
});
