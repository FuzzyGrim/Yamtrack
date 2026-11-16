"""Contracts for disposable benchmark data and portable exports."""

import gzip
import importlib.util
import io
import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from app import models
from app.benchmark_data import TRACKERS, build_dataset
from integrations.models import ImportRun
from users.models import User

spec = importlib.util.spec_from_file_location(
    "benchmark_fixtures",
    Path(__file__).resolve().parents[3] / "scripts/benchmark_fixtures.py",
)
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


class BenchmarkFixtureTests(TestCase):
    def test_coverage_and_portable_round_trip(self):
        with (
            patch(
                "requests.sessions.Session.request",
                side_effect=AssertionError("Provider I/O"),
            ),
            patch(
                "celery.app.task.Task.apply_async",
                side_effect=AssertionError("Task dispatch"),
            ),
        ):
            exported = build_dataset(100, 1729)
        self.assertEqual(sum(model.objects.count() for model in set(TRACKERS)), 100)
        self.assertEqual(User.objects.count(), 3)
        self.assertTrue(
            all(not user.has_usable_password() for user in User.objects.all())
        )
        self.assertEqual(models.Episode.objects.count(), 240)
        self.assertEqual(models.Episode.history.count(), 480)
        self.assertEqual(models.Item.objects.count(), 220)
        self.assertEqual(models.CollectionEntry.objects.count(), 40)
        self.assertEqual(ImportRun.objects.count(), 3)
        self.assertEqual(
            sum(ImportRun.objects.values_list("created_count", flat=True)), 50
        )
        self.assertEqual(
            set(models.Movie.objects.values_list("status", flat=True)),
            set(models.Status.values),
        )
        for entry in models.CollectionEntry.objects.all():
            field = models.CollectionField.objects.get(
                pk=next(iter(entry.custom_field_values))
            )
            self.assertEqual(field.group.user_id, entry.user_id)
        for episode in models.Episode.objects.select_related(
            "item", "related_season__item"
        ):
            self.assertEqual(
                episode.item.media_id, episode.related_season.item.media_id
            )
            self.assertEqual(episode.item.season_number, 1)
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "fixture.json.gz"
            checksum = harness.export_fixture(exported, fixture)
            self.assertEqual(checksum, harness.export_fixture(exported, fixture))
            with gzip.open(fixture, "rt") as stream:
                records = json.load(stream)
            self.assertEqual(
                len(records), sum(model.objects.count() for model in exported)
            )
            for model in reversed(exported):
                model.objects.all().delete()
            call_command("loaddata", str(fixture), verbosity=0, stdout=io.StringIO())
            self.assertEqual(checksum, harness.export_fixture(exported, fixture))

    def test_refuses_existing_data_and_invalid_sizes(self):
        for size in (0, 9, 11):
            with self.assertRaisesMessage(ValueError, "multiple of 10"):
                build_dataset(size, 1)
        User.objects.create_user(username="existing")
        with self.assertRaisesMessage(ValueError, "empty disposable"):
            build_dataset(10, 1)

    def test_memory_access_denied_preserves_rss(self):
        try:
            import psutil
        except ImportError:
            self.skipTest("psutil is optional")
        with patch.object(psutil, "Process") as process:
            process.return_value.memory_info.return_value.rss = 123
            process.return_value.memory_full_info.side_effect = psutil.AccessDenied()
            self.assertEqual(harness.memory(), {"rss": 123, "uss": None, "pss": None})

    def test_memory_process_exit_is_reported_unavailable(self):
        try:
            import psutil
        except ImportError:
            self.skipTest("psutil is optional")
        with patch.object(psutil, "Process", side_effect=psutil.NoSuchProcess(999999)):
            self.assertEqual(
                harness.memory(999999), {"rss": None, "uss": None, "pss": None}
            )


