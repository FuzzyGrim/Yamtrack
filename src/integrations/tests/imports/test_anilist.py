import json
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from app.models import (
    Anime,
    Manga,
    Sources,
    Status,
)
from integrations.imports import (
    anilist,
    helpers,
)

mock_path = Path(__file__).resolve().parent.parent / "mock_data"
app_mock_path = (
    Path(__file__).resolve().parent.parent.parent.parent / "app" / "tests" / "mock_data"
)


class ImportAniList(TestCase):
    """Test importing media from AniList."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

    @patch("requests.Session.post")
    def test_import_anilist_public(self, mock_request):
        """Basic test importing anime and manga from AniList."""
        with Path(mock_path / "import_anilist.json").open() as file:
            anilist_response = json.load(file)
        mock_request.return_value.json.return_value = anilist_response

        anilist.importer(None, self.user, "new", "bloodthirstiness")

        self.assertEqual(Anime.objects.filter(user=self.user).count(), 4)
        self.assertEqual(Manga.objects.filter(user=self.user).count(), 3)
        self.assertEqual(
            Anime.objects.get(user=self.user, item__title="FLCL").status,
            Status.PAUSED.value,
        )
        self.assertEqual(
            Manga.objects.filter(user=self.user, item__title="One Punch-Man")
            .first()
            .score,
            9,
        )
        self.assertEqual(
            Anime.objects.get(user=self.user, item__title="FLCL")
            .history.first()
            .history_date,
            datetime(2025, 6, 4, 10, 11, 17, tzinfo=UTC),
        )

    @patch("requests.Session.post")
    def test_import_anilist_private(self, mock_request):
        """Basic test importing anime and manga from AniList."""
        with Path(mock_path / "import_anilist.json").open() as file:
            anilist_response = json.load(file)
        mock_request.return_value.json.return_value = anilist_response

        anilist.importer(
            helpers.encrypt("token"),
            self.user,
            "new",
            "username",
        )

        self.assertEqual(Anime.objects.filter(user=self.user).count(), 4)
        self.assertEqual(Manga.objects.filter(user=self.user).count(), 3)
        self.assertEqual(
            Anime.objects.get(user=self.user, item__title="FLCL").status,
            Status.PAUSED.value,
        )
        self.assertEqual(
            Manga.objects.filter(user=self.user, item__title="One Punch-Man")
            .first()
            .score,
            9,
        )
        self.assertEqual(
            Anime.objects.get(user=self.user, item__title="FLCL")
            .history.first()
            .history_date,
            datetime(2025, 6, 4, 10, 11, 17, tzinfo=UTC),
        )

    def test_user_not_found(self):
        """Test that an error is raised if the user is not found."""
        self.assertRaises(
            helpers.MediaImportError,
            anilist.importer,
            None,
            self.user,
            "new",
            "fhdsufdsu",
        )


class ImportAniListNewMode(TestCase):
    """Test that "new" mode only imports what is missing from Yamtrack."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

    def anime(self, status, progress, score=0, repeat=0, mal_id=1):
        """Return an AniList anime list entry."""
        completed = status == "COMPLETED"
        return {
            "media": {
                "title": {"userPreferred": "Cowboy Bebop"},
                "coverImage": {"large": "https://example.com/image.jpg"},
                "id": 1,
                "idMal": mal_id,
                "chapters": None,
                "episodes": 26,
            },
            "status": status,
            "score": score,
            "progress": progress,
            "startedAt": {"year": 2023, "month": 1, "day": 1},
            "completedAt": {
                "year": 2023 if completed else None,
                "month": 2 if completed else None,
                "day": 1 if completed else None,
            },
            "updatedAt": 1672531200,
            "repeat": repeat,
            "notes": None,
        }

    def run_import(self, anime):
        """Run an AniList import in "new" mode."""
        response = {
            "data": {
                "anime": {"lists": [{"isCustomList": False, "entries": [anime]}]},
                "manga": {"lists": []},
            },
        }
        with patch("app.providers.services.api_request", return_value=response):
            return anilist.importer(None, self.user, "new", "test")

    def test_existing_anime_is_synced(self):
        """An existing anime gets its progress, status, score and end date."""
        self.run_import(self.anime("CURRENT", 3))
        self.assertIsNone(Anime.objects.get(user=self.user).score)

        self.run_import(self.anime("COMPLETED", 26, score=8.5))

        anime = Anime.objects.get(user=self.user)
        self.assertEqual(anime.progress, 26)
        self.assertEqual(anime.status, Status.COMPLETED.value)
        self.assertEqual(float(anime.score), 8.5)
        self.assertEqual(timezone.localdate(anime.end_date), date(2023, 2, 1))

    def test_rewatch_is_added(self):
        """A rewatch adds a new entry once and is completed later."""
        self.run_import(self.anime("COMPLETED", 26, score=8))

        rewatch = self.anime("REPEATING", 3, score=9)
        self.run_import(rewatch)
        self.run_import(rewatch)

        entries = Anime.objects.filter(user=self.user).order_by("created_at")
        self.assertEqual(
            [(anime.status, anime.progress) for anime in entries],
            [(Status.COMPLETED.value, 26), (Status.IN_PROGRESS.value, 3)],
        )
        # The score tracked in Yamtrack is kept
        self.assertEqual(entries[0].score, 8)

        self.run_import(self.anime("COMPLETED", 26, repeat=1))

        entries = Anime.objects.filter(user=self.user)
        self.assertEqual(entries.count(), 2)
        self.assertTrue(
            all(anime.status == Status.COMPLETED.value for anime in entries),
        )

    def test_manga_reread_uses_chapters(self):
        """A finished manga reread is imported with the manga chapters."""
        manga = {
            "media": {
                "title": {"userPreferred": "Monster"},
                "coverImage": {"large": "https://example.com/image.jpg"},
                "idMal": 1,
                "chapters": 162,
            },
            "status": "COMPLETED",
            "score": 0,
            "progress": 162,
            "startedAt": {"year": None, "month": None, "day": None},
            "completedAt": {"year": None, "month": None, "day": None},
            "updatedAt": 1672531200,
            "repeat": 1,
            "notes": None,
        }
        response = {
            "data": {
                "anime": {"lists": []},
                "manga": {"lists": [{"isCustomList": False, "entries": [manga]}]},
            },
        }
        with patch(
            "app.providers.services.api_request",
            return_value=response,
        ) as mock_api_request:
            anilist.importer(None, self.user, "new", "test")

        query = mock_api_request.call_args.kwargs["params"]["query"]
        manga_query = query.split("manga: MediaListCollection")[1]
        self.assertIn("chapters", manga_query)

        entries = Manga.objects.filter(user=self.user)
        self.assertEqual([manga.progress for manga in entries], [162, 162])

    def test_anime_without_mal_id_uses_anilist(self):
        """An anime without a MAL ID is imported and synced with its AniList ID."""
        self.run_import(self.anime("CURRENT", 3, mal_id=None))

        anime = Anime.objects.get(user=self.user)
        self.assertEqual(anime.item.source, Sources.ANILIST.value)
        self.assertEqual(anime.item.media_id, "1")

        self.run_import(self.anime("COMPLETED", 26, mal_id=None))

        anime = Anime.objects.get(user=self.user)
        self.assertEqual(anime.progress, 26)
        self.assertEqual(anime.status, Status.COMPLETED.value)
