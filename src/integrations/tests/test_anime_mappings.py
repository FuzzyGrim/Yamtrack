import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from integrations.webhooks import anime_mappings


class AnimeMappingsTests(TestCase):
    """Tests for AniBridge mapping resolution."""

    def test_fetch_mapping_data_downloads_mapping_data(self):
        """Test mapping data downloads from AniBridge."""
        mapping_data = anime_mappings.fetch_mapping_data()

        self.assertIn("tvdb_show:74796:s17", mapping_data)
        self.assertIn("anidb:3651:R", mapping_data)

    def test_bleach_thousand_year_blood_war_part_two(self):
        """Test Bleach S17E14 maps to Thousand-Year Blood War part two."""
        mal_id, episode_number = anime_mappings.get_mal_id_from_tvdb(
            anime_mappings.fetch_mapping_data(),
            74796,
            17,
            14,
        )

        self.assertEqual(mal_id, 53998)
        self.assertEqual(episode_number, 1)

    def test_bleach_season_one_maps_directly(self):
        """Test Bleach S01E09 maps directly to original Bleach progress."""
        mal_id, episode_number = anime_mappings.get_mal_id_from_tvdb(
            anime_mappings.fetch_mapping_data(),
            74796,
            1,
            9,
        )

        self.assertEqual(mal_id, 269)
        self.assertEqual(episode_number, 9)

    def test_bleach_season_two_maps_to_continuing_mal_progress(self):
        """Test Bleach S02E02 maps to episode 22 of original Bleach."""
        mal_id, episode_number = anime_mappings.get_mal_id_from_tvdb(
            anime_mappings.fetch_mapping_data(),
            74796,
            2,
            2,
        )

        self.assertEqual(mal_id, 269)
        self.assertEqual(episode_number, 22)


class AnimeMappingsWebhookPayloadTests(TestCase):
    """Tests webhook payloads using the real anime_mappings module."""

    def setUp(self):
        """Set up a user for webhook tests."""
        self.client = Client()
        self.credentials = {
            "username": "testuser",
            "token": "test-token",
            "plex_usernames": "testuser",
            "anime_enabled": True,
        }
        self.user = get_user_model().objects.create_superuser(**self.credentials)

    @patch("integrations.webhooks.base.BaseWebhookProcessor._handle_anime")
    @patch("integrations.webhooks.base.tvdb_provider.episode")
    def test_jellyfin_bleach_payload_uses_tvdb_mapping(
        self,
        mock_tvdb_episode,
        mock_handle_anime,
    ):
        """Test Jellyfin TVDB payload maps Bleach S17E14 to MAL episode 1."""
        mock_tvdb_episode.return_value = {
            "episode_id": 999,
            "series_id": 74796,
            "season_number": 17,
            "episode_number": 14,
        }
        payload = {
            "Event": "Stop",
            "Item": {
                "Type": "Episode",
                "Name": "The Last 9 Days",
                "ProviderIds": {
                    "Tvdb": "999",
                },
                "UserData": {"Played": True},
                "SeriesName": "Bleach",
                "ParentIndexNumber": 17,
                "IndexNumber": 14,
            },
        }

        response = self.client.post(
            reverse("jellyfin_webhook", kwargs={"token": "test-token"}),
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        mock_tvdb_episode.assert_called_once_with(999)
        mock_handle_anime.assert_called_once_with(53998, 1, payload, self.user)

    @patch("integrations.webhooks.base.BaseWebhookProcessor._handle_anime")
    def test_plex_anidb_guid_payload_uses_anidb_mapping(self, mock_handle_anime):
        """Test Plex Hama AniDB GUID maps through anime_mappings."""
        payload = {
            "event": "media.scrobble",
            "Account": {"title": "testuser"},
            "Metadata": {
                "type": "episode",
                "index": 1,
                "parentIndex": 1,
                "guid": "com.plexapp.agents.hama://anidb-3651/1/1?lang=en",
            },
        }

        response = self.client.post(
            reverse("plex_webhook", kwargs={"token": "test-token"}),
            data={"payload": json.dumps(payload)},
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)
        mock_handle_anime.assert_called_once_with(849, 1, payload, self.user)