class MeasurementProtocolTests(SimpleTestCase):
    def test_cache_phases_reset_each_cold_sample_and_prime_warm_phase(self):
        events = []
        cold, warm = harness.phase_samples(
            lambda: events.append("operation"),
            2,
            lambda: events.append("reset"),
        )
        self.assertEqual(
            events,
            [
                "reset",
                "operation",
                "reset",
                "operation",
                "operation",
                "operation",
                "operation",
            ],
        )
        self.assertEqual([row["iteration"] for row in cold], [1, 2])
        self.assertEqual([row["iteration"] for row in warm], [1, 2])
        self.assertTrue(all("reset" in row for row in cold))
        self.assertTrue(all("reset" not in row for row in warm))

    def test_postgres_failure_drops_only_the_created_database(self):
        with (
            patch.dict(
                os.environ,
                {
                    "BENCH_POSTGRES_HOST": "127.0.0.1",
                    "BENCH_POSTGRES_USER": "benchmark",
                },
            ),
            patch("psycopg.connect") as connect,
            patch("django.db.connections.close_all") as close,
        ):
            admin = connect.return_value.__enter__.return_value
            with self.assertRaisesMessage(RuntimeError, "operation failed"):
                with harness.disposable_database(
                    SimpleNamespace(database="postgresql")
                ) as config:
                    self.assertTrue(config["NAME"].startswith("floppy_bench02_"))
                    name = config["NAME"]
                    raise RuntimeError("operation failed")
            statements = [
                call.args[0].as_string() for call in admin.execute.call_args_list
            ]
            self.assertEqual(
                statements,
                [
                    f'CREATE DATABASE "{name}" TEMPLATE template0',
                    f'DROP DATABASE "{name}" WITH (FORCE)',
                ],
            )
            close.assert_called_once()

    def test_postgres_refuses_nonlocal_host_before_connecting(self):
        with (
            patch.dict(os.environ, {"BENCH_POSTGRES_HOST": "example.invalid"}),
            patch("psycopg.connect") as connect,
        ):
            with (
                self.assertRaisesMessage(ValueError, "must be loopback"),
                harness.disposable_database(SimpleNamespace(database="postgresql")),
            ):
                self.fail("Nonlocal database accepted")
            connect.assert_not_called()

    def comparison_report(self):
        process = dict.fromkeys(
            [
                "role",
                "debug",
                "cache_backend",
                "celery_eager",
                "celery_broker",
                "provider_network",
                "schema_setup",
                "warmup_calls_per_workload",
                "phase_order",
                "cold_reset",
                "os_database_page_cache",
                "python_hash_seed",
                "timezone",
                "database_conn_max_age",
                "thread_limits",
            ],
            False,
        )
        return {
            "schema_version": 2,
            "protocol": harness.PROTOCOL,
            "machine": {"logical_cpus": 2},
            "executions": [
                {
                    "size": 1000,
                    "seed": 1729,
                    "fixture_sha256": "synthetic",
                    "database": {"engine": "sqlite"},
                    "process": process,
                    "cold_samples": {"movie": [{"wall_s": 0.002}]},
                    "samples": {"movie": [{"wall_s": 0.001}]},
                }
            ],
        }

    def test_baseline_ratios_keep_cache_states_separate(self):
        before = self.comparison_report()
        after = deepcopy(before)
        after["executions"][0]["samples"]["movie"][0]["wall_s"] = 0.003
        result = harness.baseline_comparison(after, before)
        self.assertEqual([row["state"] for row in result], ["application_cold", "warm"])
        self.assertEqual([row["wall_ratio"] for row in result], [1, 3])
        self.assertEqual([row["change_percent"] for row in result], [0, 200])

    def test_incompatible_baselines_have_notes_and_no_timing_ratios(self):
        before = self.comparison_report()
        for field, value in (
            ("fixture_sha256", "different"),
            ("database", {"engine": "postgresql"}),
        ):
            after = deepcopy(before)
            after["executions"][0][field] = value
            rows = harness.baseline_comparison(after, before)
            self.assertTrue(all(not row["comparable"] and row["notes"] for row in rows))
            self.assertTrue(all("wall_ratio" not in row for row in rows))
        after = deepcopy(before)
        after["machine"]["logical_cpus"] = 4
        self.assertTrue(
            all(
                not row["comparable"]
                for row in harness.baseline_comparison(after, before)
            )
        )
        with self.assertRaisesMessage(ValueError, "legacy BENCH-01"):
            harness.baseline_comparison(before, {"schema_version": 1})


class LoadingExtrasTests(TestCase):
    def test_loading_extras_add_long_show_events_tags_and_artwork(self):
        import events.tasks
        from app.benchmark_data import add_loading_extras
        from events.models import Event

        with (
            patch(
                "requests.sessions.Session.request",
                side_effect=AssertionError("Provider I/O"),
            ),
            patch("celery.app.task.Task.apply_async", return_value=None),
        ):
            build_dataset(100, 1729)
            user = User.objects.get(username="bench-user-1")
            show = add_loading_extras(user, 1729)
        self.assertEqual(show.media_id, "bench-long-show")
        seasons = models.Season.objects.filter(item__media_id="bench-long-show")
        self.assertEqual(seasons.count(), 16)
        self.assertEqual(
            models.Episode.objects.filter(item__media_id="bench-long-show").count(),
            154,
        )
        self.assertGreater(Event.objects.count(), 0)
        self.assertEqual(models.Tag.objects.filter(user=user).count(), 40)
        self.assertEqual(
            models.Track.objects.filter(album__title="Synthetic Long Album").count(),
            40,
        )
        self.assertEqual(
            models.PodcastEpisode.objects.filter(
                show__podcast_uuid="bench-podcast-small"
            ).count(),
            60,
        )
        self.assertFalse(models.Item.objects.filter(image="").exists())
        self.assertTrue(
            models.Item.objects.filter(media_type="game")
            .first()
            .image.startswith("https://images.igdb.com/igdb/image/upload/t_original/")
        )
