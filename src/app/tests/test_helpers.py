from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.http import HttpRequest
from django.test import TestCase

from app.helpers import (
    enrich_items_with_user_data,
    form_error_messages,
    minutes_to_hhmm,
    redirect_back,
)
from app.models import Item, MediaTypes, Movie, Sources, Status


class HelpersTest(TestCase):
    """Test helper functions."""

    def test_minutes_to_hhmm(self):
        """Test conversion of minutes to HH:MM format."""
        # Test minutes only
        self.assertEqual(minutes_to_hhmm(30), "30min")

        # Test hours and minutes
        self.assertEqual(minutes_to_hhmm(90), "1h 30min")
        self.assertEqual(minutes_to_hhmm(125), "2h 05min")

        # Test zero
        self.assertEqual(minutes_to_hhmm(0), "0min")

    @patch("app.helpers.url_has_allowed_host_and_scheme")
    @patch("app.helpers.HttpResponseRedirect")
    @patch("app.helpers.redirect")
    def test_redirect_back_with_next(self, _, mock_http_redirect, mock_url_check):
        """Test redirect_back with a 'next' parameter."""
        mock_url_check.return_value = True
        mock_http_redirect.return_value = "redirected"

        request = MagicMock()
        request.GET = {"next": "http://example.com/path?page=2&sort=name"}

        result = redirect_back(request)

        # Check that we redirected to the URL without the page parameter
        mock_http_redirect.assert_called_once()
        redirect_url = mock_http_redirect.call_args[0][0]
        self.assertEqual(redirect_url, "http://example.com/path?sort=name")
        self.assertEqual(result, "redirected")

    @patch("app.helpers.url_has_allowed_host_and_scheme")
    @patch("app.helpers.redirect")
    def test_redirect_back_without_next(self, mock_redirect, mock_url_check):
        """Test redirect_back without a 'next' parameter."""
        mock_url_check.return_value = False
        mock_redirect.return_value = "home_redirect"

        request = MagicMock()
        request.GET = {}

        result = redirect_back(request)

        mock_redirect.assert_called_once_with("home")
        self.assertEqual(result, "home_redirect")

    @patch("app.helpers.messages")
    def test_form_error_messages(self, mock_messages):
        """Test form_error_messages function."""
        form = MagicMock()
        form.errors = {
            "title": ["This field is required."],
            "release_date": ["Enter a valid date."],
        }
        request = HttpRequest()

        form_error_messages(form, request)

        # Check that error messages were added
        self.assertEqual(mock_messages.error.call_count, 2)
        mock_messages.error.assert_any_call(request, "Title: This field is required.")
        mock_messages.error.assert_any_call(
            request,
            "Release Date: Enter a valid date.",
        )


class EnrichItemsWithUserDataTest(TestCase):
    """Test the enrich_items_with_user_data function."""

    def setUp(self):
        """Set up test data."""
        self.credentials = {"username": "test", "password": "testpass"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.request = MagicMock()
        self.request.user = self.user

        # Create test items in the database
        self.movie_item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/movie.jpg",
        )

        self.season_item = Item.objects.create(
            media_id="67890",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test TV Show",
            image="http://example.com/show.jpg",
            season_number=1,
        )

        # Create user tracking data for the movie
        self.movie_media = Movie.objects.create(
            item=self.movie_item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=1,
        )

    def test_enrich_items_with_user_data(self):
        """Test enriching items with multiple scenarios."""
        raw_items = [
            # Scenario 1: Existing movie with user tracking data
            {
                "media_id": "238",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "title": "Test Movie",
                "image": "http://example.com/movie.jpg",
                "release_date": "2023-01-01",
                "rating": 8.5,
                "genre": "Action",
            },
            # Scenario 2: Existing season without user tracking data
            {
                "media_id": "67890",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.SEASON.value,
                "title": "Test TV Show",
                "season_title": "Season 1",
                "season_number": 1,
                "image": "http://example.com/show.jpg",
            },
            # Scenario 3: Non-existent item (raw data only)
            {
                "media_id": "99999",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "title": "Unknown Movie",
                "image": "http://example.com/unknown.jpg",
                "description": "This movie doesn't exist in our database",
            },
        ]

        enriched_items = enrich_items_with_user_data(self.request, raw_items)
        self.assertEqual(len(enriched_items), 3)

        # Scenario 1: Existing movie with user tracking data
        movie_enriched = enriched_items[0]
        self.assertEqual(movie_enriched["media"], self.movie_media)
        self.assertEqual(movie_enriched["item"]["title"], "Test Movie")
        self.assertEqual(movie_enriched["item"]["media_id"], "238")
        # Verify additional properties are preserved
        self.assertEqual(movie_enriched["item"]["release_date"], "2023-01-01")
        self.assertEqual(movie_enriched["item"]["rating"], 8.5)
        self.assertEqual(movie_enriched["item"]["genre"], "Action")

        # Scenario 2: Existing season without user tracking data
        season_enriched = enriched_items[1]
        self.assertEqual(
            season_enriched["media"],
            None,
        )  # No user tracking for this season
        self.assertEqual(
            season_enriched["item"]["season_title"],
            "Season 1",
        )  # Should use season_title
        self.assertEqual(season_enriched["item"]["season_number"], 1)

        # Scenario 3: Non-existent movie (raw data)
        unknown_movie_enriched = enriched_items[2]
        self.assertEqual(
            unknown_movie_enriched["item"]["media_id"],
            raw_items[2]["media_id"],
        )
        self.assertEqual(unknown_movie_enriched["media"], None)
        self.assertEqual(unknown_movie_enriched["item"]["title"], "Unknown Movie")
        self.assertEqual(unknown_movie_enriched["item"]["media_id"], "99999")
        self.assertEqual(
            unknown_movie_enriched["item"]["description"],
            "This movie doesn't exist in our database",
        )
