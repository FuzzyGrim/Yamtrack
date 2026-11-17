import fnmatch
from unittest.mock import patch

import fakeredis
from celery import Celery
from celery.beat import ScheduleEntry, Scheduler
from django.conf import settings
from django.test import SimpleTestCase, override_settings
from kombu.utils.functional import retry_over_time

from app import celery_broker
from app.tasks import repair_celery_broker_bindings


class _FakeRedisPipeline:
    def __init__(self, client):
        self.client = client
        self.operations = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def srem(self, key, *members):
        self.operations.append(("srem", key, members))
        return self

    def sadd(self, key, *members):
        self.operations.append(("sadd", key, members))
        return self

    def execute(self):
        for operation, key, members in self.operations:
            bucket = self.client.data.setdefault(key, set())
            if operation == "srem":
                bucket.difference_update(members)
            elif operation == "sadd":
                bucket.update(members)
        return []


class _FakeRedisClient:
    def __init__(self, data):
        self.data = {key: set(values) for key, values in data.items()}

    def scan_iter(self, match=None, count=None):
        for key in self.data:
            if match is None or fnmatch.fnmatch(key, match):
                yield key

    def smembers(self, key):
        return set(self.data.get(key, set()))

    def pipeline(self):
        return _FakeRedisPipeline(self)


class CeleryBrokerRetryTests(SimpleTestCase):
    def test_broker_retries_until_connection_recovers(self):
        from config.celery import app

        self.assertTrue(app.conf.broker_connection_retry_on_startup)
        self.assertIsNone(app.conf.broker_connection_max_retries)

        with patch(
            "kombu.connection.Connection.connect",
            side_effect=[
                ConnectionError("Broker unavailable"),
                ConnectionError("Broker unavailable"),
                "connected",
            ],
        ) as connect:
            result = retry_over_time(
                connect,
                (ConnectionError,),
                max_retries=app.conf.broker_connection_max_retries,
                interval_start=0,
                interval_step=0,
                interval_max=0,
            )

        self.assertEqual(result, "connected")
        self.assertEqual(connect.call_count, 3)


class CeleryTaskPriorityTests(SimpleTestCase):
    """Guard the Redis priority direction.

    Kombu's Redis transport publishes priority N to the key "<queue>:N" (0 uses
    the bare "<queue>") and the worker BRPOPs those keys in ascending order, so a
    *lower* number is a *higher* priority -- the opposite of AMQP. Getting this
    backwards silently strands interactive work at the tail of the queue.
    """

    def test_priority_constants_are_ordered_for_redis(self):
        self.assertLess(
            settings.CELERY_TASK_PRIORITY_INTERACTIVE,
            settings.CELERY_TASK_PRIORITY_FOLLOWUP,
        )
        self.assertLess(
            settings.CELERY_TASK_PRIORITY_FOLLOWUP,
            settings.CELERY_TASK_PRIORITY_DEFAULT,
        )
        self.assertLess(
            settings.CELERY_TASK_PRIORITY_DEFAULT,
            settings.CELERY_TASK_PRIORITY_BACKGROUND,
        )
        self.assertIsNone(settings.CELERY_TASK_DEFAULT_PRIORITY)

    def test_priority_constants_are_within_configured_steps(self):
        steps = settings.CELERY_BROKER_TRANSPORT_OPTIONS["priority_steps"]
        for name in (
            "CELERY_TASK_PRIORITY_INTERACTIVE",
            "CELERY_TASK_PRIORITY_FOLLOWUP",
            "CELERY_TASK_PRIORITY_DEFAULT",
            "CELERY_TASK_PRIORITY_BACKGROUND",
        ):
            with self.subTest(setting=name):
                self.assertIn(getattr(settings, name), steps)

    def test_every_route_declares_its_priority(self):
        steps = settings.CELERY_BROKER_TRANSPORT_OPTIONS["priority_steps"]

        for name, route in settings.CELERY_TASK_ROUTES.items():
            with self.subTest(route=name):
                self.assertIn("priority", route)
                self.assertIn(route["priority"], steps)

        self.assertEqual(
            settings.CELERY_TASK_ROUTES["*"]["priority"],
            settings.CELERY_TASK_PRIORITY_DEFAULT,
        )

    def test_beat_does_not_duplicate_route_priorities(self):
        for name, entry in settings.CELERY_BEAT_SCHEDULE.items():
            with self.subTest(schedule=name):
                self.assertNotIn("priority", entry.get("options", {}))


