import React, { useEffect, useState } from 'react';
import { Image, StyleSheet, View } from 'react-native';
import { detailArtworkCrop } from './detailHeroLayout';

export function DetailBackdrop({ source, layout, background, onSharpEnd, initialAspect }) {
  const [aspect, setAspect] = useState(initialAspect || 16 / 9);
  const crop = detailArtworkCrop(layout, aspect);
  const sharpEnd = Math.min(layout.sharpHeight, crop.height);
  useEffect(() => { onSharpEnd?.(sharpEnd); }, [onSharpEnd, sharpEnd]);
  return <View pointerEvents="none" style={{ position: 'absolute', top: -layout.topBleed,
    left: -layout.sideBleed, width: layout.width, height: layout.height }}>
    <Image source={{ uri: source }} blurRadius={56} resizeMode="cover" accessible={false}
      onLoad={event => { const size = event.nativeEvent?.source; if (size?.width && size?.height) setAspect(size.width / size.height); }}
      style={[StyleSheet.absoluteFill, { height: layout.blurHeight, transform: [{ scale: 1.15 }] }]} />
    <View style={{ position: 'absolute', left: 0, top: 0, width: layout.width, height: sharpEnd,
      backgroundImage: `url(${JSON.stringify(source)})`, backgroundSize: `${crop.width}px ${crop.height}px`, backgroundPosition: `${crop.x}px ${crop.y}px`, backgroundRepeat: 'no-repeat',
      maskImage: 'linear-gradient(to bottom, black 0%, black 38%, rgba(0,0,0,.85) 50%, rgba(0,0,0,.6) 62%, rgba(0,0,0,.35) 74%, rgba(0,0,0,.15) 87%, transparent)',
    }} />
    <View style={[StyleSheet.absoluteFill, { backgroundColor: 'rgba(0,0,0,.1)' }]} />
    <View style={[StyleSheet.absoluteFill, { backgroundImage:
      `linear-gradient(to bottom, transparent 45%, ${background})` }]} />
  </View>;
}
