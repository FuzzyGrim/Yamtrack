"""Editing a play from a podcast episode's Activity History, in a real browser."""

import os
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import tag
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright

from app.models import (
    Item,
    MediaTypes,
    Podcast,
    PodcastEpisode,
    PodcastPlay,
    PodcastShow,
    Sources,
)
from app.services import podcast_plays
from app.tests.live_server import SerialStaticLiveServerTestCase


@tag("slow", "playwright")
class PodcastHistoryEditBrowserTests(SerialStaticLiveServerTestCase):
    """The history pencil must open the play editor (#1895)."""

    @classmethod
    def setUpClass(cls):
        """Start the browser."""
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        super().setUpClass()
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()
        expect.set_options(timeout=15000)

    @classmethod
    def tearDownClass(cls):
        """Stop the browser."""
        expect.set_options(timeout=5000)
        cls.browser.close()
        cls.playwright.stop()
        super().tearDownClass()

    def setUp(self):
        """Create a show with two episodes and sign in."""
        self.user = get_user_model().objects.create_user("listener", password="12345")
        self.show = PodcastShow.objects.create(
            podcast_uuid="show-browser",
            title="Browser Show",
            source=Sources.POCKETCASTS.value,
        )
        self.played = PodcastEpisode.objects.create(
            show=self.show,
            episode_uuid="ep-played",
            title="Played Episode",
            duration=1800,
            published=timezone.now() - timedelta(days=9),
        )
        self.fresh = PodcastEpisode.objects.create(
            show=self.show,
            episode_uuid="ep-fresh",
            title="Fresh Episode",
            duration=1800,
            published=timezone.now() - timedelta(days=2),
        )
        podcast_plays.create_play(
            self.user,
            self.show,
            episode=self.played,
            end_date=timezone.now() - timedelta(days=5),
        )
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.page.goto(f"{self.live_server_url}/")
        self.page.get_by_placeholder("Enter your username").fill("listener")
        self.page.get_by_placeholder("Enter your password").fill("12345")
        self.page.get_by_role("button", name="Sign in").click()
        expect(self.page.locator("#global-search")).to_be_visible()
        self.page.wait_for_function("() => window.htmx")
        self.page.wait_for_load_state("networkidle")

    def tearDown(self):
        """Close the browser context before Django flushes the database."""
        self.context.close()
        super().tearDown()

    def open_show(self):
        """Open the show page."""
        self.page.goto(
            f"{self.live_server_url}/details/pocketcasts/podcast/show-browser/browser-show",
        )
        self.page.wait_for_load_state("networkidle")

    def test_history_pencil_opens_a_form(self):
        """The pencil closes Activity History and opens a form, not a dimmed dead end."""
        self.page.goto(
            f"{self.live_server_url}/details/pocketcasts/podcast/show-browser/browser-show",
        )
        self.page.wait_for_load_state("networkidle")
        self.page.locator("[data-episode-uuid='ep-played']").get_by_title(
            "View your activity history",
        ).click()
        expect(self.page.get_by_text("Activity History")).to_be_visible()
        row = self.page.locator("[data-history-modal-root] li").first
        row.hover()
        row.get_by_title("Edit").click()
        expect(self.page.get_by_text("Activity History")).to_have_count(0)
        expect(
            self.page.get_by_role("heading", name="Track Episode").locator("visible=true"),
        ).to_have_count(1)

    def test_episode_play_dialog_has_one_owner_and_saves(self):
        """The React dialog replaces the fallback while retaining episode details."""
        self.open_show()
        for episode_id in ("ep-played", "ep-fresh"):
            row = self.page.locator(f"[data-episode-uuid='{episode_id}']")
            row.get_by_title("Track Episode").click()
            dialog = self.page.get_by_role("dialog")
            expect(dialog).to_have_count(1)
            expect(dialog.get_by_text("Played Episode" if episode_id == "ep-played" else "Fresh Episode")).to_be_visible()
            expect(row.locator("[data-track-fallback]")).to_be_hidden()
            if episode_id == "ep-played":
                if screenshot := os.environ.get("FLOPPY_TRACK_DIALOG_SCREENSHOT"):
                    self.page.wait_for_timeout(500)
                    self.page.screenshot(path=screenshot)
                self.page.keyboard.press("Escape")
                expect(dialog).to_have_count(0)
            else:
                dialog.get_by_role("button", name="Add play").click()
                expect(dialog).to_have_count(0)
        self.assertTrue(PodcastPlay.objects.filter(podcast__episode=self.fresh).exists())

    def test_episode_fallback_saves_when_react_bundle_fails(self):
        """The server form remains usable when the React adapter cannot load."""
        cookies = self.context.cookies()
        self.context.close()
        self.context = self.browser.new_context(service_workers="block")
        self.context.add_cookies(cookies)
        self.page = self.context.new_page()
        self.page.route("**/static/ui/mountTrackForm-*.js", lambda route: route.abort())
        self.open_show()
        row = self.page.locator("[data-episode-uuid='ep-fresh']")
        row.get_by_title("Track Episode").click()
        fallback = row.locator("[data-track-fallback]")
        expect(fallback).to_be_visible()
        with self.page.expect_response(lambda response: "/podcast/episode/save/" in response.url) as saved:
            fallback.get_by_role("button", name="Add play").click()
        self.assertTrue(saved.value.ok)
        expect(fallback).to_be_hidden()
        self.assertTrue(PodcastPlay.objects.filter(podcast__episode=self.fresh).exists())
