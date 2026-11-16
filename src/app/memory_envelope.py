"""Cheap, always-on attribution for Floppy's largest memory excursions.

Floppy's normal footprint is small; what is not yet bounded is its *high
water*. A container that settles under 800 MiB is not a "<1 GB application"
if a routine backup or one media-list request drives its cgroup to 5 GB, and
until now the only evidence of such an excursion was a Portainer graph that
had to be correlated against timestamps by hand.

This module makes an excursion attributable. It samples two envelopes at
request and task boundaries:

* the **application resident envelope** -- this process's RSS and its
  high-water RSS (``VmHWM``), which never falls and so records the peak even
  when the allocation has already been freed;
* the **operational cgroup envelope** -- everything charged to the container,
  including filesystem page cache, which is what separates "Python grew" from
  "a 2 GiB file was written through the page cache".

Both are read from ``/proc`` and cgroup v2 files -- no ``smaps`` walk, no PSS.
PSS costs a full VMA traversal per sample and stays where it belongs, in the
diagnostic sampler (``scripts/container_memory_sample.py``). What is here is
cheap enough to leave enabled in production, which is the point: the sample
that explains a 5 GB spike is the one that was already running when it
happened.

Nothing is logged for an ordinary request. A structured ``memory_high_water``
event is emitted only when a boundary crosses one of the thresholds in
``settings`` -- see ``_high_water_reasons``.
"""

from __future__ import annotations

import contextvars
import logging
import os
import re
import time
from contextlib import ExitStack
from dataclasses import dataclass

from django.conf import settings
from django.db import connections

from app import db_diagnostics, request_timing
from config.runtime_profile import anon_resident_bytes_from_statm

logger = logging.getLogger(__name__)

_PROC_SELF_STATM = "/proc/self/statm"
_PROC_SELF_STATUS = "/proc/self/status"
_CGROUP_V2_ROOT = "/sys/fs/cgroup"

try:
    _PAGE_SIZE = os.sysconf("SC_PAGE_SIZE")
except (AttributeError, OSError, ValueError):  # pragma: no cover - non-POSIX
    _PAGE_SIZE = 4096

# memory.stat keys worth carrying. "kernel" is a newer-kernel roll-up and is
# simply absent on older ones; an absent value stays None rather than being
# reconstructed from slab+sock+..., because a reconstructed total that is
# silently missing a component is worse than an honest unknown.
_CGROUP_STAT_KEYS = ("anon", "file", "kernel")

# Route parameters whose *values* must never reach a log line. A password
# reset key, a session id or a signed token all travel in the URL path, so
# redacting the query string alone is not enough.
_SENSITIVE_ROUTE_PARAMS = frozenset(
    {
        "code",
        "hash",
        "key",
        "password",
        "secret",
        "sid",
        "sig",
        "signature",
        "token",
        "uidb36",
        "uidb64",
    },
)

_REDACTED = "<redacted>"

# Django spells a route parameter "<name>" or "<converter:name>".
_ROUTE_PARAM = re.compile(r"<(?:[^<>:]+:)?([^<>:]+)>")


def _read_text(path: str) -> str | None:
    """Return a proc/cgroup file's contents, or None when it is unreadable.

    Every filesystem read in this module goes through here so tests can
    inject a fake ``/proc`` without building one on disk, and so that a host
    without cgroup v2 -- or with the controller absent, as on a plain CI
    runner -- degrades to unknowns instead of raising into a request.
    """
    try:
        with open(path) as handle:  # noqa: PTH123 - hot path, no Path objects
            return handle.read()
    except (OSError, ValueError):
        return None


@dataclass(frozen=True)
class MemorySample:
    """One boundary's view of both envelopes. Every field may be None."""

    rss_bytes: int | None = None
    # The anonymous part of rss_bytes: what recycling the process returns.
    # The rest is mostly the memory-mapped database (page cache).
    rss_anon_bytes: int | None = None
    hwm_bytes: int | None = None
    cgroup_current_bytes: int | None = None
    cgroup_anon_bytes: int | None = None
    cgroup_file_bytes: int | None = None
    cgroup_kernel_bytes: int | None = None
    cgroup_peak_bytes: int | None = None
    captured_at: float = 0.0


