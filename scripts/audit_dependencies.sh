#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

audit_input="$(mktemp)"
trap 'rm -f "$audit_input"' EXIT

uv export --locked --all-groups --all-extras --no-emit-local -o "$audit_input" >/dev/null
# Audit runtime and development/test groups from the pinned graph.
# uv exported the complete pinned dependency graph; do not resolve it again.
uv run --no-sync pip-audit -r "$audit_input" --no-deps --disable-pip
