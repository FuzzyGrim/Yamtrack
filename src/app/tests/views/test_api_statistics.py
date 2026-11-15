"""Tests for the token-authenticated statistics JSON API."""

from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.models import Anime, Item, MediaTypes, Movie, Sources, Status


class ApiStatisticsTests(TestCase):
    """Test GET /api/v1/statistics/<token>/."""

    def setUp(self):
        """Create a user with a known token and some tracked media."""
        credentials = {"username": "apistats", "password": "12345"}
        self.user = get_user_model().objects.create_user(**credentials)
        self.user.token = "test-stats-token"  # noqa: S105
        self.user.save(update_fields=["token"])
        self.url = reverse("api_statistics", args=[self.user.token])

        movie_item = Item.objects.create(
            media_id="m1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="none.jpg",
        )
        Movie.objects.bulk_create(
            [
                Movie(
                    item=movie_item,
                    user=self.user,
                    status=Status.COMPLETED.value,
                    progress=1,
                    end_date=datetime(2024, 6, 15, tzinfo=UTC),
                ),
            ],
        )

        anime_item = Item.objects.create(
            media_id="a1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Test Anime",
            image="none.jpg",
        )
        Anime.objects.bulk_create(
            [
                Anime(
                    item=anime_item,
                    user=self.user,
                    status=Status.IN_PROGRESS.value,
                    progress=12,
                    start_date=datetime(2024, 6, 1, tzinfo=UTC),
                ),
            ],
        )

    def test_valid_token_returns_statistics(self):
        """A valid token returns media counts and consumption totals."""
        response = self.client.get(
            self.url,
            {"start-date": "all", "end-date": "all"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")

        data = response.json()
        self.assertIn("media_count", data)
        self.assertIn("consumption", data)
        self.assertIn("in_progress", data)
        self.assertEqual(data["start_date"], None)
        self.assertEqual(data["end_date"], None)
        self.assertEqual(data["media_count"]["movie"], 1)
        self.assertEqual(data["media_count"]["anime"], 1)
        self.assertEqual(data["in_progress"], 1)

        consumption_by_type = {
            entry["media_type"]: entry for entry in data["consumption"]
        }
        self.assertEqual(consumption_by_type["movie"]["value"], 1)
        self.assertEqual(consumption_by_type["anime"]["value"], 12)
        self.assertNotIn("color", consumption_by_type["movie"])
        self.assertIn("descriptor", consumption_by_type["movie"])

    def test_invalid_token_returns_404(self):
        """An unknown token returns 404."""
        response = self.client.get(
            reverse("api_statistics", args=["not-a-real-token"]),
        )
        self.assertEqual(response.status_code, 404)

    def test_date_filters_exclude_out_of_range_media(self):
        """Date filters match the HTML statistics page behavior."""
        response = self.client.get(
            self.url,
            {"start-date": "2020-01-01", "end-date": "2020-12-31"},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["media_count"]["total"], 0)
        self.assertEqual(data["consumption"], [])
        self.assertIsNotNone(data["start_date"])
        self.assertIsNotNone(data["end_date"])

    def test_date_filters_include_in_range_media(self):
        """Media inside the requested range is included."""
        response = self.client.get(
            self.url,
            {"start-date": "2024-01-01", "end-date": "2024-12-31"},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["media_count"]["movie"], 1)
        self.assertEqual(data["media_count"]["anime"], 1)
