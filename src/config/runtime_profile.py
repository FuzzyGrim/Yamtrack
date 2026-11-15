"""Detect how much machine Floppy is running on, and size itself accordingly.

Floppy ships six Python processes in one container (nginx aside): a gunicorn
master plus its workers, three Celery workers, and beat. Each Celery worker
carries its own full Django import - ``preload_app`` only buys copy-on-write
savings for the gunicorn workers - so the fixed defaults that suit a NAS with
8 GB comfortably exceed a 2 GB VM, and on a host with swap disabled that is
not "slow", it is kernel reclaim thrashing with nowhere to page (issue #521).

This module is the single place that answers "how big is this host?", so
gunicorn, Celery, the beat schedule and the Redis budget all scale from one
decision instead of five hardcoded numbers.

Import constraints, both load-bearing:

* **No** ``django.*`` imports. ``config/gunicorn.py`` is read by gunicorn
  before ``django.setup()``, ``settings.py`` imports this while it is still
  being constructed, and ``entrypoint.sh`` shells out to ``emit_env()`` before
  anything is initialised.
* Every probe must degrade to ``None`` rather than raise. An unreadable
  ``/sys/fs/cgroup`` or an exotic ``/proc/meminfo`` must not stop the container
  from booting.

Memory detection deliberately consults ``/proc/meminfo`` as well as the cgroup
limit, taking whichever is smaller. When a user sets no ``mem_limit`` in
compose - the configuration in issue #521 - the cgroup reports "unlimited" and
``MemTotal`` is the *only* signal that the box has 2 GB. Conversely, a small
``mem_limit`` on a large host is visible only through the cgroup. Both paths
matter; neither is a fallback for the other.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

GIB = 1024**3
MIB = 1024**2

# cgroup v1 spells "no limit" as a page-aligned near-2**63 sentinel rather than
# a keyword, and the exact value varies with the kernel's page size, so treat
# anything implausibly large as absent.
_CGROUP_UNLIMITED_THRESHOLD = 2**62

_CGROUP_V2_MEMORY_MAX = Path("/sys/fs/cgroup/memory.max")
_CGROUP_V1_MEMORY_MAX = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")
_CGROUP_V2_CPU_MAX = Path("/sys/fs/cgroup/cpu.max")
_CGROUP_V1_CPU_QUOTA = Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
_CGROUP_V1_CPU_PERIOD = Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
_PROC_MEMINFO = Path("/proc/meminfo")
_PROC_SELF_STATM = Path("/proc/self/statm")

TIER_MINIMAL = "minimal"
TIER_CONSTRAINED = "constrained"
TIER_STANDARD = "standard"
TIERS = (TIER_MINIMAL, TIER_CONSTRAINED, TIER_STANDARD)

# Ordered weakest-first so "promote one tier stricter" is an index step.
_TIER_ORDER = (TIER_MINIMAL, TIER_CONSTRAINED, TIER_STANDARD)

# A "field" line in /proc/meminfo is "Name:  <value> kB" - three tokens once
# split, but two suffice to read the number.
_MEMINFO_MIN_FIELDS = 2
# /proc/self/statm: size resident shared text lib data dt, in pages.
_STATM_SHARED_FIELD = 2
_CPU_MAX_FIELDS = 2
# Two cores can't keep the standard process set fed regardless of memory.
_LOW_CPU_COUNT = 2

MINIMAL_MEMORY_THRESHOLD = int(1.5 * GIB)
CONSTRAINED_MEMORY_THRESHOLD = 3 * GIB
# Above this, a missing swap file isn't a risk worth reducing throughput for:
# Floppy's resident footprint (~1.2 GB across its processes) is a small enough
# share that a spike can't exhaust the machine.
NO_SWAP_DERATE_CEILING = 6 * GIB


def _read_text(path: Path) -> str | None:
    """Return a stripped file's contents, or None if it can't be read."""
    try:
        return path.read_text().strip()
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _read_int(path: Path) -> int | None:
    """Return a file's contents parsed as an int, or None."""
    raw = _read_text(path)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _read_cgroup_limit(path: Path) -> int | None:
    """Return a cgroup byte limit, treating "max" and the v1 sentinel as absent."""
    value = _read_int(path)
    if value is None or value <= 0 or value >= _CGROUP_UNLIMITED_THRESHOLD:
        return None
    return value