def _process_rss_bytes() -> int | None:
    """Return live RSS from statm.

    statm is the cheap one: a single short line the kernel formats from
    counters it already keeps. ``ru_maxrss`` would be cheaper still but is a
    peak that never falls, and ``/proc/self/status`` costs a full formatting
    pass -- it is read once more below, for the peak we actually want.
    """
    return _rss_from_statm(_read_text(_PROC_SELF_STATM))


def _rss_from_statm(contents: str | None) -> int | None:
    """Return the resident field of a statm line, in bytes."""
    if not contents:
        return None
    fields = contents.split()
    if len(fields) < 2:  # noqa: PLR2004 - "has a resident field at all"
        return None
    try:
        return int(fields[1]) * _PAGE_SIZE
    except ValueError:
        return None


def _process_hwm_bytes() -> int | None:
    """Return VmHWM, the peak RSS this process has ever held.

    This is what makes a freed excursion visible. A request that builds a
    1.5 GiB object graph and releases it before returning leaves RSS almost
    unchanged at both boundaries; VmHWM keeps the mark.
    """
    contents = _read_text(_PROC_SELF_STATUS)
    if not contents:
        return None
    for line in contents.splitlines():
        if line.startswith("VmHWM:"):
            fields = line.split()
            if len(fields) < 2:  # noqa: PLR2004 - "VmHWM: <number> kB"
                return None
            try:
                return int(fields[1]) * 1024
            except ValueError:
                return None
    return None


def _cgroup_int(name: str) -> int | None:
    """Return a single-value cgroup v2 file as an int, or None."""
    contents = _read_text(f"{_CGROUP_V2_ROOT}/{name}")
    if not contents:
        return None
    try:
        return int(contents.strip())
    except ValueError:
        # memory.max reads "max"; the single-value files here should not, but
        # an unparsable value is an unknown, never a zero.
        return None


def _cgroup_stat() -> dict[str, int]:
    """Return the memory.stat keys this module reports, skipping the rest.

    memory.stat is ~50 lines. Parsing all of them into a dict on every
    boundary is wasted work, so only the handful of keys asked for are kept.
    """
    contents = _read_text(f"{_CGROUP_V2_ROOT}/memory.stat")
    if not contents:
        return {}
    wanted = {}
    for line in contents.splitlines():
        key, _, value = line.partition(" ")
        if key in _CGROUP_STAT_KEYS:
            try:
                wanted[key] = int(value)
            except ValueError:
                continue
            if len(wanted) == len(_CGROUP_STAT_KEYS):
                break
    return wanted


def sample_memory() -> MemorySample:
    """Take one cheap sample of both envelopes.

    Never raises. Every probe independently degrades to None, so a cgroup v1
    host still gets process RSS and VmHWM, and a host with no ``/proc`` at all
    still gets a usable (entirely unknown) sample rather than a 500.
    """
    stat = _cgroup_stat()
    statm = _read_text(_PROC_SELF_STATM)
    return MemorySample(
        rss_bytes=_rss_from_statm(statm),
        rss_anon_bytes=anon_resident_bytes_from_statm(statm),
        hwm_bytes=_process_hwm_bytes(),
        cgroup_current_bytes=_cgroup_int("memory.current"),
        cgroup_anon_bytes=stat.get("anon"),
        cgroup_file_bytes=stat.get("file"),
        cgroup_kernel_bytes=stat.get("kernel"),
        cgroup_peak_bytes=_cgroup_int("memory.peak"),
        captured_at=time.monotonic(),
    )


def _delta(after: int | None, before: int | None) -> int | None:
    """Return after-before, or None when either end is unknown."""
    if after is None or before is None:
        return None
    return after - before


def _exceeds(value: int | None, threshold: int) -> bool:
    """Return whether a known delta crossed a positive threshold."""
    return threshold > 0 and value is not None and value >= threshold


def process_role() -> str:
    """Return this process's role, reusing the rate limiter's definition.

    Imported lazily: ``app.providers.services`` is a heavy module and the
    interactive worker deliberately does not import the provider tree at
    startup. A role string is not worth pulling it in early.
    """
    from app.providers.services import get_process_role

    return get_process_role()


