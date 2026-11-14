# ruff: noqa: T201, S603 -- standalone opt-in benchmark
"""How long a tracked write's follow-up tasks wait behind a busy background slot.

The freshness mode of ``scripts/benchmark_celery.py`` (``--freshness``). Unlike
the BENCH-05 calibration it starts real worker processes with the production
queue plan for a tier (``celery_queue_plan``), one task at a time each, against
a private socket-only Redis and a disposable BENCH-01 SQLite fixture. Providers
are blocked by the test network guard. Beat is not started.

Every publish, start and end is recorded from Celery signals in the publishing
and executing processes. Wait = start - max(publish, ETA): a History refresh's
own countdown is not counted as waiting.
"""

import argparse
import contextlib
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVENTS = "bench45:events"
BLOCKER = "bench45.blocker"
BLOCKER_PRIORITY = 5  # the default route: what most long tasks run at
SCENARIOS = ("baseline", "blocker", "yielding", "discover10")


def configure(database, socket, role):
    """Point Django and Celery at the disposable database and Redis, then hook."""
    os.environ.update(
        SECRET="bench-only-disabled",  # noqa: S106 -- synthetic benchmark secret
        DJANGO_SETTINGS_MODULE="config.test_settings",
        FLOPPY_TEST_ALLOW_NETWORK="0",
        FLOPPY_PROCESS_ROLE=role,
    )
    sys.path.insert(0, str(ROOT / "src"))
    import django
    from django.conf import settings

    settings.DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": str(database),
            "OPTIONS": {"timeout": 30},
        }
    }
    settings.USING_SQLITE_DATABASE = True
    settings.DEBUG = False
    settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, "testserver"]
    settings.CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": f"unix://{socket}?db=3",
        }
    }
    broker = f"redis+socket://{socket}?virtual_host=1"
    backend = f"redis+socket://{socket}?virtual_host=2"
    settings.CELERY_TASK_ALWAYS_EAGER = False
    settings.CELERY_BROKER_URL = broker
    settings.CELERY_RESULT_BACKEND = backend
    django.setup()
    # Test settings disable real scheduling (Statistics sync, History refresh).
    settings.TESTING = False
    from config.celery import app

    app.conf.update(
        broker_url=broker,
        result_backend=backend,
        task_always_eager=False,
        task_store_eager_result=False,
    )
    app.loader.import_default_modules()
    install_hooks(app, socket)
    return app


def install_hooks(app, socket):
    """Record publish/start/end and register the synthetic slot holder."""
    import redis
    from celery import signals

    events = redis.Redis(unix_socket_path=str(socket), db=4)

    def emit(**row):
        row["t"] = time.time()
        row["pid"] = os.getpid()
        events.rpush(EVENTS, json.dumps(row))

    @signals.before_task_publish.connect(weak=False)
    def published(sender=None, headers=None, routing_key=None, properties=None, **_):
        headers = headers or {}
        emit(
            e="publish",
            id=headers.get("id"),
            task=sender,
            queue=routing_key,
            priority=(properties or {}).get("priority"),
            eta=headers.get("eta"),
        )

    @signals.task_received.connect(weak=False)
    def received(request=None, **_):
        # The worker has taken the message off Redis (prefetch or ETA), so a
        # broker-length check can no longer see it waiting.
        emit(e="received", id=request.id, task=request.name, worker=request.hostname)

    @signals.task_prerun.connect(weak=False)
    def started(task_id=None, task=None, **_):
        info = getattr(task.request, "delivery_info", None) or {}
        emit(
            e="start",
            id=task_id,
            task=task.name,
            queue=info.get("routing_key"),
            worker=task.request.hostname,
        )

    @signals.task_postrun.connect(weak=False)
    def ended(task_id=None, task=None, state=None, **_):
        emit(e="end", id=task_id, task=task.name, state=state)

    @app.task(name=BLOCKER, ignore_result=True)
    def blocker(seconds, yielding=False, queues=("celery",)):
        """Hold the slot for ``seconds``; optionally yield like CooperativeRun."""
        from app.task_cooperation import higher_priority_task_waiting

        deadline = time.monotonic() + seconds
        while (left := deadline - time.monotonic()) > 0:
            time.sleep(min(1.0, left))
            if not yielding or time.monotonic() >= deadline:
                continue
            if any(
                higher_priority_task_waiting(queue, priority)
                for queue in queues
                for priority in range(BLOCKER_PRIORITY)
            ):
                blocker.apply_async(
                    args=[deadline - time.monotonic(), True, list(queues)],
                    queue="celery",
                    priority=BLOCKER_PRIORITY,
                )
                return "yielded"
        return "done"


