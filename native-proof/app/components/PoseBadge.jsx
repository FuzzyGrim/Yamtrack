import React from 'react';
import { Text, View } from 'react-native';
import { useDevicePose } from '../data/useDevicePose';

// A small readout of the current device pose and window size for tuning layouts on a Duo. Off unless Metro was
// started with EXPO_PUBLIC_POSE_DEBUG=1 (the PWA's is `?pose=debug`).
export default function PoseBadge() {
  const pose = useDevicePose();
  if (process.env.EXPO_PUBLIC_POSE_DEBUG !== '1') return null;
  return <View pointerEvents="none" style={{ position: 'absolute', left: 8, bottom: 8, zIndex: 99999,
    paddingHorizontal: 8, paddingVertical: 2, borderRadius: 6, backgroundColor: 'rgba(0,0,0,0.7)' }}>
    <Text style={{ color: '#fff', fontSize: 11, fontWeight: '600' }}>
      {`${pose.key} ${Math.round(pose.width)}x${Math.round(pose.height)}${pose.angle != null ? ` ${Math.round(pose.angle)}°` : ''}`}
    </Text>
  </View>;
}
