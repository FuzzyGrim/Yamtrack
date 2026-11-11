from types import SimpleNamespace

from django.test import SimpleTestCase
from rest_framework.exceptions import ParseError

from api.helpers import (
    MAX_RESULT_LIMIT,
    parse_limit_offset,
    parse_status_param,
    validate_media_path,
)
from app.models import MediaTypes, Sources
from users.models import MediaStatusChoices


class ValidateMediaPathTests(SimpleTestCase):
    """Validate the shared media type and source checks of the views."""

    def assert_error(self, detail, *args, **kwargs):
        """Assert that the path is rejected with a ParseError with the detail."""
        with self.assertRaises(ParseError) as context:
            validate_media_path(*args, **kwargs)
        self.assertEqual(str(context.exception.detail), detail)

    def test_valid_path_does_not_raise(self):
        """A valid media type and source should not raise an error."""
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
        self.assert_error("Unsupported media type.", "invalid")
        self.assert_error("Unsupported media type.", MediaTypes.SEASON.value)
        self.assertIsNone(
            validate_media_path(MediaTypes.SEASON.value, complete=True),
        )

    def test_children_require_tv(self):
        """Season and episode routes are only supported for TV shows."""
        self.assert_error(
            "Seasons are supported only for 'tv' media type.",
            MediaTypes.MOVIE.value,
            Sources.TMDB.value,
            child_type=MediaTypes.SEASON.value,
        )
        self.assert_error(
            "Episodes are supported only for 'tv' media type.",
            MediaTypes.MOVIE.value,
            Sources.TMDB.value,
            child_type=MediaTypes.EPISODE.value,
        )

    def test_invalid_source(self):
        """Sources invalid for the media type are rejected."""
        self.assert_error(
            "Cannot query `mal` for `movie` media type",
            MediaTypes.MOVIE.value,
            Sources.MAL.value,
        )
        self.assert_error(
            "Cannot sync `mal` for `movie` media type",
            MediaTypes.MOVIE.value,
            Sources.MAL.value,
            sync=True,
        )

    def test_sync_rejects_manual_source(self):
        """Manual items cannot be synced, but can be queried."""
        self.assert_error(
            "Manual items cannot be synced.",
            MediaTypes.MOVIE.value,
            Sources.MANUAL.value,
            sync=True,
        )
        self.assertIsNone(
            validate_media_path(MediaTypes.MOVIE.value, Sources.MANUAL.value),
        )


class ParseLimitOffsetTests(SimpleTestCase):
    """Parse the pagination query params."""

    def parse(self, **params):
        """Run the parser on a request with the given query params."""
        return parse_limit_offset(SimpleNamespace(GET=params))

    def test_defaults(self):
        """Missing or empty params use the defaults."""
        self.assertEqual(self.parse(), (20, 0))
        self.assertEqual(self.parse(limit="", offset=""), (20, 0))

    def test_valid_values(self):
        """Valid values are returned as integers."""
        self.assertEqual(self.parse(limit="5", offset="10"), (5, 10))

    def test_limit_is_capped(self):
        """The limit can't exceed the maximum."""
        self.assertEqual(self.parse(limit="100000"), (MAX_RESULT_LIMIT, 0))

    def test_invalid_values_raise(self):
        """Invalid values raise a ParseError with the same messages as before."""
        cases = [
            ({"limit": "abc"}, "Invalid limit parameter"),
            ({"offset": "abc"}, "Invalid offset parameter"),
            ({"limit": "0"}, "limit must be >0 and offset must be >=0"),
            ({"offset": "-1"}, "limit must be >0 and offset must be >=0"),
        ]
        for params, detail in cases:
            with self.subTest(params=params), self.assertRaises(ParseError) as ctx:
                self.parse(**params)
            self.assertEqual(str(ctx.exception.detail), detail)


class ParseStatusParamTests(SimpleTestCase):
    """Parse the status query param."""

    def test_empty_means_all(self):
        """A missing status doesn't filter."""
        self.assertEqual(parse_status_param(None), MediaStatusChoices.ALL)
        self.assertEqual(parse_status_param(""), MediaStatusChoices.ALL)

    def test_valid_status(self):
        """A known status code is converted to its name."""
        self.assertEqual(parse_status_param("3"), "Completed")

    def test_invalid_status_raises(self):
        """Non numeric and unknown statuses raise a ParseError."""
        for value in ("abc", "99"):
            with self.subTest(value=value), self.assertRaises(ParseError) as ctx:
                parse_status_param(value)
            self.assertEqual(str(ctx.exception.detail), "Invalid status")
