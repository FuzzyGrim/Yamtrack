"""Run each scheduled sync again against the same fake upstream.

The repeat runs see exactly what the first one saw, so they should write no
media, tracking or collection rows and start no History, Statistics or
Discover work. Only the run's own bookkeeping (the run record, the account's
"last synced" and health fields) may change. Where an importer was fixed, a
last run changes the upstream for real and must still write.

Set ``SYNC_PROBE_REPORT`` to a file path to write every measured run as JSON
(the ranked report the performance receipts quote).
"""

import json
import logging
import os
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from celery.app.task import Task
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, tag

from integrations.tests.recurring_sync_scenarios import SCENARIOS, SyncScenario
from integrations.tests.sync_probe import probe_run

_REPORT = {}
_QUIET_LOGGERS = ("app.providers.services", "integrations.imports.plex")


def setUpModule():
    """Silence provider lookups the network guard refuses (they are caught)."""
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.CRITICAL)


def tearDownModule():
    """Restore the logger levels for other modules."""
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.NOTSET)


@tag("slow")
class RecurringSyncNoChangeTests(TestCase):
    """A second run against an unchanged upstream writes nothing."""

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        path = os.environ.get("SYNC_PROBE_REPORT")
        if path and _REPORT:
            with Path(path).open("w", encoding="utf-8") as handle:
                json.dump(_REPORT, handle, indent=2, sort_keys=True)

    def _repeat_runs(self, scenario_class):
        cache.clear()
        user = get_user_model().objects.create_user(
            username=f"probe-{scenario_class.name}",
        )
        scenario = scenario_class()
        scenario.connect(user)
        with ExitStack() as stack:
            for patcher in scenario.patches():
                stack.enter_context(patcher)
            # The first run's queued work is dropped, as if a worker ran it.
            with (
                patch.object(Task, "apply_async", autospec=True),
                self.captureOnCommitCallbacks(execute=True),
            ):
                first = scenario.run(user)
            with probe_run(user.id, self) as second:
                second_result = scenario.run(user)
            with probe_run(user.id, self) as third:
                scenario.run(user)
            changed = changed_result = None
            if type(scenario).change is not SyncScenario.change:
                scenario.change()
                with probe_run(user.id, self) as changed:
                    changed_result = scenario.run(user)
        _REPORT[scenario_class.name] = {
            "first_result": first,
            "second_result": second_result,
            "second_run": second.as_dict(),
            "third_run": third.as_dict(),
            "changed_result": changed_result,
            "changed_run": changed.as_dict() if changed else None,
        }
        return scenario, second, third, changed

    def test_repeat_runs_write_nothing(self):
        for scenario_class in SCENARIOS:
            with self.subTest(importer=scenario_class.name):
                scenario, second, third, changed = self._repeat_runs(scenario_class)
                if changed is not None:
                    # A real upstream change still lands.
                    self.assertNotEqual(dict(changed.data_writes), {})
                self.assertEqual(dict(third.data_writes), {})
                self.assertEqual(dict(third.side_effects), {})
                if scenario.second_run_settles:
                    continue
                self.assertEqual(dict(second.data_writes), {})
                self.assertEqual(dict(second.side_effects), {})
