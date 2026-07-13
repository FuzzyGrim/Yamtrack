import importlib
from datetime import datetime
from zoneinfo import ZoneInfo

from django.apps import apps
from django.test import TestCase

from events.models import Event
from events.tests.calendar.utils import CalendarFixturesMixin

migration_module = importlib.import_module(
    "events.migrations.0015_normalize_unknown_date_events",
)

LEGACY_MIN = datetime.min.replace(tzinfo=ZoneInfo("UTC"))
PAST = datetime(2008, 1, 20, 22, 0, tzinfo=ZoneInfo("UTC"))


class NormalizeUnknownDateEventsTests(CalendarFixturesMixin, TestCase):
    """Data migration 0015 backfills legacy unknown-date events."""

    def _run_migration(self):
        migration_module.normalize_unknown_date_events(apps, None)

    def test_trailing_legacy_event_becomes_sentinel(self):
        """A legacy placeholder with no later aired episode becomes the sentinel."""
        Event.objects.create(
            item=self.season_item,
            content_number=1,
            datetime=PAST,
        )
        legacy = Event.objects.create(
            item=self.season_item,
            content_number=2,
            datetime=LEGACY_MIN,
        )

        self._run_migration()

        legacy.refresh_from_db()
        self.assertTrue(legacy.is_max_datetime)

    def test_legacy_event_with_later_aired_is_assumed_aired(self):
        """A legacy placeholder followed by an aired episode inherits its date."""
        legacy = Event.objects.create(
            item=self.season_item,
            content_number=1,
            datetime=LEGACY_MIN,
        )
        Event.objects.create(
            item=self.season_item,
            content_number=2,
            datetime=PAST,
        )

        self._run_migration()

        legacy.refresh_from_db()
        self.assertFalse(legacy.is_max_datetime)
        self.assertEqual(legacy.datetime, PAST)

    def test_dated_events_are_left_untouched(self):
        """Events with real dates are not modified by the migration."""
        dated = Event.objects.create(
            item=self.season_item,
            content_number=1,
            datetime=PAST,
        )

        self._run_migration()

        dated.refresh_from_db()
        self.assertEqual(dated.datetime, PAST)
