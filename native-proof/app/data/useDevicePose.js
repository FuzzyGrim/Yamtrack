import { useMemo } from 'react';
import { useWindowDimensions } from 'react-native';
import { useHinge } from 'expo-hinge';
import { resolvePose } from '../../../frontend/ui/devicePose';

// The device's pose (closed, open or partial, vertical or horizontal) from the hinge and the window. On a device
// with no hinge, and on web, the hinge is null and the pose comes from the window's size alone.
export function useDevicePose() {
  const { width, height } = useWindowDimensions();
  const hinge = useHinge();
  return useMemo(
    () => resolvePose({ width, height, hinge: hinge?.status, angle: hinge?.angle, hasHinge: Boolean(hinge) }),
    [width, height, hinge?.status, hinge?.angle, Boolean(hinge)],
  );
}
