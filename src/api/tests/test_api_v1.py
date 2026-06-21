from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient


class ApiV1FoundationTests(TestCase):
    """Smoke tests for the v1 mobile API foundation."""

    def setUp(self):
        self.client = APIClient()

    def test_health_is_public(self):
        response = self.client.get("/api/v1/health/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "ok")

    def test_meta_is_public(self):
        response = self.client.get("/api/v1/meta/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("movie", response.data["media_types"])
        self.assertIn("Completed", response.data["status_choices"])

    def test_register_returns_tokens_and_user(self):
        response = self.client.post(
            "/api/v1/auth/register/",
            {
                "username": "iosuser",
                "email": "ios@example.com",
                "password": "strong-password-123",
                "password_confirm": "strong-password-123",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertEqual(response.data["user"]["username"], "iosuser")

    def test_login_and_me(self):
        get_user_model().objects.create_user(
            username="mobile",
            email="mobile@example.com",
            password="strong-password-123",
        )

        login = self.client.post(
            "/api/v1/auth/login/",
            {
                "username_or_email": "mobile@example.com",
                "password": "strong-password-123",
            },
            format="json",
        )
        self.assertEqual(login.status_code, status.HTTP_200_OK)

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
        response = self.client.get("/api/v1/me/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["username"], "mobile")

    @patch("api.services.media.provider_services.search")
    def test_media_search_contract(self, search_mock):
        user = get_user_model().objects.create_user(
            username="searcher",
            password="strong-password-123",
        )
        self.client.force_authenticate(user)
        search_mock.return_value = {
            "results": [
                {
                    "media_id": "550",
                    "title": "Fight Club",
                    "image": "https://example.com/fight-club.jpg",
                    "release_date": "1999-10-15",
                },
            ],
        }

        response = self.client.get("/api/v1/media/search/?media_type=movie&q=fight")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["results"][0]["ref"]["source"], "tmdb")
        self.assertEqual(response.data["results"][0]["ref"]["media_type"], "movie")
        self.assertEqual(response.data["results"][0]["title"], "Fight Club")

    @patch("app.providers.mdblist.get_media_ratings")
    @patch("api.services.media.provider_services.get_media_metadata")
    def test_media_detail_includes_synopsis_and_external_ratings(self, metadata_mock, ratings_mock):
        metadata_mock.return_value = {
            "media_id": "550",
            "media_type": "movie",
            "source": "tmdb",
            "title": "Fight Club",
            "image": "https://example.com/fight-club.jpg",
            "synopsis": "Soap, clubs, and insomnia.",
            "score": "8.4",
            "score_count": 1000,
        }
        ratings_mock.return_value = {
            "imdb": {"value": "8.8", "votes": 2300000},
            "letterboxd": {"value": "4.3", "votes": 500000},
        }

        response = self.client.get("/api/v1/media/tmdb/movie/550/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["overview"], "Soap, clubs, and insomnia.")
        self.assertEqual(response.data["synopsis"], "Soap, clubs, and insomnia.")
        self.assertEqual(
            [(rating["source"], rating["value"]) for rating in response.data["external_ratings"]],
            [("TMDB", "8.4"), ("IMDb", "8.8"), ("Letterboxd", "4.3")],
        )
