from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import Game, Item, MediaTypes, Sources, Status
from integrations.imports import steam


@patch("integrations.imports.steam.services.api_request")
@patch("integrations.imports.steam.external_game")
@patch("integrations.imports.steam.services.get_media_metadata")
class ImportSteamUpdate(TestCase):
    """Test updating existing media from Steam."""

    def setUp(self):
        """Create user and common data for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        self.item = Item.objects.create(
            media_id="1",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Counter-Strike 2",
            image="http://example.com/cs2.jpg",
        )

    def _create_game(self, status=Status.PLANNING.value, progress=0):
        """Helper to create a game with specific status and progress."""
        return Game.objects.create(
            item=self.item,
            user=self.user,
            status=status,
            progress=progress,
        )

    def _setup_mocks(
        self,
        mock_get_metadata,
        mock_external_game,
        mock_api_request,
        playtime=1300,
    ):
        """Helper to setup common mocks."""
        mock_get_metadata.return_value = {
            "title": "Counter-Strike 2",
            "image": "http://example.com/cs2.jpg",
            "max_progress": None,
        }
        mock_external_game.return_value = 1

        mock_api_request.return_value = {
            "response": {
                "games": [
                    {
                        "appid": 730,
                        "name": "Counter-Strike 2",
                        "playtime_forever": playtime,
                        "playtime_2weeks": 120,
                        "rtime_last_played": 1704067200,
                    },
                ],
            },
        }

    def test_update_steam_game(
        self,
        mock_get_metadata,
        mock_external_game,
        mock_api_request,
    ):
        """Test updating an existing game from Steam."""
        self._setup_mocks(mock_get_metadata, mock_external_game, mock_api_request)
        game = self._create_game()

        steam.importer("76561198000000000", self.user, "update")

        game.refresh_from_db()
        self.assertEqual(game.progress, 1300)
        self.assertEqual(game.status, Status.IN_PROGRESS.value)

    def test_update_steam_game_completed_status(
        self,
        mock_get_metadata,
        mock_external_game,
        mock_api_request,
    ):
        """Test that completed games are not updated to other statuses."""
        self._setup_mocks(
            mock_get_metadata,
            mock_external_game,
            mock_api_request,
            playtime=1100,
        )
        game = self._create_game(status=Status.COMPLETED.value, progress=1000)

        steam.importer("76561198000000000", self.user, "update")

        game.refresh_from_db()
        self.assertEqual(game.progress, 1100)
        self.assertEqual(game.status, Status.COMPLETED.value)

    def test_new_mode_skips_update(
        self,
        mock_get_metadata,
        mock_external_game,
        mock_api_request,
    ):
        """Test that 'new' mode skips updating existing games."""
        self._setup_mocks(mock_get_metadata, mock_external_game, mock_api_request)
        game = self._create_game()

        steam.importer("76561198000000000", self.user, "new")

        game.refresh_from_db()
        self.assertEqual(game.progress, 0)
        self.assertEqual(game.status, Status.PLANNING.value)

