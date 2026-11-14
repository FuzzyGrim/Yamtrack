import React, { useId, useLayoutEffect, useState } from 'react';
import { Image, StyleSheet, View } from 'react-native';
import MaskedView from '@react-native-masked-view/masked-view';
import Svg, { Defs, LinearGradient, Rect, Stop } from 'react-native-svg';
import { detailArtworkCrop } from './detailHeroLayout';

export function DetailBackdrop({ source, layout, onSharpEnd, initialAspect }) {
  const id = useId().replace(/:/g, '');
  const sharp = `${id}-sharp`;
  const fade = `${id}-fade`;
  const [aspect, setAspect] = useState(initialAspect || 16 / 9);
  // Native search overlays the artwork; start the blend halfway through its
  // header and keep the blurred layer behind search and system insets.
  const crop = detailArtworkCrop(layout, aspect, layout.topBleed - layout.headerHeight / 2);
  const sharpHeight = Math.min(layout.sharpHeight - crop.y, crop.height);
  const sharpEnd = crop.y + sharpHeight;
  useLayoutEffect(() => { onSharpEnd?.(sharpEnd); }, [onSharpEnd, sharpEnd]);
  // Alpha masks let the app's actual background show through, including iOS
  // dynamic colors; painting a theme color into an SVG gradient loses that API.
  const sharpMask = <Svg width={layout.width} height={layout.height}>
    <Defs><LinearGradient id={sharp} x1="0" x2="0" y1="0"
      y2={sharpEnd} gradientUnits="userSpaceOnUse">
      <Stop offset={crop.y / sharpEnd} stopColor="black" stopOpacity="0" />
      <Stop offset={(crop.y + sharpHeight * 0.06) / sharpEnd} stopColor="black" stopOpacity="0.15" />
      <Stop offset={(crop.y + sharpHeight * 0.13) / sharpEnd} stopColor="black" stopOpacity="0.35" />
      <Stop offset={(crop.y + sharpHeight * 0.20) / sharpEnd} stopColor="black" stopOpacity="0.60" />
      <Stop offset={(crop.y + sharpHeight * 0.27) / sharpEnd} stopColor="black" stopOpacity="0.85" />
      <Stop offset={(crop.y + sharpHeight * 0.38) / sharpEnd} stopColor="black" />
      <Stop offset={(crop.y + sharpHeight * 0.50) / sharpEnd} stopColor="black" stopOpacity="0.85" />
      <Stop offset={(crop.y + sharpHeight * 0.62) / sharpEnd} stopColor="black" stopOpacity="0.60" />
      <Stop offset={(crop.y + sharpHeight * 0.74) / sharpEnd} stopColor="black" stopOpacity="0.35" />
      <Stop offset={(crop.y + sharpHeight * 0.87) / sharpEnd} stopColor="black" stopOpacity="0.15" />
      <Stop offset="1" stopColor="black" stopOpacity="0" />
    </LinearGradient></Defs>
    <Rect width={layout.width} height={layout.height} fill={`url(#${sharp})`} />
  </Svg>;
  const fadeMask = <Svg width={layout.width} height={layout.height}>
    <Defs><LinearGradient id={fade} x1="0" x2="0" y1="0"
      y2={layout.height} gradientUnits="userSpaceOnUse">
      <Stop offset="0" stopColor="black" />
      <Stop offset="0.45" stopColor="black" />
      <Stop offset="0.70" stopColor="black" stopOpacity="0.7" />
      <Stop offset="0.88" stopColor="black" stopOpacity="0.3" />
      <Stop offset="1" stopColor="black" stopOpacity="0" />
    </LinearGradient></Defs>
    <Rect width={layout.width} height={layout.height} fill={`url(#${fade})`} />
  </Svg>;
  return <View pointerEvents="none" accessibilityElementsHidden style={{
    position: 'absolute', top: -layout.topBleed, left: -layout.sideBleed,
    width: layout.width, height: layout.height,
  }}>
    <MaskedView style={StyleSheet.absoluteFill} maskElement={fadeMask}>
      <Image source={{ uri: source }} blurRadius={56} resizeMode="cover" accessible={false}
        onLoad={event => { const size = event.nativeEvent?.source; if (size?.width && size?.height) setAspect(size.width / size.height); }}
        style={[StyleSheet.absoluteFill, { height: layout.blurHeight, transform: [{ scale: 1.15 }] }]} />
      <MaskedView style={StyleSheet.absoluteFill} maskElement={sharpMask}>
        <Image source={{ uri: source }} resizeMode="cover" accessible={false}
          style={{ position: 'absolute', left: crop.x, top: crop.y, width: crop.width, height: crop.height }} />
      </MaskedView>
      <View style={[StyleSheet.absoluteFill, { backgroundColor: 'rgba(0,0,0,.1)' }]} />
    </MaskedView>
  </View>;
}