def run_worker(options):
    """One production-shaped worker process (no beat, one slot, prefetch one)."""
    app = configure(options.database, options.socket, options.role)
    app.worker_main(
        [
            "worker",
            "--queues",
            options.queues,
            "--hostname",
            options.hostname,
            "--concurrency",
            "1",
            "--prefetch-multiplier",
            "1",
            "--loglevel",
            "WARNING",
            "--without-mingle",
            "--without-gossip",
        ]
    )


def build_fixture(database, size):
    """Create the BENCH-01 fixture once, as benchmark_fixtures.py does."""
    from unittest.mock import patch

    import requests
    from django.apps import apps
    from django.conf import settings
    from django.core.management import call_command

    from app.benchmark_data import EPOCH, build_dataset

    settings.MIGRATION_MODULES = dict.fromkeys(
        config.label for config in apps.get_app_configs()
    )
    call_command("migrate", run_syncdb=True, verbosity=0)
    with (
        patch("django.utils.timezone.now", return_value=EPOCH),
        patch(
            "requests.sessions.Session.request",
            side_effect=requests.ConnectionError("offline"),
        ),
        patch("celery.app.task.Task.apply_async", return_value=None),
    ):
        build_dataset(size, 1729)
    return database


def worker_plan(tier):
    """Workers to start for ``tier``, from the production queue plan."""
    from config.runtime_profile import ResourceProfile, celery_queue_plan

    profile = ResourceProfile(
        tier=tier,
        memory_bytes=None,
        available_bytes=None,
        swap_bytes=None,
        cpus=4,
        overridden=True,
    )
    plan = celery_queue_plan(profile)
    workers = [("celery", plan["queues"], plan["role"])]
    if plan["start_interactive"] == "true":
        workers.append(("celery-interactive", "interactive", "interactive"))
    if plan["start_discover"] == "true":
        workers.append(("celery-discover", "discover", "background"))
    return plan, workers


@contextlib.contextmanager
def started_workers(options, tier, socket, logs):
    """Start the tier's workers and wait until each answers a ping."""
    from config.celery import app

    _plan, workers = worker_plan(tier)
    processes = []
    try:
        for name, queues, role in workers:
            log = (logs / f"{tier}-{name}.log").open("w")
            processes.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "--freshness-worker",
                        "--database",
                        str(options.database),
                        "--socket",
                        str(socket),
                        "--queues",
                        queues,
                        "--role",
                        role,
                        "--hostname",
                        f"{name}@bench45",
                    ],
                    cwd=ROOT,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env={**os.environ, "TZ": "UTC"},
                )
            )
        expected = {f"{name}@bench45" for name, _q, _r in workers}
        deadline = time.monotonic() + 120
        while True:
            replies = app.control.ping(timeout=1) or []
            if expected <= {next(iter(reply)) for reply in replies}:
                break
            if time.monotonic() > deadline:
                message = f"workers did not start: {expected}"
                raise RuntimeError(message)
        yield [queues for _n, queues, _r in workers]
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                process.kill()


def read_events(redis):
    """Every recorded publish/start/end, oldest first."""
    return [json.loads(row) for row in redis.lrange(EVENTS, 0, -1)]


def wait_idle(redis, after, quiet=4.0, limit=240.0):
    """Wait until every task published since ``after`` ended, then ``quiet`` s."""
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        rows = [row for row in read_events(redis) if row["t"] >= after]
        published = {r["id"] for r in rows if r["e"] == "publish"}
        ended = {r["id"] for r in rows if r["e"] == "end"}
        latest = max((r["t"] for r in rows), default=after)
        if published <= ended and time.time() - latest >= quiet:
            return True
        time.sleep(0.25)
    return False


