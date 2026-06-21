from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from app.models import DiaryEntry, Item, MediaTypes, Sources


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
            "related": {
                "recommendations": [
                    {
                        "media_id": "680",
                        "media_type": "movie",
                        "source": "tmdb",
                        "title": "Pulp Fiction",
                        "image": "https://example.com/pulp.jpg",
                    },
                ],
                "seasons": [
                    {
                        "media_id": "550",
                        "media_type": "season",
                        "source": "tmdb",
                        "season_number": 1,
                        "title": "Season 1",
                    },
                ],
            },
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
        self.assertEqual(response.data["related_sections"][0]["id"], "recommendations")
        self.assertEqual(response.data["related_sections"][0]["items"][0]["title"], "Pulp Fiction")
        self.assertNotIn("seasons", [section["id"] for section in response.data["related_sections"]])

    @patch("api.services.media.provider_services.get_media_metadata")
    def test_game_detail_exposes_all_related_as_related_section(self, metadata_mock):
        metadata_mock.return_value = {
            "media_id": "1020",
            "media_type": "game",
            "source": "igdb",
            "title": "Space Game",
            "image": "https://example.com/space.jpg",
            "related": {
                "all_related": [
                    {
                        "media_id": "1021",
                        "media_type": "game",
                        "source": "igdb",
                        "title": "Space Game 2",
                        "image": "https://example.com/space2.jpg",
                    },
                ],
            },
        }

        response = self.client.get("/api/v1/media/igdb/game/1020/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["related_sections"][0]["id"], "all_related")
        self.assertEqual(response.data["related_sections"][0]["title"], "Related")
        self.assertEqual(response.data["related_sections"][0]["items"][0]["title"], "Space Game 2")

    def test_community_stats_include_truthful_rating_distribution(self):
        user = get_user_model().objects.create_user(username="rater", password="strong-password-123")
        other = get_user_model().objects.create_user(username="other", password="strong-password-123")
        item = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="550",
            title="Fight Club",
            image="https://example.com/fight-club.jpg",
        )
        now = timezone.now()
        DiaryEntry.objects.create(user=user, item=item, consumed_at=now, rating="8.0", visibility="public")
        DiaryEntry.objects.create(user=other, item=item, consumed_at=now, rating="8.0", visibility="followers")
        DiaryEntry.objects.create(user=other, item=item, consumed_at=now, rating="9.0", visibility="private")
        DiaryEntry.objects.create(user=other, item=item, consumed_at=now, rating=None, visibility="public")

        response = self.client.get("/api/v1/media/tmdb/movie/550/community/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["average_rating"], "8.00")
        self.assertEqual(response.data["rating_count"], 2)
        self.assertEqual(response.data["rating_distribution"], [{"rating": "8.0", "count": 2}])
