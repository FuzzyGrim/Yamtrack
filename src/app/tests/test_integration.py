import base64
import json
import os
from datetime import date
from pathlib import Path
from unittest.mock import patch

import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.cache import cache
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright


class IntegrationTest(StaticLiveServerTestCase):
    """Integration tests for the application."""

    @classmethod
    def setUpClass(cls):
        """Set up the test class."""
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        super().setUpClass()
        cls.playwright = sync_playwright().start()
        # use headless=False, slow_mo=200 to see the browser
        cls.browser = cls.playwright.chromium.launch()
        cls.page = cls.browser.new_page()

    def setUp(self):
        """Set up test data for CustomList model."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.page.goto(f"{self.live_server_url}/")
        self.page.get_by_placeholder("Enter your username").fill(
            self.credentials["username"],
        )
        self.page.get_by_placeholder("Enter your password").fill(
            self.credentials["password"],
        )
        self.page.get_by_role("button", name="Sign in").click()

    @classmethod
    def tearDownClass(cls):
        """Tear down the test class."""
        super().tearDownClass()
        cls.browser.close()
        cls.playwright.stop()

    def test_manual_stage_desktop_and_mobile(self):
        """Create and inspect an imageless hybrid work at both viewport sizes."""
        for width in (1280, 390):
            with self.subTest(width=width):
                self.page.set_viewport_size({"width": width, "height": 900})
                self.page.goto(f"{self.live_server_url}/create")
                self.page.get_by_role("button", name="Stage", exact=True).click()
                self.page.get_by_placeholder("Enter title").fill(f"Local Work {width}")
                self.page.get_by_label("Play", exact=True).check()
                self.page.get_by_label("Musical", exact=True).check()
                self.page.get_by_label("Venue", exact=True).fill("First Theatre")
                self.page.get_by_label("Date Seen", exact=True).fill("2026-09-01")
                self.page.get_by_role("button", name="Create Entry").click()
                expect(self.page.locator("body")).to_contain_text(
                    f"Local Work {width} added successfully.",
                )
                self.page.goto(f"{self.live_server_url}/test/stage")
                self.page.get_by_title(f"Local Work {width}", exact=True).click()
                expect(self.page.get_by_role("main")).to_contain_text("Play, Musical")
                expect(self.page.get_by_role("main")).to_contain_text("First Theatre")
                first_visit = json.loads(
                    self.page.locator(
                        "button[hx-get*='track_modal']"
                    ).first.get_attribute("hx-vals")
                )["instance_id"]
                self._click_settled(self.page.get_by_title("More tracking options"))
                self._click_settled(
                    self.page.get_by_role("button", name="Add new entry")
                )
                self.page.get_by_label("Venue", exact=True).fill("Second Theatre")
                self._click_settled(
                    self.page.get_by_role("button", name="Add", exact=True)
                )
                expect(self.page.get_by_role("main")).to_contain_text("Second Theatre")
                expect(self.page.get_by_role("main")).to_contain_text("First Theatre")
                self._click_settled(
                    self.page.locator(
                        "button[hx-get*='track_modal']"
                        f'[hx-vals*=\'"instance_id": "{first_visit}"\']'
                    )
                )
                expect(self.page.get_by_label("Venue", exact=True)).to_have_value(
                    "First Theatre"
                )
                self.page.get_by_label("Venue", exact=True).fill("Revised Theatre")
                self.page.get_by_label("Notes", exact=True).fill(
                    "Earlier visit corrected"
                )
                self._click_settled(
                    self.page.get_by_role("button", name="Update", exact=True)
                )
                expect(self.page.get_by_role("main")).to_contain_text("Revised Theatre")
                expect(self.page.get_by_role("main")).to_contain_text("Second Theatre")
                self.page.goto(f"{self.live_server_url}/journal")
                expect(self.page.get_by_role("main")).to_contain_text(
                    "Updated venue from First Theatre to Revised Theatre",
                )
                self.assertTrue(
                    self.page.evaluate(
                        "document.documentElement.scrollWidth <= window.innerWidth",
                    ),
                )
        self.page.set_viewport_size({"width": 1280, "height": 720})

    def _click_settled(self, control):
        """Wait for injected controls to be initialized before interacting."""
        expect(control).to_be_visible()
        expect(self.page.locator(".htmx-settling")).to_have_count(0)
        control.click()

    def test_stage_wikipedia_poster_search_to_library(self):
        """Display exact-article posters and non-free credits on both sizes."""
        fixture = json.loads(
            (Path(__file__).parent / "mock_data/stage_artwork.json").read_text()
        )
        work = {
            **fixture["work"],
            "id": "Q19320959",
            "labels": {"en": {"value": "Hamilton"}},
            "sitelinks": {"enwiki": {"title": "Hamilton (musical)"}},
        }
        poster = fixture["wikipedia"]
        image_url = poster["file"]["imageinfo"][0]["thumburl"]
        image = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII="
        )

        def source_response(url, params, **_kwargs):
            if url == "https://en.wikipedia.org/w/api.php":
                payload = {
                    "query": {
                        "pages": [
                            poster["article"]
                            if params["prop"] == "pageprops|pageimages|info"
                            else poster["file"]
                        ]
                    }
                }
            elif url == "https://commons.wikimedia.org/w/api.php":
                payload = {"query": {"search": []}}
            elif params["action"] == "query":
                payload = {"query": {"search": [{"title": "Q19320959"}]}}
            else:
                payload = {"entities": {"Q19320959": work}}
            response = requests.Response()
            response.status_code = 200
            response._content = json.dumps(payload).encode()
            return response

        cache.clear()
        self.page.route(
            image_url, lambda route: route.fulfill(body=image, content_type="image/png")
        )
        try:
            with patch(
                "app.providers.services.session.get", side_effect=source_response
            ):
                for visit, width in enumerate((1280, 390)):
                    self.page.set_viewport_size({"width": width, "height": 900})
                    self.page.goto(
                        f"{self.live_server_url}/search?media_type=stage&q=Hamilton"
                    )
                    picture = self.page.get_by_role("img", name="Hamilton", exact=True)
                    expect(picture).to_have_attribute("src", image_url)
                    expect(picture).to_have_js_property("naturalWidth", 1)
                    expect(picture).to_have_css("object-fit", "contain")
                    expect(
                        self.page.get_by_text("Image credit", exact=True)
                    ).to_have_count(0)
                    self.page.get_by_title("Hamilton", exact=True).click()
                    expect(
                        self.page.get_by_text("Article fair use", exact=False)
                    ).to_be_visible()
                    expect(
                        self.page.get_by_role(
                            "link", name="Wikipedia article", exact=True
                        )
                    ).to_be_visible()
                    self.assertTrue(
                        self.page.evaluate(
                            "document.documentElement.scrollWidth <= innerWidth"
                        )
                    )
                    if visit == 0:
                        self.page.get_by_role(
                            "button", name="Add to tracker", exact=True
                        ).click()
                        self.page.get_by_label("Venue", exact=True).fill(
                            "Local Theatre"
                        )
                        with self.page.expect_response(
                            lambda response: (
                                "/media_save" in response.url
                                and response.request.method == "POST"
                            )
                        ) as saved:
                            self._click_settled(
                                self.page.get_by_role("button", name="Add", exact=True)
                            )
                        self.assertTrue(saved.value.ok)
                    self.page.goto(f"{self.live_server_url}/test/stage")
                    expect(
                        self.page.get_by_role("img", name="Hamilton", exact=True)
                    ).to_have_attribute("src", image_url)
        finally:
            self.page.unroute(image_url)
            self.page.set_viewport_size({"width": 1280, "height": 720})
            cache.clear()

    def test_stage_search_artwork_and_tracking(self):
        """Provider search and saved artwork retain readable credits on both sizes."""
        fixture = json.loads(
            (Path(__file__).parent / "mock_data/stage_artwork.json").read_text()
        )
        poster = fixture["poster"]
        fixture["work"]["claims"]["P18"] = [
            {"mainsnak": {"datavalue": {"value": "Work poster.png"}}}
        ]
        image_url = poster["imageinfo"][0]["url"]
        image = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII="
        )

        cache.clear()
        self.page.route(
            image_url, lambda route: route.fulfill(body=image, content_type="image/png")
        )
        try:
            with patch(
                "app.providers.services.session.get",
                side_effect=lambda url, params, **_kwargs: self._poster_source_response(
                    fixture, url, params
                ),
            ):
                desktop_width = 1280
                for width in (desktop_width, 390):
                    with self.subTest(width=width):
                        self.page.set_viewport_size({"width": width, "height": 900})
                        self.page.goto(
                            f"{self.live_server_url}/search?media_type=stage&q=Bernarda"
                        )
                        picture = self.page.get_by_role(
                            "img", name="The House of Bernarda Alba", exact=True
                        )
                        picture.scroll_into_view_if_needed()
                        expect(picture).to_have_attribute("src", image_url)
                        expect(picture).to_have_js_property("naturalWidth", 1)
                        expect(picture).to_have_css("object-fit", "contain")
                        expect(
                            self.page.get_by_text("Image credit", exact=True)
                        ).to_have_count(0)
                        self.page.get_by_title(
                            "The House of Bernarda Alba", exact=True
                        ).click()
                        expect(
                            self.page.get_by_text("Poster Artist", exact=False)
                        ).to_be_visible()
                        if width == desktop_width:
                            self.page.get_by_role(
                                "button", name="Add to tracker", exact=True
                            ).click()
                            self.page.get_by_label("Venue", exact=True).fill(
                                "Local Theatre"
                            )
                            self.page.get_by_role(
                                "button", name="Add", exact=True
                            ).click()
                        expect(self.page.get_by_role("main")).to_contain_text(
                            "Local Theatre"
                        )
                        self.page.goto(f"{self.live_server_url}/test/stage")
                        expect(
                            self.page.get_by_text("Image credit", exact=True)
                        ).to_have_count(0)
                        self.page.get_by_title(
                            "The House of Bernarda Alba", exact=True
                        ).click()
                        expect(
                            self.page.get_by_role(
                                "link", name="CC BY-SA 4.0", exact=True
                            )
                        ).to_be_visible()
                        self.assertTrue(
                            self.page.evaluate(
                                "document.documentElement.scrollWidth"
                                " <= window.innerWidth"
                            )
                        )
            self._assert_failed_stage_image_keeps_frame(image_url)
        finally:
            self.page.unroute(image_url)
            self.page.set_viewport_size({"width": 1280, "height": 720})
            cache.clear()

    def _assert_failed_stage_image_keeps_frame(self, image_url):
        """Verify an unavailable image leaves the saved card's frame stable."""
        picture = self.page.get_by_role(
            "img", name="The House of Bernarda Alba", exact=True
        )
        frame = picture.bounding_box()
        self.page.route(image_url, lambda route: route.abort())
        self.page.reload()
        picture.scroll_into_view_if_needed()
        expect(picture).to_have_attribute("src", settings.IMG_NONE)
        self.assertEqual(picture.bounding_box()["height"], frame["height"])

    def _poster_source_response(self, fixture, url, params):
        """Serve work-linked photo and poster metadata without discovery."""
        poster = fixture["poster"]
        if url == "https://commons.wikimedia.org/w/api.php":
            self.assertNotIn("list", params)
            self.assertEqual(params["action"], "query")
            if "Work poster.png" in params.get("titles", ""):
                payload = {"query": {"pages": {"124": poster}}}
            else:
                payload = fixture["commons"]
        elif params["action"] == "query":
            payload = {"query": {"search": [{"title": "Q822850"}]}}
        else:
            payload = {"entities": {"Q822850": fixture["work"]}}
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(payload).encode()
        return response

    def test_season_progress_edit(self):
        """Test the progress edit of a season."""
        self.page.get_by_placeholder("Search tv shows...").fill("breaking bad")
        self.page.get_by_role("button").nth(1).click()
        expect(self.page.locator("h2")).to_contain_text("Search Results")
        self.page.get_by_title("Breaking Bad", exact=True).click()
        expect(self.page.get_by_role("main")).to_contain_text("Breaking Bad")
        self.page.get_by_title("Season 1").click()
        expect(self.page.get_by_role("main")).to_contain_text("Season 1")
        self.page.locator(".p-2").first.click()
        expect(self.page.get_by_role("main")).to_contain_text("Track Episode")
        self.page.locator(".relative > .px-2").first.click()
        self.page.get_by_role("button", name="Fill in air date").click()
        self.page.get_by_role("button", name="Add watch").click()

        datetime_format = "%Y-%m-%d"

        # Episode 1 air date is 2008-01-20
        fixed_date = date(2008, 1, 20)

        expect(self.page.get_by_role("main")).to_contain_text(
            f"Last watched: {fixed_date.strftime(datetime_format)}",
        )
        self.page.get_by_role("link", name="Home").click()
        expect(self.page.get_by_text("Breaking Bad S1")).to_be_visible()
        self.page.locator("#media-grid-in-progress-season").get_by_role("button").nth(
            4,
        ).click()
        self.page.get_by_title("Breaking Bad S1").click()

        today = timezone.localtime().strftime(datetime_format)
        expect(self.page.get_by_role("main")).to_contain_text(f"Last watched: {today}")

    def test_tv_completed(self):
        """Test the completed status of a TV show."""
        self.page.get_by_placeholder("Search tv shows...").click()
        self.page.get_by_placeholder("Search tv shows...").fill("breaking bad")
        self.page.locator("form").filter(has_text="TV Shows TV").get_by_role(
            "button",
        ).first.click()
        expect(self.page.locator("h2")).to_contain_text("Search Results")
        self.page.get_by_title("Breaking Bad", exact=True).click()
        expect(self.page.get_by_role("main")).to_contain_text("Breaking Bad")
        self.page.locator("button").filter(has_text="Add to tracker").click()
        expect(self.page.locator("#track-tv-1396")).to_contain_text("Score")
        self.page.get_by_label("Status").select_option("Completed")
        self.page.get_by_role("button", name="Add", exact=True).click()
        self.page.get_by_role("link", name="TV Shows").click()
        self.page.get_by_role("link", name="Table View").click()
        expect(self.page.locator("tbody")).to_contain_text("62")

    def test_season_completed(self):
        """Test the completed status of a season."""
        self.page.get_by_placeholder("Search tv shows...").fill("breaking bad")
        self.page.get_by_role("button").nth(1).click()
        expect(self.page.locator("h2")).to_contain_text("Search Results")
        self.page.get_by_title("Breaking Bad", exact=True).click()
        expect(self.page.get_by_role("main")).to_contain_text("Breaking Bad")
        self.page.get_by_title("Season 1").click()
        expect(self.page.get_by_role("main")).to_contain_text("Season 1")
        self.page.get_by_role("button", name="Add to tracker").click()
        expect(self.page.locator("#track-season-1396-1")).to_contain_text("Score")
        self.page.get_by_role("button", name="Add", exact=True).click()
        self.page.get_by_role("link", name="TV Seasons").click()
        self.page.get_by_role("link", name="Table View").click()
        expect(self.page.locator("tbody")).to_contain_text("Completed")
        expect(self.page.locator("tbody")).to_contain_text("7")

    def test_tv_manual(self):
        """Test the manual creation of a TV show."""
        # Create TV show
        self.page.get_by_role("link", name="Create Custom").click()
        self.page.get_by_placeholder("Enter title").click()
        self.page.get_by_placeholder("Enter title").fill("Friends")
        self.page.get_by_placeholder("Enter image URL").click()
        self.page.get_by_placeholder("Enter image URL").fill(
            "https://media.themoviedb.org/t/p/w300_and_h450_bestv2/2koX1xLkpTQM4IZebYvKysFW1Nh.jpg",
        )
        self.page.get_by_role("combobox").select_option("In progress")
        self.page.get_by_role("button", name="Create Entry").click()
        expect(self.page.locator(".scheme-dark")).to_contain_text(
            "Friends added successfully.",
        )

        # Create season
        self.page.get_by_role("button", name="Season").click()
        expect(self.page.get_by_role("main")).to_contain_text("Parent TV Show")
        self.page.get_by_placeholder("Search for a TV show...").click()
        self.page.get_by_placeholder("Search for a TV show...").type("fri")
        expect(self.page.locator("#parent-tv-results")).to_contain_text("Friends")
        self.page.get_by_role("button", name="Friends").click()
        self.page.get_by_placeholder("Enter image URL").click()
        self.page.get_by_placeholder("Enter image URL").fill(
            "https://media.themoviedb.org/t/p/w130_and_h195_bestv2/odCW88Cq5hAF0ZFVOkeJmeQv1nV.jpg",
        )
        self.page.get_by_role("button", name="Create Entry").click()
        expect(self.page.locator("body")).to_contain_text(
            "Friends S1 added successfully.",
        )

        # Create episode
        self.page.get_by_role("button", name="Episode").click()
        expect(self.page.get_by_role("main")).to_contain_text("Parent Season")
        self.page.get_by_placeholder("Search for a season...").click()
        self.page.get_by_placeholder("Search for a season...").type("frien")
        expect(self.page.locator("#parent-season-results")).to_contain_text(
            "Friends - Season 1",
        )
        self.page.get_by_role("button", name="Friends - Season").click()
        self.page.get_by_placeholder("Enter image URL").click()
        self.page.get_by_placeholder("Enter image URL").fill(
            "https://media.themoviedb.org/t/p/w227_and_h127_bestv2/v6Elr1W2elOyGi1MClgV0mIBVHC.jpg",
        )
        self.page.locator('input[name="end_date"]').fill("2025-03-07")
        self.page.get_by_role("button", name="Create Entry").click()
        expect(self.page.locator("body")).to_contain_text(
            "Friends S1E1 added successfully.",
        )

        # Check visibility
        self.page.get_by_role("link", name="TV Shows").click()
        self.page.get_by_role("link", name="Grid View").click()
        expect(self.page.get_by_role("main")).to_contain_text("Friends")
        self.page.get_by_role("link", name="TV Seasons").click()
        self.page.get_by_role("link", name="Grid View").click()
        expect(self.page.get_by_role("main")).to_contain_text("Friends S1")
        self.page.get_by_role("link", name="TV Shows").click()
        self.page.get_by_title("Friends").click()
        expect(self.page.get_by_role("main")).to_contain_text("Friends")
        expect(self.page.get_by_role("main")).to_contain_text("Season 1")
        self.page.get_by_title("Season 1").click()
        expect(self.page.get_by_role("main")).to_contain_text("Season 1")
        expect(self.page.get_by_role("main")).to_contain_text(
            "Episode 1 • Unknown air date",
        )

    def test_obfuscate_unseen_episodes_enabled(self):
        """Test that obfuscate_unseen_episodes setting is accessible and functional."""
        # Navigate to preferences
        self.page.get_by_role("link", name="Settings").click()
        self.page.get_by_role("link", name="Preferences").click()

        # Verify the obfuscate setting is visible
        expect(self.page.get_by_role("main")).to_contain_text(
            "Obfuscate Unseen Episodes"
        )
        expect(self.page.get_by_role("main")).to_contain_text(
            "unseen episode images and descriptions will be blurred"
        )

        # Find and check the obfuscate checkbox by clicking the label
        # The checkbox is sr-only (hidden), so we need to click the label
        obfuscate_label = self.page.locator(
            'label:has(input[name="obfuscate_unseen_episodes"])'
        )
        obfuscate_label.click()

        # Save preferences
        self.page.get_by_role("button", name="Save Preferences").click()

        # Verify success message
        expect(self.page.locator(".scheme-dark")).to_contain_text("Settings updated")

        # Verify setting persisted
        self.page.get_by_role("link", name="Preferences").click()
        obfuscate_checkbox = self.page.locator(
            'input[name="obfuscate_unseen_episodes"]'
        )
        expect(obfuscate_checkbox).to_be_checked()

    def test_obfuscate_unseen_episodes_disabled(self):
        """Test toggling obfuscate_unseen_episodes setting off."""
        # Navigate to preferences
        self.page.get_by_role("link", name="Settings").click()
        self.page.get_by_role("link", name="Preferences").click()

        # Find the obfuscate checkbox
        obfuscate_checkbox = self.page.locator(
            'input[name="obfuscate_unseen_episodes"]'
        )
        if obfuscate_checkbox.is_checked():
            # Click the label to uncheck (checkbox is sr-only, so click label)
            obfuscate_label = self.page.locator(
                'label:has(input[name="obfuscate_unseen_episodes"])'
            )
            obfuscate_label.click()

            # Save preferences
            self.page.get_by_role("button", name="Save Preferences").click()

            # Verify success message
            expect(self.page.locator(".scheme-dark")).to_contain_text(
                "Settings updated"
            )

            # Verify setting persisted as unchecked
            self.page.get_by_role("link", name="Preferences").click()
            obfuscate_checkbox = self.page.locator(
                'input[name="obfuscate_unseen_episodes"]'
            )
            expect(obfuscate_checkbox).not_to_be_checked()
