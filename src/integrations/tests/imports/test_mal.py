import json
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from app.models import (
    Anime,
    Item,
    Manga,
    MediaTypes,
    Sources,
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


class ImportMALNewMode(TestCase):
    """Test that "new" mode only imports what is missing from Yamtrack."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

    def anime(self, status, watched, score=0, rewatching=False, rewatched=0):  # noqa: FBT002
        """Return a MyAnimeList anime list entry."""
        return {
            "node": {
                "id": 1,
                "title": "Cowboy Bebop",
                "main_picture": {"large": "https://example.com/image.jpg"},
                "num_episodes": 26,
            },
            "list_status": {
                "status": status,
                "score": score,
                "num_episodes_watched": watched,
                "is_rewatching": rewatching,
                "num_times_rewatched": rewatched,
                "updated_at": "2023-01-01T00:00:00+00:00",
                "start_date": "2023-01-01",
                "finish_date": "2023-02-01" if status == "completed" else None,
                "comments": "",
            },
        }

    def run_import(self, anime):
        """Run a MyAnimeList import in "new" mode."""
        with patch.object(
            mal.MyAnimeListImporter,
            "_get_whole_response",
            side_effect=lambda url, _: {
                "data": [anime] if url.endswith("/animelist") else [],
            },
        ):
            return mal.importer("test", self.user, "new")

    def test_existing_anime_is_synced(self):
        """An existing anime gets its progress, status, score and end date."""
        self.run_import(self.anime("watching", 3))
        self.assertIsNone(Anime.objects.get(user=self.user).score)

        imported_counts, _ = self.run_import(self.anime("completed", 26, score=8))

        anime = Anime.objects.get(user=self.user)
        self.assertEqual(anime.progress, 26)
        self.assertEqual(anime.status, Status.COMPLETED.value)
        self.assertEqual(anime.score, 8)
        self.assertEqual(timezone.localdate(anime.start_date), date(2023, 1, 1))
        self.assertEqual(timezone.localdate(anime.end_date), date(2023, 2, 1))
        self.assertEqual(imported_counts, {})

    def test_existing_anime_keeps_tracked_values(self):
        """The status and score set in Yamtrack are not overwritten."""
        item = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Cowboy Bebop",
            image="https://example.com/image.jpg",
        )
        Anime.objects.bulk_create(
            [
                Anime(
                    item=item,
                    user=self.user,
                    status=Status.PAUSED.value,
                    progress=5,
                    score=6,
                ),
            ],
        )

        self.run_import(self.anime("completed", 26, score=9))

        anime = Anime.objects.get(user=self.user)
        self.assertEqual(anime.status, Status.PAUSED.value)
        self.assertEqual(anime.score, 6)
        self.assertEqual(anime.progress, 26)
        self.assertIsNone(anime.end_date)

    def test_rewatch_is_added(self):
        """A rewatch adds a new entry once and is completed later."""
        self.run_import(self.anime("completed", 26))

        rewatch = self.anime("completed", 3, rewatching=True)
        self.run_import(rewatch)
        self.run_import(rewatch)

        entries = Anime.objects.filter(user=self.user).order_by("created_at")
        self.assertEqual(
            [(anime.status, anime.progress) for anime in entries],
            [(Status.COMPLETED.value, 26), (Status.IN_PROGRESS.value, 3)],
        )

        self.run_import(self.anime("completed", 26, rewatched=1))

        entries = Anime.objects.filter(user=self.user).order_by("created_at")
        self.assertEqual(
            [(anime.status, anime.progress) for anime in entries],
            [(Status.COMPLETED.value, 26), (Status.COMPLETED.value, 26)],
        )

    def create_paused_anime(self):
        """Create an existing anime paused at episode 5."""
        item = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Cowboy Bebop",
            image="https://example.com/image.jpg",
        )
        Anime.objects.bulk_create(
            [
                Anime(
                    item=item,
                    user=self.user,
                    status=Status.PAUSED.value,
                    progress=5,
                ),
            ],
        )

    def test_paused_entry_is_completed_by_rewatch(self):
        """A paused entry is the finished first watch of a completed rewatch."""
        self.create_paused_anime()

        self.run_import(self.anime("completed", 26, rewatched=1))

        entries = Anime.objects.filter(user=self.user).order_by("created_at")
        self.assertEqual(
            [(anime.status, anime.progress) for anime in entries],
            [(Status.COMPLETED.value, 26), (Status.COMPLETED.value, 26)],
        )

    def test_paused_rewatch_is_the_main_entry(self):
        """A paused rewatch is added as the main entry after the finished one."""
        self.create_paused_anime()

        self.run_import(self.anime("on_hold", 5, rewatched=1))

        entries = Anime.objects.filter(user=self.user).order_by("created_at")
        self.assertEqual(
            [(anime.status, anime.progress) for anime in entries],
            [(Status.COMPLETED.value, 26), (Status.PAUSED.value, 5)],
        )
        # The details page shows the newest entry as the main one
        self.assertEqual(
            Anime.objects.filter(user=self.user).first().status,
            Status.PAUSED.value,
        )

    def test_missing_repeats_are_added(self):
        """Repeats finished outside Yamtrack are added as completed entries."""
        self.run_import(self.anime("watching", 10))

        self.run_import(self.anime("completed", 26, rewatched=2))

        entries = Anime.objects.filter(user=self.user)
        self.assertEqual(entries.count(), 3)
        self.assertTrue(
            all(anime.status == Status.COMPLETED.value for anime in entries),
        )
        self.assertTrue(all(anime.progress == 26 for anime in entries))  # noqa: PLR2004
