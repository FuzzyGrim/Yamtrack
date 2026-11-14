// Artwork is framed in the inner page viewport. Bleed expands both sides of
// that frame equally, so the search overlay and a side rail never recenter it.
export function detailHeroLayout({ width, height, headerHeight = 0, insets = {}, gutters = {} }, logoAspect = 0) {
  const innerWidth = Math.max(1, width || 390);
  const viewportHeight = Math.max(1, height || 700);
  const innerHeight = Math.max(1, viewportHeight - headerHeight);
  const topBleed = Math.max(0, headerHeight + (insets.top || 0));
  const sideBleed = Math.max(0, insets.left || 0, insets.right || 0);
  const gutterLeft = gutters.paddingLeft ?? 22;
  const gutterRight = gutters.paddingRight ?? 22;
  const line = Math.max(96, viewportHeight * 0.47 - headerHeight);
  const logoWidth = Math.max(1, innerWidth - gutterLeft - gutterRight) * 0.66;
  const logoHeight = logoAspect > 0 ? Math.min(96, logoWidth / logoAspect) : 96;
  return {
    innerWidth, innerHeight, headerHeight, topBleed, sideBleed, gutterLeft, gutterRight,
    line, logoWidth, logoHeight,
    width: innerWidth + sideBleed * 2,
    height: innerHeight + topBleed,
    sharpHeight: line + 144 + topBleed,
    blurHeight: innerHeight + topBleed * 2,
  };
}

export function detailArtworkCrop(layout, aspect = 16 / 9, top = 0) {
  const height = Math.max(1, layout.sharpHeight - top) * 0.8;
  const width = Math.max(layout.width, height * aspect);
  const imageHeight = width / aspect;
  return {
    x: layout.sideBleed + (layout.innerWidth - width) / 2,
    y: top,
    width,
    height: imageHeight,
  };
}
