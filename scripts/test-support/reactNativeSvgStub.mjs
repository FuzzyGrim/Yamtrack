// Test-only stand-in for react-native-svg.
import React from 'react';
const el = props => React.createElement('svg', null, props.children);
export default el;
export const Defs = el, LinearGradient = el, RadialGradient = el, Rect = el, Stop = el, Path = el, Circle = el, G = el, Polygon = el, Line = el, Polyline = el, Ellipse = el, Mask = el, ClipPath = el, SvgXml = el;
