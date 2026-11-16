# Season and show status rules

How a season's and a show's status is decided, and why. Written for issue #1618 so
the reasoning survives. If this page and the code disagree, fix whichever is wrong
and update both. The scenario table below was agreed with Daniel on 2026-10-28.

## The rules

| Rule | Statement | Where |
|---|---|---|
| R1 | **Your status wins.** Dropped and Paused are never changed by syncs, imports, the calendar or a finished season. Only a real new play reopens them. | `TV._handle_completed_season`, `Season._sync_status_after_episode_change`, `Episode.save`, webhooks |
| R2 | **A season looks only at itself.** Completed means every episode of that season has been watched. Repeat plays count once. Other seasons never reopen it. | `Season.derived_status_from_episode_progress` |
| R3 | **Unaired is not pending work.** A season or episode that has not aired never blocks an earlier season and never auto-starts. A Planning season starts only when its first episode has aired. | `TV._start_next_available_season`, `TV.has_unfinished_aired_seasons` |
| R4 | **Only "ended" finishes a show.** Completed needs the provider to say the series has ended (or a manual show), and every aired season Completed. A provider that is unreachable, silent or unrecognised leaves the status alone. | `TV._handle_completed_season_inner` |
| R5 | **Otherwise a played show is In Progress, and the label says Caught Up.** In Progress on a show means "I am following this". It stays on the Home shelf. When every aired episode is watched and more is still to come, the details page progress card reads Caught Up. This is display only; the stored status does not change. Library cards get no extra chip. | `ProgressBreakdown.caught_up`, `detail_progress_card_slot.html` |
| R6 | **Announcements and plays reopen.** A new season announced for a Completed show reopens it to In Progress with a Planning season. A real play reopens Planning, Paused or Dropped entries. | `events/calendar/tv.py`, `integrations/webhooks/base.py` |

**What counts as watching an episode.** A play is a watch only when it is finished:
status Completed and not dropped. An open play (a check-in, a paused scrobble) is
history but not yet a watch, and a dropped row never is. Every play count, watched
flag, statistic and sync push reads this one rule through `Episode.watch_q()` (queries)
or `Episode.is_watch()` (loaded rows), so the checkmark, the season status and what
Jellyfin, MAL and WeTrakr receive cannot disagree.

The label sits on the details progress card, which computes it from the provider's
episode list (`ProgressBreakdown.caught_up`) so it can tell "nothing left to air"
(Completed) from "more is coming". Library cards keep their existing status chip
only: adding a text chip per card was rejected to keep cards uncluttered. The
library's Caught Up filter (`helpers.is_caught_up_media`) is unchanged.

## Scenarios

### Seasons

| # | Scenario | Result | Source |
|---|---|---|---|
| S1 | Season finished airing, 10 of 10 watched through Plex webhooks | Completed. Covered by `test_ten_plays_complete_a_season_of_a_returning_show`. The #1618 report could not be reproduced (no version or logs). | #1618 |
| S2 | Season still airing, every aired episode watched | In Progress, Caught Up on the details page | #1036 |
| S3 | Same episode played several times | Completed (distinct episodes counted) | #929 |
| S4 | Bulk-add every episode in Episode Plays | Completed | #1265, #1289 |
| S5 | User reopens a Completed season on purpose | In Progress (rewatch pass) | #925 |
| S6 | Season is Paused or Dropped, then a new play arrives | Resumes. A duplicate scrobble does not. | #375, #1266 |
| S7 | Next season announced but unaired | Earlier season untouched | #1618 |

### Shows

| # | Scenario | Result | Source |
|---|---|---|---|
| H1 | Returning series, S1 Completed, S2 announced and unaired | In Progress, Caught Up on the details page | #1265, #1036, #1618 |
| H2 | Ended series, every season Completed | Completed | #1265 |
| H3 | Ended series, all aired seasons Completed, leftover season dated in the future | Completed (changed by #1618). A Planning season with no date, or one already aired, still blocks. | #1618 |
| H4 | Show Completed, then a new season is announced | Reopens to In Progress with a Planning season | #1266 |
| H5 | Planning season whose first episode has aired | Starts automatically | |
| H6 | Show never played, season announced | Planning | |
| H7 | Show Dropped or Paused, a season finishes or syncs run | Unchanged | #375, #1133, #361 |
| H8 | Provider unreachable or reports an unknown production status | Unchanged | #375 |
| H9 | Manual show, all added seasons watched | Completed | #1039 |
| H10 | Home "In progress" shelf for a caught-up show | Stays on the shelf | #1036 |

## Decisions and why

- **Stored status stays In Progress for a caught-up returning show.** #1265 asked for
  it (a returning series must not read Completed), #1036 and #375 exist because shows
  vanished from Home when they were treated as finished. #1618 read the same state as
  misleading ("In Progress implies something to watch"). A Caught Up label answers
  that without reversing the other three. Storing Completed was considered and
  rejected because the new season would then have to reopen it again.
- **Unaired seasons are judged by the provider's date, not by status alone.** A
  Planning season with no date may be one the user skipped, so it keeps blocking
  completion. Only a season the provider explicitly dates in the future is ignored.
- **The label is display only, and only on the details page.** No migration, no new status value, no API change, no new chip on library cards.

## Related history

#929 (duplicate plays kept seasons open), #925 (rewatch passes), #375 (shows
"forgotten"), #1036 and #1266 (user status wins, caught-up shelf), #1289 (production
status decides completion), #1265 (returning series stays open), #1618 (this page).
