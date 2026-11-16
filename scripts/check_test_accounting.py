#!/usr/bin/env python3
"""Prove that every required test ran exactly once across the CI shards.

Inputs are the files the test runner writes when FLOPPY_TEST_ACCOUNTING_DIR is
set (src/config/test_runner.py):

    --expected DIR   A directory holding one or more subdirectories, each with a
                     selected.txt from a FLOPPY_TEST_LIST_ONLY=1 discovery run.
                     Their union is the set of tests that must run.
    --shards DIR     A directory holding one subdirectory per shard, each with
                     the shard's selected.txt and reported.txt.

The check fails, naming the test IDs, unless:

* the union of every shard's selected IDs equals the expected IDs,
* no ID is selected by two shards, or twice by one,
* every selected ID has a reported outcome (pass, fail, error or skip).

A shard whose directory is missing entirely shows up as missing IDs. Standard
library only, so the aggregator job needs no dependencies.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from pathlib import Path

SHOWN = 40

# unittest reports a failure or skip in class or module setup once, under a
# stand-in named like "setUpClass (app.tests.test_x.SomeTests)", instead of
# once per test. That outcome is the result of every test it covers.
_FIXTURE = re.compile(
    r"^(setUpClass|tearDownClass|setUpModule|tearDownModule) \((.+)\)$"
)


def _covered(selected, reported: set[str]) -> set[str]:
    """Return the selected IDs answered directly or by their class or module."""
    prefixes = tuple(
        match.group(2) + "." for line in reported if (match := _FIXTURE.match(line))
    )
    return {
        test_id
        for test_id in selected
        if test_id in reported or (prefixes and test_id.startswith(prefixes))
    }


def _read_ids(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [line.strip() for line in path.read_text().splitlines() if line.strip()]


def _subdirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(child for child in root.iterdir() if child.is_dir())


def _listing(title: str, ids) -> list[str]:
    ids = sorted(ids)
    lines = [f"{title}: {len(ids)}"]
    lines.extend(f"  {test_id}" for test_id in ids[:SHOWN])
    if len(ids) > SHOWN:
        lines.append(f"  ... and {len(ids) - SHOWN} more")
    return lines


def check(expected_root: Path, shards_root: Path) -> tuple[bool, list[str]]:
    """Return (passed, report lines)."""
    report: list[str] = []
    problems = False

    expected_counts: Counter[str] = Counter()
    groups = [d for d in _subdirs(expected_root) if (d / "selected.txt").is_file()]
    for group in groups:
        expected_counts.update(_read_ids(group / "selected.txt"))
    expected = set(expected_counts)
    report.append(
        f"Discovered {len(expected)} required tests in {len(groups)} group(s): "
        + ", ".join(group.name for group in groups)
    )
    if not groups:
        problems = True
        report.append("No discovery output found under " + str(expected_root))
    twice_expected = [i for i, n in expected_counts.items() if n > 1]
    if twice_expected:
        problems = True
        report.extend(_listing("Discovered in more than one group", twice_expected))

    owner: dict[str, list[str]] = {}
    no_result: dict[str, list[str]] = {}
    shards = _subdirs(shards_root)
    if not shards:
        problems = True
        report.append("No shard output found under " + str(shards_root))
    for shard in shards:
        selected = _read_ids(shard / "selected.txt")
        reported = set(_read_ids(shard / "reported.txt"))
        counts = Counter(selected)
        for test_id in counts:
            owner.setdefault(test_id, []).append(shard.name)
        doubled = [i for i, n in counts.items() if n > 1]
        if doubled:
            problems = True
            report.extend(_listing(f"{shard.name}: selected more than once", doubled))
        answered = _covered(counts, reported)
        missing_results = sorted(set(counts) - answered)
        no_result[shard.name] = missing_results
        status = "ok" if not missing_results else f"{len(missing_results)} no result"
        if not (shard / "selected.txt").is_file():
            status = "selected.txt missing"
        report.append(
            f"  {shard.name}: {len(counts)} selected, "
            f"{len(answered)} reported ({status})"
        )

    overlaps = {i: names for i, names in owner.items() if len(names) > 1}
    if overlaps:
        problems = True
        report.append(f"Selected by more than one shard: {len(overlaps)}")
        report.extend(
            f"  {test_id}  ({', '.join(overlaps[test_id])})"
            for test_id in sorted(overlaps)[:SHOWN]
        )

    selected_all = set(owner)
    missing = expected - selected_all
    extra = selected_all - expected
    if missing:
        problems = True
        report.extend(_listing("Required but selected by no shard", missing))
    if extra:
        problems = True
        report.extend(_listing("Selected but not in the required set", extra))
    for name, ids in no_result.items():
        if ids:
            problems = True
            report.extend(_listing(f"{name}: selected but no result reported", ids))

    if not problems:
        report.append(
            f"OK: all {len(expected)} required tests were selected by exactly "
            "one shard and reported a result."
        )
    return not problems, report


def main(argv: list[str] | None = None) -> int:
    """Print the report, add it to the job summary, and return the exit status."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--expected", type=Path, required=True)
    parser.add_argument("--shards", type=Path, required=True)
    args = parser.parse_args(argv)

    passed, report = check(args.expected, args.shards)
    text = "\n".join(report)
    print(text)  # noqa: T201
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as handle:
            handle.write("## Test accounting\n\n```\n" + text + "\n```\n")
    if not passed:
        print(  # noqa: T201
            "::error::Test accounting failed: see the lists above.",
            file=sys.stderr,
        )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
