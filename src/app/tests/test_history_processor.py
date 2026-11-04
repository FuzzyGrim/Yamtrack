from datetime import timedelta
from types import SimpleNamespace

from django.test import TestCase
from django.utils import timezone, translation

from app import config, history_processor
from app.history_processor import format_description
from app.models import MediaTypes, Status


class HistoryProcessorTests(TestCase):
    """Test the history processor functions."""

    def test_get_activity_covers_all_media_types(self):
        """Test that every media type has an activity with history phrases."""
        # Get all media types from the MediaTypes enum
        for media_type in MediaTypes:
            try:
                activity = config.get_activity(media_type.value)
            except KeyError:
                self.fail(f"Media type {media_type.name} has no activity")
            self.assertIn(activity, history_processor.INITIAL_PROGRESS_DESCRIPTIONS)

    def test_format_description_status_initial(self):
        """Test format_description for initial status changes."""
        # Test initial status settings
        self.assertEqual(
            format_description(
                "status",
                None,
                Status.IN_PROGRESS.value,
                MediaTypes.TV.value,
            ),
            "Marked as currently watching",
        )
        self.assertEqual(
            format_description(
                "status",
                None,
                Status.COMPLETED.value,
                MediaTypes.MANGA.value,
            ),
            "Marked as finished reading",
        )
        self.assertEqual(
            format_description(
                "status",
                None,
                Status.PLANNING.value,
                MediaTypes.GAME.value,
            ),
            "Added to playing list",
        )
        self.assertEqual(
            format_description(
                "status",
                None,
                Status.DROPPED.value,
                MediaTypes.BOOK.value,
            ),
            "Marked as dropped",
        )
        self.assertEqual(
            format_description(
                "status",
                None,
                Status.PAUSED.value,
                MediaTypes.ANIME.value,
            ),
            "Marked as paused watching",
        )

    def test_format_description_status_transitions(self):
        """Test format_description for status transitions."""
        # Test status transitions
        self.assertEqual(
            format_description(
                "status",
                Status.PLANNING.value,
                Status.IN_PROGRESS.value,
                MediaTypes.TV.value,
            ),
            "Currently watching",
        )
        self.assertEqual(
            format_description(
                "status",
                Status.IN_PROGRESS.value,
                Status.COMPLETED.value,
                MediaTypes.MANGA.value,
            ),
            "Finished reading",
        )
        self.assertEqual(
            format_description(
                "status",
                Status.IN_PROGRESS.value,
                Status.PAUSED.value,
                MediaTypes.GAME.value,
            ),
            "Paused playing",
        )
        self.assertEqual(
            format_description(
                "status",
                Status.PAUSED.value,
                Status.IN_PROGRESS.value,
                MediaTypes.BOOK.value,
            ),
            "Resumed reading",
        )
        self.assertEqual(
            format_description(
                "status",
                Status.IN_PROGRESS.value,
                Status.DROPPED.value,
                MediaTypes.ANIME.value,
            ),
            "Stopped watching",
        )
        self.assertEqual(
            format_description("status", "Custom1", "Custom2", MediaTypes.TV.value),
            "Changed status from Custom1 to Custom2",
        )

    def test_format_description_score(self):
        """Test format_description for score changes."""
        # Initial score
        self.assertEqual(
            format_description("score", None, 8.5, MediaTypes.TV.value),
            "Rated 8.5/10",
        )
        self.assertEqual(
            format_description("score", 0, 7.0, MediaTypes.ANIME.value),
            "Rated 7.0/10",
        )
        # Score change
        self.assertEqual(
            format_description("score", 6.5, 8.0, MediaTypes.MOVIE.value),
            "Changed rating from 6.5 to 8.0",
        )

    def test_format_description_progress(self):
        """Test format_description for progress changes."""
        # Initial progress
        self.assertEqual(
            format_description("progress", None, 120, MediaTypes.GAME.value),
            "Played for 2h 00min",
        )
        self.assertEqual(
            format_description("progress", None, 5, MediaTypes.BOOK.value),
            "Read up to page 5",
        )
        self.assertEqual(
            format_description("progress", None, 10, MediaTypes.MANGA.value),
            "Read up to chapter 10",
        )

        # Progress change
        self.assertEqual(
            format_description("progress", 60, 90, MediaTypes.GAME.value),
            "Added 30min of playtime",
        )
        self.assertEqual(
            format_description("progress", 90, 60, MediaTypes.GAME.value),
            "Removed 30min of playtime",
        )
        self.assertEqual(
            format_description("progress", 10, 15, MediaTypes.BOOK.value),
            "Progress set to 15 pages",
        )
        self.assertEqual(
            format_description("progress", 5, 10, MediaTypes.MANGA.value),
            "Progress set to 10 chapters",
        )

    def test_format_description_notes(self):
        """Test format_description for notes changes."""
        # Initial notes
        self.assertEqual(
            format_description("notes", None, "Test notes"),
            "Added notes",
        )

        # Update notes
        self.assertEqual(
            format_description("notes", "Old notes", "New notes"),
            "Updated notes",
        )

        # Remove notes
        self.assertEqual(
            format_description("notes", "Old notes", ""),
            "Removed notes",
        )

    def test_format_description_generic(self):
        """Test format_description for generic field changes."""
        self.assertEqual(
            format_description("custom_field", "old", "new"),
            "Updated custom field from old to new",
        )

    def test_build_journal_days_labels_are_translated(self):
        """Test the Today and Yesterday separators follow the active language."""
        now = timezone.now()
        entries = [{"date": now}, {"date": now - timedelta(days=1)}]
        user = SimpleNamespace(date_format="Y-m-d")

        with translation.override("it-it"):
            days = history_processor.build_journal_days(entries, user)
            expected = [translation.gettext("Today"), translation.gettext("Yesterday")]

        self.assertEqual([day["label"] for day in days], expected)
        self.assertNotEqual(expected, ["Today", "Yesterday"])
