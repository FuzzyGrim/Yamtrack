# Dev container

Opens Yamtrack in a Python 3.12 container with uv, Node (for the Tailwind CLI) and a Redis sidecar.

1. In VS Code run **Dev Containers: Reopen in Container**, or **Clone Repository in Container Volume** for faster file watching on Windows/macOS.
2. `postCreateCommand` installs uv and the Tailwind CLI, runs `uv sync` and applies migrations.
3. Start web, Celery and Tailwind together:

   ```bash
   uvx honcho -e /dev/null start
   ```

4. Open <http://localhost:8000> (port 8000 is forwarded).

The virtualenv lives in a named volume at `/home/vscode/.venv`, and the SQLite database is `src/db/db.sqlite3`.
