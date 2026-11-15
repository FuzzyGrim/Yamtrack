import React, { useMemo, useRef } from 'react';
import { PanResponder, Pressable, StyleSheet, Text, View } from 'react-native';
import { Icon } from './Icon';

const FALLBACK_TONES = {
  indigo: '#4f46e5',
  emerald: '#059669',
  amber: '#d97706',
  rose: '#e11d48',
};

const FALLBACK_PALETTE = {
  actionText: '#ffffff',
  actionTones: FALLBACK_TONES,
  overlay: 'rgba(0, 0, 0, 0.5)',
  reorder: 'rgba(79, 70, 229, 0.95)',
  focusStyle: {},
};

function resolveTone(palette, tone) {
  return palette?.actionTones?.[tone]
    || FALLBACK_TONES[tone]
    || palette?.link
    || FALLBACK_TONES.indigo;
}

function ActionGlyph({ action, palette }) {
  if (action.icon) {
    return <Icon spec={action.icon} color={palette.actionText} size={16} style={[styles.glyph, { color: palette.actionText }]}>
      {action.glyph || (action.title || '?').slice(0, 1).toUpperCase()}
    </Icon>;
  }
  return <Text accessible={false} style={[styles.glyph, { color: palette.actionText }]}>
    {action.glyph || (action.title || '?').slice(0, 1).toUpperCase()}
  </Text>;
}

export function MediaCardActionButton({ action, palette, onAction, renderIcon }) {
  const colors = { ...FALLBACK_PALETTE, ...(palette || {}) };
  return <Pressable
    accessibilityRole={action.kind === 'link' ? 'link' : 'button'}
    accessibilityLabel={action.title}
    accessibilityState={{ disabled: !!action.disabled, busy: !!action.busy }}
    disabled={!!action.disabled}
    onPress={() => onAction?.(action)}
    style={({ hovered, pressed, focused }) => [
      styles.roundAction,
      {
        backgroundColor: resolveTone(colors, action.tone),
        opacity: action.disabled ? 0.45 : hovered || pressed ? 0.85 : 1,
      },
      focused && colors.focusStyle,
    ]}>
    {renderIcon ? renderIcon(action) : <ActionGlyph action={action} palette={colors} />}
  </Pressable>;
}

export function MediaCardActionRow({ actions, palette, onAction, renderIcon }) {
  if (!actions?.items?.length) return null;
  return <View style={styles.row}>
    {actions.items.map((action, index) => <MediaCardActionButton
      key={`${action.title}-${index}`}
      action={action}
      palette={palette}
      onAction={onAction}
      renderIcon={renderIcon} />)}
  </View>;
}

const REORDER_STEPS = [
  ['first', '<<'],
  ['back', '<'],
  ['next', '>'],
  ['last', '>>'],
];

function DragHandle({ label, hint, position, count, icon, stepLabels = {}, palette, renderIcon, onReorder, onDrag, dragging }) {
  const dragRef = useRef(onDrag);
  dragRef.current = onDrag;
  const responder = useMemo(() => PanResponder.create({
    onStartShouldSetPanResponder: () => Boolean(dragRef.current),
    onMoveShouldSetPanResponder: (_, gesture) => Boolean(dragRef.current)
      && Math.abs(gesture.dx) + Math.abs(gesture.dy) > 4,
    onPanResponderGrant: () => dragRef.current?.({ phase: 'start', dx: 0, dy: 0 }),
    onPanResponderMove: (_, gesture) => dragRef.current?.({
      phase: 'move',
      dx: gesture.dx,
      dy: gesture.dy,
    }),
    onPanResponderRelease: (_, gesture) => dragRef.current?.({
      phase: 'end',
      dx: gesture.dx,
      dy: gesture.dy,
    }),
    onPanResponderTerminate: (_, gesture) => dragRef.current?.({
      phase: 'cancel',
      dx: gesture.dx,
      dy: gesture.dy,
    }),
    onPanResponderTerminationRequest: () => false,
  }), []);
  const actions = onReorder
    ? [
      { name: 'decrement', label: stepLabels.back },
      { name: 'increment', label: stepLabels.next },
    ]
    : undefined;
  return <View {...responder.panHandlers}
    accessible
    accessibilityRole="adjustable"
    accessibilityLabel={label}
    accessibilityHint={hint}
    accessibilityActions={actions}
    accessibilityValue={count > 0 && position > 0
      ? { min: 1, max: count, now: position, text: `${position} of ${count}` }
      : undefined}
    accessibilityState={{ disabled: !onDrag && !onReorder, busy: !!dragging }}
    onAccessibilityAction={event => {
      if (event.nativeEvent.actionName === 'increment') onReorder?.('next');
      if (event.nativeEvent.actionName === 'decrement') onReorder?.('back');
    }}
    style={[styles.dragHandle, dragging && styles.dragHandleActive]}>
    {renderIcon
      ? renderIcon({ kind: 'drag', title: label, icon })
      : <Text accessible={false} style={[styles.dragGlyph, { color: palette.actionText }]}>≡</Text>}
  </View>;
}

