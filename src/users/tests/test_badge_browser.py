import json
import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import tag
from django.urls import reverse
from playwright.sync_api import sync_playwright

from app.models import PodcastShow
from app.tests.live_server import SerialStaticLiveServerTestCase


@tag("slow", "playwright")
class BadgeBrowserTests(SerialStaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.allow_async = patch.dict(os.environ, {"DJANGO_ALLOW_ASYNC_UNSAFE": "true"})
        cls.allow_async.start()
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.allow_async.stop()
        super().tearDownClass()

    def test_badge_variants_match_fallback_and_keep_real_links(self):
        tones = ("indigo", "rose", "violet", "neutral", "outline", "tint", "genre", "green-soft", "indigo-soft", "emerald-soft", "amber-soft")
        rows = []
        for size in ("xs", "sm", "md", "tag"):
            for tone in tones:
                key = f"{size}-{tone}"
                link = tone == "genre"
                tag_name = "a" if link else "span"
                href = ' href="/movies/?genre=Drama"' if link else ""
                old = f'<{tag_name}{href} class="ui-badge ui-badge-{size}" data-tone="{tone}">Example Label</{tag_name}>'
                props = json.dumps({"label": "Example Label", "size": size, "tone": tone, "href": "/movies/?genre=Drama" if link else "", "nowrap": False})
                rows.append(f'<div data-case="{key}"><div class="old">{old}</div><div class="new"><script id="p-{key}" type="application/json">{props}</script><span data-badge-root data-props-id="p-{key}" class="ui-badge-host">{old}</span></div></div>')
        props = json.dumps({"label": "A long genre label that must not wrap", "size": "sm", "tone": "genre", "href": "/movies/?genre=Drama", "nowrap": True})
        old = '<a href="/movies/?genre=Drama" class="ui-badge ui-badge-sm ui-badge-nowrap" data-tone="genre">A long genre label that must not wrap</a>'
        rows.append(f'<div data-case="nowrap"><div class="old" style="width:100px">{old}</div><div class="new" style="width:100px"><script id="p-nowrap" type="application/json">{props}</script><span data-badge-root data-props-id="p-nowrap" class="ui-badge-host">{old}</span></div></div>')
        page = self.browser.new_page(viewport={"width": 390, "height": 844})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(self.live_server_url + "/static/css/main.css")
        page.set_content(f"""<!doctype html><html class="dark"><head>
          <link rel="stylesheet" href="/static/css/main.css">
          <link rel="stylesheet" href="/static/ui/settings-islands.css">
          <script type="module" src="/static/ui/settings-islands.js"></script>
          </head><body>{''.join(rows)}</body></html>""")
        page.wait_for_function("document.querySelectorAll('[data-badge-root][data-react-mounted]').length === 45")
        for theme in ("", "light", "dark", "catppuccin_mocha", "dracula", "nord", "gruvbox", "oled", "glass", "plex", "projector", "video_store", "custom"):
            page.evaluate("theme => { document.documentElement.className = theme; }", theme)
            page.wait_for_timeout(200)
            diffs = page.evaluate("""() => Array.from(document.querySelectorAll('[data-case]')).flatMap(row => {
              const old = row.querySelector('.old > *');
              const current = row.querySelector('[data-badge-root] > *');
              const badge = current.tagName === 'A' ? current.firstElementChild : current;
              const a = old.getBoundingClientRect(), b = badge.getBoundingClientRect();
              const x = getComputedStyle(old), y = getComputedStyle(badge);
              const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1;
              const ctx = canvas.getContext('2d');
              const rgba = value => { ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = value; ctx.fillRect(0, 0, 1, 1); return [...ctx.getImageData(0, 0, 1, 1).data]; };
              const colorDiff = property => rgba(x[property]).some((v, i) => Math.abs(v - rgba(y[property])[i]) > 3);
              return Math.abs(a.width - b.width) > 0.15 || Math.abs(a.height - b.height) > 0.15 || colorDiff('color') || colorDiff('backgroundColor') || colorDiff('borderTopColor') ? [row.dataset.case] : [];
            })""")
            self.assertEqual(diffs, [], f"{theme}: {diffs}")
        link = page.locator('[data-case="sm-genre"] .new a')
        self.assertEqual(link.locator("> *").evaluate("e => getComputedStyle(e).transitionDuration"), "0.2s")
        self.assertEqual(
            page.locator('[data-case="nowrap"] .new a > *').evaluate("e => getComputedStyle(e).whiteSpace"),
            "nowrap",
        )
        self.assertEqual(link.get_attribute("href"), "/movies/?genre=Drama")
        self.assertEqual(link.evaluate("e => getComputedStyle(e).textDecorationLine"), "none")
        link.focus()
        self.assertTrue(link.evaluate("e => document.activeElement === e"))
        initial = link.locator("> *").evaluate("e => getComputedStyle(e).backgroundColor")
        link.hover()
        page.wait_for_function(
            "before => getComputedStyle(document.querySelector('[data-case=sm-genre] .new a > *')).backgroundColor !== before",
            arg=initial,
        )
        page.wait_for_function(
            "getComputedStyle(document.querySelector('[data-case=sm-genre] .new a > *')).backgroundColor === getComputedStyle(document.documentElement).getPropertyValue('--color-genre-badge-hover-bg').trim()"
        )
        self.assertEqual(
            link.locator("> *").evaluate("e => getComputedStyle(e).backgroundColor"),
            page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--color-genre-badge-hover-bg').trim()"),
        )
        host = page.locator('[data-case="sm-genre"] [data-badge-root]')
        host.evaluate("e => e.dispatchEvent(new CustomEvent('htmx:beforeCleanupElement', {bubbles:true, detail:{elt:e}}))")
        self.assertIsNone(host.get_attribute("data-react-mounted"))
        self.assertEqual(host.locator("a.ui-badge").count(), 1)
        host.evaluate("e => e.dispatchEvent(new CustomEvent('htmx:load', {bubbles:true, detail:{elt:e}}))")
        page.wait_for_function("document.querySelector('[data-case=sm-genre] [data-badge-root]').dataset.reactMounted === 'true'")
        self.assertFalse(errors, errors)
        page.close()

    def test_real_podcast_genre_badge_mounts(self):
        from app.podcast_views import podcast_show_url

        user = get_user_model().objects.create_user(username="badge-proof")
        show = PodcastShow.objects.create(podcast_uuid="badge-proof", title="Badge proof", genres=["Comedy"])
        self.client.force_login(user)
        context = self.browser.new_context(viewport={"width": 390, "height": 844})
        context.add_cookies([{"name": "sessionid", "value": self.client.cookies["sessionid"].value, "url": self.live_server_url}])
        page = context.new_page()
        page.goto(self.live_server_url + podcast_show_url(show), wait_until="domcontentloaded")
        # The show page can render the genre row in more than one layout slot.
        badge = page.locator("[data-badge-root][data-react-mounted]").filter(has_text="Comedy").first
        badge.wait_for(state="attached")
        self.assertEqual(badge.locator("> *").inner_text(), "Comedy")
        self.assertEqual(badge.locator("> *").evaluate("e => getComputedStyle(e).height"), "24px")
        context.close()
