from unittest.mock import patch

from django.urls import reverse

from api.helpers import MAX_SEARCH_PROVIDER_PAGES

from .base import YamtrackApiTestCase
from .helpers import check_pagination_structure


class SearchTests(YamtrackApiTestCase):
    """Validate search endpoint contracts."""

    def test_search_rejects_invalid_media_type(self):
        """Search endpoint should reject unknown media types."""
        response = self.call_api(
            "get",
            "api_search_provider",
            args=("invalid",),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(
            "unsupported media type", response.json().get("detail", "").lower()
        )

    def test_search_rejects_children_media_types(self):
        """Search endpoint should reject season and episode media types."""
        season_response = self.call_api(
            "get",
            "api_search_provider",
            args=("season",),
            headers=self.auth_headers,
        )
        self.assertEqual(season_response.status_code, 400)

        episode_response = self.call_api(
            "get",
            "api_search_provider",
            args=("episode",),
            headers=self.auth_headers,
        )
        self.assertEqual(episode_response.status_code, 400)

    @patch("api.views.services.search")
    def test_search_returns_paginated_payload(self, mock_search):
        """Search endpoint should return paginated response with provider data."""
        mock_search.return_value = {
            "results": [
                {
                    "media_id": "1",
                    "source": "tmdb",
                    "media_type": "movie",
                    "title": "Example",
                    "image": "https://example.com/example.jpg",
                },
            ],
            "total_results": 1,
            "total_pages": 1,
        }

        response = self.call_api(
            "get",
            "api_search_provider",
            args=("movie",),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("pagination", payload)
        self.assertIn("results", payload)
        check_pagination_structure(self, payload["pagination"])

    @patch("api.views.services.search")
    def test_search_passes_query_params_to_provider(self, mock_search):
        """Search endpoint should forward search/source/limit/offset and user."""
        mock_search.return_value = {
            "results": [{"id": 1, "title": "Example"}],
            "total_results": 1,
            "total_pages": 1,
        }

        response = self.client.get(
            reverse("api_search_provider", args=("movie",))
            + "?search=matrix&source=tmdb&limit=5&offset=2",
            **self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        mock_search.assert_called_once_with(
            "movie",
            "matrix",
            1,
            "tmdb",
            limit=5,
            offset=2,
            user=self.user1,
        )

        payload = response.json()
        self.assertEqual(payload["pagination"]["limit"], 5)
        self.assertEqual(payload["pagination"]["offset"], 2)

    @patch("api.views.services.search")
    def test_search_enforces_max_limit(self, mock_search):
        """Search endpoint should clamp limit to the API maximum."""
        mock_search.return_value = {
            "results": [{"id": i, "title": f"Item {i}"} for i in range(1, 6)],
            "total_results": 5,
            "total_pages": 1,
        }

        response = self.client.get(
            reverse("api_search_provider", args=("movie",)) + "?limit=999",
            **self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_search.call_args.kwargs["limit"], 200)
        self.assertEqual(response.json()["pagination"]["limit"], 200)

    def test_search_rejects_invalid_limit(self):
        """Search endpoint should reject non-integer limit values."""
        response = self.client.get(
            reverse("api_search_provider", args=("movie",)) + "?limit=abc",
            **self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("invalid limit", response.json().get("detail", "").lower())

    def test_search_rejects_invalid_offset(self):
        """Search endpoint should reject non-integer offset values."""
        response = self.client.get(
            reverse("api_search_provider", args=("movie",)) + "?offset=abc",
            **self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("invalid offset", response.json().get("detail", "").lower())

    def test_search_rejects_non_positive_limit(self):
        """Search endpoint should reject limit <= 0."""
        response = self.client.get(
            reverse("api_search_provider", args=("movie",)) + "?limit=0",
            **self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("limit must be >0", response.json().get("detail", "").lower())

    @patch("api.views.services.search")
    def test_search_manual_source_uses_provider_page(self, mock_search):
        """Manual search already applies the offset, it must not be applied twice."""
        mock_search.return_value = {
            "results": [{"id": 3, "title": "C"}, {"id": 4, "title": "D"}],
            "total_results": 4,
            "total_pages": 2,
        }

        response = self.client.get(
            reverse("api_search_provider", args=("movie",))
            + "?source=manual&limit=2&offset=2",
            **self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_search.call_count, 1)
        payload = response.json()
        self.assertEqual([item["id"] for item in payload["results"]], [3, 4])
        self.assertEqual(payload["pagination"]["total"], 4)

    @patch("api.views.services.search")
    def test_search_stops_after_max_provider_pages(self, mock_search):
        """Search endpoint should not fetch an unbounded number of pages."""
        mock_search.return_value = {
            "results": [{"id": 1, "title": "A"}],
            "total_results": 1000,
            "total_pages": 1000,
        }

        response = self.client.get(
            reverse("api_search_provider", args=("movie",)) + "?limit=10&offset=150",
            **self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_search.call_count, MAX_SEARCH_PROVIDER_PAGES)

    def test_search_rejects_invalid_source_for_media_type(self):
        """Search endpoint should reject a source invalid for the media type."""
        response = self.client.get(
            reverse("api_search_provider", args=("movie",)) + "?source=mal",
            **self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)

    @patch("api.views.services.search")
    def test_search_accumulates_pages_before_paginating(self, mock_search):
        """Search endpoint should keep fetching pages until offset+limit is covered."""
        mock_search.side_effect = [
            {
                "results": [
                    {"id": 1, "title": "A"},
                    {"id": 2, "title": "B"},
                ],
                "total_results": 4,
                "total_pages": 2,
            },
            {
                "results": [
                    {"id": 3, "title": "C"},
                    {"id": 4, "title": "D"},
                ],
                "total_results": 4,
                "total_pages": 2,
            },
        ]

        response = self.client.get(
            reverse("api_search_provider", args=("movie",)) + "?limit=2&offset=2",
            **self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_search.call_count, 2)

        payload = response.json()
        self.assertEqual(payload["pagination"]["total"], 4)
        self.assertEqual(payload["pagination"]["limit"], 2)
        self.assertEqual(payload["pagination"]["offset"], 2)
        self.assertEqual([item["id"] for item in payload["results"]], [3, 4])
        self.assertIsNone(payload["pagination"]["next"])
        self.assertIsNotNone(payload["pagination"]["previous"])

    @patch("api.views.services.search")
    def test_search_handles_malformed_provider_payload(self, mock_search):
        """Search endpoint should return empty paginated payload on bad response."""
        mock_search.return_value = {"unexpected": "payload"}

        response = self.call_api(
            "get",
            "api_search_provider",
            args=("movie",),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["results"], [])
        self.assertEqual(payload["pagination"]["total"], 0)

    @patch("api.views.services.search")
    def test_search_provider_exception_returns_internal_error(self, mock_search):
        """Search endpoint should map provider failures to HTTP 500."""
        mock_search.side_effect = RuntimeError("provider failure")

        response = self.call_api(
            "get",
            "api_search_provider",
            args=("movie",),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 500)
