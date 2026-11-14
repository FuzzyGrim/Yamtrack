# Device poses

A layout can be written for a pose instead of a screen size. A pose is a fold (the hinge) and an orientation (the
window). The same names work in Expo, in the PWA and in tests, so "Media Details when the Duo is open vertical" means
one thing everywhere.

| Pose | Fold | Window | Typical device |
| --- | --- | --- | --- |
| `closed-vertical` | closed | taller than wide | Duo closed in portrait, an iPhone in portrait, the PWA on a phone |
| `closed-horizontal` | closed | wider than tall | Duo closed in landscape, an iPhone in landscape |
| `open-vertical` | fully open | taller than wide | Duo flat, held tall; an iPad in portrait |
| `open-horizontal` | fully open | wider than tall | Duo flat, held wide; an iPad in landscape, a desktop PWA window |
| `partial-vertical` | partially open | taller than wide | Duo part open (one screen is the window, the other is reserved) |
| `partial-horizontal` | partially open | wider than tall | Duo part open in landscape |

`orientation` is `vertical` when the window is as tall as it is wide or taller, and `horizontal` when it is wider.

## A device with no hinge

An iPhone, an iPad and the PWA in a browser report no hinge, so their pose comes from the window alone. A window whose
**short side is under 600** is one small screen: `closed`. A window at least 600 on both sides has room for two
screens: `open`. `partial` only comes from a real hinge.

That is the point of the rule. Anything written for `closed-vertical` also holds on an iPhone in portrait, and
anything written for `open-horizontal` also holds on a tablet or a wide PWA window, so improving a Duo pose improves
the devices closest to it. 600 is the PWA's phone breakpoint and the shell's full-width menu rule, measured on the
short side so a phone in landscape is still one small screen.

## Using a pose

All the logic is `frontend/ui/devicePose.js`, which has no platform imports:

```js
import { poseValue, isPose } from '../ui/devicePose';

const columns = poseValue(pose, { default: 1, open: 2, 'open-horizontal': 3 });
if (isPose(pose, 'partial')) { /* ... */ }
```

`poseValue` takes the most specific key that matches (a whole pose, then a fold, then an orientation) and falls back to
`default`, so a rule can start broad and be narrowed for one pose.

- **Expo:** `useDevicePose()` (`native-proof/app/data/useDevicePose.js`) combines `expo-hinge` and the window size. Media
  Details also gets `pose` on its `heroViewport`.
- **PWA:** `frontend/settings/pose.web.js` sets `data-pose`, `data-pose-fold` and `data-pose-orientation` on `<html>`
  and fires a `devicepose` event on `window` when it changes. `window.__devicePose` holds the current pose. CSS can use
  the attributes, or the Tailwind variants `pose-closed`, `pose-open`, `pose-vertical` and `pose-horizontal`, which
  are media queries with the same thresholds and so work on first paint, before the script runs.
- **The web has no `partial`.** There is no hinge API in the browser, so the PWA reports `closed` or `open` by size.

## One system for the shell and the screens

The Expo shell reads the same pose, not the hinge directly. `menuGeometry` (the sidebar's width and gap) takes the pose
for the fold and only adds the hinge's measured position (the division) to say where a partially open window splits.
The shell's "is there a side rail" check, the page scroll view that reaches under the rail, and the settings menu on
the rail all ask `pose.hasHinge` (a hinge is reported at all) together with the system's vertical bar. Nothing else in
the app imports `expo-hinge`: `useDevicePose` is the one place that does, so a new pose rule is written once.

The sidebar's narrow-window rule is not the pose's: a content area under 600 wide gets the full-width menu on any
device, which is why a phone in landscape keeps its side menu while a phone in portrait gets the full-width one.

## Checking a pose

Both platforms have a small readout of the pose and window size, off by default:

- Expo: start Metro with `EXPO_PUBLIC_POSE_DEBUG=1`.
- PWA: open a page with `?pose=debug` (`?pose=off` turns it off again; the choice is remembered in the browser). A few pages redirect and drop the query, such as the media lists; on those run `localStorage.setItem('floppy:pose-debug', '1')` once.

In Device Hub, put the PWA and the Expo app on the two screens, turn the readout on in both, and fold, flip and
half-open the device: both should name the same pose wherever the PWA can know it.
