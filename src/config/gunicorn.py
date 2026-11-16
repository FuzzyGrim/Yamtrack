import gc
import importlib
import logging.handlers
import time

from config.runtime_profile import (
    PROFILE,
    by_tier,
    current_anon_rss_bytes,
    gunicorn_max_worker_memory_bytes,
    gunicorn_threads,
    web_concurrency,
    web_concurrency_warning,
)

bind = "localhost:8001"
preload_app = True

# Threaded workers so one slow request can't stall the whole UI.  The
# container also runs nginx, up to three celery workers, and beat, so keep the
# process count low and rely on threads for I/O-bound concurrency.
worker_class = "gthread"

# Sized from the host rather than fixed. One threaded, preloaded worker keeps
# normal-host idle memory down; each extra worker duplicates private Django
# state despite copy-on-write. WEB_CONCURRENCY and GUNICORN_THREADS still win
# when set - see config/runtime_profile.py.
workers = web_concurrency()
threads = gunicorn_threads()

# gthread accepts up to worker_connections sockets whatever the thread count,
# and hands each to a private queue in front of that worker's pool (gunicorn
# gthread.accept/enqueue_req). At the default of 1000 a connection accepted by
# a worker whose threads are all busy waits there, invisible and unreachable,
# even while the other worker idles -- so a trivial request such as
# /serviceworker.js stalls behind slow ones until nginx gives up on it. Capping
# accepted connections at the thread count leaves the surplus in the kernel
# backlog, where whichever worker frees a thread first takes it. nginx speaks
# HTTP/1.0 to this upstream, so no keepalive slots are lost; gunicorn's
# "No keepalived connections can be handled" boot warning is expected.
worker_connections = threads

# Recycle workers more aggressively on small hosts: RSS only creeps upward
# within a worker's life, so a lower ceiling caps the steady-state footprint.
max_requests = by_tier(200, 300, 500)
max_requests_jitter = 10
timeout = by_tier(120, 200, 200)

# A request ceiling alone does not bound a worker. Floppy's expensive pages --
# the talent fragment, a details page, a large media list -- cost hundreds of
# times an ordinary request, so a worker can grow for hours without reaching
# the count. Production showed one worker at 567 MiB of private memory after
# three hours, never recycled, because real traffic had not yet served 500
# requests. Celery already bounds its children by RSS; this is the same bound
# for the web worker, checked after the response so no request is ever failed
# by it.
# Sized from a measured fresh worker, not a guess: with preload_app one starts
# at 109-136 MiB RSS, because RSS counts the shared application image it was
# forked from. A ceiling near that retires workers as fast as they start -- a
# 120 MiB ceiling produced 30 workers in six minutes, none older than 16s.
max_worker_memory_bytes = gunicorn_max_worker_memory_bytes()
# However the ceiling is set, a worker must earn its keep before it can be
# retired for size. Without this a misconfigured ceiling below the starting
# size is a restart loop -- every worker crosses it on its first request -- and
# the symptom (workers churning, latency spikes) points nowhere near the cause.
#
# The floor is a lifetime, not only a request count. A count alone is no
# protection under load: at four concurrent clients a worker reaches 50
# requests in about two seconds, and a 120 MiB ceiling still produced 42
# workers in thirteen minutes. A minute of life bounds the respawn rate
# whatever the request rate is.
MINIMUM_REQUESTS_BEFORE_RETIREMENT = 50
MINIMUM_SECONDS_BEFORE_RETIREMENT = 60


def _worker_rss_bytes():
    """Return this worker's anonymous resident size, or None where /proc is absent.

    Not plain RSS. SQLite memory-maps the database, and the mapped pages count
    as resident: one cold Home row put ~120 MiB of the database file into a
    worker, and RSS stays there. Recycling for that returns nothing (it is
    page cache, and the replacement maps the same file), so the ceiling
    measures only what a recycle gives back. See docs/architecture/memory-envelope.md.
    """
    return current_anon_rss_bytes()


def _record_crash_markers(server):
    """Leave evidence on the log volume for the next start (see run_state).

    Gunicorn's own error log (worker timeouts, "Worker was sent SIGKILL!
    Perhaps out of memory?") does not propagate to the root logger, so it never
    reached floppy.log. Attach the root's file handler to it. Never fatal.
    """
    try:
        from config import run_state

        for handler in logging.getLogger().handlers:
            if isinstance(handler, logging.handlers.RotatingFileHandler):
                server.log.error_log.addHandler(handler)
        run_state.enable_fault_log()
        run_state.start()
        _record_stop_signals(server)
    except Exception:  # diagnostics must never stop the server
        server.log.exception("[gunicorn] could not record crash markers")


def _load_url_conf(server):
    """Import the URLconf, and every view module behind it, in the master.

    Django resolves ROOT_URLCONF on the first request, not in django.setup(),
    so with preload_app every forked worker paid for it: about 1.5 s of CPU on
    a laptop, and on a loaded NAS 1 to 16 s for the first request after each
    recycle -- once, at worst, a health check slower than Docker's 15 s
    timeout. Importing here lets the fork inherit it, and the freeze below
    keeps those pages shared. Never fatal: a worker would still import it
    lazily and report the real error.
    """
    try:
        from django.urls import get_resolver

        resolver = get_resolver()
        resolver.url_patterns  # noqa: B018  # the property imports the URLconf
        # Building the reverse table and importing what the save views import
        # inside the function are the rest of a first write's cold cost (the
        # first save after each recycle took seconds on a slow disk).
        resolver._populate()
        for module in (
            "app.tasks",
            "lists.tasks",
            "app.services.metadata_resolution",
            "app.services.bulk_episode_tracking",
            "app.statistics_sync",
        ):
            importlib.import_module(module)
    except Exception:
        server.log.exception("[gunicorn] could not preload the URLconf")


