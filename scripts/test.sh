#!/usr/bin/env bash
# Tiered test runner. Default is the fast suite (excludes @tag("slow")
# benchmark/Playwright tests) with bounded output via --buffer.
#
# Usage:
#   scripts/test.sh                       Fast suite (default; use this)
#   scripts/test.sh <label> [...]         Targeted run, e.g. app.tests.test_query_counts
#                                         (skips @tag("network"); run those with
#                                         --network <label>)
#   scripts/test.sh --full                Full suite incl. slow tests (20+ min,
#                                         needs `playwright install`)
#   scripts/test.sh --slow                Only @tag("slow") tests
#   scripts/test.sh --network             Only @tag("network") tests (needs API
#                                         keys and internet; excluded by default
#                                         because they are slow and flaky)
#   scripts/test.sh --affected            Tests that executed the changed lines,
#                                         from .floppy/affected.coverage when
#                                         that map exists. Otherwise the import
#                                         walk. The fast suite for templates,
#                                         static, the lockfile, settings, and
#                                         the runner.
#   scripts/test.sh --affected --include-slow
#                                         Same selection. A full fallback keeps
#                                         @tag("slow"), matching CI.
#   scripts/test.sh --shards N [label ...]
#                                         N independent serial processes, each
#                                         running one FLOPPY_TEST_SHARD=i/N
#                                         slice by test module. No labels means
#                                         the fast suite. Logs in .floppy/shards.
#   scripts/test.sh --affected-record     Record the map: the CI suite (slow
#                                         included, network excluded) under
#                                         coverage, written to
#                                         .floppy/affected.coverage.
#
# Real-migration runs reuse a saved migrated database when no migration input
# changed; FLOPPY_TEST_DB_TEMPLATE=0 forces a replay.
#
# Extra manage.py test flags (e.g. -v 2, --failfast) pass through after the mode.
set -euo pipefail

cd "$(dirname "$0")/.."

# "config" holds the settings, the startup integrity check and the recovery
# page. Leaving it out let a helper that nothing called, and a report that
# always raised, both ship under a green suite (PR #780).
APPS=(app users integrations lists events api config)
COMMON=(--parallel --buffer)

FLOPPY_TEST_TIMEOUT="${FLOPPY_TEST_TIMEOUT:-2700}"

# Dump every thread's stack shortly before the timeout kills the run, so a
# lost-result hang leaves evidence instead of just a non-zero exit. Only worth
# arming when the timeout leaves room for it.
if [ -z "${FLOPPY_TEST_WATCHDOG:-}" ] && [ "$FLOPPY_TEST_TIMEOUT" != "0" ] \
  && [ "$FLOPPY_TEST_TIMEOUT" -gt 180 ] 2>/dev/null; then
  export FLOPPY_TEST_WATCHDOG=$((FLOPPY_TEST_TIMEOUT - 120))
fi

if [ "$FLOPPY_TEST_TIMEOUT" != "0" ] && command -v timeout >/dev/null 2>&1; then
  # SIGTERM first so the runner can tear its databases down, SIGKILL 30s later
  # if it is wedged hard enough to ignore that.
  RUNNER=(timeout --kill-after=30s "$FLOPPY_TEST_TIMEOUT" uv run --no-sync python)
else
  RUNNER=(uv run --no-sync python)
fi

if [ "${FLOPPY_TEST_FAST_DB:-}" = "1" ]; then
  echo "[test.sh] FLOPPY_TEST_FAST_DB=1: schema built from models, migrations NOT replayed." >&2
fi

# Reuse the migrated SQLite test database while every migration input is
# unchanged (src/config/test_db_template.py). The runner prints one "[test-db]"
# line saying whether it replayed or restored. On by default locally. In CI
# only the jobs that set it use it (app-tests.yml); everything else replays.
# FLOPPY_TEST_DB_TEMPLATE=0 turns it off anywhere.
if [ -z "${FLOPPY_TEST_DB_TEMPLATE:-}" ]; then
  if [ -n "${CI:-}" ]; then
    export FLOPPY_TEST_DB_TEMPLATE=0
  else
    export FLOPPY_TEST_DB_TEMPLATE=1
  fi
fi

