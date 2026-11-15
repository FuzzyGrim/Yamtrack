// Test-only stand-in for react-native: host components render plain elements so
// shared components can be rendered with react-dom in node tests.
import React from 'react';
const host = tag => React.forwardRef(function Host({ children, style, onPress, testID, accessibilityLabel }, ref) {
  return React.createElement(tag, { ref, 'data-testid': testID, 'aria-label': accessibilityLabel }, typeof children === 'function' ? children({ pressed: false }) : children);
});
export const View = host('div');
export const Text = host('span');
export const Pressable = host('button');
export const ScrollView = host('div');
export const Image = Object.assign(host('img'), { prefetch: () => Promise.resolve() });
export const ActivityIndicator = host('div');
export const Modal = host('div');
export const StyleSheet = { create: s => s, flatten: s => (Array.isArray(s) ? Object.assign({}, ...s.flat().filter(Boolean)) : s || {}), absoluteFill: {}, hairlineWidth: 1 };
export const Platform = { OS: 'ios', select: o => o.ios ?? o.native ?? o.default };
export const PanResponder = { create: () => ({ panHandlers: {} }) };
export const AccessibilityInfo = { announceForAccessibility() {} };
export const Animated = { View: host('div'), Value: class { constructor(v) { this.v = v; } setValue() {} }, timing: () => ({ start() {} }) };
export function useWindowDimensions() { return { width: 1200, height: 800, scale: 2, fontScale: 1 }; }
export const PixelRatio = { get: () => 2 };
export const Linking = { openURL() {} };
export const I18nManager = { isRTL: false };
export const Easing = { linear: x => x, inOut: f => f, ease: x => x, out: f => f, in: f => f, bezier: () => x => x };
Animated.loop = () => ({ start() {}, stop() {} });
Animated.sequence = () => ({ start() {}, stop() {} });
Animated.Value.prototype.interpolate = function () { return this; };
