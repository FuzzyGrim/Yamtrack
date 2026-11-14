// One size for the app's small option dropdowns (the sidebar row options and the
// Search media type menu), so they match on web and native. The sidebar row options
// set the size; change it here, not in each menu.
export const DROPDOWN = {
  width: 208,
  radius: 8,
  paddingVertical: 4,
  itemPaddingHorizontal: 12,
  itemPaddingVertical: 10,
  itemGap: 8,
  fontSize: 14,
};

export function toggleDropdownSelection(selected, value) {
  return selected.includes(value) ? selected.filter(entry => entry !== value) : [...selected, value];
}

// Under the trigger, flipping above it when there is no room below. `align: 'end'` (the default, for a trigger
// at the right such as a gear or a row's dots) lines the right edges up and, when that would push the menu
// past the left screen edge, lines the left edges up instead. `align: 'start'` (a toolbar trigger) lines the
// left edges up and only slides the menu left when it would run past the right edge. Either way it stays
// inside the screen.
export function dropdownPlacement(anchor, bounds, estimatedHeight = 112, width = DROPDOWN.width, align = 'end',
  inset = { top: 0, right: 0, bottom: 0, left: 0 }) {
  const x = anchor.x - bounds.x;
  const y = anchor.y - bounds.y;
  const triggerRight = x + anchor.width;
  // The usable area is the screen less the safe area (the side rail, say): the menu is placed inside it.
  const minLeft = inset.left + 8;
  const maxLeft = Math.max(minLeft, bounds.width - inset.right - width - 8);
  const fitLeft = (left) => Math.min(Math.max(minLeft, left), maxLeft);
  const horizontal = align === 'start'
    ? { left: fitLeft(x) }
    : triggerRight - width >= minLeft
      ? { right: Math.max(inset.right + 8, bounds.width - triggerRight) }
      : { left: fitLeft(x) };
  const usableBottom = bounds.height - inset.bottom;
  const below = y + anchor.height + 4;
  return below + Math.min(estimatedHeight, usableBottom - 16) > usableBottom - 8
    ? { ...horizontal, bottom: Math.max(8 + inset.bottom, bounds.height - y + 4) }
    : { ...horizontal, top: below };
}
