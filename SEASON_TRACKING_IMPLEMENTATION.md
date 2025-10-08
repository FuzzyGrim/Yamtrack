# Season vs Show Logging and Tracking Implementation

## Overview

This document explains the comprehensive implementation of seamless season vs show logging and tracking in Yamtrack. The system ensures users are never confused about what actions affect which levels (TV show, season, or episode).

## Key Principles

1. **Clear Hierarchy**: TV Show → Seasons → Episodes
2. **Explicit Actions**: Every button clearly states what it affects
3. **Visual Feedback**: Status indicators show current state at all levels
4. **Contextual Help**: Tooltips and explanations guide users
5. **Bidirectional Propagation**: Status changes flow both up and down the hierarchy

## Components Implemented

### 1. Enhanced Action Buttons (`media_actions.html`)

#### TV Show Page Actions

- **"Watched" button**: Tooltip explains it marks ALL seasons and episodes
- **"Log Show" button**: Opens diary modal for entire show
- **"Start Watching" button**: Clearly states it starts from Season 1
- **"Watching" indicator**: Shows when any season is in progress with helper text
- **"Unwatch Entire Show" button**: Explicit label warns about removing all data

#### Season Page Actions

- **"Watched" button**: Marks only this season and its episodes
- **"Log Season" button**: Opens diary modal for only this season
- **Helper text**: "Only this season will be affected - other seasons remain unchanged"
- **Tooltips**: Each button has explanatory tooltip on hover

### 2. TV Season Progress Component (`tv_season_progress.html`)

Shows on TV show detail pages when user is tracking the show:

- **Visual Status Icons**:

  - ✓ Green checkmark for completed seasons
  - ▶ Blue play icon for in-progress seasons
  - ✗ Red X for dropped seasons
  - ○ Gray circle for unstarted seasons

- **Progress Information**:

  - Episode count for each season
  - Status badges (Completed, In Progress, Dropped)
  - Clickable season links

- **Helper Text**: "Track progress in individual seasons below"

### 3. Season Parent Show Context (`season_parent_show.html`)

Shows on season detail pages:

- **Parent Show Card**:

  - Thumbnail and link to main show page
  - Current season position (e.g., "Season 2 of 5")
  - Parent show tracking status

- **Status Indicators**:

  - Show's overall status (Completed, Watching, Dropped)
  - Total episodes watched across all seasons

- **Important Notice**:
  - "Actions on this season only affect this season. To manage the entire show, visit the main show page."

### 4. Toast Notification System (`toast-notifications.js`)

Provides instant feedback when actions affect multiple levels:

- **Success Notifications** (green):

  - "Season 2 marked as watched (10 episodes)"
  - "Entire show marked as watched (all 5 seasons and episodes)"
  - "Started watching - Season 1 is now in progress"

- **Info Notifications** (blue):

  - "Season 2 unmarked - all episodes removed"
  - "Show unmarked - all 5 seasons and episodes removed"

- **Features**:
  - Auto-dismiss after 4 seconds
  - Manual dismiss button
  - Slide-in animation from right
  - Stacks multiple notifications
  - Integrates with HTMX via custom headers

### 5. Enhanced Log Modals (`tv_log_modal.html`)

#### Modal Header Explanations

- **TV Show Modal**: "This will log the entire show to your diary. You can also log individual seasons separately."
- **Season Modal**: "This will log only Season X to your diary. Other seasons won't be affected."

#### Action Button Clarifications

- **"Log Show" vs "Just Mark Watched"**:
  - Helper text: "'Log Show' adds to your diary with review. 'Just Mark Watched' updates tracking status only."
- **Season Buttons**:
  - Helper text: "Only affects Season X. Other seasons remain unchanged."

## Backend Notifications (views.py)

Enhanced views to send notification headers:

### `mark_tv_watched`

```python
response["X-Notification-Message"] = f"Entire show marked as watched (all {season_count} seasons and episodes)"
response["X-Notification-Type"] = "success"
```

### `unmark_tv_watched`

```python
response["X-Notification-Message"] = f"Show unmarked - all {season_count} seasons and episodes removed"
response["X-Notification-Type"] = "info"
```

### `start_tracking_tv`

```python
response["X-Notification-Message"] = "Started watching - Season 1 is now in progress"
response["X-Notification-Type"] = "success"
```

### `mark_season_watched`

```python
response["X-Notification-Message"] = f"Season {season_number} marked as watched ({episode_count} episodes)"
response["X-Notification-Type"] = "success"
```

### `unmark_season_watched`

```python
response["X-Notification-Message"] = f"Season {season_number} unmarked - all episodes removed"
response["X-Notification-Type"] = "info"
```

## User Flow Examples

### Flow 1: Starting a New Show

1. User clicks "Start Watching" on TV show page
2. Toast notification: "Started watching - Season 1 is now in progress"
3. Action button changes to "Watching" with helper text
4. Season progress panel appears showing Season 1 in progress
5. User can click Season 1 to see episodes and track progress

