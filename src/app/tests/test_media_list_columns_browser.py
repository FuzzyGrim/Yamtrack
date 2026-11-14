"""Browser regression coverage for column changes on filtered media lists."""

from django.contrib.auth import get_user_model
from django.test import tag
from playwright.sync_api import expect, sync_playwright

from app.models import Game, Item, MediaTypes, Sources, Status
from app.tests.live_server import SerialStaticLiveServerTestCase


@tag("slow", "playwright")
class MediaListColumnsBrowserTests(SerialStaticLiveServerTestCase):
    """Exercise the actual HTMX swap, including artwork preparation."""

    def test_layout_switch_keeps_updated_status(self):
        user = get_user_model().objects.create_user(
            username="layout-browser", password="12345"
        )
        for title, status in (
            ("Playing", Status.IN_PROGRESS.value),
            ("Finished", Status.COMPLETED.value),
        ):
            item = Item.objects.create(
                media_id=title,
                source=Sources.MANUAL.value,
                media_type=MediaTypes.GAME.value,
                title=title,
            )
            Game.objects.create(item=item, user=user, status=status)

        with sync_playwright() as playwright, playwright.chromium.launch() as browser:
            page = browser.new_page(viewport={"width": 1600, "height": 1000})
            page.goto(self.live_server_url)
            page.get_by_placeholder("Enter your username").fill("layout-browser")
            page.get_by_placeholder("Enter your password").fill("12345")
            page.get_by_role("button", name="Sign in").click()
            page.goto(f"{self.live_server_url}/medialist/game?status=In%20progress")
            page.get_by_role("button", name="Status: In Progress", exact=True).click()
            page.get_by_text("Clear status filter", exact=True).click()
            expect(page.get_by_text("Finished", exact=True)).to_be_visible()
            page.get_by_role("button", name="View settings").first.click()
            page.get_by_role("menuitem", name="Grid View").first.click()  # names the current view; it switches to the table
            table = page.get_by_role("table")
            expect(table).to_contain_text("Finished")
            expect(table).to_contain_text("Playing")
            expect(page.locator("[data-media-table-root][data-react-mounted]")).to_have_count(0)
            expect(page.locator('[data-testid="media-table"]')).to_have_count(0)
            self.assertIn("status=all", page.url)

    def test_toggle_notes_preserves_filtered_table(self):
        user = get_user_model().objects.create_user(
            username="columns-browser", password="12345"
        )
        for title, status in (
            ("Pac-Man", Status.PAUSED.value),
            ("Galaga", Status.PAUSED.value),
            ("Donkey Kong", Status.COMPLETED.value),
        ):
            item = Item.objects.create(
                media_id=title,
                source=Sources.MANUAL.value,
                media_type=MediaTypes.GAME.value,
                title=title,
            )
            Game.objects.create(item=item, user=user, status=status, notes="A note")

        with (
            sync_playwright() as playwright,
            playwright.chromium.launch() as browser,
        ):
            page = browser.new_page(viewport={"width": 1600, "height": 1000})
            page.goto(self.live_server_url)
            page.get_by_placeholder("Enter your username").fill("columns-browser")
            page.get_by_placeholder("Enter your password").fill("12345")
            page.get_by_role("button", name="Sign in").click()
            page.goto(
                f"{self.live_server_url}/medialist/game?layout=table&status=Paused"
            )
            table = page.get_by_role("table")
            expect(table.get_by_role("row")).to_have_count(3)
            expect(page.locator("[data-media-table-root][data-react-mounted]")).to_have_count(0)
            expect(page.locator('[data-testid="media-table"]')).to_have_count(0)
            expect(
                table.get_by_role("columnheader", name="Notes", exact=True)
            ).to_have_count(1)
            expect(table).not_to_contain_text("Donkey Kong")

            page.get_by_role("button", name="Configure Table Columns").click()
            menu = page.get_by_role("button", name="Configure Table Columns").locator("xpath=..")
            for action, notes_count in (("Hide column", 0), ("Show column", 1)):
                notes_row = menu.get_by_text("Notes", exact=True).locator("xpath=..")
                notes_row.locator(f'button[title="{action}"]').click()
                page.wait_for_load_state("networkidle")
                expect(table.get_by_role("row")).to_have_count(3)
                expect(
                    table.get_by_role("columnheader", name="Notes", exact=True)
                ).to_have_count(notes_count)
                expect(table).to_contain_text("Pac-Man")
                expect(table).to_contain_text("Galaga")
                expect(table).not_to_contain_text("Donkey Kong")
                expect(table.get_by_role("img")).not_to_have_count(0)
