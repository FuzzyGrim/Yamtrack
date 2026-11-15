from unittest.mock import MagicMock, patch

import requests
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from app.providers import pocketcasts


@override_settings(PER_PAGE=2)
class PocketCastsSearchPaginationTests(TestCase):
    """iTunes ignores `offset` for podcasts, so pages are sliced locally (#1817)."""

    def setUp(self):
        cache.clear()

    @patch("app.providers.pocketcasts._directory_candidates", return_value=[])
    @patch("app.providers.pocketcasts.requests.get")
    def test_pages_return_different_results(self, mock_get, _candidates):
        response = MagicMock()
        response.json.return_value = {
            "results": [
                {"collectionId": i, "collectionName": f"Show {i}"} for i in range(1, 4)
            ],
        }
        mock_get.return_value = response

        first = pocketcasts.search("show", 1)
        second = pocketcasts.search("show", 2)

        self.assertEqual([r["media_id"] for r in first["results"]], ["1", "2"])
        self.assertEqual([r["media_id"] for r in second["results"]], ["3"])
        self.assertEqual(first["total_pages"], 2)


@override_settings(PER_PAGE=10)
class PocketCastsSearchDiscoveryTests(TestCase):
    """Duplicate listings collapse and directory-only shows are found (#1829)."""

    def setUp(self):
        cache.clear()

    @patch("app.providers.pocketcasts.requests.post")
    @patch("app.providers.pocketcasts.requests.get")
    def test_partial_word_completion_finds_exact_title(self, mock_get, mock_post):
        target = {
            "collectionId": 9,
            "collectionName": "Blackout",
            "artistName": "QCODE",
        }
        other = {
            "collectionId": 1,
            "collectionName": "Being Blackout",
            "artistName": "Blackou",
        }
        mock_post.return_value.json.return_value = [
            {"title": "Being Blackout", "author": "Blackou"},
        ]

        def get(url, params=None, **kwargs):
            response = MagicMock()
            response.json.return_value = {
                "results": [target, target]
                if params["term"] == "Blackout"
                else [other],
            }
            return response

        mock_get.side_effect = get
        results = pocketcasts.search("blackou", 1)["results"]
        self.assertEqual([item["media_id"] for item in results], ["9", "1"])

    @patch("app.providers.pocketcasts.requests.post")
    @patch("app.providers.pocketcasts.requests.get")
    def test_full_word_does_not_trigger_completion(self, mock_get, mock_post):
        mock_post.return_value.json.return_value = [
            {"title": "Being Blackout", "author": "A"}
        ]
        mock_get.return_value.json.return_value = {
            "results": [
                {
                    "collectionId": 1,
                    "collectionName": "Being Blackout",
                    "artistName": "A",
                },
            ]
        }
        self.assertEqual(
            pocketcasts.search("Blackout", 1)["results"][0]["media_id"], "1"
        )
        self.assertEqual(mock_get.call_count, 1)

    @patch("app.providers.pocketcasts.requests.post")
    @patch("app.providers.pocketcasts.requests.get")
    def test_directory_failure_preserves_itunes_results(self, mock_get, mock_post):
        mock_post.side_effect = requests.RequestException("offline")
        mock_get.return_value.json.return_value = {
            "results": [
                {
                    "collectionId": 1,
                    "collectionName": "Being Blackout",
                    "artistName": "Blackou",
                },
            ]
        }
        self.assertEqual(
            pocketcasts.search("blackou", 1)["results"][0]["media_id"], "1"
        )

    @patch("app.providers.pocketcasts.requests.post")
    def test_malformed_directory_response_is_ignored(self, mock_post):
        for value in ({"podcasts": []}, [None, {}, {"title": 3}]):
            mock_post.return_value.json.return_value = value
            self.assertEqual(pocketcasts._directory_candidates("blackou"), [])

    @patch("app.providers.pocketcasts.requests.post")
    @patch("app.providers.pocketcasts.requests.get")
    def test_completion_failure_preserves_directory_results(self, mock_get, mock_post):
        mock_post.return_value.json.return_value = [
            {"title": "Being Blackout", "author": "Blackou"}
        ]
        original = {
            "collectionId": 1,
            "collectionName": "Being Blackout",
            "artistName": "Blackou",
        }

        def get(url, params=None, **kwargs):
            if params["term"] == "Blackout":
                raise requests.RequestException("offline")
            response = MagicMock()
            response.json.return_value = {"results": [original]}
            return response

        mock_get.side_effect = get
        self.assertEqual(
            pocketcasts.search("blackou", 1)["results"][0]["media_id"], "1"
        )

    @patch("app.providers.pocketcasts.requests.post")
    @patch("app.providers.pocketcasts.requests.get")
    def test_partial_search_reaches_web_and_api_routes(self, mock_get, mock_post):
        user = get_user_model().objects.create_user(
            username="searcher", password="test"
        )
        self.client.force_login(user)
        mock_post.return_value.json.return_value = [
            {"title": "Being Blackout", "author": "Blackou"}
        ]

        def get(url, params=None, **kwargs):
            response = MagicMock()
            response.json.return_value = {
                "results": [
                    {
                        "collectionId": 9,
                        "collectionName": "Blackout",
                        "artistName": "QCODE",
                    }
                    if params["term"] == "Blackout"
                    else {
                        "collectionId": 1,
                        "collectionName": "Being Blackout",
                        "artistName": "Blackou",
                    },
                ]
            }
            return response

        mock_get.side_effect = get
        web = self.client.get(
            reverse("search_results_json"),
            {
                "q": "blackou",
                "media_type": "podcast",
                "row": "online:pocketcasts",
            },
        )
        api = self.client.get(
            reverse("api_search_provider", args=("podcast",)),
            {
                "search": "blackou",
                "source": "pocketcasts",
            },
        )
        self.assertEqual(web.status_code, 200)
        self.assertContains(web, "Blackout")
        self.assertEqual(api.status_code, 200)
        self.assertEqual(api.json()["results"][0]["media_id"], "9")

    @patch("app.providers.pocketcasts.requests.post")
    @patch("app.providers.pocketcasts.requests.get")
    def test_duplicates_collapse_and_directory_show_is_added(self, mock_get, mock_post):
        listing = [
            {"collectionId": 1, "collectionName": "Babies", "artistName": "A"},
            {"collectionId": 2, "collectionName": "Babies", "artistName": "A"},
        ]
        target = {
            "collectionId": 9,
            "collectionName": "Blackout",
            "artistName": "QCODE",
        }

        def get(url, params=None, **kwargs):
            response = MagicMock()
            is_lookup = params["limit"] == 5
            response.json.return_value = {"results": [target] if is_lookup else listing}
            return response

        mock_get.side_effect = get
        mock_post.return_value.json.return_value = [
            {"title": "Blackout", "author": "QCODE"},
        ]

        data = pocketcasts.search("blackou", 1)

        self.assertEqual([r["media_id"] for r in data["results"]], ["9", "1"])
