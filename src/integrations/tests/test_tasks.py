from django.test import SimpleTestCase

from app.models import MediaTypes
from integrations.imports import helpers
from integrations.tasks import format_import_message


class FormatImportMessage(SimpleTestCase):
    """Test the summary shown once an import finishes."""

    def test_media_types_are_pluralized(self):
        """Media type counts use the readable plural of the media type."""
        message = format_import_message(
            {MediaTypes.MOVIE.value: 1, MediaTypes.TV.value: 2},
        )

        self.assertEqual(message, "Imported 1 Movie and 2 TV Shows.")

    def test_custom_lists_are_counted(self):
        """Custom lists are not a media type, so they get their own label."""
        self.assertEqual(
            format_import_message({helpers.CUSTOM_LIST_COUNT_KEY: 1}),
            "Imported 1 custom list.",
        )
        self.assertEqual(
            format_import_message(
                {MediaTypes.MOVIE.value: 3, helpers.CUSTOM_LIST_COUNT_KEY: 2},
            ),
            "Imported 3 Movies and 2 custom lists.",
        )

    def test_empty_counts_report_nothing_imported(self):
        """An import that found nothing says so."""
        self.assertEqual(
            format_import_message({MediaTypes.MOVIE.value: 0}),
            "No media was imported.",
        )
