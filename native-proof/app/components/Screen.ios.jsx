import React from 'react';
import { useDevicePose } from '../data/useDevicePose';
import { useVerticalBarEdgeIOS } from 'expo-status-bar';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Screen as SharedScreen } from './Screen.jsx';
import { horizontalContentPadding } from '../safeAreaSpacing.mjs';

export { Section } from './Screen.jsx';

// The safe area already contributes to the distance from the screen edge.
// With a side rail the scroll view reaches under it (the shell pads the screen by the rail's width, and
// the negative margin takes that back), so a drag that starts on the rail scrolls the page like it does
// in other iPhone Duo apps. The content padding gives the rail's width back, so nothing moves.
export function Screen({ contentStyle, ...props }) {
  const insets = useSafeAreaInsets();
  const pose = useDevicePose();
  const railEdge = useVerticalBarEdgeIOS();
  const underRail = Boolean(pose.hasHinge || railEdge);
  return <SharedScreen {...props}
    scrollStyle={underRail ? { marginLeft: -insets.left, marginRight: -insets.right } : undefined}
    contentStyle={[underRail
      ? { paddingLeft: Math.max(insets.left, 22), paddingRight: Math.max(insets.right, 22) }
      : horizontalContentPadding(insets, 22), contentStyle]} />;
}
