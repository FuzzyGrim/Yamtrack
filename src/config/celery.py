import gc
import os

from celery import Celery, Task, states
from celery.exceptions import Ignore
from celery.signals import (
    worker_before_create_process,
    worker_process_init,
    worker_ready,
    worker_shutting_down,
)

from config.runtime_profile import current_anon_rss_bytes

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")


class FloppyTask(Task):
    """Base task that refuses to run an import the user cancelled while queued.

    ``cancel_pending_import`` marks the tracked row REVOKED, which survives a
    worker restart that loses Celery's in-memory revoke list. Raising Ignore
    here stops the task before it does any work and leaves that row REVOKED
    (no success or failure signal fires), for every tracked task at once.
    """

    def __call__(self, *args, **kwargs):
        """Skip the task when its tracked row was cancelled, else run it."""
        from django_celery_results.models import TaskResult

        from app.signals import _is_tracked_task

        task_id = self.request.id
        if (
            task_id
            and _is_tracked_task(self.name)
            and TaskResult.objects.filter(
                task_id=task_id,
                status=states.REVOKED,
            ).exists()
        ):
            raise Ignore
        return super().__call__(*args, **kwargs)


app = Celery("floppy", task_cls=FloppyTask)

app.config_from_object("django.conf:settings", namespace="CELERY")

# The dedicated interactive worker only consumes the task modules listed in
# CELERY_IMPORTS. Avoid autodiscovering every background task and importer into
# that long-lived process. Background and combined workers retain the complete
# task registry.
if os.environ.get("FLOPPY_PROCESS_ROLE") != "interactive":
    app.autodiscover_tasks()


@worker_ready.connect
def _enable_fault_log(**_kwargs):
    """Record the traceback of a fatal signal in a worker (see run_state)."""
    from config import run_state

    run_state.enable_fault_log()


@worker_ready.connect
def _warm_database_on_start(**_kwargs):
    """Queue a page-cache warm now instead of waiting for the hourly schedule.

    A restart is when the cache is coldest. The interactive worker skips it:
    the task runs on the background queue and one request is enough.
    """
    if os.environ.get("FLOPPY_PROCESS_ROLE") == "interactive":
        return
    import logging

    try:
        app.send_task("Warm database page cache", ignore_result=True)
    except Exception:  # a start must not fail because the broker is slow
        logging.getLogger(__name__).debug("startup warm not queued", exc_info=True)


@worker_shutting_down.connect
def _log_shutdown(sig=None, how=None, **_kwargs):
    """Record which tasks were running when the worker was told to stop."""
    import logging

    from config import run_state

    try:
        run_state.log_worker_shutdown(sig, how)
    except Exception:  # diagnostics must never block a shutdown
        logging.getLogger(__name__).debug("shutdown note failed", exc_info=True)


_parent_image_frozen = False


@worker_before_create_process.connect
@worker_ready.connect
def _freeze_parent_image(**_kwargs):
    """Freeze the imported image before the first prefork child is created.

    The prefork pool forks its children from this process, so whatever is
    imported here starts shared between them. Collection un-shares it: a pass
    writes to the header of every object it examines, and a written page stops
    being shared.

    Waiting for worker_ready lets the initial children collect an unfrozen
    image first, copying pages that replacement children keep shared. Keep
    worker_ready as a fallback for pools without the before-create signal.

    Freeze only once: replacement forks must not freeze transient parent
    state. Objects created after this point are still collected normally.
    """
    global _parent_image_frozen  # noqa: PLW0603 - once per worker parent

    if _parent_image_frozen:
        return
    gc.collect()
    gc.freeze()
    _parent_image_frozen = True


@worker_process_init.connect
def _freeze_child_image(**_kwargs):
    """Do the same in a pool child, for the pages it inherited.

    Freezing in the parent stops the parent from un-sharing them; this stops
    the child from doing it from its own side. Both are needed, and this also
    covers the children forked before the parent signalled ready.
    """
    gc.collect()
    gc.freeze()


def _child_anon_rss_kib():
    """Return this child's anonymous resident size in KiB, as billiard expects.

    billiard's own reading is ``ru_maxrss``: the child's lifetime *peak*, file
    pages included. SQLite memory-maps the database, so one task that reads a
    wide table pushes that peak past the ceiling for good, and the child is
    recycled after every later task for page cache it cannot give back. A
    non-positive return makes billiard log "unable to determine memory usage"
    and keep the child, so an unreadable /proc falls back to billiard's figure.
    """
    from billiard import compat

    resident = current_anon_rss_bytes()
    if resident is None:
        return compat.mem_rss()
    return resident // 1024


@worker_process_init.connect
def _measure_child_memory_without_page_cache(**_kwargs):
    """Make CELERY_WORKER_MAX_MEMORY_PER_CHILD compare what a recycle returns.

    The pool loop looks ``mem_rss`` up in ``billiard.pool`` after each task,
    so replacing it there, in the child, is enough.
    """
    import billiard.pool

    billiard.pool.mem_rss = _child_anon_rss_kib
