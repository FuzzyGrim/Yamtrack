import React from 'react';
import { Text, View } from 'react-native';
import { SheetCloseButton } from './SheetCloseButton';
import { SheetContainer, SheetHost, useNativeSheetClose } from './SheetHost';
import { useSheetInsets } from './useSheetInsets';
import { RailModalHost, RailModalProvider } from '../../../frontend/ui/Modal';

// Android's native sheet offers half and full states rather than arbitrary detents.
export function AdaptivePresentationSheet({ children, title, onClose, titleColor = '#fff', snapPoints = ['half', 'full'], ...props }) {
  const insets = useSheetInsets();
  const railEdge = useNativeSheetClose(insets);
  const nativeClose = Boolean(railEdge && title && onClose);
  return <SheetContainer snapPoints={snapPoints} showDragIndicator contentPadding={0} {...props}>
    <SheetHost nativeClose={nativeClose} onClose={onClose}><RailModalProvider enabled><View style={{ flex: 1 }}>
      {title ? <View style={{ height: 80, paddingLeft: 16 + (nativeClose ? 0 : insets.left), paddingRight: 16, justifyContent: 'center', alignItems: 'flex-start' }}>
        <Text accessibilityRole="header" numberOfLines={1} style={{ color: titleColor, fontSize: 20, fontWeight: '600', marginRight: 52 }}>{title}</Text>
        {onClose && !nativeClose ? <View style={{ position: 'absolute', right: 16 }}><SheetCloseButton onPress={onClose} color={titleColor} /></View> : null}
      </View> : null}
      <View style={{ flex: 1, paddingLeft: nativeClose ? 0 : insets.left, paddingRight: nativeClose ? 0 : insets.right,
        // The native rail reserves its full width; the sheet body previously extended 16pt into it.
        marginLeft: nativeClose && railEdge === 'left' ? -16 : 0,
        marginRight: nativeClose && railEdge === 'right' ? -16 : 0 }}>{children}</View>
      {/* Nested selectors must appear above the sheet, not behind its native controller. */}
      <RailModalHost />
    </View></RailModalProvider></SheetHost>
  </SheetContainer>;
}
