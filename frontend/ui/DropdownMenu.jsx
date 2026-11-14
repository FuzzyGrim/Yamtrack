import React, { useContext, useRef, useState } from 'react';
import { Animated, Pressable, StyleSheet, Text, TextInput, View, useWindowDimensions } from 'react-native';
import { Modal } from './Modal';
import { DROPDOWN, dropdownPlacement } from './dropdownMetrics';
import { DropdownInsetsContext, KeyboardOverlapContext } from './keyboardOverlapContext';

// Anchor coordinates are in the window, while a sheet's modal host is local to
// the sheet. Measure the host so the same menu positions correctly in both.
export function DropdownMenu({ open, onClose, children, palette, closeLabel, anchor,
  estimatedHeight = 112, align = 'end', layerProps }) {
  // A caller may clear the anchor in the same update that closes the menu. It must close the way it opened:
  // switching to the fade animation while closing flashes a dark overlay across the page and leaves a layer
  // over it that swallows clicks until the fade ends.
  const openedAnchored = useRef(false);
  if (open) openedAnchored.current = Boolean(anchor);
  const anchored = open ? Boolean(anchor) : openedAnchored.current;
  return <Modal transparent visible={open} animationType={anchored ? 'none' : 'fade'} onRequestClose={onClose}>
    <DropdownMenuContent {...{ onClose, children, palette, closeLabel, anchor, estimatedHeight, align, layerProps }} />
  </Modal>;
}

// Keep keyboard-driven layout inside the modal host: Duo registers modal children in a separate
// commit, which would otherwise consume the keyboard's LayoutAnimation before the menu resizes.
function DropdownMenuContent({ onClose, children, palette, closeLabel, anchor, estimatedHeight, align, layerProps }) {
  const viewport = useWindowDimensions();
  const host = useRef(null);
  const [bounds, setBounds] = useState(null);
  const dropdown = Boolean(anchor);
  const inset = useContext(DropdownInsetsContext);
  const useKeyboardOverlap = useContext(KeyboardOverlapContext);
  const keyboard = useKeyboardOverlap(true);
  const width = bounds?.width || viewport.width;
  const fullHeight = bounds?.height || viewport.height;
  // Placement ignores the keyboard, so opening it never moves the menu: the top stays where it was and only
  // the bottom comes up, the menu getting shorter and scrolling (the Search suggestions fit the same way).
  const placement = anchor ? dropdownPlacement(anchor, { ...(bounds || { x: 0, y: 0 }), width, height: fullHeight }, estimatedHeight, undefined, align, inset) : null;
  const baseRoom = placement?.top != null ? fullHeight - inset.bottom - placement.top - 8
    : placement?.bottom != null ? fullHeight - placement.bottom - 8 : fullHeight - 16;
  const overlap = placement?.bottom != null ? 0 : keyboard;
  const room = typeof overlap === 'number' ? Math.max(80, baseRoom - overlap)
    : overlap.interpolate({ inputRange: [0, Math.max(1, baseRoom - 80)], outputRange: [Math.max(80, baseRoom), 80], extrapolate: 'clamp' });
  const paddingBottom = typeof keyboard === 'number' ? 24 + keyboard : Animated.add(24, keyboard);
  return <Animated.View ref={host} {...layerProps} style={dropdown ? StyleSheet.absoluteFill : [styles.modalRoot, { paddingBottom }]}
      onLayout={() => host.current?.measureInWindow?.((hostX, hostY, hostWidth, hostHeight) => {
        setBounds(current => current?.x === hostX && current?.y === hostY && current?.width === hostWidth && current?.height === hostHeight
          ? current : { x: hostX, y: hostY, width: hostWidth, height: hostHeight });
      })}>
      <Pressable accessibilityRole="button" accessibilityLabel={closeLabel} onPress={onClose}
        style={[StyleSheet.absoluteFill, dropdown ? null : { backgroundColor: palette.overlay }]} />
      <Animated.View accessibilityViewIsModal style={[styles.menu, dropdown && [styles.popover, placement], {
        maxHeight: room, backgroundColor: dropdown ? palette.surface : palette.surfaceStrong,
        borderColor: palette.border,
      }]}>{children}</Animated.View>
    </Animated.View>;
}

