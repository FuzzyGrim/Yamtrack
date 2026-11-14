"""Scheduled syncs that were rewriting unchanged rows on every run.

Each importer here runs twice against the same fake upstream: the second run
must write no tracking, item or collection rows and start no follow-up work.
A real change on a later run must still land. The slow
``test_recurring_sync_no_change`` measures every recurring importer this way.
"""

import logging
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from celery.app.task import Task
from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import Book, BookListeningSession, CollectionEntry, Item
from integrations.models import CollectionSourceState, PlexWatchlistSyncItem
from integrations.source_sync import sync_collection_source_state
from integrations.tasks._import_helpers import (
    format_import_message,
    has_imported_media,
    import_run_counts,
)
from integrations.tests import recurring_sync_scenarios as scenarios
from integrations.tests.sync_probe import probe_run

_QUIET_LOGGERS = ("app.providers.services", "integrations.imports.plex")


def setUpModule():
    """Silence provider lookups the network guard refuses (they are caught)."""
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.CRITICAL)


def tearDownModule():
    """Restore the logger levels for other modules."""
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.NOTSET)


class UnchangedRunTestCase(TestCase):
    """Run one scenario on demand, probing the runs that matter."""

    scenario_class = None

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="sync-user")
        self.scenario = self.scenario_class()
        self.scenario.connect(self.user)
        stack = ExitStack()
        for patcher in self.scenario.patches():
            stack.enter_context(patcher)
        self.addCleanup(stack.close)

    def run_sync(self):
        """Run once, dropping queued work as if a worker ran it."""
        with (
            patch.object(Task, "apply_async", autospec=True),
            self.captureOnCommitCallbacks(execute=True),
        ):
            return self.scenario.run(self.user)

    def probed_run(self):
        """Run once and return what it wrote and queued."""
        with probe_run(self.user.id, self) as report:
            result = self.scenario.run(self.user)
        return report, result

    def assert_silent(self, report):
        """Nothing but the run's own bookkeeping was written or started."""
        self.assertEqual(dict(report.data_writes), {})
        self.assertEqual(dict(report.side_effects), {})


class RadarrUnchangedRunTests(UnchangedRunTestCase):
    scenario_class = scenarios.RadarrScenario

    def test_unchanged_library_writes_nothing(self):
        self.run_sync()

        report, result = self.probed_run()

        self.assert_silent(report)
        self.assertEqual(result, "No media was imported. 1 unchanged.")

    def test_changed_quality_and_new_movie_still_import(self):
        self.run_sync()
        self.scenario.change()

        report, result = self.probed_run()

        self.assertEqual(result, "Imported 2 collection entries. 2 updated.")
        self.assertEqual(report.tasks["Reload calendar"], 1)
        self.assertTrue(report.statistics_generation_moved)
        arrival = CollectionEntry.objects.get(user=self.user, item__media_id="26718")
        self.assertEqual(arrival.resolution, "Bluray-2160p")
        self.assertTrue(
            CollectionEntry.objects.filter(user=self.user, item__media_id="27205").exists(),
        )


class SonarrUnchangedRunTests(UnchangedRunTestCase):
    scenario_class = scenarios.SonarrScenario

    def test_unchanged_library_writes_nothing(self):
        self.run_sync()

        report, result = self.probed_run()

        self.assert_silent(report)
        self.assertEqual(result, "No media was imported. 1 unchanged.")

    def test_new_episode_file_still_imports(self):
        self.run_sync()
        self.scenario.change()

        report, result = self.probed_run()

        self.assertEqual(result, "Imported 1 collection entry. 1 updated.")
        self.assertEqual(report.writes["INSERT integrations_collectionsourcestate"], 1)
        self.assertEqual(report.tasks["Reload calendar"], 1)

    def test_series_losing_every_file_still_drops_its_copies(self):
        self.run_sync()
        self.scenario.series[0]["statistics"]["episodeFileCount"] = 0

        report, _result = self.probed_run()

        self.assertFalse(
            CollectionSourceState.objects.filter(user=self.user, source="sonarr").exists(),
        )
        self.assertEqual(report.writes["DELETE integrations_collectionsourcestate"], 2)


class MylarUnchangedRunTests(UnchangedRunTestCase):
    scenario_class = scenarios.MylarScenario

    def test_unchanged_library_writes_nothing(self):
        self.run_sync()

        report, result = self.probed_run()

        self.assert_silent(report)
        self.assertEqual(result, "No media was imported. 3 unchanged.")

    def test_new_and_removed_copies_still_sync(self):
        self.run_sync()
        self.scenario.change()

        report, _result = self.probed_run()

        owned = set(
            CollectionSourceState.objects.filter(user=self.user).values_list(
                "item__media_id",
                flat=True,
            ),
        )
        self.assertEqual(owned, {"301", "303", "401"})
        self.assertEqual(report.tasks["Reload calendar"], 1)


