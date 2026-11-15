import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from app.models import (
    Anime,
    DeletedMedia,
    Manga,
    MediaTypes,
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
    def test_unrated_entries_and_repeats_have_no_score(self, mock_request):
        with Path(mock_path / "import_anilist.json").open() as file:
            response = json.load(file)
        for media_type in ("anime", "manga"):
            entry = response["data"][media_type]["lists"][0]["entries"][0]
            entry["score"] = 0
            entry["repeat"] = 1
            importer = anilist.AniListImporter(None, self.user, "new", "test")
            importer._process_entry(entry, media_type)
            self.assertEqual(
                [row.score for row in importer.bulk_media[media_type]], [None, None]
            )
        mock_request.return_value.json.return_value = response

        anilist.importer(None, self.user, "new", "bloodthirstiness")

        for model in (Anime, Manga):
            rows = model.objects.filter(user=self.user, score__isnull=True)
            self.assertGreaterEqual(rows.count(), 1)

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
    def test_import_does_not_bring_back_a_deleted_anime(self, mock_request):
        """An anime the user deleted by hand stays deleted across syncs (#1133)."""
        with Path(mock_path / "import_anilist.json").open() as file:
            anilist_response = json.load(file)
        mock_request.return_value.json.return_value = anilist_response
        DeletedMedia.objects.create(
            user=self.user,
            media_type=MediaTypes.ANIME.value,
            source=Sources.MAL.value,
            media_id="227",
        )

        anilist.importer(None, self.user, "new", "bloodthirstiness")

        self.assertEqual(Anime.objects.filter(user=self.user).count(), 3)
        self.assertFalse(
            Anime.objects.filter(user=self.user, item__title="FLCL").exists(),
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

    @tag("network")
    def test_user_not_found(self):
        """Test that an error is raised if the user is not found.

        Unlike its neighbours this one does not patch the session, so it asks
        AniList about a made-up user for real. AniList answers a bare 403 when
        it feels like it, which surfaces as the wrong exception type and fails
        the run for reasons that have nothing to do with the change under test.
        """
        self.assertRaises(
            helpers.MediaImportError,
            anilist.importer,
            None,
            self.user,
            "new",
            "fhdsufdsu",
        )
