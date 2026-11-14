import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, tag
from django.urls import reverse
from django.utils import timezone

from app import statistics
from app.helpers import minutes_to_hhmm
from app.models import (
    TV,
    Anime,
    Episode,
    Item,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Status,
)
from app.templatetags import app_tags

User = get_user_model()


class StatisticsDateFilteringTests(TestCase):
    """Test the date filtering functionality in the statistics module."""

    def setUp(self):
        """Set up test data."""
        self.credentials = {"username": "testuser", "password": "testpassword"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        # Create season item
        self.season_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test TV Show",
            season_number=1,
        )

        # Create episode items
        self.episode1_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Test TV Show",
            season_number=1,
            episode_number=1,
        )

        self.episode2_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Test TV Show",
            season_number=1,
            episode_number=2,
        )

        # Create items for different date scenarios
        self.movie1_item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Movie with start and end dates",
        )

        self.movie2_item = Item.objects.create(
            media_id="239",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Movie with only start date",
        )

        self.movie3_item = Item.objects.create(
            media_id="240",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Movie with only end date",
        )

        self.movie4_item = Item.objects.create(
            media_id="241",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Movie with no dates",
        )

        self.movie5_item = Item.objects.create(
            media_id="242",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Movie outside date range (before)",
        )

        self.movie6_item = Item.objects.create(
            media_id="243",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Movie outside date range (after)",
        )

        self.movie7_item = Item.objects.create(
            media_id="244",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Movie partially in range (starts before, ends in range)",
        )

        self.movie8_item = Item.objects.create(
            media_id="245",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Movie partially in range (starts in range, ends after)",
        )

        self.season = Season.objects.create(
            user=self.user,
            item=self.season_item,
            status=Status.IN_PROGRESS.value,
            score=8.0,
        )

        # Create episodes
        self.episode1 = Episode.objects.create(
            item=self.episode1_item,
            related_season=self.season,
            end_date=datetime.datetime(2025, 1, 1, 0, 0, tzinfo=datetime.UTC),
        )

        self.episode2 = Episode.objects.create(
            item=self.episode2_item,
            related_season=self.season,
            end_date=datetime.datetime(2025, 1, 15, 0, 0, tzinfo=datetime.UTC),
        )

        # Create movies for different date scenarios
        # Case 1: Movie with both start and end dates within range
        self.movie1 = Movie.objects.create(
            user=self.user,
            item=self.movie1_item,
            status=Status.COMPLETED.value,
            score=7.5,
            start_date=datetime.datetime(2025, 2, 10, 0, 0, tzinfo=datetime.UTC),
            end_date=datetime.datetime(2025, 2, 10, 0, 0, tzinfo=datetime.UTC),
        )

        # Case 2: Movie with only start date within range
        self.movie2 = Movie.objects.create(
            user=self.user,
            item=self.movie2_item,
            status=Status.IN_PROGRESS.value,
            score=8.0,
            start_date=datetime.datetime(2025, 2, 15, 0, 0, tzinfo=datetime.UTC),
            end_date=None,
        )

        # Case 3: Movie with only end date within range
        self.movie3 = Movie.objects.create(
            user=self.user,
            item=self.movie3_item,
            status=Status.COMPLETED.value,
            score=6.5,
            start_date=None,
            end_date=datetime.datetime(2025, 2, 20, 0, 0, tzinfo=datetime.UTC),
        )

        # Case 4: Movie with no dates
        self.movie4 = Movie.objects.create(
            user=self.user,
            item=self.movie4_item,
            status=Status.PLANNING.value,
            score=None,
            start_date=None,
            end_date=None,
        )

        # Case 5: Movie with dates before range
        self.movie5 = Movie.objects.create(
            user=self.user,
            item=self.movie5_item,
            status=Status.COMPLETED.value,
            score=9.0,
            start_date=datetime.datetime(2025, 1, 10, 0, 0, tzinfo=datetime.UTC),
            end_date=datetime.datetime(2025, 1, 15, 0, 0, tzinfo=datetime.UTC),
        )

        # Case 6: Movie with dates after range
        self.movie6 = Movie.objects.create(
            user=self.user,
            item=self.movie6_item,
            status=Status.PLANNING.value,
            score=None,
            start_date=datetime.datetime(2025, 3, 10, 0, 0, tzinfo=datetime.UTC),
            end_date=datetime.datetime(2025, 3, 15, 0, 0, tzinfo=datetime.UTC),
        )

        # Case 7: Movie that starts before range but ends within range
        self.movie7 = Movie.objects.create(
            user=self.user,
            item=self.movie7_item,
            status=Status.COMPLETED.value,
            score=8.5,
            start_date=datetime.datetime(2025, 1, 20, 0, 0, tzinfo=datetime.UTC),
            end_date=datetime.datetime(2025, 2, 25, 0, 0, tzinfo=datetime.UTC),
        )

        # Case 8: Movie that starts within range but ends after range
        self.movie8 = Movie.objects.create(
            user=self.user,
            item=self.movie8_item,
            status=Status.COMPLETED.value,
            score=8.5,
            start_date=datetime.datetime(2025, 2, 25, 0, 0, tzinfo=datetime.UTC),
            end_date=datetime.datetime(2025, 3, 5, 0, 0, tzinfo=datetime.UTC),
        )

    def test_all_time_filtering(self):
        """Test when no date filtering is applied (All Time)."""
        _, media_count = statistics.get_user_media(
            self.user,
            None,
            None,
        )

        # Should include all media except Planning status items
        self.assertEqual(
            media_count["total"], 8
        )  # TV, Season, and 6 Movies (excluding Planning movie4 and movie6)
        self.assertEqual(media_count[MediaTypes.TV.value], 1)
        self.assertEqual(media_count[MediaTypes.SEASON.value], 1)
        self.assertEqual(media_count[MediaTypes.MOVIE.value], 6)

    def test_date_range_filtering(self):
        """Test filtering with a specific date range."""
        start_date = datetime.datetime(2025, 2, 1, 0, 0, tzinfo=datetime.UTC)
        end_date = datetime.datetime(2025, 2, 28, 0, 0, tzinfo=datetime.UTC)

        user_media, media_count = statistics.get_user_media(
            self.user,
            start_date,
            end_date,
        )

        # Should include:
        # - movie1 (both dates in range)
        # - movie2 (start date in range)
        # - movie3 (end date in range)
        # - movie7 (overlaps with range - starts before, ends in range)
        # - movie8 (overlaps with range - starts in range, ends after)
        # Should exclude:
        # - TV and Season (January episodes)
        # - movie4 (no dates)
        # - movie5 (both dates before range)
        # - movie6 (both dates after range)

        self.assertEqual(media_count[MediaTypes.TV.value], 0)
        self.assertEqual(media_count[MediaTypes.SEASON.value], 0)
        self.assertEqual(media_count[MediaTypes.MOVIE.value], 5)
        self.assertEqual(media_count["total"], 5)

        # Verify the specific movies included
        movie_ids = [m.item.id for m in user_media[MediaTypes.MOVIE.value]]
        self.assertIn(self.movie1_item.id, movie_ids)
        self.assertIn(self.movie2_item.id, movie_ids)
        self.assertIn(self.movie3_item.id, movie_ids)
        self.assertIn(self.movie7_item.id, movie_ids)
        self.assertIn(self.movie8_item.id, movie_ids)

        # Verify the excluded movies
        self.assertNotIn(self.movie4_item.id, movie_ids)
        self.assertNotIn(self.movie5_item.id, movie_ids)
        self.assertNotIn(self.movie6_item.id, movie_ids)

    def test_both_dates_filtering(self):
        """Test filtering for media with both start and end dates."""
        # Test case where media has both dates within range
        start_date = datetime.datetime(2025, 2, 5, 0, 0, tzinfo=datetime.UTC)
        end_date = datetime.datetime(2025, 2, 15, 0, 0, tzinfo=datetime.UTC)

        user_media, _ = statistics.get_user_media(
            self.user,
            start_date,
            end_date,
        )

        movie_ids = [m.item.id for m in user_media[MediaTypes.MOVIE.value]]

        # Should include movie1 (dates in range) and movie7 (overlaps)
        self.assertIn(self.movie1_item.id, movie_ids)
        self.assertIn(self.movie7_item.id, movie_ids)

        # Should exclude movie5 (before range) and movie6 (after range)
        self.assertNotIn(self.movie5_item.id, movie_ids)
        self.assertNotIn(self.movie6_item.id, movie_ids)

    def test_start_date_only_filtering(self):
        """Test filtering for media with only start date."""
        start_date = datetime.datetime(2025, 2, 10, 0, 0, tzinfo=datetime.UTC)
        end_date = datetime.datetime(2025, 2, 20, 0, 0, tzinfo=datetime.UTC)

        user_media, _ = statistics.get_user_media(
            self.user,
            start_date,
            end_date,
        )

        movie_ids = [m.item.id for m in user_media[MediaTypes.MOVIE.value]]

        # Should include movie2 (start date in range)
        self.assertIn(self.movie2_item.id, movie_ids)

        # Create a movie with start date outside range
        outside_item = Item.objects.create(
            media_id="246",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Movie with start date outside range",
        )

        Movie.objects.create(
            user=self.user,
            item=outside_item,
            status=Status.IN_PROGRESS.value,
            start_date=datetime.datetime(2025, 3, 1, 0, 0, tzinfo=datetime.UTC),
            end_date=None,
        )

        # Re-run the query
        user_media, _ = statistics.get_user_media(
            self.user,
            start_date,
            end_date,
        )

        movie_ids = [m.item.id for m in user_media[MediaTypes.MOVIE.value]]

        # Should not include the movie with start date outside range
        self.assertNotIn(outside_item.id, movie_ids)

    def test_end_date_only_filtering(self):
        """Test filtering for media with only end date."""
        start_date = datetime.datetime(2025, 2, 10, 0, 0, tzinfo=datetime.UTC)
        end_date = datetime.datetime(2025, 2, 20, 0, 0, tzinfo=datetime.UTC)

        user_media, _ = statistics.get_user_media(
            self.user,
            start_date,
            end_date,
        )

        movie_ids = [m.item.id for m in user_media[MediaTypes.MOVIE.value]]

        # Should include movie3 (end date in range)
        self.assertIn(self.movie3_item.id, movie_ids)

        # Create a movie with end date outside range
        outside_item = Item.objects.create(
            media_id="247",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Movie with end date outside range",
        )

        Movie.objects.create(
            user=self.user,
            item=outside_item,
            status=Status.COMPLETED.value,
            start_date=None,
            end_date=datetime.datetime(2025, 3, 1, 0, 0, tzinfo=datetime.UTC),
        )

        # Re-run the query
        user_media, _ = statistics.get_user_media(
            self.user,
            start_date,
            end_date,
        )

        movie_ids = [m.item.id for m in user_media[MediaTypes.MOVIE.value]]

        # Should not include the movie with end date outside range
        self.assertNotIn(outside_item.id, movie_ids)

    def test_no_dates_filtering(self):
        """Test that media with no dates is excluded from date-filtered results."""
        start_date = datetime.datetime(2025, 2, 1, 0, 0, tzinfo=datetime.UTC)
        end_date = datetime.datetime(2025, 2, 28, 0, 0, tzinfo=datetime.UTC)

        user_media, _ = statistics.get_user_media(
            self.user,
            start_date,
            end_date,
        )

        movie_ids = [m.item.id for m in user_media[MediaTypes.MOVIE.value]]

        # Should exclude movie4 (no dates)
        self.assertNotIn(self.movie4_item.id, movie_ids)

        # But should be included in "All Time" results
        user_media, _ = statistics.get_user_media(
            self.user,
            None,
            None,
        )

        movie_ids = [m.item.id for m in user_media[MediaTypes.MOVIE.value]]
        # movie4 has Planning status and should be excluded
        self.assertNotIn(self.movie4_item.id, movie_ids)

    def test_overlapping_ranges(self):
        """Test media with date ranges that overlap with the filter range."""
        start_date = datetime.datetime(2025, 2, 1, 0, 0, tzinfo=datetime.UTC)
        end_date = datetime.datetime(2025, 2, 28, 0, 0, tzinfo=datetime.UTC)

        user_media, _ = statistics.get_user_media(
            self.user,
            start_date,
            end_date,
        )

        movie_ids = [m.item.id for m in user_media[MediaTypes.MOVIE.value]]

        # Should include movie7 (starts before range, ends in range)
        self.assertIn(self.movie7_item.id, movie_ids)

        # Should include movie8 (starts in range, ends after range)
        self.assertIn(self.movie8_item.id, movie_ids)

        # Create a movie that completely spans the range
        spanning_item = Item.objects.create(
            media_id="248",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Movie that spans the entire range",
        )

        Movie.objects.create(
            user=self.user,
            item=spanning_item,
            status=Status.COMPLETED.value,
            start_date=datetime.datetime(2025, 1, 15, 0, 0, tzinfo=datetime.UTC),
            end_date=datetime.datetime(2025, 3, 15, 0, 0, tzinfo=datetime.UTC),
        )

        # Re-run the query
        user_media, _ = statistics.get_user_media(
            self.user,
            start_date,
            end_date,
        )

        movie_ids = [m.item.id for m in user_media[MediaTypes.MOVIE.value]]

        # Should include the spanning movie
        self.assertIn(spanning_item.id, movie_ids)


