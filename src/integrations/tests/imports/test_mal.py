import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from app.models import (
    Anime,
    DeletedMedia,
    Item,
    Manga,
    MediaTypes,
    Movie,
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
    def test_unrated_entries_and_repeats_have_no_score(self, mock_request):
        responses = []
        for media_type in ("anime", "manga"):
            with Path(mock_path / f"import_mal_{media_type}.json").open() as file:
                response = json.load(file)
            status = response["data"][0]["list_status"]
            status["score"] = 0
            repeat_field = "num_times_rewatched" if media_type == "anime" else "num_times_reread"
            status[repeat_field] = 1
            mock_response = MagicMock()
            mock_response.json.return_value = response
            responses.append(mock_response)
        mock_request.side_effect = responses

        mal.importer("bloodthirstiness", self.user, "new")

        for model in (Anime, Manga):
            self.assertGreaterEqual(
                model.objects.filter(user=self.user, score__isnull=True).count(), 2
            )

    @patch("integrations.webhooks.anime_mappings.fetch_mapping_data")
    def test_unrated_anime_synced_to_movie_has_no_score(self, mock_mapping):
        mock_mapping.return_value = {"tmdb_movie:99999": {"mal:32253": {}}}
        item = Item.objects.create(
            media_id="99999",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Movie",
        )
        Movie.objects.create(user=self.user, item=item, score=5)
        importer = mal.MyAnimeListImporter("test", self.user, "overwrite")
        with Path(mock_path / "import_mal_anime.json").open() as file:
            entry = json.load(file)["data"][0]
        entry["list_status"]["score"] = 0

        importer._process_entry(entry, MediaTypes.ANIME.value)

        self.assertIsNone(importer.bulk_media[MediaTypes.MOVIE.value][0].score)

    @patch("requests.Session.get")
    def test_import_animelist(self, mock_request):
        """Basic test importing anime and manga from MyAnimeList."""
        with Path(mock_path / "import_mal_anime.json").open(encoding="utf-8") as file:
            anime_response = json.load(file)
        with Path(mock_path / "import_mal_manga.json").open(encoding="utf-8") as file:
            manga_response = json.load(file)

        anime_mock = MagicMock()
        anime_mock.json.return_value = anime_response
        manga_mock = MagicMock()
        manga_mock.json.return_value = manga_response
        mock_request.side_effect = [anime_mock, manga_mock]

        mal.importer("bloodthirstiness", self.user, "new")
        self.assertEqual(Anime.objects.filter(user=self.user).count(), 6)
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

        erased_item = Item.objects.get(
            media_id="31043",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
        )
        self.assertEqual(erased_item.title, "Erased")
        self.assertEqual(erased_item.localized_title, "Erased")
        self.assertEqual(erased_item.original_title, "Boku dake ga Inai Machi")

    @patch("requests.Session.get")
    def test_import_does_not_bring_back_a_deleted_anime(self, mock_request):
        """An anime the user deleted by hand stays deleted across syncs (#1133)."""
        with Path(mock_path / "import_mal_anime.json").open(encoding="utf-8") as file:
            anime_response = json.load(file)
        with Path(mock_path / "import_mal_manga.json").open(encoding="utf-8") as file:
            manga_response = json.load(file)
        anime_mock = MagicMock()
        anime_mock.json.return_value = anime_response
        manga_mock = MagicMock()
        manga_mock.json.return_value = manga_response
        mock_request.side_effect = [anime_mock, manga_mock]
        DeletedMedia.objects.create(
            user=self.user,
            media_type=MediaTypes.ANIME.value,
            source=Sources.MAL.value,
            media_id="31043",
        )

        mal.importer("bloodthirstiness", self.user, "new")

        self.assertEqual(Anime.objects.filter(user=self.user).count(), 5)
        self.assertFalse(
            Anime.objects.filter(user=self.user, item__media_id="31043").exists(),
        )

    @tag("network")
    def test_user_not_found(self):
        """Test that an error is raised if the user is not found."""
        self.assertRaises(
            helpers.MediaImportError,
            mal.importer,
            "fhdsufdsu",
            self.user,
            "new",
        )

    @patch("integrations.imports.mal.MyAnimeListImporter._get_whole_response")
    def test_private_manga_list_is_skipped_with_warning(self, mock_get_response):
        """A private manga list is skipped with a warning, anime still imports."""
        with Path(mock_path / "import_mal_anime.json").open(encoding="utf-8") as file:
            anime_response = json.load(file)

        forbidden_response = MagicMock()
        forbidden_response.status_code = requests.codes.forbidden
        forbidden_error = requests.exceptions.HTTPError(response=forbidden_response)

        mock_get_response.side_effect = [anime_response, forbidden_error]

        imported_counts, warnings = mal.importer("bloodthirstiness", self.user, "new")

        self.assertEqual(Anime.objects.filter(user=self.user).count(), 6)
        self.assertEqual(Manga.objects.filter(user=self.user).count(), 0)
        self.assertNotIn(MediaTypes.MANGA.value, imported_counts)
        self.assertIn("manga", warnings)
        self.assertIn("private", warnings)

    @patch("app.models.media.Media.process_status")
    @patch("integrations.webhooks.anime_mappings.fetch_mapping_data")
    @patch("requests.Session.get")
    def test_overwrite_syncs_to_existing_movie_entry(
        self, mock_request, mock_mapping, _mock_ps
    ):
        """MAL overwrite import also updates a corresponding Movie entry."""
        # MAL ID 32253 ("Ama Gli Animali", completed, score=7) maps to TMDB movie 99999
        mock_mapping.return_value = {"tmdb_movie:99999": {"mal:32253": {}}}

        movie_item = Item.objects.create(
            media_id="99999",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Ama Gli Animali",
            image="http://example.com/img.jpg",
        )
        Movie.objects.create(
            item=movie_item,
            user=self.user,
            progress=1,
            status=Status.COMPLETED.value,
            score=5,  # stale score — should be overwritten
        )

        with Path(mock_path / "import_mal_anime.json").open(encoding="utf-8") as file:
            anime_response = json.load(file)
        with Path(mock_path / "import_mal_manga.json").open(encoding="utf-8") as file:
            manga_response = json.load(file)

        anime_mock = MagicMock()
        anime_mock.json.return_value = anime_response
        manga_mock = MagicMock()
        manga_mock.json.return_value = manga_response
        mock_request.side_effect = [anime_mock, manga_mock]

        mal.importer("bloodthirstiness", self.user, "overwrite")

        movie = Movie.objects.get(item=movie_item, user=self.user)
        self.assertEqual(int(movie.score), 7)  # updated from MAL (was 5)
        self.assertEqual(movie.status, Status.IN_PROGRESS.value)  # is_rewatching=True
