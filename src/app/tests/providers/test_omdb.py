from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from app.providers.omdb import OmdbBudgetError, fetch_ratings

OMDB_HIT = {
    "Response": "True",
    "imdbRating": "8.7",
    "Metascore": "73",
    "Ratings": [
        {"Source": "Internet Movie Database", "Value": "8.7/10"},
        {"Source": "Rotten Tomatoes", "Value": "83%"},
        {"Source": "Metacritic", "Value": "73/100"},
    ],
}


class OmdbClientTests(SimpleTestCase):
    """Tests for the OMDb client."""

    def setUp(self):
        """Drop cached OMDb responses between tests."""
        cache.clear()

    @override_settings(OMDB_API_KEY="")
    @patch("app.providers.omdb.services.api_request")
    def test_no_api_key_skips_the_request(self, mock_request):
        """A missing key leaves the ratings empty and does not call OMDb."""
        self.assertIsNone(fetch_ratings("tt0133093"))
        mock_request.assert_not_called()

    @override_settings(OMDB_API_KEY="test-key", OMDB_DAILY_LIMIT=900)
    @patch("app.providers.omdb.services.api_request", return_value=OMDB_HIT)
    def test_parses_mocked_response(self, mock_request):
        """IMDb, critics, and Metacritic are read from a mocked OMDb body."""
        parsed = fetch_ratings("tt0133093")

        self.assertEqual(parsed["imdb"], Decimal("8.7"))
        self.assertEqual(parsed["rotten_tomatoes"], Decimal(83))
        self.assertEqual(parsed["metacritic"], Decimal(73))
        mock_request.assert_called_once()

        fetch_ratings("tt0133093")
        mock_request.assert_called_once()

    @override_settings(OMDB_API_KEY="test-key", OMDB_DAILY_LIMIT=900)
    @patch("app.providers.omdb.services.api_request")
    def test_missing_omdb_values_stay_null(self, mock_request):
        """N/A and an absent critics score are None, not zero."""
        mock_request.return_value = {
            "Response": "True",
            "imdbRating": "N/A",
            "Metascore": "N/A",
            "Ratings": [],
        }

        parsed = fetch_ratings("tt0000001")

        self.assertEqual(
            parsed,
            {"imdb": None, "rotten_tomatoes": None, "metacritic": None},
        )

    @override_settings(OMDB_API_KEY="test-key", OMDB_DAILY_LIMIT=1)
    @patch("app.providers.omdb.services.api_request", return_value=OMDB_HIT)
    def test_daily_limit_blocks_the_next_request(self, mock_request):
        """The second uncached title is refused once the daily cap is spent."""
        fetch_ratings("tt0000001")

        with self.assertRaises(OmdbBudgetError):
            fetch_ratings("tt0000002")

        mock_request.assert_called_once()