class CeleryDispatchRoutingTests(SimpleTestCase):
    """Exercise every non-eager dispatch path against the real route merge."""

    def setUp(self):
        self.app = Celery("celery-priority-dispatch-test", broker="memory://")
        self.app.conf.update(
            task_always_eager=False,
            task_default_priority=settings.CELERY_TASK_DEFAULT_PRIORITY,
            task_routes=settings.CELERY_TASK_ROUTES,
        )

        def task_body(*args, **kwargs):
            return None

        # Finalize first. Celery replays every @shared_task onto a newly
        # finalized app, so the real implementations of these names land here
        # -- and dispatching then reaches the real task and fails its signature
        # check (`import_radarr_recurring() missing 1 required positional
        # argument`). This only bites when app and integrations tests share a
        # process, which is why running the suite serially surfaced it.
        self.app.finalize()

        def _register(name):
            # _task_from_fun returns the *existing* task when the name is
            # already registered, so the injected real implementation has to be
            # unregistered first or the stand-in silently never takes effect.
            self.app.tasks.pop(name, None)
            return self.app.task(name=name, ignore_result=True, shared=False)(task_body)

        self.background_task = _register("Backfill item metadata")
        self.followup_task = _register("Import from Radarr (Recurring)")
        self.interactive_task = _register("Process media server webhook")
        self.reconcile_task = _register("Reconcile statistics sync")
        self.fallback_task = _register("Unclassified priority test task")

    def _dispatch_and_capture(self, dispatch):
        with patch.object(self.app.amqp, "send_task_message") as publish:
            dispatch()

        self.assertEqual(publish.call_count, 1)
        return publish.call_args

    @staticmethod
    def _priority(call):
        return call.kwargs["priority"]

    def test_delay_apply_async_send_task_and_beat_use_route_priorities(self):
        cases = (
            (
                "delay",
                self.background_task.delay,
                settings.CELERY_TASK_PRIORITY_BACKGROUND,
            ),
            (
                "apply_async",
                self.followup_task.apply_async,
                settings.CELERY_TASK_PRIORITY_FOLLOWUP,
            ),
            (
                "send_task",
                lambda: self.app.send_task(self.interactive_task.name),
                settings.CELERY_TASK_PRIORITY_INTERACTIVE,
            ),
        )

        for name, dispatch, expected_priority in cases:
            with self.subTest(dispatch=name):
                call = self._dispatch_and_capture(dispatch)
                self.assertEqual(self._priority(call), expected_priority)

        entry = ScheduleEntry(
            name="background-beat",
            task=self.background_task.name,
            schedule=60,
            args=(),
            kwargs={},
            options={},
        )
        scheduler = Scheduler(app=self.app, schedule={}, lazy=True)
        call = self._dispatch_and_capture(
            lambda: scheduler.apply_async(entry, advance=False),
        )
        self.assertEqual(
            self._priority(call),
            settings.CELERY_TASK_PRIORITY_BACKGROUND,
        )

    def test_route_fallback_preserves_default_priority(self):
        call = self._dispatch_and_capture(self.fallback_task.delay)

        self.assertEqual(self._priority(call), settings.CELERY_TASK_PRIORITY_DEFAULT)

    def test_periodic_reconciler_uses_default_queue_for_direct_and_beat_dispatch(self):
        self.assertNotIn("queue", settings.CELERY_TASK_ROUTES[self.reconcile_task.name])
        entry = ScheduleEntry(
            name="reconcile_statistics_sync",
            task=self.reconcile_task.name,
            schedule=60,
            options={"expires": 60},
        )
        scheduler = Scheduler(app=self.app, schedule={}, lazy=True)
        for dispatch in (
            self.reconcile_task.delay,
            lambda: scheduler.apply_async(entry, advance=False),
        ):
            with self.subTest(dispatch=dispatch):
                call = self._dispatch_and_capture(dispatch)
                queue = call.kwargs.get("queue") or self.app.amqp.default_queue
                self.assertEqual(queue.name, "celery")
                self.assertEqual(
                    self._priority(call), settings.CELERY_TASK_PRIORITY_STATISTICS_SYNC
                )

    def test_explicit_priority_remains_a_contextual_override(self):
        call = self._dispatch_and_capture(
            lambda: self.background_task.apply_async(
                priority=settings.CELERY_TASK_PRIORITY_INTERACTIVE,
            ),
        )

        self.assertEqual(
            self._priority(call),
            settings.CELERY_TASK_PRIORITY_INTERACTIVE,
        )


