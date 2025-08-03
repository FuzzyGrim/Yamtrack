import json
from datetime import UTC, datetime
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.test import TestCase
from django.urls import reverse

from app import providers
from app.models import (
    Book,
    Episode,
    Item,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Status,
)

class APITest(TestCase):
    """Test getting items from the api."""

    def setUp(self):
        """Create necessary data for the tests."""
        self.credentials = {"username": "test", "password": "12345", "token": "test-token"}
        self.user = get_user_model().objects.create_superuser(**self.credentials)
        self.client.login(**self.credentials)

        item_movie = Item.objects.create(
            media_id="10494",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Perfect Blue",
            image="https://image.url",
        )
        Movie.objects.create(
            item=item_movie,
            user=self.user,
            score=9,
            status=Status.COMPLETED.value,
            notes="Nice",
            start_date=datetime(2023, 6, 1, 0, 0, tzinfo=UTC),
            end_date=datetime(2023, 6, 1, 0, 0, tzinfo=UTC),
        )

        item_season = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="https://image.url",
            season_number=1,
        )

        season = Season.objects.create(
            item=item_season,
            user=self.user,
            score=9,
            status=Status.IN_PROGRESS.value,
            notes="Nice",
        )

        item_episode = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="https://image.url",
            season_number=1,
            episode_number=1,
        )
        Episode.objects.create(
            item=item_episode,
            related_season=season,
            end_date=datetime(2023, 6, 1, 0, 0, tzinfo=UTC),
        )

        item_book = Item.objects.create(
            media_id="OL21733390M",
            source=Sources.OPENLIBRARY.value,
            media_type=MediaTypes.BOOK.value,
            title="Fantastic Mr. Fox",
            image="https://image.url",
        )
        Book.objects.create(
            item=item_book,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=120,
            start_date=datetime(2021, 6, 1, 0, 0, tzinfo=UTC),
        )

    def test_invalid_token(self):
        """Test api with invalid token returns 401."""
        url = reverse("api_medialist", kwargs={"media_type": "tv"})
        query_kwargs = {"token": "invalid-token"}
        response = self.client.get(f'{url}?{urlencode(query_kwargs)}')
        self.assertEqual(response.status_code, 401)

    def test_api_medialist_tv(self):
        """Test receiving basic TV medialist info."""
        # Generate the CSV file by accessing the export view
        api_url = reverse("api_medialist", kwargs={"media_type": "tv"})
        query_kwargs = {"token": "test-token"}
        response = self.client.get(f'{api_url}?{urlencode(query_kwargs)}')

        # Assert that the response is successful (status code 200)
        self.assertEqual(response.status_code, 200)

        # Assert that the response content type is text/csv
        self.assertEqual(response["Content-Type"], "application/json")

        # Read the streaming content and decode it
        content = [x["tvdbId"] for x in response.json()]

        db_media_ids = set(
            Item.objects.filter(
                Q(tv__user=self.user)
                | Q(season__user=self.user)
            ).values_list("media_id", flat=True),
        )
        for id in db_media_ids:
            metadata = providers.services.get_media_metadata(
                    "tv",
                    id,
                    Sources.TMDB.value,
                )

            self.assertTrue(str(metadata["tvdb_id"]) in content)

    def test_api_medialist_movie(self):
        """Test receiving basic movie medialist info."""
        # Generate the CSV file by accessing the export view
        api_url = reverse("api_medialist", kwargs={"media_type": "movie"})
        query_kwargs = {"token": "test-token"}
        response = self.client.get(f'{api_url}?{urlencode(query_kwargs)}')

        # Assert that the response is successful (status code 200)
        self.assertEqual(response.status_code, 200)

        # Assert that the response content type is text/csv
        self.assertEqual(response["Content-Type"], "application/json")

        # Read the streaming content and decode it
        content = [x["id"] for x in response.json()]

        db_media_ids = set(
            Item.objects.filter(
                Q(movie__user=self.user)
            ).values_list("media_id", flat=True),
        )
        for id in db_media_ids:
            self.assertTrue(id in content)

    def test_api_medialist_book(self):
        """Test receiving basic book medialist info."""
        # Generate the CSV file by accessing the export view
        api_url = reverse("api_medialist", kwargs={"media_type": "book"})
        query_kwargs = {"token": "test-token"}
        response = self.client.get(f'{api_url}?{urlencode(query_kwargs)}')

        # Assert that the response is successful (status code 200)
        self.assertEqual(response.status_code, 200)

        # Assert that the response content type is text/csv
        self.assertEqual(response["Content-Type"], "application/json")

        # Read the streaming content and decode it
        content = [x["id"] for x in response.json()]

        db_media_ids = set(
            Item.objects.filter(
                Q(book__user=self.user)
            ).values_list("media_id", flat=True),
        )
        for id in db_media_ids:
            self.assertTrue(id in content)