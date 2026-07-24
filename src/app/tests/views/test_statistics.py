from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.models import Item, MediaTypes, Movie, Sources


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
        self.assertIn("activity_data", response.context)
        self.assertIn("media_type_distribution", response.context)
        self.assertIn("score_distribution", response.context)
        self.assertIn("status_distribution", response.context)
        self.assertIn("status_pie_chart_data", response.context)
        self.assertIn("timeline", response.context)
        self.assertNotIn("summary_groups", response.context)
        self.assertContains(response, "Media Timeline")

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
        self.assertIn("activity_data", response.context)
        self.assertIn("media_type_distribution", response.context)
        self.assertIn("score_distribution", response.context)
        self.assertIn("status_distribution", response.context)
        self.assertIn("status_pie_chart_data", response.context)
        self.assertIn("timeline", response.context)

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

    def test_statistics_summary_route_and_modes(self):
        """The dedicated route renders both summary aggregation modes."""
        self.assertEqual(
            reverse("statistics_summary"),
            "/statistics/summary/",
        )

        for view_name in ("summary", "titles"):
            with self.subTest(view=view_name):
                response = self.client.get(
                    reverse("statistics_summary"),
                    {"view": view_name},
                )

                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "app/statistics_summary.html")
                self.assertEqual(response.context["summary_view"], view_name)
                self.assertIn("summary_groups", response.context)
                self.assertContains(response, 'aria-current="page"')
                self.assertContains(response, "No activity in this period")
                self.assertNotContains(response, "Media Type Distribution")

    def test_populated_summary_route_renders_in_both_modes(self):
        """Populated item and title summaries render without an error."""
        item = Item.objects.create(
            media_id="summary-movie",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Dedicated Summary Movie",
        )
        movie = Movie(user=self.user, item=item)
        Movie.save_base(movie)

        for view_name in ("summary", "titles"):
            with self.subTest(view=view_name):
                response = self.client.get(
                    reverse("statistics_summary"),
                    {
                        "start-date": "all",
                        "end-date": "all",
                        "view": view_name,
                    },
                )

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Dedicated Summary Movie")

    def test_summary_links_preserve_query_parameters(self):
        """Summary tabs and page navigation preserve applicable filters."""
        response = self.client.get(
            reverse("statistics_summary"),
            {
                "start-date": "2025-01-01",
                "end-date": "2025-05-31",
                "view": "summary",
                "future-filter": "kept",
            },
        )

        for view_name, view_url in response.context["summary_view_urls"].items():
            query = parse_qs(urlparse(view_url).query)
            self.assertEqual(query["start-date"], ["2025-01-01"])
            self.assertEqual(query["end-date"], ["2025-05-31"])
            self.assertEqual(query["future-filter"], ["kept"])
            self.assertEqual(query["view"], [view_name])

        statistics_query = parse_qs(
            urlparse(response.context["statistics_page_url"]).query,
        )
        self.assertEqual(statistics_query["start-date"], ["2025-01-01"])
        self.assertEqual(statistics_query["end-date"], ["2025-05-31"])
        self.assertEqual(statistics_query["future-filter"], ["kept"])
        self.assertNotIn("view", statistics_query)

    def test_summary_custom_date_range_stays_on_summary_page(self):
        """Date-filtered requests render the dedicated page directly."""
        response = self.client.get(
            reverse("statistics_summary"),
            {
                "start-date": "2025-02-01",
                "end-date": "2025-02-28",
                "view": "titles",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/statistics_summary.html")
        self.assertEqual(
            response.context["start_date"].date().isoformat(),
            "2025-02-01",
        )
        self.assertEqual(
            response.context["end_date"].date().isoformat(),
            "2025-02-28",
        )
        self.assertEqual(response.context["summary_view"], "titles")

    def test_invalid_summary_view_falls_back_to_item_summary(self):
        """Unknown summary modes render the safe default without an error."""
        response = self.client.get(
            reverse("statistics_summary"),
            {"view": "unknown"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["summary_view"], "summary")

    def test_legacy_summary_query_redirects_to_dedicated_page(self):
        """Bookmarks from the combined implementation continue to work."""
        response = self.client.get(
            reverse("statistics"),
            {
                "start-date": "2025-01-01",
                "end-date": "2025-05-31",
                "view": "titles",
                "future-filter": "kept",
            },
        )

        self.assertEqual(response.status_code, 302)
        location = urlparse(response["Location"])
        self.assertEqual(location.path, reverse("statistics_summary"))
        query = parse_qs(location.query)
        self.assertEqual(query["start-date"], ["2025-01-01"])
        self.assertEqual(query["end-date"], ["2025-05-31"])
        self.assertEqual(query["future-filter"], ["kept"])
        self.assertEqual(query["view"], ["titles"])

    def test_statistics_and_summary_navigation_is_visible(self):
        """Both pages expose clear navigation while preserving dates."""
        for route_name in ("statistics", "statistics_summary"):
            with self.subTest(route=route_name):
                response = self.client.get(
                    reverse(route_name),
                    {
                        "start-date": "2025-01-01",
                        "end-date": "2025-05-31",
                    },
                )

                self.assertContains(response, 'aria-label="Statistics pages"')
                self.assertContains(response, ">Statistics</a>")
                self.assertContains(response, ">Summary</a>")