class KapowarrUnchangedRunTests(UnchangedRunTestCase):
    scenario_class = scenarios.KapowarrScenario

    def test_unchanged_library_writes_nothing(self):
        self.run_sync()

        report, result = self.probed_run()

        self.assert_silent(report)
        self.assertEqual(result, "No media was imported. 2 unchanged.")

    def test_new_and_removed_copies_still_sync(self):
        self.run_sync()
        self.scenario.change()

        report, _result = self.probed_run()

        owned = set(
            CollectionSourceState.objects.filter(user=self.user).values_list(
                "item__media_id",
                flat=True,
            ),
        )
        self.assertEqual(owned, {"302", "303"})
        self.assertEqual(report.tasks["Reload calendar"], 1)


class HardcoverUnchangedRunTests(UnchangedRunTestCase):
    scenario_class = scenarios.HardcoverScenario

    def test_unchanged_library_starts_no_follow_up_work(self):
        """Unchanged books used to count as imported media."""
        self.run_sync()

        report, result = self.probed_run()

        self.assert_silent(report)
        self.assertEqual(result, "No media was imported. 2 unchanged.")

    def test_changed_progress_still_imports(self):
        self.run_sync()
        self.scenario.change()

        report, _result = self.probed_run()

        self.assertEqual(Book.objects.get(user=self.user, item__media_id="2").progress, 150)
        self.assertEqual(report.writes["UPDATE app_book"], 1)
        self.assertTrue(report.statistics_generation_moved)


class AudiobookshelfUnchangedRunTests(UnchangedRunTestCase):
    scenario_class = scenarios.AudiobookshelfScenario

    def test_unchanged_listening_session_is_not_rewritten(self):
        self.run_sync()

        report, _result = self.probed_run()

        self.assert_silent(report)

    def test_changed_listening_session_still_updates(self):
        self.run_sync()
        self.scenario.change()

        report, _result = self.probed_run()

        self.assertEqual(BookListeningSession.objects.get().minutes, 45)
        self.assertEqual(report.writes["UPDATE app_booklisteningsession"], 1)
        self.assertGreater(report.follow_ups["history_refreshes"], 0)


class PlexWatchlistUnchangedRunTests(UnchangedRunTestCase):
    scenario_class = scenarios.PlexWatchlistScenario

    def test_unchanged_watchlist_writes_nothing(self):
        self.run_sync()

        report, _result = self.probed_run()

        self.assert_silent(report)

    def test_last_seen_is_refreshed_once_a_day(self):
        self.run_sync()
        stale = datetime(2026, 1, 1, tzinfo=UTC)
        PlexWatchlistSyncItem.objects.update(last_seen_at=stale)

        self.run_sync()

        for seen in PlexWatchlistSyncItem.objects.values_list("last_seen_at", flat=True):
            self.assertGreater(seen, stale + timedelta(days=1))

    def test_removed_item_still_leaves_planning(self):
        self.run_sync()
        self.scenario.change()

        report, _result = self.probed_run()

        self.assertEqual(report.writes["DELETE app_tv"], 1)
        self.assertFalse(
            PlexWatchlistSyncItem.objects.filter(
                user=self.user,
                item__media_id="321",
                is_active=True,
            ).exists(),
        )


class CollectionSourceStateChangeTests(TestCase):
    """What counts as a changed copy for the shared collection helper."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="collector")
        self.item = Item.objects.create(
            media_id="1",
            source="tmdb",
            media_type="movie",
            title="Movie",
        )

    def _sync(self, **kwargs):
        return sync_collection_source_state(
            user=self.user,
            item=self.item,
            source="radarr",
            source_instance_id=1,
            **kwargs,
        )[1]

    def test_without_a_source_timestamp_the_first_seen_time_stays(self):
        self.assertTrue(self._sync(quality_label="1080p"))
        first = CollectionSourceState.objects.get().last_source_updated_at

        self.assertFalse(self._sync(quality_label="1080p"))

        self.assertEqual(CollectionSourceState.objects.get().last_source_updated_at, first)

    def test_a_new_source_timestamp_or_label_is_a_change(self):
        added = datetime(2026, 1, 2, tzinfo=UTC)
        self.assertTrue(self._sync(quality_label="1080p", source_updated_at=added))
        self.assertFalse(self._sync(quality_label="1080p", source_updated_at=added))
        self.assertTrue(
            self._sync(quality_label="1080p", source_updated_at=added + timedelta(days=1)),
        )
        self.assertTrue(self._sync(quality_label="2160p"))
        self.assertEqual(CollectionEntry.objects.get().resolution, "2160p")

    def test_a_deleted_entry_is_restored_without_counting_a_change(self):
        """The entry is still reconciled, so drift is repaired as before."""
        self._sync(quality_label="1080p")
        CollectionEntry.objects.all().delete()

        self.assertFalse(self._sync(quality_label="1080p"))

        self.assertEqual(CollectionEntry.objects.get().resolution, "1080p")


class UnchangedCountTests(TestCase):
    """Rows reported as unchanged are a metric, not imported media."""

    def test_unchanged_alone_is_not_imported_media(self):
        counts = {"unchanged": 4}

        self.assertEqual(import_run_counts(counts), (0, 0))
        self.assertFalse(has_imported_media(counts))
        self.assertEqual(format_import_message(counts), "No media was imported. 4 unchanged.")

    def test_a_removal_beside_unchanged_rows_is_still_a_change(self):
        self.assertTrue(has_imported_media({"unchanged": 4, "removed": 1}))
