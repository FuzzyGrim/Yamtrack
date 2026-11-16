#!/usr/bin/env python3
"""Rewrite src/config/test_shard_weights.json from a green CI run.

Every CI shard uploads a ``test-accounting-<shard>`` artifact holding
``module_seconds.json`` (wall seconds per test module, written by the runner
when FLOPPY_TEST_ACCOUNTING_DIR is set). Download the ``unit-*`` shards of one
green run and pass their directories:

    gh run download <run-id> -R dannyvfilms/Floppy -p 'test-accounting-unit-*' -D /tmp/acc
    python3 scripts/refresh_test_shard_weights.py --run <run-id> /tmp/acc/*

Only the non-browser shards matter: they are the ones split by these weights.
Directories from several runs may be passed together (repeat --run): a module
appears in one shard per run, so its weight is the mean of its appearances.
Averaging two or three runs smooths out a slow runner.
Stale weights only cost balance, never completeness.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

WEIGHTS = Path(__file__).resolve().parent.parent / "src/config/test_shard_weights.json"


def merge(directories: list[Path]) -> dict[str, float]:
    """Average each module's seconds over the shard directories that ran it."""
    totals: dict[str, float] = defaultdict(float)
    seen: dict[str, int] = defaultdict(int)
    for directory in directories:
        path = directory / "module_seconds.json"
        if not path.is_file():
            message = f"{path} is missing; is this a shard artifact?"
            raise SystemExit(message)
        for module, seconds in json.loads(path.read_text()).items():
            totals[module] += float(seconds)
            seen[module] += 1
    return {module: round(totals[module] / seen[module], 2) for module in sorted(totals)}


def main(argv: list[str] | None = None) -> int:
    """Write the merged weights file."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--run", required=True, action="append", help="a CI run id, for the record"
    )
    parser.add_argument("directories", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=WEIGHTS)
    args = parser.parse_args(argv)

    modules = merge(args.directories)
    document = {
        "source": (
            f"App Tests run(s) {', '.join(args.run)}: mean of "
            f"{len(args.directories)} non-browser shard results. "
            "Refresh with scripts/refresh_test_shard_weights.py."
        ),
        "modules": modules,
    }
    args.output.write_text(json.dumps(document, indent=1) + "\n")
    total = sum(modules.values())
    print(f"wrote {len(modules)} modules, {total:.0f}s in all, to {args.output}")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main())
