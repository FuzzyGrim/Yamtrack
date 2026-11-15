import React, { useRef } from 'react';
import { Vibration } from 'react-native';
import { MenuView } from '@expo/ui/community/menu';

// The Android card Pressable consumes the hold; show the Material menu from it
// and provide feedback because the native wrapper does not trigger a haptic.
export default function NativeCardMenu({ actions, onPressAction, children }) {
  const menu = useRef(null);
  return <MenuView ref={menu} actions={actions} shouldOpenOnLongPress
    onOpenMenu={() => Vibration.vibrate(10)} onPressAction={onPressAction}>
    {React.cloneElement(children, { onLongPress: () => menu.current?.show() })}
  </MenuView>;
}
