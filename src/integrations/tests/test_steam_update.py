from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import Game, Item, MediaTypes, Sources, Status
from integrations.imports import steam


@patch("integrations.imports.steam.services.api_request")
@patch("integrations.imports.steam.external_game")
@patch("integrations.imports.steam.services.get_media_metadata")
class ImportSteamUpdate(TestCase):
    """Test Steam overwrite behavior for existing games."""

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
        """Create a game with a specific status and progress."""
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
        """Set up the common Steam and IGDB mocks."""
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

    def test_overwrite_steam_game_updates_existing(
        self,
        mock_get_metadata,
        mock_external_game,
        mock_api_request,
    ):
        """Test overwrite mode updates an existing game instead of recreating it."""
        self._setup_mocks(mock_get_metadata, mock_external_game, mock_api_request)
        game = self._create_game()

        imported_counts, _ = steam.importer(
            "76561198000000000",
            self.user,
            "overwrite",
        )

        game.refresh_from_db()
        self.assertEqual(imported_counts[MediaTypes.GAME.value], 1)
        self.assertEqual(Game.objects.filter(user=self.user).count(), 1)
        self.assertEqual(game.progress, 1300)
        self.assertEqual(game.status, Status.IN_PROGRESS.value)
        self.assertEqual(game.history.count(), 2)

    def test_overwrite_steam_game_completed_status(
        self,
        mock_get_metadata,
        mock_external_game,
        mock_api_request,
    ):
        """Test overwrite mode does not downgrade completed games."""
        self._setup_mocks(
            mock_get_metadata,
            mock_external_game,
            mock_api_request,
            playtime=1100,
        )
        game = self._create_game(status=Status.COMPLETED.value, progress=1000)

        imported_counts, _ = steam.importer(
            "76561198000000000",
            self.user,
            "overwrite",
        )

        game.refresh_from_db()
        self.assertEqual(imported_counts[MediaTypes.GAME.value], 1)
        self.assertEqual(game.progress, 1100)
        self.assertEqual(game.status, Status.COMPLETED.value)

    def test_overwrite_updates_newest_game_instance(
        self,
        mock_get_metadata,
        mock_external_game,
        mock_api_request,
    ):
        """Test overwrite mode updates the newest game instance."""
        self._setup_mocks(mock_get_metadata, mock_external_game, mock_api_request)
        older_game = self._create_game(progress=100)
        newer_game = self._create_game(progress=200)

        imported_counts, _ = steam.importer(
            "76561198000000000",
            self.user,
            "overwrite",
        )

        older_game.refresh_from_db()
        newer_game.refresh_from_db()
        self.assertEqual(imported_counts[MediaTypes.GAME.value], 1)
        self.assertEqual(older_game.progress, 100)
        self.assertEqual(older_game.status, Status.PLANNING.value)
        self.assertEqual(newer_game.progress, 1300)
        self.assertEqual(newer_game.status, Status.IN_PROGRESS.value)

    def test_new_mode_skips_existing_game(
        self,
        mock_get_metadata,
        mock_external_game,
        mock_api_request,
    ):
        """Test that new mode still skips existing games."""
        self._setup_mocks(mock_get_metadata, mock_external_game, mock_api_request)
        game = self._create_game()

        imported_counts, _ = steam.importer("76561198000000000", self.user, "new")

        game.refresh_from_db()
        self.assertEqual(imported_counts, {})
        self.assertEqual(game.progress, 0)
        self.assertEqual(game.status, Status.PLANNING.value)
