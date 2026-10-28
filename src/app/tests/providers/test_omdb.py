from unittest.mock import patch

import requests
from django.core.cache import cache
from django.test import TestCase, override_settings

from app.providers import omdb

OMDB_RESPONSE = {
    "Title": "Superman",
    "Year": "2025",
    "imdbRating": "7.6",
    "imdbVotes": "228,380",
    "Ratings": [
        {"Source": "Internet Movie Database", "Value": "7.6/10"},
        {"Source": "Rotten Tomatoes", "Value": "83%"},
        {"Source": "Metacritic", "Value": "68/100"},
    ],
    "Response": "True",
}


@override_settings(OMDB_API="test_omdb_key")
class OMDbRatings(TestCase):
    """Test fetching critic ratings from OMDb."""

    def setUp(self):
        """Clear the cache between tests."""
        cache.clear()

    @patch("app.providers.omdb.services.api_request")
    def test_ratings(self, mock_api_request):
        """Test that all supported rating sources are returned."""
        mock_api_request.return_value = OMDB_RESPONSE

        response = omdb.ratings("tt5950044")

        self.assertEqual(
            response["rotten_tomatoes"],
            {"value": "83", "suffix": "%"},
        )
        self.assertEqual(
            response["imdb"],
            {"value": "7.6", "suffix": "/10", "votes": "228,380"},
        )
        self.assertEqual(
            response["metacritic"],
            {"value": "68", "suffix": "/100"},
        )
        mock_api_request.assert_called_once()
        self.assertEqual(
            mock_api_request.call_args.kwargs["params"],
            {"apikey": "test_omdb_key", "i": "tt5950044"},
        )

    @patch("app.providers.omdb.services.api_request")
    def test_ratings_cached(self, mock_api_request):
        """Test that ratings are only fetched once."""
        mock_api_request.return_value = OMDB_RESPONSE

        omdb.ratings("tt5950044")
        response = omdb.ratings("tt5950044")

        self.assertEqual(response["rotten_tomatoes"]["value"], "83")
        mock_api_request.assert_called_once()

    @patch("app.providers.omdb.services.api_request")
    def test_ratings_partial(self, mock_api_request):
        """Test a series with only an IMDb rating and no vote count."""
        mock_api_request.return_value = {
            "imdbVotes": "N/A",
            "Ratings": [{"Source": "Internet Movie Database", "Value": "6.1/10"}],
            "Response": "True",
        }

        response = omdb.ratings("tt0000001")

        self.assertEqual(response, {"imdb": {"value": "6.1", "suffix": "/10"}})
        self.assertNotIn("rotten_tomatoes", response)

    @patch("app.providers.omdb.services.api_request")
    def test_ratings_unknown_id(self, mock_api_request):
        """Test that an unknown IMDb ID returns no ratings."""
        mock_api_request.return_value = {
            "Response": "False",
            "Error": "Incorrect IMDb ID.",
        }

        self.assertEqual(omdb.ratings("tt0000000"), {})

    @patch("app.providers.omdb.services.api_request")
    def test_ratings_request_error(self, mock_api_request):
        """Test that request failures return no ratings and are not cached."""
        mock_api_request.side_effect = requests.exceptions.ConnectionError("down")

        self.assertEqual(omdb.ratings("tt5950044"), {})

        mock_api_request.side_effect = None
        mock_api_request.return_value = OMDB_RESPONSE

        self.assertEqual(omdb.ratings("tt5950044")["rotten_tomatoes"]["value"], "83")
        self.assertEqual(mock_api_request.call_count, 2)

    @patch("app.providers.omdb.services.api_request")
    def test_ratings_missing_imdb_id(self, mock_api_request):
        """Test that no request is made without an IMDb ID."""
        self.assertEqual(omdb.ratings(None), {})
        mock_api_request.assert_not_called()

    @override_settings(OMDB_API="")
    @patch("app.providers.omdb.services.api_request")
    def test_ratings_not_configured(self, mock_api_request):
        """Test that no request is made when OMDb is not configured."""
        self.assertEqual(omdb.ratings("tt5950044"), {})
        mock_api_request.assert_not_called()

    def test_split_rating(self):
        """Test splitting OMDb rating strings into score and scale."""
        self.assertEqual(omdb.split_rating("85%"), {"value": "85", "suffix": "%"})
        self.assertEqual(omdb.split_rating("7.6/10"), {"value": "7.6", "suffix": "/10"})
        self.assertEqual(omdb.split_rating("68/100"), {"value": "68", "suffix": "/100"})
