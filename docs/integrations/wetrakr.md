# WeTrakr outbound sync

Floppy can send what you watch, what you plan or drop, and your ratings to
[WeTrakr](https://api.wetrakr.com/). It is one provider on the shared outbound
engine described in [watched-state-sync](../architecture/watched-state-sync.md);
this page covers only what is specific to WeTrakr.

The runnable half is `src/integrations/tests/test_wetrakr.py`. If this page and
that file disagree, the file is right.

```bash
SECRET=test-only scripts/test.sh integrations.tests.test_wetrakr
```

## Verification status

**Not yet verified against a live WeTrakr account.** Every request and reply
shape in `wetrakr_api.py` and `state/adapters/wetrakr.py` comes from an
unofficial transcription of WeTrakr's documentation (2026-10-01), and the tests
run against a mocked WeTrakr. Do not call this integration complete until a
consenting test account has confirmed, using the bundled key:

- the device-login field name (`code` or `device_code`; Floppy tries both);
- the exact reply buckets (`added`, `notFound`, `errored`) and `play_id`;
- the rate-limit and quota headers;
- that an unrecognised reply is retried rather than lost (it is).

## Connecting

1. Nothing to set up: Floppy ships WeTrakr's app-owned key (a shared default,
   like the TMDB key), sent as the `wetrakr-api-key` header. It is public by
   design and every install shares its quota. WeTrakr issues the key to the app,
   so members cannot get their own and Settings offers no key field. A rebrand or
   public host with its own key sets `WETRAKR_CLIENT_ID` in the environment.

   Running Floppy yourself, including with small changes of your own, needs
   nothing more. If you rebrand Floppy, make a major departure from it, or host
   it as a public service for other people, ask WeTrakr for your own key so your
   users are counted separately from Floppy's.
2. Providers > WeTrakr > Connection > Connect WeTrakr. Floppy shows a code; approve it on
   wetrakr.com. Floppy stores the tokens encrypted and refreshes them under a
   lock, because WeTrakr rotates refresh tokens.
3. Connecting sends nothing. Plays, statuses and ratings each have a switch,
   all off.

## What is sent

| Switch | Sent | Never sent |
|---|---|---|
| Plays | A movie or episode you finish, with the date you watched it | Imported history, undated plays, a whole show as watched |
| Remove plays | When you delete a play in Floppy, that exact play (off by default; see below) | The latest play, all plays, anything you removed through an import |
| Statuses | Planning and dropped for movies; planning, paused and dropped for shows | Watching and completed (a play already says so) |
| Ratings | Movie and show ratings, including clearing one | Episode ratings |

Deliveries are Live (within seconds), Hourly or Daily. Hourly and Daily use UTC
boundaries.

## Send existing library

Plays are WeTrakr's activity feed, so imported history never reaches it by
itself. **Send existing library** queues your dated plays, plans, drops and
ratings once, only when you press it. Re-running it never sends the same dated
play twice. Plays without a date are counted on the card and left out, because
WeTrakr would show them as watched today.

## Removing a play

With **Remove a play from WeTrakr when I delete it here** switched on, deleting a
play in Floppy removes that one play from WeTrakr. Floppy keeps the `play_id`
WeTrakr returned for each play it sent and deletes by that id. If WeTrakr did
not return one, it removes by the exact date the play was sent with. A play
WeTrakr no longer has counts as removed. A rewatch that is still in Floppy is
never touched, because each play is matched by its own date. A play that was
queued but not yet sent is simply not sent. Floppy never calls WeTrakr's
remove-all or remove-latest routes.

## Known limits

- Removal only follows a play you delete. Editing a play's date does not change
  what WeTrakr holds.
- A play that is being sent at the very moment you delete it may stay on WeTrakr.
- Un-watching something Floppy never sent leaves WeTrakr alone.