case "${1:-}" in
  --full)
    shift
    exec env FLOPPY_TEST_ALLOW_NETWORK=1 \
      "${RUNNER[@]}" src/manage.py test "${APPS[@]}" "${COMMON[@]}" "$@"
    ;;
  --slow)
    shift
    exec "${RUNNER[@]}" src/manage.py test "${APPS[@]}" "${COMMON[@]}" "$@" --tag slow
    ;;
  --network)
    shift
    exec env FLOPPY_TEST_ALLOW_NETWORK=1 \
      "${RUNNER[@]}" src/manage.py test "${APPS[@]}" "${COMMON[@]}" "$@" --tag network
    ;;
  --shards)
    # N separate serial processes, each FLOPPY_TEST_SHARD=i/N, not Django's
    # --parallel pool (docs/architecture/test-suite-cost.md, "Cause 3").
    shift
    count="${1:-}"
    if ! [[ "$count" =~ ^[1-9][0-9]*$ ]]; then
      echo "[test.sh] --shards needs a positive count, e.g. --shards 4 integrations" >&2
      exit 2
    fi
    shift
    if [ "${FLOPPY_TEST_PARALLEL:-1}" != "1" ]; then
      echo "[test.sh] --shards runs serial processes; unset FLOPPY_TEST_PARALLEL." >&2
      exit 2
    fi
    if [ "$#" -eq 0 ]; then
      # Same selection as the default fast suite.
      args=("${APPS[@]}" --exclude-tag slow --exclude-tag network)
    else
      args=("$@" --exclude-tag network)
    fi
    logs=".floppy/shards"
    mkdir -p "$logs"
    pids=()
    for ((i = 1; i <= count; i++)); do
      rm -f "$logs/$i.log"
      shard_env=(FLOPPY_TEST_SHARD="$i/$count" FLOPPY_TEST_PARALLEL=1)
      if [ -n "${FLOPPY_TEST_ACCOUNTING_DIR:-}" ]; then
        rm -rf "$FLOPPY_TEST_ACCOUNTING_DIR/shard-$i"
        shard_env+=(FLOPPY_TEST_ACCOUNTING_DIR="$FLOPPY_TEST_ACCOUNTING_DIR/shard-$i")
      fi
      env "${shard_env[@]}" "${RUNNER[@]}" src/manage.py test --buffer "${args[@]}" \
        >"$logs/$i.log" 2>&1 &
      pids+=("$!")
    done
    failed=0
    for ((i = 1; i <= count; i++)); do
      status=0
      wait "${pids[$((i - 1))]}" || status=$?
      echo "=== shard $i/$count: exit $status (full log: $logs/$i.log) ==="
      # Bounded: the summary lines, or the end of a failure.
      if [ "$status" -eq 0 ]; then
        grep -E '^\[test-(shard|db)\]|^Ran [0-9]+ tests|^OK' "$logs/$i.log" || true
      else
        tail -n 60 "$logs/$i.log"
        failed=1
      fi
    done
    if [ "$failed" -ne 0 ]; then
      echo "[test.sh] at least one shard failed" >&2
    fi
    exit "$failed"
    ;;
  --affected-record)
    shift
    mkdir -p .floppy
    rm -f .coverage .coverage.* .floppy/affected.coverage
    # A failing test still executed lines. Keep the map, then propagate the
    # suite's exit status.
    set +e
    # Record against a real replay, so the map never depends on a saved database.
    env COVERAGE_CORE=ctrace FLOPPY_TEST_DB_TEMPLATE=0 "${RUNNER[@]}" -m coverage run \
      src/manage.py test "${APPS[@]}" --parallel "$@" --exclude-tag network
    status=$?
    set -e
    uv run --no-sync coverage combine
    cp .coverage .floppy/affected.coverage
    echo "[test.sh] wrote .floppy/affected.coverage" >&2
    exit "$status"
    ;;
  --affected)
    shift
    include_slow=0
    if [ "${1:-}" = "--include-slow" ]; then
      include_slow=1
      shift
    fi
    # Run the file, not `python -m config.affected_tests`. Importing the
    # config package executes config/__init__.py, which boots Celery.
    selection="$(PYTHONPATH=src "${RUNNER[@]}" src/config/affected_tests.py)"
    mode="${selection%%$'\n'*}"
    case "$mode" in
      none)
        exit 0
        ;;
      full)
        if [ "$include_slow" -eq 1 ]; then
          exec "${RUNNER[@]}" src/manage.py test "${APPS[@]}" "${COMMON[@]}" "$@" \
            --exclude-tag network
        fi
        exec "${RUNNER[@]}" src/manage.py test "${APPS[@]}" "${COMMON[@]}" "$@" \
          --exclude-tag slow --exclude-tag network
        ;;
      labels)
        labels=()
        rest="${selection#*$'\n'}"
        if [ "$rest" = "$selection" ]; then
          rest=""
        fi
        while IFS= read -r line; do
          if [ -n "$line" ]; then
            labels+=("$line")
          fi
        done <<EOF
$rest
EOF
        if [ "${#labels[@]}" -eq 0 ]; then
          echo "[test.sh] --affected produced no labels" >&2
          exit 0
        fi
        exec "${RUNNER[@]}" src/manage.py test "${COMMON[@]}" "${labels[@]}" "$@" \
          --exclude-tag network
        ;;
      *)
        echo "[test.sh] --affected: unexpected selector output" >&2
        exit 1
        ;;
    esac
    ;;
  "")
    exec "${RUNNER[@]}" src/manage.py test "${APPS[@]}" "${COMMON[@]}" \
      --exclude-tag slow --exclude-tag network
    ;;
  *)
    # Network tests cannot pass here: the offline guard is installed unless
    # FLOPPY_TEST_ALLOW_NETWORK is set, so a targeted run of a module holding
    # them failed for reasons unrelated to the change. Use --network <label>.
    exec "${RUNNER[@]}" src/manage.py test "${COMMON[@]}" "$@" --exclude-tag network
    ;;
esac
