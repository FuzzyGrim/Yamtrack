from __future__ import annotations

import argparse
import ast
import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.db.migrations.loader import MigrationLoader

from app.migration_operations import SHARED_OPERATION_NAMES

DEFAULT_APPS = ("app", "users", "lists", "integrations", "events")
DEFAULT_BASE_REF = "upstream/dev"
MIGRATION_FILE_RE = re.compile(r"^\d{4}_.+\.py$")
RISKY_OP_RE = re.compile(
    r"\bmigrations\.(AddConstraint|RemoveConstraint|AddIndex|RemoveIndex|AddField)\s*\("
)
SHARED_OPERATIONS_MODULE = "app.migration_operations"
RISKY_FIX_HINTS = {
    operation: (f"use {operation}{suffix} from {SHARED_OPERATIONS_MODULE}")
    for operation, suffix in (
        ("AddConstraint", "IfNotExists"),
        ("RemoveConstraint", "IfExists"),
        ("AddIndex", "IfNotExists"),
        ("RemoveIndex", "IfExists"),
        ("AddField", "IfNotExists"),
    )
}
INLINE_ALLOWLIST_PATH = Path(__file__).with_name(
    "inline_migration_operations_allowlist.txt"
)


@dataclass(frozen=True)
class RiskyOpViolation:
    """A risky migration operation found in a fork-only migration file."""

    path: str
    line_number: int
    operation: str
    line: str


@dataclass(frozen=True)
class InlineOperationViolation:
    """A migration that defines a shared idempotent operation inline."""

    path: str
    line_number: int
    class_name: str


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


@lru_cache(maxsize=1)
def _git_binary() -> str:
    git_binary = shutil.which("git")
    if not git_binary:
        message = "Could not find git executable in PATH."
        raise CommandError(message)
    return git_binary


