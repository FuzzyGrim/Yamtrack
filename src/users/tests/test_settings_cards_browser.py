"""Browser proof for the React card shell alongside real Django/Alpine forms."""

import json
import os
import re
from unittest import skipUnless
from unittest.mock import patch
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings, tag
from django_celery_beat.models import CrontabSchedule, PeriodicTask
from django_celery_results.models import TaskResult
from playwright.sync_api import expect, sync_playwright

from app.providers import credentials
from app.tests.live_server import SerialStaticLiveServerTestCase
from integrations.models import AudiobookshelfAccount, ImportRun

# The header search form, the sidebar, the popup host, the bulk-actions dock and
# the settings shell are React islands on every signed-in page, so the card
# counts below leave them out and measure the one content island.
MOUNTED_ISLANDS = (
    '[data-react-mounted="true"]:not([data-shell-root]):not([data-global-search-root])'
    ":not([data-sidebar-root]):not([data-modal-host-root]):not([data-bulk-actions-root])"
    ":not([data-settings-view-root])"
)


@tag("slow", "playwright")
class SettingsCardsBrowserTests(SerialStaticLiveServerTestCase):
    """Exercise a real local Django server with provider network access blocked."""

    @classmethod
    def setUpClass(cls):
        """Use the same synchronous browser/Django boundary as the local proof."""
        super().setUpClass()
        cls.async_override = patch.dict(os.environ, {"DJANGO_ALLOW_ASYNC_UNSAFE": "true"})
        cls.async_override.start()
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()
        cls.proof_metrics = {}

    @classmethod
    def tearDownClass(cls):
        """Release Chromium and restore the Django async guard."""
        cls.browser.close()
        cls.playwright.stop()
        cls.async_override.stop()
        super().tearDownClass()
        if cls.proof_metrics:
            print("Settings-card browser metrics:", json.dumps(cls.proof_metrics))

    def setUp(self):
        """Authenticate the browser through an isolated local Django session."""
        cache.clear()
        self.user = get_user_model().objects.create_user(username="card-proof", theme="system")
        self.client.force_login(self.user)
        self.errors = []
        self.block_renderer = False
        self.probe = patch("app.providers.credentials._probe", return_value=None)
        self.probe.start()
        self.addCleanup(self.probe.stop)
        self.context = self.browser.new_context(
            viewport={"width": 1440, "height": 1000}, service_workers="block",
        )
        self.addCleanup(self.context.close)
        self.context.add_cookies([{
            "name": "sessionid", "value": self.client.cookies["sessionid"].value,
            "url": self.live_server_url,
        }])
        self.context.route("**/*", self.serve)
        self.page = self.context.new_page()
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.failed_requests = []
        self.page.on("requestfailed", lambda request: self.failed_requests.append((request.url, request.failure)))

    def serve(self, route):
        """Allow real local HTTP responses and block only provider traffic/chunks."""
        url = urlsplit(route.request.url)
        if url.netloc != urlsplit(self.live_server_url).netloc or (self.block_renderer and (
            "/static/ui/mount" in url.path or "/static/ui/Directory-" in url.path
        )):
            route.abort()
        else:
            route.continue_()

    def open_cards(self, tab="import"):
        """Wait for the single owner of the entire AniList provider region."""
        response = self.page.goto(f"{self.live_server_url}/settings/providers/anilist?tab={tab}")
        try:
            expect(self.page.locator(MOUNTED_ISLANDS)).to_have_count(1)
        except AssertionError as error:
            error.add_note(
                f"HTTP status: {response.status if response else None}; URL: {self.page.url}; "
                f"provider hosts: {self.page.locator('[data-provider-root]').count()}; "
                f"page errors: {self.errors[-5:]}; failed requests: {self.failed_requests[-5:]}"
            )
            raise

    def select_trigger(self, label):
        """SelectField exposes the field label and current value as a button."""
        return self.page.get_by_role(
            "button", name=re.compile(rf"^{re.escape(label)}: ")
        )

    def choose_option(self, label, option):
        """Exercise the native SelectField dialog and accessible radio choices."""
        self.select_trigger(label).click()
        self.page.get_by_role("radio", name=option, exact=True).click()

    @skipUnless("debug_toolbar" in settings.INSTALLED_APPS, "debug toolbar is disabled")
    @override_settings(DEBUG=True, INTERNAL_IPS=["127.0.0.1"])
    def test_debug_toolbar_initializes_when_present(self):
        """A permitted debug toolbar initializes without breaking boosted navigation."""
        self.open_cards()
        expect(self.page.locator("#djDebug")).to_have_count(1)
        self.page.get_by_role("navigation", name="Provider sections").get_by_role(
            "tab", name="Connection", exact=True,
        ).click()
        expect(self.page.locator("#djDebug")).to_have_count(1)
        self.assertEqual(self.errors, [])

    def test_shared_dropdown_menu_keyboard_dismissal_and_connection_status(self):
        """Universal select sheets filter the directory and remain usable on phones."""
        self.page.goto(f"{self.live_server_url}/settings/providers")
        expect(
            self.page.locator('[data-provider-directory-root][data-react-mounted="true"]')
        ).to_have_count(1)
        dropdown = self.select_trigger("Filter by capability")
        dropdown.click()
        expect(self.page.get_by_role("radio", name="All Capabilities", exact=True)).to_be_visible()
        self.page.get_by_role("radio", name="Connection", exact=True).click()
        expect(self.select_trigger("Filter by capability")).to_contain_text("Connection")
        self.page.wait_for_url("**capability=connection")
        expect(self.page.get_by_role("button", name=re.compile(r"^Open ")).first).to_be_visible()
        expect(self.page.get_by_role("button", name="Open Goodreads", exact=True)).to_have_count(0)

        self.select_trigger("Filter by capability").click()
        expect(self.page.get_by_role("radio", name="Connection", exact=True)).to_be_visible()
        self.page.keyboard.press("Escape")
        expect(self.page.get_by_role("radio", name="Connection", exact=True)).to_have_count(0)

        self.page.set_viewport_size({"width": 390, "height": 1000})
        for label, option in (
            ("Filter by capability", "All Capabilities"),
            ("Filter by media type", "All Types"),
        ):
            self.select_trigger(label).click()
            radio = self.page.get_by_role("radio", name=option, exact=True)
            expect(radio).to_be_visible()
            box = radio.bounding_box()
            self.assertGreaterEqual(box["x"], 0)
            self.assertLessEqual(box["x"] + box["width"], 390)
            self.page.keyboard.press("Escape")

        self.page.set_viewport_size({"width": 1440, "height": 1000})
        self.open_cards("import")
        self.select_trigger("Import Frequency").click()
        expect(self.page.get_by_role("radio", name="Every Day", exact=True)).to_be_visible()
        self.page.keyboard.press("Escape")
        self.assertEqual(self.errors, [])

    def test_credentials_help_restores_focus_and_forms_keep_csrf(self):
        """React owns the dialog and submits through the existing CSRF-safe backend."""
        self.open_cards("connection")
        badge = self.page.get_by_role("button", name="Read-only: explain instance credential access")
        badge.focus()
        self.page.keyboard.press("Enter")
        dialog = self.page.get_by_role("dialog", name="Instance credential access")
        expect(dialog).to_be_visible()
        self.page.keyboard.press("Escape")
        expect(dialog).not_to_be_visible()
        expect(badge).to_be_focused()
        self.page.get_by_label("Your Client ID", exact=True).fill("test-public-client")
        self.page.get_by_label("Your Client secret", exact=True).fill("test-personal-secret")
        with self.page.expect_response(lambda response: "/personal" in response.url and response.request.method == "POST") as result:
            self.page.get_by_role("button", name="Save personal key", exact=True).click()
        self.assertEqual(result.value.status, 302)
        self.page.wait_for_load_state("networkidle")
        self.assertIn("tab=connection", self.page.url, (result.value.headers, self.failed_requests))
        expect(self.page.locator(MOUNTED_ISLANDS)).to_have_count(1)
        self.assertNotIn("test-personal-secret", "".join(self.page.locator('script[type="application/json"]').all_text_contents()))
        self.assertEqual(self.errors, [])

    def test_import_settings_submit_and_private_gate_still_work(self):
        """React preference writes and private/public toggles preserve entered state."""
        self.open_cards()
        self.page.get_by_label("Public username", exact=True).fill("public-person")
        with self.page.expect_response(lambda response: "/settings/import/save-settings" in response.url) as result:
            self.choose_option("Override mode", "Overwrite Existing")
        self.assertEqual(result.value.status, 204)
        self.user.refresh_from_db()
        self.assertEqual(self.user.import_mode, "overwrite")
        toggle = self.page.get_by_role("button", name="Public / Private", exact=True)
        expect(toggle).to_have_text("Public")
        expect(toggle).to_have_attribute("aria-pressed", "false")
        toggle.click()
        expect(toggle).to_have_text("Private")
        expect(toggle).to_have_attribute("aria-pressed", "true")
        expect(self.page.get_by_text("Private imports require provider credentials.")).to_be_visible()
        toggle.focus()
        toggle.press("Space")
        expect(toggle).to_have_text("Public")
        expect(toggle).to_have_attribute("aria-pressed", "false")
        expect(self.page.get_by_label("Public username", exact=True)).to_have_value("public-person")
        self.assertEqual(self.errors, [])

    def test_shared_import_preferences_and_steam_tooltip(self):
        """The retained provider uses the same controls and clamps help to the viewport."""
        self.page.set_viewport_size({"width": 390, "height": 850})
        self.page.goto(f"{self.live_server_url}/settings/providers/steam?tab=import")
        expect(self.page.locator('[data-import-preferences-root] [data-testid="settings-card"]')).to_be_visible()
        mode = self.select_trigger("Import Mode")
        frequency = self.select_trigger("Import Frequency")
        self.assertLess(mode.bounding_box()["y"], frequency.bounding_box()["y"])
        self.choose_option("Import Mode", "Watchlist")
        expect(self.page.get_by_text("This import mode is not supported by this provider.")).to_be_visible()
        expect(self.select_trigger("Watchlist status")).to_contain_text("Planning")
        expect(self.select_trigger("Watchlist tag")).to_contain_text("No tag")
        self.choose_option("Import Mode", "History")
        expect(self.select_trigger("Override mode")).to_be_visible()
        tooltip = self.page.locator("[data-tooltip-root] button")
        tooltip.click()
        box = self.page.get_by_role("tooltip").bounding_box()
        self.assertGreaterEqual(box["x"], 0)
        self.assertLessEqual(box["x"] + box["width"], 390)
        self.assertEqual(self.errors, [])

    def test_import_controls_use_surface_borders_inset_arrows_and_purple_action(self):
        """Ordinary fields must not resemble invalid controls in either theme."""
        for scheme in ("light", "dark"):
            self.page.emulate_media(color_scheme=scheme)
            self.open_cards()
            for label in ("Import Frequency", "Import Mode", "Public username"):
                field = (self.page.get_by_label(label, exact=True)
                         if label == "Public username" else self.select_trigger(label))
                style = field.evaluate("""element => {
                  const style = getComputedStyle(element), probe = document.createElement('div');
                  probe.style.color = 'var(--color-surface-border)'; element.after(probe);
                  const expected = getComputedStyle(probe).color; probe.remove();
                  return {border:style.borderTopColor, expected, outlineStyle:style.outlineStyle};
                }""")
                self.assertEqual(style["border"], style["expected"])
                self.assertEqual(style["outlineStyle"], "none", (label, style))
            for arrow in self.page.locator(".ui-select-chevron").all():
                geometry = arrow.evaluate("""arrow => {
                  const select = arrow.previousElementSibling, box = select.getBoundingClientRect();
                  const icon = arrow.getBoundingClientRect();
                  return {inset:box.right-icon.right, padding:parseFloat(getComputedStyle(select).paddingRight),
                    iconWidth:icon.width};
                }""")
                self.assertGreaterEqual(geometry["inset"], 10)
                self.assertGreater(geometry["padding"], geometry["iconWidth"] + geometry["inset"])
            color = self.page.get_by_role("button", name="Import", exact=True).evaluate("""element => {
              const probe = document.createElement('div');
              probe.style.color = 'var(--color-indigo-600)'; element.after(probe);
              const expected = getComputedStyle(probe).color; probe.remove();
              return {actual:getComputedStyle(element).backgroundColor, expected};
            }""")
            self.assertEqual(color["actual"], color["expected"])
        self.assertEqual(self.errors, [])

    def test_connection_and_import_heading_spacing_matches(self):
        """Tab divider spacing stays consistent for plain and action-row headings."""
        for width in (390, 1440):
            self.page.set_viewport_size({"width": width, "height": 1000})
            gaps = []
            for tab, title in (("connection", "Provider credentials"), ("import", "Import settings")):
                self.open_cards(tab)
                heading = self.page.get_by_role("heading", name=title, exact=True)
                gap = heading.evaluate("""heading => heading.getBoundingClientRect().top -
                  document.querySelector('[role=tablist]').getBoundingClientRect().bottom""")
                self.assertGreaterEqual(gap, 23)
                self.assertLessEqual(gap, 26)
                gaps.append(gap)
            self.assertLessEqual(abs(gaps[0] - gaps[1]), 2)
        self.assertEqual(self.errors, [])

    def test_retained_import_uses_shared_spacing_and_grid(self):
        """Steam's retained forms use the same section spacing as Connection."""
        for width in (390, 768, 1440):
            self.page.set_viewport_size({"width": width, "height": 1000})
            gaps = []
            for tab, title in (("connection", "Provider credentials"), ("import", "Import settings")):
                self.page.goto(f"{self.live_server_url}/settings/providers/steam?tab={tab}")
                heading = self.page.get_by_role("heading", name=title, exact=True)
                expect(heading).to_be_visible()
                gaps.append(heading.evaluate("heading => heading.getBoundingClientRect().top - document.querySelector('[role=tablist]').getBoundingClientRect().bottom"))
            self.assertLessEqual(abs(gaps[0] - gaps[1]), 2, (width, gaps))
            self.assertEqual(self.page.locator(".ui-card-grid").count(), 1)
            heading = self.page.get_by_role("heading", name="Import settings", exact=True)
            card = self.page.locator(".ui-card-grid .settings-card").first
            self.assertGreaterEqual(card.bounding_box()["y"] - heading.bounding_box()["y"] - heading.bounding_box()["height"], 18)
        self.assertEqual(self.errors, [])

    @patch("integrations.views.tasks.import_audiobookshelf.delay")
    @patch("integrations.views.AudiobookshelfClient.get_me", return_value={})
    def test_account_connection_and_import_flow(self, mock_get_me, mock_import):
        """Shared account form connects, runs an import, and disconnects in place."""
        self.page.goto(f"{self.live_server_url}/settings/providers/audiobookshelf?tab=connection")
        root = self.page.locator('[data-account-connection-root][data-react-mounted="true"]')
        expect(root).to_be_visible()
        expect(root.get_by_role("heading", name="Account connection")).to_be_visible()
        activity_card = root.locator('[data-testid="settings-card"]').filter(has=self.page.get_by_role("heading", name="Automatic import"))
        expect(activity_card.get_by_text("Connecting starts an initial import and recurring sync.")).to_be_visible()
        expect(activity_card.get_by_role("button", name="View activity")).to_be_visible()
        colors = [
            self.page.get_by_role("button", name=label, exact=True).evaluate(
                """element => {
                  const style = getComputedStyle(element);
                  return [style.backgroundColor, style.color, element.getBoundingClientRect().width];
                }"""
            )
            for label in ("Connect", "View activity")
        ]
        self.assertEqual(colors[0], colors[1])
        root.get_by_label("Base URL").fill("https://books.example.com")
        root.get_by_label("API token").fill("browser-secret-token")
        root.get_by_role("button", name="Connect", exact=True).click()
        self.page.wait_for_url("**/settings/providers/audiobookshelf?tab=connection")
        expect(self.page.get_by_role("button", name="Disconnect", exact=True)).to_be_visible()
        mock_get_me.assert_called_once()
        mock_import.assert_called_once()
        self.assertNotIn("browser-secret-token", "".join(self.page.locator('script[type="application/json"]').all_text_contents()))
        self.page.goto(f"{self.live_server_url}/settings/providers/audiobookshelf?tab=import")
        self.page.get_by_role("button", name="Sync now", exact=True).click()
        self.page.wait_for_url("**/settings/providers/audiobookshelf?tab=import")
        self.assertEqual(mock_import.call_count, 2)
        self.page.goto(f"{self.live_server_url}/settings/providers/audiobookshelf?tab=connection")
        self.page.get_by_role("button", name="Disconnect", exact=True).click()
        self.page.wait_for_url("**/settings/providers/audiobookshelf?tab=connection")
        expect(self.page.get_by_label("API token")).to_be_visible()
        self.assertFalse(AudiobookshelfAccount.objects.filter(user=self.user).exists())
        self.assertEqual(self.errors, [])

    def test_disconnected_account_import_uses_shared_action(self):
        self.page.goto(f"{self.live_server_url}/settings/providers/audiobookshelf?tab=import")
        root = self.page.locator('[data-account-import-action-root][data-react-mounted="true"]')
        expect(root.get_by_text("Connect this provider before starting an import.")).to_be_visible()
        expect(root.locator(".settings-card")).to_have_count(0)
        root.get_by_role("button", name="Open Connection").click()
        self.page.wait_for_url("**/settings/providers/audiobookshelf?tab=connection")
        self.assertEqual(self.errors, [])

    def test_file_import_frequency_and_clz_website(self):
        """File-only imports display a locked one-time frequency without changing the saved preference."""
        self.user.import_frequency = "daily"
        self.user.save(update_fields=["import_frequency"])
        self.page.goto(f"{self.live_server_url}/settings/providers/clz?tab=import")
        frequency = self.select_trigger("Import Frequency")
        expect(frequency).to_be_disabled()
        expect(frequency).to_contain_text("One Time Import")
        self.page.get_by_role("button", name="Import frequency help").click()
        expect(self.page.get_by_role("tooltip")).to_contain_text("automatic imports are unavailable")
        self.page.get_by_role("tooltip").get_by_role("button", name="Close").click()
        expect(self.page.get_by_role("link", name="CLZ (formerly Collectorz.com)")).to_have_attribute("href", "https://clz.com/")
        with self.page.expect_response(lambda response: "/settings/import/save-settings" in response.url):
            self.choose_option("Override mode", "Overwrite Existing")
        self.user.refresh_from_db()
        self.assertEqual(self.user.import_frequency, "daily")
        self.page.goto(f"{self.live_server_url}/settings/providers/clz?tab=overview")
        card = self.page.get_by_role("link", name="Automatic import", exact=True)
        expect(card).to_contain_text("File only")
        expect(card).to_contain_text("Automatic imports are unavailable for file uploads")
        self.page.goto(f"{self.live_server_url}/settings/providers/hardcover?tab=import")
        frequency = self.select_trigger("Import Frequency")
        expect(frequency).to_be_enabled()
        expect(frequency).to_contain_text("Every Day")
        self.assertEqual(self.errors, [])

    @patch("integrations.views._queue_staged_task_or_message")
    @patch("integrations.views._stage_upload_or_message", return_value="staged-clz.csv")
    def test_clz_preferences_drive_shared_file_import(self, mock_stage, mock_queue):
        """CLZ preferences submit both wishlist choices with the shared file control."""
        self.user.import_mode = "watchlist"
        self.user.save(update_fields=["import_mode"])
        self.page.goto(f"{self.live_server_url}/settings/providers/clz?tab=import")
        expect(self.page.locator('[data-file-import-root][data-react-mounted="true"]')).to_be_visible()
        mode = self.select_trigger("Import Mode")
        expect(mode).to_be_disabled()
        expect(mode).to_contain_text("Collection and wishlist")
        self.page.get_by_role("button", name="Import mode help").click()
        expect(self.page.get_by_role("tooltip")).to_contain_text("does not import reading or viewing history")
        self.page.get_by_role("tooltip").get_by_role("button", name="Close").click()
        self.choose_option("Wishlist items", "Planning")
        self.page.get_by_label("Tag for wishlist items").fill("Wanted comics")
        self.choose_option("Override mode", "Overwrite Existing")
        self.choose_option("Import as", "Comics")
        picker = self.page.get_by_role("button", name="Select CLZ Export")
        self.assertTrue(
            picker.count(),
            f"CLZ file picker missing. Mounted root: {self.page.locator('[data-file-import-root]').inner_text()[:700]!r}; "
            f"page errors: {self.errors!r}",
        )
        with self.page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/import/clz")):
            with self.page.expect_file_chooser() as chooser:
                picker.click()
            chooser.value.set_files({"name": "clz.csv", "mimeType": "text/csv", "buffer": b"Title,Status\nExample,On Wish List\n"})
        self.page.wait_for_url("**/settings/providers/clz?tab=import")
        mock_stage.assert_called_once()
        kwargs = mock_queue.call_args.kwargs
        self.assertEqual(kwargs["mode"], "overwrite")
        self.assertEqual(kwargs["media_type"], "comicissue")
        self.assertEqual(kwargs["wishlist_status"], "Planning")
        self.assertEqual(kwargs["wishlist_tag"], "Wanted comics")
        self.user.refresh_from_db()
        self.assertEqual((self.user.clz_wishlist_status, self.user.clz_wishlist_tag), ("Planning", "Wanted comics"))
        self.assertEqual(self.errors, [])

    @patch("integrations.views._queue_staged_task_or_message")
    @patch("integrations.views._stage_upload_or_message", return_value="staged-clz.csv")
    def test_clz_upload_fallback_receives_react_preferences(self, mock_stage, mock_queue):
        self.context.route("**/static/ui/mountFileImport-*", lambda route: route.abort())
        self.page.goto(f"{self.live_server_url}/settings/providers/clz?tab=import")
        expect(self.page.locator('[data-import-preferences-root][data-react-mounted="true"]')).to_be_visible()
        self.choose_option("Wishlist items", "Planning")
        self.page.get_by_label("Tag for wishlist items").fill("Wanted fallback")
        self.choose_option("Override mode", "Overwrite Existing")
        self.page.locator('input[name="clz_export"]').set_input_files({"name": "clz.csv", "mimeType": "text/csv", "buffer": b"Title\nExample\n"})
        with self.page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/import/clz")):
            self.page.get_by_role("button", name="Import", exact=True).click()
        mock_stage.assert_called_once()
        self.assertEqual(mock_queue.call_args.kwargs["wishlist_status"], "Planning")
        self.assertEqual(mock_queue.call_args.kwargs["wishlist_tag"], "Wanted fallback")
        self.assertEqual(mock_queue.call_args.kwargs["mode"], "overwrite")

    @patch("integrations.views._queue_staged_task_or_message")
    @patch("integrations.views._stage_upload_or_message", return_value="staged-clz.csv")
    def test_clz_react_upload_receives_fallback_preferences(self, mock_stage, mock_queue):
        self.context.route("**/static/ui/mountImportPreferences-*", lambda route: route.abort())
        self.page.goto(f"{self.live_server_url}/settings/providers/clz?tab=import")
        expect(self.page.locator('[data-file-import-root][data-react-mounted="true"]')).to_be_visible()
        self.page.get_by_label("Wishlist items", exact=True).select_option("Planning")
        self.page.get_by_label("Tag for wishlist items").fill("Fallback preferences")
        self.page.get_by_label("Override mode").select_option("overwrite")
        picker = self.page.get_by_role("button", name="Select CLZ Export")
        self.assertTrue(
            picker.count(),
            f"CLZ file picker missing. Mounted root: {self.page.locator('[data-file-import-root]').inner_text()[:700]!r}; "
            f"page errors: {self.errors!r}",
        )
        with self.page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/import/clz")):
            with self.page.expect_file_chooser() as chooser:
                picker.click()
            chooser.value.set_files({"name": "clz.csv", "mimeType": "text/csv", "buffer": b"Title\nExample\n"})
        mock_stage.assert_called_once()
        self.assertEqual(mock_queue.call_args.kwargs["wishlist_status"], "Planning")
        self.assertEqual(mock_queue.call_args.kwargs["wishlist_tag"], "Fallback preferences")
        self.assertEqual(mock_queue.call_args.kwargs["mode"], "overwrite")

    def test_clz_file_import_fallback_without_bundle(self):
        self.context.route("**/static/ui/mountFileImport-*", lambda route: route.abort())
        self.page.goto(f"{self.live_server_url}/settings/providers/clz?tab=import")
        expect(self.page.get_by_label("Import as")).to_be_visible()
        expect(self.page.locator('input[name="clz_export"]')).to_be_visible()
        expect(self.page.get_by_role("button", name="Import", exact=True)).to_be_visible()

    def test_account_connection_fallback_without_bundle(self):
        self.context.route("**/static/ui/AccountConnection-*", lambda route: route.abort())
        self.page.goto(f"{self.live_server_url}/settings/providers/audiobookshelf?tab=connection")
        expect(self.page.get_by_label("Base URL")).to_be_visible()
        expect(self.page.get_by_label("API token")).to_be_visible()
        expect(self.page.get_by_role("button", name="Connect", exact=True)).to_be_visible()

    def test_provider_activity_uses_shared_cards(self):
        """Activity fragments use the shared card headings, including undoable runs."""
        ImportRun.objects.create(user=self.user, source="pocketcasts", status="completed")
        self.page.goto(f"{self.live_server_url}/settings/providers/pocketcasts?tab=activity")
        panel = self.page.locator("#import-activity-panel")
        for title in ("Active Periodic Imports", "Import History", "Recent Import Runs"):
            card = panel.locator(".settings-card").filter(has=self.page.get_by_role("heading", name=title, exact=True))
            expect(card).to_be_visible()
            expect(card.locator('[data-settings-card-root][data-react-mounted="true"]')).to_have_count(1)
            gap = card.locator(".ui-activity-content").evaluate("element => element.querySelector('.ui-activity-list').getBoundingClientRect().top - element.querySelector('p').getBoundingClientRect().bottom")
            self.assertGreaterEqual(gap, 11, title)
        expect(panel.get_by_role("button", name="Undo this import")).to_be_visible()
        self.assertEqual(self.errors, [])

    def test_directory_uses_available_width_and_preserves_live_filters_and_back(self):
        """Native catalog columns follow container width; filters and history still work."""
        for width in (1920, 1440, 1280, 1135, 390):
            self.page.set_viewport_size({"width": width, "height": 1000})
            self.page.goto(f"{self.live_server_url}/settings/providers")
            expect(self.page.locator('[data-provider-directory-root][data-react-mounted="true"]')).to_have_count(1)
            grid = self.page.locator('[data-testid="provider-directory"] [data-testid="card-grid"]')
            expect(grid.get_by_role("button", name=re.compile(r"^Open ")).first).to_be_visible()
            # CatalogGrid uses flex rows with 1/2/3/4 columns at its documented
            # 384/608/896px content breakpoints, not CSS gridTemplateColumns.
            self.page.wait_for_function("""() => {
              const grid = document.querySelector('[data-testid="provider-directory"] [data-testid="card-grid"]');
              const content = grid?.firstElementChild;
              if (!content) return false;
              const items = [...content.children].map(item => item.getBoundingClientRect());
              if (!items.length) return false;
              const expected = 1 + [384, 608, 896].filter(bp => content.getBoundingClientRect().width >= bp).length;
              return items.filter(item => Math.abs(item.top - items[0].top) <= 2).length === expected;
            }""")
            geometry = grid.evaluate("""grid => {
              const root = grid.firstElementChild;
              const boxes = [...root.children].map(item => item.getBoundingClientRect());
              const inner = root.getBoundingClientRect();
              return { minimumWidth: Math.min(...boxes.map(box => box.width)),
                overflow: boxes.some(box => box.left < inner.left - 1 || box.right > inner.right + 1) };
            }""")
            self.assertGreaterEqual(geometry["minimumWidth"], 185)
            self.assertFalse(geometry["overflow"])
            self.assertFalse(self.page.evaluate("document.documentElement.scrollWidth > innerWidth + 1"))
        search = self.page.get_by_label("Search providers", exact=True)
        search.fill("steam")
        self.page.wait_for_url("**/settings/providers?q=steam")
        expect(self.page.get_by_role("button", name="Open SteamGridDB", exact=True)).to_be_visible()
        self.choose_option("Filter by capability", "Metadata")
        self.choose_option("Filter by media type", "Games")
        self.page.wait_for_url("**capability=metadata&media=game")
        grid = self.page.locator('[data-testid="provider-directory"] [data-testid="card-grid"]')
        expect(grid.get_by_role("button", name=re.compile(r"^Open "))).to_have_count(2)
        expect(self.page.get_by_role("button", name="Open Steam", exact=True)).to_be_visible()
        search.fill("no-such-provider")
        expect(self.page.get_by_text("No providers match these filters.", exact=True)).to_be_visible()
        self.page.go_back()
        expect(search).to_have_value("steam")
        expect(grid.get_by_role("button", name=re.compile(r"^Open "))).to_have_count(2)
        self.page.get_by_role("button", name="Open Steam", exact=True).click()
        self.page.wait_for_url("**/settings/providers/steam?tab=metadata")
        self.assertEqual(self.errors, [])

    def test_directory_connection_and_pin_icons_match(self):
        """Native status and pin affordances remain accessible and inside each card."""
        self.page.goto(f"{self.live_server_url}/settings/providers?capability=connection")
        card = self.page.locator('[data-testid="provider-directory"] [data-testid="card-grid"] > div > div').first
        expect(card.get_by_role("img")).to_be_visible()
        expect(card.get_by_role("button", name=re.compile(r"^Pin "))).to_be_visible()
        geometry = card.evaluate("""card => {
          const bounds = card.getBoundingClientRect();
          const status = card.querySelector('[role="img"]').getBoundingClientRect();
          const pin = card.querySelector('[role="button"][aria-label^="Pin "]').getBoundingClientRect();
          return { statusWithin: status.left >= bounds.left && status.right <= bounds.right + 1,
            pinWithin: pin.left >= bounds.left && pin.right <= bounds.right + 1,
            pinAboveBottom: pin.bottom <= bounds.bottom + 1, separated: status.right <= pin.left + 1 };
        }""")
        self.assertTrue(all(geometry.values()), geometry)
        self.assertEqual(self.errors, [])

    def test_directory_pin_persists_and_sorts_first(self):
        """Pins remain personal and survive a directory reload."""
        self.page.goto(f"{self.live_server_url}/settings/providers")
        pin = self.page.get_by_role("button", name="Pin Steam", exact=True)
        expect(pin).to_be_visible()
        with self.page.expect_response(lambda response: "/settings/providers/pin" in response.url):
            pin.click()
        expect(self.page.get_by_role("button", name="Unpin Steam", exact=True)).to_have_attribute("aria-pressed", "true")
        expect(self.page.locator('[data-testid="provider-directory"] [data-testid="card-grid"] > div > div').first.get_by_role("button", name="Open Steam")).to_be_visible()
        self.page.reload()
        expect(self.page.get_by_role("button", name="Unpin Steam", exact=True)).to_be_visible()
        self.assertEqual(self.errors, [])

    def test_directory_bundle_failure_preserves_native_filter_fallback(self):
        self.block_renderer = True
        self.page.goto(f"{self.live_server_url}/settings/providers")
        expect(self.page.get_by_role("heading", name="Providers", exact=True)).to_be_visible()
        self.page.get_by_label("Search providers", exact=True).fill("steam")
        self.page.get_by_label("Filter by capability", exact=True).select_option("metadata")
        self.page.get_by_label("Filter by media type", exact=True).select_option("game")
        self.page.get_by_role("button", name="Search providers", exact=True).click()
        self.page.wait_for_load_state("networkidle")
        expect(self.page.locator("#provider-results a")).to_have_count(2)
        expect(self.page.locator(MOUNTED_ISLANDS)).to_have_count(0)
        self.assertEqual(self.errors, [])

    def test_chip_focus_ring_is_for_keyboard_navigation_only(self):
        """Shared toggle, dialog and link chips distinguish pointer/keyboard focus."""
        self.open_cards()
        toggle = self.page.get_by_role("button", name="Public / Private", exact=True)
        toggle.click()
        expect(toggle).to_be_focused()
        self.assertEqual(toggle.evaluate("element => getComputedStyle(element).outlineStyle"), "none")
        self.page.keyboard.press("Shift+Tab")
        self.page.keyboard.press("Tab")
        expect(toggle).to_be_focused()
        self.assertEqual(toggle.evaluate("element => getComputedStyle(element).outlineStyle"), "solid")
        self.page.keyboard.press("Space")
        expect(toggle).to_have_attribute("aria-pressed", "false")
        self.open_cards("connection")
        badge = self.page.get_by_role("button", name="Read-only: explain instance credential access")
        badge.click()
        self.page.keyboard.press("Escape")
        expect(badge).to_be_focused()
        self.page.mouse.click(0, 0)
        badge.click()
        self.page.get_by_role("dialog").get_by_role("button", name="Close", exact=True).click()
        expect(badge).to_be_focused()
        self.assertEqual(badge.evaluate("element => getComputedStyle(element).outlineStyle"), "none")
        self.page.keyboard.press("Shift+Tab")
        link = self.page.get_by_role("link", name="Get a key from AniList", exact=True)
        expect(link).to_be_focused()
        self.assertEqual(link.evaluate("element => getComputedStyle(element).outlineStyle"), "solid")
        self.assertEqual(self.errors, [])

    def test_repeated_htmx_cleanup_swaps_and_bfcache_restore(self):
        """Partial/OOB replacements dispose roots and reinitialize exactly once."""
        self.open_cards()
        for _ in range(12):
            self.page.evaluate("""() => {
              const host = document.querySelector('[data-provider-root]');
              const original = host.querySelector('input[aria-label="Public username"]');
              htmx.trigger(host, 'htmx:load', {elt: host});
              if (host.querySelector('input[aria-label="Public username"]') !== original) throw Error('Duplicate load replaced the form');
              htmx.trigger(host, 'htmx:beforeCleanupElement', {elt: host});
              if (host.dataset.reactMounted) throw Error('Root was not disposed');
              const copy = host.cloneNode(true);
              host.replaceWith(copy);
              htmx.trigger(copy, 'htmx:load', {elt: copy});
            }""")
            expect(self.page.locator(MOUNTED_ISLANDS)).to_have_count(1)
        self.page.get_by_label("Public username", exact=True).fill("retained-on-back")
        self.page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted:true}))")
        expect(self.page.locator(MOUNTED_ISLANDS)).to_have_count(1)
        self.page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}))")
        expect(self.page.locator(MOUNTED_ISLANDS)).to_have_count(1)
        expect(self.page.get_by_label("Public username", exact=True)).to_have_value("retained-on-back")
        # Dispatch the navigation without returning an HTMX promise to
        # Playwright: a body replacement can detach its fulfillment handler.
        self.page.evaluate("() => { void htmx.ajax('GET', '/settings/providers/anilist?tab=connection', {target:'body', swap:'innerHTML'}); }")
        expect(self.page.get_by_role("heading", name="Instance credentials")).to_be_visible()
        expect(self.page.locator(MOUNTED_ISLANDS)).to_have_count(1)
        self.assertEqual(self.errors, [])

    def test_theme_and_responsive_parity(self):
        """Cover the six theme states and phone/tablet/desktop without overflow."""
        for theme, scheme in (("system", "light"), ("system", "dark"), ("light", "dark"),
                              ("dark", "light"), ("nord", "light"), ("custom", "light")):
            self.user.theme = theme
            self.user.save(update_fields=["theme"])
            self.page.emulate_media(color_scheme=scheme, reduced_motion="reduce")
            for width in (390, 768, 1440):
                with self.subTest(theme=theme, scheme=scheme, width=width):
                    self.page.set_viewport_size({"width": width, "height": 1000})
                    for tab in ("overview", "connection", "import", "activity"):
                        self.open_cards(tab)
                        expect(self.page.locator('[data-provider-root] [data-testid="settings-card"]')).to_have_count(3 if tab == "overview" else 2)
                        self.assertFalse(self.page.evaluate("document.documentElement.scrollWidth > innerWidth + 1"))
        self.assertEqual(self.errors, [])

    def test_blocked_bundle_preserves_fallback_and_import_controls(self):
        """A failed lazy chunk must leave useful SSR content and working forms."""
        self.block_renderer = True
        self.page.goto(f"{self.live_server_url}/settings/providers/anilist?tab=import")
        expect(self.page.get_by_role("heading", name="Import preferences")).to_be_visible()
        expect(self.page.get_by_label("Public username", exact=True)).to_be_visible()
        expect(self.page.locator(MOUNTED_ISLANDS)).to_have_count(0)
        self.assertEqual(self.errors, [])

    def test_navigation_from_a_page_without_cards_lazily_loads_the_runtime(self):
        """The global loader must discover islands introduced by boosted navigation."""
        self.page.goto(f"{self.live_server_url}/settings/account")
        self.page.wait_for_load_state("networkidle")
        self.assertFalse(self.page.evaluate("""performance.getEntriesByType('resource')
          .some(entry => /\\/ui\\/mount-/.test(entry.name))"""))
        # An HTMX body replacement can orphan the thenable returned by ajax.
        # Fire the request without awaiting a promise tied to the detached DOM.
        self.page.evaluate("() => { void htmx.ajax('GET', '/settings/providers/anilist?tab=import', {target:'body', swap:'innerHTML'}); }")
        expect(self.page.locator(MOUNTED_ISLANDS)).to_have_count(1)
        self.assertEqual(self.errors, [])

    def test_runtime_cost_layout_shift_and_cleanup_memory(self):
        """Bound the first pilot's desktop execution/layout and retained memory."""
        self.page.add_init_script("""(() => {
          window.cardProof = {cls: 0, mountedAt: 0};
          new PerformanceObserver(list => {
            for (const entry of list.getEntries()) {
              if (!entry.hadRecentInput) cardProof.cls += entry.value;
            }
          }).observe({type: 'layout-shift', buffered: true});
          new MutationObserver(() => {
            if (document.querySelectorAll('[data-react-mounted=true]:not([data-shell-root]):not([data-global-search-root]):not([data-sidebar-root]):not([data-modal-host-root]):not([data-bulk-actions-root]):not([data-settings-view-root])').length === 1) {
              cardProof.mountedAt ||= performance.now();
            }
          }).observe(document, {subtree:true, attributes:true, attributeFilter:['data-react-mounted']});
        })()""")
        self.open_cards()
        self.page.wait_for_load_state("networkidle")
        metrics = self.page.evaluate("""() => ({
          cls: cardProof.cls,
          mountAfterFetchMs: cardProof.mountedAt - performance.getEntriesByType('resource')
            .find(entry => /\\/ui\\/mount-/.test(entry.name)).responseEnd,
          version: navigator.userAgent,
        })""")
        session = self.context.new_cdp_session(self.page)
        session.send("HeapProfiler.collectGarbage")
        before = session.send("Runtime.getHeapUsage")["usedSize"]
        for _ in range(40):
            self.page.evaluate("""() => {
              window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted:false}));
              window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted:false}));
            }""")
            expect(self.page.locator(MOUNTED_ISLANDS)).to_have_count(1)
        session.send("HeapProfiler.collectGarbage")
        metrics["heapGrowthBytes"] = session.send("Runtime.getHeapUsage")["usedSize"] - before
        self.__class__.proof_metrics = metrics
        self.assertLess(metrics["cls"], 0.02)
        self.assertLess(metrics["mountAfterFetchMs"], 1000)  # CI smoke guard; desktop acceptance is measured separately.
        self.assertLess(metrics["heapGrowthBytes"], 5 * 1024 * 1024)
        self.assertEqual(self.errors, [])

    def test_overview_shows_three_cards_using_real_state(self):
        """Credential saves, enabled schedules and the latest run feed Overview."""
        self.open_cards("overview")
        expect(self.page.locator('[data-testid="provider-overview"] [data-testid="settings-card"]')).to_have_count(3)
        expect(self.page.get_by_text("No credentials active", exact=True)).to_be_visible()
        expect(self.page.get_by_text("No automatic imports configured", exact=True)).to_be_visible()
        credentials.set_user("anilist", self.user, {"client_id": "browser-client", "client_secret": "browser-secret"})
        cron = CrontabSchedule.objects.create(minute="0", hour="3")
        PeriodicTask.objects.create(name="browser-auto", task="Import from AniList", crontab=cron,
                                   kwargs=json.dumps({"user_id": self.user.pk, "mode": "new"}))
        ImportRun.objects.create(user=self.user, source="anilist", status="completed")
        self.open_cards("overview")
        expect(self.page.get_by_text("Personal credentials active", exact=True)).to_be_visible()
        expect(self.page.get_by_text("1 enabled schedule", exact=True)).to_be_visible()
        expect(self.page.get_by_text("Completed", exact=True)).to_be_visible()
        self.assertEqual(self.errors, [])

    def test_activity_details_pause_updates_and_schedule_remove_returns_here(self):
        """Keep diagnostics open while the server changes and preserve action routing."""
        TaskResult.objects.create(task_id="browser-failed", task_name="Import from AniList", status="FAILURE",
                                  task_kwargs=json.dumps({"user_id": self.user.pk}), result="{}", traceback="AniList test traceback")
        ImportRun.objects.create(user=self.user, source="anilist", status="running")
        self.open_cards("activity")
        self.page.get_by_text("Show traceback", exact=True).click()
        expect(self.page.get_by_text("AniList test traceback", exact=True)).to_be_visible()
        # An open details element prevents another request even when a retry signal
        # or active interval would otherwise refresh the list.
        requests = []
        self.page.on("request", lambda request: requests.append(request.url) if "format=json" in request.url else None)
        self.page.wait_for_timeout(5100)
        self.assertEqual(requests, [])
        cron = CrontabSchedule.objects.create(minute="0", hour="3")
        PeriodicTask.objects.create(name="browser-remove", task="Import from AniList", crontab=cron,
                                   kwargs=json.dumps({"user_id": self.user.pk, "mode": "new"}))
        # The dialog has both header dismiss and footer Close actions.
        # Choose the footer to avoid a strict-mode selector ambiguity.
        self.page.get_by_role("dialog").get_by_role("button", name="Close", exact=True).last.click()
        self.page.get_by_role("button", name="Remove schedule", exact=True).wait_for(state="visible")
        self.page.get_by_role("button", name="Remove schedule", exact=True).click()
        self.page.wait_for_url("**/settings/providers/anilist?tab=activity")
        expect(self.page.get_by_text("No periodic imports configured", exact=True)).to_be_visible()
        self.assertFalse(PeriodicTask.objects.filter(name="browser-remove").exists())
        self.assertEqual(self.errors, [])

    def test_retained_provider_still_uses_shared_header_islands(self):
        """The full-page port does not orphan partial migrations on other providers."""
        self.page.goto(f"{self.live_server_url}/settings/providers/hardcover?tab=connection")
        expect(self.page.locator("[data-provider-credentials-root][data-react-mounted=true]")).to_have_count(1)
        expect(self.page.locator("[data-provider-root][data-react-mounted=true]")).to_have_count(1)
        expect(self.page.get_by_role("navigation", name="Provider sections")).to_be_visible()
        expect(self.page.get_by_text("Your key may avoid shared instance or bundled rate limits.", exact=True)).to_be_visible()
        self.open_cards("connection")
        expect(self.page.get_by_text("Your key may avoid shared instance or bundled rate limits.", exact=True)).to_be_visible()
        self.assertEqual(self.errors, [])

    def test_all_provider_overviews_share_the_react_page_and_card_components(self):
        """Different capabilities change data, never the provider page renderer."""
        for slug, titles in (("bgg", ["Connection", "Metadata"]),
                             ("anilist", ["Connection", "Automatic import", "Last active import"]),
                             ("goodreads", ["Automatic import", "Last active import"])):
            self.page.goto(f"{self.live_server_url}/settings/providers/{slug}?tab=overview")
            expect(self.page.locator("[data-provider-root][data-react-mounted=true]")).to_have_count(1)
            cards = self.page.locator('[data-testid="provider-overview"] [data-testid="settings-card"]')
            self.assertEqual(cards.locator('[role="heading"]').all_text_contents(), titles)
            self.assertFalse(self.page.evaluate("document.documentElement.scrollWidth > innerWidth + 1"))
            self.assertEqual(self.page.locator('[data-testid="provider-page"]').count(), 1)
        self.page.goto(f"{self.live_server_url}/settings/providers/bgg?tab=overview")
        expect(self.page.locator("[data-provider-root][data-react-mounted=true]")).to_have_count(1)
        self.page.locator('[data-testid="provider-overview"]').get_by_role("link", name="Connection", exact=True).click()
        expect(self.page.get_by_role("heading", name="Instance credentials", exact=True)).to_be_visible()
        expect(self.page.locator("[data-provider-root][data-react-mounted=true]")).to_have_count(1)
        expect(self.page.locator("[data-provider-credentials-root][data-react-mounted=true]")).to_have_count(1)
        self.assertEqual(self.errors, [])

    def test_overview_and_directory_share_column_breakpoints(self):
        """Both native catalog surfaces honor the same measured-width breakpoints."""
        for width in (390, 1135, 1280, 1440):
            self.page.set_viewport_size({"width": width, "height": 1000})
            for path in ("/settings/providers", "/settings/providers/anilist?tab=overview"):
                self.page.goto(self.live_server_url + path)
                expect(self.page.locator(MOUNTED_ISLANDS)).to_have_count(1)
                self.page.wait_for_function("""() => {
                  const grid = document.querySelector('[data-testid="card-grid"]');
                  const content = grid?.firstElementChild;
                  if (!content) return false;
                  const boxes = [...content.children].map(item => item.getBoundingClientRect());
                  if (!boxes.length) return false;
                  const expected = 1 + [384, 608, 896].filter(bp => content.getBoundingClientRect().width >= bp).length;
                  return boxes.filter(box => Math.abs(box.top - boxes[0].top) <= 2).length === Math.min(boxes.length, expected);
                }""")
                self.assertFalse(self.page.evaluate("document.documentElement.scrollWidth > innerWidth + 1"))
        self.assertEqual(self.errors, [])

    def test_audiobookshelf_metadata_explains_imports_and_library_sources(self):
        self.user.book_metadata_source_default = "openlibrary"
        self.user.save(update_fields=["book_metadata_source_default"])
        credentials.set_user("hardcover", self.user, {"api_key": "browser-metadata-key"})
        self.page.goto(f"{self.live_server_url}/settings/providers/audiobookshelf?tab=metadata")
        expect(self.page.locator('[data-provider-root][data-react-mounted="true"]')).to_have_count(1)
        expect(self.page.get_by_text("Metadata for imported items", exact=True)).to_be_visible()
        expect(self.page.get_by_role("heading", name="Library sources")).to_be_visible()
        rows = self.page.locator('[data-testid="metadata-source-row"]')
        expect(rows).to_have_count(2)
        expect(rows.nth(0)).to_contain_text("Books")
        expect(rows.nth(0)).to_contain_text("Open Library")
        expect(rows.nth(1)).to_contain_text("Podcasts")
        expect(rows.nth(1)).to_contain_text("Pocket Casts")
        self.page.get_by_role("button", name="Configure Podcasts", exact=True).click()
        expect(self.page.get_by_text("Default for new titles: Pocket Casts")).to_be_visible()
        expect(self.page.get_by_text("Imported items", exact=True)).to_be_visible()
        expect(self.page.get_by_role("img", name="Search results: From Open Library, Pocket Casts")).to_be_visible()
        for width in (320, 390, 768, 1440):
            self.page.set_viewport_size({"width": width, "height": 1000})
            geometry = self.page.locator('[data-testid="provider-metadata"]').evaluate("""root => {
              const bounds = root.getBoundingClientRect();
              const cards = [...root.querySelectorAll('[data-testid="settings-card"]')]
                .map(card => card.getBoundingClientRect());
              const sources = [...root.querySelectorAll('[data-testid="metadata-source-row"]')]
                .map(row => row.getBoundingClientRect());
              const feature = root.querySelector('[data-testid="feature-row"]');
              const icon = feature?.querySelector('[data-testid="status-icon"]')?.getBoundingClientRect();
              const row = feature?.getBoundingClientRect();
              return {
                cardsWithin: cards.every(card => card.left >= bounds.left - 1 && card.right <= bounds.right + 1),
                sourcesWithin: sources.every(source => source.left >= bounds.left - 1 && source.right <= bounds.right + 1),
                belowCards: sources.length && cards.length && sources[0].top >= Math.max(...cards.map(card => card.bottom)) - 1,
                iconWithin: Boolean(icon && row && icon.right <= row.right + 1),
              };
            }""")
            self.assertTrue(all(geometry.values()), (width, geometry))
        self.page.get_by_role("button", name="Configure Books", exact=True).click()
        self.choose_option("Provider for new titles", "Hardcover")
        self.page.get_by_role("button", name="Save", exact=True).click()
        self.user.refresh_from_db()
        self.assertEqual(self.user.book_metadata_source_default, "hardcover")
        for width in (390, 768, 1440):
            self.page.set_viewport_size({"width": width, "height": 1000})
            self.assertFalse(self.page.evaluate("document.documentElement.scrollWidth > innerWidth + 1"))
        self.assertEqual(self.errors, [])

    def test_metadata_is_inline_and_saves_shared_primary_source(self):
        credentials.set_user("hardcover", self.user, {"api_key": "browser-metadata-key"})
        self.page.goto(f"{self.live_server_url}/settings/providers/hardcover?tab=metadata")
        expect(self.page.locator('[data-provider-root][data-react-mounted="true"]')).to_have_count(1)
        expect(self.page.get_by_role("heading", name="Metadata sources", exact=True)).to_be_visible()
        expect(self.page.get_by_role("heading", name="Provider settings", exact=True)).to_be_visible()
        expect(self.page.get_by_role("dialog")).to_have_count(0)
        self.page.get_by_role("button", name="Configure Books", exact=True).click()
        self.choose_option("Provider for new titles", "Hardcover")
        self.page.get_by_role("button", name="Save", exact=True).click()
        self.page.wait_for_url("**/settings/providers/hardcover?tab=metadata")
        self.user.refresh_from_db()
        self.assertEqual(self.user.book_metadata_source_default, "hardcover")
        self.page.goto(f"{self.live_server_url}/settings/providers/openlibrary?tab=metadata")
        expect(self.page.locator('[data-provider-root][data-react-mounted="true"]')).to_have_count(1)
        self.page.get_by_role("button", name="Configure Books", exact=True).click()
        expect(self.select_trigger("Provider for new titles")).to_contain_text("Hardcover")
        expect(self.page.get_by_role("img", name="Search results: From Hardcover", exact=True)).to_be_visible()
        for width in (390, 768, 1440):
            self.page.set_viewport_size({"width": width, "height": 1000})
            self.assertFalse(self.page.evaluate("document.documentElement.scrollWidth > innerWidth + 1"))
        self.assertEqual(self.errors, [])

    def test_metadata_feature_list_prioritizes_activity_and_provider_outputs(self):
        for slug in ("bgg", "igdb", "steamgriddb", "libretro"):
            self.page.goto(f"{self.live_server_url}/settings/providers/{slug}?tab=metadata")
            expect(self.page.locator('[data-provider-root][data-react-mounted="true"]')).to_have_count(1)
            expect(self.page.locator('[data-testid="feature-list"] [data-testid="feature-row"]').first).to_be_visible()
            feature_list = self.page.locator('[data-testid="provider-metadata"] [data-testid="feature-list"]').first
            expect(feature_list.get_by_text("Active", exact=True)).to_have_count(0)
            self.assertTrue(feature_list.evaluate("element => element.nextElementSibling === null"))
            self.assertTrue(feature_list.locator('[data-testid="feature-row"]').evaluate_all("""rows => rows.every(row => {
              const icon = row.querySelector('[data-testid="status-icon"]'), text = row.querySelector('[data-testid="feature-copy"]');
              return icon.getBoundingClientRect().right <= text.getBoundingClientRect().left &&
                getComputedStyle(row).borderTopWidth === '0px';
            })"""))
            expect(self.page.get_by_role("heading", name="No additional provider settings", exact=True)).to_have_count(0)
            expect(self.page.get_by_role("link", name="Configure Connection", exact=True)).to_have_count(0)
            expect(self.page.get_by_text("This provider has no additional settings.", exact=True)).to_be_visible()
            expect(self.page.get_by_text("Read-only", exact=True)).to_have_count(0)
            for width in (390, 768, 1440):
                self.page.set_viewport_size({"width": width, "height": 1000})
                self.assertFalse(self.page.evaluate("document.documentElement.scrollWidth > innerWidth + 1"))
        expect(self.page.get_by_role("img", name="Title logos: Active", exact=True)).to_be_visible()
        expect(self.page.get_by_role("img", name="Search results: Not provided", exact=True)).to_be_visible()
        expect(self.page.get_by_role("img", name="Title details: Not provided", exact=True)).to_be_visible()
        games = self.page.locator('[data-testid="metadata-source-row"]').filter(has=self.page.get_by_role("heading", name="Games", exact=True))
        expect(games).to_contain_text("Internet Game Database")
        self.assertEqual(self.errors, [])

    @override_settings(STEAM_API_KEY="")
    def test_steam_explains_public_artwork_without_a_connection(self):
        self.page.goto(f"{self.live_server_url}/settings/providers/steam?tab=overview")
        expect(self.page.locator('[data-provider-root][data-react-mounted="true"]')).to_have_count(1)
        connection = self.page.locator('[data-testid="provider-overview"]').get_by_role("link", name="Connection", exact=True)
        expect(connection).to_contain_text("Active")
        expect(connection).to_contain_text("Public API active")
        metadata = self.page.locator('[data-testid="provider-overview"]').get_by_role("link", name="Metadata", exact=True)
        expect(metadata).to_contain_text("Supplemental metadata active")
        connection.click()
        card = self.page.locator('[data-testid="settings-card"]').filter(has=self.page.get_by_role("heading", name="Instance credentials"))
        expect(card.get_by_text("Public API", exact=True)).to_be_visible()
        expect(self.page.get_by_text("Game artwork uses a public API and does not require an API key.")).to_have_count(0)
        link = self.page.get_by_role("link", name="Get a key from Steam", exact=True)
        link.hover()
        self.assertEqual(link.evaluate("element => getComputedStyle(element).textDecorationLine"), "none")
        expect(self.page.get_by_text("Your key may avoid shared instance or bundled rate limits.", exact=True)).to_be_visible()
        gap = self.page.get_by_text("Your key may avoid shared instance or bundled rate limits.", exact=True).evaluate("""element => {
          const field = element.parentElement.querySelector('[id$="-label"]');
          return field.getBoundingClientRect().top - element.getBoundingClientRect().bottom;
        }""")
        self.assertGreaterEqual(gap, 10)
        self.page.get_by_role("navigation", name="Provider sections").get_by_role("tab", name="Metadata", exact=True).click()
        expect(self.page.get_by_text("Public artwork needs no API key", exact=True)).to_have_count(0)
        expect(self.page.get_by_role("img", name="Title logos: Active", exact=True)).to_be_visible()
        expect(self.page.get_by_role("img", name="Background images: Active", exact=True)).to_be_visible()
        expect(self.page.get_by_role("img", name="Search results: Not provided", exact=True)).to_be_visible()
        expect(self.page.get_by_role("img", name="Title details: Not provided", exact=True)).to_be_visible()
        self.assertEqual(self.errors, [])

    @override_settings(STEAM_API_KEY="")
    def test_public_access_disclosure_survives_a_blocked_react_bundle(self):
        self.block_renderer = True
        self.page.goto(f"{self.live_server_url}/settings/providers/steam?tab=connection")
        expect(self.page.get_by_text("Public API", exact=True)).to_be_visible()
        expect(self.page.get_by_text("Game artwork uses a public API and does not require an API key.", exact=True)).to_have_count(0)
        self.assertEqual(self.errors, [])

    def test_status_chips_stay_at_the_top_right_on_all_shared_cards(self):
        for width in (320, 390, 768, 1280):
            self.page.set_viewport_size({"width": width, "height": 1000})
            for path in ("/settings/providers/anilist?tab=connection", "/settings/providers/anilist?tab=import",
                         "/settings/providers/steam?tab=connection", "/settings/providers/steam?tab=metadata"):
                self.page.goto(self.live_server_url + path)
                expect(self.page.locator('[data-provider-root][data-react-mounted="true"]')).to_have_count(1)
                self.page.wait_for_load_state("networkidle")
                bad = self.page.locator('[data-testid="settings-card"] [data-testid="settings-card-heading"]').evaluate_all("""headers => headers.flatMap(header => {
                  const badge = header.querySelector('[data-testid=settings-card-badge], .settings-chip');
                  if (!badge) return [];
                  const row = header.getBoundingClientRect(), chip = badge.getBoundingClientRect();
                  return chip.top > row.top + 1 || chip.right < row.right - 1 || chip.right > row.right + 1 ? [badge.textContent] : [];
                })""")
                self.assertEqual(bad, [], (width, path, bad))
        self.assertEqual(self.errors, [])

    @patch("integrations.views.tasks.import_anilist.delay")
    def test_public_import_submits_current_react_preferences(self, import_task):
        """The React form retains the established task arguments and return route."""
        self.open_cards()
        self.page.get_by_label("Public username", exact=True).fill("react-public-profile")
        with self.page.expect_response(lambda response: "/save-settings" in response.url):
            self.choose_option("Override mode", "Overwrite Existing")
        self.page.get_by_role("button", name="Import", exact=True).click()
        self.page.wait_for_url("**/settings/providers/anilist?tab=import")
        expect(self.page.locator("[data-provider-root][data-react-mounted=true]")).to_have_count(1)
        import_task.assert_called_once_with(user_id=self.user.pk, mode="overwrite", username="react-public-profile")
        self.assertEqual(self.errors, [])

    def test_preference_failure_is_visible_and_retry_saves_the_current_value(self):
        """A network failure cannot silently imply that a preference was saved."""
        self.open_cards()
        pattern = "**/settings/import/save-settings"
        self.page.route(pattern, lambda route: route.abort())
        self.choose_option("Override mode", "Overwrite Existing")
        expect(self.page.get_by_text("Could not save preferences. Try again.", exact=True)).to_be_visible()
        self.page.unroute(pattern)
        with self.page.expect_response(lambda response: response.url.endswith("/settings/import/save-settings")):
            self.page.get_by_role("button", name="Retry", exact=True).click()
        expect(self.page.get_by_text("Could not save preferences. Try again.", exact=True)).to_be_hidden()
        self.user.refresh_from_db()
        self.assertEqual(self.user.import_mode, "overwrite")
        self.assertEqual(self.errors, [])

    def test_sidebar_controller_registers_after_alpine_started(self):
        """A late-loaded sidebar script still registers its Alpine controller."""
        self.block_renderer = True
        self.page.goto(f"{self.live_server_url}/settings/providers/anilist?tab=overview")
        script_order = self.page.evaluate("""() => [...document.head.querySelectorAll('script[src]')]
          .filter(script => script.src.includes('savedViews.js') || script.src.includes('alpinejs-3.14.9'))
          .map(script => ({src: script.src, defer: script.defer}))""")
        self.assertEqual(len(script_order), 2)
        self.assertIn("savedViews.js", script_order[0]["src"])
        self.assertIn("alpinejs-3.14.9", script_order[1]["src"])
        self.assertTrue(all(script["defer"] for script in script_order))
        sidebar_html = self.page.locator("aside[x-data^='sidebarNavigation']").evaluate(
            "element => element.outerHTML"
        )
        page = self.context.new_page()
        self.addCleanup(page.close)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.set_content('<div x-data="{isMobileMenuOpen: false, ready: true}"><span x-text="ready"></span></div>')
        page.add_script_tag(url=f"{self.live_server_url}/static/js/libraries/alpinejs-3.14.9.min.js")
        expect(page.locator("span")).to_have_text("true")
        page.add_script_tag(url=f"{self.live_server_url}/static/js/savedViews.js")
        page.evaluate("""html => {
          document.querySelector('[x-data]').insertAdjacentHTML('beforeend', html);
        }""", sidebar_html)
        button = page.locator("aside").get_by_role("button", name="Explore", exact=True)
        expect(button).to_have_attribute("aria-expanded", "false")
        button.click()
        expect(button).to_have_attribute("aria-expanded", "true")
        self.assertEqual(errors, [])

    def test_overview_chips_stay_inside_clickable_cards_at_narrow_breakpoints(self):
        """Contain badges inside narrow grid cards and make the whole card a link."""
        for width in (390, 768, 1024, 1100, 1200, 1280, 1440):
            with self.subTest(width=width):
                self.page.set_viewport_size({"width": width, "height": 1000})
                self.open_cards("overview")
                spills = self.page.locator('[data-testid="provider-overview"] [data-testid="settings-card"]').evaluate_all("""cards => cards.flatMap(card => {
                  const bounds = card.getBoundingClientRect();
                  return [...card.querySelectorAll('[data-testid=settings-card-badge]')].filter(badge => {
                    const box = badge.getBoundingClientRect();
                    const heading = badge.closest('[data-testid=settings-card-heading]').getBoundingClientRect();
                    return box.left < bounds.left || box.right > bounds.right + 1 ||
                      box.top > heading.top + 1 || box.right < heading.right - 1;
                  }).map(badge => badge.textContent);
                })""")
                self.assertEqual(spills, [])
                expect(self.page.locator('[data-testid="provider-overview"]').get_by_role("link")).to_have_count(3)
                expect(self.page.get_by_text("Manage connection", exact=True)).to_have_count(0)
                expect(self.page.get_by_text("Configure imports", exact=True)).to_have_count(0)
                expect(self.page.get_by_text("View activity", exact=True)).to_have_count(0)
        self.page.locator('[data-testid="provider-overview"]').get_by_role("link", name="Connection", exact=True).click()
        self.page.wait_for_url("**/settings/providers/anilist?tab=connection")
        self.open_cards("overview")
        self.page.locator('[data-testid="provider-overview"]').get_by_role("link", name="Automatic import", exact=True).focus()
        self.page.keyboard.press("Enter")
        self.page.wait_for_url("**/settings/providers/anilist?tab=import")
        self.open_cards("overview")
        self.page.locator('[data-testid="provider-overview"]').get_by_role("link", name="Last active import", exact=True).click()
        self.page.wait_for_url("**/settings/providers/anilist?tab=activity")
        self.assertEqual(self.errors, [])

    def test_connection_summary_is_compact_and_hover_has_no_white_outline_flash(self):
        """Keep explanatory copy in a tooltip and sample the first painted hover frames."""
        self.page.emulate_media(color_scheme="dark")
        self.open_cards("overview")
        card = self.page.locator('[data-testid="provider-overview"]').get_by_role("link", name="Connection", exact=True)
        expect(card.locator('[data-testid="settings-card-heading"] + *')).to_have_count(1)
        expect(card).to_have_attribute("title", "Public username imports do not require provider credentials.")
        self.page.mouse.move(0, 0)
        card.evaluate("""card => {
          window.providerHoverFrames = [];
          window.providerHoverRecording = true;
          let count = 0;
          function sample() {
            const style = getComputedStyle(card);
            if (card.matches(':hover')) {
              // Chromium can report a default 3px outline width even when
              // the outline is not painted. Compare visible outlines only.
              const visible = style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) > 0;
              providerHoverFrames.push(visible
                ? {color: style.outlineColor, width: style.outlineWidth, style: style.outlineStyle}
                : {color: null, width: '0px', style: 'none'});
            }
            if (providerHoverRecording && ++count < 120) requestAnimationFrame(sample);
          }
          requestAnimationFrame(sample);
        }""")
        card.hover()
        self.page.wait_for_timeout(400)
        frames = self.page.evaluate("providerHoverRecording=false; providerHoverFrames")
        self.assertTrue(frames)
        self.assertTrue(all(frame == frames[-1] for frame in frames), frames)
        # The shared React card draws its own rounded border. Hover should
        # not add an outer outline; keyboard focus has a separate focus ring.
        self.assertEqual(frames[-1], {"color": None, "width": "0px", "style": "none"})
        self.open_cards("connection")
        for control in (
            self.page.get_by_role("button", name="Save personal key", exact=True),
            self.page.get_by_role("button", name="Read-only: explain instance credential access"),
            self.page.get_by_role("link", name="Get a key from AniList", exact=True),
        ):
            control.hover()
            properties = control.evaluate("element => getComputedStyle(element).transitionProperty.split(',').map(value => value.trim())")
            self.assertNotIn("all", properties)
            self.assertFalse(any(value.startswith("outline") for value in properties))
        self.assertEqual(self.errors, [])

    def test_overview_subtitles_and_interactive_chips_are_consistent(self):
        """Match status typography and shared link/button chip interactions."""
        self.open_cards("overview")
        subtitles = self.page.locator('[data-testid="provider-overview"] [data-testid="settings-card"]').evaluate_all("""cards => cards.map(card => {
          const p = card.querySelector('[data-testid="settings-card-heading"]').nextElementSibling, style = getComputedStyle(p);
          return {text:p.textContent.trim(), color:style.color, weight:style.fontWeight, size:style.fontSize};
        })""")
        self.assertTrue(all(not row["text"].endswith(".") for row in subtitles))
        self.assertTrue(all((row["color"], row["weight"], row["size"]) ==
                            (subtitles[0]["color"], subtitles[0]["weight"], subtitles[0]["size"]) for row in subtitles))
        self.open_cards("connection")
        read_only = self.page.get_by_role("button", name="Read-only: explain instance credential access")
        key_link = self.page.get_by_role("link", name="Get a key from AniList", exact=True)
        dimensions = []
        for chip in (read_only, key_link):
            self.page.mouse.move(0, 0)
            self.page.wait_for_timeout(300)
            before = chip.evaluate("element => getComputedStyle(element).backgroundColor")
            chip.hover()
            self.page.wait_for_timeout(300)
            style = chip.evaluate("""element => {
              const s=getComputedStyle(element), text=getComputedStyle(element.firstElementChild);
              return {background:s.backgroundColor, underline:s.textDecorationLine, radius:s.borderRadius,
                padding:s.padding, border:s.borderWidth, font:text.fontSize};
            }""")
            self.assertNotEqual(style["background"], before)
            self.assertEqual(style["underline"], "none")
            dimensions.append((style["radius"], style["padding"], style["border"], style["font"]))
        self.assertEqual(dimensions[0], dimensions[1])
        expect(key_link).to_have_attribute("href", "https://anilist.co/settings/developer")
        expect(key_link).to_have_attribute("target", "_blank")
        with self.page.expect_popup() as popup:
            key_link.click()
        popup.value.close()
        for name in ("Overview", "Connection", "Import", "Activity"):
            tab = self.page.get_by_role("navigation", name="Provider sections").get_by_role("tab", name=name, exact=True)
            tab.hover()
            self.assertEqual(tab.evaluate("element => getComputedStyle(element).textDecorationLine"), "none")
        active = self.page.get_by_role("tab", name="Activity", exact=True)
        self.assertEqual(active.evaluate("element => getComputedStyle(element).borderBottomWidth"), "2px")
        self.assertEqual(self.errors, [])
