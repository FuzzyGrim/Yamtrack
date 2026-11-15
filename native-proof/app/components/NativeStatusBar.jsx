import React from 'react';
import { StatusBar, useColorScheme } from 'react-native';

export default function NativeStatusBar({ backgroundColor }) {
  const scheme = useColorScheme();
  return <StatusBar barStyle={scheme === 'dark' ? 'light-content' : 'dark-content'} backgroundColor={backgroundColor} />;
}
