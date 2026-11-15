import React from 'react';
import { BottomSheet, RNHostView } from '@expo/ui';

export function SheetContainer(props) { return <BottomSheet {...props} />; }
export function useNativeSheetClose() { return false; }
export function SheetHost({ nativeClose, onClose, ...props }) { return <RNHostView {...props} />; }
