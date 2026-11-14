import { useEffect, useRef, useState } from 'react';
import { Animated, Dimensions, Easing, Keyboard, LayoutAnimation, Platform } from 'react-native';

const ios = Platform.OS === 'ios';

// Search suggestions use the keyboard's layout animation; dropdowns animate their height explicitly.
function followKeyboard(event) {
  if (!event?.duration || !event.easing) return;
  LayoutAnimation.configureNext({
    duration: event.duration,
    update: { duration: event.duration, type: LayoutAnimation.Types[event.easing] || LayoutAnimation.Types.keyboard },
  });
}

// How far the keyboard covers the bottom of the window. iOS announces the keyboard before it moves
// (`Will` events, with its duration and curve), so the change can animate in step with it; Android only
// reports it once it has settled.
export function useKeyboardOverlap(animated = false) {
  const [height, setHeight] = useState(0);
  const animatedHeight = useRef(new Animated.Value(0)).current;
  const targetHeight = useRef(0);
  useEffect(() => {
    // The keyboard may already be up when the field mounts, so read it as well as listen.
    const current = Keyboard.metrics?.();
    if (current?.height) { setHeight(current.height); targetHeight.current = current.height; animatedHeight.setValue(current.height); }
    const update = (value, event) => {
      if (targetHeight.current === value) return;
      targetHeight.current = value;
      if (animated) {
        // iOS provides the keyboard duration; Android only reports its final frame.
        Animated.timing(animatedHeight, { toValue: value, duration: ios ? event?.duration || 250 : 0,
          easing: Easing.out(Easing.poly(4)), useNativeDriver: false }).start();
      } else {
        followKeyboard(event);
        setHeight(value);
      }
    };
    const shown = Keyboard.addListener(ios ? 'keyboardWillChangeFrame' : 'keyboardDidShow', event => {
      const covered = ios
        ? Math.max(0, Dimensions.get('window').height - (event.endCoordinates?.screenY ?? Dimensions.get('window').height))
        : event.endCoordinates?.height || 0;
      update(covered, event);
    });
    const hidden = Keyboard.addListener(ios ? 'keyboardWillHide' : 'keyboardDidHide', event => {
      update(0, event);
    });
    return () => { shown.remove(); hidden.remove(); };
  }, [animated, animatedHeight]);
  return animated ? animatedHeight : height;
}