### Flow 2: Completing Individual Seasons

1. User visits season page (e.g., Season 2)
2. Sees parent show context card showing they're watching the show
3. Clicks "Watched" button on season page
4. Toast notification: "Season 2 marked as watched (10 episodes)"
5. Helper text reminds: "Only this season will be affected"
6. Season 2 shows as completed in parent show's season progress panel
7. Parent show remains "In Progress" status

### Flow 3: Completing Entire Show at Once

1. User clicks "Watched" on TV show page
2. Tooltip on hover explains: "Mark all seasons and episodes as watched"
3. After clicking, toast: "Entire show marked as watched (all 5 seasons and episodes)"
4. All seasons and episodes are created and marked complete
5. TV show status changes to "Completed"

### Flow 4: Logging vs Tracking

1. User opens "Log Show" modal
2. Header explains: "This will log the entire show to your diary"
3. Two buttons presented:
   - "Log Show": Creates diary entry with date, rating, review
   - "Just Mark Watched": Only updates tracking status
4. Helper text clarifies the difference
5. User makes informed choice

### Flow 5: Season Independence

1. User watching Season 3 decides to drop it
2. Views season page - sees parent show card
3. Marks Season 3 as dropped
4. Parent show status updates to "Dropped" (per propagation rules)
5. Seasons 1-2 remain "Completed"
6. Helper text confirms: "Only this season will be affected"

## Status Propagation Rules

### Season → TV Show (Upward)

- Season marked "In Progress" → TV show becomes "In Progress"
- Season marked "Dropped" → TV show becomes "Dropped"
- Last season completed → TV show becomes "Completed"

### TV Show → Season (Downward)

- TV show marked "Completed" → All seasons become "Completed"
- TV show marked "Dropped" → All in-progress seasons become "Dropped"
- TV show marked "In Progress" → Start next available season

## Visual Design Principles

### Color Coding

- **Green**: Completed status (success)
- **Blue**: In Progress status (active)
- **Red**: Dropped status (stopped)
- **Gray**: Not started (neutral)

### Typography

- **Bold labels**: Action buttons and status indicators
- **Small gray text**: Helper text and explanations
- **Medium text**: Main content

### Layout

- **Sticky poster cards**: Always visible for quick actions
- **Inline helper text**: Contextual guidance where needed
- **Toast notifications**: Non-intrusive feedback
- **Status badges**: Quick visual scan of state

## Testing Scenarios

### Critical Paths to Test

1. [ ] Start watching a new show → Check Season 1 created as "In Progress"
2. [ ] Complete a season → Verify only that season marked complete
3. [ ] Complete entire show → Verify all seasons and episodes created
4. [ ] Unwatch a season → Verify episodes removed, other seasons intact
5. [ ] Unwatch entire show → Verify all data removed
6. [ ] Log season vs log show → Verify diary entries separate
7. [ ] Drop a season → Verify show status updates correctly
8. [ ] Complete last season → Verify show becomes complete

### UI/UX to Verify

1. [ ] All buttons have clear labels
2. [ ] Tooltips appear on hover
3. [ ] Helper text visible below relevant actions
4. [ ] Toast notifications appear for all actions
5. [ ] Season progress panel shows on TV pages
6. [ ] Parent show card shows on season pages
7. [ ] Log modals have explanatory headers
8. [ ] Status icons display correctly

## Files Modified

### Templates

- `src/templates/app/components/media_actions.html` - Enhanced action buttons
- `src/templates/app/components/media/poster_card.html` - Integrated new components
- `src/templates/app/components/media/tv_season_progress.html` - NEW: Progress tracking
- `src/templates/app/components/media/season_parent_show.html` - NEW: Parent context
- `src/templates/app/components/tv_log_modal.html` - Added explanatory text
- `src/templates/base.html` - Added toast notification script

### JavaScript

- `src/static/js/toast-notifications.js` - NEW: Toast notification system

### Backend

- `src/app/views.py` - Added notification headers to all TV/season actions

## Future Enhancements

### Potential Improvements

1. **Episode-level tracking**: Individual episode checkboxes in season view
2. **Batch operations**: "Mark seasons 1-3 as watched"
3. **Smart suggestions**: "Continue watching Season 4?" when Season 3 completed
4. **Progress statistics**: "You've watched 45% of this show"
5. **Undo functionality**: Quick undo button in toast notifications
6. **Keyboard shortcuts**: Quick actions without clicking

### Analytics to Consider

1. Track which actions users find confusing (via support tickets)
2. Measure completion rates for multi-season shows
3. Monitor season vs show logging preferences
4. Identify common user flows and optimize them

## Conclusion

This implementation creates a clear, intuitive system for managing TV show tracking at multiple levels. Users receive constant feedback about what their actions will affect, preventing confusion and mistakes. The visual indicators and helper text ensure that users always understand the current state and available actions at any given moment.
