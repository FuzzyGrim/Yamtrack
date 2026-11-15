"""Where Floppy keeps its data, readable without starting Django.

``settings.py`` and the background SQLite verifier
(``config/sqlite_background.py``) both resolve their paths here, so the two can
never disagree about which database file is live. Standard library only.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


def resolve_data_paths(read: Callable, base_dir: Path) -> tuple[Path, Path]:
    """Return ``(FLOPPY_DATA_DIR, FLOPPY_DB_PATH)`` from ``read`` (decouple's config).

    An empty override counts as unset, as it always has.
    """
    data_dir_override = read("FLOPPY_DATA_DIR", default=None)
    data_dir = Path(data_dir_override) if data_dir_override else base_dir / "db"
    database_override = read("FLOPPY_DB_PATH", default=None)
    database = Path(database_override) if database_override else data_dir / "db.sqlite3"
    return data_dir, database
