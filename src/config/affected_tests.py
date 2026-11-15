"""Turn a git diff into Django test labels.

When ``.floppy/affected.coverage`` exists, selection is the tests whose
coverage context executed the old side of the diff. That map is recorded
with coverage's ``test_function`` dynamic context and the ``ctrace`` core.
The import walk is the fallback for a missing map, a new file, and anything
coverage does not measure (templates, migrations, ``__init__.py``).

The import walk is direct edges plus names re-exported from package
``__init__.py`` files. A transitive walk through ``app.models`` would select
almost every test. String imports and ``@patch`` targets are not parsed;
those misses fall back to the app label.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

APPS = ("app", "users", "integrations", "lists", "events", "api", "config")

MODE_NONE = "none"
MODE_FULL = "full"
MODE_LABELS = "labels"

# Lockfile, settings, and the runner. A change here runs the fast suite.
ESCAPE_HATCH = frozenset(
    {
        "uv.lock",
        "pyproject.toml",
        "src/manage.py",
        "scripts/test.sh",
        "src/config/settings.py",
        "src/config/test_settings.py",
        "src/config/__init__.py",
        "src/config/test_runner.py",
        "src/config/test_sharding.py",
        "src/config/test_shard_weights.json",
        "src/config/test_db_template.py",
        "src/config/affected_tests.py",
    }
)

# Loaded by string from AppConfig.ready(), so a static importer set is not
# the set of tests that can fire them.
SIGNAL_MODULES = frozenset(
    {
        "app.signals",
        "app.signals_watch_state",
        "app.signals_music",
        "integrations.signals_state",
        "users.signals",
    }
)

_OUTSIDE_RUNNER = ("mcp_server/", "scripts/tests/")
_MAP_PATH = Path(".floppy/affected.coverage")
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class MissingBaseError(RuntimeError):
    """origin/latest is not in this clone."""


class GitFailedError(RuntimeError):
    """A git command exited non-zero."""


def changed_paths(repo: Path) -> list[str]:
    """Return branch commits plus staged, unstaged, and untracked paths.

    Raises:
        MissingBaseError: ``origin/latest`` cannot be resolved.
    """
    base = _git(repo, ["merge-base", "HEAD", "origin/latest"])
    if base is None:
        raise MissingBaseError
    sha = base[0] if base else ""
    if not sha:
        raise MissingBaseError
    names: set[str] = set()
    names.update(_git(repo, ["diff", "--name-only", f"{sha}...HEAD"]) or [])
    names.update(_git(repo, ["diff", "--name-only", "HEAD"]) or [])
    names.update(_git(repo, ["ls-files", "--others", "--exclude-standard"]) or [])
    return sorted(names)


def select(
    paths: list[str],
    *,
    repo: Path,
    hooks_dir: str | None = None,
    line_map: Mapping[str, tuple[str, Collection[int]]] | None = None,
    coverage_index: Mapping[str, Mapping[int, Collection[str]]] | None = None,
) -> tuple[str, list[str], list[str]]:
    """Classify ``paths`` as ``none``, ``full``, or a sorted label list.

    ``coverage_index`` maps a repo path to the test modules that executed
    each line. ``line_map`` maps a diff path to ``(base path, old lines)``.
    An empty line set means the file is new. Both are omitted when no
    coverage map is on disk, and selection stays on the import walk.

    Returns:
        Mode, labels (empty unless mode is ``labels``), and stderr notes.
    """
    notes: list[str] = []
    outside: list[str] = []
    force_full = False
    python_paths: list[str] = []
    hook_hit = False

    for path in paths:
        if path.startswith(_OUTSIDE_RUNNER):
            outside.append(path)
            continue
        if _is_hook(path, repo, hooks_dir):
            hook_hit = True
            continue
        if path in ESCAPE_HATCH or path.startswith(("src/templates/", "src/static/")):
            force_full = True
            continue
        if path.startswith("src/") and path.endswith(".py"):
            if _app_label(path) is None:
                force_full = True
            else:
                python_paths.append(path)
            continue

    if outside:
        notes.append("outside the Django runner: " + ", ".join(outside))
    if force_full:
        notes.append("cross-cutting or runner change: running the fast suite")
        return MODE_FULL, [], notes

    labels: set[str] = set()
    unmapped: list[str] = []
    if hook_hit:
        labels.add("app")
    if coverage_index is None:
        imported, unmapped = _labels_from_imports(python_paths, repo)
        labels.update(imported)
    else:
        fallback: list[str] = []
        missed: list[str] = []
        quiet: list[str] = []
        for path in python_paths:
            hit = _labels_from_coverage(path, line_map, coverage_index, repo)
            if hit is None:
                fallback.append(path)
                if not _is_test_module(path):
                    missed.append(path)
                continue
            if hit:
                labels.update(hit)
            elif not _is_test_module(path):
                quiet.append(path)
                fallback.append(path)
        imported, unmapped = _labels_from_imports(fallback, repo)
        labels.update(imported)
        if missed:
            notes.append("no coverage for: " + ", ".join(missed))
        if quiet:
            notes.append(
                "changed lines had no test in the map; using imports: "
                + ", ".join(quiet),
            )

    if unmapped:
        notes.append("no importing test for: " + ", ".join(unmapped))

    pruned = _prune(labels)
    if not pruned:
        return MODE_NONE, [], notes
    return MODE_LABELS, pruned, notes


def main() -> int:
    """Print ``none``, ``full``, or ``labels`` plus one label per line."""
    repo = Path.cwd()
    try:
        paths = changed_paths(repo)
        line_map, coverage_index = _load_coverage(repo)
    except MissingBaseError:
        sys.stderr.write("origin/latest is missing; fetch it and rerun\n")
        return 1
    except GitFailedError:
        return 1
    hooks = os.environ.get("MUSIC_HOOKS_DIR") or None
    mode, labels, notes = select(
        paths,
        repo=repo,
        hooks_dir=hooks,
        line_map=line_map,
        coverage_index=coverage_index,
    )
    for note in notes:
        sys.stderr.write(note + "\n")
    sys.stdout.write(mode + "\n")
    if mode == MODE_LABELS:
        for label in labels:
            sys.stdout.write(label + "\n")
    return 0


def parse_diff_hunks(text: str) -> dict[str, tuple[str, frozenset[int]]]:
    """Map each diff path to the base path and the old line numbers.

    An empty line set means the file is new, so a map recorded against the
    base has nothing to say about it. A pure insertion includes the anchor
    line on the base (the line the new code was inserted after).
    """
    parsed: dict[str, tuple[str, set[int]]] = {}
    old_path: str | None = None
    new_path: str | None = None
    lines: set[int] = set()
    saw_file = False

    def flush() -> None:
        nonlocal old_path, new_path, lines, saw_file
        if not saw_file:
            return
        lookup = old_path or new_path
        if lookup:
            recorded = (lookup, set(lines))
            for path in (old_path, new_path):
                if path:
                    parsed[path] = recorded
        old_path = None
        new_path = None
        lines = set()
        saw_file = False

    for raw in text.splitlines():
        if raw.startswith("diff --git "):
            flush()
            saw_file = True
            continue
        if raw.startswith("--- "):
            old_path = _diff_path(raw[4:])
            continue
        if raw.startswith("+++ "):
            new_path = _diff_path(raw[4:])
            continue
        match = _HUNK_RE.match(raw)
        if match is None:
            continue
        start = int(match.group(1))
        count_text = match.group(2)
        count = int(count_text) if count_text is not None else 1
        if count == 0:
            if start > 0:
                lines.add(start)
            continue
        lines.update(range(start, start + count))
    flush()
    return {
        path: (lookup, frozenset(line_set))
        for path, (lookup, line_set) in parsed.items()
    }


def test_module_from_context(context: str) -> str | None:
    """Collapse a coverage ``test_function`` context to a Django module label.

    Contexts look like ``app.tests.test_media.MediaTests.test_movie``. The
    runner takes the module. A context that is not a test function is dropped.
    """
    if not context:
        return None
    parts = context.split(".")
    if parts[-1] != "runTest" and not parts[-1].startswith("test"):
        return None
    parts = parts[:-1]
    while parts and parts[-1][:1].isupper():
        parts = parts[:-1]
    if not parts or not parts[-1].startswith("test"):
        return None
    return ".".join(parts)


def changed_lines(repo: Path) -> dict[str, tuple[str, frozenset[int]]]:
    """Return old-side lines for the working tree compared with the base.

    Raises:
        MissingBaseError: ``origin/latest`` cannot be resolved.
    """
    base = _git(repo, ["merge-base", "HEAD", "origin/latest"])
    if not base:
        raise MissingBaseError
    text = _git_output(repo, ["diff", "-U0", base[0]])
    if text is None:
        raise MissingBaseError
    return parse_diff_hunks(text)


def _load_coverage(
    repo: Path,
) -> tuple[
    dict[str, tuple[str, frozenset[int]]] | None,
    dict[str, dict[int, set[str]]] | None,
]:
    """Load the coverage map and the old-side lines it should be queried with.

    A missing file means the import walk. A file that cannot be read is the
    same fallback, with a note on stderr, rather than a wrong label set.
    """
    configured = os.environ.get("FLOPPY_AFFECTED_MAP")
    map_path = Path(configured) if configured else _MAP_PATH
    if not map_path.is_absolute():
        map_path = repo / map_path
    if not map_path.is_file():
        sys.stderr.write("no coverage map; using imports\n")
        return None, None
    line_map = changed_lines(repo)
    lookups = {lookup for lookup, lines in line_map.values() if lines}
    try:
        index = index_for_diff(map_path, repo, lookups)
    except OSError as exc:
        sys.stderr.write(f"coverage map unreadable ({exc}); using imports\n")
        return None, None
    else:
        sys.stderr.write(f"coverage map: {map_path}\n")
        return line_map, index


def index_for_diff(
    data_file: Path,
    repo: Path,
    lookups: set[str],
) -> dict[str, dict[int, set[str]]]:
    """Read test modules per line for ``lookups`` out of a coverage data file."""
    from coverage.data import CoverageData
    from coverage.exceptions import CoverageException

    data = CoverageData(basename=str(data_file))
    try:
        data.read()
    except CoverageException as exc:
        raise OSError(str(exc)) from exc
    stored_by_relative: dict[str, str] = {}
    for stored in data.measured_files():
        relative = _relative_to_repo(stored, repo)
        if relative:
            stored_by_relative[relative] = stored
    index: dict[str, dict[int, set[str]]] = {}
    for relative in lookups:
        stored = stored_by_relative.get(relative)
        if stored is None:
            continue
        per_line: dict[int, set[str]] = {}
        for lineno, contexts in data.contexts_by_lineno(stored).items():
            modules = {
                module
                for context in contexts
                if (module := test_module_from_context(context))
            }
            if modules:
                per_line[lineno] = modules
        index[relative] = per_line
    return index


def _labels_from_imports(
    paths: list[str],
    repo: Path,
) -> tuple[set[str], list[str]]:
    """Return import-graph labels and the paths that fell back to an app."""
    labels: set[str] = set()
    unmapped: list[str] = []
    if not paths:
        return labels, unmapped
    reverse = _reverse_index(repo)
    for path in paths:
        module = _module_name(path)
        if module is None:
            continue
        if _is_test_module(path):
            if (repo / path).is_file():
                labels.add(module)
            continue
        if module in SIGNAL_MODULES:
            label = _app_label(path)
            if label:
                labels.add(label)
            continue
        importers = reverse.get(module, ())
        if importers:
            labels.update(importers)
            continue
        label = _app_label(path)
        if label:
            labels.add(label)
            unmapped.append(path)
    return labels, unmapped


def _labels_from_coverage(
    path: str,
    line_map: Mapping[str, tuple[str, Collection[int]]] | None,
    coverage_index: Mapping[str, Mapping[int, Collection[str]]],
    repo: Path,
) -> set[str] | None:
    """Return test labels for ``path``, or ``None`` to use the import walk.

    ``None`` is a file the map cannot answer: untracked, new, or never
    measured. An empty set means the map measured those lines and no test
    executed them.
    """
    module = _module_name(path)
    if module is None:
        return set()
    if _is_test_module(path):
        if (repo / path).is_file():
            return {module}
        return set()
    if not line_map or path not in line_map:
        return None
    lookup, line_numbers = line_map[path]
    if not line_numbers:
        return None
    by_line = coverage_index.get(lookup)
    if by_line is None:
        by_line = coverage_index.get(path)
    if by_line is None:
        return None
    found: set[str] = set()
    for line in line_numbers:
        found.update(by_line.get(line, ()))
    return found


def _diff_path(token: str) -> str | None:
    """Return the repo path from a ``---`` or ``+++`` diff header."""
    path = token.strip().split("\t", 1)[0]
    if path == "/dev/null":
        return None
    if path.startswith(("a/", "b/")):
        path = path[2:]
    if len(path) > 1 and path[0] == '"' and path[-1] == '"':
        path = path[1:-1]
    return path


def _relative_to_repo(filename: str, repo: Path) -> str | None:
    """Return ``filename`` relative to ``repo`` when it lives there."""
    path = Path(filename)
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return None


def _git_output(repo: Path, args: list[str]) -> str | None:
    """Run git and return stdout. ``None`` when merge-base fails."""
    result = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        if args[0] == "merge-base":
            return None
        if result.stderr:
            sys.stderr.write(result.stderr)
        raise GitFailedError
    return result.stdout


def _git(repo: Path, args: list[str]) -> list[str] | None:
    """Run git and return stdout lines. ``None`` when merge-base fails."""
    text = _git_output(repo, args)
    if text is None:
        return None
    return [line for line in text.splitlines() if line]


def _module_name(path: str) -> str | None:
    """Return the dotted module for ``src/**/*.py``."""
    if not path.startswith("src/") or not path.endswith(".py"):
        return None
    relative = path[len("src/") : -len(".py")]
    relative = relative.removesuffix("/__init__")
    return relative.replace("/", ".")


def _app_label(path: str) -> str | None:
    """Return the app name when ``path`` is under ``src/<app>/``."""
    prefix, _slash, rest = path.partition("/")
    app, sep, _tail = rest.partition("/")
    if prefix != "src" or not sep or app not in APPS:
        return None
    return app


def _is_test_module(path: str) -> bool:
    """Return whether ``path`` is a discoverable ``test_*.py`` module."""
    name = path.rsplit("/", 1)[-1]
    return (
        path.startswith("src/")
        and "/tests/" in path
        and name.startswith("test_")
        and name.endswith(".py")
    )


def _is_hook(path: str, repo: Path, hooks_dir: str | None) -> bool:
    """Return whether ``path`` is a music listen hook file."""
    if not hooks_dir:
        return False
    hooks = Path(hooks_dir)
    if not hooks.is_absolute():
        hooks = repo / hooks
    candidate = (repo / path).resolve()
    try:
        candidate.relative_to(hooks.resolve())
    except ValueError:
        return False
    return candidate.suffix == ".py" and not candidate.name.startswith("_")


def _prune(labels: set[str]) -> list[str]:
    """Drop module labels already covered by an app label in the same set."""
    apps = {label for label in labels if label in APPS}
    return [
        label
        for label in sorted(labels)
        if label in apps or label.split(".", 1)[0] not in apps
    ]


def _reverse_index(repo: Path) -> dict[str, set[str]]:
    """Map a production module to the test labels that import it."""
    src = repo / "src"
    if not src.is_dir():
        return {}
    known: set[str] = set()
    init_files: list[tuple[str, str]] = []
    test_files: list[tuple[str, str]] = []
    for file in src.rglob("*.py"):
        relative = file.relative_to(repo).as_posix()
        module = _module_name(relative)
        if module is None:
            continue
        known.add(module)
        if file.name == "__init__.py":
            init_files.append((relative, module))
        elif _is_test_module(relative):
            test_files.append((relative, module))

    reexports: dict[str, dict[str, str]] = {}
    for relative, module in init_files:
        reexports[module] = _reexport_names(
            (repo / relative).read_text(encoding="utf-8", errors="replace"),
            module,
            known,
        )

    reverse: dict[str, set[str]] = {}
    for relative, module in test_files:
        source = (repo / relative).read_text(encoding="utf-8", errors="replace")
        for imported in _direct_imports(source, module, reexports, known):
            reverse.setdefault(imported, set()).add(module)
    return reverse


def _reexport_names(source: str, package: str, known: set[str]) -> dict[str, str]:
    """Map names bound in a package ``__init__`` to the module they come from."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    names: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        base = _import_base(node, package, is_package=True)
        if not base:
            continue
        for alias in node.names:
            if alias.name == "*":
                continue
            child = f"{base}.{alias.name}"
            names[alias.name] = child if child in known else base
    return names


def _direct_imports(
    source: str,
    importer: str,
    reexports: dict[str, dict[str, str]],
    known: set[str],
) -> set[str]:
    """Return modules a test file imports, resolving re-exported names."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        base = _import_base(node, importer, is_package=False)
        if not base:
            continue
        found.add(base)
        exported = reexports.get(base, {})
        for alias in node.names:
            if alias.name == "*":
                found.update(exported.values())
                continue
            if alias.name in exported:
                found.add(exported[alias.name])
            child = f"{base}.{alias.name}"
            if child in known:
                found.add(child)
    return found


def _import_base(node: ast.ImportFrom, importer: str, *, is_package: bool) -> str:
    """Resolve the module an ``import from`` statement names."""
    if node.level:
        parts = importer.split(".")
        if not is_package:
            parts = parts[:-1]
        drop = node.level - 1
        if drop:
            parts = parts[:-drop] if drop <= len(parts) else []
        if node.module:
            parts.extend(node.module.split("."))
        return ".".join(parts)
    return node.module or ""


if __name__ == "__main__":
    raise SystemExit(main())