// One row of an app dropdown: the sidebar options, the Search media type and the library settings menu all
// draw it, so their size, spacing, colours and hover match. The row owns its colours (text, muted detail and
// the surface-muted hover/selected background) and reads them from the caller's palette; a caller only adds
// `danger` for a destructive row. `icon` is an already-rendered node (each platform supplies its own glyph).
export function DropdownItem({ label, icon, detail, chevron = false, checked = false, selected = false, disabled = false, danger = false, onPress, palette,
  numberOfLines, accessibilityRole = 'menuitem', testID }) {
  const color = danger ? palette.danger : palette.textSecondary;
  return <Pressable accessibilityRole={accessibilityRole} accessibilityLabel={label}
    accessibilityState={{ disabled, selected }} disabled={disabled} onPress={onPress} testID={testID}
    style={({ hovered, pressed }) => [styles.item, (hovered || pressed || selected) && !disabled && { backgroundColor: palette.surfaceMuted },
      disabled && { opacity: 0.5 }]}>
    {icon || null}
    <Text numberOfLines={numberOfLines} style={[styles.itemText, { color }]}>{label}</Text>
    {detail ? <Text style={[styles.itemDetail, { color: palette.textMuted }]}>{detail}</Text> : null}
    {checked ? <Text accessible={false} style={[styles.itemDetail, { color: palette.accent || palette.textSecondary }]}>✓</Text> : null}
    {chevron ? <Text accessible={false} style={[styles.chevron, { color: palette.textMuted }]}>›</Text> : null}
  </Pressable>;
}

// The first row of a level one deeper than the menu's root: it names the level and goes back up. A menu
// goes a level deeper with a `chevron` row (a multi-select, say) whose press swaps the menu's rows for that
// level's rows, led by this one.
export function DropdownBackItem({ label, onPress, palette }) {
  return <DropdownItem label={label} accessibilityRole="button" onPress={onPress} palette={palette}
    icon={<Text accessible={false} style={[styles.back, { color: palette.textMuted }]}>‹</Text>} />;
}

// The quiet rule between groups of rows: the collection Summary divider (muted text colour at 40%).
export function DropdownSeparator({ palette }) {
  return <View accessibilityRole="none" style={[styles.separator, { backgroundColor: palette.textMuted }]} />;
}

// A labelled text field inside a menu level (a search box, a range's From and To).
export function DropdownField({ label, palette, ...input }) {
  return <View style={styles.field}>
    {label ? <Text style={[styles.fieldLabel, { color: palette.textMuted }]}>{label}</Text> : null}
    <TextInput accessibilityLabel={input.accessibilityLabel || label} placeholderTextColor={palette.textMuted}
      {...input} style={[styles.input, { color: palette.text, borderColor: palette.border, backgroundColor: palette.background || palette.surfaceStrong }]} />
  </View>;
}

export function DropdownHeading({ children, palette }) {
  return <Text accessibilityRole="header" style={[styles.heading, { color: palette.textMuted }]}>{children}</Text>;
}

const styles = StyleSheet.create({
  item: { flexDirection: 'row', alignItems: 'center', gap: DROPDOWN.itemGap,
    paddingHorizontal: DROPDOWN.itemPaddingHorizontal, paddingVertical: DROPDOWN.itemPaddingVertical },
  itemText: { flexShrink: 1, fontSize: DROPDOWN.fontSize },
  itemDetail: { marginLeft: 'auto', fontSize: 12 },
  chevron: { fontSize: 16, lineHeight: 18 },
  field: { gap: 4, paddingHorizontal: DROPDOWN.itemPaddingHorizontal, paddingVertical: 4 },
  fieldLabel: { fontSize: 12 },
  input: { minHeight: 36, paddingHorizontal: 10, borderRadius: 6, borderWidth: 1, fontSize: DROPDOWN.fontSize },
  back: { fontSize: 18, lineHeight: 18, width: 16, textAlign: 'center' },
  separator: { height: 1, opacity: 0.4, marginVertical: 4 },
  heading: { fontSize: 12, paddingHorizontal: DROPDOWN.itemPaddingHorizontal, paddingVertical: 8 },
  modalRoot: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 },
  menu: { width: DROPDOWN.width, borderRadius: DROPDOWN.radius, borderWidth: 1, paddingVertical: DROPDOWN.paddingVertical },
  popover: { position: 'absolute', shadowColor: '#000', shadowOpacity: 0.35, shadowRadius: 12,
    shadowOffset: { width: 0, height: 6 }, elevation: 8 },
});
