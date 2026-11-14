import datetime
import os
import re

from django.contrib.auth import get_user_model
from django.test import tag
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright

from app.models import Item, MediaTypes, Movie, Sources, Status
from app.tests.live_server import SerialStaticLiveServerTestCase

# Widths where the five summary cards are narrowest: a phone, and a laptop with the
# sidebar open (the cards only get ~130px each there).
NARROW_WIDTHS = (360, 1024, 1280)


@tag("slow", "playwright")
class StatisticsLayoutTests(SerialStaticLiveServerTestCase):
    """Nothing on Statistics may spill out of its card or off the screen (#1526)."""

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
        self.credentials = {"username": "stats-layout", "password": "12345"}
        user = get_user_model().objects.create_user(**self.credentials)
        # Most plays on a Wednesday, the longest English weekday name.
        now = timezone.now()
        wednesday = (now - datetime.timedelta(days=(now.weekday() - 2) % 7)).replace(
            hour=12,
            minute=0,
        )
        for index in range(4):
            item = Item.objects.create(
                media_id=f"movie-{index}",
                source=Sources.MANUAL.value,
                media_type=MediaTypes.MOVIE.value,
                title=(f"Movie {index}: " + "An exceptionally long title about the history of everything"),
                runtime_minutes=120,
            )
            Movie.objects.create(
                item=item,
                user=user,
                status=Status.COMPLETED.value,
                score=7,
                start_date=wednesday - datetime.timedelta(days=7 * index),
                end_date=wednesday - datetime.timedelta(days=7 * index),
            )

    def open_statistics(self, width, height=800):
        context = self.browser.new_context(
            viewport={"width": width, "height": height},
        )
        self.addCleanup(context.close)
        page = context.new_page()
        page.goto(f"{self.live_server_url}/")
        page.get_by_placeholder("Enter your username").fill(
            self.credentials["username"],
        )
        page.get_by_placeholder("Enter your password").fill(
            self.credentials["password"],
        )
        page.get_by_role("button", name="Sign in").click()
        page.goto(f"{self.live_server_url}/statistics")
        expect(page.locator('[data-testid="stats-hero-count"]')).to_contain_text("4 titles")
        return page

    def test_summary_cards_keep_their_text_inside(self):
        for width in NARROW_WIDTHS:
            with self.subTest(width=width):
                page = self.open_statistics(width)
                expect(
                    page.locator('[data-testid="stats-summary-card"]').filter(has=page.get_by_text("Most Active Day", exact=True)).locator('[data-testid="stats-summary-value"]'),
                ).to_be_visible()
                spills = page.evaluate(
                    """() => [...document.querySelectorAll('[data-testid="stats-summary-card"]')]
                      .map((card) => {
                        const box = card.getBoundingClientRect();
                        const text = card.querySelector('[data-testid="stats-summary-value"]');
                        return {
                          title: card.querySelector('[data-testid="stats-summary-label"]')?.textContent.trim(),
                          cardOverflows: card.scrollWidth > card.clientWidth,
                          textOverflows: text.scrollWidth > text.clientWidth,
                          offScreen: box.left < 0 || box.right > window.innerWidth,
                        };
                      })
                      .filter((c) => c.cardOverflows || c.textOverflows || c.offScreen)""",
                )
                self.assertEqual(spills, [])

    def test_populated_overview_panels_stay_inside_the_page(self):
        for width in (320, 360, 1280):
            with self.subTest(width=width):
                self.check_overview_panels(width)

    def check_overview_panels(self, width):
        page = self.open_statistics(width)
        overview = page.locator('[data-statistics-sections-root][data-section="overview"]').filter(
            has=page.get_by_text("Most Played", exact=True),
        ).first
        expect(overview.get_by_text("Most Played", exact=True)).to_be_visible()
        panels = overview.locator('[data-testid="statistics-overview-panel"]')
        self.assertGreater(panels.count(), 0)
        spills = panels.evaluate_all(
            """panels => panels.filter(panel => {
              const box = panel.getBoundingClientRect();
              const parent = panel.closest('[data-statistics-sections-root]').getBoundingClientRect();
              return box.left < parent.left - 1 || box.right > parent.right + 1;
            }).map(panel => panel.textContent.slice(0, 80))""",
        )
        self.assertEqual(spills, [])
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)

    def test_summary_values_are_not_broken_mid_word(self):
        for width in NARROW_WIDTHS:
            with self.subTest(width=width):
                page = self.open_statistics(width)
                value = page.locator('[data-testid="stats-summary-card"]').filter(has=page.get_by_text("Most Active Day", exact=True)).locator('[data-testid="stats-summary-value"]')
                expect(value).to_be_visible()
                lines = value.evaluate(
                    "(el) => Math.round(el.getBoundingClientRect().height"
                    " / parseFloat(getComputedStyle(el).lineHeight))",
                )
                self.assertEqual(lines, 1)

    def test_average_rating_rows_stay_inside_their_card(self):
        for width in NARROW_WIDTHS:
            with self.subTest(width=width):
                page = self.open_statistics(width)
                card = page.locator('[data-testid="stats-summary-card"]').filter(
                    has=page.get_by_text("Average Rating", exact=True),
                )
                score = card.locator('[data-testid="stats-summary-value"]')
                expect(score).to_have_text(re.compile(r"^7(?:\.0)?(?:\s*/\s*10)?$"))
                expect(score).to_be_visible()
                inside = page.evaluate(
                    """([cardEl, scoreEl]) =>
                      scoreEl.getBoundingClientRect().right
                        <= cardEl.getBoundingClientRect().right""",
                    [card.element_handle(), score.element_handle()],
                )
                self.assertTrue(inside)

    def test_explore_reveal_reverses_and_respects_reduced_motion(self):
        page = self.open_statistics(1280, height=450)
        button = page.get_by_role("button", name="Explore")
        reveal = page.locator(".sidebar-reveal")
        expect(button).to_have_attribute("aria-expanded", "false")
        self.assertTrue(reveal.locator("..").evaluate("el => el.inert"))

        button.click()
        expect(button).to_have_attribute("aria-expanded", "true")
        expect(reveal.get_by_role("link", name="Statistics", exact=True)).to_be_visible()
        self.assertFalse(reveal.locator("..").evaluate("el => el.inert"))
        self.assertEqual(reveal.evaluate("el => getComputedStyle(el).transitionProperty"), "transform")
        self.assertTrue(reveal.evaluate("""el => [...el.querySelectorAll('div')].some(
            child => getComputedStyle(child).overflowY === 'auto' && child.scrollHeight > child.clientHeight
        )"""))

        # Reverse before the transition ends; the last click must win.
        button.click()
        button.click()
        page.wait_for_timeout(300)
        expect(button).to_have_attribute("aria-expanded", "true")
        self.assertEqual(reveal.evaluate("el => getComputedStyle(el).transform"), "matrix(1, 0, 0, 1, 0, 0)")
        page.emulate_media(reduced_motion="reduce")
        button.click()
        expect(button).to_have_attribute("aria-expanded", "false")
        self.assertLess(float(reveal.evaluate("el => parseFloat(getComputedStyle(el).transitionDuration)")), 0.001)
        self.assertTrue(reveal.locator("..").evaluate("el => el.inert"))

    def test_boosted_sidebar_navigation_initialises_the_page(self):
        # hx-boost swaps the body, and Alpine used to run before the externally
        # loaded date-range.js, leaving the whole component uninitialised (#1667).
        # Build the snapshot through the test client first: a cold Statistics
        # response can take longer than the boosted swap's timeout on CI, and
        # this test is about the swap, not the first build.
        self.client.force_login(
            get_user_model().objects.get(username=self.credentials["username"]),
        )
        self.assertEqual(self.client.get("/statistics").status_code, 200)
        context = self.browser.new_context(viewport={"width": 1280, "height": 800})
        self.addCleanup(context.close)
        page = context.new_page()
        page.goto(f"{self.live_server_url}/")
        page.get_by_placeholder("Enter your username").fill(
            self.credentials["username"],
        )
        page.get_by_placeholder("Enter your password").fill(
            self.credentials["password"],
        )
        page.get_by_role("button", name="Sign in").click()
        # Wait for the post-login document and its deferred Home rows, otherwise
        # the login/navigation can finish after the marker is set or the boosted
        # request can queue behind them on the serial test server.
        expect(page.locator('[data-sidebar-root][data-react-mounted="true"]')).to_be_visible()
        page.wait_for_load_state("networkidle")

        # A full page load would drop this marker; a boosted swap keeps it.
        page.evaluate("window.__floppyBoostMarker = 'home'")
        # Statistics moved to Explore; this lower nav remains hx-boosted.
        page.get_by_role("button", name="Explore").click()
        page.get_by_role("link", name="Statistics", exact=True).click()

        expect(page.locator('[data-testid="stats-hero-count"]')).to_contain_text(
            "4 titles",
            timeout=15000,
        )
        expect(page.locator(".stats-range-button .stats-button-title")).not_to_be_empty()
        expect(
            page.locator('[data-testid="stats-summary-card"]').filter(has=page.get_by_text("Most Active Day", exact=True)).locator('[data-testid="stats-summary-value"]'),
        ).to_be_visible()
        self.assertEqual(page.evaluate("window.__floppyBoostMarker"), "home")

    def test_dropdown_menus_stay_on_a_short_screen(self):
        # 640px is where the toolbar wraps and the "Updated ... ago" line pushes the
        # menus further down, so the room left below them is smaller than at 1100px.
        for width in (1100, 640):
            page = self.open_statistics(width, height=600)
            for button, panel in (
                (".stats-mediatype-button", ".stats-mediatype-wrapper"),
                (".stats-range-button", ".stats-range-wrapper"),
                (".stats-compare-button", ".stats-compare-wrapper"),
            ):
                with self.subTest(width=width, menu=button):
                    page.locator(button).click()
                    menu = page.locator(f"{panel} .stats-dropdown-panel")
                    expect(menu).to_be_visible()
                    # Let the open animation finish before measuring.
                    page.wait_for_timeout(400)
                    bottom = menu.evaluate("(el) => el.getBoundingClientRect().bottom")
                    self.assertLessEqual(bottom, 600)
                    page.locator(button).click()
                    expect(menu).to_be_hidden()
