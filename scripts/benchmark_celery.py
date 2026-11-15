# ruff: noqa: INP001, T201, PLR2004 -- standalone opt-in benchmark dimensions
"""BENCH-05: disposable schedules, real Celery/Redis and mocked provider work."""

import argparse
import contextlib
import gc
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import benchmark_fixtures as common  # noqa: E402
from scripts import benchmark_server as server  # noqa: E402
from scripts.celery_trace import Trace, correlate, inventory  # noqa: E402

PROTOCOL = "bench05-celery-v1"
NOTES = [
    "Synthetic read/provider/CPU/retry tasks calibrate tracing; scheduled production imports and beat are not executed. No production speedup claimed.",
    "Queue delay is before-publication intent to task_prerun, including publication, ETA/retry countdown and scheduling. It is not pure broker residence time.",
    "After-publish can race consumption; negative broker-return-to-start values are retained. Monotonic timestamps are comparable only on this single host.",
    "Overlap is intersection of task signal boundaries, including observer work and waits; it is not evidence of simultaneous CPU execution.",
    "Thread CPU is per execution thread. Process CPU may include other threads; do not sum overlapping process CPU or shared RSS, especially with the threads pool.",
    "Worker RSS/USS/PSS are boundary snapshots, not peaks. Null means unsupported/denied. Prefork startup and parent/Redis CPU are outside per-attempt resource totals.",
    "Enabled mode combines existing task diagnostics with the new signal/Redis/resource collector. Control retains basic body timers/results and mocked providers. Batch ratios are noisy aggregate overhead estimates, not isolated signal costs.",
    "Provider counts/duration observe mocked requests, not live API latency. Task args, results beyond numeric/checksum/profile allowlists, errors, headers, URLs, process IDs and credentials are not exported.",
    "Fixture/plan/schedule hashes repeat with pinned runtime/source/seed/settings; event times, worker assignment, overlap and resource use are nondeterministic.",
    "Current-model syncdb is not migration replay. LocMem replaces application Redis cache; broker/backend/tracing use newly created socket-only Redis. Workers consume synthetic queues, not the production queue topology.",
]


