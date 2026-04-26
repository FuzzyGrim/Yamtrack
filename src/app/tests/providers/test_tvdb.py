from unittest.mock import patch

from django.test import TestCase, override_settings

from app.providers import tvdb


class TVDBProviderTests(TestCase):
    """Tests for TVDB provider helpers."""

    @override_settings(TVDB_API="test-tvdb-key", TVDB_PIN="")
    @patch("app.providers.tvdb.cache")
    @patch("app.providers.tvdb.services.api_request")
    def test_get_access_token_logs_in_and_caches_token(
        self,
        mock_api_request,
        mock_cache,
    ):
        """Test TVDB login returns and caches a bearer token."""
        mock_cache.get.return_value = None
        mock_api_request.return_value = {
            "data": {
                "token": "test-token",
            },
        }

        result = tvdb.get_access_token()

        self.assertEqual(result, "test-token")
        mock_api_request.assert_called_once_with(
            tvdb.PROVIDER,
            "POST",
            f"{tvdb.BASE_URL}/login",
            params={"apikey": "test-tvdb-key"},
        )
        mock_cache.set.assert_called_once_with(
            tvdb.ACCESS_TOKEN_CACHE_KEY,
            "test-token",
            tvdb.ACCESS_TOKEN_TIMEOUT,
        )

    @patch("app.providers.tvdb.cache")
    @patch("app.providers.tvdb.get_access_token")
    @patch("app.providers.tvdb.services.api_request")
    def test_episode_queries_episode_endpoint(
        self,
        mock_api_request,
        mock_get_access_token,
        mock_cache,
    ):
        """Test TVDB episode lookup returns normalized episode metadata."""
        mock_cache.get.return_value = None
        mock_get_access_token.return_value = "test-token"
        mock_api_request.return_value = {
            "data": {
                "id": 12345,
                "seriesId": 74796,
                "seasonNumber": 2,
                "number": 2,
                "absoluteNumber": 22,
            },
        }

        result = tvdb.episode(12345)

        self.assertEqual(
            result,
            {
                "episode_id": 12345,
                "series_id": 74796,
                "season_number": 2,
                "episode_number": 2,
                "absolute_number": 22,
            },
        )
        mock_api_request.assert_called_once_with(
            tvdb.PROVIDER,
            "GET",
            f"{tvdb.BASE_URL}/episodes/12345",
            headers={"Authorization": "Bearer test-token"},
        )
        mock_cache.set.assert_called_once_with(
            "tvdb_episode_12345",
            {
                "episode_id": 12345,
                "series_id": 74796,
                "season_number": 2,
                "episode_number": 2,
                "absolute_number": 22,
            },
        )
