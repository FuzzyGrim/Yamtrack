import React from 'react';
import { Button, ContextMenu, Group, Host, Picker, RNHostView, Text } from '@expo/ui/swift-ui';
import { contentShape, disabled, pickerStyle, shapes, tag } from '@expo/ui/swift-ui/modifiers';

// A menu-style Picker expresses one selected status as a native hierarchical
// choice inside the iOS context menu, rather than opening a second Menu view.
export default function NativeCardMenu({ actions, onPressAction, children }) {
  const fire = event => onPressAction?.({ nativeEvent: { event } });
  return <Host ignoreSafeArea="all">
    <ContextMenu>
      <ContextMenu.Trigger>
        {/* iOS lifts a context-menu preview; Android and web menus do not lift the card. */}
        <Group modifiers={[contentShape(shapes.roundedRectangle({ cornerRadius: 8 }), 'contextMenuPreview')]}>
          <RNHostView matchContents boundedByHost>
            {React.cloneElement(children, { onLongPress: () => {} })}
          </RNHostView>
        </Group>
      </ContextMenu.Trigger>
      <ContextMenu.Items>
        {actions.map(action => action.subactions ? <Picker key={action.id} label={action.title}
          selection={action.subactions.find(option => option.state === 'on')?.id || null}
          onSelectionChange={fire} modifiers={[pickerStyle('menu')]}>
          {action.subactions.map(option => <Text key={option.id} modifiers={[tag(option.id)]}>{option.title}</Text>)}
        </Picker> : <Button key={action.id} label={action.title}
          modifiers={action.attributes?.disabled ? [disabled(true)] : []} onPress={() => fire(action.id)} />)}
      </ContextMenu.Items>
    </ContextMenu>
  </Host>;
}