class StatisticsTests(TestCase):
    """Test the statistics module functions."""

    def setUp(self):
        """Set up test data."""
        self.credentials = {"username": "testuser", "password": "testpassword"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        # Create season item
        self.season_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test TV Show",
            season_number=1,
        )

        # Create episode items
        self.episode1_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Test TV Show",
            season_number=1,
            episode_number=1,
        )

        self.episode2_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Test TV Show",
            season_number=1,
            episode_number=2,
        )

        self.movie_item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
        )

        self.anime_item = Item.objects.create(
            media_id="437",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Test Anime",
        )

        self.season = Season.objects.create(
            user=self.user,
            item=self.season_item,
            status=Status.IN_PROGRESS.value,
            score=8.0,
        )

        # Create episodes
        self.episode1 = Episode.objects.create(
            item=self.episode1_item,
            related_season=self.season,
            end_date=datetime.datetime(2025, 1, 1, 0, 0, tzinfo=datetime.UTC),
        )

        self.episode2 = Episode.objects.create(
            item=self.episode2_item,
            related_season=self.season,
            end_date=datetime.datetime(2025, 1, 15, 0, 0, tzinfo=datetime.UTC),
        )

        # Create a movie with different dates
        self.movie = Movie.objects.create(
            user=self.user,
            item=self.movie_item,
            status=Status.PLANNING.value,
            score=7.5,
            start_date=datetime.datetime(2025, 2, 1, 0, 0, tzinfo=datetime.UTC),
            end_date=datetime.datetime(2025, 2, 1, 0, 0, tzinfo=datetime.UTC),
        )

        # Create an anime with different dates
        self.anime = Anime.objects.create(
            user=self.user,
            item=self.anime_item,
            status=Status.COMPLETED.value,
            score=None,
            start_date=datetime.datetime(2025, 3, 1, 0, 0, tzinfo=datetime.UTC),
            end_date=datetime.datetime(2025, 3, 31, 0, 0, tzinfo=datetime.UTC),
        )

    def test_get_media_type_distribution(self):
        """Test the get_media_type_distribution function."""
        media_count = {
            "total": 3,
            MediaTypes.TV: 1,
            MediaTypes.MOVIE: 1,
            MediaTypes.ANIME: 1,
            MediaTypes.BOOK: 0,  # Should be excluded
        }
        minutes_per_type = {
            MediaTypes.TV.value: 120,
            MediaTypes.MOVIE.value: 60,
            MediaTypes.ANIME.value: 30,
            MediaTypes.BOOK.value: 0,
        }

        chart_data = statistics.get_media_type_distribution(
            media_count,
            minutes_per_type,
        )

        # Check structure
        self.assertIn("labels", chart_data)
        self.assertIn("datasets", chart_data)
        self.assertEqual(len(chart_data["datasets"]), 1)
        self.assertIn("data", chart_data["datasets"][0])
        self.assertIn("backgroundColor", chart_data["datasets"][0])

        # Check content
        self.assertEqual(len(chart_data["labels"]), 3)  # 3 media types with hours > 0
        self.assertEqual(len(chart_data["datasets"][0]["data"]), 3)
        self.assertEqual(len(chart_data["datasets"][0]["backgroundColor"]), 3)
        self.assertEqual(chart_data["datasets"][0]["value_label"], "Hours")
        self.assertEqual(chart_data["datasets"][0]["value_suffix"], "h")
        self.assertEqual(chart_data["datasets"][0]["value_decimals"], 1)

        # Hours should be calculated from minutes
        expected_labels = [
            app_tags.media_type_readable(MediaTypes.TV.value),
            app_tags.media_type_readable(MediaTypes.MOVIE.value),
            app_tags.media_type_readable(MediaTypes.ANIME.value),
        ]
        self.assertEqual(chart_data["labels"], expected_labels)
        self.assertAlmostEqual(chart_data["datasets"][0]["data"][0], 2.0)
        self.assertAlmostEqual(chart_data["datasets"][0]["data"][1], 1.0)
        self.assertAlmostEqual(chart_data["datasets"][0]["data"][2], 0.5)

        # Book should be excluded (count = 0)
        self.assertNotIn("Book", chart_data["labels"])

    def test_get_status_distribution(self):
        """Test the get_status_distribution function."""
        # Create user_media dict with our test objects
        user_media = {
            MediaTypes.TV.value: TV.objects.filter(user=self.user),
            MediaTypes.MOVIE.value: Movie.objects.filter(user=self.user),
            MediaTypes.ANIME.value: Anime.objects.filter(user=self.user),
        }

        status_distribution = statistics.get_status_distribution(user_media)

        # Check structure
        self.assertIn("labels", status_distribution)
        self.assertIn("datasets", status_distribution)
        self.assertIn("total_completed", status_distribution)

        # Check content
        self.assertEqual(len(status_distribution["labels"]), 3)  # 3 media types
        self.assertEqual(
            len(status_distribution["datasets"]),
            len(Status.values),
        )  # All statuses

        # Check total completed count
        self.assertEqual(
            status_distribution["total_completed"],
            1,
        )  # Only anime is completed

        # Check individual status counts
        completed_dataset = next(
            d
            for d in status_distribution["datasets"]
            if d["label"] == Status.COMPLETED.value
        )
        in_progress_dataset = next(
            d
            for d in status_distribution["datasets"]
            if d["label"] == Status.IN_PROGRESS.value
        )
        planning_dataset = next(
            d
            for d in status_distribution["datasets"]
            if d["label"] == Status.PLANNING.value
        )

        self.assertEqual(completed_dataset["total"], 1)  # Anime
        self.assertEqual(in_progress_dataset["total"], 1)  # TV
        self.assertEqual(planning_dataset["total"], 1)  # Movie

    def test_get_status_pie_chart_data(self):
        """Test the get_status_pie_chart_data function."""
        # Create sample status distribution
        status_distribution = {
            "labels": ["TV", "Movie", "Anime"],
            "datasets": [
                {
                    "label": Status.COMPLETED.value,
                    "data": [1, 0, 0],
                    "background_color": "#10b981",
                    "total": 1,
                },
                {
                    "label": Status.IN_PROGRESS.value,
                    "data": [0, 1, 0],
                    "background_color": "#6366f1",
                    "total": 1,
                },
                {
                    "label": Status.PLANNING.value,
                    "data": [0, 0, 1],
                    "background_color": "#3b82f6",
                    "total": 1,
                },
                {
                    "label": Status.PAUSED.value,
                    "data": [0, 0, 0],
                    "background_color": "#f97316",
                    "total": 0,
                },
            ],
            "total_completed": 1,
        }

        chart_data = statistics.get_status_pie_chart_data(status_distribution)

        # Check structure
        self.assertIn("labels", chart_data)
        self.assertIn("datasets", chart_data)
        self.assertEqual(len(chart_data["datasets"]), 1)
        self.assertIn("data", chart_data["datasets"][0])
        self.assertIn("backgroundColor", chart_data["datasets"][0])

        # Check content - should only include statuses with count > 0
        self.assertEqual(len(chart_data["labels"]), 3)
        self.assertEqual(len(chart_data["datasets"][0]["data"]), 3)
        self.assertEqual(len(chart_data["datasets"][0]["backgroundColor"]), 3)

        # PAUSED status should be excluded (total = 0)
        self.assertNotIn(Status.PAUSED.value, chart_data["labels"])

    def test_get_score_distribution(self):
        """Test the get_score_distribution function."""
        TV.objects.filter(user=self.user).update(score=8.5)

        # Create user_media dict with our test objects
        user_media = {
            MediaTypes.TV.value: TV.objects.filter(user=self.user),
            MediaTypes.MOVIE.value: Movie.objects.filter(user=self.user),
            MediaTypes.ANIME.value: Anime.objects.filter(user=self.user),
        }

        score_distribution, top_rated, _ = statistics.get_score_distribution(user_media)

        # Check structure
        self.assertIn("labels", score_distribution)
        self.assertIn("datasets", score_distribution)
        self.assertIn("average_score", score_distribution)
        self.assertIn("total_scored", score_distribution)

        # Check content
        self.assertEqual(len(score_distribution["labels"]), 11)  # Scores 0-10
        self.assertEqual(len(score_distribution["datasets"]), 3)  # 3 media types

        # Check average score and total scored
        self.assertEqual(
            score_distribution["total_scored"],
            2,
        )  # TV and Movie have scores
        self.assertEqual(score_distribution["average_score"], 8.0)  # (8.5 + 7.5) / 2

        # Check top rated
        self.assertEqual(
            len(top_rated),
            2,
        )  # Only 2 items have scores
        self.assertEqual(
            top_rated[0].score,
            8.5,
        )  # TV should be first
        self.assertEqual(
            top_rated[1].score,
            7.5,
        )  # Movie should be second

    def test_get_status_color(self):
        """Test the get_status_color function."""
        # Test all status colors
        for status in Status.values:
            color = statistics.get_status_color(status)
            self.assertIsNotNone(color)
            self.assertTrue(color.startswith("#"))

    def test_get_timeline(self):
        """Test the get_timeline function."""
        # Create user_media dict with our test objects
        user_media = {
            MediaTypes.TV.value: TV.objects.filter(user=self.user),
            MediaTypes.SEASON.value: Season.objects.filter(user=self.user),
            MediaTypes.MOVIE.value: Movie.objects.filter(user=self.user),
            MediaTypes.ANIME.value: Anime.objects.filter(user=self.user),
        }
        timeline = statistics.get_timeline(user_media)

        # Check structure - should be a dict with month-year keys
        self.assertIsInstance(timeline, dict)

        # Check content
        self.assertIn("January 2025", timeline)  # Season spans Jan 1-15
        self.assertIn("February 2025", timeline)  # Movie on Feb 1
        self.assertIn("March 2025", timeline)  # Anime starts on Mar 1

        # Check items in each month
        self.assertEqual(len(timeline["January 2025"]), 1)  # Season
        self.assertEqual(len(timeline["February 2025"]), 1)  # Movie
        self.assertEqual(len(timeline["March 2025"]), 1)  # Anime

        # Check sorting - should be in chronological order
        months = list(timeline.keys())
        self.assertEqual(months[0], "March 2025")  # Most recent first
        self.assertEqual(months[1], "February 2025")
        self.assertEqual(months[2], "January 2025")

    def test_get_level(self):
        """Test the get_level function."""
        self.assertEqual(statistics.get_level(0), 0)
        self.assertEqual(statistics.get_level(1), 1)
        self.assertEqual(statistics.get_level(3), 1)
        self.assertEqual(statistics.get_level(4), 2)
        self.assertEqual(statistics.get_level(6), 2)
        self.assertEqual(statistics.get_level(7), 3)
        self.assertEqual(statistics.get_level(9), 3)
        self.assertEqual(statistics.get_level(10), 4)
        self.assertEqual(statistics.get_level(20), 4)

    @patch("app.statistics.get_filtered_historical_data")
    def test_get_activity_data(self, mock_get_filtered_data):
        """Test the get_activity_data function."""
        start_date = datetime.datetime(2025, 1, 1, 0, 0, tzinfo=datetime.UTC)
        end_date = datetime.datetime(2025, 3, 31, 0, 0, tzinfo=datetime.UTC)

        # Mock the historical data
        mock_get_filtered_data.return_value = [
            {"date": datetime.date(2025, 1, 1), "count": 2},
            {"date": datetime.date(2025, 1, 2), "count": 1},
            {"date": datetime.date(2025, 1, 3), "count": 3},
            {"date": datetime.date(2025, 1, 4), "count": 0},
            {"date": datetime.date(2025, 1, 5), "count": 5},
            {"date": datetime.date(2025, 1, 6), "count": 2},
            {"date": datetime.date(2025, 1, 7), "count": 1},
            {"date": datetime.date(2025, 1, 8), "count": 4},
            {"date": datetime.date(2025, 1, 9), "count": 0},
            {"date": datetime.date(2025, 1, 10), "count": 0},
            {"date": datetime.date(2025, 3, 31), "count": 3},  # Last day
        ]

        result = statistics.get_activity_data(self.user, start_date, end_date)

        # Check that the function returns the expected structure
        self.assertIn("calendar_weeks", result)
        self.assertIn("months", result)
        self.assertIn("stats", result)

        # Check stats
        stats = result["stats"]
        self.assertIn("most_active_day", stats)
        self.assertIn("most_active_day_percentage", stats)
        self.assertIn("current_streak", stats)
        self.assertIn("longest_streak", stats)

        # Check calendar data
        calendar_weeks = result["calendar_weeks"]
        self.assertIsInstance(calendar_weeks, list)

        # Verify the first day is aligned to Monday
        first_week = calendar_weeks[0]
        self.assertEqual(len(first_week), 7)  # 7 days in a week

        # Check months data
        months = result["months"]
        self.assertIsInstance(months, list)

    @patch("app.stats_activity.BasicMedia.objects.get_historical_models")
    @patch("app.statistics.apps.get_model")
    def test_get_filtered_historical_data(self, mock_get_model, mock_get_hist_models):
        """Test the get_filtered_historical_data function."""
        start = datetime.datetime(2025, 1, 1, tzinfo=datetime.UTC)
        end = datetime.datetime(2025, 3, 31, tzinfo=datetime.UTC)

        mock_get_hist_models.return_value = ["historicalmodel1", "historicalmodel2"]

        def build_fake_model(timestamps):
            qs = MagicMock()
            qs.filter.return_value = qs
            qs.values_list.return_value.iterator.return_value = timestamps
            model = MagicMock()
            model.objects = qs
            return model

        model1_ts = [
            datetime.datetime(2025, 1, 5, 12, tzinfo=datetime.UTC),
            datetime.datetime(2025, 1, 5, 18, tzinfo=datetime.UTC),
            datetime.datetime(2025, 1, 10, 9, tzinfo=datetime.UTC),
            datetime.datetime(2025, 1, 10, 10, tzinfo=datetime.UTC),
            datetime.datetime(2025, 1, 10, 11, tzinfo=datetime.UTC),
        ]
        model2_ts = [
            datetime.datetime(2025, 2, 15, 8, tzinfo=datetime.UTC),
            datetime.datetime(2025, 3, 20, 17, tzinfo=datetime.UTC),
            datetime.datetime(2025, 3, 20, 18, tzinfo=datetime.UTC),
            datetime.datetime(2025, 3, 20, 19, tzinfo=datetime.UTC),
            datetime.datetime(2025, 3, 20, 20, tzinfo=datetime.UTC),
        ]

        fake_model1 = build_fake_model(model1_ts)
        fake_model2 = build_fake_model(model2_ts)
        mock_get_model.side_effect = lambda _, name: (
            fake_model1 if name == "historicalmodel1" else fake_model2
        )

        result = statistics.get_filtered_historical_data(start, end, self.user)

        expected = [
            {"date": datetime.date(2025, 1, 5), "count": 2},
            {"date": datetime.date(2025, 1, 10), "count": 3},
            {"date": datetime.date(2025, 2, 15), "count": 1},
            {"date": datetime.date(2025, 3, 20), "count": 4},
        ]
        self.assertCountEqual(result, expected)

    def test_calculate_day_of_week_stats(self):
        """Test the calculate_day_of_week_stats function."""
        # Create sample date counts
        date_counts = {
            datetime.date(2025, 1, 1): 2,  # Wednesday
            datetime.date(2025, 1, 2): 1,  # Thursday
            datetime.date(2025, 1, 3): 3,  # Friday
            datetime.date(2025, 1, 4): 0,  # Saturday
            datetime.date(2025, 1, 5): 5,  # Sunday
            datetime.date(2025, 1, 6): 2,  # Monday
            datetime.date(2025, 1, 7): 1,  # Tuesday
            datetime.date(2025, 1, 8): 4,  # Wednesday
            datetime.date(2025, 1, 9): 0,  # Thursday
            datetime.date(2025, 1, 10): 0,  # Friday
            datetime.date(2025, 1, 12): 5,  # Sunday
            datetime.date(2025, 1, 19): 3,  # Sunday
        }

        start_date = datetime.date(2025, 1, 1)

        most_active_day, percentage = statistics.calculate_day_of_week_stats(
            date_counts,
            start_date,
        )

        # Sunday has highest count (3 occurrences)
        self.assertEqual(most_active_day, "Sunday")
        # 3 out of 9 active days = ~33%
        self.assertEqual(percentage, 33)

        # Test with empty data
        empty_counts = {}
        most_active_day, percentage = statistics.calculate_day_of_week_stats(
            empty_counts,
            start_date,
        )
        self.assertIsNone(most_active_day)
        self.assertEqual(percentage, 0)

    def test_calculate_most_active_weekday(self):
        """Test calculate_most_active_weekday based on consumption minutes."""
        # Create day_minutes_by_type data (same format as used by daily hours chart)
        day_minutes_by_type = {
            "Movie": {
                "2025-01-01": 120,  # Wednesday - 2 hours
                "2025-01-02": 60,  # Thursday - 1 hour
                "2025-01-03": 90,  # Friday - 1.5 hours
                "2025-01-04": 0,  # Saturday - 0
                "2025-01-05": 180,  # Sunday - 3 hours
            },
            "TV": {
                "2025-01-01": 60,  # Wednesday - 1 hour (total: 3h)
                "2025-01-02": 30,  # Thursday - 0.5 hour (total: 1.5h)
                "2025-01-03": 0,  # Friday (total: 1.5h)
                "2025-01-04": 0,  # Saturday (total: 0)
                "2025-01-05": 120,  # Sunday - 2 hours (total: 5h)
            },
        }

        day_list = [
            datetime.date(2025, 1, 1),  # Wednesday
            datetime.date(2025, 1, 2),  # Thursday
            datetime.date(2025, 1, 3),  # Friday
            datetime.date(2025, 1, 4),  # Saturday
            datetime.date(2025, 1, 5),  # Sunday
        ]

        most_active_day, percentage = statistics.calculate_most_active_weekday(
            day_minutes_by_type,
            day_list,
        )

        # Sunday has highest total minutes (300 min = 5 hours)
        self.assertEqual(most_active_day, "Sunday")
        # 300 out of 660 total minutes = ~45%
        self.assertEqual(percentage, 45)

        # Test with empty data
        empty_minutes = {}
        most_active_day, percentage = statistics.calculate_most_active_weekday(
            empty_minutes,
            day_list,
        )
        self.assertIsNone(most_active_day)
        self.assertEqual(percentage, 0)

        # Test that weekdays not in day_list are not counted
        # (This is the key fix - only days in the filtered range are considered)
        short_day_list = [
            datetime.date(2025, 1, 1),  # Wednesday
            datetime.date(2025, 1, 2),  # Thursday
            datetime.date(2025, 1, 3),  # Friday
            # No Saturday or Sunday
        ]

        most_active_day, percentage = statistics.calculate_most_active_weekday(
            day_minutes_by_type,
            short_day_list,
        )

        # Wednesday should be most active (180 min) since Sunday is not in range
        self.assertEqual(most_active_day, "Wednesday")

    def test_calculate_streaks(self):
        """Test the calculate_streaks function."""
        # Create sample date counts
        today = datetime.date(2025, 3, 31)
        yesterday = today - datetime.timedelta(days=1)
        two_days_ago = today - datetime.timedelta(days=2)

        # Test current streak
        date_counts = {
            today: 1,
            yesterday: 2,
            two_days_ago: 3,
            datetime.date(2025, 3, 27): 0,
            datetime.date(2025, 3, 26): 1,
            datetime.date(2025, 3, 25): 1,
            datetime.date(2025, 3, 24): 1,
            datetime.date(2025, 3, 23): 1,
            datetime.date(2025, 3, 22): 0,
            datetime.date(2025, 3, 21): 1,
        }

        current_streak, longest_streak = statistics.calculate_streaks(
            date_counts,
            today,
        )

        # Current streak should be 3 (today, yesterday, two days ago)
        self.assertEqual(current_streak, 3)
        # Longest streak should be 4 (Mar 23-26)
        self.assertEqual(longest_streak, 4)

        # Test no current streak
        date_counts = {
            yesterday: 2,
            two_days_ago: 3,
            datetime.date(2025, 3, 27): 0,
            datetime.date(2025, 3, 26): 1,
        }

        current_streak, longest_streak = statistics.calculate_streaks(
            date_counts,
            today,
        )

        # No activity today, so current streak is 0
        self.assertEqual(current_streak, 0)
        # Longest streak should be 2 (Mar 29-30)
        self.assertEqual(longest_streak, 2)

        # Test empty data
        empty_counts = {}
        current_streak, longest_streak = statistics.calculate_streaks(
            empty_counts,
            today,
        )
        self.assertEqual(current_streak, 0)
        self.assertEqual(longest_streak, 0)

    def test_get_top_played_media(self):
        """Test the get_top_played_media function."""
        # Test with empty user media
        empty_user_media = {}
        result = statistics.get_top_played_media(empty_user_media, None, None)
        self.assertEqual(result, {})

        # Test with non-existent media types
        user_media = {"anime": MagicMock()}
        user_media["anime"].exists.return_value = False
        result = statistics.get_top_played_media(user_media, None, None)
        self.assertEqual(result, {})

    def test_get_top_played_media_aggregates_movie_repeats(self):
        """Ensure repeated movie plays are aggregated into a single entry."""

        class FakeQuerySet(list):
            def exists(self):
                return bool(self)

        start_date = timezone.make_aware(datetime.datetime(2025, 1, 1))
        end_date = timezone.make_aware(datetime.datetime(2025, 12, 31, 23, 59, 59))

        movie_item = SimpleNamespace(
            id=42,
            runtime_minutes=106,
            title="Cars 2",
            media_type=MediaTypes.MOVIE.value,
            image="https://example.com/cars2.jpg",
            media_id="557",
            source=Sources.TMDB.value,
        )

        def make_movie_instance(day):
            end_dt = timezone.make_aware(datetime.datetime(2025, 5, day, 12, 0, 0))
            return SimpleNamespace(
                item=movie_item,
                end_date=end_dt,
                start_date=end_dt - datetime.timedelta(hours=2),
                created_at=end_dt - datetime.timedelta(days=1),
                status=Status.COMPLETED.value,
            )

        first_play = make_movie_instance(6)
        second_play = make_movie_instance(7)

        other_movie_item = SimpleNamespace(
            id=43,
            runtime_minutes=150,
            title="Another Movie",
            media_type=MediaTypes.MOVIE.value,
            image="https://example.com/other.jpg",
            media_id="558",
            source=Sources.TMDB.value,
        )
        other_movie = SimpleNamespace(
            item=other_movie_item,
            end_date=timezone.make_aware(datetime.datetime(2025, 1, 5, 20, 0, 0)),
            start_date=timezone.make_aware(datetime.datetime(2025, 1, 5, 18, 0, 0)),
            created_at=timezone.make_aware(datetime.datetime(2025, 1, 4, 18, 0, 0)),
            status=Status.COMPLETED.value,
        )

        user_media = {"movie": FakeQuerySet([first_play, second_play, other_movie])}

        result = statistics.get_top_played_media(user_media, start_date, end_date)

        self.assertIn("movie", result)
        self.assertEqual(len(result["movie"]), 2)

        top_movie = result["movie"][0]
        self.assertIs(top_movie["media"], second_play)
        self.assertEqual(top_movie["play_count"], 2)
        self.assertEqual(top_movie["total_time_minutes"], 212)
        self.assertEqual(top_movie["formatted_duration"], minutes_to_hhmm(212))
        self.assertEqual(top_movie["last_activity"], second_play.end_date)

        second_entry = result["movie"][1]
        self.assertIs(second_entry["media"], other_movie)
        self.assertEqual(second_entry["play_count"], 1)
        self.assertEqual(second_entry["total_time_minutes"], 150)

    def test_get_top_played_media_aggregates_game_sessions(self):
        """Ensure game sessions for the same title are aggregated into a single entry."""

        class FakeQuerySet(list):
            def exists(self):
                return bool(self)

        start_date = timezone.make_aware(datetime.datetime(2025, 10, 1))
        end_date = timezone.make_aware(datetime.datetime(2025, 12, 31, 23, 59, 59))

        game_item = SimpleNamespace(
            id=101,
            title="Ghost of Yotei",
            media_type=MediaTypes.GAME.value,
            image="https://example.com/ghost.jpg",
            media_id="999",
            source=Sources.IGDB.value,
        )

        def make_game_session(day, minutes):
            start_dt = timezone.make_aware(datetime.datetime(2025, 10, day, 12, 0, 0))
            end_dt = start_dt
            return SimpleNamespace(
                item=game_item,
                start_date=start_dt,
                end_date=end_dt,
                created_at=start_dt - datetime.timedelta(days=1),
                progress=minutes,
                status=Status.IN_PROGRESS.value,
            )

        first_session = make_game_session(15, 120)
        second_session = make_game_session(16, 60)

        other_game_item = SimpleNamespace(
            id=102,
            title="Other Game",
            media_type=MediaTypes.GAME.value,
            image="https://example.com/other.jpg",
            media_id="998",
            source=Sources.IGDB.value,
        )
        other_game = SimpleNamespace(
            item=other_game_item,
            start_date=timezone.make_aware(datetime.datetime(2025, 11, 1, 12, 0, 0)),
            end_date=timezone.make_aware(datetime.datetime(2025, 11, 1, 12, 0, 0)),
            created_at=timezone.make_aware(datetime.datetime(2025, 10, 30, 12, 0, 0)),
            progress=90,
            status=Status.IN_PROGRESS.value,
        )

        user_media = {"game": FakeQuerySet([first_session, second_session, other_game])}

        result = statistics.get_top_played_media(user_media, start_date, end_date)

        self.assertIn("game", result)
        self.assertEqual(len(result["game"]), 2)

        top_game = result["game"][0]
        self.assertIs(top_game["media"], second_session)
        self.assertEqual(top_game["play_count"], 2)
        self.assertEqual(top_game["total_time_minutes"], 180)
        self.assertEqual(top_game["formatted_duration"], minutes_to_hhmm(180))


