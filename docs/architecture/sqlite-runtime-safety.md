# SQLite runtime safety

Floppy uses SQLite when `DB_HOST` is not set. SQLite is a local database and does not need a network service.

## WAL-reset corruption guard

SQLite documents a WAL-reset bug that can corrupt a database when all of these conditions occur:

1. the database uses WAL journal mode;
2. two or more connections are open in separate threads or processes; and
3. writes and checkpoints occur at the same time.

This can match a Floppy installation that runs web and background workers while imports write media state.

SQLite reports the bug as likely present from 3.7.0 through 3.51.2. The fix is in 3.51.3 and later. SQLite also supplies fixed backports in 3.44.6 and 3.50.7, so those two release lines are handled explicitly.

Source: [SQLite WAL documentation — WAL-reset bug](https://www.sqlite.org/wal.html#the_wal_reset_bug).

## Controlled runtimes use fixed SQLite

The compatibility fallback is not a substitute for updating a runtime that Floppy controls.

- The Docker image uses Alpine 3.24, whose current SQLite package is on a fixed 3.53 release line.
- Linux CI installs and verifies SQLite 3.53.4 before the application suite runs.
- CI verifies the SQLite version reported by Python, not only the `sqlite3` command-line tool.

SQLite 3.53.4 is the current pinned CI target. Its official source archive is verified with SQLite's published SHA3-256 before it is compiled. The CI installer lives at `scripts/ci/install-fixed-sqlite.sh`.

The runtime fallback remains required for source installs and future packaged distributions because Floppy cannot safely replace the operating system SQLite library on an operator-owned machine.

## Floppy behavior

Floppy checks the SQLite library used by Python before Django or Celery opens the database.

- If `SQLITE_JOURNAL_MODE` is `WAL` and the SQLite runtime has the fix, Floppy keeps WAL mode.
- If `SQLITE_JOURNAL_MODE` is `WAL` and the runtime is affected, Floppy uses `DELETE` journal mode and writes a startup warning.
- The affected-runtime fallback raises `SQLITE_SYNCHRONOUS=NORMAL` to `FULL` so the rollback journal does not replace the WAL defect with an avoidable power-loss corruption path.
- If an operator selected another supported crash-recoverable mode, Floppy keeps that mode.
- PostgreSQL installations are not changed.

The guard uses the linked Python SQLite runtime. It does not assume a Docker image, Linux distribution, package manager, or network connection.

To see the SQLite library used by Python:

```bash
python -c "import sqlite3; print(sqlite3.sqlite_version)"
```

## Test-runtime behavior

Django normally creates a shared in-memory SQLite database for tests. SQLite reports `journal_mode=MEMORY` for a true in-memory database even when a persistent database would use WAL or DELETE.

The normal test settings therefore do not run Floppy's persistent-file journal hook against Django's in-memory test database. The SQLite safety unit tests separately prove that MEMORY is accepted only for true in-memory database names and remains a mismatch for persistent file paths.

Ordinary tests are also offline by default. A test that reaches a public HTTP provider fails immediately and must mock the provider boundary or carry the `network` tag. `scripts/test.sh --network` and `scripts/test.sh --full` explicitly opt into external HTTP.

## Performance

WAL normally gives better write concurrency, so Floppy keeps it on fixed SQLite releases. The rollback journal with `synchronous=FULL` is a compatibility fallback for affected releases. A write-heavy installation can have more lock contention while the fallback is active.

Upgrade the SQLite library used by Python to a fixed release to restore WAL automatically. Do not force WAL on an affected runtime to recover performance; that restores the corruption condition.

CI should not benchmark normal SQLite behavior on the rollback fallback. Controlled CI uses a fixed SQLite so concurrency and lock behavior are closer to a supported production runtime.

## A read page does not wait for the writer

SQLite has one writer, and Floppy takes the write lock when a transaction starts (`transaction_mode=IMMEDIATE`). A GET that issues any write, or opens `transaction.atomic()` even to read, queues behind an import or background job for up to the busy timeout. So a page load issues no write. `app.tests.test_read_pages_do_not_write` requests the read pages and API endpoints and fails on a write that is not in its allowlist; each allowlist entry carries its reason. Where a page has just computed something worth keeping (a Discover row or taste profile), it wraps the save in `app.db_retry.optional_save()`: on a GET or HEAD request the save waits 50 ms at most, once per request, and is otherwise skipped and left to the next refresh task. Tasks, commands and every non-GET request keep the full wait. `app.tests.test_page_loads_with_writer_busy` holds the lock and checks the page still answers. Both tests run in WAL; on the rollback fallback a committing writer can also block readers.

Music pages follow the same rule and also make no MusicBrainz call. An artist, album or track page draws stored rows and reads MusicBrainz answers from the cache only (`musicbrainz.cached_*`). What used to run inline (merging duplicate albums, healing a placeholder album, a discography or member sync, fetching tracks, credits, genres or artist metadata) is asked for by the read-only checks in `app.music_page_repair` and done by one task per artist, album or track on the default queue (`repair_music_*_page`), queued at most once per cool-down. When something the reader can see is missing (no tracks, no biography), the page shows a poller that reloads it once the task finishes, and a merged album or artist redirects to the survivor; the Expo app gets the same through `pending` and `repair_pending` in the music API. `app.tests.test_read_pages_do_not_write` covers the healthy, no-tracks and duplicate-album states and fails on a write or a provider call.

## Configuration validation

Floppy accepts these crash-recoverable SQLite journal modes:

- `DELETE`
- `TRUNCATE`
- `PERSIST`
- `WAL`

Floppy accepts these `SQLITE_SYNCHRONOUS` values:

- `NORMAL` or `1`
- `FULL` or `2`
- `EXTRA` or `3`

`journal_mode=OFF`, `journal_mode=MEMORY`, and `synchronous=OFF` are not accepted for persistent Floppy databases because SQLite documents corruption risks after process, operating-system, or power failures. Unexpected values are also rejected before they can become SQLite PRAGMA grammar.

Sources: [SQLite PRAGMA documentation](https://www.sqlite.org/pragma.html#pragma_journal_mode) and [How To Corrupt An SQLite Database File](https://www.sqlite.org/howtocorrupt.html#cfgerr).

## Recovery scope

This guard prevents the known WAL-reset condition. It does not replace the startup integrity check, verified backups, or SQLite recovery tools. Those controls remain separate because an existing database can already contain damage from an older run, storage failure, or another cause.

No schema migration is required for this runtime policy.

## Planner statistics

A database that has never been `ANALYZE`d has no `sqlite_stat1`, and SQLite then chooses indexes by heuristic: a lookup such as "this user's season for this show" walks every season the user tracks by `user_id` and probes each item, instead of seeking by item. On a 2 GB database on a spinning array disk one such query took 3.65 s for 1,126 rows (ledger: `USER-REPORTED/episode-page`).

The `Optimize database planner statistics` Celery task runs `PRAGMA analysis_limit=1000; PRAGMA optimize=0x10002` once a day, and on the first beat start. It analyses only tables without statistics or that changed substantially, on a row sample, so steady-state runs are nearly free. SQLite only; disable with `DB_OPTIMIZE_ENABLED=false`, tune the sample with `DB_OPTIMIZE_ANALYSIS_LIMIT`. Its log line is `optimize_database_statistics stat_rows_before=… stat_rows_after=… duration_ms=…`.

`app.tests.test_tasks_db_optimize` asserts the plan moves from `app_season_user_id` to `app_item_media_lookup_idx`.