def _read_meminfo_kb(field: str) -> int | None:
    """Return a /proc/meminfo field in bytes, or None."""
    contents = _read_text(_PROC_MEMINFO)
    if not contents:
        return None
    prefix = f"{field}:"
    for line in contents.splitlines():
        if not line.startswith(prefix):
            continue
        parts = line.split()
        if len(parts) < _MEMINFO_MIN_FIELDS:
            return None
        try:
            return int(parts[1]) * 1024
        except ValueError:
            return None
    return None


def detect_memory_bytes() -> int | None:
    """Return the memory Floppy may actually use, or None if undetectable.

    The minimum of every signal available: a cgroup limit caps what the
    container may use, and MemTotal caps what the host has to give. Either can
    be the binding constraint (see the module docstring).
    """
    candidates = [
        _read_cgroup_limit(_CGROUP_V2_MEMORY_MAX),
        _read_cgroup_limit(_CGROUP_V1_MEMORY_MAX),
        _read_meminfo_kb("MemTotal"),
    ]
    detected = [value for value in candidates if value]
    return min(detected) if detected else None


def detect_available_memory_bytes() -> int | None:
    """Return currently-available host memory, or None."""
    return _read_meminfo_kb("MemAvailable")


def detect_swap_bytes() -> int | None:
    """Return total swap, or None if /proc/meminfo is unreadable.

    Zero is meaningfully different from None: a host that reports no swap has
    no shock absorber for a memory spike, which is what turns overcommitment
    into an unrecoverable hang rather than a slowdown.
    """
    return _read_meminfo_kb("SwapTotal")


def detect_cpus() -> int:
    """Return the effective CPU count, honouring a cgroup CPU quota."""
    quota_cpus = None

    cpu_max = _read_text(_CGROUP_V2_CPU_MAX)
    if cpu_max:
        parts = cpu_max.split()
        if len(parts) == _CPU_MAX_FIELDS and parts[0] != "max":
            try:
                quota, period = int(parts[0]), int(parts[1])
                if quota > 0 and period > 0:
                    quota_cpus = quota / period
            except ValueError:
                quota_cpus = None

    if quota_cpus is None:
        quota = _read_int(_CGROUP_V1_CPU_QUOTA)
        period = _read_int(_CGROUP_V1_CPU_PERIOD)
        if quota and period and quota > 0 and period > 0:
            quota_cpus = quota / period

    if quota_cpus is not None:
        return max(1, int(quota_cpus))

    try:
        return max(1, len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        return max(1, os.cpu_count() or 1)


def current_rss_bytes() -> int | None:
    """Return this process's current RSS, or None.

    ``history_cache_utils._get_rss_kb()`` reports ``ru_maxrss``, which is the
    process *peak* and never falls, so it can't answer "how much am I using
    now". This reads the live value from /proc/self/statm instead.
    """
    contents = _read_text(_PROC_SELF_STATM)
    if not contents:
        return None
    parts = contents.split()
    if len(parts) < _MEMINFO_MIN_FIELDS:
        return None
    try:
        return int(parts[1]) * os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError):
        return None


def anon_resident_bytes_from_statm(contents: str | None) -> int | None:
    """Return the anonymous part of a statm line's resident size, or None.

    statm's "resident" counts every page mapped in, including file pages.
    SQLite memory-maps the database (``SQLITE_MMAP_SIZE_MIB``), so a request
    that reads many rows maps hundreds of MiB of the database file into the
    process. Those pages are page cache: the kernel reclaims them under
    pressure, and recycling the process returns nothing, because the next
    process maps the same file. Subtracting "shared" (file and shmem pages)
    leaves the memory a recycle actually gives back.
    """
    if not contents:
        return None
    parts = contents.split()
    if len(parts) < _STATM_SHARED_FIELD + 1:
        return None
    try:
        resident = int(parts[1])
        shared = int(parts[_STATM_SHARED_FIELD])
    except ValueError:
        return None
    return max(0, resident - shared) * _page_size()


def current_anon_rss_bytes() -> int | None:
    """Return this process's anonymous resident size, or None.

    The size worker recycling compares against its ceiling; see
    ``anon_resident_bytes_from_statm`` for why it is not plain RSS. Read with
    a bare ``open`` because gunicorn calls it after every request.
    """
    try:
        with open(_PROC_SELF_STATM) as statm:  # noqa: PTH123 - hot path
            contents = statm.read()
    except OSError:
        return None
    return anon_resident_bytes_from_statm(contents)


def _page_size() -> int:
    try:
        return os.sysconf("SC_PAGE_SIZE")
    except (AttributeError, OSError, ValueError):  # pragma: no cover - non-POSIX
        return 4096