class GameDailyAverageTests(TestCase):
    def test_daily_average_uses_unique_days(self):
        game_item = SimpleNamespace(
            id=201,
            title="Dispatch",
            media_type=MediaTypes.GAME.value,
            image="https://example.com/dispatch.jpg",
            media_id="disp",
            source=Sources.IGDB.value,
        )

        session_dt = timezone.make_aware(datetime.datetime(2025, 12, 29, 12, 0, 0))
        first_session = SimpleNamespace(
            item=game_item,
            start_date=session_dt,
            end_date=session_dt,
            created_at=session_dt,
            progress=105,
        )
        second_session = SimpleNamespace(
            item=game_item,
            start_date=session_dt,
            end_date=session_dt,
            created_at=session_dt,
            progress=60,
        )

        result = statistics._collect_game_data(
            [first_session, second_session], None, None
        )

        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0]["daily_average"], 165 / 60)

    def test_daily_average_handles_missing_start_date(self):
        game_item = SimpleNamespace(
            id=202,
            title="Dispatch",
            media_type=MediaTypes.GAME.value,
            image="https://example.com/dispatch.jpg",
            media_id="disp2",
            source=Sources.IGDB.value,
        )

        session_dt = timezone.make_aware(datetime.datetime(2025, 12, 29, 18, 0, 0))
        session = SimpleNamespace(
            item=game_item,
            start_date=None,
            end_date=session_dt,
            created_at=session_dt,
            progress=90,
        )

        result = statistics._collect_game_data([session], None, None)

        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0]["daily_average"], 90 / 60)


