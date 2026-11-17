"""The code map must not point at files that no longer exist.

Runs scripts/check_code_map.py (loaded by path, as in test_ui_docs): every
repository path and test label docs/architecture/code-map.md names in backticks
exists. If this fails after a move or delete, update the map in the same change.
"""

import contextlib
import importlib.util
import io
from pathlib import Path
from unittest import SkipTest, TestCase

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _ROOT / "scripts" / "check_code_map.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_code_map", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CodeMapTests(TestCase):
    def setUp(self):
        if not _SCRIPT.exists() or not (_ROOT / "docs" / "architecture").exists():
            raise SkipTest("docs/ or scripts/ is not part of this checkout")
        self.check = _load()

    def test_every_path_in_the_map_exists(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = self.check.main()
        self.assertEqual(exit_code, 0, output.getvalue())

    def test_a_missing_path_or_test_label_is_reported(self):
        text = (
            "`src/app/helpers.py` `src/app/no_such_module.py` `src/app/stats_*.py` "
            "`src/app/no_such_*.py` `app.tests.test_library_query` "
            "`app.tests.test_no_such_module` `src/app/tasks_<subject>.py`\n"
            "```bash\nrg -n x src/app/also_missing.py\n```\n"
        )
        self.assertEqual(
            self.check.missing_entries(text),
            [
                "src/app/no_such_module.py",
                "src/app/no_such_*.py",
                "app.tests.test_no_such_module",
            ],
        )