def tracked_write(user):
    """One play, through the ORM path a real play uses (signals and all)."""
    from django.utils import timezone

    from app.models import Item, Movie, Status

    item = Item.objects.filter(media_type="movie").order_by("?").first()
    Movie.objects.create(
        user=user,
        item=item,
        status=Status.COMPLETED.value,
        end_date=timezone.now(),
    )
    return item.title


def view_pages(user, title):
    """Part C: what the viewer sees 2 s after the write."""
    import logging

    from django.test import Client

    client = Client()
    client.force_login(user)
    captured = []

    class Capture(logging.Handler):
        def emit(self, record):
            captured.append(f"{record.name}: {record.getMessage()}"[:300])

    handler = Capture(level=logging.INFO)
    root = logging.getLogger()
    previous = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    pages = {}
    try:
        for path in ("/history", "/statistics"):
            del captured[:]
            started = time.perf_counter()
            response = client.get(path, follow=True)
            body = response.content.decode("utf-8", "replace")
            pages[path] = {
                "status": response.status_code,
                "ms": round((time.perf_counter() - started) * 1000, 1),
                "shows_play": title in body,
                "log": list(captured),
            }
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)
    return pages


def run_scenario(app, redis, user, scenario, queues, view):
    """One trial: arrange the slot, write at +5 s, collect every task's events."""
    from django.core.cache import cache

    cache.clear()  # debounce and gate keys from the previous trial
    redis.delete(EVENTS)
    trial_start = time.time()
    if scenario in {"blocker", "yielding"}:
        consumed = [q for group in queues for q in group.split(",")]
        mine = sorted({q for q in consumed if q != "interactive"} | {"celery"})
        app.send_task(
            BLOCKER,
            args=[30, scenario == "yielding", mine],
            queue="celery",
            priority=BLOCKER_PRIORITY,
        )
        deadline = time.monotonic() + 30
        while not any(
            r["e"] == "start" and r["task"] == BLOCKER for r in read_events(redis)
        ):
            if time.monotonic() > deadline:
                message = "blocker did not start"
                raise RuntimeError(message)
            time.sleep(0.05)
        time.sleep(5)
    elif scenario == "discover10":
        tabs = ["movie", "tv", "anime", "music", "book", "game", "manga", "podcast"]
        for index in range(10):
            app.send_task(
                "Refresh Discover Tab Cache",
                args=[user.pk, tabs[index % len(tabs)]],
                kwargs={"force": True},
            )
    write_at = time.time()
    title = tracked_write(user)
    pages = None
    if view:
        time.sleep(max(0.0, write_at + 2 - time.time()))
        pages = view_pages(user, title)
    settled = wait_idle(redis, trial_start)
    return {
        "scenario": scenario,
        "write_at": write_at,
        "settled": settled,
        "pages": pages,
        "events": read_events(redis),
    }


def waits(trial):
    """Per task published after the write: wait, run time, queue, priority."""
    rows = trial["events"]
    by_id = {}
    for row in rows:
        by_id.setdefault(row["id"], {})[row["e"]] = row
    out = []
    for phases in by_id.values():
        publish = phases.get("publish")
        if (
            not publish
            or publish["t"] < trial["write_at"]
            or publish["task"] == BLOCKER
        ):
            continue
        eta = publish.get("eta")
        ready = publish["t"]
        if eta:
            ready = max(ready, datetime.fromisoformat(eta).astimezone(UTC).timestamp())
        start = phases.get("start")
        end = phases.get("end")
        taken = phases.get("received")
        out.append(
            {
                "task": publish["task"],
                "queue": publish["queue"],
                "priority": publish["priority"],
                "countdown_s": round(ready - publish["t"], 3),
                "wait_s": None if not start else round(start["t"] - ready, 3),
                "since_write_s": None
                if not start
                else round(start["t"] - trial["write_at"], 3),
                "run_s": None
                if not (start and end)
                else round(end["t"] - start["t"], 3),
                "taken_by_worker_s": None
                if not taken
                else round(taken["t"] - publish["t"], 3),
                "worker": start and start.get("worker"),
                "state": end and end.get("state"),
            }
        )
    return out


