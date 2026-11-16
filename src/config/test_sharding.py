"""Split one serial test run into N independent shards, by test module.

``FLOPPY_TEST_SHARD=<index>/<count>`` (1-based) makes the runner keep only the
tests whose module this function assigns to ``index``. Unset, nothing is
filtered. Each shard is its own serial process; this is not Django's
``--parallel`` pool, which loses results (docs/architecture/test-suite-cost.md,
"Cause 3").

The assignment is a pure function of the sorted module list, the committed
weights file and the shard count, so every process that discovers the same
tests agrees on it, and every module lands in exactly one shard by
construction:

* The unit is the module (``test.__class__.__module__``), so a class's
  ``setUpClass``/``setUpTestData`` is never split across processes.
* Modules named in ``test_shard_weights.json`` are placed heaviest first, each
  on the currently lightest shard (ties go to the lowest index).
* A module missing from the file goes to ``sha256(name) mod count``. That is
  stable across processes, unlike Python's salted ``hash``. Stale or missing
  weights only cost balance, never completeness.

This file imports nothing from Django, so its functions are cheap to test and
to call from the runner before any database exists.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

WEIGHTS_PATH = Path(__file__).with_name("test_shard_weights.json")


class ShardSpecError(ValueError):
    """``FLOPPY_TEST_SHARD`` is not ``<index>/<count>`` with 1 <= index <= count."""


def parse_shard(raw: str | None) -> tuple[int, int] | None:
    """Return ``(index, count)``, or None when sharding is off."""
    if raw is None or not raw.strip():
        return None
    text = raw.strip()
    index_text, sep, count_text = text.partition("/")
    try:
        index, count = int(index_text), int(count_text)
    except ValueError:
        index = count = 0
    if not sep or count < 1 or not 1 <= index <= count:
        message = (
            f"FLOPPY_TEST_SHARD={raw!r} is not <index>/<count> with "
            "1 <= index <= count, for example 2/4."
        )
        raise ShardSpecError(message)
    return index, count


def load_weights(path: Path = WEIGHTS_PATH) -> dict[str, float]:
    """Seconds per module from the committed file; empty if it is missing."""
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    modules = data.get("modules", {}) if isinstance(data, dict) else {}
    return {
        str(name): float(seconds)
        for name, seconds in modules.items()
        if isinstance(seconds, int | float) and seconds >= 0
    }


def stable_bucket(module: str, count: int) -> int:
    """1-based shard for a module with no weight, the same in every process."""
    digest = hashlib.sha256(module.encode()).digest()
    return int.from_bytes(digest[:8], "big") % count + 1


def assign(modules, weights: dict[str, float], count: int) -> dict[str, int]:
    """Map each module to a 1-based shard. Every module gets exactly one."""
    if count < 1:
        message = "shard count must be at least 1"
        raise ShardSpecError(message)
    names = sorted(set(modules))
    weighted = sorted(
        (name for name in names if name in weights),
        key=lambda name: (-weights[name], name),
    )
    loads = [0.0] * count
    assignment: dict[str, int] = {}
    for name in weighted:
        lightest = min(range(count), key=lambda shard: (loads[shard], shard))
        loads[lightest] += weights[name]
        assignment[name] = lightest + 1
    for name in names:
        if name not in assignment:
            assignment[name] = stable_bucket(name, count)
    return assignment


def shard_database_name(base: str, raw_shard: str | None) -> str:
    """Return a test database name of this shard's own, or ``base`` unsharded.

    Shards started together by ``scripts/test.sh --shards`` share one server
    when the checkout uses PostgreSQL; each must create, migrate and drop its
    own database. SQLite test databases are per process already.
    """
    shard = parse_shard(raw_shard)
    if shard is None:
        return base
    index, count = shard
    return f"{base}_shard{index}of{count}"


def module_of(test) -> str:
    """Return the unit of assignment for one test case: its module."""
    return type(test).__module__
