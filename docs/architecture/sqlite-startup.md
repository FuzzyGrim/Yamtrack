# SQLite availability and verification

Ordinary restarts do not warm the database, run `quick_check`, run
`foreign_key_check`, or invoke no-op migration handlers. Admission reads the
schema, migration history, model table/column names and recovery sidecars.
Each SQLite statement has a five-second progress deadline and a two-second busy
timeout; the entrypoint bounds the whole command, including imports and I/O,
to `FLOPPY_SQLITE_ADMISSION_TIMEOUT` seconds (default 30, then five seconds to
kill it). This setting must be a positive whole number. GNU `timeout` returns
124 when it reaches the deadline; BusyBox may return 143 after sending TERM.
The entrypoint treats both as timeouts, retries admission once, then parks if
the retry also times out. A kernel storage operation in uninterruptible sleep
cannot be made healthy by a Python or shell deadline.

Because the deadline includes imports, the image ships compiled bytecode for
the standard library, dependencies and app (`compileall` in the Dockerfile,
guarded by `config.tests.test_dockerfile_bytecode`). Without it every Python
start recompiled its imports from source: from a cold disk cache, admission
took 7 to 11 s and the container 27 to 32 s to answer, against 4 to 5 s and
12 to 14 s with bytecode (Docker Desktop, 4 CPUs, 20k-item library).

An expired verification, absent record, interrupted scan or previous timeout
only lets the sliced background verifier continue; a changed image does not. These are not corruption findings. An old record is not proof that
the current database is entirely healthy; admission checks only accessibility
and compatibility. Latent damage outside the pages read can be found later.

Availability still blocks for:

- unreadable/malformed database or incompatible migration history/schema;
- unresolved, accepted legacy, prepared, corrupt or unreadable recovery reports;
- a previous explicitly failed/blocked startup check;
- a database replacement that would inherit another file's verification;
- pending migrations without an unchanged verified recovery snapshot and
  matching migration plan/image, or with a missing/changed backup;
- admission exceeding its deadline or storage/lock errors.

The parked container serves a diagnosis/recovery page. Admission never repairs
rows. An incident-scoped operator decision dispatches bounded offline recovery
before admission, while services remain stopped; stale approvals do not trigger
a new scan after the incident closes. PostgreSQL retains its existing
migration/startup path and does not run SQLite verification.

## Pending migrations: automatic snapshot at startup

