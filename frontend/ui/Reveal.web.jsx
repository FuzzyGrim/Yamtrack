import React from 'react';

// Web moves the panel inside a fixed clip so the browser can composite the
// animation without recalculating layout on every frame. Native uses its own
// measured-height animation because it does not use browser CSS transitions.
export function Reveal({ open, children, duration = 200 }) {
  return <div inert={!open} style={{ overflow: 'hidden', pointerEvents: open ? 'auto' : 'none' }}>
    <div className="sidebar-reveal" data-open={open ? 'true' : 'false'}
      style={{ '--sidebar-reveal-duration': `${duration}ms` }}>
      {children}
    </div>
  </div>;
}
