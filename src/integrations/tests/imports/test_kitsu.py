import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import (
    Anime,
    Manga,
    MediaTypes,
    Status,
)
from integrations.imports import (
    kitsu,
)

mock_path = Path(__file__).resolve().parent.parent / "mock_data"
app_mock_path = (
    Path(__file__).resolve().parent.parent.parent.parent / "app" / "tests" / "mock_data"
)


class ImportKitsu(TestCase):
    """Test importing media from Kitsu."""

    def setUp(self):
        """Create user for the tests."""
        credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**credentials)

        with Path(mock_path / "import_kitsu_anime.json").open() as file:
            self.sample_anime_response = json.load(file)

        with Path(mock_path / "import_kitsu_manga.json").open() as file:
            self.sample_manga_response = json.load(file)

        self.importer = kitsu.KitsuImporter("testuser", self.user, "new")

    @patch("app.providers.services.api_request")
    def test_get_kitsu_id(self, mock_api_request):
        """Test getting Kitsu ID from username."""
        mock_api_request.return_value = {
            "data": [{"id": "12345"}],
        }
        kitsu_id = self.importer._get_kitsu_id("testuser")
        self.assertEqual(kitsu_id, "12345")

    @patch("app.providers.services.api_request")
    def test_get_media_response(self, mock_api_request):
        """Test getting media response from Kitsu."""
        mock_api_request.side_effect = [
            self.sample_anime_response,
            self.sample_manga_response,
        ]

        imported_counts, warning_message = kitsu.importer(
            "123",
            self.user,
            "new",
        )
        self.assertEqual(imported_counts[MediaTypes.ANIME.value], 6)
        self.assertEqual(imported_counts[MediaTypes.MANGA.value], 6)
        self.assertEqual(warning_message, "")

        self.assertEqual(Anime.objects.count(), 6)
        self.assertEqual(Manga.objects.count(), 6)
        self.assertEqual(
            Anime.objects.get(item__title="Test Anime 2").history.first().history_date,
            datetime(2024, 4, 8, 16, 16, 59, 18000, tzinfo=UTC),
        )

    def test_get_rating(self):
        """Test getting rating from Kitsu."""
        self.assertEqual(self.importer._get_rating(20), 10)
        self.assertEqual(self.importer._get_rating(10), 5)
        self.assertEqual(self.importer._get_rating(1), 0.5)
        self.assertIsNone(self.importer._get_rating(None))

    def test_get_status(self):
        """Test getting status from Kitsu."""
        self.assertEqual(self.importer._get_status("completed"), Status.COMPLETED.value)
        self.assertEqual(self.importer._get_status("current"), Status.IN_PROGRESS.value)
        self.assertEqual(self.importer._get_status("planned"), Status.PLANNING.value)
        self.assertEqual(self.importer._get_status("on_hold"), Status.PAUSED.value)

    def test_process_entry(self):
        """Test processing an entry from Kitsu."""
        entry = self.sample_anime_response["data"][0]
        media_lookup = {
            item["id"]: item
            for item in self.sample_anime_response["included"]
            if item["type"] == "anime"
        }
        mapping_lookup = {
            item["id"]: item
            for item in self.sample_anime_response["included"]
            if item["type"] == "mappings"
        }

        self.importer._process_entry(
            entry,
            MediaTypes.ANIME.value,
            media_lookup,
            mapping_lookup,
        )

        instance = self.importer.bulk_media[MediaTypes.ANIME.value][0]

        self.assertEqual(instance.item.media_id, "1")
        self.assertIsInstance(instance, Anime)
        self.assertEqual(instance.score, 9)
        self.assertEqual(instance.progress, 26)
        self.assertEqual(instance.status, Status.COMPLETED.value)
        self.assertEqual(instance.notes, "Great series!")


class ImportKitsuNewMode(TestCase):
    """Test that "new" mode only imports what is missing from Yamtrack."""

    def setUp(self):
        """Create user for the tests."""
        credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**credentials)

    def anime(self, status, progress, rating=None, reconsume_count=0, **kwargs):
        """Return a Kitsu library entry of an anime."""
        return {
            "attributes": {
                "status": status,
                "progress": progress,
                "ratingTwenty": rating,
                "startedAt": "2023-01-01T00:00:00.000Z",
                "finishedAt": (
                    "2023-02-01T00:00:00.000Z" if status == "completed" else None
                ),
                "notes": None,
                "reconsumeCount": reconsume_count,
                "reconsuming": kwargs.get("reconsuming", False),
                "updatedAt": "2023-02-01T00:00:00.000Z",
            },
            "relationships": {"anime": {"data": {"id": "1"}}},
        }

    def run_import(self, anime):
        """Run a Kitsu import in "new" mode."""
        response = {
            "entries": [anime],
            "included": [
                {
                    "id": "1",
                    "type": "anime",
                    "attributes": {
                        "canonicalTitle": "Cowboy Bebop",
                        "posterImage": {"medium": "https://example.com/image.jpg"},
                        "episodeCount": 26,
                    },
                    "relationships": {"mappings": {"data": [{"id": "10"}]}},
                },
                {
                    "id": "10",
                    "type": "mappings",
                    "attributes": {
                        "externalSite": "myanimelist/anime",
                        "externalId": "1",
                    },
                },
            ],
        }
        with patch.object(
            kitsu.KitsuImporter,
            "_get_media_response",
            side_effect=lambda media_type: (
                response
                if media_type == MediaTypes.ANIME.value
                else {"entries": [], "included": []}
            ),
        ):
            return kitsu.importer("1", self.user, "new")

    def test_existing_anime_is_synced(self):
        """An existing anime gets its progress, status and score."""
        self.run_import(self.anime("current", 3))

        self.run_import(self.anime("completed", 26, rating=17))

        anime = Anime.objects.get(user=self.user)
        self.assertEqual(anime.progress, 26)
        self.assertEqual(anime.status, Status.COMPLETED.value)
        self.assertEqual(float(anime.score), 8.5)
        self.assertEqual(anime.end_date, datetime(2023, 2, 1, tzinfo=UTC))

    def test_reconsuming_adds_completed_entry(self):
        """Reconsuming for the first time keeps the first watch as completed."""
        self.run_import(self.anime("completed", 26))

        rewatch = self.anime("current", 3, reconsuming=True)
        self.run_import(rewatch)
        self.run_import(rewatch)

        entries = Anime.objects.filter(user=self.user).order_by("created_at")
        self.assertEqual(
            [(anime.status, anime.progress) for anime in entries],
            [(Status.COMPLETED.value, 26), (Status.IN_PROGRESS.value, 3)],
        )

    def test_fresh_import_of_reconsuming_entry(self):
        """A fresh import of a reconsumed entry adds the first watch as completed."""
        self.run_import(self.anime("current", 3, reconsuming=True))

        entries = Anime.objects.filter(user=self.user).order_by("created_at")
        self.assertEqual(
            [(anime.status, anime.progress) for anime in entries],
            [(Status.COMPLETED.value, 26), (Status.IN_PROGRESS.value, 3)],
        )
