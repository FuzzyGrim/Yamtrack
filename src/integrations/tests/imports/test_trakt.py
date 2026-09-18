import json
import zipfile
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from requests import Response
from requests.exceptions import HTTPError

from app.models import (
    TV,
    Episode,
    Item,
    MediaTypes,
    Movie,
    Season,
    Status,
)
from integrations.imports import (
    helpers,
)
from integrations.imports.helpers import MediaImportError
from integrations.imports.trakt import (
    ENDPOINTS_TO_FILES,
    EXPORT_FALLBACK_USERNAME,
    FAVORITES_LIST_NAME,
    TraktArchiveManager,
    TraktExportImporter,
    TraktImporter,
    get_access_token,
    importer,
)
from lists.models import CustomList, CustomListItem

mock_path = Path(__file__).resolve().parent.parent / "mock_data"
app_mock_path = (
    Path(__file__).resolve().parent.parent.parent.parent / "app" / "tests" / "mock_data"
)
trakt_export_path = mock_path / "trakt_export"


class ImportTrakt(TestCase):
    """Test importing media from Trakt."""

    def setUp(self):
        """Create user for the tests."""
        credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**credentials)

    def test_get_date_strips_seconds(self):
        """Trakt watched_at timestamps with seconds get truncated to the minute."""
        trakt_importer = TraktImporter("test", self.user, "new")
        self.assertEqual(
            trakt_importer._get_date("2023-01-02T10:04:54.000Z"),
            datetime(2023, 1, 2, 10, 4, 0, tzinfo=UTC),
        )

    @patch("integrations.imports.trakt.TraktImporter._get_metadata")
    def test_process_watched_movie(self, mock_get_metadata):
        """Test processing a movie entry."""
        movie_entry = {
            "type": "movie",
            "movie": {"title": "Test Movie", "ids": {"tmdb": 67890}},
            "watched_at": "2023-01-02T00:00:59.000Z",
        }

        mock_get_metadata.return_value = {
            "title": "Test Movie",
            "image": "movie_image.jpg",
        }

        trakt_importer = TraktImporter("test", self.user, "new")
        trakt_importer.process_watched_movie(movie_entry)

        self.assertEqual(len(trakt_importer.bulk_media[MediaTypes.MOVIE.value]), 1)
        self.assertEqual(len(trakt_importer.media_instances[MediaTypes.MOVIE.value]), 1)

        # Verify progress is set to 1 for completed movies
        movie_obj = trakt_importer.bulk_media[MediaTypes.MOVIE.value][0]
        self.assertEqual(movie_obj.progress, 1)

        # watched_at seconds should be stripped from end_date
        self.assertEqual(movie_obj.end_date.second, 0)

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
            "watched_at": "2023-01-01T00:00:59.000Z",
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

        # watched_at seconds should be stripped from end_date
        episode_obj = trakt_importer.bulk_media[MediaTypes.EPISODE.value][0]
        self.assertEqual(episode_obj.end_date.second, 0)

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

    @patch("integrations.imports.trakt.update_refresh_token")
    @patch("app.providers.services.api_request")
    def test_get_access_token_uses_redirect_uri(self, mock_api_request, _):
        """Test refreshing Trakt tokens sends the configured redirect URI."""
        mock_api_request.return_value = {
            "access_token": "access-token",
            "refresh_token": "new-refresh-token",
        }
        encrypted_token = helpers.encrypt("refresh-token")

        access_token = get_access_token(
            encrypted_token,
            redirect_uri="https://yamtrack.example.com/import/trakt/private",
        )

        self.assertEqual(access_token, "access-token")
        params = mock_api_request.call_args.kwargs["params"]
        self.assertEqual(
            params["redirect_uri"],
            "https://yamtrack.example.com/import/trakt/private",
        )

    @patch("integrations.imports.trakt.TraktImporter._make_api_request")
    @patch("integrations.imports.trakt.TraktImporter._get_metadata")
    def test_process_lists(self, mock_get_metadata, mock_make_request):
        """Personal lists, collaborations and favorites become custom lists."""
        personal_list = {
            "name": "Personal",
            "description": "My list",
            "ids": {"trakt": 1, "slug": "personal"},
        }
        collaboration = {
            "name": "Shared",
            "description": "",
            "ids": {"trakt": 2, "slug": "shared"},
        }
        entry = {
            "type": "movie",
            "movie": {"title": "Listed Movie", "ids": {"tmdb": 42}},
            "listed_at": "2023-01-01T00:00:00.000Z",
        }

        mock_make_request.side_effect = [
            [personal_list],  # /lists
            [collaboration],  # /lists/collaborations
            [entry],  # /lists/1/items
            [entry],  # /lists/2/items
            [entry],  # /favorites
        ]
        mock_get_metadata.return_value = {
            "title": "Listed Movie",
            "image": "movie.jpg",
        }

        trakt_importer = TraktImporter("testuser", self.user, "new")
        trakt_importer.process_lists()

        self.assertEqual(
            list(trakt_importer.custom_lists),
            ["Personal", "Shared", FAVORITES_LIST_NAME],
        )
        self.assertEqual(trakt_importer.save_custom_lists(), 3)
        self.assertEqual(CustomList.objects.filter(owner=self.user).count(), 3)

        personal = CustomList.objects.get(owner=self.user, name="Personal")
        self.assertEqual(personal.description, "My list")
        self.assertEqual(personal.items.count(), 1)

        # The list endpoints are read without pagination parameters.
        called = [call.args[0] for call in mock_make_request.call_args_list]
        self.assertEqual(
            called,
            [
                "https://api.trakt.tv/users/testuser/lists",
                "https://api.trakt.tv/users/testuser/lists/collaborations",
                "https://api.trakt.tv/users/testuser/lists/1/items",
                "https://api.trakt.tv/users/testuser/lists/2/items",
                "https://api.trakt.tv/users/testuser/favorites",
            ],
        )

    @patch("integrations.imports.trakt.TraktImporter._make_api_request")
    def test_private_lists_are_reported_not_fatal(self, mock_make_request):
        """Lists a public profile does not share are skipped with a warning."""
        response = Response()
        response.status_code = 403
        mock_make_request.side_effect = HTTPError(response=response)

        trakt_importer = TraktImporter("testuser", self.user, "new")
        trakt_importer.process_lists()

        self.assertEqual(trakt_importer.custom_lists, {})
        self.assertIn("Trakt lists: not available, skipped.", trakt_importer.warnings)

    @patch("integrations.imports.trakt.TraktImporter._make_api_request")
    def test_list_request_error_is_raised(self, mock_make_request):
        """An unexpected error reading a list is not swallowed."""
        response = Response()
        response.status_code = 500
        mock_make_request.side_effect = HTTPError(response=response)

        trakt_importer = TraktImporter("testuser", self.user, "new")

        with self.assertRaises(HTTPError):
            trakt_importer.process_lists()

    @patch("integrations.imports.trakt.TraktImporter._make_api_request")
    @patch("integrations.imports.trakt.TraktImporter._get_metadata")
    def test_unsupported_list_entries_are_reported(
        self,
        mock_get_metadata,
        mock_make_request,
    ):
        """People in a Trakt list cannot be tracked, so they are reported."""
        mock_make_request.side_effect = [
            [{"name": "People", "ids": {"trakt": 1, "slug": "people"}}],
            [],
            [
                {
                    "type": "person",
                    "person": {"name": "Bryan Cranston", "ids": {"tmdb": 17419}},
                    "listed_at": "2023-01-01T00:00:00.000Z",
                },
            ],
            [],
        ]
        mock_get_metadata.return_value = None

        trakt_importer = TraktImporter("testuser", self.user, "new")
        trakt_importer.process_lists()

        self.assertEqual(trakt_importer.save_custom_lists(), 0)
        self.assertFalse(CustomList.objects.filter(owner=self.user).exists())
        self.assertIn(
            "People: list entries of type person are not supported, skipped.",
            trakt_importer.warnings,
        )

    @patch("integrations.imports.trakt.TraktImporter._make_api_request")
    @patch("integrations.imports.trakt.TraktImporter._get_metadata")
    def test_unnamed_list_is_skipped(self, mock_get_metadata, mock_make_request):
        """A list without a name has nowhere to go in Yamtrack."""
        mock_make_request.side_effect = [
            [{"name": "  ", "ids": {"trakt": 1, "slug": ""}}],
            [],
            [],
        ]
        mock_get_metadata.return_value = None

        trakt_importer = TraktImporter("testuser", self.user, "new")
        trakt_importer.process_lists()

        self.assertEqual(trakt_importer.custom_lists, {})
        self.assertIn(
            "A Trakt list without a name was skipped.",
            trakt_importer.warnings,
        )

    @patch("integrations.imports.trakt.TraktImporter._make_api_request")
    @patch("integrations.imports.trakt.TraktImporter._get_metadata")
    def test_existing_list_is_merged(self, mock_get_metadata, mock_make_request):
        """Importing twice adds the missing items instead of duplicating lists."""
        first = {
            "type": "movie",
            "movie": {"title": "First Movie", "ids": {"tmdb": 1}},
            "listed_at": "2023-01-01T00:00:00.000Z",
        }
        second = {
            "type": "movie",
            "movie": {"title": "Second Movie", "ids": {"tmdb": 2}},
            "listed_at": "2023-02-01T00:00:00.000Z",
        }
        personal_list = {"name": "Personal", "ids": {"trakt": 1, "slug": "personal"}}

        mock_get_metadata.side_effect = lambda *args: {
            "title": args[2],
            "image": "movie.jpg",
        }

        for entries in ([first], [first, second]):
            mock_make_request.side_effect = [[personal_list], [], entries, []]
            trakt_importer = TraktImporter("testuser", self.user, "new")
            trakt_importer.process_lists()
            trakt_importer.save_custom_lists()

        self.assertEqual(CustomList.objects.filter(owner=self.user).count(), 1)
        custom_list = CustomList.objects.get(owner=self.user, name="Personal")
        self.assertEqual(custom_list.items.count(), 2)