@contextlib.contextmanager
def disposable_redis():
    """Start socket-only Redis in a new directory and always stop that process."""
    binary = shutil.which("redis-server")
    if not binary:
        message = "redis-server is required (for example: brew install redis)"
        raise RuntimeError(message)
    with tempfile.TemporaryDirectory(prefix="floppy-bench05-", dir="/tmp") as directory:
        socket = Path(directory) / "redis.sock"
        process = subprocess.Popen(  # noqa: S603 -- resolved installed executable, fixed flags
            [
                binary,
                "--port",
                "0",
                "--unixsocket",
                str(socket),
                "--unixsocketperm",
                "700",
                "--save",
                "",
                "--appendonly",
                "no",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 10
            while not socket.exists():
                if process.poll() is not None or time.monotonic() > deadline:
                    message = "Disposable Redis failed to start"
                    raise RuntimeError(message)
                time.sleep(0.01)
            yield socket
        finally:
            process.terminate()
            process.wait(timeout=10)


def plan(seed):
    """Generate fixed identities and work, independent of scheduling order."""
    rng = random.Random(seed)  # noqa: S311 -- deterministic benchmark inputs
    return [
        {
            "job": str(uuid5(NAMESPACE_URL, f"bench05:{seed}:{index}")),
            "queue": "bench05.interactive" if index % 3 == 2 else "bench05.background",
            "calls": 2 if index < 2 else 1,
            "iterations": rng.randrange(15000, 25001),
            "retry_once": index == 3,
            "barrier": index < 2,
        }
        for index in range(6)
    ]


def seed_schedules():
    """Represent dynamic cadence/state kinds without personal schedule metadata."""
    from django_celery_beat.models import (
        ClockedSchedule,
        CrontabSchedule,
        IntervalSchedule,
        PeriodicTask,
    )

    from app.benchmark_data import EPOCH

    interval = IntervalSchedule.objects.create(every=15, period="minutes")
    cron = CrontabSchedule.objects.create(minute="30", hour="3", timezone="UTC")
    clock = ClockedSchedule.objects.create(clocked_time=EPOCH)
    PeriodicTask.objects.bulk_create(
        [
            PeriodicTask(
                name="Synthetic interval",
                task="bench05.work",
                interval=interval,
                queue="bench05.background",
                kwargs='{"user_id":1}',
            ),
            PeriodicTask(
                name="Synthetic disabled",
                task="bench05.work",
                interval=interval,
                enabled=False,
            ),
            PeriodicTask(
                name="Synthetic cron",
                task="bench05.work",
                crontab=cron,
                queue="bench05.interactive",
            ),
            PeriodicTask(
                name="Synthetic one-off",
                task="bench05.work",
                clocked=clock,
                one_off=True,
            ),
        ]
    )
    return [IntervalSchedule, CrontabSchedule, ClockedSchedule, PeriodicTask]


def profile_rows(profiler):
    """Allowlist bounded cProfile function statistics without absolute paths."""
    import pstats

    result = []
    for (filename, line, name), (primitive, calls, total, cumulative, _) in sorted(
        pstats.Stats(profiler).stats.items(), key=lambda item: item[1][3], reverse=True
    )[:20]:
        source = (
            str(Path(filename).relative_to(ROOT))
            if Path(filename).is_relative_to(ROOT)
            else Path(filename).name
        )
        result.append(
            {
                "source": source,
                "line": line,
                "function": name,
                "primitive_calls": primitive,
                "calls": calls,
                "self_s": total,
                "cumulative_s": cumulative,
            }
        )
    return result


def exercise(options, app, redis, work, jobs, traced):
    """Run actual async tasks through one isolated two-slot worker."""
    import requests
    from celery import current_task
    from celery.contrib.testing.worker import start_worker
    from django.conf import settings
    from django.db import connections

    settings.PERF_LOG_ENABLED = traced
    redis.delete("bench05:events", "bench05:barrier")
    trace = Trace(redis, time.perf_counter())

    def provider(_session, method, url, **_kwargs):
        # Deliberate mocked I/O; no DNS/HTTP, credentials or provider payloads.
        started = time.perf_counter()
        time.sleep(options.provider_ms / 1000)
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"synthetic":true}'
        key = trace.identity(current_task)
        if traced and key in trace.active:
            trace.active[key]["provider_calls"] += 1
            trace.active[key]["provider_s"] += time.perf_counter() - started
        return response

    connections.close_all()
    with (
        trace.connected() if traced else contextlib.nullcontext(),
        patch("requests.sessions.Session.request", new=provider),
        start_worker(
            app,
            pool="prefork",
            concurrency=2,
            queues=["bench05.background", "bench05.interactive"],
            perform_ping_check=False,
            shutdown_timeout=30,
        ),
    ):
        started, cpu = time.perf_counter(), time.process_time()
        futures = [
            work.apply_async(args=[job], task_id=job["job"], queue=job["queue"])
            for job in jobs
        ]
        try:
            results = [future.get(timeout=60) for future in futures]
        finally:
            # Tracebacks can retain failed AsyncResults past Redis shutdown.
            # Cancel subscriptions while the private backend is alive, then
            # detach these discarded result handles so __del__ cannot reconnect.
            for future in futures:
                future.backend.remove_pending_result(future)
                future.backend = None
        batch = {
            "wall_s": time.perf_counter() - started,
            "producer_cpu_s": time.process_time() - cpu,
        }
        del futures
        if traced:
            deadline = time.monotonic() + 10
            while (
                sum(
                    json.loads(row)["event"] == "end"
                    for row in redis.lrange("bench05:events", 0, -1)
                )
                < len(jobs) + 1
            ):
                if time.monotonic() > deadline:
                    message = "Task postrun trace did not complete"
                    raise RuntimeError(message)
                time.sleep(0.01)
        # Measured separately, outside the batch interval, in the worker host.
        _, probes = common.measure(lambda: [common.memory() for _ in range(20)])
    events = [json.loads(row) for row in redis.lrange("bench05:events", 0, -1)]
    traced_result = correlate(events) if traced else None
    if traced and (
        traced_result["incomplete_attempts"]
        or traced_result["duplicate_attempts"]
        or not traced_result["overlap"]
    ):
        message = "Incomplete tracing or no controlled overlap observed"
        raise RuntimeError(message)
    for job, result in zip(jobs, results, strict=True):
        if (
            result["attempt"] != int(job["retry_once"])
            or result["provider_calls"] != job["calls"]
        ):
            message = "Synthetic task result did not match its deterministic plan"
            raise RuntimeError(message)
    # Avoid stale deterministic IDs hitting the old result backend in next mode.
    for job in jobs:
        app.backend.forget(job["job"])
    # AsyncResult promise cycles must be collected while Redis is still alive,
    # matching the existing contention harness cleanup order.
    gc.collect()
    return {
        "mode": "enabled" if traced else "control",
        "batch": batch,
        "body_results": results,
        "trace": traced_result,
        "parent_memory_probes_20": probes,
    }


def worker(options, config, socket):
    """Build the shared deterministic fixture, then trace synthetic Celery work."""
    options.output.mkdir(parents=True, exist_ok=False)
    os.environ.update(
        SECRET="bench-only-disabled",  # noqa: S106 -- synthetic benchmark secret
        DJANGO_SETTINGS_MODULE="config.test_settings",
        FLOPPY_TEST_ALLOW_NETWORK="0",
    )
    sys.path.insert(0, str(ROOT / "src"))
    import django
    from django.conf import settings

    settings.DATABASES = {"default": config}
    settings.USING_SQLITE_DATABASE = options.database == "sqlite"
    settings.DEBUG = False
    settings.MEMORY_HIGH_WATER_ENABLED = False
    settings.PERF_LOG_ENABLED = True
    settings.PERF_LOG_SLOW_TASK_MS = 0
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }
    settings.CELERY_TASK_ALWAYS_EAGER = False
    settings.CELERY_BROKER_URL = f"redis+socket://{socket}?virtual_host=1"
    settings.CELERY_RESULT_BACKEND = f"redis+socket://{socket}?virtual_host=2"
    settings.LOGGING = {
        "version": 1,
        "disable_existing_loggers": False,
        "handlers": {"null": {"class": "logging.NullHandler"}},
        "root": {"handlers": ["null"], "level": "ERROR"},
        "loggers": {"app": {"handlers": ["null"], "propagate": False}},
    }
    django.setup()
    import redis as redis_module
    import requests
    from celery import Celery
    from django.apps import apps
    from django.core.management import call_command
    from django.db import connection, connections

    from app import request_timing
    from app.benchmark_data import EPOCH, build_dataset
    from app.models import Item

    settings.MIGRATION_MODULES = dict.fromkeys(
        app.label for app in apps.get_app_configs()
    )
    call_command("migrate", run_syncdb=True, verbosity=0)
    if options.database == "sqlite":
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA journal_mode=WAL")
    redis = redis_module.Redis(unix_socket_path=str(socket))
    app = Celery(
        "bench05",
        broker=settings.CELERY_BROKER_URL,
        backend=settings.CELERY_RESULT_BACKEND,
    )
    app.conf.update(
        task_always_eager=False,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        worker_prefetch_multiplier=1,
        task_routes={},
        worker_hijack_root_logger=False,
        worker_redirect_stdouts=False,
        task_default_queue="bench05.background",
        timezone="UTC",
    )

    @request_timing.timed_provider_call
    def mock_provider():
        return requests.Session().get("https://provider.invalid/benchmark").json()

    @app.task(bind=True, name="bench05.work", max_retries=1)
    def work(self, job):
        # A body baseline is intentionally present in both modes.
        import cProfile

        started, cpu = time.perf_counter(), time.thread_time()
        profiler = cProfile.Profile() if options.profile else None
        if profiler:
            profiler.enable()
        try:
            if job["barrier"]:
                redis.rpush("bench05:barrier", job["job"])
                deadline = time.monotonic() + 10
                while redis.llen("bench05:barrier") < 2:
                    if time.monotonic() > deadline:
                        message = "Two-slot overlap barrier did not open"
                        raise RuntimeError(message)
                    time.sleep(0.005)
            list(Item.objects.order_by("pk").values_list("pk", flat=True)[:24])
            digest = hashlib.pbkdf2_hmac(
                "sha256", b"bench05", str(options.seed).encode(), job["iterations"]
            ).hex()
            for _ in range(job["calls"]):
                mock_provider()
            if job["retry_once"] and not self.request.retries:
                error = ValueError("Synthetic retry")
                raise self.retry(exc=error, countdown=0.05)
        finally:
            if profiler:
                profiler.disable()
        result = {
            "attempt": self.request.retries,
            "checksum": digest,
            "provider_calls": job["calls"],
            "wall_s": time.perf_counter() - started,
            "thread_cpu_s": time.thread_time() - cpu,
        }
        if profiler:
            result["profile"] = profile_rows(profiler)
        return result

    try:
        with (
            patch("django.utils.timezone.now", return_value=EPOCH),
            patch(
                "requests.sessions.Session.request",
                side_effect=AssertionError("Unexpected setup HTTP"),
            ),
            patch(
                "celery.app.task.Task.apply_async",
                side_effect=AssertionError("Unexpected setup publication"),
            ),
        ):
            models, generation = common.measure(
                lambda: build_dataset(options.size, options.seed)
            )
            models += seed_schedules()
            checksum = common.export_fixture(models, options.output / "fixture.json.gz")
            schedules = inventory(ROOT)
            database = common.database_metadata(connection)
        jobs = plan(options.seed)
        modes = [False, True] if options.repeat_index % 2 else [True, False]
        measurements = [
            exercise(options, app, redis, work, jobs, traced) for traced in modes
        ]
        receipt = {
            "fixture_sha256": checksum,
            "size": options.size,
            "seed": options.seed,
            "generation": generation,
            "database": database,
            "inventory": schedules,
            "inventory_sha256": hashlib.sha256(
                json.dumps(schedules, sort_keys=True).encode()
            ).hexdigest(),
            "plan": jobs,
            "plan_sha256": hashlib.sha256(
                json.dumps(jobs, sort_keys=True).encode()
            ).hexdigest(),
            "measurements": measurements,
            "redis_version": redis.info("server")["redis_version"],
            "settings": {
                "pool": "prefork",
                "concurrency": 2,
                "prefetch_multiplier": 1,
                "provider_ms": options.provider_ms,
                "profile": options.profile,
                "eager": False,
                "schema": "syncdb-current-models",
                "clock": EPOCH.isoformat(),
                "cache": "LocMem",
                "broker": "disposable-socket-redis",
                "memory_envelope": False,
            },
        }
        (options.output / "sample.json").write_text(
            json.dumps(receipt, indent=2) + "\n"
        )
    finally:
        connections.close_all()
        redis.close()
        app.close()
        gc.collect()
        if options.database == "sqlite":
            for suffix in ("", "-wal", "-shm"):
                Path(str(options.output / "dataset.sqlite3") + suffix).unlink(
                    missing_ok=True
                )


