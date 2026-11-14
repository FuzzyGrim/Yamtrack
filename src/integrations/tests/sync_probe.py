"""Count what one scheduled sync run writes and what follow-up work it queues.

Used by the recurring-sync tests to prove that a run against an upstream that
has not changed writes nothing and starts no History, Statistics or Discover
work. Everything is recorded, nothing is sent: queued Celery tasks are captured
instead of run.
"""

import re
from collections import Counter
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from unittest.mock import patch

from celery.app.task import Task
from django.core.cache import caches
from django.db import connection
from django.db.models.signals import post_delete, post_save
from django.test.utils import CaptureQueriesContext

from app import history_cache_lifecycle, statistics_sync
from app.discover import tab_cache
from events.tasks import reload_calendar

_WRITE = re.compile(r'^\s*(INSERT INTO|UPDATE|DELETE FROM)\s+"?(\w+)"?', re.IGNORECASE)
_CACHE_METHODS = (
    "get",
    "set",
    "add",
    "delete",
    "get_many",
    "set_many",
    "delete_many",
    "incr",
    "touch",
    "delete_pattern",
)
# Bookkeeping every run writes on purpose: the run record, its progress, and the
# account's own "last synced" and health fields that Integrations shows.
BOOKKEEPING_TABLES = frozenset(
    {
        "integrations_importrun",
        "app_statisticssyncstate",
        "integrations_radarrinstance",
        "integrations_sonarrinstance",
        "integrations_mylarinstance",
        "integrations_kapowarrinstance",
        "integrations_audiobookshelfaccount",
        "integrations_kavitaaccount",
        "integrations_komgaaccount",
        "integrations_pocketcastsaccount",
        "integrations_gpodderaccount",
        "integrations_plexaccount",
        "django_celery_results_taskresult",
    },
)
_FOLLOW_UP_SPIES = (
    (tab_cache, "bump_activity_version", "discover_version_bumps"),
    (history_cache_lifecycle, "invalidate_history_days", "history_invalidations"),
    (history_cache_lifecycle, "invalidate_history_cache", "history_invalidations"),
    (history_cache_lifecycle, "schedule_history_refresh", "history_refreshes"),
    (statistics_sync, "mark_days", "statistics_marks"),
    (statistics_sync, "mark_aggregate", "statistics_marks"),
)


@dataclass
class RunReport:
    """What one run did, by kind."""

    writes: Counter = field(default_factory=Counter)
    signals: Counter = field(default_factory=Counter)
    tasks: Counter = field(default_factory=Counter)
    cache_ops: Counter = field(default_factory=Counter)
    follow_ups: Counter = field(default_factory=Counter)
    statements: int = 0
    data_write_sql: list = field(default_factory=list)
    statistics_generation_moved: bool = False

    @property
    def data_writes(self) -> Counter:
        """Writes outside the run's own bookkeeping tables."""
        return Counter(
            {
                key: count
                for key, count in self.writes.items()
                if key.split(" ", 1)[1] not in BOOKKEEPING_TABLES
            },
        )

    @property
    def side_effects(self) -> Counter:
        """Signals, tasks and follow-up marks a no-change run must not cause."""
        effects = Counter(self.follow_ups)
        effects.update({f"task:{name}": n for name, n in self.tasks.items()})
        if self.statistics_generation_moved:
            effects["statistics_generation_moved"] = 1
        return effects

    def as_dict(self) -> dict:
        """Return a JSON-friendly summary."""
        return {
            "statements": self.statements,
            "writes": dict(sorted(self.writes.items())),
            "data_writes": dict(sorted(self.data_writes.items())),
            "signals": dict(sorted(self.signals.items())),
            "tasks": dict(sorted(self.tasks.items())),
            "cache_ops": sum(self.cache_ops.values()),
            "follow_ups": dict(sorted(self.follow_ups.items())),
            "statistics_generation_moved": self.statistics_generation_moved,
            "data_write_sql": self.data_write_sql,
        }


def _spy(report, label, real):
    def wrapper(*args, **kwargs):
        # Every import marks this up front for a worker killed mid-run, and a
        # verified no-op run restores it (statistics_sync.coalesce_import_changes).
        if kwargs.get("reason") != "media_import_started":
            report.follow_ups[label] += 1
        return real(*args, **kwargs)

    return wrapper


@contextmanager
def probe_run(user_id, test_case=None):
    """Record one run's writes, signals, queued tasks and follow-up marks.

    ``test_case`` (a Django ``TestCase``) runs on-commit callbacks at the end,
    so marks a write schedules after commit are counted too.
    """
    report = RunReport()
    generation_before = statistics_sync.current_generation(user_id)

    def count_signal(sender, **_kwargs):
        report.signals[sender.__name__] += 1

    def record_task(task, *_args, **_kwargs):
        report.tasks[task.name] += 1

    backend = type(caches["default"])

    def cache_counter(name, real):
        def wrapper(self, *args, **kwargs):
            report.cache_ops[name] += 1
            return real(self, *args, **kwargs)

        return wrapper

    post_save.connect(count_signal, weak=False, dispatch_uid="sync_probe_save")
    post_delete.connect(count_signal, weak=False, dispatch_uid="sync_probe_delete")
    try:
        with ExitStack() as stack:
            queries = stack.enter_context(CaptureQueriesContext(connection))
            if test_case is not None:
                stack.enter_context(test_case.captureOnCommitCallbacks(execute=True))
            stack.enter_context(
                patch.object(Task, "apply_async", autospec=True, side_effect=record_task),
            )
            # Test settings swap this task's ``delay`` for a stub (events/apps.py).
            stack.enter_context(
                patch.object(
                    reload_calendar,
                    "delay",
                    side_effect=lambda *_a, **_k: report.tasks.update(
                        [reload_calendar.name],
                    ),
                ),
            )
            for name in _CACHE_METHODS:
                real = getattr(backend, name, None)
                if real is not None:
                    stack.enter_context(
                        patch.object(backend, name, cache_counter(name, real)),
                    )
            for module, name, label in _FOLLOW_UP_SPIES:
                stack.enter_context(
                    patch.object(module, name, _spy(report, label, getattr(module, name))),
                )
            yield report
    finally:
        post_save.disconnect(dispatch_uid="sync_probe_save")
        post_delete.disconnect(dispatch_uid="sync_probe_delete")

    report.statements = len(queries.captured_queries)
    for query in queries.captured_queries:
        match = _WRITE.match(query["sql"])
        if match:
            verb = match.group(1).split()[0].upper()
            table = match.group(2).lower()
            report.writes[f"{verb} {table}"] += 1
            if table not in BOOKKEEPING_TABLES:
                report.data_write_sql.append(query["sql"][:400])
    report.statistics_generation_moved = (
        statistics_sync.current_generation(user_id) != generation_before
    )