def role_ceiling_bytes(role: str) -> int | None:
    """Return the size at which this role's process is recycled, if any.

    Both ceilings already exist; this only reads them, so an event can say
    "this task ended at 94% of the ceiling that will retire it" rather than
    leaving the reader to look the number up. Returns None where the role has
    no ceiling, or where it is disabled.
    """
    if role == "web":
        ceiling = getattr(settings, "GUNICORN_MAX_WORKER_MEMORY_BYTES", None)
        return ceiling or None
    per_child_kib = getattr(settings, "CELERY_WORKER_MAX_MEMORY_PER_CHILD", None)
    if not per_child_kib:
        return None
    return int(per_child_kib) * 1024


def _high_water_reasons(before: MemorySample, after: MemorySample, duration_ms: float):
    """Return the threshold names this boundary crossed, in report order.

    An empty tuple means "ordinary boundary, log nothing". Each reason is an
    independent signal, and they are deliberately not collapsed: "slow" alone
    and "cgroup_file" alone describe very different events, and a boundary
    that trips both is the one worth reading first.
    """
    reasons = []
    if duration_ms >= settings.MEMORY_HIGH_WATER_DURATION_MS > 0:
        reasons.append("duration")
    if _exceeds(
        _delta(after.rss_bytes, before.rss_bytes),
        settings.MEMORY_HIGH_WATER_RSS_DELTA_BYTES,
    ):
        reasons.append("rss_growth")
    if _exceeds(
        _delta(after.hwm_bytes, before.hwm_bytes),
        settings.MEMORY_HIGH_WATER_HWM_DELTA_BYTES,
    ):
        reasons.append("peak_rss")
    if _exceeds(
        _delta(after.cgroup_current_bytes, before.cgroup_current_bytes),
        settings.MEMORY_HIGH_WATER_CGROUP_DELTA_BYTES,
    ):
        reasons.append("cgroup_growth")
    if _exceeds(
        _delta(after.cgroup_file_bytes, before.cgroup_file_bytes),
        settings.MEMORY_HIGH_WATER_CGROUP_FILE_DELTA_BYTES,
    ):
        reasons.append("page_cache_growth")
    return tuple(reasons)


def _near_ceiling(after: MemorySample, role: str) -> bool:
    """Return whether this process ended close enough to be recycled soon.

    Compared on the anonymous size, as the recyclers themselves now are
    (config.gunicorn and config.celery): mapped database pages do not count.
    """
    ceiling = role_ceiling_bytes(role)
    size = after.rss_anon_bytes if after.rss_anon_bytes is not None else after.rss_bytes
    if not ceiling or size is None:
        return False
    ratio = settings.MEMORY_HIGH_WATER_CEILING_RATIO
    return ratio > 0 and size >= ceiling * ratio


def _format(value) -> str:
    """Render a possibly-unknown number for the log line."""
    return "unknown" if value is None else str(value)


def report_boundary(
    *,
    kind: str,
    name: str,
    before: MemorySample,
    after: MemorySample,
    duration_ms: float,
    extra: dict | None = None,
) -> tuple[str, ...]:
    """Emit a memory_high_water event when this boundary was noteworthy.

    Returns the reasons it fired, so callers and tests can see the decision
    without parsing a log line. An empty tuple means nothing was logged.
    """
    role = process_role()
    reasons = _high_water_reasons(before, after, duration_ms)
    if _near_ceiling(after, role):
        reasons = (*reasons, "near_recycle_ceiling")
    if not reasons:
        return ()

    fields = {
        "kind": kind,
        "name": name,
        "pid": os.getpid(),
        "role": role,
        "reasons": ",".join(reasons),
        "duration_ms": f"{duration_ms:.0f}",
        "rss_before": _format(before.rss_bytes),
        "rss_after": _format(after.rss_bytes),
        "rss_delta": _format(_delta(after.rss_bytes, before.rss_bytes)),
        "rss_anon_before": _format(before.rss_anon_bytes),
        "rss_anon_after": _format(after.rss_anon_bytes),
        "hwm_before": _format(before.hwm_bytes),
        "hwm_after": _format(after.hwm_bytes),
        "cgroup_before": _format(before.cgroup_current_bytes),
        "cgroup_after": _format(after.cgroup_current_bytes),
        "cgroup_delta": _format(
            _delta(after.cgroup_current_bytes, before.cgroup_current_bytes),
        ),
        "anon_before": _format(before.cgroup_anon_bytes),
        "anon_after": _format(after.cgroup_anon_bytes),
        "file_before": _format(before.cgroup_file_bytes),
        "file_after": _format(after.cgroup_file_bytes),
        "kernel_after": _format(after.cgroup_kernel_bytes),
        "cgroup_peak": _format(after.cgroup_peak_bytes),
        "ceiling": _format(role_ceiling_bytes(role)),
    }
    if extra:
        fields.update({key: _format(value) for key, value in extra.items()})
    logger.info(
        "memory_high_water %s",
        " ".join(f"{key}={value}" for key, value in fields.items()),
    )
    return reasons


