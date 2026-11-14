import React from 'react';
import { SettingsMenu } from '../../../frontend/ui/SettingsMenu';

// The media list settings gear: the shared SettingsMenu on the standard dropdown, as the web header shows it.
export default function LibrarySettingsGear({ items, palette }) {
  return <SettingsMenu label="View settings" palette={palette} items={items} testID="library-settings" />;
}