class ConsumptionStatisticsTests(TestCase):
    """Validate TV and movie consumption aggregations."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="consumption-user",
            password="password",
        )
        self.client.force_login(self.user)

        now = timezone.now()
        self.start_date = now - datetime.timedelta(days=30)
        self.end_date = now

        # Create TV show with a season and two episodes (45 minutes each)
        self.tv_item = Item.objects.create(
            media_id="tv1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Aggregation TV",
        )
        self.tv = TV.objects.create(
            user=self.user,
            item=self.tv_item,
            status=Status.COMPLETED.value,
        )

        self.season_item = Item.objects.create(
            media_id="tv1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=1,
            title="Aggregation TV Season 1",
        )
        self.season = Season.objects.create(
            user=self.user,
            item=self.season_item,
            related_tv=self.tv,
            status=Status.COMPLETED.value,
        )

        episode_times = [
            now - datetime.timedelta(days=5, hours=2),
            now - datetime.timedelta(days=2, hours=10),
        ]

        for index, end_time in enumerate(episode_times, start=1):
            episode_item = Item.objects.create(
                media_id=f"tv1-ep{index}",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                season_number=1,
                episode_number=index,
                title=f"Episode {index}",
                runtime_minutes=45,
            )
            with self.captureOnCommitCallbacks(execute=True):
                Episode.objects.create(
                    item=episode_item,
                    related_season=self.season,
                    end_date=end_time,
                )

        # Create movie with runtime 120 minutes
        self.movie_item = Item.objects.create(
            media_id="movie1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Aggregation Movie",
        )
        Movie.objects.create(
            user=self.user,
            item=self.movie_item,
            status=Status.COMPLETED.value,
            end_date=now - datetime.timedelta(days=1, hours=3),
        )
        Movie.objects.create(
            user=self.user,
            item=self.movie_item,
            status=Status.COMPLETED.value,
            end_date=now - datetime.timedelta(hours=6),
        )
        cache.clear()

    @tag("network")
    def test_consumption_stats_aggregation(self):
        """TV/movie consumption helpers should return expected totals and chart data."""
        with patch(
            "app.stats_time._get_media_metadata_for_statistics",
        ) as metadata_mock:
            metadata_mock.return_value = {"runtime": "2h"}
            user_media, _ = statistics.get_user_media(
                self.user,
                self.start_date,
                self.end_date,
            )
            minutes_per_type = statistics.calculate_minutes_per_media_type(
                user_media,
                self.start_date,
                self.end_date,
            )

            tv_stats = statistics.get_tv_consumption_stats(
                user_media,
                self.start_date,
                self.end_date,
                minutes_per_type,
            )
            movie_stats = statistics.get_movie_consumption_stats(
                user_media,
                self.start_date,
                self.end_date,
                minutes_per_type,
            )

        self.assertEqual(tv_stats["plays"]["total"], 2)
        self.assertAlmostEqual(tv_stats["hours"]["total"], 1.5, places=2)
        self.assertEqual(
            tv_stats["charts"]["by_year"]["labels"], [str(self.end_date.year)]
        )
        self.assertEqual(
            tv_stats["charts"]["by_year"]["datasets"][0]["data"],
            [2],
        )

        tv_month_data = tv_stats["charts"]["by_month"]["datasets"][0]["data"]
        self.assertEqual(
            tv_month_data[self.end_date.month - 1],
            2,
        )

        self.assertEqual(movie_stats["plays"]["total"], 2)
        self.assertAlmostEqual(movie_stats["hours"]["total"], 4.0, places=2)
        self.assertEqual(
            movie_stats["charts"]["by_year"]["labels"], [str(self.end_date.year)]
        )
        self.assertEqual(
            movie_stats["charts"]["by_year"]["datasets"][0]["data"],
            [2],
        )

    def test_tv_consumption_stats_without_precomputed_minutes(self):
        """TV consumption should infer minutes without needing a precomputed map."""
        user_media, _ = statistics.get_user_media(
            self.user,
            self.start_date,
            self.end_date,
        )

        tv_stats = statistics.get_tv_consumption_stats(
            user_media,
            self.start_date,
            self.end_date,
            minutes_per_type=None,
        )

        self.assertEqual(tv_stats["plays"]["total"], 2)
        self.assertAlmostEqual(tv_stats["hours"]["total"], 1.5, places=2)

    def test_statistics_view_includes_consumption_context(self):
        """Statistics view should include the consumption breakdown data."""
        with patch(
            "app.statistics._get_media_metadata_for_statistics"
        ) as metadata_mock:
            metadata_mock.return_value = {"runtime": "2h"}
            response = self.client.get(reverse("statistics"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("tv_consumption", response.context)
        self.assertIn("movie_consumption", response.context)
        self.assertEqual(response.context["tv_consumption"]["plays"]["total"], 2)
        self.assertEqual(response.context["movie_consumption"]["plays"]["total"], 2)
        self.assertNotIn("tv_episodes_by_year", response.content.decode())
        self.assertIn("show_year_charts", response.context)
        self.assertFalse(response.context["show_year_charts"])

    def test_statistics_respects_disabled_season_sidebar_preference(self):
        """Season media should be removed when the sidebar preference hides it."""
        self.user.season_enabled = False
        self.user.save(update_fields=["season_enabled"])

        with patch(
            "app.statistics._get_media_metadata_for_statistics"
        ) as metadata_mock:
            metadata_mock.return_value = {"runtime": "2h"}
            response = self.client.get(reverse("statistics"))

        self.assertEqual(response.status_code, 200)
        media_count = response.context["media_count"]
        self.assertNotIn(MediaTypes.SEASON.value, media_count)
        self.assertEqual(response.context["tv_consumption"]["plays"]["total"], 2)


class GroupedAnimeStatisticsTests(TestCase):
    """Grouped anime (TMDB/TVDB) should count as anime, not TV, in all statistics."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="grouped-anime-user",
            password="password",
        )

        now = timezone.now()
        self.start_date = now - datetime.timedelta(days=30)
        self.end_date = now

        # --- Grouped anime: TV model with library_media_type="anime" ---
        self.anime_tv_item = Item.objects.get_or_create(
            media_id="anime_show_1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            library_media_type=MediaTypes.ANIME.value,
            defaults={"title": "Attack on Titan (TMDB)"},
        )[0]
        self.anime_tv = TV.objects.create(
            user=self.user,
            item=self.anime_tv_item,
            status=Status.COMPLETED.value,
        )
        self.anime_season_item = Item.objects.get_or_create(
            media_id="anime_show_1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=1,
            library_media_type=MediaTypes.ANIME.value,
            defaults={"title": "Attack on Titan S1"},
        )[0]
        self.anime_season = Season.objects.create(
            user=self.user,
            item=self.anime_season_item,
            related_tv=self.anime_tv,
            status=Status.COMPLETED.value,
        )
        for ep_num in (1, 2):
            ep_item = Item.objects.get_or_create(
                media_id="anime_show_1",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                season_number=1,
                episode_number=ep_num,
                library_media_type=MediaTypes.ANIME.value,
                defaults={
                    "title": f"Episode {ep_num}",
                    "runtime_minutes": 24,
                },
            )[0]
            Episode.objects.create(
                item=ep_item,
                related_season=self.anime_season,
                end_date=now - datetime.timedelta(days=ep_num),
            )

        # --- Regular TV show (should stay in TV bucket) ---
        self.tv_item = Item.objects.get_or_create(
            media_id="regular_tv_1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            defaults={"title": "Regular TV Show"},
        )[0]
        self.tv = TV.objects.create(
            user=self.user,
            item=self.tv_item,
            status=Status.COMPLETED.value,
        )
        self.tv_season_item = Item.objects.get_or_create(
            media_id="regular_tv_1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=1,
            defaults={"title": "Regular TV S1"},
        )[0]
        self.tv_season = Season.objects.create(
            user=self.user,
            item=self.tv_season_item,
            related_tv=self.tv,
            status=Status.COMPLETED.value,
        )
        self.tv_ep_item = Item.objects.get_or_create(
            media_id="regular_tv_1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=1,
            defaults={"title": "TV Episode 1", "runtime_minutes": 45},
        )[0]
        Episode.objects.create(
            item=self.tv_ep_item,
            related_season=self.tv_season,
            end_date=now - datetime.timedelta(days=3),
        )

    def _get_media(self):
        return statistics.get_user_media(self.user, self.start_date, self.end_date)

    def test_rating_statistics_exclude_unrated_anime_but_keep_explicit_zero(self):
        from app import statistics_sync

        cache.clear()
        scores = ((None, True), (None, False), (0, True), (8, False))
        for index, (score, dated) in enumerate(scores):
            item = Item.objects.create(
                media_id=f"rating-anime-{index}",
                source=Sources.MANUAL.value,
                media_type=MediaTypes.ANIME.value,
                title=f"Anime {index}",
            )
            Anime.objects.create(
                user=self.user,
                item=item,
                status=Status.COMPLETED.value,
                score=score,
                end_date=timezone.now() if dated else None,
            )
        movie_item = Item.objects.create(
            media_id="rating-movie",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MOVIE.value,
            title="Rated movie",
        )
        Movie.objects.create(user=self.user, item=movie_item, score=6)

        for scale, expected in ((10, 14 / 3), (5, 7 / 3)):
            self.user.rating_scale = str(scale)
            self.user.save(update_fields=["rating_scale"])
            cache.clear()
            data = statistics_sync._aggregate_range(self.user, "All Time", 0)
            distribution = data["score_distribution"]
            anime = next(
                row for row in distribution["datasets"] if row["label"] == "Anime"
            )
            self.assertEqual(sum(anime["data"]), 2)
            self.assertEqual(anime["data"][0], 1)
            self.assertEqual(distribution["total_scored"], 3)
            self.assertEqual(distribution["average_score"], round(expected, 2))

    def test_grouped_anime_is_in_anime_bucket_not_tv(self):
        """get_user_media must put grouped anime in 'anime', not 'tv'."""
        user_media, media_count = self._get_media()

        anime_bucket = user_media.get(MediaTypes.ANIME.value)
        # Anime bucket may be a single queryset or a list of querysets (flat + grouped)
        anime_ids = (
            [
                m.id
                for m in statistics._iter_media_list(anime_bucket)
                if anime_bucket is not None
            ]
            if anime_bucket is not None
            else []
        )
        tv_ids = [m.id for m in user_media.get(MediaTypes.TV.value, [])]

        self.assertIn(self.anime_tv.id, anime_ids)
        self.assertNotIn(self.anime_tv.id, tv_ids)
        # Regular TV show must stay in tv bucket
        self.assertIn(self.tv.id, tv_ids)

    def test_grouped_anime_count_in_anime_media_count(self):
        """media_count must attribute grouped anime to anime, not tv."""
        _, media_count = self._get_media()

        self.assertGreaterEqual(media_count.get(MediaTypes.ANIME.value, 0), 1)
        # TV count should reflect only pure TV shows
        self.assertEqual(media_count.get(MediaTypes.TV.value, 0), 1)

    def test_grouped_anime_minutes_counted_as_anime(self):
        """Minutes for grouped anime episodes must land in the anime bucket."""
        user_media, _ = self._get_media()
        minutes = statistics.calculate_minutes_per_media_type(
            user_media, self.start_date, self.end_date
        )

        # 2 episodes × 24 min = 48 min of anime
        self.assertGreaterEqual(minutes.get(MediaTypes.ANIME.value, 0), 48)
        # TV minutes should reflect only the regular TV episode (45 min)
        self.assertLessEqual(minutes.get(MediaTypes.TV.value, 0), 45)

    def test_grouped_anime_status_distribution_is_anime(self):
        """Status distribution must attribute grouped anime entries to anime."""
        user_media, _ = self._get_media()
        distribution = statistics.get_status_distribution(user_media)

        labels = distribution["labels"]
        self.assertIn("Anime", labels)

        anime_idx = labels.index("Anime")
        # Sum completed count for anime across all status datasets
        completed_anime = sum(
            ds["data"][anime_idx]
            for ds in distribution["datasets"]
            if ds["label"] == Status.COMPLETED.value
        )
        self.assertGreaterEqual(completed_anime, 1)

    def test_regular_tv_unaffected(self):
        """Regular TV entries must still be counted as TV after the fix."""
        user_media, _ = self._get_media()
        minutes = statistics.calculate_minutes_per_media_type(
            user_media, self.start_date, self.end_date
        )

        self.assertGreater(minutes.get(MediaTypes.TV.value, 0), 0)


