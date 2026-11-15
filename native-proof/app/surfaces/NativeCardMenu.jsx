import React, { useState } from 'react';
import { ScrollView, View } from 'react-native';
import { DropdownBackItem, DropdownItem, DropdownMenu } from '../../../frontend/ui/DropdownMenu';

// Web has no system context menu: use the shared dropdown with the same actions.
export default function NativeCardMenu({ actions, onPressAction, palette, children }) {
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState(null);
  const [submenu, setSubmenu] = useState(null);
  function select(action) {
    if (action.subactions) { setSubmenu(action); return; }
    setOpen(false);
    onPressAction?.({ nativeEvent: { event: action.id } });
  }
  return <View>
    {React.cloneElement(children, { onLongPress: nextAnchor => { setAnchor(nextAnchor); setSubmenu(null); setOpen(true); } })}
    <DropdownMenu open={open} onClose={() => setOpen(false)} anchor={anchor} align="start"
      estimatedHeight={360} closeLabel="Close menu" palette={palette}>
      <ScrollView>
        {submenu ? <DropdownBackItem label={submenu.title} palette={palette} onPress={() => setSubmenu(null)} /> : null}
        {(submenu?.subactions || actions).map(action => <DropdownItem key={action.id} label={action.title}
          palette={palette} chevron={Boolean(action.subactions)} selected={action.state === 'on'}
          disabled={action.attributes?.disabled} onPress={() => select(action)} />)}
      </ScrollView>
    </DropdownMenu>
  </View>;
}
