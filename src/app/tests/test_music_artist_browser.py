"""Artist grids render and remount with the bundled browser islands."""

import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import tag
from django.urls import reverse
from playwright.sync_api import expect, sync_playwright

from app.models import Album, Artist, ArtistMember
from app.tests.live_server import SerialStaticLiveServerTestCase


@tag("slow", "playwright")
class MusicArtistBrowserTests(SerialStaticLiveServerTestCase):
    def test_grids_mount_and_survive_relation_refresh(self):
        user = get_user_model().objects.create_user(username="music-browser")
        self.client.force_login(user)
        band = Artist.objects.create(name="Band", image="/static/img/placeholder.svg")
        for number in range(3):
            member = Artist.objects.create(
                name=f"Member {number}", image="/static/img/placeholder.svg"
            )
            ArtistMember.objects.create(band=band, member=member, is_current=True)
            Album.objects.create(
                title=f"Album {number}",
                artist=band,
                image="/static/img/placeholder.svg",
            )
        url = reverse("music_artist_details", args=[band.id, "band"])
        refresh = reverse("prefetch_artist_relation_images", args=[band.id])
        with (
            patch.dict(os.environ, {"DJANGO_ALLOW_ASYNC_UNSAFE": "true"}),
            patch("app.services.music.resolve_artist_mbid", return_value=(None, 0, "")),
            sync_playwright() as playwright,
        ):
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            context.add_cookies(
                [
                    {
                        "name": "sessionid",
                        "value": self.client.cookies["sessionid"].value,
                        "url": self.live_server_url,
                    }
                ]
            )
            page = context.new_page()
            page.goto(f"{self.live_server_url}{url}")
            for width in (1280, 390):
                page.set_viewport_size({"width": width, "height": 900})
                for selector in (
                    "[data-music-relations-root]",
                    "[data-music-albums-root]",
                ):
                    grid = page.locator(selector).first
                    expect(grid).to_have_attribute("data-react-mounted", "true")
                    cards = grid.locator(".media-card")
                    expect(cards).to_have_count(3)
                    self.assertGreater(
                        cards.nth(1).bounding_box()["x"],
                        cards.nth(0).bounding_box()["x"],
                    )
                    self.assertGreater(cards.nth(0).bounding_box()["width"], 100)
                    self.assertTrue(
                        cards.nth(0).locator("a").first.get_attribute("href")
                    )
            page.evaluate(
                "url => htmx.ajax('GET', url, {target: '#artist-relations-container', swap: 'outerHTML'})",
                refresh,
            )
            expect(
                page.locator(
                    "[data-music-relations-root][data-react-mounted=true] .media-card"
                )
            ).to_have_count(3)
            browser.close()
