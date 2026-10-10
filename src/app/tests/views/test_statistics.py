import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app import statistics as stats
from app.models import (
    Anime,
    Book,
    Episode,
    Game,
    Item,
    Manga,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Status,
)


class ConsumptionStatsTests(TestCase):
    """Test the per-media-type consumption aggregation."""

    def setUp(self):
        """Create a user and seed media across several types."""
        credentials = {"username": "cons", "password": "12345"}
        self.user = get_user_model().objects.create_user(**credentials)

    def _item(self, media_type, media_id):
        return Item.objects.create(
            media_id=media_id,
            source=Sources.MANUAL.value,
            media_type=media_type,
            title=media_id,
            image="none.jpg",
        )

    def test_consumption_stats(self):
        """Aggregate progress into episodes/chapters/pages/hours and movie counts."""
        # bulk_create bypasses the custom save so no provider lookups fire.
        Movie.objects.bulk_create(
            [
                Movie(
                    item=self._item(MediaTypes.MOVIE.value, "m1"),
                    user=self.user,
                    status=Status.COMPLETED.value,
                    progress=1,
                ),
                Movie(
                    item=self._item(MediaTypes.MOVIE.value, "m2"),
                    user=self.user,
                    status=Status.COMPLETED.value,
                    progress=1,
                ),
            ],
        )
        Anime.objects.bulk_create(
            [
                Anime(
                    item=self._item(MediaTypes.ANIME.value, "a1"),
                    user=self.user,
                    status=Status.IN_PROGRESS.value,
                    progress=12,
                ),
            ],
        )
        Manga.objects.bulk_create(
            [
                Manga(
                    item=self._item(MediaTypes.MANGA.value, "g1"),
                    user=self.user,
                    status=Status.IN_PROGRESS.value,
                    progress=40,
                ),
            ],
        )
        Book.objects.bulk_create(
            [
                Book(
                    item=self._item(MediaTypes.BOOK.value, "b1"),
                    user=self.user,
                    status=Status.COMPLETED.value,
                    progress=250,
                ),
            ],
        )
        Game.objects.bulk_create(
            [
                Game(
                    item=self._item(MediaTypes.GAME.value, "gm1"),
                    user=self.user,
                    status=Status.COMPLETED.value,
                    progress=600,  # minutes -> 10 hours
                ),
            ],
        )

        user_media, media_count = stats.get_user_media(self.user, None, None)
        result = {
            entry["media_type"]: (entry["value"], entry["descriptor"])
            for entry in stats.get_consumption_stats(user_media, media_count)
        }

        self.assertEqual(result[MediaTypes.MOVIE.value], (2, "Movies watched"))
        self.assertEqual(result[MediaTypes.ANIME.value], (12, "Anime episodes watched"))
        self.assertEqual(result[MediaTypes.MANGA.value], (40, "Manga chapters read"))
        self.assertEqual(result[MediaTypes.BOOK.value], (250, "Book pages read"))
        self.assertEqual(result[MediaTypes.GAME.value], (10, "Game hours played"))

    def test_consumption_stats_distinguishes_episodes(self):
        """Anime and TV episodes get labelled distinctly when both exist."""
        Anime.objects.bulk_create(
            [
                Anime(
                    item=self._item(MediaTypes.ANIME.value, "a1"),
                    user=self.user,
                    status=Status.IN_PROGRESS.value,
                    progress=12,
                ),
            ],
        )

        season_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Show",
            image="none.jpg",
            season_number=1,
        )
        season = Season.objects.create(
            item=season_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        for number in (1, 2):
            episode_item = Item.objects.create(
                media_id="1668",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title="Show",
                image="none.jpg",
                season_number=1,
                episode_number=number,
            )
            Episode.objects.create(
                item=episode_item,
                related_season=season,
                end_date=datetime.datetime(2025, 1, number, tzinfo=datetime.UTC),
            )

        user_media, media_count = stats.get_user_media(self.user, None, None)
        result = {
            entry["media_type"]: entry["descriptor"]
            for entry in stats.get_consumption_stats(user_media, media_count)
        }

        self.assertEqual(result[MediaTypes.ANIME.value], "Anime episodes watched")
        self.assertEqual(result[MediaTypes.SEASON.value], "TV episodes watched")

    def test_consumption_stats_skips_empty(self):
        """Media types with no consumed amount are omitted."""
        Anime.objects.bulk_create(
            [
                Anime(
                    item=self._item(MediaTypes.ANIME.value, "a0"),
                    user=self.user,
                    status=Status.PLANNING.value,
                    progress=0,
                ),
            ],
        )
        user_media, media_count = stats.get_user_media(self.user, None, None)
        entries = stats.get_consumption_stats(user_media, media_count)
        media_types = {entry["media_type"] for entry in entries}
        self.assertNotIn(MediaTypes.ANIME.value, media_types)


class StatisticsViewTests(TestCase):
    """Test the statistics view."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_statistics_view_default_date_range(self):
        """Test the statistics view with default date range (last year)."""
        # Call the view
        response = self.client.get(reverse("statistics"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/statistics.html")

        self.assertIn("media_count", response.context)
        self.assertIn("media_type_distribution", response.context)
        self.assertIn("score_distribution", response.context)
        self.assertIn("status_distribution", response.context)
        self.assertIn("status_pie_chart_data", response.context)
        self.assertIn("consumption_stats", response.context)
        self.assertIn("in_progress_count", response.context)
        self.assertIn("rated_percent", response.context)

    def test_statistics_view_custom_date_range(self):
        """Test the statistics view with custom date range."""
        start_date = "2023-01-01"
        end_date = "2023-12-31"

        # Call the view with custom date range
        response = self.client.get(
            reverse("statistics") + f"?start-date={start_date}&end-date={end_date}",
        )

        self.assertEqual(response.status_code, 200)

        self.assertIn("media_count", response.context)
        self.assertIn("media_type_distribution", response.context)
        self.assertIn("score_distribution", response.context)
        self.assertIn("status_distribution", response.context)
        self.assertIn("status_pie_chart_data", response.context)
        self.assertIn("consumption_stats", response.context)

    def test_statistics_view_renders_consumption(self):
        """The consumption section renders when the user has consumed media."""
        item = Item.objects.create(
            media_id="a1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Anime",
            image="none.jpg",
        )
        # bulk_create bypasses the custom save so no provider lookups fire.
        Anime.objects.bulk_create(
            [
                Anime(
                    item=item,
                    user=self.user,
                    status=Status.IN_PROGRESS.value,
                    progress=24,
                ),
            ],
        )

        response = self.client.get(
            reverse("statistics") + "?start-date=all&end-date=all",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Consumption")
        self.assertContains(response, "Anime episodes watched")
        self.assertNotContains(response, "Media Timeline")

    def test_statistics_view_summary_metrics(self):
        """The In Progress count and % Rated reflect the user's library."""
        seed = [
            (Status.IN_PROGRESS.value, 8),
            (Status.IN_PROGRESS.value, None),
            (Status.COMPLETED.value, None),
            (Status.PLANNING.value, None),
        ]
        # bulk_create bypasses the custom save so no provider lookups fire.
        Anime.objects.bulk_create(
            [
                Anime(
                    item=Item.objects.create(
                        media_id=f"an{idx}",
                        source=Sources.MANUAL.value,
                        media_type=MediaTypes.ANIME.value,
                        title=f"Anime {idx}",
                        image="none.jpg",
                    ),
                    user=self.user,
                    status=status,
                    score=score,
                    progress=1,
                )
                for idx, (status, score) in enumerate(seed)
            ],
        )

        response = self.client.get(
            reverse("statistics") + "?start-date=all&end-date=all",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["in_progress_count"], 2)
        # 1 of 4 items is scored -> 25%.
        self.assertEqual(response.context["rated_percent"], 25)
        self.assertContains(response, "In Progress")
        self.assertContains(response, "% Rated")

    def test_statistics_view_invalid_date_format(self):
        """Test the statistics view with invalid date format."""
        start_date = "01/01/2023"  # MM/DD/YYYY instead of YYYY-MM-DD
        end_date = "2023/12/31"

        # Call the view with invalid date format
        response = self.client.get(
            reverse("statistics") + f"?start-date={start_date}&end-date={end_date}",
        )

        self.assertEqual(response.status_code, 200)

        date_is_none = (
            response.context["start_date"] is None
            and response.context["end_date"] is None
        )

        self.assertTrue(date_is_none)