def enabled() -> bool:
    """Return whether boundary instrumentation should run at all."""
    return bool(getattr(settings, "MEMORY_HIGH_WATER_ENABLED", False))


def redacted_route(request) -> str:
    """Return a loggable name for a request: no query string, no secrets.

    The resolved URL pattern is the safe skeleton, but on its own it loses the
    part that matters -- ``medialist/<str:media_type>`` does not say *movie*,
    and "which list blew up" is exactly the question these events exist to
    answer. So the captured parameters are substituted back in, except for the
    ones that carry credentials in the path (a password-reset key, a signed
    token). Those are redacted by name.

    Falls back to the route pattern, then to the view name, then to a constant
    -- never to ``request.get_full_path()``, which carries the query string.
    """
    match = getattr(request, "resolver_match", None)
    if match is None:
        return "<unresolved>"
    route = getattr(match, "route", "") or ""
    if not route:
        return getattr(match, "view_name", None) or "<unnamed>"
    kwargs = match.kwargs or {}

    def substitute(found):
        name = found.group(1)
        if name.lower() in _SENSITIVE_ROUTE_PARAMS:
            return _REDACTED
        if name in kwargs:
            return str(kwargs[name])
        # A parameter the resolver did not capture by that name (an unnamed
        # re_path group) keeps its placeholder rather than guessing.
        return found.group(0)

    rendered = _ROUTE_PARAM.sub(substitute, route)
    return rendered if rendered.startswith("/") else f"/{rendered}"


class MemoryHighWaterMiddleware:
    """Sample both envelopes around each request, reporting only excursions."""

    def __init__(self, get_response):
        """Store the next handler and resolve the on/off switch once."""
        self.get_response = get_response
        self.enabled = enabled()

    def __call__(self, request):
        """Bracket the response with two samples, reporting when noteworthy."""
        if not self.enabled:
            return self.get_response(request)
        before = sample_memory()
        started = time.perf_counter()
        response = self.get_response(request)
        duration_ms = (time.perf_counter() - started) * 1000
        try:
            report_boundary(
                kind="request",
                name=redacted_route(request),
                before=before,
                after=sample_memory(),
                duration_ms=duration_ms,
                extra={"method": request.method, "status": response.status_code},
            )
        except Exception:  # instrumentation must never fail a request
            logger.exception("memory_high_water reporting failed")
        return response


# Celery boundaries. The samples are keyed by task id rather than held on the
# task object: a prefork child runs one task at a time, but eager mode and
# nested calls in tests do not, and a dict keyed by id is correct in both.
_task_samples: dict[str, tuple[MemorySample, float]] = {}

# A task whose postrun signal never fires (revoked, hard time limit, killed
# child) would otherwise leak its entry forever. The map is bounded rather
# than swept: instrumentation must not grow the memory it is measuring.
_MAX_TRACKED_TASKS = 64


@dataclass
class _TaskTiming:
    task_id: str
    tally: dict
    token: contextvars.Token
    wrappers: ExitStack
    started: float
    cpu_started: float
    rss_started: int | None = None
    queries: int = 0


_task_timings = contextvars.ContextVar("task_performance_timings", default=())


def task_timing_started(task_id: str) -> None:
    """Open context-local I/O accounting; never record task arguments."""
    if not settings.PERF_LOG_ENABLED:
        return
    active = _task_timings.get()
    if len(active) >= _MAX_TRACKED_TASKS:
        return
    request_timing.install_boundaries()
    tally, token = request_timing.begin()
    sample = _TaskTiming(
        task_id, tally, token, ExitStack(), time.perf_counter(), time.thread_time(),
        rss_started=_process_rss_bytes(),
    )

    def count_query(execute, sql, params, many, context):
        # Eager tasks can nest. Only the innermost task owns a query.
        if not _task_timings.get() or _task_timings.get()[-1] is not sample:
            return execute(sql, params, many, context)
        sample.queries += 1
        with request_timing.boundary("db"):
            return db_diagnostics.track_query(execute, sql, params, many, context)

    try:
        for connection in connections.all():
            sample.wrappers.enter_context(connection.execute_wrapper(count_query))
    except Exception:
        sample.wrappers.close()
        request_timing.end(token)
        raise
    _task_timings.set((*active, sample))


