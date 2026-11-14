import { resolvePose } from '../ui/devicePose';

// The PWA's device pose (docs/architecture/device-poses.md). The web has no hinge API, so the pose comes from the
// window's size: a small screen is `closed`, a window with room for two screens is `open`. It is written on <html>
// as data-pose, data-pose-fold and data-pose-orientation and kept current on resize and rotation, so CSS and
// scripts can address a pose. The same thresholds are the Tailwind variants pose-closed, pose-open, pose-vertical
// and pose-horizontal in input.css, which a first paint can use before this script runs.
function currentPose() {
  return resolvePose({ width: window.innerWidth, height: window.innerHeight });
}

// The readout is CSS: html[data-pose-debug]::after draws data-pose and data-pose-size (controls.css), so this
// adapter only sets attributes. `?pose=debug` turns it on and `?pose=off` turns it off; the choice is kept.
function syncDebug() {
  const params = new URLSearchParams(window.location.search);
  if (params.get('pose') === 'off') window.localStorage?.removeItem('floppy:pose-debug');
  if (params.get('pose') === 'debug') window.localStorage?.setItem('floppy:pose-debug', '1');
  const root = document.documentElement;
  if (window.localStorage?.getItem('floppy:pose-debug') === '1') root.dataset.poseDebug = '';
  else delete root.dataset.poseDebug;
}

function apply() {
  const pose = currentPose();
  const root = document.documentElement;
  if (root.dataset.pose !== pose.key) {
    root.dataset.pose = pose.key;
    root.dataset.poseFold = pose.fold;
    root.dataset.poseOrientation = pose.orientation;
    window.dispatchEvent(new CustomEvent('devicepose', { detail: pose }));
  }
  root.dataset.poseSize = `${pose.width}x${pose.height}`;
  window.__devicePose = pose;
  syncDebug();
}

export function watchDevicePose() {
  apply();
  window.addEventListener('resize', apply);
  window.addEventListener('orientationchange', apply);
}