class EpisodelessCompletedSeasonStatisticsTests(TestCase):
    """A Completed season/show with zero Episode rows must still count.

    Bulk imports (e.g. an MDBList season-rating import, see issue #369)
    can persist a Season as COMPLETED via bulk_create, bypassing
    Season.save()'s episode-completion fan-out, leaving it with no
    Episode children. get_user_media() used to key the TV/Season "All
    Time" buckets off existing Episode rows, making such shows/seasons
    invisible in Statistics even though they exist in the library.
    """

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="episodeless-user",
            password="password",
        )

        self.tv_item = Item.objects.create(
            media_id="900001",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Rating Only Show",
        )
        self.tv = TV.objects.create(
            user=self.user,
            item=self.tv_item,
            status=Status.COMPLETED.value,
        )

        self.season_item = Item.objects.create(
            media_id="900001",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=1,
            title="Rating Only Show S1",
        )
        # bulk_create mirrors the bulk-import path: it skips Season.save(),
        # so no Episode backfill happens here.
        self.season = Season.objects.bulk_create(
            [
                Season(
                    user=self.user,
                    item=self.season_item,
                    related_tv=self.tv,
                    status=Status.COMPLETED.value,
                    score=7,
                ),
            ],
        )[0]

    def test_episodeless_season_and_show_included_all_time(self):
        """All Time stats must include the show/season despite zero episodes."""
        self.assertEqual(
            Episode.objects.filter(related_season=self.season).count(),
            0,
        )

        user_media, media_count = statistics.get_user_media(self.user, None, None)

        self.assertIn(
            self.tv.id,
            list(user_media[MediaTypes.TV.value].values_list("id", flat=True)),
        )
        self.assertIn(
            self.season.id,
            list(user_media[MediaTypes.SEASON.value].values_list("id", flat=True)),
        )
        self.assertEqual(media_count[MediaTypes.TV.value], 1)
        self.assertEqual(media_count[MediaTypes.SEASON.value], 1)