def task_timing_finished(task_id: str, task_name: str, state: str | None = None) -> None:
    """Close accounting before emitting one summary for a slow task."""
    active = _task_timings.get()
    if not active or active[-1].task_id != task_id:
        return
    sample = active[-1]
    duration_ms = (time.perf_counter() - sample.started) * 1000
    cpu_ms = (time.thread_time() - sample.cpu_started) * 1000
    try:
        sample.wrappers.close()
    finally:
        request_timing.end(sample.token)
        _task_timings.set(active[:-1])
    if duration_ms < settings.PERF_LOG_SLOW_TASK_MS:
        return
    spans = sample.tally["boundaries"]
    fields = " ".join(
        f"{name}_ms={spans.get(name, 0.0) * 1000:.1f} "
        f"{name}_calls={sample.tally['boundary_calls'].get(name, 0)}"
        for name in sorted({"db", "provider", "cache", "broker", "db_connect", "render", *spans})
    )
    logger.info(
        "slow_task name=%s task_id=%s state=%s duration_ms=%.1f cpu_ms=%.1f "
        "queries=%s provider_calls=%s unclassified_ms=%.1f "
        "rss_delta_bytes=%s process_hwm_bytes=%s %s",
        task_name,
        task_id,
        state or "unknown",
        duration_ms,
        cpu_ms,
        sample.queries,
        sample.tally["calls"],
        max(0.0, duration_ms - sum(spans.values()) * 1000),
        _delta(_process_rss_bytes(), sample.rss_started),
        _process_hwm_bytes(),
        fields,
    )


def task_started(task_id: str) -> None:
    """Record the opening sample for a task."""
    if len(_task_samples) >= _MAX_TRACKED_TASKS:
        _task_samples.clear()
    _task_samples[task_id] = (sample_memory(), time.perf_counter())


def task_finished(task_id: str, task_name: str, state: str | None = None) -> None:
    """Report a task's boundary, if its opening sample is still held."""
    opening = _task_samples.pop(task_id, None)
    if opening is None:
        return
    before, started = opening
    report_boundary(
        kind="task",
        name=task_name,
        before=before,
        after=sample_memory(),
        duration_ms=(time.perf_counter() - started) * 1000,
        # The task id is an opaque uuid, so it is safe to log and is the only
        # way to tie this event to the task's own log lines. Task *arguments*
        # are never logged: they carry user ids, search terms and credentials.
        extra={"task_id": task_id, "state": state or "unknown"},
    )


def connect_celery_signals() -> None:
    """Wire the task boundaries up, once, if instrumentation is enabled."""
    if not enabled() and not settings.PERF_LOG_ENABLED:
        return
    from celery.signals import task_postrun, task_prerun

    def _prerun(task_id=None, **_kwargs):
        if task_id:
            try:
                task_timing_started(task_id)
                if enabled():
                    task_started(task_id)
            except Exception:  # instrumentation must never fail a task
                logger.exception("task boundary instrumentation failed")

    def _postrun(task_id=None, task=None, state=None, **_kwargs):
        if not task_id:
            return
        try:
            task_timing_finished(task_id, getattr(task, "name", "<unknown>"), state)
        except Exception:  # never fail a task on instrumentation
            logger.exception("task performance reporting failed")
        if enabled():
            try:
                task_finished(task_id, getattr(task, "name", "<unknown>"), state)
            except Exception:  # never fail a task on instrumentation
                logger.exception("memory_high_water task reporting failed")

    # weak=False: the handlers are closures with no other reference, so the
    # default weak connection would let them be collected immediately.
    task_prerun.connect(_prerun, weak=False, dispatch_uid="memory_high_water_prerun")
    task_postrun.connect(_postrun, weak=False, dispatch_uid="memory_high_water_postrun")


__all__ = [
    "MemoryHighWaterMiddleware",
    "MemorySample",
    "connect_celery_signals",
    "report_boundary",
    "role_ceiling_bytes",
    "sample_memory",
]