class CeleryPriorityDrainOrderTests(SimpleTestCase):
    """Prove the priority constants actually drain in the right order.

    The tests above only check the constants are ordered relative to each
    other; they can't catch a broker-level regression because
    CELERY_TASK_ALWAYS_EAGER=True in tests bypasses the broker entirely
    (7ab93ebe). Kombu's Redis transport stores a priority-N message under key
    "<queue>:N" (bare "<queue>" for N=0, see PRIORITY_STEPS handling in
    kombu.transport.redis) and drains queues with BRPOP in ascending order.
    This performs real LPUSH/BRPOP calls against fakeredis using our actual
    priority constants and that key scheme, so a future accidental flip of
    the constants fails this test even though eager-mode functional tests
    would not notice.
    """

    def test_interactive_priority_drains_before_background(self):
        server = fakeredis.FakeServer()
        client = fakeredis.FakeStrictRedis(server=server)

        queue = "statistics-priority-test"

        def priority_key(priority):
            return queue if priority == 0 else f"{queue}:{priority}"

        # Enqueue background first, interactive second - drain order must
        # still put interactive first, proving priority (not insertion
        # order) governs delivery.
        client.lpush(
            priority_key(settings.CELERY_TASK_PRIORITY_BACKGROUND),
            "background",
        )
        client.lpush(
            priority_key(settings.CELERY_TASK_PRIORITY_INTERACTIVE),
            "interactive",
        )

        priority_steps = settings.CELERY_BROKER_TRANSPORT_OPTIONS["priority_steps"]
        keys_by_ascending_priority = [priority_key(p) for p in priority_steps]

        first = client.brpop(keys_by_ascending_priority, timeout=1)
        second = client.brpop(keys_by_ascending_priority, timeout=1)

        self.assertEqual(first[1], b"interactive")
        self.assertEqual(second[1], b"background")


