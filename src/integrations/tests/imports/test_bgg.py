from pathlib import Path
from unittest.mock import patch

from defusedxml import ElementTree
from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import BoardGame, Item, MediaTypes, Sources, Status
from integrations.imports import bgg

mock_path = Path(__file__).resolve().parent.parent / "mock_data"


class ImportBGG(TestCase):
    """Test importing board games from BoardGameGeek."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

    def _create_boardgame(self, status=Status.PLANNING.value, progress=0, score=None):
        """Create an existing board game for the user."""
        item, _ = Item.objects.get_or_create(
            media_id="1",
            source=Sources.BGG.value,
            media_type=MediaTypes.BOARDGAME.value,
            defaults={
                "title": "Catan",
                "image": "http://example.com/catan.jpg",
            },
        )
        return BoardGame.objects.create(
            item=item,
            user=self.user,
            status=status,
            progress=progress,
            score=score,
        )

    @patch("app.providers.services.api_request")
    def test_import_bgg_boardgames(self, mock_api_request):
        """Test importing board games from a BGG collection."""
        with Path(mock_path / "import_bgg.xml").open() as file:
            mock_api_request.return_value = ElementTree.fromstring(file.read())

        imported_counts, _ = bgg.importer("someuser", self.user, "new")

        self.assertEqual(imported_counts[MediaTypes.BOARDGAME.value], 3)

        games = BoardGame.objects.filter(user=self.user)
        self.assertEqual(games.count(), 3)

        catan = games.get(item__title="Catan")
        self.assertEqual(catan.status, Status.IN_PROGRESS.value)
        self.assertEqual(catan.score, 8)
        self.assertEqual(catan.progress, 5)
        self.assertEqual(catan.item.image, "http://example.com/catan.jpg")

        carcassonne = games.get(item__title="Carcassonne")
        self.assertEqual(carcassonne.status, Status.PLANNING.value)
        self.assertIsNone(carcassonne.score)
        self.assertEqual(carcassonne.progress, 0)
        self.assertEqual(
            carcassonne.item.image,
            "http://example.com/carcassonne_thumb.jpg",
        )

        ttr = games.get(item__title="Ticket to Ride")
        self.assertEqual(ttr.status, Status.DROPPED.value)
        self.assertEqual(ttr.score, 7)
        self.assertEqual(ttr.progress, 3)

    @patch("app.providers.services.api_request")
    def test_new_mode_skips_existing_boardgame(self, mock_api_request):
        """Test that new mode skips a board game that already exists."""
        with Path(mock_path / "import_bgg.xml").open() as file:
            mock_api_request.return_value = ElementTree.fromstring(file.read())
        game = self._create_boardgame(progress=2)

        imported_counts, _ = bgg.importer("someuser", self.user, "new")

        game.refresh_from_db()
        self.assertEqual(imported_counts[MediaTypes.BOARDGAME.value], 2)
        self.assertEqual(BoardGame.objects.filter(user=self.user).count(), 3)
        self.assertEqual(game.progress, 2)
        self.assertEqual(game.status, Status.PLANNING.value)

    @patch("app.providers.services.api_request")
    def test_overwrite_mode_replaces_existing_boardgame(self, mock_api_request):
        """Test that overwrite mode replaces an existing board game."""
        with Path(mock_path / "import_bgg.xml").open() as file:
            mock_api_request.return_value = ElementTree.fromstring(file.read())
        self._create_boardgame(progress=2, score=5)

        imported_counts, _ = bgg.importer("someuser", self.user, "overwrite")

        self.assertEqual(imported_counts[MediaTypes.BOARDGAME.value], 3)
        games = BoardGame.objects.filter(user=self.user)
        self.assertEqual(games.count(), 3)

        catan = games.get(item__title="Catan")
        self.assertEqual(catan.status, Status.IN_PROGRESS.value)
        self.assertEqual(catan.score, 8)
        self.assertEqual(catan.progress, 5)
