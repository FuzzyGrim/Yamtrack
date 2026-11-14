import { Modal, ModalCloseButton } from '../Modal';
import React, { useMemo, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, useWindowDimensions, View } from 'react-native';
import { Icon } from '../Icon';
import { SelectField } from '../SelectField';

const FALLBACK_PALETTE = {
  surface: '#27272a',
  surfaceStrong: '#3f3f46',
  surfaceMuted: '#52525b',
  border: '#52525b',
  text: '#fafafa',
  textSecondary: '#d4d4d8',
  textMuted: '#a1a1aa',
  accent: '#6366f1',
  accentText: '#ffffff',
  overlay: 'rgba(0, 0, 0, 0.55)',
};

/**
 * A labelled action menu. Items keep the legacy contract:
 * `{ heading }`, `{ label, onSelect }`, `{ label, href }`, or
 * `{ label, actions: [{ label, onSelect, selected }] }`.
 *
 * `href` rows call `onOpenUrl`. Native cannot open links directly from
 * presentation, so the owning adapter supplies navigation or `Linking`.
 */
export function ActionMenu({
  label,
  items = [],
  empty,
  disabled = false,
  secondary = true,
  icon,
  palette = {},
  closeLabel = 'Close menu',
  onOpenUrl,
  testID,
}) {
  const [open, setOpen] = useState(false);
  const { width } = useWindowDimensions();
  const colors = useMemo(() => ({ ...FALLBACK_PALETTE, ...palette }), [palette]);
  const compact = width < 640;
  const actionable = items.some(item => !item.heading);

  function close() {
    setOpen(false);
  }

  function choose(item) {
    if (item.disabled) return;
    close();
    if (item.href && onOpenUrl) {
      onOpenUrl(item.href);
      return;
    }
    item.onSelect?.();
  }

  return <View style={styles.root} testID={testID}>
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityState={{ expanded: open, disabled }}
      disabled={disabled}
      testID={testID ? `${testID}-trigger` : undefined}
      onPress={() => setOpen(current => !current)}
      style={({ pressed }) => [
        icon ? styles.iconTrigger : styles.trigger,
        {
          backgroundColor: secondary ? colors.surface : colors.accent,
          borderColor: secondary ? colors.border : colors.accent,
        },
        pressed && !disabled && {
          backgroundColor: secondary ? colors.surfaceStrong : colors.accent,
          opacity: secondary ? 1 : 0.85,
        },
        disabled && styles.disabled,
      ]}>
      {icon
        ? (typeof icon === 'string'
          ? <Text accessible={false} style={[styles.iconText, { color: secondary ? colors.textSecondary : colors.accentText }]}>{icon}</Text>
          : icon)
        : <>
          <Text numberOfLines={1} style={[styles.triggerText, { color: secondary ? colors.textSecondary : colors.accentText }]}>{label}</Text>
          <Text accessible={false} style={[styles.chevron, { color: secondary ? colors.textMuted : colors.accentText }]}>▾</Text>
        </>}
    </Pressable>

    <Modal
      transparent
      visible={open}
      animationType="fade"
      statusBarTranslucent
      onRequestClose={close}>
      <View style={[styles.overlay, compact ? styles.overlayCompact : styles.overlayWide,
        { backgroundColor: colors.overlay }]}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={closeLabel}
          onPress={close}
          style={styles.backdrop} />
        <View
          accessibilityViewIsModal
          testID={testID ? `${testID}-menu` : undefined}
          style={[styles.sheet, compact ? styles.sheetCompact : styles.sheetWide,
            { backgroundColor: colors.surface, borderColor: colors.border }]}>
          <View style={[styles.header, { borderBottomColor: colors.border }]}>
            <Text accessibilityRole="header" style={[styles.title, { color: colors.text }]}>{label}</Text>
            <ModalCloseButton
              accessibilityRole="button"
              accessibilityLabel={closeLabel}
              onPress={close}
              style={({ pressed }) => [styles.close, pressed && styles.pressed]}>
              <Text accessible={false} style={[styles.closeText, { color: colors.textMuted }]}>×</Text>
            </ModalCloseButton>
          </View>

          <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent} keyboardShouldPersistTaps="handled">
            {items.map((item, index) => {
              const key = `${item.key || item.label || 'item'}-${index}`;
              if (item.separator) {
                return <View key={key} accessibilityRole="none" style={[styles.separator, { backgroundColor: colors.textMuted, opacity: 0.4 }]} />;
              }
              if (item.heading) {
                return <Text key={key} accessibilityRole="header" style={[styles.heading, { color: colors.textMuted }]}>{item.heading}</Text>;
              }
              if (item.actions) {
                return <View key={key} style={styles.group}>
                  <Text style={[styles.groupLabel, { color: colors.textMuted }]}>{item.label}</Text>
                  <View style={styles.groupActions}>
                    {item.actions.map((action, actionIndex) => {
                      const actionDisabled = !!action.disabled;
                      return <Pressable
                        key={`${action.key || action.label}-${actionIndex}`}
                        accessibilityRole="menuitem"
                        accessibilityLabel={action.accessibilityLabel || `${item.label}: ${action.label}`}
                        accessibilityState={{ selected: !!action.selected, disabled: actionDisabled }}
                        disabled={actionDisabled}
                        onPress={() => choose(action)}
                        style={({ pressed }) => [
                          styles.groupAction,
                          { backgroundColor: colors.surfaceStrong, borderColor: colors.border },
                          pressed && !actionDisabled && { backgroundColor: colors.surfaceMuted },
                          actionDisabled && styles.disabled,
                        ]}>
                        <Text style={[styles.groupActionText, { color: action.selected ? colors.text : colors.accent }]}>{action.label}</Text>
                      </Pressable>;
                    })}
                  </View>
                </View>;
              }
              return <Pressable
                key={key}
                accessibilityRole={item.href ? 'link' : 'menuitem'}
                accessibilityLabel={item.label}
                accessibilityState={{ disabled: !!item.disabled }}
                disabled={item.disabled}
                onPress={() => choose(item)}
                style={({ pressed }) => [
                  styles.item,
                  pressed && !item.disabled && { backgroundColor: colors.surfaceStrong },
                  item.disabled && styles.disabled,
                ]}>
                {item.icon ? <Icon spec={item.icon} size={16} color={colors.textSecondary} /> : null}
                <Text style={[styles.itemText, { color: colors.textSecondary }]}>{item.label}</Text>
              </Pressable>;
            })}
            {!actionable && empty ? <Text style={[styles.empty, { color: colors.textMuted }]}>{empty}</Text> : null}
          </ScrollView>
        </View>
      </View>
    </Modal>
  </View>;
}