def _tier_from_memory(memory_bytes: int | None) -> str:
    """Return the tier implied by memory alone."""
    if memory_bytes is None:
        # Never degrade on ignorance: an unreadable probe on a big host would
        # otherwise silently halve its throughput.
        return TIER_STANDARD
    if memory_bytes < MINIMAL_MEMORY_THRESHOLD:
        return TIER_MINIMAL
    if memory_bytes < CONSTRAINED_MEMORY_THRESHOLD:
        return TIER_CONSTRAINED
    return TIER_STANDARD


def _stricter(tier: str) -> str:
    """Return the next tier down, saturating at the strictest."""
    index = _TIER_ORDER.index(tier)
    return _TIER_ORDER[max(0, index - 1)]


def _env(name: str) -> str | None:
    """Read FLOPPY_<name>, falling back to the legacy YAMTRACK_<name>."""
    for prefix in ("FLOPPY_", "YAMTRACK_"):
        value = os.environ.get(f"{prefix}{name}")
        if value is not None and value.strip():
            return value.strip()
    return None


@dataclass(frozen=True)
class ResourceProfile:
    """A single, immutable answer to "how big is this host?"."""

    tier: str
    memory_bytes: int | None
    available_bytes: int | None
    swap_bytes: int | None
    cpus: int
    overridden: bool
    notes: tuple[str, ...] = ()

    @property
    def is_constrained(self) -> bool:
        """Whether this host needs reduced defaults of any kind."""
        return self.tier in (TIER_MINIMAL, TIER_CONSTRAINED)

    def describe(self) -> str:
        """Return a one-line human-readable summary for logs."""

        def gib(value: int | None) -> str:
            return "unknown" if value is None else f"{value / GIB:.1f}GiB"

        parts = [
            f"tier={self.tier}",
            f"mem={gib(self.memory_bytes)}",
            f"swap={gib(self.swap_bytes)}",
            f"cpus={self.cpus}",
        ]
        if self.overridden:
            parts.append("source=override")
        if self.notes:
            parts.append(f"reasons={','.join(self.notes)}")
        return " ".join(parts)


def detect_profile() -> ResourceProfile:
    """Probe the host and derive its resource tier."""
    memory_bytes = detect_memory_bytes()
    swap_bytes = detect_swap_bytes()
    cpus = detect_cpus()

    override = (_env("RESOURCE_TIER") or "auto").lower()
    if override in TIERS:
        return ResourceProfile(
            tier=override,
            memory_bytes=memory_bytes,
            available_bytes=detect_available_memory_bytes(),
            swap_bytes=swap_bytes,
            cpus=cpus,
            overridden=True,
        )

    notes: list[str] = []
    if override != "auto":
        notes.append(f"ignored_invalid_override={override}")

    tier = _tier_from_memory(memory_bytes)
    if memory_bytes is None:
        notes.append("memory_undetected")

    # No swap means a memory spike has nowhere to go, which is the difference
    # between a slow host and a hung one (issue #521). Treat it as one tier less
    # headroom than the raw numbers suggest - but only where Floppy's own
    # footprint is a meaningful fraction of the machine. Running Docker without
    # swap is a common and sensible policy, and a 16 GB host doesn't need
    # de-rating just because it has none.
    if (
        swap_bytes == 0
        and memory_bytes is not None
        and memory_bytes < NO_SWAP_DERATE_CEILING
    ):
        stricter = _stricter(tier)
        if stricter != tier:
            notes.append("no_swap")
            tier = stricter

    # Two cores can't keep three Celery workers and two gunicorn workers fed
    # regardless of how the memory maths works out.
    if cpus <= _LOW_CPU_COUNT and memory_bytes is not None and memory_bytes < 4 * GIB:
        stricter = _stricter(tier)
        if stricter != tier:
            notes.append("low_cpu")
            tier = stricter

    return ResourceProfile(
        tier=tier,
        memory_bytes=memory_bytes,
        available_bytes=detect_available_memory_bytes(),
        swap_bytes=swap_bytes,
        cpus=cpus,
        overridden=False,
        notes=tuple(notes),
    )


PROFILE = detect_profile()


def by_tier(minimal, constrained, standard, profile: ResourceProfile | None = None):
    """Return whichever of three values matches the active tier.

    Keeps tier-scaled settings readable at the call site:
    ``by_tier(50, 75, 150)`` rather than a dict lookup per setting.
    """
    tier = (profile or PROFILE).tier
    if tier == TIER_MINIMAL:
        return minimal
    if tier == TIER_CONSTRAINED:
        return constrained
    return standard


