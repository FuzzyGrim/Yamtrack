"""Browser acceptance for the shared alphabet island and its legacy siblings."""

import io
import os
import re
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import tag
from django.urls import reverse
from PIL import Image
from playwright.sync_api import expect, sync_playwright

from app.models import Item, Movie, Sources, Status
from app.tests.live_server import SerialStaticLiveServerTestCase
from lists.models import CustomList, CustomListItem


@tag("slow", "playwright")
class AlphabetBrowserTests(SerialStaticLiveServerTestCase):
    """Use real Django responses, bundled React and the existing HTMX runtime."""

    # Browser layout can overshoot on very short viewports in hosted Chromium.
    # Keep this acceptance test running as a diagnostic until the rail's
    # responsive positioning and clipping behavior are verified and repaired.
    @tag("flaky_alphabet_rail")
    def test_alphabet_navigation_fallback_themes_and_lifecycle(self):
        """Check desktop/mobile, current form state, remounts and unchanged results."""
        cache.clear()
        user = get_user_model().objects.create_user(username="alphabet-browser")
        self.client.force_login(user)
        manual = CustomList.objects.create(
            name="Alphabet manual", owner=user, visibility="public"
        )
        smart = CustomList.objects.create(
            name="Alphabet smart",
            owner=user,
            is_smart=True,
            smart_filters={"media_types": ["movie"]},
        )
        for title in ("Alpha", "Beta", "【Series"):
            item = Item.objects.create(
                media_id=title,
                title=title,
                source=Sources.MANUAL.value,
                media_type="movie",
                image="/static/img/placeholder.svg",
            )
            Movie.objects.create(item=item, user=user, status=Status.COMPLETED.value)
            CustomListItem.objects.create(custom_list=manual, item=item)
        library = reverse("medialist", args=["movie"])
        screenshots = os.environ.get("FLOPPY_ALPHABET_SCREENSHOTS")
        with (
            patch.dict(os.environ, {"DJANGO_ALLOW_ASYNC_UNSAFE": "true"}),
            sync_playwright() as playwright,
            playwright.chromium.launch() as browser,
        ):
            context = browser.new_context(
                viewport={"width": 1440, "height": 1000}, service_workers="block"
            )
            context.add_cookies(
                [
                    {
                        "name": "sessionid",
                        "value": self.client.cookies["sessionid"].value,
                        "url": self.live_server_url,
                    }
                ]
            )
            block_renderer = False

            def serve(route):
                """Keep the test offline and allow exercising the real fallback."""
                url = urlsplit(route.request.url)
                if url.netloc != urlsplit(self.live_server_url).netloc or (
                    block_renderer and "/static/ui/mountAlphabet-" in url.path
                ):
                    route.abort()
                else:
                    route.continue_()

            context.route("**/*", serve)
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            for theme, scheme in (
                ("light", "dark"),
                ("dark", "light"),
                ("system", "light"),
                ("system", "dark"),
                ("custom", "light"),
                ("custom", "dark"),
            ):
                user.theme = theme
                user.save(update_fields=["theme"])
                page.emulate_media(color_scheme=scheme, reduced_motion="reduce")
                # Compare the server fallback and mounted bar without touching cards.
                block_renderer = True
                page.goto(f"{self.live_server_url}{library}?initial=a")
                expect(
                    page.locator('.alphabet-rail a[aria-current="true"]')
                ).to_have_text("A")
                before = page.locator(".media-grid").bounding_box()
                if screenshots:
                    Path(screenshots).mkdir(parents=True, exist_ok=True)
                    page.screenshot(
                        path=str(Path(screenshots) / f"{theme}-{scheme}-fallback.png")
                    )
                block_renderer = False
                page.reload()
                expect(
                    page.locator('.alphabet-rail[data-react-mounted="true"]')
                ).to_have_count(1)
                after = page.locator(".media-grid").bounding_box()
                for dimension in before:
                    self.assertAlmostEqual(before[dimension], after[dimension], delta=1)
                # The mounted rail follows the first poster, which can sit a
                # few pixels below the grid box. Do not compare it with the
                # grid's outer edge: that is only the fallback position.
                # Mounting and poster layout settle in separate frames, and on a
                # loaded runner the grid can still move after the rail first
                # matches it. Wait until the rail and its anchor agree on two
                # frames in a row, in one in-page check, so nothing can shift
                # between the wait and the comparison.
                page.wait_for_function(
                    """() => {
                      const place = () => {
                        const rail = document.querySelector('.alphabet-rail');
                        const grid = document.querySelector('.media-grid');
                        if (!rail || !grid) return null;
                        const poster = grid.querySelector('img')?.getBoundingClientRect();
                        const anchor = poster?.width && poster?.height
                          ? poster.top : grid.getBoundingClientRect().top;
                        const want = Math.max(96, Math.ceil(anchor));
                        const got = rail.getBoundingClientRect().top;
                        return Math.abs(got - want) <= 1 ? want : null;
                      };
                      const first = place();
                      if (first === null) return false;
                      return new Promise(resolve => requestAnimationFrame(() =>
                        requestAnimationFrame(() => resolve(place() === first))));
                    }""",
                    timeout=15000,
                )
                rail_bounds = page.locator(".alphabet-rail").bounding_box()
                self.assertAlmostEqual(
                    rail_bounds["y"] + rail_bounds["height"], 984, delta=0.5
                )
                self.assertEqual(
                    page.locator(".alphabet-rail a").all_text_contents()[:3],
                    ["All", "#", "A"],
                )
                expect(page.locator(".alphabet-rail a").last).to_have_text("【")
                self.assertEqual(
                    page.locator('.alphabet-rail [aria-current="true"]').inner_text(),
                    "A",
                )
                self.assertLessEqual(
                    page.evaluate("document.documentElement.scrollWidth"), 1440
                )
                if screenshots:
                    page.screenshot(
                        path=str(Path(screenshots) / f"{theme}-{scheme}-react.png")
                    )

            # Check visible glyph ink, not its full-width Unicode advance box.
            glyph = Image.open(
                io.BytesIO(page.locator('[data-alphabet-initial="other"]').screenshot())
            ).convert("RGB")
            background = glyph.getpixel((0, 0))
            ink_x = [
                x
                for y in range(glyph.height)
                for x in range(glyph.width)
                if max(
                    abs(a - b)
                    for a, b in zip(glyph.getpixel((x, y)), background, strict=True)
                )
                > 10
            ]
            self.assertTrue(ink_x)
            self.assertAlmostEqual(
                (min(ink_x) + max(ink_x)) / 2, (glyph.width - 1) / 2, delta=1.5
            )
            if screenshots:
                glyph.save(Path(screenshots) / "other-glyph.png")

            block_renderer = True
            page.goto(f"{self.live_server_url}{library}?initial=q&layout=grid")
            empty_before = page.locator(".alphabet-empty").bounding_box()
            expect(
                page.get_by_role(
                    "heading", name="No titles match this alphabet filter", level=3
                )
            ).to_be_visible()
            if screenshots:
                page.screenshot(path=str(Path(screenshots) / "empty-fallback.png"))
            block_renderer = False
            page.reload()
            expect(
                page.locator('.alphabet-empty[data-react-mounted="true"]')
            ).to_have_count(1)
            empty_after = page.locator(".alphabet-empty").bounding_box()
            for dimension in empty_before:
                self.assertAlmostEqual(
                    empty_before[dimension], empty_after[dimension], delta=1
                )
            expect(
                page.get_by_role(
                    "heading", name="No titles match this alphabet filter", level=3
                )
            ).to_be_visible()
            if screenshots:
                page.screenshot(path=str(Path(screenshots) / "empty-react.png"))
            block_renderer = True
            page.goto(
                f"{self.live_server_url}{library}?initial=a&search=Beta&layout=grid&page=2"
            )
            page.locator(".alphabet-rail").get_by_role(
                "link", name="Titles starting with B", exact=True
            ).click()
            expect(page.locator(".media-grid")).to_contain_text("Beta")
            self.assertIn("search=Beta", page.url)
            self.assertNotIn("page=", page.url)
            page.locator(".alphabet-rail").get_by_role(
                "link", name="All titles", exact=True
            ).click()
            expect(page.locator(".media-grid")).to_contain_text("Beta")
            self.assertNotIn("initial=", page.url)
            self.assertIn("search=Beta", page.url)
            block_renderer = False

            for path in (
                library,
                reverse("list_detail", args=[manual.public_reference]),
                reverse("list_detail", args=[smart.public_reference]),
            ):
                for layout in ("grid", "table"):
                    page.goto(f"{self.live_server_url}{path}?layout={layout}&initial=a")
                    expect(
                        page.locator('.alphabet-rail[data-react-mounted="true"]')
                    ).to_have_count(1)
                    beta = page.locator(".alphabet-rail").get_by_role(
                        "link", name="Titles starting with B", exact=True
                    )
                    beta.focus()
                    beta.press("Enter")
                    page.wait_for_url("**initial=b*")
                    expect(
                        page.locator('.alphabet-rail [aria-current="true"]')
                    ).to_have_text("B")
                    self.assertIn(f"layout={layout}", page.url)
                    page.go_back()
                    expect(
                        page.locator('.alphabet-rail[data-react-mounted="true"]')
                    ).to_have_count(1)
                    expect(
                        page.locator('.alphabet-rail [aria-current="true"]')
                    ).to_have_text("A")

            page.goto(f"{self.live_server_url}{library}?initial=q&layout=grid")
            expect(
                page.locator('.alphabet-empty[data-react-mounted="true"]')
            ).to_have_count(1)
            page.get_by_role("link", name="Clear alphabet filter", exact=True).click()
            expect(page.locator(".media-grid")).to_contain_text("Beta")
            page.wait_for_load_state("load")
            page.wait_for_function("Boolean(window.Alpine && window.htmx)")
            self.assertEqual(errors, [])
            # Form changes made by existing controls must survive a letter click.
            page.evaluate(
                "Alpine.$data(document.getElementById('filter-form')).search = 'Beta'"
            )
            expect(page.locator('#filter-form [name="search"]')).to_have_value("Beta")
            page.locator(".alphabet-rail").get_by_role(
                "link", name="Titles starting with B", exact=True
            ).click()
            page.wait_for_url("**initial=b*")
            self.assertIn("search=Beta", page.url)
            self.assertEqual(
                page.evaluate(
                    "htmx.values(document.getElementById('filter-form')).initial"
                ),
                "b",
            )
            # Existing HTMX swaps remove and remount the new component cleanly.
            page.evaluate(
                "window.previousAlphabetHost = document.querySelector('[data-alphabet-root]')"
            )
            page.evaluate(
                "void htmx.ajax('GET', location.href, {target: 'body', swap: 'innerHTML', headers: {'HX-Boosted': 'true'}})"
            )
            page.wait_for_function("!previousAlphabetHost.isConnected")
            expect(
                page.locator('.alphabet-rail[data-react-mounted="true"]')
            ).to_have_count(1)
            self.assertFalse(page.evaluate("previousAlphabetHost.isConnected"))
            self.assertIsNone(
                page.evaluate("previousAlphabetHost.dataset.reactMounted")
            )
            for height in (600, 480, 360):
                page.set_viewport_size({"width": 1024, "height": height})
                page.wait_for_function(
                    """() => {
                      const grid = document.querySelector('.media-grid');
                      const poster = grid.querySelector('img')?.getBoundingClientRect();
                      const anchor = poster?.width && poster?.height
                        ? poster.top : grid.getBoundingClientRect().top;
                      const railTop = document.querySelector('.alphabet-rail')
                        .getBoundingClientRect().top;
                      return Math.abs(railTop - Math.max(96, Math.ceil(anchor))) <= 1;
                    }"""
                )
                rail = page.locator(".alphabet-rail")
                self.assertEqual(rail.bounding_box()["width"], 24)
                page.wait_for_function(
                    """() => {
                      const rail = document.querySelector('.alphabet-rail');
                      return rail.scrollHeight <= rail.clientHeight + 1;
                    }"""
                )
                # Verify the actual link hit areas, not only the clipped rail.
                # Include the offending letter and measured viewport position
                # in failures so a short-screen regression is diagnosable.
                violations = rail.locator("a").evaluate_all(
                    """elements => elements.flatMap(element => {
                      const rect = element.getBoundingClientRect();
                      const within = rect.top >= 95 && rect.bottom <= innerHeight - 14;
                      return within ? [] : [{
                        letter: element.getAttribute('aria-label') || element.textContent,
                        top: rect.top, bottom: rect.bottom, viewport: innerHeight
                      }];
                    })"""
                )
                self.assertEqual(violations, [], (height, violations))
            page.set_viewport_size({"width": 390, "height": 844})
            expect(page.locator(".alphabet-rail")).to_be_hidden()
            self.assertLessEqual(
                page.evaluate("document.documentElement.scrollWidth"), 390
            )
            if screenshots:
                page.screenshot(path=str(Path(screenshots) / "mobile.png"))
                (Path(screenshots) / "browser.txt").write_text(
                    f"Chromium {browser.version}\n"
                )
            context.clear_cookies()
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.goto(
                f"{self.live_server_url}{reverse('list_detail', args=[manual.public_reference])}?initial=b"
            )
            expect(
                page.locator('.alphabet-rail[data-react-mounted="true"]')
            ).to_have_count(1)
            expect(page.locator("#items-view")).to_contain_text("Beta")
            self.assertEqual(errors, [])
            context.close()

    def test_boosted_navigation_refreshes_stale_ui_assets(self):
        """An already-open pre-build page must not retain the old island assets."""
        cache.clear()
        user = get_user_model().objects.create_user(username="stale-alphabet")
        self.client.force_login(user)
        movie_url = reverse("medialist", args=["movie"])
        tv_url = reverse("medialist", args=["tv"])
        with (
            patch.dict(os.environ, {"DJANGO_ALLOW_ASYNC_UNSAFE": "true"}),
            sync_playwright() as playwright,
            playwright.chromium.launch() as browser,
        ):
            context = browser.new_context(
                viewport={"width": 1440, "height": 900}, service_workers="block"
            )
            context.add_cookies(
                [
                    {
                        "name": "sessionid",
                        "value": self.client.cookies["sessionid"].value,
                        "url": self.live_server_url,
                    }
                ]
            )
            first_document = True

            def serve(route):
                """Reproduce a page opened before the alphabet loader/CSS existed."""
                nonlocal first_document
                url = urlsplit(route.request.url)
                if url.netloc != urlsplit(self.live_server_url).netloc:
                    route.abort()
                elif url.query == "stale-ui":
                    route.fulfill(
                        status=200,
                        body="",
                        content_type="text/css"
                        if url.path.endswith(".css")
                        else "application/javascript",
                    )
                elif first_document and route.request.is_navigation_request():
                    first_document = False
                    response = route.fetch()
                    body = re.sub(
                        r"<script data-ui-asset-refresh>.*?</script>",
                        "",
                        response.text(),
                        flags=re.DOTALL,
                    )
                    body = re.sub(
                        r'(/static/ui/settings-islands\.(?:js|css))\?[^"\s]+',
                        r"\1?stale-ui",
                        body,
                    )
                    route.fulfill(response=response, body=body)
                else:
                    route.continue_()

            context.route("**/*", serve)
            page = context.new_page()
            page.goto(self.live_server_url + movie_url)
            self.assertEqual(
                page.locator(".alphabet-rail").evaluate(
                    "element => getComputedStyle(element).position"
                ),
                "static",
            )
            # The normal sidebar uses HTMX boost: its response replaces only body.
            page.get_by_role("link", name="TV Shows", exact=True).click()
            expect(
                page.locator('head script[src*="ui/settings-islands.js"]')
            ).not_to_have_attribute("src", re.compile("stale-ui"))
            expect(
                page.locator('.alphabet-rail[data-react-mounted="true"]')
            ).to_have_count(1)
            self.assertEqual(urlsplit(page.url).path, tv_url)
            self.assertEqual(
                page.locator(".alphabet-rail").evaluate(
                    "element => getComputedStyle(element).position"
                ),
                "fixed",
            )
            self.assertEqual(page.locator(".alphabet-rail").bounding_box()["width"], 24)
            context.close()
