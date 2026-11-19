from django.test import SimpleTestCase

from api.helpers import validate_media_path
from app.models import MediaTypes, Sources


class ValidateMediaPathTests(SimpleTestCase):
    """Validate the shared media type and source checks of the views."""

    def assert_error(self, response, detail):
        """Assert that the response is a bad request with the given detail."""
        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {"detail": detail})

    def test_valid_path_returns_none(self):
        """A valid media type and source should not return an error."""
        self.assertIsNone(validate_media_path(MediaTypes.MOVIE.value))
        self.assertIsNone(
            validate_media_path(MediaTypes.MOVIE.value, Sources.TMDB.value),
        )
        self.assertIsNone(
            validate_media_path(
                MediaTypes.TV.value,
                Sources.TMDB.value,
                child_type=MediaTypes.SEASON.value,
                sync=True,
            ),
        )

    def test_unsupported_media_type(self):
        """Unknown media types, and children types unless complete, are rejected."""
        self.assert_error(validate_media_path("invalid"), "Unsupported media type.")
        self.assert_error(
            validate_media_path(MediaTypes.SEASON.value),
            "Unsupported media type.",
        )
        self.assertIsNone(
            validate_media_path(MediaTypes.SEASON.value, complete=True),
        )

    def test_children_require_tv(self):
        """Season and episode routes are only supported for TV shows."""
        self.assert_error(
            validate_media_path(
                MediaTypes.MOVIE.value,
                Sources.TMDB.value,
                child_type=MediaTypes.SEASON.value,
            ),
            "Seasons are supported only for 'tv' media type.",
        )
        self.assert_error(
            validate_media_path(
                MediaTypes.MOVIE.value,
                Sources.TMDB.value,
                child_type=MediaTypes.EPISODE.value,
            ),
            "Episodes are supported only for 'tv' media type.",
        )

    def test_invalid_source(self):
        """Sources invalid for the media type are rejected."""
        self.assert_error(
            validate_media_path(MediaTypes.MOVIE.value, Sources.MAL.value),
            "Cannot query `mal` for `movie` media type",
        )
        self.assert_error(
            validate_media_path(MediaTypes.MOVIE.value, Sources.MAL.value, sync=True),
            "Cannot sync `mal` for `movie` media type",
        )

    def test_sync_rejects_manual_source(self):
        """Manual items cannot be synced, but can be queried."""
        self.assert_error(
            validate_media_path(
                MediaTypes.MOVIE.value,
                Sources.MANUAL.value,
                sync=True,
            ),
            "Manual items cannot be synced.",
        )
        self.assertIsNone(
            validate_media_path(MediaTypes.MOVIE.value, Sources.MANUAL.value),
        )
