from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import (
    MediaTypes,
    Movie,
    Status,
)
from integrations.imports import (
    helpers,
)
from integrations.imports.trakt import TraktImporter, importer

mock_path = Path(__file__).resolve().parent.parent / "mock_data"
app_mock_path = (
    Path(__file__).resolve().parent.parent.parent.parent / "app" / "tests" / "mock_data"
)


class ImportTrakt(TestCase):
    """Test importing media from Trakt."""

    def setUp(self):
        """Create user for the tests."""
        credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**credentials)

    @patch("integrations.imports.trakt.TraktImporter._get_metadata")
    def test_process_watched_movie(self, mock_get_metadata):
        """Test processing a movie entry."""
        movie_entry = {
            "type": "movie",
            "movie": {"title": "Test Movie", "ids": {"tmdb": 67890}},
            "watched_at": "2023-01-02T00:00:00.000Z",
        }

        mock_get_metadata.return_value = {
            "title": "Test Movie",
            "image": "movie_image.jpg",
        }

        trakt_importer = TraktImporter("test", self.user, "new")
        trakt_importer.process_watched_movie(movie_entry)

        self.assertEqual(len(trakt_importer.bulk_media[MediaTypes.MOVIE.value]), 1)
        self.assertEqual(len(trakt_importer.media_instances[MediaTypes.MOVIE.value]), 1)

        # Process the same movie again to test repeat handling
        trakt_importer.process_watched_movie(movie_entry)
        self.assertEqual(len(trakt_importer.bulk_media[MediaTypes.MOVIE.value]), 2)

    @patch("integrations.imports.trakt.TraktImporter._get_metadata")
    def test_process_watched_episode(self, mock_get_metadata):
        """Test processing an episode entry."""
        episode_entry = {
            "type": "episode",
            "episode": {"season": 1, "number": 1, "title": "Pilot"},
            "show": {"title": "Test Show", "ids": {"tmdb": 12345}},
            "watched_at": "2023-01-01T00:00:00.000Z",
        }

        def mock_metadata_side_effect(media_type, _, __, ___=None):
            if media_type == MediaTypes.TV.value:
                return {
                    "title": "Test Show",
                    "image": "tv_image.jpg",
                    "last_episode_season": 1,
                    "max_progress": 1,
                }
            if media_type == MediaTypes.SEASON.value:
                return {
                    "title": "Season 1",
                    "image": "season_image.jpg",
                    "episodes": [{"episode_number": 1, "still_path": "/still.jpg"}],
                    "max_progress": 1,
                }
            return None

        mock_get_metadata.side_effect = mock_metadata_side_effect

        trakt_importer = TraktImporter("testuser", self.user, "new")
        trakt_importer.process_watched_episode(episode_entry)

        self.assertEqual(len(trakt_importer.bulk_media[MediaTypes.TV.value]), 1)
        self.assertEqual(len(trakt_importer.bulk_media[MediaTypes.SEASON.value]), 1)
        self.assertEqual(len(trakt_importer.bulk_media[MediaTypes.EPISODE.value]), 1)

        # Process the same episode again to test repeat handling
        trakt_importer.process_watched_episode(episode_entry)
        self.assertEqual(len(trakt_importer.bulk_media[MediaTypes.EPISODE.value]), 2)

    @patch("integrations.imports.trakt.TraktImporter._make_api_request")
    @patch("integrations.imports.trakt.TraktImporter._get_metadata")
    def test_process_watchlist(self, mock_get_metadata, mock_make_request):
        """Test processing a watchlist entry."""
        watchlist_entry = {
            "listed_at": "2023-01-01T00:00:00.000Z",
            "type": "show",
            "show": {"title": "Watchlist Show", "ids": {"tmdb": 54321}},
        }

        mock_make_request.return_value = [watchlist_entry]
        mock_get_metadata.return_value = {
            "title": "Watchlist Show",
            "image": "show_image.jpg",
        }

        trakt_importer = TraktImporter("testuser", self.user, "new")
        trakt_importer.process_watchlist()

        self.assertEqual(len(trakt_importer.bulk_media[MediaTypes.TV.value]), 1)
        tv_obj = trakt_importer.bulk_media[MediaTypes.TV.value][0]
        self.assertEqual(tv_obj.status, Status.PLANNING.value)

    @patch("integrations.imports.trakt.TraktImporter._make_api_request")
    @patch("integrations.imports.trakt.TraktImporter._get_metadata")
    def test_process_ratings(self, mock_get_metadata, mock_make_request):
        """Test processing a rating entry."""
        rating_entry = {
            "rated_at": "2023-01-01T00:00:00.000Z",
            "type": "movie",
            "movie": {"title": "Rated Movie", "ids": {"tmdb": 238}},
            "rating": 8,
        }

        mock_make_request.return_value = [rating_entry]
        mock_get_metadata.return_value = {
            "title": "Rated Movie",
            "image": "movie_image.jpg",
        }

        trakt_importer = TraktImporter("testuser", self.user, "new")
        trakt_importer.process_ratings()

        self.assertEqual(len(trakt_importer.bulk_media[MediaTypes.MOVIE.value]), 1)
        movie_obj = trakt_importer.bulk_media[MediaTypes.MOVIE.value][0]
        self.assertEqual(movie_obj.score, 8)

    @patch("integrations.imports.trakt.TraktImporter._make_api_request")
    @patch("integrations.imports.trakt.TraktImporter._get_metadata")
    def test_process_comments(self, mock_get_metadata, mock_make_request):
        """Test processing paginated comments from Trakt."""
        # First page with one comment
        first_page = [
            {
                "type": "movie",
                "movie": {"title": "Commented Movie", "ids": {"tmdb": 123}},
                "comment": {
                    "comment": "Great movie!",
                    "updated_at": "2023-01-01T00:00:00.000Z",
                },
            },
        ]

        # Second empty page to stop pagination
        second_page = []

        mock_make_request.side_effect = [first_page, second_page]
        mock_get_metadata.return_value = {
            "title": "Commented Movie",
            "image": "movie_image.jpg",
        }

        trakt_importer = TraktImporter("testuser", self.user, "new")
        trakt_importer.process_comments()

        calls = mock_make_request.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertIn("?page=1&limit=1000", calls[0].args[0])  # First page
        self.assertIn("?page=2&limit=1000", calls[1].args[0])  # Second page

        self.assertEqual(len(trakt_importer.bulk_media[MediaTypes.MOVIE.value]), 1)
        movie_obj = trakt_importer.bulk_media[MediaTypes.MOVIE.value][0]
        self.assertEqual(movie_obj.notes, "Great movie!")

    @patch("integrations.imports.trakt.TraktImporter._get_paginated_data")
    @patch("integrations.imports.trakt.TraktImporter._make_api_request")
    @patch("integrations.imports.trakt.TraktImporter._get_metadata")
    def test_public_import_full_flow(
        self,
        mock_get_metadata,
        mock_make_request,
        mock_get_paginated,
    ):
        """Test full import flow with public username (no OAuth)."""
        mock_get_paginated.side_effect = [
            [
                {
                    "type": "movie",
                    "movie": {"title": "Public Movie", "ids": {"tmdb": 999}},
                    "watched_at": "2023-01-01T00:00:00.000Z",
                },
            ],
            [],  # Empty comments
        ]

        mock_make_request.return_value = []

        mock_get_metadata.return_value = {
            "title": "Public Movie",
            "image": "movie.jpg",
        }

        imported_counts, _ = importer(None, self.user, "new", "public_user")

        self.assertEqual(imported_counts[MediaTypes.MOVIE.value], 1)
        self.assertEqual(Movie.objects.filter(user=self.user).count(), 1)

    @patch("integrations.imports.trakt.TraktImporter._get_paginated_data")
    @patch("integrations.imports.trakt.TraktImporter._make_api_request")
    @patch("integrations.imports.trakt.TraktImporter._get_metadata")
    def test_oauth_import_full_flow(
        self,
        mock_get_metadata,
        mock_make_request,
        mock_get_paginated,
    ):
        """Test full import flow with OAuth token."""
        mock_get_paginated.side_effect = [
            [
                {
                    "type": "movie",
                    "movie": {"title": "OAuth Movie", "ids": {"tmdb": 888}},
                    "watched_at": "2023-01-01T00:00:00.000Z",
                },
            ],
            [],  # Empty comments
        ]

        mock_make_request.return_value = []

        mock_get_metadata.return_value = {
            "title": "OAuth Movie",
            "image": "movie.jpg",
        }

        encrypted_token = helpers.encrypt("test_refresh_token")
        imported_counts, _ = importer(
            encrypted_token,
            self.user,
            "new",
            "oauth_user",
        )

        self.assertEqual(imported_counts[MediaTypes.MOVIE.value], 1)
        self.assertEqual(Movie.objects.filter(user=self.user).count(), 1)

    def test_trakt_importer_with_refresh_token(self):
        """Test TraktImporter initialization with refresh token."""
        encrypted_token = helpers.encrypt("test_token")
        importer = TraktImporter(
            "testuser",
            self.user,
            "new",
            refresh_token=encrypted_token,
        )

        self.assertEqual(importer.username, "testuser")
        self.assertEqual(importer.refresh_token, encrypted_token)
        self.assertEqual(importer.mode, "new")

    def test_trakt_importer_without_refresh_token(self):
        """Test TraktImporter initialization without refresh token (public)."""
        importer = TraktImporter("testuser", self.user, "new", refresh_token=None)

        self.assertEqual(importer.username, "testuser")
        self.assertIsNone(importer.refresh_token)
        self.assertEqual(importer.mode, "new")


