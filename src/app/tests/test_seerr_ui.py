"""Playwright tests for the Seerr integration UI."""

import os

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse
from playwright.sync_api import expect, sync_playwright

from app.models import Item, MediaTypes, Movie, Sources, Status


class SeerrIntegrationTest(StaticLiveServerTestCase):
    """Tests for the Seerr integration UI."""

    @classmethod
    def setUpClass(cls):
        """Set up the test class."""
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        super().setUpClass()
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()
        cls.page = cls.browser.new_page()

    @classmethod
    def tearDownClass(cls):
        """Tear down the test class."""
        super().tearDownClass()
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        """Create a user and test data, then inject session cookie."""
        user_model = get_user_model()
        self.credentials = {"username": "test", "password": "12345"}
        self.user = user_model.objects.create_user(**self.credentials)

        # Create a tracked movie that will appear on the home page
        item = Item.objects.create(
            media_id="550",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Fight Club",
            image="http://example.com/image.jpg",
        )
        Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=0,
            score=0,
        )

        # Inject session cookie to avoid login page
        django_client = Client()
        django_client.force_login(self.user)
        session_cookie = django_client.cookies["sessionid"]
        self.page.context.add_cookies(
            [
                {
                    "name": "sessionid",
                    "value": session_cookie.value,
                    "domain": "localhost",
                    "path": "/",
                },
            ]
        )

    # --- Browser tests ---

    def test_seerr_settings_tab(self):
        """The Seerr tab and its form fields appear in Integrations settings."""
        self.page.goto(f"{self.live_server_url}{reverse('integrations')}")

        seerr_tab = self.page.get_by_role("tab", name="Seerr")
        expect(seerr_tab).to_be_visible()

        seerr_tab.click()

        expect(self.page.locator("[name='seerr_url']")).to_be_visible()
        expect(self.page.locator("[name='seerr_api_key']")).to_be_visible()

        save_btn = self.page.get_by_role("button", name="Save Changes")
        expect(save_btn).to_be_visible()

    def test_seerr_button_hidden_when_not_configured(self):
        """Without Seerr config, the Request on Seerr button does not appear."""
        self.page.goto(f"{self.live_server_url}{reverse('home')}")
        self.page.wait_for_load_state("networkidle")

        card = self.page.locator("[id^='home-media-']").first
        card.hover()
        self.page.wait_for_timeout(300)

        seerr_btn = self.page.locator("[title='Request on Seerr']")
        expect(seerr_btn).to_have_count(0)

    def test_seerr_button_visible_when_configured(self):
        """With Seerr configured, the Request on Seerr button appears on cards."""
        self.user.seerr_url = "https://requests.example.com"
        self.user.seerr_api_key = "test-api-key-12345"
        self.user.save(update_fields=["seerr_url", "seerr_api_key"])

        self.page.goto(f"{self.live_server_url}{reverse('home')}")
        self.page.wait_for_load_state("networkidle")

        card = self.page.locator("[id^='home-media-']").first
        card.hover()
        self.page.wait_for_timeout(300)

        seerr_btn = self.page.locator("[title='Request on Seerr']")
        expect(seerr_btn).to_have_count(1)
        expect(seerr_btn).to_be_visible()

    # --- Django Client test (no browser needed) ---

    def test_seerr_request_returns_error_when_not_configured(self):
        """POST to seerr_request without Seerr config returns an error message."""
        client = Client()
        client.force_login(self.user)

        url = reverse(
            "seerr_request",
            kwargs={
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "media_id": "550",
            },
        )
        response = client.post(url, {"return_url": reverse("home")}, follow=True)

        messages = list(response.context["messages"])
        assert len(messages) == 1  # noqa: S101
        assert "Seerr is not configured" in str(messages[0])  # noqa: S101
