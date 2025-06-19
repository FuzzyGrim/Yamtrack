from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import Item, MediaTypes, Sources
from app.templatetags import app_tags


class AppTagsTests(TestCase):
    """Test the app template tags."""

    def setUp(self):
        """Set up test data."""
        # Create a sample item for testing
        self.tv_item = Item(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test TV Show",
        )

        self.season_item = Item(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test TV Show",
            season_number=1,
        )

        self.episode_item = Item(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Test TV Show",
            season_number=1,
            episode_number=1,
        )

        # Create a dict version for testing dict-based functions
        self.tv_dict = {
            "media_id": "1668",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "title": "Test TV Show",
        }

        self.season_dict = {
            "media_id": "1668",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.SEASON.value,
            "title": "Test TV Show",
            "season_number": 1,
        }

        self.episode_dict = {
            "media_id": "1668",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.EPISODE.value,
            "title": "Test TV Show",
            "season_number": 1,
            "episode_number": 1,
        }

    @patch("pathlib.Path.stat")
    def test_get_static_file_mtime(self, mock_stat):
        """Test the get_static_file_mtime tag."""
        # Mock the stat method to return a fixed mtime
        mock_stat_result = MagicMock()
        mock_stat_result.st_mtime = 1234567890
        mock_stat.return_value = mock_stat_result

        # Test with a valid file
        result = app_tags.get_static_file_mtime("css/style.css")
        self.assertEqual(result, "?1234567890")

        # Test with file not found
        mock_stat.side_effect = OSError()
        result = app_tags.get_static_file_mtime("nonexistent.css")
        self.assertEqual(result, "")

    def test_no_underscore(self):
        """Test the no_underscore filter."""
        self.assertEqual(app_tags.no_underscore("hello_world"), "hello world")
        self.assertEqual(
            app_tags.no_underscore("test_string_with_underscores"),
            "test string with underscores",
        )
        self.assertEqual(
            app_tags.no_underscore("no_underscores_here"),
            "no underscores here",
        )

    def test_slug(self):
        """Test the slug filter."""
        # Test normal slugification
        self.assertEqual(app_tags.slug("Hello World"), "hello-world")

        # Test with special characters
        self.assertEqual(app_tags.slug("Anime: 31687"), "anime-31687")
        self.assertEqual(app_tags.slug("★★★"), "%E2%98%85%E2%98%85%E2%98%85")
        self.assertEqual(app_tags.slug("[Oshi no Ko]"), "oshi-no-ko")
        self.assertEqual(app_tags.slug("_____"), "_____")

    def test_media_type_readable(self):
        """Test the media_type_readable filter."""
        # Test all media types from the MediaTypes class
        for media_type, label in MediaTypes.choices:
            self.assertEqual(app_tags.media_type_readable(media_type), label)

    def test_media_type_readable_plural(self):
        """Test the media_type_readable_plural filter."""
        # Test all media types from the MediaTypes class
        for media_type, label in MediaTypes.choices:
            singular = label

            # Special cases that don't change in plural form
            if singular.lower() in [MediaTypes.ANIME.value, MediaTypes.MANGA.value]:
                expected = singular
            else:
                expected = f"{singular}s"

            self.assertEqual(app_tags.media_type_readable_plural(media_type), expected)

    def test_default_source(self):
        """Test the default_source filter."""
        # Test all media types from the MediaTypes class
        for media_type in MediaTypes.values:
            result = app_tags.default_source(media_type)

            # Check that it returns a non-empty string
            self.assertTrue(isinstance(result, str))
            self.assertTrue(len(result) > 0)

            # This implicitly checks that all media types are handled
        try:
            app_tags.default_source(media_type)
        except KeyError:
            self.fail(f"default_source raised KeyError for {media_type}")

    def test_media_past_verb(self):
        """Test the media_past_verb filter."""
        # Test all media types
        for media_type in MediaTypes.values:
            result = app_tags.media_past_verb(media_type)

            # Check that it returns a non-empty string
            self.assertTrue(isinstance(result, str))

    def test_sample_search(self):
        """Test the sample_search filter."""
        # Test all media types
        for media_type in MediaTypes.values:
            if media_type in (MediaTypes.SEASON.value, MediaTypes.EPISODE.value):
                # Skip season and episode for sample_search
                continue

            result = app_tags.sample_search(media_type)

            self.assertIn("/search", result)
            self.assertIn(f"media_type={media_type}", result)
            self.assertIn("q=", result)

    def test_media_color(self):
        """Test the media_color filter."""
        # Test all media types
        for media_type in MediaTypes.values:
            result = app_tags.media_color(media_type)

            # Check that it returns a non-empty string
            self.assertTrue(isinstance(result, str))

    def test_natural_day(self):
        """Test the natural_day filter."""
        # Mock current date to March 29, 2025
        with patch("django.utils.timezone.now") as mock_now:
            # Use timezone.datetime to create timezone-aware datetimes
            mock_now.return_value = timezone.datetime(
                2025,
                3,
                29,
                12,
                0,
                0,
                tzinfo=timezone.get_current_timezone(),
            )

            # Test today
            today = timezone.datetime(
                2025,
                3,
                29,
                15,
                0,
                0,
                tzinfo=timezone.get_current_timezone(),
            )
            self.assertEqual(app_tags.natural_day(today), "Today")

            # Test tomorrow
            tomorrow = timezone.datetime(
                2025,
                3,
                30,
                15,
                0,
                0,
                tzinfo=timezone.get_current_timezone(),
            )
            self.assertEqual(app_tags.natural_day(tomorrow), "Tomorrow")

            # Test in X days
            in_3_days = timezone.datetime(
                2025,
                4,
                1,
                15,
                0,
                0,
                tzinfo=timezone.get_current_timezone(),
            )
            self.assertEqual(app_tags.natural_day(in_3_days), "In 3 days")

            # Test further away
            further = timezone.datetime(
                2025,
                4,
                10,
                15,
                0,
                0,
                tzinfo=timezone.get_current_timezone(),
            )
            self.assertEqual(app_tags.natural_day(further), "Apr 10")

    def test_media_url(self):
        """Test the media_url filter."""
        # Test with object for TV
        tv_url = app_tags.media_url(self.tv_item)
        expected_tv_url = reverse(
            "media_details",
            kwargs={
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.TV.value,
                "media_id": "1668",
                "title": "test-tv-show",
            },
        )
        self.assertEqual(tv_url, expected_tv_url)

        # Test with dict for TV
        tv_dict_url = app_tags.media_url(self.tv_dict)
        self.assertEqual(tv_dict_url, expected_tv_url)

        # Test with object for Season
        season_url = app_tags.media_url(self.season_item)
        expected_season_url = reverse(
            "season_details",
            kwargs={
                "source": Sources.TMDB.value,
                "media_id": "1668",
                "title": "test-tv-show",
                "season_number": 1,
            },
        )
        self.assertEqual(season_url, expected_season_url)

        # Test with dict for Season
        season_dict_url = app_tags.media_url(self.season_dict)
        self.assertEqual(season_dict_url, expected_season_url)

    def test_component_id(self):
        """Test the component_id tag."""
        # Test with object for TV
        tv_id = app_tags.component_id("card", self.tv_item)
        self.assertEqual(tv_id, "card-tv-1668")

        # Test with dict for TV
        tv_dict_id = app_tags.component_id("card", self.tv_dict)
        self.assertEqual(tv_dict_id, "card-tv-1668")

        # Test with object for Season
        season_id = app_tags.component_id("card", self.season_item)
        self.assertEqual(season_id, "card-season-1668-1")

        # Test with dict for Season
        season_dict_id = app_tags.component_id("card", self.season_dict)
        self.assertEqual(season_dict_id, "card-season-1668-1")

        # Test with object for Episode
        episode_id = app_tags.component_id("card", self.episode_item)
        self.assertEqual(episode_id, "card-episode-1668-1-1")

        # Test with dict for Episode
        episode_dict_id = app_tags.component_id("card", self.episode_dict)
        self.assertEqual(episode_dict_id, "card-episode-1668-1-1")

    def test_media_view_url(self):
        """Test the media_view_url tag."""
        # Test with object for TV
        tv_modal = app_tags.media_view_url("track_modal", self.tv_item)
        expected_tv_modal = reverse(
            "track_modal",
            kwargs={
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.TV.value,
                "media_id": "1668",
            },
        )
        self.assertEqual(tv_modal, expected_tv_modal)

        # Test with dict for TV
        tv_dict_modal = app_tags.media_view_url("track_modal", self.tv_dict)
        self.assertEqual(tv_dict_modal, expected_tv_modal)

        # Test with object for Episode
        episode_modal = app_tags.media_view_url("history_modal", self.episode_item)
        expected_episode_modal = reverse(
            "history_modal",
            kwargs={
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.EPISODE.value,
                "media_id": "1668",
                "season_number": 1,
                "episode_number": 1,
            },
        )
        self.assertEqual(episode_modal, expected_episode_modal)

        # Test with dict for Episode
        episode_dict_modal = app_tags.media_view_url("history_modal", self.episode_dict)
        self.assertEqual(episode_dict_modal, expected_episode_modal)

    def test_unicode_icon(self):
        """Test the unicode_icon tag for all media types."""
        # Test all media types from MediaTypes
        for media_type in MediaTypes.values:
            try:
                result = app_tags.unicode_icon(media_type)
                # Just check that we get a non-empty string
                self.assertTrue(isinstance(result, str))
                self.assertTrue(len(result) > 0)
            except KeyError:
                self.fail(f"unicode_icon raised KeyError for {media_type}")

    def test_icon_media_types(self):
        """Test the icon tag for all media types."""
        # Test all media types from MediaTypes
        for media_type in MediaTypes.values:
            try:
                # Test with both active and inactive states
                active_result = app_tags.icon(media_type, is_active=True)
                inactive_result = app_tags.icon(media_type, is_active=False)

                # Just check that we get a non-empty string
                self.assertTrue(isinstance(active_result, str))
                self.assertTrue(len(active_result) > 0)
                self.assertTrue(isinstance(inactive_result, str))
                self.assertTrue(len(inactive_result) > 0)
            except KeyError:
                self.fail(f"icon raised KeyError for {media_type}")
