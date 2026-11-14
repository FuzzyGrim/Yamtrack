import React from 'react';
import { Text } from 'react-native';

// Both adapters supply already formatted and translated activity text.
export function DetailActivitySubtitle({ text, color, style }) {
  if (!text) return null;
  const parts = text.split(' • ');
  return <Text style={[{ color }, style]}>{parts.map((part, index) =>
    <React.Fragment key={index}>{index > 0 ? <Text style={{ color: '#fff' }}>{' • '}</Text> : null}{part}</React.Fragment>)}</Text>;
}
