import React from 'react';
import { BottomSheet } from '@expo/ui';
import { Button, NavigationStack, RNHostView, Toolbar, ToolbarItem } from '@expo/ui/swift-ui';
import { ignoreSafeArea } from '@expo/ui/swift-ui/modifiers';
import { useVerticalBarEdgeIOS } from 'expo-status-bar';
import { useDevicePose } from '../data/useDevicePose';

// The iOS sheet header may place its close control in the Duo's system rail;
// its body reapplies the app's safe-area insets separately.
const horizontal = ignoreSafeArea({ regions: 'container', edges: 'horizontal' });
export function SheetContainer({ modifiers = [], ...props }) {
  return <BottomSheet {...props} modifiers={[horizontal, ...modifiers]} />;
}
export function useNativeSheetClose(insets) {
  const pose = useDevicePose();
  const railEdge = useVerticalBarEdgeIOS();
  // Expo Go may not expose the vertical-bar trait; the hinge and sheet insets
  // identify its rail while leaving the inner portrait display horizontal.
  return railEdge || (pose.hasHinge && (insets.right > 0 ? 'right' : insets.left > 0 ? 'left' : null));
}
export function SheetHost({ nativeClose, onClose, ...props }) {
  const content = <RNHostView {...props} modifiers={[horizontal]} />;
  if (!nativeClose || !onClose) return content;
  // The system owns the rail's dynamically changing status-bar exclusion zone.
  return <NavigationStack><Toolbar>
    {content}
    <Toolbar.Content><ToolbarItem placement="cancellationAction">
      <Button label="Close" systemImage="xmark" onPress={onClose} />
    </ToolbarItem></Toolbar.Content>
  </Toolbar></NavigationStack>;
}