class CeleryBrokerRepairTests(SimpleTestCase):
    def test_normalize_kombu_binding_member_repairs_default_separator(self):
        legacy_member = celery_broker.KOMBU_REDIS_DEFAULT_SEPARATOR.join(
            [
                "reply.celery.pidbox",
                "",
                "celery@worker.celery.pidbox",
            ],
        )

        normalized = celery_broker.normalize_kombu_binding_member(
            legacy_member,
            desired_separator=":",
        )

        self.assertEqual(
            normalized,
            "reply.celery.pidbox::celery@worker.celery.pidbox",
        )

    @override_settings(
        CELERY_BROKER_URL="redis://example:6379/0",
        CELERY_BROKER_TRANSPORT_OPTIONS={
            "sep": ":",
            "global_keyprefix": "yamtrack_",
        },
    )
    @patch("app.celery_broker.redis.Redis.from_url")
    def test_repair_celery_redis_bindings_rewrites_legacy_members(
        self,
        mock_from_url,
    ):
        legacy_member = celery_broker.KOMBU_REDIS_DEFAULT_SEPARATOR.join(
            [
                "reply.celery.pidbox",
                "",
                "celery@worker.celery.pidbox",
            ],
        )
        key = "yamtrack__kombu.binding.reply.celery.pidbox"
        fake_client = _FakeRedisClient({key: {legacy_member}})
        mock_from_url.return_value = fake_client

        summary = celery_broker.repair_celery_redis_bindings()

        self.assertEqual(
            summary,
            {
                "keys": 1,
                "members": 1,
                "repaired": 1,
                "removed": 0,
            },
        )
        self.assertEqual(
            fake_client.data[key],
            {"reply.celery.pidbox::celery@worker.celery.pidbox"},
        )
        mock_from_url.assert_called_once_with(
            "redis://example:6379/0",
            socket_timeout=30,
            socket_connect_timeout=10,
        )

    @override_settings(
        CELERY_BROKER_URL="redis://example:6379/0",
        CELERY_BROKER_TRANSPORT_OPTIONS={
            "sep": ":",
            "socket_timeout": 7,
            "socket_connect_timeout": 3,
        },
    )
    @patch("app.celery_broker.redis.Redis.from_url")
    def test_repair_uses_bounded_broker_timeouts(self, mock_from_url):
        mock_from_url.return_value.scan_iter.return_value = []

        celery_broker.repair_celery_redis_bindings()

        mock_from_url.assert_called_once_with(
            "redis://example:6379/0",
            socket_timeout=7,
            socket_connect_timeout=3,
        )

    @override_settings(
        CELERY_BROKER_URL="redis://example:6379/0",
        CELERY_BROKER_TRANSPORT_OPTIONS={"sep": ":"},
    )
    @patch("app.celery_broker.redis.Redis.from_url")
    def test_repair_celery_redis_bindings_drops_malformed_members(
        self,
        mock_from_url,
    ):
        fake_client = _FakeRedisClient(
            {
                "_kombu.binding.reply.celery.pidbox": {
                    "invalid-binding-entry",
                },
            },
        )
        mock_from_url.return_value = fake_client

        summary = celery_broker.repair_celery_redis_bindings()

        self.assertEqual(summary["removed"], 1)
        self.assertEqual(
            fake_client.data["_kombu.binding.reply.celery.pidbox"],
            set(),
        )


class RepairCeleryBrokerBindingsTaskTests(SimpleTestCase):
    @patch("app.celery_broker.repair_celery_redis_bindings")
    def test_calls_repair_and_returns_summary(self, mock_repair):
        mock_repair.return_value = {
            "keys": 1,
            "members": 1,
            "repaired": 1,
            "removed": 0,
        }

        result = repair_celery_broker_bindings()

        mock_repair.assert_called_once_with()
        self.assertEqual(result, mock_repair.return_value)

    @patch("app.celery_broker.repair_celery_redis_bindings")
    def test_swallows_repair_errors(self, mock_repair):
        mock_repair.side_effect = RuntimeError("broker unavailable")

        result = repair_celery_broker_bindings()

        self.assertIsNone(result)


class CeleryBrokerRepairScanTests(SimpleTestCase):
    @override_settings(
        CELERY_BROKER_URL="redis://example:6379/0",
        CELERY_BROKER_TRANSPORT_OPTIONS={"sep": ":"},
    )
    @patch("app.celery_broker.redis.Redis.from_url")
    def test_repair_scans_a_large_keyspace_in_few_round_trips(self, mock_from_url):
        """Every cache key is in the same keyspace; ten per call took ~3 s a run."""
        client = fakeredis.FakeStrictRedis(server=fakeredis.FakeServer())
        for n in range(5000):
            client.set(f"cache:{n}", 1)
        client.sadd("_kombu.binding.celery", "celery::celery")
        scans = []
        original_scan = client.scan

        def counting_scan(*args, **kwargs):
            scans.append(kwargs.get("count"))
            return original_scan(*args, **kwargs)

        client.scan = counting_scan
        mock_from_url.return_value = client

        summary = celery_broker.repair_celery_redis_bindings()

        self.assertEqual(summary["keys"], 1)
        self.assertLessEqual(len(scans), 10)