def report(options, receipts):
    """Record raw attempts, comparisons, variability and explicit limitations."""
    import importlib.metadata

    summary = {
        mode: {
            "wall_s": server.summarize(
                [
                    m["batch"]["wall_s"]
                    for r in receipts
                    for m in r["measurements"]
                    if m["mode"] == mode
                ]
            )
        }
        for mode in ("control", "enabled")
    }
    sources = (
        "scripts/benchmark_celery.py",
        "scripts/celery_trace.py",
        "scripts/benchmark_fixtures.py",
        "scripts/benchmark_server.py",
        "src/app/benchmark_data.py",
        "src/app/memory_envelope.py",
        "src/app/request_timing.py",
        "src/config/settings.py",
        "uv.lock",
    )
    result = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "machine": common.machine(),
        "git": {
            "sha": subprocess.check_output(
                ["git", "rev-parse", "HEAD"],  # noqa: S607 -- fixed Git executable
                cwd=ROOT,
                text=True,
            ).strip(),
            "dirty": bool(
                subprocess.check_output(
                    ["git", "status", "--porcelain"],  # noqa: S607 -- fixed Git executable
                    cwd=ROOT,
                    text=True,
                ).strip()
            ),
        },
        "versions": {
            name: importlib.metadata.version(name)
            for name in ("celery", "kombu", "redis")
        },
        "sources": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in sources
        },
        "scenario": {
            "size": options.size,
            "seed": options.seed,
            "repeats": options.repeats,
            "fixtures": [r["fixture_sha256"] for r in receipts],
            "settings": receipts[0]["settings"],
            "database": receipts[0]["database"],
            "inventory": receipts[0]["inventory_sha256"],
            "plan": receipts[0]["plan_sha256"],
            "redis": receipts[0]["redis_version"],
        },
        "summary": summary,
        "receipts": receipts,
        "notes": NOTES,
    }
    result["enabled_over_control_wall_ratio"] = (
        summary["enabled"]["wall_s"]["median"] / summary["control"]["wall_s"]["median"]
    )
    result["scenario"]["versions"] = result["versions"]
    for key in (
        "fixture_sha256",
        "inventory_sha256",
        "plan_sha256",
        "settings",
        "database",
    ):
        if any(receipt[key] != receipts[0][key] for receipt in receipts):
            message = f"Non-reproducible worker input: {key}"
            raise RuntimeError(message)
    if options.baseline:
        result["comparison"] = server.comparison(
            result, json.loads(options.baseline.read_text())
        )
    command = [
        "python",
        "scripts/benchmark_celery.py",
        "--size",
        str(options.size),
        "--seed",
        str(options.seed),
        "--repeats",
        str(options.repeats),
        "--provider-ms",
        str(options.provider_ms),
        "--database",
        options.database,
        *(["--profile"] if options.profile else []),
        "--output",
        "<new-directory>",
    ]
    result["command"] = command
    (options.output / "report.json").write_text(json.dumps(result, indent=2) + "\n")
    lines = [
        "# Celery work tracing",
        "",
        f"Commit `{result['git']['sha']}`; protocol `{PROTOCOL}`.",
        "",
        "Command: `" + " ".join(command) + "`",
        "",
        "| Mode | N | Batch median ms | Min-max ms | CV % |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, row in summary.items():
        s = row["wall_s"]
        lines.append(
            f"| {name} | {s['n']} | {s['median'] * 1000:.3f} | {s['min'] * 1000:.3f}-{s['max'] * 1000:.3f} | {s['cv_pct']:.2f} |"
        )
    lines += [
        "",
        f"Enabled/control batch wall ratio: {result['enabled_over_control_wall_ratio']:.4f}.",
        "",
        "## Attempt samples",
        "",
        "| Repeat | Job suffix/attempt | State | Queue delay ms | Wall ms | Thread CPU ms | Provider calls |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for index, receipt in enumerate(receipts, 1):
        traced = next(
            m["trace"] for m in receipt["measurements"] if m["mode"] == "enabled"
        )
        for attempt in traced["attempts"]:
            end = next((e for e in attempt["events"] if e["event"] == "end"), {})
            lines.append(
                f"| {index} | {attempt['job'][-8:]}/{attempt['attempt']} | {attempt['state']} | {attempt['queue_delay_s'] * 1000:.3f} | {end.get('wall_s', 0) * 1000:.3f} | {end.get('thread_cpu_s', 0) * 1000:.3f} | {end.get('provider_calls')} |"
            )
        sample = next(
            e for a in traced["attempts"] for e in a["events"] if e["event"] == "start"
        )
        lines += [
            "",
            f"Repeat {index}: {traced['worker_count']} worker processes observed, {len(traced['overlap'])} overlapping attempt pairs; incomplete={traced['incomplete_attempts']}. First memory bytes: `{sample['memory_bytes']}`. Null metrics unavailable.",
            "",
        ]
    inv = receipts[0]["inventory"]
    lines += [
        "## Schedule inventory",
        "",
        f"{len(inv['static'])} static entries; {len(inv['dynamic'])} synthetic dynamic rows; {len(inv['creation_sites'])} direct creation sites.",
        "",
        "Full cadence, state and source evidence in report.json; beat is not executed.",
        "",
        "Machine/limits, settings, fixture/plan/inventory hashes, probe costs and raw events are in report.json.",
        "",
    ]
    if "comparison" in result:
        lines += [
            "## Baseline comparison",
            "",
            "```json",
            json.dumps(result["comparison"], indent=2),
            "```",
            "",
        ]
    lines += ["## Limitations", "", *[f"- {note}" for note in [*NOTES, *inv["notes"]]]]
    (options.output / "report.md").write_text("\n".join(lines) + "\n")


def main():
    """Run fresh independent repeat processes; never attach to live workers."""
    if "--freshness" in sys.argv[1:]:
        # Production queue plan, real workers: see benchmark_celery_freshness.py.
        from scripts import benchmark_celery_freshness

        benchmark_celery_freshness.main(
            [arg for arg in sys.argv[1:] if arg != "--freshness"]
        )
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--size", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=1729)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--provider-ms", type=float, default=40)
    parser.add_argument(
        "--database", choices=("sqlite", "postgresql"), default="sqlite"
    )
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--repeat-index", type=int, default=1, help=argparse.SUPPRESS)
    options = parser.parse_args()
    options.output = options.output.resolve()
    if (
        options.size < 10
        or options.size % 10
        or options.repeats < 2
        or not 1 <= options.provider_ms <= 1000
    ):
        parser.error(
            "size >=10 divisible by10, repeats >=2, provider-ms between1 and1000 required"
        )
    if options.worker:
        with (
            common.disposable_database(options) as config,
            disposable_redis() as socket,
        ):
            worker(options, config, socket)
        return
    options.output.mkdir(parents=True, exist_ok=False)
    receipts = []
    for repeat in range(1, options.repeats + 1):
        target = options.output / f"repeat-{repeat}"
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            "--output",
            str(target),
            "--size",
            str(options.size),
            "--seed",
            str(options.seed),
            "--provider-ms",
            str(options.provider_ms),
            "--database",
            options.database,
            "--repeat-index",
            str(repeat),
        ]
        if options.profile:
            command.append("--profile")
        subprocess.run(  # noqa: S603 -- fixed interpreter/script, argument list
            command,
            cwd=ROOT,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": "0", "TZ": "UTC"},
        )
        receipts.append(json.loads((target / "sample.json").read_text()))
        print(f"Finished repeat={repeat}", flush=True)
    report(options, receipts)
    print("Reports: report.json and report.md", flush=True)


if __name__ == "__main__":
    main()