def _noting_stop(original, name):
    """Wrap an arbiter signal handler so the signal is recorded first."""

    def handler():
        from config import run_state

        try:
            run_state.note_stop_signal(name)
        except Exception:  # diagnostics must never stop the server
            logging.getLogger(__name__).debug("run-state stop note failed")
        original()

    return handler


def _record_stop_signals(server):
    """Note which signal stops the master, then let gunicorn handle it as usual.

    gunicorn's ``on_exit`` hook is not told why it runs. The arbiter looks its
    ``handle_<signal>`` method up on the instance when a signal arrives, so
    wrapping those three is enough.
    """
    for signal_name in ("term", "int", "quit"):
        original = getattr(server, f"handle_{signal_name}")
        setattr(
            server,
            f"handle_{signal_name}",
            _noting_stop(original, f"SIG{signal_name.upper()}"),
        )


def on_exit(server):
    """Mark a deliberate stop, so the next start can tell it from a crash."""
    from config import run_state

    run_state.stop()


def when_ready(server):
    """Move the preloaded application out of the garbage collector's reach.

    preload_app imports Django once and forks workers from it, so the whole
    application image starts shared. Collection then un-shares it: a pass
    writes to the header of every object it examines, and a written page stops
    being shared.

    The cost is measured, not theoretical. Production's shared pages fell from
    76 MiB to 30 MiB over three hours, and the master's private memory rose by
    exactly what it lost -- the master never wrote those pages, but once a
    child had written its own copy the master's became exclusively mapped and
    was recounted as private. Every process converged on the same ~30 MiB,
    which is the part nothing ever touched. A local run reproduces it: 36.9
    MiB shared decaying to the same 30.6 MiB floor.

    Freezing moves everything alive now into a generation collection never
    visits, so those pages stay shared. Objects created afterwards are still
    collected normally. Children inherit the frozen state through fork, so
    this runs once here rather than in each of them.
    """
    _record_crash_markers(server)
    _load_url_conf(server)
    gc.collect()
    gc.freeze()
    server.log.info(
        "[gunicorn] froze %s preloaded objects to keep them shared after fork",
        gc.get_freeze_count(),
    )


def post_worker_init(worker):
    """Record when this worker started, for the retirement floor below."""
    worker.floppy_started_at = time.monotonic()


def post_request(worker, req, environ, resp):  # gunicorn's hook signature
    """Retire a worker that has outgrown its ceiling, once its response is sent.

    Marking the worker not-alive lets it finish what it is holding and exit;
    the arbiter forks a replacement, which with preload_app is nearly free.
    """
    if not max_worker_memory_bytes:
        return
    if getattr(worker, "nr", 0) < MINIMUM_REQUESTS_BEFORE_RETIREMENT:
        return
    started_at = getattr(worker, "floppy_started_at", None)
    if (
        started_at is not None
        and time.monotonic() - started_at < MINIMUM_SECONDS_BEFORE_RETIREMENT
    ):
        return
    resident = _worker_rss_bytes()
    if resident is not None and resident > max_worker_memory_bytes:
        worker.alive = False


print(  # noqa: T201  # gunicorn has no logger configured this early
    f"[gunicorn] {PROFILE.describe()} -> workers={workers} threads={threads} "
    f"max_requests={max_requests} timeout={timeout} "
    f"max_worker_memory={max_worker_memory_bytes // (1024 * 1024)}MiB",
)

# Repeated on every restart in `docker logs`, which is the only place an
# override saved in an orchestrator's own template becomes visible.
_override_warning = web_concurrency_warning()
if _override_warning:
    print(f"[gunicorn] {_override_warning}")  # noqa: T201  # see above

# Nginx owns the request log. A second Gunicorn access line duplicates every
# dynamic request and can include the raw query string. Keep Gunicorn errors.
accesslog = None
errorlog = "-"


def pre_fork(server, worker):
    """Close database and Redis pools in the master before workers are forked.

    ``preload_app`` runs ``django.setup()`` (and every ``AppConfig.ready()``)
    once in the master before forking, and ``AppConfig.ready()`` touches the
    Redis cache (startup-task scheduling keys). Psycopg pools own background
    threads and must not be inherited by the preloaded workers (#341); the
    Redis connection likewise must not be inherited, or every forked
    worker/thread shares one raw socket and corrupts each other's commands -
    including session writes, which silently fail to persist (#335).
    """
    from django.core.cache import caches
    from django.db import connections

    connections.close_all()
    for connection in connections.all():
        close_pool = getattr(connection, "close_pool", None)
        if close_pool is not None:
            close_pool()

    for cache in caches.all():
        client = getattr(cache, "client", None)
        do_close_clients = getattr(client, "do_close_clients", None)
        if do_close_clients is not None:
            do_close_clients()


def post_fork(server, worker):
    """Drop database connections inherited from the preloaded master process.

    ``pre_fork`` closes any process-local database/Redis pool before the
    fork; this hook remains as a final guard against inherited database
    connections (issue #335).
    """
    from django.db import connections

    connections.close_all()
