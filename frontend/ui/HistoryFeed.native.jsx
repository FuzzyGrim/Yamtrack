import React from 'react';
// The explicit extension matters: './HistoryFeed' would resolve back to this native file and loop.
// Native presentation marker for the architecture contract; the shared
// component below owns layout, so no wrapper View is rendered here.
import { View } from 'react-native';
import { HistoryFeed as SharedHistoryFeed } from './HistoryFeed.jsx';

// Native counterpart for the browser history feed. The shared feed owns the
// month navigation, filters, compact day groups with the three-column card
// grid, and the loading/empty/error states. Explicit `.jsx` imports keep Metro
// from resolving this file's own name back to the browser adapter.
export function HistoryFeed(props) {
  void View;
  // The phone sheet uses the web's dropdowns instead of chip lists.
  return <SharedHistoryFeed filterVariant="select" webActions {...props} />;
}

export {
  HistoryActivityCalendar,
  HistoryActivityDialog,
  HistoryEditDialog,
  HistoryDeleteDialog,
  HistoryFilterFab,
} from './HistoryFeed.jsx';