def _run_git(repo_root: Path, *args: str) -> str:
    process = subprocess.run(  # noqa: S603
        [_git_binary(), *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        stderr = process.stderr.strip()
        message = f"git {' '.join(args)} failed: {stderr}"
        raise CommandError(message)
    return process.stdout


def _git_ref_exists(repo_root: Path, ref: str) -> bool:
    process = subprocess.run(  # noqa: S603
        [_git_binary(), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    return process.returncode == 0


def _resolve_base_ref(repo_root: Path, requested_ref: str) -> tuple[str, list[str]]:
    warnings = []
    if _git_ref_exists(repo_root, requested_ref):
        return requested_ref, warnings

    fallback_candidates = ("origin/dev", "dev")
    for candidate in fallback_candidates:
        if _git_ref_exists(repo_root, candidate):
            warnings.append(
                f"Base ref '{requested_ref}' not found; using fallback '{candidate}'."
            )
            return candidate, warnings

    message = (
        f"Base ref '{requested_ref}' not found and no fallback ref exists "
        "(tried: origin/dev, dev)."
    )
    raise CommandError(message)


def _parse_apps(raw_apps: str) -> list[str]:
    apps = []
    seen = set()
    for chunk in raw_apps.split(","):
        for app in chunk.split():
            app_name = app.strip()
            if app_name and app_name not in seen:
                apps.append(app_name)
                seen.add(app_name)
    return apps


def _local_migration_paths(repo_root: Path, apps: list[str]) -> set[str]:
    paths = set()
    for app in apps:
        migration_dir = repo_root / "src" / app / "migrations"
        if not migration_dir.exists():
            continue
        for migration_file in migration_dir.glob("[0-9][0-9][0-9][0-9]_*.py"):
            if MIGRATION_FILE_RE.match(migration_file.name):
                paths.add(migration_file.relative_to(repo_root).as_posix())
    return paths


def _ref_migration_paths(repo_root: Path, ref: str, apps: list[str]) -> set[str]:
    tree_paths = [f"src/{app}/migrations" for app in apps]
    output = _run_git(repo_root, "ls-tree", "-r", "--name-only", ref, "--", *tree_paths)
    paths = set()
    for line in output.splitlines():
        migration_name = Path(line).name
        if MIGRATION_FILE_RE.match(migration_name):
            paths.add(line.strip())
    return paths


def _release_tag_refs(repo_root: Path) -> list[str]:
    output = _run_git(repo_root, "tag", "--list", "v*", "--sort=v:refname")
    return [line.strip() for line in output.splitlines() if line.strip()]


def _immutable_migration_paths(
    repo_root: Path, apps: list[str]
) -> tuple[set[str], list[str]]:
    immutable_paths = set()
    warnings = []

    latest_ref = None
    for candidate in ("origin/latest", "latest"):
        if _git_ref_exists(repo_root, candidate):
            latest_ref = candidate
            break

    if latest_ref:
        immutable_paths.update(_ref_migration_paths(repo_root, latest_ref, apps))
    else:
        warnings.append(
            "Could not find origin/latest or local latest for immutable checks."
        )

    for tag_ref in _release_tag_refs(repo_root):
        immutable_paths.update(_ref_migration_paths(repo_root, tag_ref, apps))

    return immutable_paths, warnings


def _collect_multi_leaf_apps(graph, apps: list[str]) -> dict[str, list[str]]:
    issues = {}
    for app in apps:
        leaf_nodes = graph.leaf_nodes(app)
        rendered_nodes = [f"{label}.{name}" for label, name in leaf_nodes]
        if len(rendered_nodes) != 1:
            issues[app] = sorted(rendered_nodes)
    return issues


def _find_risky_operations(path: Path, rel_path: str) -> list[RiskyOpViolation]:
    violations = []
    lines = path.read_text(encoding="utf-8").splitlines()
    for line_number, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        match = RISKY_OP_RE.search(line)
        if match:
            violations.append(
                RiskyOpViolation(
                    path=rel_path,
                    line_number=line_number,
                    operation=match.group(1),
                    line=stripped,
                )
            )
    return violations


@lru_cache(maxsize=1)
def _inline_allowlist() -> frozenset[str]:
    """Return the shipped migrations allowed to keep their inline copies."""
    lines = INLINE_ALLOWLIST_PATH.read_text(encoding="utf-8").splitlines()
    return frozenset(
        line.strip() for line in lines if line.strip() and not line.startswith("#")
    )


def _find_inline_operation_classes(
    path: Path, rel_path: str
) -> list[InlineOperationViolation]:
    """Return classes named like a shared operation defined in ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel_path)
    return [
        InlineOperationViolation(
            path=rel_path, line_number=node.lineno, class_name=node.name
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name in SHARED_OPERATION_NAMES
    ]


def _inline_operation_violations(
    repo_root: Path, apps: list[str]
) -> list[InlineOperationViolation]:
    """Return inline definitions in migrations that are not on the allowlist."""
    allowlist = _inline_allowlist()
    violations = []
    for rel_path in sorted(_local_migration_paths(repo_root, apps)):
        if rel_path in allowlist:
            continue
        violations.extend(
            _find_inline_operation_classes(repo_root / rel_path, rel_path)
        )
    return violations


def _duplicate_number_info(
    repo_root: Path, apps: list[str]
) -> dict[str, dict[str, list[str]]]:
    app_duplicates = {}
    for app in apps:
        migration_dir = repo_root / "src" / app / "migrations"
        if not migration_dir.exists():
            continue
        buckets: dict[str, list[str]] = {}
        for migration_file in migration_dir.glob("[0-9][0-9][0-9][0-9]_*.py"):
            buckets.setdefault(migration_file.name[:4], []).append(migration_file.name)
        duplicates = {
            number: sorted(files) for number, files in buckets.items() if len(files) > 1
        }
        if duplicates:
            app_duplicates[app] = duplicates
    return app_duplicates


class Command(BaseCommand):
    """Validate fork migration hygiene after upstream syncs."""

    help = "Validate migration graph hygiene and risky fork migration operations."

    def add_arguments(self, parser):
        """Register command arguments."""
        parser.add_argument(
            "--base-ref",
            default=DEFAULT_BASE_REF,
            help=(
                "Git ref used as the upstream migration baseline "
                "(default: upstream/dev)."
            ),
        )
        parser.add_argument(
            "--apps",
            default=" ".join(DEFAULT_APPS),
            help="Space/comma separated app labels to check.",
        )
        parser.add_argument(
            "--strict",
            action=argparse.BooleanOptionalAction,
            default=True,
            help=(
                "Fail on risky raw Add/RemoveConstraint/Index operations in new "
                "fork-only migrations."
            ),
        )

    def handle(self, *_args, **options):
        """Run migration hygiene checks and exit non-zero on violations."""
        repo_root = _repo_root()
        apps = _parse_apps(options["apps"])
        strict = options["strict"]

        if not apps:
            message = "No apps provided. Use --apps with at least one app label."
            raise CommandError(message)

        loader = MigrationLoader(connections["default"], ignore_no_migrations=True)
        multi_leaf_apps = _collect_multi_leaf_apps(loader.graph, apps)

        duplicate_number_info = _duplicate_number_info(repo_root, apps)
        inline_violations = _inline_operation_violations(repo_root, apps)

        resolution_warnings: list[str] = []
        immutable_warnings: list[str] = []
        risky_violations = []
        base_ref = None
        try:
            base_ref, resolution_warnings = _resolve_base_ref(
                repo_root, options["base_ref"]
            )
        except CommandError as exc:
            resolution_warnings.append(
                f"could not resolve a base ref ({exc}); skipping risky-operation check."
            )
        else:
            local_paths = _local_migration_paths(repo_root, apps)
            base_paths = _ref_migration_paths(repo_root, base_ref, apps)
            fork_only_paths = sorted(local_paths - base_paths)

            immutable_paths, immutable_warnings = _immutable_migration_paths(
                repo_root, apps
            )
            enforce_paths = [
                path for path in fork_only_paths if path not in immutable_paths
            ]

            for rel_path in enforce_paths:
                risky_violations.extend(
                    _find_risky_operations(repo_root / rel_path, rel_path)
                )

        for warning in [*resolution_warnings, *immutable_warnings]:
            self.stdout.write(self.style.WARNING(f"Warning: {warning}"))

        if duplicate_number_info:
            self.stdout.write(
                "Info: duplicate migration numbers detected "
                "(expected when merge migrations exist)."
            )

        failures = []
        if strict and base_ref is None:
            failures.append(
                "Strict migration hygiene requires a resolvable base ref; "
                "fetch it or pass --base-ref explicitly."
            )
        if multi_leaf_apps:
            lines = ["Multiple migration leaf nodes detected:"]
            for app, nodes in sorted(multi_leaf_apps.items()):
                rendered_nodes = ", ".join(nodes) if nodes else "<none>"
                lines.append(f"- {app}: {rendered_nodes}")
                lines.append(
                    f"  Fix: point the migration your branch adds at the other leaf "
                    f"and renumber it. Only when both leaves are already on latest, "
                    f"run python src/manage.py makemigrations {app} --merge"
                )
            failures.append("\n".join(lines))

        if inline_violations and strict:
            lines = [
                "Migrations define a shared idempotent operation inline. "
                f"Import it instead: from {SHARED_OPERATIONS_MODULE} import "
                "<OperationName>"
            ]
            lines.extend(
                f"- {violation.path}:{violation.line_number}: "
                f"class {violation.class_name}"
                for violation in inline_violations
            )
            failures.append("\n".join(lines))
        elif inline_violations:
            self.stdout.write(
                self.style.WARNING(
                    f"Warning: {len(inline_violations)} inline idempotent "
                    f"operation classes found; import them from "
                    f"{SHARED_OPERATIONS_MODULE} (strict mode disabled)."
                )
            )

        if risky_violations and strict:
            lines = [
                "Risky raw schema operations found in new fork-only migrations "
                "(strict mode is enabled):"
            ]
            for violation in risky_violations[:25]:
                fix_hint = RISKY_FIX_HINTS.get(
                    violation.operation, "use idempotent wrapper"
                )
                lines.append(
                    f"- {violation.path}:{violation.line_number}: "
                    f"migrations.{violation.operation}(...) -> {fix_hint}"
                )
            remaining = len(risky_violations) - 25
            if remaining > 0:
                lines.append(f"- ... and {remaining} more violations")
            failures.append("\n".join(lines))
        elif risky_violations:
            self.stdout.write(
                self.style.WARNING(
                    f"Warning: {len(risky_violations)} risky operations found "
                    "(strict mode disabled)."
                )
            )

        if failures:
            details = "\n\n".join(failures)
            raise CommandError(details)

        self.stdout.write(
            self.style.SUCCESS(
                "Migration hygiene checks passed "
                f"(base_ref={base_ref}, apps={','.join(apps)}, strict={strict})."
            )
        )
