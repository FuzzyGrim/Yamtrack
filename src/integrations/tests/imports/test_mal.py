import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import (
    Anime,
    Manga,
    Status,
)
from integrations.imports import (
    helpers,
    mal,
)

mock_path = Path(__file__).resolve().parent.parent / "mock_data"
app_mock_path = (
    Path(__file__).resolve().parent.parent.parent.parent / "app" / "tests" / "mock_data"
)


class ImportMAL(TestCase):
    """Test importing media from MyAnimeList."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

    @patch("requests.Session.get")
    def test_import_animelist(self, mock_request):
        """Basic test importing anime and manga from MyAnimeList."""
        with Path(mock_path / "import_mal_anime.json").open() as file:
            anime_response = json.load(file)
        with Path(mock_path / "import_mal_manga.json").open() as file:
            manga_response = json.load(file)

        anime_mock = MagicMock()
        anime_mock.json.return_value = anime_response
        manga_mock = MagicMock()
        manga_mock.json.return_value = manga_response
        mock_request.side_effect = [anime_mock, manga_mock]

        mal.importer("bloodthirstiness", self.user, "new")
        self.assertEqual(Anime.objects.filter(user=self.user).count(), 5)
        self.assertEqual(Manga.objects.filter(user=self.user).count(), 3)

        self.assertEqual(
            Anime.objects.filter(
                user=self.user,
                item__title="Ama Gli Animali",
            )
            .first()
            .item.image,
            settings.IMG_NONE,
        )
        self.assertEqual(
            Anime.objects.get(user=self.user, item__title="FLCL").status,
            Status.PAUSED.value,
        )
        self.assertEqual(
            Manga.objects.get(user=self.user, item__title="Fire Punch").score,
            7,
        )

        self.assertEqual(
            Anime.objects.filter(
                user=self.user,
                item__title="Chainsaw Man",
            )
            .first()
            .history.first()
            .history_date,
            datetime(2022, 12, 28, 19, 20, 54, tzinfo=UTC),
        )

    def test_user_not_found(self):
        """Test that an error is raised if the user is not found."""
        self.assertRaises(
            helpers.MediaImportError,
            mal.importer,
            "fhdsufdsu",
            self.user,
            "new",
        )

    @patch("integrations.imports.mal.settings.MAL_TITLE_LANG", "en")
    def test_process_entry_uses_english_alternative_title_when_available(self):
        """Ensure import prefers MAL English alternative titles when configured."""
        importer = mal.MyAnimeListImporter("bloodthirstiness", self.user, "new")
        content = {
            "node": {
                "id": 999001,
                "title": "Shingeki no Kyojin",
                "alternative_titles": {"en": "Attack on Titan"},
                "num_episodes": 25,
            },
            "list_status": {
                "status": "completed",
                "num_episodes_watched": 25,
                "num_times_rewatched": 0,
                "is_rewatching": False,
                "score": 10,
                "comments": "",
                "start_date": None,
                "finish_date": None,
                "updated_at": "2025-01-01T00:00:00+00:00",
            },
        }

        importer._process_entry(content, "anime")

        self.assertEqual(
            Anime.objects.get(user=self.user, item__media_id="999001").item.title,
            "Attack on Titan",
        )

    @patch("integrations.imports.mal.settings.MAL_TITLE_LANG", "en")
    def test_process_entry_falls_back_when_alternative_titles_missing_or_null(self):
        """Ensure import does not crash and falls back to MAL default title."""
        importer = mal.MyAnimeListImporter("bloodthirstiness", self.user, "new")

        for idx, alternative_titles in enumerate((None, {}), start=1):
            content = {
                "node": {
                    "id": 999100 + idx,
                    "title": f"Default MAL Title {idx}",
                    "alternative_titles": alternative_titles,
                    "num_chapters": 10,
                },
                "list_status": {
                    "status": "reading",
                    "num_chapters_read": 2,
                    "num_times_reread": 0,
                    "is_rereading": False,
                    "score": 8,
                    "comments": "",
                    "start_date": None,
                    "finish_date": None,
                    "updated_at": "2025-01-01T00:00:00+00:00",
                },
            }

            importer._process_entry(content, "manga")

        self.assertEqual(
            Manga.objects.get(user=self.user, item__media_id="999101").item.title,
            "Default MAL Title 1",
        )
        self.assertEqual(
            Manga.objects.get(user=self.user, item__media_id="999102").item.title,
            "Default MAL Title 2",
        )