def build_archive(files, prefix=""):
    """Build an in-memory Trakt export archive from a name to contents mapping."""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, contents in files.items():
            payload = contents if isinstance(contents, str) else json.dumps(contents)
            archive.writestr(f"{prefix}{name}.json", payload)
    buffer.seek(0)
    return buffer


MOVIE_HISTORY_ENTRY = {
    "type": "movie",
    "movie": {"title": "Export Movie", "ids": {"tmdb": 111}},
    "watched_at": "2023-01-01T00:00:00.000Z",
}


class ImportTraktExport(TestCase):
    """Test importing media from a Trakt export archive."""

    def setUp(self):
        """Create user for the tests."""
        credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**credentials)
        self.metadata_patcher = patch(
            "integrations.imports.trakt.TraktImporter._get_metadata",
        )
        mock_get_metadata = self.metadata_patcher.start()
        mock_get_metadata.return_value = {
            "title": "Export Movie",
            "image": "movie.jpg",
        }
        self.addCleanup(self.metadata_patcher.stop)

    def test_import_full_flow(self):
        """A minimal export archive imports through the standard pipeline."""
        archive = build_archive(
            {
                "user-profile": {"username": "exported_user"},
                "watched-history": [MOVIE_HISTORY_ENTRY],
                "lists-watchlist": [],
                "ratings-movies": [],
                "comments-movies": [],
            },
        )

        imported_counts, messages = importer(None, self.user, "new", file=archive)

        self.assertEqual(imported_counts[MediaTypes.MOVIE.value], 1)
        self.assertEqual(Movie.objects.filter(user=self.user).count(), 1)
        self.assertEqual(messages, "")

    def test_username_read_from_profile(self):
        """The Trakt username is taken from user-profile.json."""
        archive = build_archive(
            {
                "user-profile": {"username": "exported_user"},
                "watched-history": [],
            },
        )

        export_importer = TraktExportImporter(archive, self.user, "new")

        self.assertEqual(export_importer.username, "exported_user")

    def test_missing_user_profile_falls_back(self):
        """A missing user-profile.json does not break the import."""
        archive = build_archive({"watched-history": [MOVIE_HISTORY_ENTRY]})

        export_importer = TraktExportImporter(archive, self.user, "new")
        imported_counts, _ = export_importer.import_data()

        self.assertEqual(export_importer.username, EXPORT_FALLBACK_USERNAME)
        self.assertEqual(imported_counts[MediaTypes.MOVIE.value], 1)

    def test_nested_archive_is_read(self):
        """Archives that keep the export inside a directory are still read."""
        archive = build_archive(
            {
                "user-profile": {"username": "exported_user"},
                "watched-history": [MOVIE_HISTORY_ENTRY],
            },
            prefix="trakt-export/",
        )

        imported_counts, _ = importer(None, self.user, "new", file=archive)

        self.assertEqual(imported_counts[MediaTypes.MOVIE.value], 1)

    def test_paged_files_are_read_in_order(self):
        """Numbered history pages are concatenated in page order."""
        archive = build_archive(
            {
                "user-profile": {"username": "exported_user"},
                "watched-history-1": [MOVIE_HISTORY_ENTRY],
                "watched-history-2": [
                    {
                        "type": "movie",
                        "movie": {"title": "Second Movie", "ids": {"tmdb": 222}},
                        "watched_at": "2023-02-01T00:00:00.000Z",
                    },
                ],
            },
        )

        manager = TraktArchiveManager(archive)

        self.assertEqual(
            manager._match_names("watched-history"),
            ["watched-history-1", "watched-history-2"],
        )
        self.assertEqual(len(manager.load("watched-history")), 2)

    def test_custom_list_is_not_read_as_watchlist_page(self):
        """A custom list ending in digits is not treated as a watchlist page."""
        archive = build_archive(
            {
                "user-profile": {"username": "exported_user"},
                "lists-watchlist": [],
                "lists-watchlist-2025": [
                    {
                        "type": "movie",
                        "movie": {"title": "Custom List Movie", "ids": {"tmdb": 333}},
                    },
                ],
            },
        )

        manager = TraktArchiveManager(archive)

        self.assertEqual(manager._match_names("lists-watchlist"), ["lists-watchlist"])
        self.assertEqual(manager.load("lists-watchlist"), [])

    def test_unreadable_files_are_reported(self):
        """Every unreadable export file is reported in the import summary."""
        archive = build_archive(
            {
                "user-profile": {"username": "exported_user"},
                "watched-history": [],
                "ratings-movies": "not json",
                "ratings-shows": "also not json",
                "comments-movies": {"unexpected": "shape"},
            },
        )

        _, messages = importer(None, self.user, "new", file=archive)

        self.assertIn("ratings-movies.json: could not be read, skipped.", messages)
        self.assertIn("ratings-shows.json: could not be read, skipped.", messages)
        self.assertIn("comments-movies.json: unexpected contents, skipped.", messages)

    def test_invalid_zip_raises(self):
        """A file that is not a ZIP archive raises a readable error."""
        with self.assertRaises(MediaImportError):
            TraktArchiveManager(BytesIO(b"definitely not a zip"))

    def test_unrecognized_archive_raises(self):
        """A ZIP without any Trakt export file raises a readable error."""
        archive = build_archive({"something-else": []})

        with self.assertRaises(MediaImportError):
            TraktArchiveManager(archive)

    def test_oversized_archive_raises(self):
        """An archive larger than the uncompressed cap is rejected."""
        archive = build_archive(
            {
                "user-profile": {"username": "exported_user"},
                "watched-history": " " * 128,
            },
        )

        with (
            patch("integrations.imports.trakt.MAX_EXPORT_UNCOMPRESSED_BYTES", 16),
            self.assertRaises(MediaImportError),
        ):
            TraktArchiveManager(archive)

    def test_list_items_file_is_read(self):
        """A custom list's items are read from the file Trakt named after it."""
        archive = build_archive(
            {
                "user-profile": {"username": "exported_user"},
                "lists-lists": [
                    {"name": "Picks", "ids": {"trakt": 7, "slug": "picks"}},
                ],
                "lists-list-7-picks": [
                    {
                        "type": "movie",
                        "movie": {"title": "Export Movie", "ids": {"tmdb": 111}},
                        "listed_at": "2023-01-01T00:00:00.000Z",
                    },
                ],
            },
        )

        imported_counts, messages = importer(None, self.user, "new", file=archive)

        self.assertEqual(imported_counts[helpers.CUSTOM_LIST_COUNT_KEY], 1)
        self.assertEqual(messages, "")
        custom_list = CustomList.objects.get(owner=self.user, name="Picks")
        self.assertEqual(custom_list.items.count(), 1)

    def test_renamed_list_items_file_is_still_read(self):
        """A list renamed after the export was written is matched by its id."""
        archive = build_archive(
            {
                "user-profile": {"username": "exported_user"},
                "lists-lists": [
                    {"name": "Picks", "ids": {"trakt": 7, "slug": "new-slug"}},
                ],
                "lists-list-7-old-slug": [
                    {
                        "type": "movie",
                        "movie": {"title": "Export Movie", "ids": {"tmdb": 111}},
                        "listed_at": "2023-01-01T00:00:00.000Z",
                    },
                ],
            },
        )

        _, messages = importer(None, self.user, "new", file=archive)

        self.assertEqual(messages, "")
        self.assertEqual(
            CustomList.objects.get(owner=self.user, name="Picks").items.count(),
            1,
        )

    def test_paged_list_items_files_are_read(self):
        """A list whose items Trakt split across pages is read in full."""
        archive = build_archive(
            {
                "user-profile": {"username": "exported_user"},
                "lists-lists": [
                    {"name": "Picks", "ids": {"trakt": 7, "slug": "picks"}},
                ],
                "lists-list-7-picks-1": [
                    {
                        "type": "movie",
                        "movie": {"title": "First Movie", "ids": {"tmdb": 111}},
                        "listed_at": "2023-01-01T00:00:00.000Z",
                    },
                ],
                "lists-list-7-picks-2": [
                    {
                        "type": "movie",
                        "movie": {"title": "Second Movie", "ids": {"tmdb": 222}},
                        "listed_at": "2023-02-01T00:00:00.000Z",
                    },
                ],
            },
        )

        _, messages = importer(None, self.user, "new", file=archive)

        self.assertEqual(messages, "")
        self.assertEqual(
            CustomList.objects.get(owner=self.user, name="Picks").items.count(),
            2,
        )

    def test_missing_list_items_file_is_reported(self):
        """A list whose items file is absent is reported and skipped."""
        archive = build_archive(
            {
                "user-profile": {"username": "exported_user"},
                "lists-lists": [
                    {"name": "Picks", "ids": {"trakt": 7, "slug": "picks"}},
                ],
            },
        )

        _, messages = importer(None, self.user, "new", file=archive)

        self.assertIn(
            "lists-list-7.json: missing from the export, skipped.",
            messages,
        )
        self.assertFalse(CustomList.objects.filter(owner=self.user).exists())

    def test_favorites_file_becomes_a_list(self):
        """lists-favorites.json is imported as a list of its own."""
        archive = build_archive(
            {
                "user-profile": {"username": "exported_user"},
                "lists-favorites": [
                    {
                        "type": "movie",
                        "movie": {"title": "Export Movie", "ids": {"tmdb": 111}},
                        "listed_at": "2023-01-01T00:00:00.000Z",
                    },
                ],
            },
        )

        _, messages = importer(None, self.user, "new", file=archive)

        self.assertEqual(messages, "")
        self.assertEqual(
            CustomList.objects.get(
                owner=self.user,
                name=FAVORITES_LIST_NAME,
            ).items.count(),
            1,
        )

    def test_list_items_keep_their_trakt_date(self):
        """date_added holds the date Trakt listed the item, not the import time."""
        archive = build_archive(
            {
                "user-profile": {"username": "exported_user"},
                "lists-favorites": [
                    {
                        "type": "movie",
                        "movie": {"title": "Export Movie", "ids": {"tmdb": 111}},
                        "listed_at": "2023-01-01T00:00:00.000Z",
                    },
                ],
            },
        )

        importer(None, self.user, "new", file=archive)

        list_item = CustomListItem.objects.get(custom_list__owner=self.user)
        self.assertEqual(
            list_item.date_added,
            datetime(2023, 1, 1, tzinfo=UTC),
        )


