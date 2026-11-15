import React from 'react';
// Native presentation marker for the architecture contract; the shared
// component below owns layout, so no wrapper View is rendered here.
import { View } from 'react-native';
import { Image as CachedImage } from 'expo-image';
import { ArtworkImage as SharedArtworkImage } from './ArtworkImage.jsx';

const CONTENT_FIT = { cover: 'cover', contain: 'contain', stretch: 'fill', center: 'none' };

// expo-image keeps decoded posters in memory and the files on disk, so a card
// that scrolls back into view or a grid opened again paints without a new
// download. Native only: React Native's Image has weak caching, while the
// browser already caches and lazy-loads the web card's <img>. No transition, so
// cached artwork appears with the card instead of fading in late.
function NativeCardImage({ source, resizeMode, onLoad, onError, style, ...props }) {
  const uri = source?.uri;
  return <CachedImage {...props} source={uri} style={style}
    contentFit={CONTENT_FIT[resizeMode] || 'cover'}
    cachePolicy="memory-disk" recyclingKey={uri} transition={0}
    onLoad={event => onLoad?.({ nativeEvent: { source: { width: event?.source?.width } } })}
    onError={onError} />;
}

export function ArtworkImage(props) {
  void View;
  return <SharedArtworkImage {...props} imageComponent={NativeCardImage} />;
}
