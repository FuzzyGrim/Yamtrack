// How much of History renders before the reader scrolls. Pure logic shared by
// the web adapter and the Expo screen; the server mirrors the same rule in
// src/app/history_views.py (HISTORY_FIRST_VIEW_ENTRIES, _first_view_day_count).

// About two screens of cards: a desktop grid shows ~16 cards a screen, a
// phone's three-column grid ~10. Whole days only, so a day never splits.
export const HISTORY_FIRST_VIEW_ENTRIES = 36;

// The first day always renders. Each later day renders while it still fits in
// the budget; every day after the first that does not fit waits.
export function firstViewDayCount(entryCounts, budget = HISTORY_FIRST_VIEW_ENTRIES) {
  const counts = entryCounts || [];
  if (!counts.length) return 0;
  let total = Math.max(Number(counts[0]) || 0, 0);
  let days = 1;
  while (days < counts.length) {
    const next = Math.max(Number(counts[days]) || 0, 0);
    if (total + next > budget) break;
    total += next;
    days += 1;
  }
  return days;
}
