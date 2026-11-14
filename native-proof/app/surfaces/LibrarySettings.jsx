import React from 'react';
import LibrarySettingsGear from './LibrarySettingsGear';

// Android and web keep the gear in the page header; Liquid Glass and the side rail are iOS-only.
export default function LibrarySettings(props) {
  return <LibrarySettingsGear {...props} />;
}
