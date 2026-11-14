import React, { useRef, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { DropdownHeading, DropdownItem, DropdownMenu, DropdownSeparator } from './DropdownMenu';
import { Icon } from './Icon';
import { actionIcons } from './actionIcons';

const FALLBACK_PALETTE = {
  surface: '#27272a',
  surfaceStrong: '#3f3f46',
  surfaceMuted: '#52525b',
  border: '#52525b',
  text: '#f4f4f5',
  textSecondary: '#d4d4d8',
  textMuted: '#a1a1aa',
  overlay: 'rgba(0, 0, 0, 0.55)',
};

/**
 * The gear button and its menu, on the app's standard dropdown (`DropdownMenu`, the same popover the
 * Search media type and the sidebar options use). Rows are `{ label, icon, onSelect, detail?, disabled? }`,
 * `{ heading }` or `{ separator: true }`. `items` may be a function, read each time the menu opens, so
 * a page that owns the state (the web's Alpine scope) always shows the current labels.
 */
export function SettingsMenu({ label = 'View settings', items, palette, closeLabel = 'Close menu', testID }) {
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState(null);
  const trigger = useRef(null);
  const colors = { ...FALLBACK_PALETTE, ...palette };
  const rows = open ? (typeof items === 'function' ? items() : items) : [];

  function show() {
    if (trigger.current?.measureInWindow) {
      trigger.current.measureInWindow((x, y, width, height) => { setAnchor({ x, y, width, height }); setOpen(true); });
    } else { setAnchor(null); setOpen(true); }
  }

  return <View testID={testID}>
    <Pressable ref={trigger} accessibilityRole="button" accessibilityLabel={label} accessibilityState={{ expanded: open }}
      onPress={show}
      style={({ pressed }) => [styles.trigger, { backgroundColor: colors.surfaceStrong, borderColor: colors.border },
        pressed && { backgroundColor: colors.surfaceMuted }]}>
      <Icon spec={actionIcons.settings} size={20} color={colors.textSecondary} />
    </Pressable>
    <DropdownMenu open={open} anchor={anchor} palette={colors} closeLabel={closeLabel}
      estimatedHeight={rows.length * 40 + 8} onClose={() => setOpen(false)}>
      {rows.map((row, index) => {
        if (row.separator) return <DropdownSeparator key={`separator-${index}`} palette={colors} />;
        if (row.heading) return <DropdownHeading key={`heading-${row.heading}`} palette={colors}>{row.heading}</DropdownHeading>;
        return <DropdownItem key={row.key || row.label} label={row.label} detail={row.detail} disabled={row.disabled}
          onPress={() => { setOpen(false); row.onSelect?.(); }}
          palette={colors}
          icon={row.icon ? <Icon spec={row.icon} size={16} color={colors.textSecondary} /> : null} />;
      })}
    </DropdownMenu>
  </View>;
}

const styles = StyleSheet.create({
  trigger: { width: 40, height: 40, alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderRadius: 6 },
});