/**
 * Hidden-label select used in filter toolbars. The caller owns the value and
 * receives the same `{ target: { name, value } }` change shape as SelectField.
 */
export function FilterDropdown(props) {
  return <SelectField {...props} labelHidden filter />;
}

const styles = StyleSheet.create({
  root: { alignSelf: 'flex-start' },
  trigger: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 8,
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 9,
  },
  iconTrigger: {
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderRadius: 8,
    width: 36,
    height: 36,
  },
  triggerText: { flexShrink: 1, fontSize: 14 },
  chevron: { fontSize: 14 },
  iconText: { fontSize: 16, fontWeight: '600' },
  disabled: { opacity: 0.5 },
  pressed: { opacity: 0.7 },
  overlay: { flex: 1 },
  overlayCompact: { justifyContent: 'flex-end' },
  overlayWide: { justifyContent: 'center', alignItems: 'center', padding: 24 },
  backdrop: { position: 'absolute', top: 0, right: 0, bottom: 0, left: 0 },
  sheet: { overflow: 'hidden', borderWidth: 1 },
  sheetCompact: {
    width: '100%',
    maxHeight: '82%',
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    borderBottomLeftRadius: 0,
    borderBottomRightRadius: 0,
  },
  sheetWide: {
    width: 340,
    maxWidth: '100%',
    maxHeight: '78%',
    borderRadius: 12,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 8,
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderBottomWidth: 1,
  },
  title: { flexShrink: 1, fontSize: 16, fontWeight: '700' },
  close: {
    width: 32,
    height: 32,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 8,
  },
  closeText: { fontSize: 22, lineHeight: 24 },
  body: { maxHeight: 420 },
  bodyContent: { paddingVertical: 6 },
  heading: { fontSize: 12, lineHeight: 17, paddingHorizontal: 16, paddingVertical: 8 },
  group: { gap: 6, paddingHorizontal: 16, paddingVertical: 8 },
  groupLabel: { fontSize: 12, fontWeight: '600' },
  groupActions: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  groupAction: { borderWidth: 1, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 6 },
  groupActionText: { fontSize: 13, fontWeight: '600' },
  separator: { height: 1, marginVertical: 6 },
  item: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 16, paddingVertical: 12 },
  itemText: { fontSize: 14 },
  empty: { fontSize: 13, lineHeight: 18, paddingHorizontal: 16, paddingVertical: 12 },
});
