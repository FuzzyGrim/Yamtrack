// The device poses a layout can be written for. Universal: no platform imports, so Expo, the PWA and the tests
// share one vocabulary. See docs/architecture/device-poses.md.
//
//   fold         closed | open | partial   (the hinge: closed, flat, or in between)
//   orientation  vertical | horizontal     (the window: taller than wide, or wider than tall)
//   key          fold-orientation          closed-vertical, closed-horizontal, open-vertical, open-horizontal,
//                                          partial-vertical, partial-horizontal
//
// A device with no hinge (an iPhone, an iPad, the PWA in a browser) gets a pose from its size alone: a window
// under SINGLE_SCREEN_MIN_SIDE on its short side is one small screen, so it is `closed`; one at least that big on
// both sides has room for two screens, so it is `open`. That is what lets a rule written for a Duo pose also hold
// on the devices closest to it: an iPhone is a closed Duo, a tablet or a wide PWA window is an open one. `partial`
// only comes from a hinge.

// The PWA's phone breakpoint and the shell's full-width menu rule (600), measured on the short side so a phone in
// landscape is still one small screen.
export const SINGLE_SCREEN_MIN_SIDE = 600;

export const FOLDS = ['closed', 'open', 'partial'];
export const ORIENTATIONS = ['vertical', 'horizontal'];
export const POSES = FOLDS.flatMap((fold) => ORIENTATIONS.map((orientation) => `${fold}-${orientation}`));

const FOLD_FOR_STATUS = { closed: 'closed', fullyOpen: 'open', partiallyOpen: 'partial' };

/**
 * @param {{ width: number, height: number, hinge?: string|null, angle?: number|null, hasHinge?: boolean }} input
 *   `hinge` is the hinge status (`closed`, `partiallyOpen`, `fullyOpen`) or null/`unknown` when there is none.
 *   `hasHinge` says the device reports a hinge at all, even one whose status is `unknown`: that is what puts the
 *   system's side rail on screen, so layouts that make room for the rail ask `pose.hasHinge`.
 */
export function resolvePose({ width, height, hinge = null, angle = null, hasHinge = false }) {
  const fromHinge = FOLD_FOR_STATUS[hinge];
  const fold = fromHinge || (Math.min(width, height) < SINGLE_SCREEN_MIN_SIDE ? 'closed' : 'open');
  const orientation = width > height ? 'horizontal' : 'vertical';
  return {
    key: `${fold}-${orientation}`,
    fold,
    orientation,
    // Whether the pose came from a real hinge, or from the window's size.
    hinged: Boolean(fromHinge),
    hasHinge: Boolean(hasHinge || fromHinge),
    angle: angle ?? null,
    width,
    height,
  };
}

export function isPose(pose, name) {
  return Boolean(pose) && (pose.key === name || pose.fold === name || pose.orientation === name);
}

/**
 * Pick a value for a pose. Keys can be a whole pose (`'open-vertical'`), a fold (`'open'`), an orientation
 * (`'horizontal'`) or `default`; the most specific match wins, and a pose with no match gets `default`.
 *
 *   poseValue(pose, { default: 1, open: 2, 'open-horizontal': 3 })
 */
export function poseValue(pose, values) {
  if (pose) {
    for (const key of [pose.key, pose.fold, pose.orientation]) {
      if (Object.prototype.hasOwnProperty.call(values, key)) return values[key];
    }
  }
  return values.default;
}
