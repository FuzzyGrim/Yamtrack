# Dev container

Opens Yamtrack in a Python container with dependencies and a Redis sidecar.

1. Open the Dev Container in your VS Code (or other supported IDE)
   1. Run **Dev Containers: Reopen in Container** or
   2. *(Preferred)* Run **Clone Repository in Container Volume** for faster file watching on Windows/macOS.
2. `postCreateCommand` installs uv and the Tailwind CLI, runs `uv sync` and applies migrations.
3. VS Code starts web, Celery and Tailwind automatically via the **Yamtrack: start dev stack** task (`.vscode/tasks.json`, runs on folder open). Restart it with **Tasks: Restart Running Task**. Elsewhere, run:

   ```bash
   uvx honcho -e /dev/null start
   ```

4. Open <http://localhost:8000> (port 8000 is forwarded).

The virtualenv lives in a named volume at `/home/vscode/.venv`, and the SQLite database is `src/db/db.sqlite3`.
