import React, { useState } from 'react';
import { Image, StyleSheet, View } from 'react-native';
import { detailArtworkCrop } from './detailHeroLayout';

// Portable fallback; the native and browser renderers use continuous masks.
export function DetailBackdrop({ source, layout, background }) {
  const [aspect, setAspect] = useState(16 / 9);
  const crop = detailArtworkCrop(layout, aspect);
  const frame = { position: 'absolute', top: -layout.topBleed,
    left: -layout.sideBleed, width: layout.width, height: layout.height };
  return <View pointerEvents="none" style={frame} accessibilityElementsHidden>
    <Image source={{ uri: source }} blurRadius={56} resizeMode="cover"
      style={[StyleSheet.absoluteFill, { height: layout.blurHeight }]} accessible={false}
      onLoad={event => { const size = event.nativeEvent?.source; if (size?.width && size?.height) setAspect(size.width / size.height); }} />
    {Array.from({ length: 24 }, (_, index) => {
      const fadeHeight = Math.min(layout.sharpHeight, crop.height);
      const top = fadeHeight * index / 24;
      return <View key={index} style={{ position: 'absolute', top, left: 0, right: 0,
        height: fadeHeight / 24 + 1, overflow: 'hidden', opacity: Math.max(0, 1 - index / 23) }}>
        <Image source={{ uri: source }} resizeMode="cover" accessible={false}
          style={{ position: 'absolute', left: crop.x, top: crop.y - top, width: crop.width, height: crop.height }} />
      </View>;
    })}
    {Array.from({ length: 24 }, (_, index) => <View key={`fade-${index}`} style={{
      position: 'absolute', left: 0, right: 0, top: layout.height * index / 24,
      height: layout.height / 24 + 1, backgroundColor: background,
      opacity: Math.max(0, (index / 23 - 0.45) / 0.55),
    }} />)}
  </View>;
}