export function MediaCardActions({
  actions,
  style,
  palette,
  labels = {},
  revealed = false,
  onOpen,
  onLongPress,
  onAction,
  onReorder,
  onReorderDrag,
  dragging = false,
  renderIcon,
}) {
  if (!actions?.items?.length || !revealed) return null;
  const colors = { ...FALLBACK_PALETTE, ...(palette || {}) };
  const ordering = actions.ordering;
  return <View style={[styles.overlay, style]}>
    <Pressable accessibilityRole="button" accessibilityLabel={labels.open || actions.title}
      onPress={onOpen} onLongPress={onLongPress} style={[StyleSheet.absoluteFill, { backgroundColor: colors.overlay }]} />
    <View style={styles.overlayBody}>
      {ordering
        ? <View style={styles.ordering}>
          <View style={styles.row}>
            {REORDER_STEPS.map(([step, text]) => <Pressable key={step}
              accessibilityRole="button" accessibilityLabel={ordering.labels?.[step]}
              onPress={() => onReorder?.(step)}
              style={({ hovered, pressed, focused }) => [styles.orderButton, {
                backgroundColor: colors.reorder,
                opacity: hovered || pressed ? 0.85 : 1,
              }, focused && colors.focusStyle]}>
              <Text style={[styles.orderText, { color: colors.actionText }]}>{text}</Text>
            </Pressable>)}
          </View>
          <DragHandle label={ordering.labels?.drag} hint={ordering.labels?.dragHint || labels.dragHint}
            position={ordering.position} count={ordering.count}
            icon={ordering.dragIcon} stepLabels={ordering.labels}
            palette={colors} renderIcon={renderIcon} onReorder={onReorder}
            onDrag={onReorderDrag} dragging={dragging} />
        </View>
        : <MediaCardActionRow actions={actions} palette={colors} onAction={onAction} renderIcon={renderIcon} />}
    </View>
  </View>;
}

const styles = StyleSheet.create({
  overlay: {
    position: 'absolute',
    left: 0,
    right: 0,
    zIndex: 30,
    alignItems: 'center',
    justifyContent: 'center',
  },
  overlayBody: { maxWidth: '100%' },
  row: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'center', gap: 8 },
  roundAction: {
    width: 36,
    height: 36,
    borderRadius: 18,
    alignItems: 'center',
    justifyContent: 'center',
  },
  glyph: { fontSize: 15, fontWeight: '700' },
  ordering: { alignItems: 'center', gap: 6 },
  orderButton: {
    minWidth: 34,
    height: 32,
    paddingHorizontal: 8,
    borderRadius: 6,
    alignItems: 'center',
    justifyContent: 'center',
  },
  orderText: { fontSize: 13, fontWeight: '700' },
  dragHandle: { minHeight: 28, paddingHorizontal: 16, alignItems: 'center', justifyContent: 'center' },
  dragHandleActive: { opacity: 0.55 },
  dragGlyph: { fontSize: 18, fontWeight: '700' },
});
