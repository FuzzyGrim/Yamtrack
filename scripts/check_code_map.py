#!/usr/bin/env python3
"""Check that every path and test label docs/architecture/code-map.md names exists.

Run from anywhere: ``python3 scripts/check_code_map.py``. Standard library only.

It reads each inline backticked token outside fenced code blocks and checks:

* a repository path (starting with a known top-level directory or file) exists;
  a token with ``*`` must match at least one file, and ``<placeholder>`` segments
  are skipped because they describe a pattern, not a file;
* a dotted test label such as ``app.tests.test_library_query`` names a test
  module or package under ``src/``.

It does not check prose. Exit status 1 lists every missing path.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CODE_MAP = ROOT / "docs/architecture/code-map.md"

PATH_PREFIXES = ("src/", "docs/", "scripts/", "frontend/", "native-proof/", ".github/")
ROOT_FILES = ("AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md", "README.md")
TEST_LABEL = re.compile(r"^(?:app|api|users|lists|integrations|events|config)\.tests(?:\.\w+)+$")


def inline_tokens(text: str) -> list[str]:
    """Return inline backticked tokens, ignoring fenced code blocks."""
    text = re.sub(r"^```.*?^```", "", text, flags=re.MULTILINE | re.DOTALL)
    return re.findall(r"`([^`\n]+)`", text)


def test_label_exists(label: str) -> bool:
    """A label resolves if some prefix of it is a module or package under src/."""
    parts = label.split(".")
    for end in range(len(parts), 2, -1):
        base = ROOT / "src" / Path(*parts[:end])
        if base.with_suffix(".py").is_file() or (base / "__init__.py").is_file():
            return True
    return False


def missing_entries(text: str) -> list[str]:
    """Return the backticked paths and test labels in ``text`` that do not exist."""
    missing = []
    for token in dict.fromkeys(inline_tokens(text)):
        if " " in token or "<" in token:
            continue
        if token.startswith(PATH_PREFIXES) or token in ROOT_FILES:
            path = token.rstrip("/")
            found = any(ROOT.glob(path)) if "*" in path else (ROOT / path).exists()
            if not found:
                missing.append(token)
        elif TEST_LABEL.match(token) and not test_label_exists(token):
            missing.append(token)
    return missing


def main() -> int:
    """Print every missing entry; return 1 if there are any."""
    if not CODE_MAP.exists():
        print(f"missing: {CODE_MAP.relative_to(ROOT)}")
        return 1
    missing = missing_entries(CODE_MAP.read_text(encoding="utf-8"))
    for token in missing:
        print(f"code-map.md names a path that does not exist: {token}")
    if missing:
        print("Update docs/architecture/code-map.md to point at where the code lives now.")
        return 1
    print("code-map.md: every path exists")
    return 0


if __name__ == "__main__":
    sys.exit(main())