def summarise(trials):
    """Median and maximum wait per tier, scenario and task."""
    table = {}
    for trial in trials:
        for row in waits(trial):
            if row["wait_s"] is None:
                continue
            key = (trial["tier"], trial["scenario"], row["task"])
            table.setdefault(key, []).append(row["wait_s"])
    return [
        {
            "tier": tier,
            "scenario": scenario,
            "task": task,
            "n": len(values),
            "median_wait_s": round(statistics.median(values), 2),
            "max_wait_s": round(max(values), 2),
        }
        for (tier, scenario, task), values in sorted(table.items())
    ]


def run(options):
    """Build the fixture once, then every tier, repeat and scenario."""
    from scripts.benchmark_celery import disposable_redis

    options.output.mkdir(parents=True, exist_ok=False)
    logs = options.output / "workers"
    logs.mkdir()
    workdir = options.workdir or options.output
    workdir.mkdir(parents=True, exist_ok=True)
    redis_context = (
        contextlib.nullcontext(options.redis_socket)
        if options.redis_socket
        else disposable_redis()
    )
    with redis_context as socket:
        if options.redis_socket:
            # A private Redis started for this run (for example a container):
            # start from empty.
            import redis as redis_module

            for db in (1, 2, 3, 4):
                redis_module.Redis(unix_socket_path=str(socket), db=db).flushdb()
        template = workdir / "fixture.sqlite3"
        options.database = workdir / "live.sqlite3"
        app = configure(template, socket, "web")
        build_fixture(template, options.size)
        from django.db import connections

        connections.close_all()
        import redis as redis_module
        from django.contrib.auth import get_user_model

        redis = redis_module.Redis(unix_socket_path=str(socket), db=4)
        trials = []
        for tier in options.tiers:
            for suffix in ("-wal", "-shm"):
                Path(f"{options.database}{suffix}").unlink(missing_ok=True)
            shutil.copyfile(template, options.database)
            from django.conf import settings

            settings.DATABASES["default"]["NAME"] = str(options.database)
            connections.close_all()
            with started_workers(options, tier, socket, logs) as queues:
                user = get_user_model().objects.get(username="bench-user-1")
                for repeat in range(1, options.repeats + 1):
                    for scenario in options.scenarios:
                        view = scenario == "blocker" and options.view
                        trial = run_scenario(app, redis, user, scenario, queues, view)
                        trial.update(tier=tier, repeat=repeat, queues=queues)
                        trial["waits"] = waits(trial)
                        trials.append(trial)
                        with (options.output / "trials.jsonl").open("a") as handle:
                            handle.write(json.dumps(trial, default=str) + "\n")
                        print(
                            f"{tier} {scenario} #{repeat} settled={trial['settled']} "
                            + " ".join(
                                f"{w['task'].rsplit('.', 1)[-1][:22]}={w['wait_s']}"
                                for w in trial["waits"]
                            ),
                            flush=True,
                        )
    summary = summarise(trials)
    (options.output / "summary.json").write_text(json.dumps(summary, indent=1))
    for row in summary:
        print(json.dumps(row))


def main(argv=None):
    """Run the freshness benchmark, or one of its worker processes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--freshness-worker", action="store_true", help=argparse.SUPPRESS
    )
    parser.add_argument("--database", type=Path)
    parser.add_argument("--socket", type=Path)
    parser.add_argument("--queues")
    parser.add_argument("--role")
    parser.add_argument("--hostname")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--size", type=int, default=1000)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--tiers", nargs="+", default=["standard", "minimal"])
    parser.add_argument(
        "--scenarios", nargs="+", default=list(SCENARIOS), choices=SCENARIOS
    )
    parser.add_argument("--no-view", dest="view", action="store_false")
    parser.add_argument(
        "--redis-socket",
        type=Path,
        help="use this private, disposable Redis socket instead of starting one",
    )
    parser.add_argument("--workdir", type=Path, help="where the databases live")
    options = parser.parse_args(argv)
    if options.freshness_worker:
        run_worker(options)
        return
    if options.output is None:
        parser.error("--output is required")
    options.output = options.output.resolve()
    sys.path.insert(0, str(ROOT))
    run(options)


if __name__ == "__main__":
    main()
