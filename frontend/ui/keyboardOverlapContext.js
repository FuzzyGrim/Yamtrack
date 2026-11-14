import { createContext } from 'react';

// A platform adapter supplies the hook that reports how far the keyboard covers the bottom of the
// window (the native keyboard, or the web visual viewport), the same way GlobalSearch takes it as a
// prop. Without an adapter nothing is covered. Shared menus read it from here instead of each taking a prop.
export const KeyboardOverlapContext = createContext(() => 0);

// The part of the window the platform keeps for itself (the safe area: the iOS side rail, the notch, the home
// indicator). A menu stays out of it instead of opening under the rail. Web has none, so it defaults to zero.
export const DropdownInsetsContext = createContext({ top: 0, right: 0, bottom: 0, left: 0 });