def web_concurrency_default(profile: ResourceProfile | None = None) -> int:
    """Return the worker count this host's profile implies, ignoring the env.

    Separate from :func:`web_concurrency` so an operator's override can be
    reported against what would otherwise have been chosen.
    """
    # gthread handles concurrent I/O in one preloaded worker, on every tier. A
    # second worker mostly duplicates the resident application at idle.
    del profile
    return 1


def gunicorn_threads_default(profile: ResourceProfile | None = None) -> int:
    """Return the thread count this host's profile implies, ignoring the env."""
    return by_tier(2, 4, 4, profile)


def _explicit_int(name: str) -> int | None:
    """Return a positive int from the environment, or None if unusable."""
    explicit = os.environ.get(name)
    if not explicit:
        return None
    try:
        return max(1, int(explicit))
    except ValueError:
        return None


def sizing_source(name: str) -> str:
    """Return whether a sizing variable was chosen by an operator or detected.

    ``emit_env`` exports the answer it reached, because everything supervisord
    starts inherits the ``WEB_CONCURRENCY`` it wrote and would otherwise report
    every value as an override. An empty string is not an override: that
    matches how the value itself is read.
    """
    recorded = os.environ.get(f"FLOPPY_{name}_SOURCE")
    if recorded in ("override", "auto", "invalid"):
        return recorded
    explicit = os.environ.get(name)
    if not explicit:
        return "auto"
    return "override" if _explicit_int(name) is not None else "invalid"


def web_concurrency(profile: ResourceProfile | None = None) -> int:
    """Return the gunicorn worker count for this host."""
    explicit = _explicit_int("WEB_CONCURRENCY")
    if explicit is not None:
        return explicit
    return web_concurrency_default(profile)


def gunicorn_threads(profile: ResourceProfile | None = None) -> int:
    """Return the gunicorn thread count per worker for this host."""
    explicit = _explicit_int("GUNICORN_THREADS")
    if explicit is not None:
        return explicit
    return gunicorn_threads_default(profile)


def celery_queue_plan(profile: ResourceProfile | None = None) -> dict[str, str]:
    """Return which Celery workers to start, and what each consumes.

    A separate interactive worker exists so bulk backfills can never starve
    interactive webhook processing - see ``get_process_role`` in
    ``app/providers/services.py``. Each worker is a whole Django import, so on
    small hosts that isolation has to be traded away, but gradually:

    * standard and constrained: two - the Discover queue shares the background
      worker while the interactive worker stays isolated. This avoids a third
      resident Django import without allowing bulk work to block the UI.
    * minimal: one, consuming every queue. In-queue priority ordering takes
      over from cross-process isolation.

    No tier starts the separate Discover worker; ``start_discover`` stays in
    the plan because supervisord and the installer templates read it.
    """
    resolved = profile or PROFILE
    if resolved.tier == TIER_MINIMAL:
        return {
            "queues": "celery,interactive,discover",
            "role": "combined",
            "start_interactive": "false",
            "start_discover": "false",
        }
    return {
        "queues": "celery,discover",
        "role": "background",
        "start_interactive": "true",
        "start_discover": "false",
    }


def sizing_report(profile: ResourceProfile | None = None) -> dict:
    """Return the whole sizing decision as data, for every surface to read.

    The preflight check, the gunicorn boot line and ``emit_env`` all describe
    the same decision. Deriving each from this one function is what stops them
    disagreeing, and ``expected_programs`` is built from the queue plan rather
    than restated, so it cannot claim a worker that never starts.
    """
    resolved = profile or PROFILE
    plan = celery_queue_plan(resolved)
    web = web_concurrency(resolved)
    web_default = web_concurrency_default(resolved)
    web_source = sizing_source("WEB_CONCURRENCY")

    programs = ["nginx", "gunicorn", "celery"]
    if plan["start_interactive"] == "true":
        programs.append("celery-interactive")
    if plan["start_discover"] == "true":
        programs.append("celery-discover")

    return {
        "tier": resolved.tier,
        "tier_source": "override" if resolved.overridden else "detected",
        "profile": resolved.describe(),
        "memory_bytes": resolved.memory_bytes,
        "available_bytes": resolved.available_bytes,
        "swap_bytes": resolved.swap_bytes,
        "cpus": resolved.cpus,
        "notes": list(resolved.notes),
        "web_concurrency": web,
        "web_concurrency_default": web_default,
        "web_concurrency_source": web_source,
        "gunicorn_threads": gunicorn_threads(resolved),
        "gunicorn_threads_default": gunicorn_threads_default(resolved),
        "gunicorn_threads_source": sizing_source("GUNICORN_THREADS"),
        "celery_queues": plan["queues"],
        "celery_role": plan["role"],
        "start_interactive_worker": plan["start_interactive"] == "true",
        "start_discover_worker": plan["start_discover"] == "true",
        "expected_programs": programs,
        "web_concurrency_over_profile": web_source == "override" and web > web_default,
    }


