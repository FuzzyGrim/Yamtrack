from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import translation

from integrations.imports.helpers import MediaImportError
from integrations.tasks import import_media


class ImportMediaLanguageTests(TestCase):
    """Test import tasks build their messages in the user's language."""

    def setUp(self):
        """Create a user with Italian as the saved language."""
        self.user = get_user_model().objects.create_user(
            username="test",
            password="12345",  # noqa: S106
            language="it-it",
        )

    @patch("integrations.tasks.events.tasks.reload_calendar.delay")
    def test_summary_in_user_language(self, _mock_reload_calendar):
        """Test the import summary is translated to the user's language."""

        def importer(_identifier, _user, _mode):
            return {}, None

        result = import_media(importer, "identifier", self.user.id, "new")

        with translation.override("it-it"):
            expected = translation.gettext("No media was imported.")
        self.assertNotEqual(expected, "No media was imported.")
        self.assertEqual(result, expected)

    @patch("integrations.tasks.events.tasks.reload_calendar.delay")
    def test_importer_runs_in_user_language(self, _mock_reload_calendar):
        """Test the importer runs with the user's language active."""
        active_languages = []
        language_before = translation.get_language()

        def importer(_identifier, _user, _mode):
            active_languages.append(translation.get_language())
            return {}, None

        import_media(importer, "identifier", self.user.id, "new")

        self.assertEqual(active_languages, ["it-it"])
        self.assertEqual(translation.get_language(), language_before)


class ImportMediaModeTests(TestCase):
    """Test import tasks only run "update" mode for importers that support it."""

    def setUp(self):
        """Create a user."""
        self.user = get_user_model().objects.create_user(
            username="test",
            password="12345",  # noqa: S106
        )

    @patch("integrations.tasks.events.tasks.reload_calendar.delay")
    def test_update_mode_unsupported(self, _mock_reload_calendar):
        """Test an importer without "update" mode runs only in other modes."""
        modes = []

        def importer(_identifier, _user, mode):
            modes.append(mode)
            return {}, None

        with self.assertRaisesMessage(
            MediaImportError,
            "Update mode is only available for AniList imports.",
        ):
            import_media(importer, "identifier", self.user.id, "update")
        import_media(importer, "identifier", self.user.id, "new")

        self.assertEqual(modes, ["new"])
