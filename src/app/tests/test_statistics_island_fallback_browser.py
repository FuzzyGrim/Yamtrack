import datetime
import os
import re

from django.contrib.auth import get_user_model
from django.test import tag
from django.utils import timezone
from playwright.sync_api import sync_playwright

from app.models import Item, MediaTypes, Movie, Sources, Status
from app.tests.live_server import SerialStaticLiveServerTestCase

LEGACY_CHARTS = """() => Object.values(window.Chart?.instances || {})
  .map(chart => chart.canvas.id)
  .sort()"""
UNMOUNTED = """() => [...document.querySelectorAll('[data-props-id]')]
  .filter(host => !host.hasAttribute('data-react-mounted'))
  .map(host => host.dataset.propsId)"""


@tag("slow", "playwright")
class StatisticsIslandFallbackTests(SerialStaticLiveServerTestCase):
    """The legacy Chart.js script draws only what React does not replace."""

    @classmethod
    def setUpClass(cls):
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        super().setUpClass()
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        super().tearDownClass()

    def setUp(self):
        self.credentials = {"username": "stats-fallback", "password": "12345"}
        user = get_user_model().objects.create_user(**self.credentials)
        now = timezone.now()
        for index in range(3):
            item = Item.objects.create(
                media_id=f"fallback-movie-{index}",
                source=Sources.MANUAL.value,
                media_type=MediaTypes.MOVIE.value,
                title=f"Fallback Movie {index}",
                runtime_minutes=100,
            )
            Movie.objects.create(
                item=item,
                user=user,
                status=Status.COMPLETED.value,
                score=7 + index,
                start_date=now - datetime.timedelta(days=index + 1),
                end_date=now - datetime.timedelta(days=index + 1),
            )

    def open_statistics(self, block_islands=False):
        context = self.browser.new_context()
        self.addCleanup(context.close)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        self.addCleanup(lambda: self.assertEqual(errors, []))
        page.goto(f"{self.live_server_url}/")
        page.get_by_placeholder("Enter your username").fill(
            self.credentials["username"],
        )
        page.get_by_placeholder("Enter your password").fill(
            self.credentials["password"],
        )
        page.get_by_role("button", name="Sign in").click()
        if block_islands:
            # A bundle that never arrives: the chart and section islands stay
            # on their server fallback.
            context.route(
                re.compile(r"/static/ui/mountStatistics(Charts|Sections)-"),
                lambda route: route.abort(),
            )
        page.goto(f"{self.live_server_url}/statistics")
        return page

    def test_charts_react_replaces_are_not_drawn_by_the_legacy_script(self):
        page = self.open_statistics()
        page.wait_for_function(f"() => ({UNMOUNTED})().length === 0")
        self.assertEqual(page.evaluate(LEGACY_CHARTS), [])
        # Past the fallback reveal: nothing was drawn late either.
        page.wait_for_timeout(4500)
        self.assertEqual(page.evaluate(LEGACY_CHARTS), [])
        self.assertGreater(
            page.locator("[data-statistics-charts-root][data-react-mounted] *").count(),
            0,
        )

    def test_a_fallback_that_is_revealed_gets_its_charts(self):
        page = self.open_statistics(block_islands=True)
        page.wait_for_timeout(1500)
        self.assertEqual(page.evaluate(LEGACY_CHARTS), [])
        page.wait_for_function("() => Object.keys(window.Chart?.instances || {}).length > 0", timeout=8000)
        charts = page.evaluate(LEGACY_CHARTS)
        self.assertIn("ratingDistributionChart", charts)
        self.assertIn("combinedPlaysByMonthChart", charts)
        self.assertIn("statistics-charts-overview", page.evaluate(UNMOUNTED))
        self.assertTrue(page.locator("#activityRhythmContainer svg").count() > 0)