def gunicorn_max_worker_memory_bytes(profile: ResourceProfile | None = None) -> int:
    """Return the RSS at which a gunicorn worker is retired after a response.

    Lives here, not in ``config/gunicorn.py``, because two callers need the
    same number: the gunicorn hook that enforces it, and Django, whose
    high-water instrumentation reports how close a request ended to it. A
    ceiling the instrumentation disagreed with would misreport exactly the
    events it exists to explain.

    Zero disables retirement, and is passed through rather than replaced by a
    default -- an operator who set it to zero meant it.
    """
    override = os.environ.get("FLOPPY_GUNICORN_MAX_WORKER_MEMORY_BYTES")
    if override is not None and override.strip():
        try:
            return int(override)
        except ValueError:
            pass
    return by_tier(250, 320, 400, profile) * 1024 * 1024


def web_concurrency_warning(profile: ResourceProfile | None = None) -> str:
    """Return a one-line warning when an override costs resident memory.

    Empty when there is nothing to say, so callers can print it or not.
    """
    report = sizing_report(profile)
    if not report["web_concurrency_over_profile"]:
        return ""
    return (
        f"WEB_CONCURRENCY={report['web_concurrency']} is set explicitly; this host"
        f" ({report['profile']}) would use {report['web_concurrency_default']}."
        " Each extra worker holds its own resident copy of the application."
    )


def emit_env(profile: ResourceProfile | None = None) -> None:
    """Print shell ``export`` lines describing the sizing decision.

    ``entrypoint.sh`` evals this so the tier is probed exactly once per
    container rather than independently in each of the six supervised
    processes, which could otherwise disagree. Values the user set explicitly
    are echoed back unchanged - an operator's choice always wins.
    """
    resolved = profile or PROFILE
    plan = celery_queue_plan(resolved)
    # Resolved before the exports below are written: everything supervisord
    # starts inherits WEB_CONCURRENCY from here, so without a recorded source
    # each of those processes would read its own inherited value as an
    # operator override.
    report = sizing_report(resolved)
    exports = {
        "FLOPPY_RESOURCE_TIER": resolved.tier,
        "FLOPPY_CELERY_QUEUES": plan["queues"],
        "FLOPPY_CELERY_ROLE": plan["role"],
        "FLOPPY_START_INTERACTIVE_WORKER": plan["start_interactive"],
        "FLOPPY_START_DISCOVER_WORKER": plan["start_discover"],
        "WEB_CONCURRENCY": str(report["web_concurrency"]),
        "GUNICORN_THREADS": str(report["gunicorn_threads"]),
        "FLOPPY_WEB_CONCURRENCY_SOURCE": report["web_concurrency_source"],
        "FLOPPY_GUNICORN_THREADS_SOURCE": report["gunicorn_threads_source"],
    }
    for key, value in exports.items():
        # stdout is eval'd by the shell; keep it exclusively export lines.
        print(f"export {key}='{value}'")  # noqa: T201  # this is the command's output
    summary = (
        f"[entrypoint] resources {resolved.describe()}"
        f" -> gunicorn {exports['WEB_CONCURRENCY']}x{exports['GUNICORN_THREADS']},"
        f' celery workers background=on({plan["queues"]})'
        f' interactive={"on(interactive)" if plan["start_interactive"] == "true" else "off(combined)"}'
        f' discover={"on(discover)" if plan["start_discover"] == "true" else "off(merged)"}'
    )
    print(summary, file=sys.stderr)  # noqa: T201  # surfaced in container logs
    warning = web_concurrency_warning(resolved)
    if warning:
        # The one surface that reaches an existing install: a template default
        # cannot un-set a value an operator already has saved.
        print(f"[entrypoint] {warning}", file=sys.stderr)  # noqa: T201