When migrations are pending and no valid permit exists, admission exits 11 and
the entrypoint (services not yet started) runs `sqlite_startup
--prepare-migrations --fast`: an online-API copy of the database, verified with
the **page sweep** (`sqlite_page_sweep.py`, sequential) on the copy, then a
permit, then re-admission and migrate. `PRAGMA quick_check` is not run on the
copy: it walks b-trees in key order, which took 1.9 h on slow storage. The
sweep proves the copy is a readable, structurally sound restore point (header,
every b-tree page's layout, child pointers, overflow chains, freelist). If the
sweep cannot run (no `sqlite_dbpage` and a non-empty WAL) the copy falls back to
`quick_check`. The permit records what ran (`snapshot_verified`: `page_sweep` or
`quick_check`; `quick_check_deferred`). The relationship (`foreign_key_check`)
scan is skipped too.
**Tradeoff:** record format and key order of the restore point, and
pre-existing foreign-key conflicts, are verified only later, after the
migration, by the background verifier. After a sweep-gated copy, preparation
asks the verifier (`request_deferred_quick_check`) to re-run every table's
`quick_check` in its next slices regardless of the 30-day cadence (progress file
key `deferred_quick_check` with `pending_tables`, until all tables are done). A
finding is a `corrupt` incident, as for any verifier finding. That deep check is
limited to 150 s per table per slice (below), so on slow storage a large table
may stay in `deep_incomplete` with the request pending: a coverage limit, not a
pass. The copy is still the restore point, but it may hold damage the sweep
cannot see. Incidents and previous failed/blocked checks still block. Set
`FLOPPY_SQLITE_AUTO_SNAPSHOT=false` to require the manual step below. Measured
here: 18.6s for a 2GB database on local SSD.

## Pending migrations: explicit offline preparation

Stop **all** application containers, workers, scheduled jobs and other database
writers. Use the intended new image with the same database, secret and backup
mounts, overriding its entrypoint:

```sh
docker compose run --rm --no-deps --entrypoint python floppy \
  manage.py sqlite_startup --prepare-migrations
docker compose up -d floppy
```

For Unraid/Portainer, run the equivalent one-off container using that image and
the existing mounts/environment, with `python manage.py sqlite_startup
--prepare-migrations` as its command. Do not start a second normal application
container against the database during preparation.

Preparation makes a durable recovery snapshot under `BACKUP_DIR/verification`,
checks its structure **and** foreign keys, and records a private migration
permit beside the live database. Main database, WAL and rollback-journal file
identity/size/mtime bracket preparation. Startup rechecks those signatures,
the snapshot signature, migration plan and image SHA before permitting DDL.
Absent and zero-byte WAL files are equivalent because neither contains frames;
nonempty WAL files retain their full fingerprint. SQLite may create an empty WAL
while opening a read-only backup. No WAL/SHM files are manually removed.
Changed data fingerprints, restored/replaced files, changed backups or changed plans
requires preparation again. Online verification cannot issue this permit.
Preparation invalidates any previous permit before beginning.

Maintenance has a one-hour process deadline by default. `--timeout SECONDS`
allows an explicitly chosen longer maintenance window. On failure/timeout,
inspect the phase/logs and storage; no new migration permit is granted. This
workflow can cost a full copy plus two scans and needs space for the snapshot
and SQLite temporary files. It deliberately occurs in a scheduled maintenance
window, rather than on every restart. Regular quick-check-only backups and
old live verification records cannot authorize migrations.

SQLite migrations use the progress watchdog: 180 seconds with neither read
nor CPU progress stops the command; an absolute four-hour ceiling remains.
They do not retry automatically after failure. The recovery snapshot remains
available for inspection/restoration. Migration `events.0017` creates its
datetime index transactionally, without Django's unrelated global FK exit
scan; interrupted index creation and its migration record roll back together.
The same skip now applies to every index-only migration:
`config.sqlite_backend`'s schema editor omits Django's exit-time
`foreign_key_check` when every statement it ran was `CREATE`/`DROP [UNIQUE]
INDEX` and SQLite reports no row change (so a `RunPython` in the same migration
still gets the scan). Table rebuilds and data changes keep it. **Tradeoff:** a
foreign-key conflict already present is not re-found by an index migration; the
background verifier's per-table `foreign_key_check` remains the detector. On a
4 GB test database the scan cost about 50 s per migration.
No production migration records are faked or deleted.

## Verification while serving traffic

**Why the old scan was slow.** `PRAGMA quick_check` walks every b-tree in key
order. After churn, key order is unrelated to file order, so on spinning or
network storage each page is a separate seek: a 5.2 GB database took 1.9 h with
about 43 s of CPU (sequential read of the same file ran at 44 MB/s). Giving
SQLite more cache does not help when the container's memory is smaller than the
database, and no `PRAGMA` changes the walk order. The fix is to verify in file
order.

Supervisor runs `config/sqlite_background.py` (`sqlite-verification`) 60 seconds
after start and then every 10 minutes, niced. It is the same slice as
`sqlite_startup --background`, run without starting Django: the verifier needs
only the standard library, and two Django start-ups per slice (the command and
its deadline worker, about 6.5 s of CPU) were most of an idle instance's CPU. It makes no copy and takes no
write lock. Nothing blocks startup or serving, and nothing is re-run because the
image changed: state is tied to the database file (`DB_FILE.verification.
progress.json`), so work resumes across restarts and upgrades, and a replaced
file starts over. Units, oldest first:

| Unit | Cadence | What it proves |
|---|---|---|
| **Page sweep** (`sqlite_page_sweep.py`) | 7 days | Every page read once, sequentially, from a WAL-aware snapshot (`sqlite_dbpage`; without it the raw file only while the WAL is empty). Header and file size; every b-tree page header, cell-pointer array and content bounds; every child pointer in range, unique and of the right family; schema roots; **overflow chains** (length matches payload, no sharing); freelist chain and count; pages nobody references. |
| `foreign_key_check(table)` per table | 7 days | Relationship conflicts. Reads the child table and probes parent keys; it needs no extra indexes on the child. |
| `quick_check(table)` per table (deep tier) | 30 days, best effort | Record format, key ordering. Limited to 150 s per table; a table that does not finish is listed in `deep_incomplete` and never blocks `verified_through`. |

The sweep does not parse records or compare keys, so those are covered only by
the deep tier, and index-versus-table agreement by neither (`quick_check` never
did that; `integrity_check` does). On damaged copies of a churned 4 KiB-page
database the sweep and `quick_check` flagged the same number of cases (random
8-byte flips 1/60 each, zeroed pages 28/60, clobbered page headers 46/60,
truncation 60/60; most flips land in free space or row data neither tool
inspects).

Bounds: a slice does at most 300 s of table work, all checking is capped at
3600 s per rolling 24 h, the sweep gets its own 1800 s allowance. Overrides:
`FLOPPY_SQLITE_VERIFY_{SLICE,DAILY,UNIT,SWEEP}_SECONDS`. Read transactions last
one slice (sweep: its duration), so WAL checkpoints can stall that long.
Findings use the existing reports: damage writes a `corrupt` incident and
conflicts a `blocked` one, both blocking the next start; live rows are never
touched. Busy/I/O/interrupted errors are transient and retried.

`--prepare-migrations` without `--fast` (the explicit operator path) keeps the
copy, `quick_check` and `foreign_key_check` path (maintenance window, needed for
the migration permit). The scheduled snapshot task still runs `quick_check` on
its copy; that is not yet sliced or swept.

Observe `[sqlite-verification] slice status=...` logs and the progress file
(`verified_through`, `unverified_units`, `fk_incomplete`, `deep_incomplete`).
Run a slice manually with `python manage.py sqlite_startup --background` (or
`python config/sqlite_background.py`; both share `run_background_slice`).

A snapshot passing `quick_check` establishes structural integrity of its
copy only. It does **not** establish relational integrity. Existing scheduled
snapshots retain their quick-check-only contract and cannot satisfy the
migration gate. The comprehensive verifier separately runs
`foreign_key_check`, publishes relationship findings as a blocking incident,
and leaves live rows untouched. Corruption/relationship findings remain on
disk, visible in logs and block the next startup. They do not trigger repairs
or terminate an already-serving process.

For relationship recovery, stop writers and run
`python manage.py sqlite_startup --recover`; this uses the existing
incident-scoped, backup-protected recovery policy and operator decisions.
Then prepare pending migrations again. For physical corruption or unreadable
reports, preserve the database and sidecars, inspect/restore a known-good
backup offline and explicitly archive the old incident only after confirming
the restored database is healthy. Never delete WAL/SHM to force a start.
