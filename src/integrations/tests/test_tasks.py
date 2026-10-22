from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import translation

from integrations.tasks import import_media


class ImportMediaLanguageTests(TestCase):
    """Test import tasks build their messages in the user's language."""

    def setUp(self):
        """Create a user with Italian as the saved language."""
        self.user = get_user_model().objects.create_user(
            username="test",
            password="12345",  # noqa: S106
            language="it",
        )

    @patch("integrations.tasks.events.tasks.reload_calendar.delay")
    def test_summary_in_user_language(self, _mock_reload_calendar):
        """Test the import summary is translated to the user's language."""

        def importer(_identifier, _user, _mode):
            return {}, None

        result = import_media(importer, "identifier", self.user.id, "new")

        self.assertEqual(result, "Nessun media è stato importato.")

    @patch("integrations.tasks.events.tasks.reload_calendar.delay")
    def test_importer_runs_in_user_language(self, _mock_reload_calendar):
        """Test the importer runs with the user's language active."""
        active_languages = []
        language_before = translation.get_language()

        def importer(_identifier, _user, _mode):
            active_languages.append(translation.get_language())
            return {}, None

        import_media(importer, "identifier", self.user.id, "new")

        self.assertEqual(active_languages, ["it"])
        self.assertEqual(translation.get_language(), language_before)
