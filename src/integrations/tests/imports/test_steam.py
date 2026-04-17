from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from requests import Response
from requests.exceptions import HTTPError

from app.models import (
    Game,
    MediaTypes,
    Sources,
    Status,
)
from integrations.imports import (
    helpers,
    steam,
)

mock_path = Path(__file__).resolve().parent.parent / "mock_data"
app_mock_path = (
    Path(__file__).resolve().parent.parent.parent.parent / "app" / "tests" / "mock_data"
)


class ImportSteam(TestCase):
    """Test importing media from Steam."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

    @patch("integrations.imports.steam.services.api_request")
    @patch("integrations.imports.steam.external_game")
    @patch("integrations.imports.steam.services.get_media_metadata")
    def test_import_steam_games(
        self,
        mock_get_metadata,
        mock_external_game,
        mock_api_request,
    ):
        """Test importing games from Steam."""
        mock_api_request.return_value = {
            "response": {
                "games": [
                    {
                        "appid": 730,
                        "name": "Counter-Strike 2",
                        "playtime_forever": 1250,
                        "playtime_2weeks": 120,  # Recent activity
                        "rtime_last_played": 1704067200,  # Recent timestamp
                    },
                    {
                        "appid": 570,
                        "name": "Dota 2",
                        "playtime_forever": 0,  # Never played
                        "playtime_2weeks": 0,  # No recent activity
                    },
                    {
                        "appid": 440,
                        "name": "Team Fortress 2",
                        "playtime_forever": 500,
                        "playtime_2weeks": 0,  # No recent activity
                        "rtime_last_played": 1672531200,  # Old timestamp (over 14 days)
                    },
                ],
            },
        }

        mock_external_game.side_effect = [1, 2, 3]  # IGDB game IDs for each Steam app

        mock_get_metadata.side_effect = [
            {"title": "Counter-Strike 2", "image": "http://example.com/cs2.jpg"},
            {"title": "Dota 2", "image": "http://example.com/dota2.jpg"},
            {"title": "Team Fortress 2", "image": "http://example.com/tf2.jpg"},
        ]

        imported_counts, _ = steam.importer(
            "76561198000000000",
            self.user,
            "new",
        )

        self.assertEqual(imported_counts[MediaTypes.GAME.value], 3)

        games = Game.objects.filter(user=self.user)
        self.assertEqual(games.count(), 3)

        cs2_game = games.get(item__title="Counter-Strike 2")
        self.assertEqual(cs2_game.status, Status.IN_PROGRESS.value)
        self.assertEqual(cs2_game.progress, 1250)

        dota_game = games.get(item__title="Dota 2")
        self.assertEqual(dota_game.status, Status.PLANNING.value)
        self.assertEqual(dota_game.progress, 0)

        tf2_game = games.get(item__title="Team Fortress 2")
        self.assertEqual(tf2_game.status, Status.PAUSED.value)
        self.assertEqual(tf2_game.progress, 500)

    @patch("integrations.imports.steam.services.api_request")
    def test_import_steam_private_profile(self, mock_api_request):
        """Test handling of private Steam profile."""
        response = Response()
        response.status_code = 403
        mock_api_request.side_effect = HTTPError(response=response)

        with self.assertRaises(helpers.MediaImportError) as context:
            steam.importer("76561198000000000", self.user, "new")

        self.assertIn("private or invalid", str(context.exception))

    @patch("integrations.imports.steam.services.api_request")
    @patch("integrations.imports.steam.external_game")
    def test_import_steam_game_not_found_in_igdb(
        self,
        mock_external_game,
        mock_api_request,
    ):
        """Test handling of games not found in IGDB."""
        mock_api_request.return_value = {
            "response": {
                "games": [
                    {
                        "appid": 999,
                        "name": "Unknown Game",
                        "playtime_forever": 100,
                        "playtime_2weeks": 0,
                    },
                ],
            },
        }

        mock_external_game.return_value = None

        imported_counts, warnings = steam.importer(
            "76561198000000000",
            self.user,
            "new",
        )

        self.assertEqual(imported_counts.get(MediaTypes.GAME.value, 0), 0)

        self.assertIn("Unknown Game (999)", warnings)
        self.assertIn(f"Couldn't find a match in {Sources.IGDB.label}", warnings)

        self.assertEqual(Game.objects.filter(user=self.user).count(), 0)

    def test_determine_game_status_logic(self):
        """Test the status determination logic."""
        importer_instance = steam.SteamImporter("76561198000000000", self.user, "new")

        status = importer_instance._determine_game_status(0, 0)
        self.assertEqual(status, Status.PLANNING.value)

        status = importer_instance._determine_game_status(100, 50)
        self.assertEqual(status, Status.IN_PROGRESS.value)

        status = importer_instance._determine_game_status(100, 0)
        self.assertEqual(status, Status.PAUSED.value)

        status = importer_instance._determine_game_status(100, 0)
        self.assertEqual(status, Status.PAUSED.value)

    @patch("integrations.imports.steam.services.api_request")
    def test_import_steam_no_api_key(self, _mock_api_request):
        """Test handling when Steam API key is not configured."""
        with patch.object(settings, "STEAM_API_KEY", ""):
            with self.assertRaises(helpers.MediaImportError) as context:
                steam.importer("76561198000000000", self.user, "new")

            self.assertIn("Steam API key not configured", str(context.exception))
