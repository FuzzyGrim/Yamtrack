import React from 'react';
import { Stack } from 'expo-router';
import { useDevicePose } from '../data/useDevicePose';
import { useVerticalBarEdgeIOS } from 'expo-status-bar';
import LibrarySettingsGear from './LibrarySettingsGear';

// The side rail exists on a hinged device or when the system moves the status bar into a vertical bar.
// There the settings are one system Liquid Glass button at the bottom of the rail; otherwise the
// in-page gear. The menu takes the same items either way: `{ label, onSelect }` rows and `{ heading }` groups.
export default function LibrarySettings({ items, palette }) {
  const pose = useDevicePose();
  const railEdge = useVerticalBarEdgeIOS();
  if (!pose.hasHinge && !railEdge) return <LibrarySettingsGear items={items} palette={palette} />;
  // A divider or a heading starts a new inline section; iOS draws a separator between inline menus.
  const rows = [{ actions: [] }];
  items.forEach(item => {
    if (item.separator) rows.push({ actions: [] });
    else if (item.heading) rows.push({ title: item.heading, actions: [] });
    else rows[rows.length - 1].actions.push(item);
  });
  const action = item => <Stack.Toolbar.MenuAction key={item.key || item.label} icon={item.symbol} onPress={item.onSelect}>
    {item.label}
  </Stack.Toolbar.MenuAction>;
  return <Stack.Toolbar placement="bottom">
    <Stack.Toolbar.Menu icon="gearshape" accessibilityLabel="View settings" tintColor={palette.text}>
      {rows.filter(row => row.actions.length).map((row, index) => <Stack.Toolbar.Menu key={row.title || index} inline
        title={row.title}>{row.actions.map(action)}</Stack.Toolbar.Menu>)}
    </Stack.Toolbar.Menu>
  </Stack.Toolbar>;
}