# Enough episodes for every season in the sample export, so the fake metadata
# never rejects an episode number the real archive contains.
FIXTURE_SEASON_EPISODES = 140

# Trakt left comments-episodes.json out of the sample export even though it wrote
# empty comments-movies.json and comments-shows.json files.
EXPORT_FILES_WITHOUT_SAMPLES = {"comments-episodes"}


def build_fixture_archive():
    """Zip the sample Trakt export checked into mock_data."""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in sorted(trakt_export_path.glob("*.json")):
            archive.writestr(path.name, path.read_text())
    buffer.seek(0)
    return buffer


def fake_metadata(media_type, tmdb_id, title, season_number=None):  # noqa: ARG001
    """Stand in for TMDB lookups when importing the sample export."""
    metadata = {"title": title, "image": f"{tmdb_id}.jpg"}
    if media_type == MediaTypes.SEASON.value:
        metadata["episodes"] = [
            {"episode_number": number, "still_path": f"/{number}.jpg"}
            for number in range(1, FIXTURE_SEASON_EPISODES + 1)
        ]
        metadata["max_progress"] = FIXTURE_SEASON_EPISODES
    return metadata


class ImportTraktSampleExport(TestCase):
    """Test importing the sample Trakt export archive in mock_data."""

    @classmethod
    def setUpTestData(cls):
        """Import the sample export once for the whole class."""
        credentials = {"username": "test", "password": "12345"}
        cls.user = get_user_model().objects.create_user(**credentials)
        with patch(
            "integrations.imports.trakt.TraktImporter._get_metadata",
            side_effect=fake_metadata,
        ):
            cls.imported_counts, cls.messages = importer(
                None,
                cls.user,
                "new",
                file=build_fixture_archive(),
            )

    def test_sample_export_imports(self):
        """Every media type in the sample export reaches the database."""
        self.assertEqual(self.messages, "")
        self.assertEqual(
            self.imported_counts[MediaTypes.MOVIE.value],
            Movie.objects.filter(user=self.user).count(),
        )
        self.assertEqual(
            self.imported_counts[MediaTypes.TV.value],
            TV.objects.filter(user=self.user).count(),
        )
        self.assertEqual(
            self.imported_counts[MediaTypes.SEASON.value],
            Season.objects.filter(user=self.user).count(),
        )
        self.assertEqual(
            self.imported_counts[MediaTypes.EPISODE.value],
            Episode.objects.filter(related_season__user=self.user).count(),
        )

    def test_username_read_from_sample_profile(self):
        """The username comes from the sample user-profile.json."""
        export_importer = TraktExportImporter(build_fixture_archive(), self.user, "new")

        self.assertEqual(export_importer.username, "yamtrack_user")

    def test_history_is_imported_oldest_first(self):
        """The export lists history newest first, so entries are reversed."""
        history = json.loads((trakt_export_path / "watched-history.json").read_text())
        watched_at = [entry["watched_at"] for entry in history]
        self.assertEqual(watched_at, sorted(watched_at, reverse=True))

        with patch(
            "integrations.imports.trakt.TraktImporter._get_metadata",
            side_effect=fake_metadata,
        ):
            export_importer = TraktExportImporter(
                build_fixture_archive(),
                self.user,
                "new",
            )
            export_importer.process_history()

        end_dates = [
            episode.end_date
            for episode in export_importer.bulk_media[MediaTypes.EPISODE.value]
        ]
        self.assertEqual(end_dates, sorted(end_dates))

    def test_show_rating_is_imported(self):
        """A rated show from ratings-shows.json keeps its score."""
        # Breaking Bad, rated 8 in the sample export and absent from the history.
        breaking_bad = TV.objects.get(user=self.user, item__media_id="1396")

        self.assertEqual(breaking_bad.score, 8)

    def test_season_rating_is_imported(self):
        """A rated season from ratings-seasons.json keeps its score."""
        # Breaking Bad season 4, rated 8 in the sample export.
        season = Season.objects.get(
            user=self.user,
            item__media_id="1396",
            item__season_number=4,
        )

        self.assertEqual(season.score, 8)

    def test_watchlist_entries_are_planning(self):
        """Watchlist entries are imported with the planning status."""
        # Perfect Blue is only on the watchlist, never watched.
        perfect_blue = Movie.objects.get(user=self.user, item__media_id="10494")

        self.assertEqual(perfect_blue.status, Status.PLANNING.value)

    def test_season_comment_becomes_notes(self):
        """A comment from comments-seasons.json is stored as notes."""
        # Friends season 10, commented on in the sample export.
        season = Season.objects.get(
            user=self.user,
            item__media_id="1668",
            item__season_number=10,
        )

        self.assertEqual(season.notes, "it was okay i guess")

    def test_episode_ratings_are_skipped(self):
        """Episode ratings are dropped because Episode has no score field."""
        ratings = json.loads((trakt_export_path / "ratings-episodes.json").read_text())
        self.assertTrue(any(entry["type"] == "episode" for entry in ratings))

        # Skipped silently, so the import reports no warning about them.
        self.assertEqual(self.messages, "")

    def test_repeat_plays_are_kept(self):
        """A rewatched episode keeps one record per play."""
        # Reacher S1E1 was watched three times in the sample export.
        episodes = Episode.objects.filter(
            related_season__user=self.user,
            item__media_id="108978",
            item__season_number=1,
            item__episode_number=1,
        )

        self.assertEqual(episodes.count(), 3)

    def test_personal_list_is_imported(self):
        """lists-lists.json and its items file become a custom list."""
        custom_list = CustomList.objects.get(owner=self.user, name="Rewatch Soon")

        self.assertEqual(custom_list.description, "Things worth a second look")
        self.assertEqual(custom_list.items.count(), 5)
        self.assertEqual(
            sorted(custom_list.items.values_list("media_type", flat=True)),
            [
                MediaTypes.EPISODE.value,
                MediaTypes.MOVIE.value,
                MediaTypes.MOVIE.value,
                MediaTypes.SEASON.value,
                MediaTypes.TV.value,
            ],
        )

    def test_collaboration_list_is_imported(self):
        """A list the user only collaborates on is imported as their own."""
        custom_list = CustomList.objects.get(owner=self.user, name="Shared Picks")

        self.assertEqual(custom_list.items.count(), 1)

    def test_favorites_become_a_list(self):
        """Trakt favorites are imported as a list named after them."""
        custom_list = CustomList.objects.get(owner=self.user, name=FAVORITES_LIST_NAME)

        self.assertEqual(custom_list.items.count(), 2)

    def test_imported_counts_report_lists(self):
        """The import summary counts the lists it created."""
        self.assertEqual(self.imported_counts[helpers.CUSTOM_LIST_COUNT_KEY], 3)

    def test_list_items_keep_their_trakt_date(self):
        """Items are ordered by the date Trakt listed them, not the import time."""
        custom_list = CustomList.objects.get(owner=self.user, name="Rewatch Soon")
        list_items = CustomListItem.objects.filter(custom_list=custom_list)

        self.assertEqual(
            list_items.earliest("date_added").date_added,
            datetime(2021, 3, 2, 10, 16, tzinfo=UTC),
        )
        self.assertEqual(
            list_items.latest("date_added").date_added,
            datetime(2025, 1, 4, 18, 22, 11, tzinfo=UTC),
        )

    def test_listed_media_is_not_tracked(self):
        """Listing an item does not give it a status of its own."""
        # The Matrix is only on the Rewatch Soon list, never watched or rated.
        self.assertTrue(
            Item.objects.filter(
                media_id="603",
                media_type=MediaTypes.MOVIE.value,
            ).exists(),
        )
        self.assertFalse(
            Movie.objects.filter(user=self.user, item__media_id="603").exists(),
        )

    def test_every_mapped_prefix_has_a_sample_file(self):
        """The sample export covers the files the importer looks for."""
        expected = {
            prefix for prefixes in ENDPOINTS_TO_FILES.values() for prefix in prefixes
        }
        available = {path.stem for path in trakt_export_path.glob("*.json")}

        self.assertLessEqual(expected - available, EXPORT_FILES_WITHOUT_SAMPLES)
