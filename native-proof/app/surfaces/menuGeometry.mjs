// The division frame is measured in the containing view, not screen coordinates.
// `pose` is the device pose (frontend/ui/devicePose.js): the same fold the rest of the app reads. The division,
// the hinge's measured position, only says where to split a partially open window.
export function menuGeometry(width, pose, division) {
  const partial = pose?.fold === 'partial' && division
    && division.x > 0 && division.x < width;
  // A closed hinge gives the whole window to the menu. Without one, a narrow content area does the same.
  const full = (pose?.hinged && pose.fold === 'closed') || (!partial && width < 600);
  if (full) return { full: true, menuWidth: width, gap: 0 };
  if (partial) return {
    full: false,
    menuWidth: division.x,
    gap: Math.min(division.width, width - division.x),
  };
  return { full: false, menuWidth: Math.min(288, width), gap: 0 };
}
