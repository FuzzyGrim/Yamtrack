"""Run one background SQLite verification slice without starting Django.

Supervisor runs a slice every ten minutes (``supervisord.conf``,
``docs/architecture/sqlite-startup.md``). The verifier is standard library
only, but ``manage.py sqlite_startup --background`` started Django twice per
slice: once for the command and once more for the ``--worker`` child that runs
under the deadline. With no work due, those two start-ups were the whole cost,
about 6.5 s of CPU per slice and the largest source of idle CPU (OPS-10).

This entry runs the same slice, with the same niceness, deadline, log line and
progress file, at a fraction of that cost. Run it as a file, not with ``-m``:
``python config/sqlite_background.py``. Importing the ``config`` package
normally would load Celery and the settings chain, which is the cost this
avoids, so the script registers ``config`` as a bare package first. Only the
standard library, ``decouple`` and Django-free ``config`` modules may be
imported from here. ``manage.py sqlite_startup --background`` still works for
operators and runs the same slice through :func:`run_background_slice`.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path

WORKER_FLAG = "--worker"
# The management command's default --timeout. A slice is bounded to minutes by
# its own budgets; this only ends a worker that stopped making progress.
DEADLINE_SECONDS = 3600


class BackgroundSliceError(Exception):
    """A slice found damage or a blocking conflict; the message says which."""


def run_background_slice(db_path: str) -> dict:
    """Run one niced slice, log its status line, and raise if it failed."""
    from config import sqlite_integrity as integrity
    from config.sqlite_sliced_check import run_slice

    os.nice(10)
    state = run_slice(db_path)
    integrity._log(
        f"[sqlite-verification] slice status={state['status']} "
        f"verified_through={state.get('verified_through')} "
        f"unverified={state.get('unverified_units', [])}"
    )
    if state["status"] in {"failed", "blocked"}:
        raise BackgroundSliceError(state.get("error", state["status"]))
    return state


def database_path() -> str | None:
    """Return the live SQLite file as settings.py resolves it; None on PostgreSQL."""
    from decouple import config

    from config.data_paths import resolve_data_paths

    if config("DB_HOST", default=None):
        return None
    base_dir = Path(__file__).resolve().parent.parent
    return str(resolve_data_paths(config, base_dir)[1])


def _worker(db_path: str) -> int:
    try:
        run_background_slice(db_path)
    except (BackgroundSliceError, OSError, ValueError, sqlite3.DatabaseError) as error:
        print(f"[sqlite-verification] slice error: {error}", file=sys.stderr)  # noqa: T201
        return 1
    return 0


def supervise(command: list[str], timeout: float = DEADLINE_SECONDS) -> int:
    """Run ``command`` as a killable child with a fixed deadline.

    Termination signals are forwarded so supervisord's stop reaches the slice.
    """
    from config.sqlite_startup_watchdog import _stop

    worker = subprocess.Popen(command)  # noqa: S603 - our own interpreter and file

    def forward(signum, _frame):
        worker.send_signal(signum)

    handlers = {
        signum: signal.signal(signum, forward)
        for signum in (signal.SIGTERM, signal.SIGINT)
    }
    try:
        result = worker.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _stop(worker)
        print(  # noqa: T201
            "[sqlite-verification] Verification deadline reached; stopped the slice.",
            file=sys.stderr,
        )
        return 1
    finally:
        for signum, handler in handlers.items():
            signal.signal(signum, handler)
    if result:
        print(  # noqa: T201
            f"[sqlite-verification] SQLite maintenance exited with status {result}",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: list[str]) -> int:
    """Supervise a worker, or (with ``--worker``) run the slice itself."""
    db_path = database_path()
    if db_path is None:
        return 0
    if WORKER_FLAG in argv:
        return _worker(db_path)
    return supervise([sys.executable, str(Path(__file__).resolve()), WORKER_FLAG])


def _register_config_package() -> None:
    """Make ``config.*`` importable without running ``config/__init__.py``."""
    here = Path(__file__).resolve().parent
    # Run as a file, Python puts config/ itself first on sys.path, where
    # config/celery.py would shadow the real ``celery``. Use src/ instead.
    sys.path[0] = str(here.parent)
    spec = importlib.machinery.ModuleSpec("config", None, is_package=True)
    spec.submodule_search_locations = [str(here)]
    sys.modules.setdefault("config", importlib.util.module_from_spec(spec))


if __name__ == "__main__":
    _register_config_package()
    sys.exit(main(sys.argv[1:]))
