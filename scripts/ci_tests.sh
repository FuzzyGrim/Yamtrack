#!/usr/bin/env bash
# What App Tests must run, and how its shards cover it. Called by
# .github/workflows/app-tests.yml; kept in one file so the two can be read side
# by side. scripts/check_test_accounting.py then proves, on every run, that the
# shards' union equals the contract, with no overlap and a result for each.
#
# Usage: CI_TEST_MODE=<pr|release|full> FLOPPY_TEST_ACCOUNTING_DIR=<dir> \
#          scripts/ci_tests.sh <discover|browser|unit>
#
#   discover  The contract. Lists test IDs only (FLOPPY_TEST_LIST_ONLY=1): no
#             database, no test runs. Written per app group, independently of
#             how the shards are split, into <dir>/<group>/selected.txt.
#   browser   Every required Playwright test, in one serial process. The only
#             job that needs Chromium.
#   unit      Every non-Playwright test of every app, cut by module with
#             FLOPPY_TEST_SHARD=<i>/<n> (src/config/test_sharding.py).
#
# Modes, unchanged from the app-label shards this replaces:
#   pr       Pull requests. All tests except @tag("network"), the one
#            flaky_alphabet_rail case, and Playwright in users, lists, events,
#            api and config; those run in the non-blocking "Browser E2E (other,
#            diagnostic)" job.
#   release  The Docker publish gate. Every non-Playwright test.
#   full     Manual and other calls. Every test except @tag("network").
#
# scripts/test.sh adds --exclude-tag network to every labelled run.
set -euo pipefail

cd "$(dirname "$0")/.."

mode="${CI_TEST_MODE:-}"
role="${1:-}"
out="${FLOPPY_TEST_ACCOUNTING_DIR:?set FLOPPY_TEST_ACCOUNTING_DIR}"

APPS=(app integrations users lists events api config)
OTHER=(users lists events api config)
REPORT=(--timing --durations 30)

list() {
  local group="$1"
  shift
  env -u FLOPPY_TEST_SHARD FLOPPY_TEST_LIST_ONLY=1 \
    FLOPPY_TEST_ACCOUNTING_DIR="$out/$group" scripts/test.sh "$@"
}

case "$mode/$role" in
  pr/discover)
    list app app --exclude-tag flaky_alphabet_rail
    list integrations integrations
    list other "${OTHER[@]}" --exclude-tag playwright
    ;;
  release/discover)
    list all "${APPS[@]}" --exclude-tag playwright
    ;;
  full/discover)
    list all "${APPS[@]}"
    ;;
  pr/browser)
    exec scripts/test.sh app integrations --tag playwright \
      --exclude-tag flaky_alphabet_rail "${REPORT[@]}"
    ;;
  release/browser)
    # The publish gate excludes Playwright. An empty, complete record keeps
    # the accounting uniform.
    mkdir -p "$out"
    : >"$out/selected.txt"
    : >"$out/reported.txt"
    echo "::notice::Docker release gate excludes Playwright; pull requests still run it."
    ;;
  full/browser)
    exec scripts/test.sh "${APPS[@]}" --tag playwright "${REPORT[@]}"
    ;;
  pr/unit | release/unit | full/unit)
    : "${FLOPPY_TEST_SHARD:?unit shards need FLOPPY_TEST_SHARD=<i>/<n>}"
    exec scripts/test.sh "${APPS[@]}" --exclude-tag playwright "${REPORT[@]}"
    ;;
  *)
    echo "usage: CI_TEST_MODE=<pr|release|full> $0 <discover|browser|unit>" >&2
    exit 2
    ;;
esac