class GroupedAnimeStatisticsBucketTests(TestCase):
    """Grouped anime must be counted exactly once, in the anime bucket."""

    def setUp(self):
        """Create a user tracking one grouped-anime show."""
        self.user = User.objects.create_user(
            username="grouped-anime-stats-user",
            password="password",
        )
        self.item = Item.objects.create(
            media_id="9350138",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            library_media_type=MediaTypes.ANIME.value,
            title="Frieren: Beyond Journey's End",
            image="",
        )

    def test_all_time_counts_grouped_anime_without_episode_rows(self):
        """A rating-only grouped anime must not vanish from All Time.

        TV excludes the anime bucket unconditionally, and the anime pass was
        keyed off shows with episode activity, so a show with no Episode rows
        was counted in neither bucket.
        """
        TV.objects.create(
            item=self.item,
            user=self.user,
            status=Status.COMPLETED.value,
            score=9.0,
        )

        _, media_count = statistics.get_user_media(self.user, None, None)

        self.assertEqual(media_count[MediaTypes.ANIME.value], 1)
        self.assertEqual(media_count[MediaTypes.TV.value], 0)
        self.assertEqual(media_count["total"], 1)

    def test_grouped_anime_seasons_are_not_also_counted_as_seasons(self):
        """A grouped anime season belongs to anime, not to the Season bucket."""
        tv = TV.objects.create(
            item=self.item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        season_item = Item.objects.create(
            media_id="9350138",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            library_media_type=MediaTypes.ANIME.value,
            season_number=1,
            title="Frieren: Beyond Journey's End",
            image="",
        )
        Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=tv,
            status=Status.IN_PROGRESS.value,
        )

        _, media_count = statistics.get_user_media(self.user, None, None)

        self.assertEqual(media_count[MediaTypes.SEASON.value], 0)
        self.assertEqual(media_count[MediaTypes.TV.value], 0)
        self.assertEqual(media_count[MediaTypes.ANIME.value], 1)
