"""The image must ship compiled bytecode for everything Python imports.

Without it every Python start recompiles its imports from source, and a cold
start could run past the SQLite admission deadline (#1875).
"""

import re
import shlex
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = ROOT / "Dockerfile"


def _final_stage(text):
    """Return the instructions after the last FROM, continuation lines joined."""
    joined = re.sub(r"\\\n\s*", " ", text)
    lines = [line.strip() for line in joined.splitlines()]
    lines = [line for line in lines if line and not line.startswith("#")]
    last_from = max(i for i, line in enumerate(lines) if line.startswith("FROM "))
    return lines[last_from + 1 :]


class DockerfileBytecodeTests(SimpleTestCase):
    """Guard the compile step against reordering and base-image bumps."""

    def setUp(self):
        self.text = DOCKERFILE.read_text()
        self.stage = _final_stage(self.text)
        found = [
            (i, shlex.split(line[len("RUN ") :]))
            for i, line in enumerate(self.stage)
            if line.startswith("RUN ") and "compileall" in line
        ]
        if not found:
            self.fail("The final image stage has no `python -m compileall` step.")
        self.compile_index, self.compile_args = found[-1]

    def test_compiles_stdlib_dependencies_and_app(self):
        python = re.search(r"python:(\d+\.\d+)", self.text).group(1)
        for path in (f"/usr/local/lib/python{python}", "/opt/venv", "/floppy"):
            self.assertIn(path, self.compile_args)

    def test_compile_runs_after_every_python_source_copy(self):
        copies = [
            i
            for i, line in enumerate(self.stage)
            if line.startswith("COPY ")
            and re.search(r"\b(src|mcp_server|/opt/venv)\b", line)
        ]
        self.assertTrue(copies)
        self.assertGreater(self.compile_index, max(copies))

    def test_runtime_still_does_not_write_bytecode(self):
        self.assertIn("ENV PYTHONDONTWRITEBYTECODE=1", self.stage)
