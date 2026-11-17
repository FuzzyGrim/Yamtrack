import copy
import os
import re
import time
from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.db import OperationalError
from django.test import RequestFactory, tag
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright

from app.discover.schemas import RowResult
from app.middleware import DatabaseRetryMiddleware
from app.models import Game, Item, MediaTypes, Movie, Sources, Status
from app.tests.live_server import SerialStaticLiveServerTestCase
from app.tests.views.test_track_modal import _tv_with_seasons_payload
from users.models import DateFormatChoices, HomeScreenRow, HomeScreenRowTypeChoices


@tag("slow", "playwright")
class IntegrationTest(SerialStaticLiveServerTestCase):
    """Integration tests for the application."""

    @classmethod
    def setUpClass(cls):
        """Set up the test class."""
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        super().setUpClass()
        cls.playwright = sync_playwright().start()
        # use headless=False, slow_mo=200 to see the browser
        cls.browser = cls.playwright.chromium.launch()
        # CI runners for this repo's full test suite (~2900 tests including
        # heavy benchmarks) get slow enough under load that the default 5s
        # expect() timeout occasionally races a real HTMX swap/navigation
        # that would otherwise succeed. Reset in tearDownClass since this is
        # process-global, not scoped to this class.
        expect.set_options(timeout=15000)

    def setUp(self):
        """Set up test data for CustomList model."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.user.date_format = DateFormatChoices.ISO_8601
        self.user.save(update_fields=["date_format"])
        show = _tv_with_seasons_payload(
            "1396",
            Sources.TMDB.value,
            title="Breaking Bad",
            episode_count=1,
        )
        episode = {
            "title": "Breaking Bad",
            "season_title": "Season 1",
            "episode_title": "Episode 1",
            "image": "https://example.com/episode.jpg",
            "cast": [],
            "crew": [],
        }
        search = {
            "page": 1,
            "total_pages": 1,
            "total_results": 1,
            "results": [show],
        }
        # Views mutate the returned dicts in place (e.g. replacing related.seasons
        # entries with enriched {"item": ..., "media": ...} wrappers), so a shared
        # return_value would corrupt later calls within the same test. Hand back a
        # fresh deep copy every call instead.
        for provider_patch in (
            patch(
                "app.providers.tmdb.search",
                side_effect=lambda *a, **k: copy.deepcopy(search),
            ),
            patch(
                "app.providers.tmdb.tv",
                side_effect=lambda *a, **k: copy.deepcopy(show),
            ),
            patch(
                "app.providers.tmdb.tv_with_seasons",
                side_effect=lambda *a, **k: copy.deepcopy(show),
            ),
            patch(
                "app.providers.tmdb.episode",
                side_effect=lambda *a, **k: copy.deepcopy(episode),
            ),
            patch("app.providers.tmdb.get_tvdb_episode_image_map", return_value={}),
            patch(
                "app.providers.tmdb.metadata_languages",
                return_value=[("", "Server Default (en)"), ("en", "English")],
            ),
            patch(
                "app.providers.tmdb.carousel_media",
                return_value={"video": None, "photos": []},
            ),
            patch(
                "app.tasks_trakt.populate_trakt_episode_ratings_for_season.delay",
            ),
        ):
            provider_patch.start()
            self.addCleanup(provider_patch.stop)

        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.page.goto(f"{self.live_server_url}/")
        self.page.get_by_placeholder("Enter your username").fill(
            self.credentials["username"],
        )
        self.page.get_by_placeholder("Enter your password").fill(
            self.credentials["password"],
        )
        self.page.get_by_role("button", name="Sign in").click()
        expect(self.page.locator("#global-search")).to_be_visible()
        # htmx is a deferred script, so the page can be visible before it runs.
        self.page.wait_for_function("() => window.htmx")
        # Home loads rows after login. Finish those reads before tests write fixtures.
        self.page.wait_for_load_state("networkidle")

    def search_and_submit(self, query):
        """Submit through the shared search control.

        The web mount replaces the legacy Alpine form with the accessible
        React search button. Wait for that mount instead of a retired form.
        """
        root = self.page.locator('[data-global-search-root][data-react-mounted="true"]')
        root.locator("#global-search").fill(query)
        root.get_by_role("button", name="Search", exact=True).click()

    def test_htmx_database_contention_retries_original_get(self):
        """A failed fragment retries its full URL without another user click."""
        requests = []

        def respond(route):
            requests.append(route.request.url)
            if len(requests) <= 2:
                route.fulfill(
                    status=503,
                    headers={"X-Floppy-Transient-DB": "contention"},
                    body="",
                )
            else:
                route.fulfill(status=200, body='<p id="contention-loaded">Loaded</p>')

        self.page.route("**/track_modal/tmdb/tv/1396?*", respond)
        self.page.evaluate(
            """() => {
                const target = document.createElement('div');
                target.id = 'contention-test-target';
                document.body.append(target);
                const button = document.createElement('button');
                button.id = 'contention-test-button';
                button.textContent = 'Open';
                button.setAttribute('hx-get', '/track_modal/tmdb/tv/1396?instance_id=7&home_row_id=recent');
                button.setAttribute('hx-target', '#contention-test-target');
                button.setAttribute('hx-trigger', 'click once');
                document.body.append(button);
                htmx.process(button);
            }"""
        )
        self.page.locator("#contention-test-button").click()
        expect(
            self.page.locator("#contention-test-target #contention-loaded")
        ).to_be_visible()
        self.assertEqual(len(requests), 3)
        for request_url in requests:
            query = parse_qs(urlparse(request_url).query)
            self.assertEqual(query["instance_id"], ["7"])
            self.assertEqual(query["home_row_id"], ["recent"])
            self.assertEqual(len(query.get("org.htmx.cache-buster", [])), 1)
            self.assertEqual(len(query.get("cache_bust", [])), 1)

    def test_full_page_database_contention_reloads_until_success(self):
        response = DatabaseRetryMiddleware(lambda _request: None).process_exception(
            RequestFactory().get("/medialist/tv?page=2"),
            OperationalError("database is locked"),
        )
        failures = 0

        def fail_twice(route):
            nonlocal failures
            failures += 1
            if failures <= 2:
                route.fulfill(
                    status=503,
                    headers={"Content-Type": "text/html"},
                    body=response.content,
                )
            else:
                route.continue_()

        self.page.route("**/medialist/tv?page=2", fail_twice)
        self.page.goto(f"{self.live_server_url}/medialist/tv?page=2")
        expect(self.page.locator("#global-search")).to_be_visible()
        self.assertEqual(failures, 3)

    def test_touch_media_card_reveals_and_executes_wrapped_actions(self):
        """A coarse-pointer card reveals, dismisses, and accepts an action tap."""
        touch_context = self.browser.new_context(
            storage_state=self.context.storage_state(),
            has_touch=True,
            is_mobile=True,
            viewport={"width": 390, "height": 844},
        )
        try:
            touch_page = touch_context.new_page()
            touch_page.goto(self.live_server_url + "/")
            search = touch_page.locator('[data-global-search-root][data-react-mounted="true"]')
            search.locator("#global-search").fill("breaking bad")
            search.get_by_role("button", name="Search", exact=True).click()
            expect(touch_page.get_by_role("heading", name="Online Results")).to_be_visible()

            card = touch_page.locator(".media-card").filter(has_text="Breaking Bad").first
            overlay = card.locator(".media-card-overlay")
            expect(card).to_be_visible()
            expect(overlay).not_to_be_visible()
            card.evaluate("element => { element.style.width = '96px'; }")

            card.locator(".media-card-poster").click()
            expect(overlay).to_be_visible()
            self.assertIn(
                "media-card-revealed",
                card.get_attribute("class") or "",
            )
            lists_action = card.get_by_role("button", name="Add to custom lists")
            expect(lists_action).to_be_visible()
            action_group = overlay.locator("div.relative.z-10.flex").first
            self.assertEqual(
                action_group.evaluate("element => getComputedStyle(element).flexWrap"),
                "wrap",
            )
            card_box = card.bounding_box()
            self.assertIsNotNone(card_box)
            for action in overlay.get_by_role("button").all():
                action_box = action.bounding_box()
                self.assertIsNotNone(action_box)
                self.assertGreaterEqual(action_box["x"], card_box["x"])
                self.assertLessEqual(
                    action_box["x"] + action_box["width"],
                    card_box["x"] + card_box["width"],
                )

            touch_page.get_by_role("heading", name="Online Results").click()
            self.assertNotIn(
                "media-card-revealed",
                card.get_attribute("class") or "",
            )

            card.locator(".media-card-poster").click()
            with touch_page.expect_request(
                lambda request: "lists_modal" in request.url,
            ):
                card.get_by_title("Add to custom lists").click()
            # The popup lives in the page's one modal host, not inside the card.
            lists_modal = touch_page.locator('[role="dialog"]:visible').last
            expect(lists_modal).to_be_visible()
        finally:
            touch_context.close()

    def test_home_poster_action_modal_is_visible_on_touch(self):
        item = Item.objects.create(
            media_id="poster-action-test",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Poster Action Test",
            image="https://example.com/poster.jpg",
        )
        Movie.objects.create(item=item, user=self.user, status=Status.IN_PROGRESS.value)
        HomeScreenRow.objects.create(
            user=self.user,
            media_type=MediaTypes.MOVIE.value,
            position=10,
            row_type=HomeScreenRowTypeChoices.LIBRARY_QUERY,
            sort_by="recent",
            direction="desc",
            filters={"status": [Status.IN_PROGRESS.value]},
        )
        touch_context = self.browser.new_context(
            storage_state=self.context.storage_state(),
            has_touch=True,
            is_mobile=True,
            viewport={"width": 390, "height": 844},
        )
        try:
            page = touch_context.new_page()
            page.goto(self.live_server_url + "/")
            card = page.locator(".media-card").first
            expect(card).to_be_visible()
            card.locator("img.media-card-poster").first.tap()
            expect(page.get_by_role("button", name="Add to tracker")).to_be_visible()
            expect(page.get_by_role("button", name="Add to custom lists")).to_be_visible()
            expect(page.get_by_role("link", name="View your activity history")).to_be_visible()
            expect(page.get_by_role("link", name="View your activity history")).to_be_visible()
        finally:
            touch_context.close()

    @patch("app.providers.services.get_media_metadata")
    def test_glass_and_custom_grid_modals_fill_viewport(self, mock_get_metadata):
        """Translucent cards must not capture their fixed modal overlays (#1579)."""
        mock_get_metadata.return_value = {
            "media_id": "1579",
            "title": "Modal Test",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "max_progress": 1,
            "details": {"release_date": "2007-01-01"},
            "related": {},
        }
        item = Item.objects.create(
            media_id="1579",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Modal Test",
        )
        Movie.objects.create(item=item, user=self.user, status=Status.COMPLETED.value)

        for theme in ("glass", "custom"):
            with self.subTest(theme=theme):
                # Finish browser reads before writing to the shared SQLite database.
                self.page.wait_for_load_state("networkidle")
                self.user.theme = theme
                self.user.save(update_fields=["theme"])
                self.page.goto(f"{self.live_server_url}/medialist/movie?layout=grid")
                card = self.page.locator(".media-card").first
                expect(card).to_be_visible()
                for action in ("Add to tracker", "Add to custom lists"):
                    card.hover()
                    card.get_by_role("button", name=action, exact=True).click()
                    self._assert_visible_overlay_fills_viewport()
                    self.page.keyboard.press("Escape")
                    expect(
                        self.page.locator("div.fixed.inset-0:visible")
                    ).to_have_count(0)

    @patch("app.providers.services.get_media_metadata")
    def test_escape_while_track_form_loads_leaves_no_dialog(self, mock_get_metadata):
        """Closing the tracker before its React form settles must not strand it.

        The Track form's dialog is portaled to document.body. When Escape
        removed the popup host mid-load, that dialog stayed on screen and
        blocked every later click, so a slow load stranded the page.
        """
        mock_get_metadata.return_value = {
            "media_id": "1579",
            "title": "Modal Test",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "max_progress": 1,
            "details": {"release_date": "2007-01-01"},
            "related": {},
        }
        item = Item.objects.create(
            media_id="1579",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Modal Test",
        )
        Movie.objects.create(item=item, user=self.user, status=Status.COMPLETED.value)

        def slow(route):
            time.sleep(0.6)
            route.continue_()

        self.user.theme = "custom"
        self.user.save(update_fields=["theme"])
        self.page.route("**/static/ui/mountTrackForm-*.js", slow)
        self.page.route("**/track_modal/**", slow)
        # The race only strands the dialog for some timings, so sweep several.
        for delay_ms in (0, 50, 100, 200, 400, 800, 1200):
            self.page.goto(f"{self.live_server_url}/medialist/movie?layout=grid")
            card = self.page.locator(".media-card").first
            expect(card).to_be_visible()
            card.hover()
            card.get_by_role("button", name="Add to tracker", exact=True).click()
            self.page.wait_for_timeout(delay_ms)
            self.page.keyboard.press("Escape")
            # The stranded dialog mounts after the delayed form arrives, so an
            # immediate count would see none; let both delayed loads settle.
            self.page.wait_for_timeout(2000)
            # A leaked dialog blocks every later click, so fail on the first one.
            self.assertEqual(
                self.page.get_by_role("dialog").count(),
                0,
                f"Tracker dialog stranded after Escape {delay_ms}ms after opening",
            )

    def test_episode_track_modal_fills_viewport_in_card_and_list_layouts(self):
        """Episode surface blur must not confine the tracker to its row."""
        self.page.set_viewport_size({"width": 1600, "height": 1000})
        for theme in ("glass", "custom"):
            with self.subTest(theme=theme):
                self.user.theme = theme
                self.user.save(update_fields=["theme"])
                self.page.goto(
                    self.live_server_url
                    + reverse(
                        "season_details",
                        kwargs={
                            "source": Sources.TMDB.value,
                            "media_id": "1396",
                            "title": "breaking-bad",
                            "season_number": 1,
                        },
                    ),
                )
                for layout in ("Card view", "List view"):
                    self.page.get_by_role("button", name=layout, exact=True).click()
                    suffix = "-list" if layout == "List view" else ""
                    track_button = self.page.locator(
                        f'button[hx-target="#track-episode-1396-1-1{suffix}"]',
                    )
                    expect(track_button).to_be_visible()
                    track_button.click()
                    # The shared Track dialog portals outside the card, so the
                    # dialog itself is the thing to watch, not the legacy root.
                    modal = self.page.get_by_role("dialog", name="Episode 1")
                    expect(modal).to_be_visible()
                    self._assert_visible_overlay_fills_viewport()
                    modal.get_by_role("button", name="Close", exact=True).click()
                    expect(modal).to_have_count(0)
                    track_button.click()
                    expect(modal).to_be_visible()
                    self.page.keyboard.press("Escape")
                    expect(modal).to_have_count(0)

    SHARED_MONTHS = (
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    )

    def shared_dialog(self):
        """Return the topmost shared dialog (the Track dialog or its picker)."""
        return self.page.get_by_role("dialog").last

    def pick_track_status(self, dialog, label):
        """Choose a status in the Track dialog's Status dropdown."""
        dialog.get_by_role("button", name=re.compile(r"^Status:")).click()
        self.page.get_by_role("radio", name=label, exact=True).click()

    def expect_track_status(self, dialog, label):
        """Assert the Track dialog's Status dropdown shows the given status."""
        expect(
            dialog.get_by_role("button", name=re.compile(rf"^Status: {label}$", re.IGNORECASE)),
        ).to_be_visible()

    def shared_date_buttons(self, dialog):
        """Return each Track date field's chevron, in field order.

        Every field has exactly one, whether or not it holds a date.
        """
        return dialog.get_by_role("button", name="Open date picker", exact=True)

    def shared_date_values(self, dialog):
        """Return every set date field as a datetime, read from its button name."""
        buttons = dialog.get_by_role("button", name=re.compile(r"^Date picker:"))
        values = []
        for index in range(buttons.count()):
            text = buttons.nth(index).get_attribute("aria-label").split(":", 1)[1]
            values.append(
                datetime.strptime(text.strip(), "%b %d, %Y, %H:%M:%S"),
            )
        return values

    def pick_shared_date(self, dialog, field_index, day_iso):
        """Pick a calendar day on one of the Track dialog's date fields.

        The shared picker commits as soon as a day is chosen and stays open, so
        close it again to get back to the Track dialog.
        """
        self.shared_date_buttons(dialog).nth(field_index).click()
        picker = self.page.get_by_role("dialog").filter(
            has=self.page.get_by_role("button", name="Close date picker"),
        )
        expect(picker).to_have_count(1)
        year, month, _day = day_iso.split("-")
        picker.get_by_role("button", name="Select month").click()
        picker.get_by_role("button", name="Select year").click()
        year_box = picker.get_by_role("textbox", name="Year")
        year_box.fill(year)
        year_box.press("Enter")
        picker.get_by_role(
            "button", name=self.SHARED_MONTHS[int(month) - 1], exact=True,
        ).click()
        picker.get_by_role("button", name=day_iso, exact=True).click()
        self.close_shared_picker()

    def shared_picker(self):
        """Return the open shared date picker panel, not the Track dialog."""
        return self.page.get_by_role("dialog").filter(
            has=self.page.get_by_role("button", name="Close date picker"),
        )

    def close_shared_picker(self):
        """Close the open shared date picker, if it is still open."""
        picker = self.page.get_by_role("dialog").filter(
            has=self.page.get_by_role("button", name="Close date picker"),
        )
        # The first close control is the full-screen backdrop; its centre sits
        # under the panel, so press a corner.
        backdrop = picker.get_by_role("button", name="Close date picker").first
        if picker.count() and backdrop.is_visible():
            backdrop.click(position={"x": 4, "y": 4})
        expect(picker).to_have_count(0)

    def set_date_input(self, locator, value):
        """Set a hidden date-picker input and dispatch its change events."""
        locator.evaluate(
            """(input, value) => {
                input.value = value;
                input.dispatchEvent(new Event("input", { bubbles: true }));
                input.dispatchEvent(new Event("change", { bubbles: true }));
            }""",
            value,
        )

    def test_home_row_load_more_guard_and_progress(self):
        """Home rows append normally and stop after a zero-progress response."""
        load_more_requests = []

        def record_request(request):
            query = parse_qs(urlparse(request.url).query)
            if "load_row" in query:
                load_more_requests.append(
                    (query["load_row"][0], query.get("offset", ["0"])[0]),
                )

        def handle_route(route):
            query = parse_qs(urlparse(route.request.url).query)
            row_id = query.get("load_row", [None])[0]
            offset = query.get("offset", ["0"])[0]
            if offset != "14" or row_id not in {"624", "625"}:
                route.continue_()
                return

            headers = {
                "Content-Type": "text/html; charset=utf-8",
                "X-Home-Row-Total": "15" if row_id == "624" else "37",
                "X-Home-Row-Loaded": "15" if row_id == "624" else "14",
            }
            route.fulfill(
                status=200,
                body=(
                    '<div class="home-row-card" data-test-card="new">new</div>'
                    if row_id == "624"
                    else ""
                ),
                headers=headers,
            )

        def install_row(row_id, loaded, total):
            self.page.evaluate(
                """
                ({rowId, loaded, total}) => {
                    const row = document.createElement('div');
                    row.id = `test-home-row-${rowId}`;
                    row.style.width = '1px';
                    row.style.height = '20px';
                    row.style.display = 'flex';
                    row.style.flexWrap = 'nowrap';
                    row.style.overflowX = 'auto';
                    row.dataset.homeRow = 'true';
                    row.dataset.loadedCount = String(loaded);
                    row.dataset.loading = 'false';
                    row.dataset.totalCount = String(total);
                    row.dataset.rowId = rowId;
                    row.dataset.stickToEnd = 'false';
                    row.setAttribute('hx-get', `/?load_row=${rowId}`);
                    row.setAttribute(
                        'hx-vals',
                        'js:{offset: Number(event.target.dataset.loadedCount || 0)}',
                    );
                    row.setAttribute('hx-trigger', 'home-row-load-more');
                    row.setAttribute('hx-target', 'this');
                    row.setAttribute('hx-swap', 'beforeend');

                    for (let index = 0; index < loaded; index += 1) {
                        const card = document.createElement('div');
                        card.className = 'home-row-card';
                        card.style.width = '100px';
                        card.style.flex = '0 0 auto';
                        card.textContent = `card-${index}`;
                        row.appendChild(card);
                    }

                    const sentinel = document.createElement('div');
                    sentinel.dataset.homeRowSentinel = 'true';
                    row.appendChild(sentinel);
                    document.body.appendChild(row);
                    htmx.process(row);
                    const realIntersectionObserver = window.IntersectionObserver;
                    window.IntersectionObserver = class {
                        observe() {}
                        disconnect() {}
                    };
                    window.initHomeRowInfiniteScroll();
                    window.IntersectionObserver = realIntersectionObserver;
                    row.scrollLeft = row.scrollWidth;
                }
                """,
                {"rowId": row_id, "loaded": loaded, "total": total},
            )

        self.page.on("request", record_request)
        self.page.route("**/*", handle_route)

        install_row("624", loaded=14, total=15)
        success_row = self.page.locator("#test-home-row-624")
        success_row.evaluate(
            """
            row => {
                row.scrollLeft = row.scrollWidth;
                row.dispatchEvent(new Event('scroll'));
                row.dispatchEvent(new Event('scroll'));
            }
            """,
        )
        expect(success_row).to_have_attribute("data-loaded-count", "15")
        self.assertEqual(
            [request for request in load_more_requests if request[0] == "624"],
            [("624", "14")],
        )

        success_row.evaluate(
            "row => row.dispatchEvent(new Event('scroll'))",
        )
        self.page.wait_for_timeout(200)
        self.assertEqual(
            [request for request in load_more_requests if request[0] == "624"],
            [("624", "14")],
        )

        install_row("625", loaded=14, total=37)
        empty_row = self.page.locator("#test-home-row-625")
        empty_row.evaluate(
            "row => row.dispatchEvent(new Event('scroll'))",
        )
        expect(empty_row).to_have_attribute("data-home-row-exhausted", "true")
        self.page.wait_for_timeout(4000)
        self.assertEqual(
            [request for request in load_more_requests if request[0] == "625"],
            [("625", "14")],
        )

    @classmethod
    def tearDownClass(cls):
        """Tear down the test class."""
        expect.set_options(timeout=5000)
        cls.browser.close()
        cls.playwright.stop()
        super().tearDownClass()

    def tearDown(self):
        """Close browser connections before Django flushes the database."""
        self.context.close()
        super().tearDown()

    def test_season_progress_edit(self):
        """Test the progress edit of a season."""
        self.search_and_submit("breaking bad")
        expect(self.page.get_by_role("heading", name="Online Results")).to_be_visible()
        self.page.get_by_title("Breaking Bad", exact=True).click()
        expect(self.page.get_by_role("main")).to_contain_text("Breaking Bad")
        season_href = self.page.locator(
            'a[href*="/season/1"]',
        ).first.get_attribute("href")
        self.page.goto(f"{self.live_server_url}{season_href}")
        expect(self.page.get_by_role("main")).to_contain_text("Breaking Bad")
        self.page.locator('button[title="Track Episode"]:visible').first.click()
        datetime_format = "%Y-%m-%d"

        # Episode 1 air date is 2008-01-20
        fixed_date = date(2008, 1, 20)
        modal = self.shared_dialog()
        self.pick_shared_date(modal, 1, fixed_date.isoformat())
        with self.page.expect_request(
            lambda request: request.method == "POST" and "/episode_save" in request.url,
        ) as save_request:
            modal.get_by_role("button", name="Add", exact=True).click()
        save_request.value.response()

        expect(self.page.get_by_role("main")).to_contain_text(
            f"Ended: {fixed_date.strftime(datetime_format)}",
        )

        with self.page.expect_response(
            lambda response: "fragment=secondary" in response.url,
        ) as secondary_response:
            self.page.reload()
        self.assertEqual(secondary_response.value.status, 200)
        expect(self.page.locator("#episodes-list")).to_be_visible()
        today = timezone.localtime().strftime(datetime_format)
        tracked_button = self.page.locator(
            "button[title='Track Episode'][hx-vals*='instance_id']:visible",
        ).first
        expect(tracked_button).to_be_visible()
        tracked_button.click()
        modal = self.shared_dialog()
        save_as_new_entry = modal.get_by_role("button", name="Save as new entry")
        expect(save_as_new_entry).to_be_visible()
        self.pick_shared_date(modal, 1, today)
        with self.page.expect_request(
            lambda request: request.method == "POST" and "/episode_save" in request.url,
        ) as save_request:
            save_as_new_entry.click()
        save_request.value.response()
        expect(self.page.get_by_role("main")).to_contain_text(f"Ended: {today}")

    def test_tv_completed(self):
        """Test the completed status of a TV show."""
        self.search_and_submit("breaking bad")
        expect(self.page.get_by_role("heading", name="Online Results")).to_be_visible()
        self.page.get_by_title("Breaking Bad", exact=True).click()
        expect(self.page.get_by_role("main")).to_contain_text("Breaking Bad")
        self.page.locator("button").filter(has_text="Add to tracker").click()
        track_form = self.page.locator("#track-tv-1396")
        expect(track_form).to_contain_text("Score")
        self.pick_track_status(self.shared_dialog(), "Completed")
        with self.page.expect_request(
            lambda request: request.method == "POST" and "/media_save" in request.url,
        ) as save_request:
            self.page.get_by_role("dialog").last.get_by_role(
                "button", name="Add", exact=True,
            ).click()
        save_request.value.response()
        # List cards no longer print their status; the Completed filter shows it.
        self.page.goto(f"{self.live_server_url}/medialist/tv?status=Completed")
        expect(
            self.page.get_by_role("button", name="Status: Completed", exact=True),
        ).to_be_visible()
        expect(self.page.get_by_role("main")).to_contain_text("Breaking Bad")

    def test_season_completed(self):
        """Test the completed status of a season."""
        self.search_and_submit("breaking bad")
        expect(self.page.get_by_role("heading", name="Online Results")).to_be_visible()
        self.page.get_by_title("Breaking Bad", exact=True).click()
        expect(self.page.get_by_role("main")).to_contain_text("Breaking Bad")
        season_href = self.page.locator(
            'a[href*="/season/1"]',
        ).first.get_attribute("href")
        self.page.goto(f"{self.live_server_url}{season_href}")
        expect(self.page.get_by_role("main")).to_contain_text("Breaking Bad")
        self.page.get_by_role("button", name="Add to tracker").click()
        track_form = self.page.locator("#track-season-1396-1")
        expect(track_form).to_contain_text("Score")
        self.pick_track_status(self.shared_dialog(), "Completed")
        with self.page.expect_request(
            lambda request: request.method == "POST" and "/media_save" in request.url,
        ) as save_request:
            self.page.get_by_role("dialog").last.get_by_role(
                "button", name="Add", exact=True,
            ).click()
        save_request.value.response()
        # List cards no longer print their status; the Completed filter shows it.
        self.page.goto(f"{self.live_server_url}/medialist/season?status=Completed")
        expect(
            self.page.get_by_role("button", name="Status: Completed", exact=True),
        ).to_be_visible()
        expect(self.page.get_by_role("main")).to_contain_text("Breaking Bad")

    def test_tv_manual(self):
        """Test the manual creation of a TV show."""
        # Custom now lives under the collapsible Explore section.
        self.page.get_by_role("button", name="Explore").click()
        self.page.get_by_role("link", name="Custom", exact=True).click()
        self.page.get_by_placeholder("Enter title").click()
        self.page.get_by_placeholder("Enter title").fill("Friends")
        self.page.get_by_placeholder("Enter image URL").click()
        self.page.get_by_placeholder("Enter image URL").fill(
            "https://media.themoviedb.org/t/p/w300_and_h450_bestv2/2koX1xLkpTQM4IZebYvKysFW1Nh.jpg",
        )
        self.page.locator('select[name="status"]').select_option("In progress")
        self.page.get_by_role("button", name="Create Entry").click()
        expect(
            self.page.get_by_text("Friends added successfully.", exact=True)
        ).to_be_visible()

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
            "Friends S01E01 added successfully.",
        )

        # Check visibility
        self.page.goto(f"{self.live_server_url}/medialist/tv")
        expect(self.page.get_by_role("main")).to_contain_text("Friends")
        self.page.goto(f"{self.live_server_url}/medialist/season")
        expect(self.page.get_by_role("main")).to_contain_text("Season 1")
        self.page.goto(f"{self.live_server_url}/medialist/tv")
        friends_href = self.page.get_by_role(
            "link", name="Friends", exact=True
        ).first.get_attribute("href")
        self.page.goto(f"{self.live_server_url}{friends_href}")
        expect(self.page.get_by_role("main")).to_contain_text("Friends")
        season_href = self.page.locator(
            'a[href*="/season/1"]',
        ).first.get_attribute("href")
        self.page.goto(f"{self.live_server_url}{season_href}")
        expect(self.page.get_by_role("main")).to_contain_text("Friends")
        expect(self.page.get_by_role("main")).to_contain_text("Episode 1")

    @patch("app.providers.services.get_media_metadata")
    def test_movie_split_track_modal_close_button_and_release_date(
        self,
        mock_get_metadata,
    ):
        """Date picker shortcuts support release dates and clearing both dates."""
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "max_progress": 1,
            "score": 7.6,
            "score_count": 42000,
            "details": {
                "release_date": "2019-11-08",
            },
            "related": {},
        }
        item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
            runtime_minutes=95,
        )
        Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=1,
            start_date=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
            end_date=datetime(2026, 3, 1, 14, 0, tzinfo=UTC),
        )
        Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=1,
            start_date=datetime(2026, 3, 12, 12, 0, tzinfo=UTC),
            end_date=datetime(2026, 3, 12, 14, 0, tzinfo=UTC),
        )

        self.page.goto(
            self.live_server_url
            + reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                    "title": "test-movie",
                },
            ),
        )

        expect(self.page.get_by_role("main")).to_contain_text("Test Movie")

        # A completed movie leads with Check-in; Add new entry is in the menu.
        self.page.get_by_role("button", name="More tracking actions").click()
        expect(self.page.get_by_role("button", name="Add new entry")).to_be_visible()
        self.page.get_by_role("button", name="Add new entry").click()

        create_modal = self.shared_dialog()
        expect(create_modal).to_be_visible()
        create_modal.get_by_role("button", name="Close", exact=True).click()
        expect(self.page.get_by_role("dialog")).to_have_count(0)

        self.page.get_by_role("button", name="More tracking actions").click()
        self.page.get_by_role("button", name="Add new entry").click()
        create_modal = self.shared_dialog()
        expect(create_modal).to_be_visible()

        set_dates = create_modal.get_by_role("button", name=re.compile(r"^Date picker:"))
        clear_buttons = create_modal.get_by_role("button", name="Clear date")
        start_now = create_modal.get_by_role("button", name="Start Now", exact=True)
        release_date = create_modal.get_by_role(
            "button", name="Release Date", exact=True,
        )
        just_finished = create_modal.get_by_role(
            "button", name="Just Finished", exact=True,
        )

        def clear_dates():
            # The form may auto-fill dates after it opens; empty both fields.
            while clear_buttons.count():
                clear_buttons.first.click()
            expect(set_dates).to_have_count(0)

        clear_dates()
        # Split per #1243: the start field offers Start Now and Release Date,
        # the end field only Just Finished.
        expect(start_now).to_have_count(1)
        expect(release_date).to_have_count(1)
        expect(just_finished).to_have_count(1)
        expect(create_modal.get_by_text("Not set", exact=True)).to_have_count(2)

        before_start_action = self.page.evaluate("Date.now()")
        start_now.click()
        after_start_action = self.page.evaluate("Date.now()")
        expect(set_dates).to_have_count(2)
        start_dt, end_dt = self.shared_date_values(create_modal)
        start_value_ms = self._ms(start_dt.strftime("%Y-%m-%dT%H:%M:%S"))
        self.assertGreaterEqual(start_value_ms, before_start_action - 1000)
        self.assertLessEqual(start_value_ms, after_start_action + 1000)
        self.assertEqual(end_dt - start_dt, timedelta(minutes=95))
        expect(start_now).to_have_count(0)
        expect(just_finished).to_have_count(0)
        self.expect_track_status(create_modal, "In Progress")

        clear_dates()
        expect(start_now).to_be_visible()
        expect(just_finished).to_be_visible()

        before_end_finished = self.page.evaluate("Date.now()")
        just_finished.click()
        after_end_finished = self.page.evaluate("Date.now()")
        expect(just_finished).to_have_count(0)
        end_dt = self.shared_date_values(create_modal)[-1]
        end_value_ms = self._ms(end_dt.strftime("%Y-%m-%dT%H:%M:%S"))
        self.assertGreaterEqual(end_value_ms, before_end_finished - 1000)
        self.assertLessEqual(end_value_ms, after_end_finished + 1000)

        clear_dates()
        release_date.click()
        expect(set_dates).to_have_count(1)
        self.assertEqual(
            self.shared_date_values(create_modal)[0].date(),
            date(2019, 11, 8),
        )
        expect(release_date).to_have_count(0)

        clear_dates()
        expect(just_finished).to_be_visible()
        before_just_finished = self.page.evaluate("Date.now()")
        just_finished.click()
        after_just_finished = self.page.evaluate("Date.now()")
        expect(set_dates).to_have_count(2)
        start_dt, end_dt = self.shared_date_values(create_modal)
        just_finished_start_ms = self._ms(start_dt.strftime("%Y-%m-%dT%H:%M:%S"))
        just_finished_end_ms = self._ms(end_dt.strftime("%Y-%m-%dT%H:%M:%S"))
        self.assertGreaterEqual(
            just_finished_start_ms,
            before_just_finished - 95 * 60 * 1000 - 1000,
        )
        self.assertLessEqual(
            just_finished_start_ms,
            after_just_finished - 95 * 60 * 1000 + 1000,
        )
        self.assertGreaterEqual(just_finished_end_ms, before_just_finished - 1000)
        self.assertLessEqual(just_finished_end_ms, after_just_finished + 1000)

        # The End picker's Release Date back-fills an empty start from the runtime.
        clear_dates()
        self.shared_date_buttons(create_modal).nth(1).click()
        self.shared_picker().get_by_role(
            "button", name="Release Date", exact=True,
        ).click()
        self.close_shared_picker()
        expect(set_dates).to_have_count(2)
        start_dt, end_dt = self.shared_date_values(create_modal)
        self.assertEqual(end_dt.date(), date(2019, 11, 8))
        self.assertEqual(end_dt - start_dt, timedelta(minutes=95))

        # None empties each date again.
        self.shared_date_buttons(create_modal).nth(1).click()
        self.shared_picker().get_by_role("button", name="None", exact=True).click()
        self.close_shared_picker()
        expect(set_dates).to_have_count(1)
        self.shared_date_buttons(create_modal).nth(0).click()
        self.shared_picker().get_by_role("button", name="None", exact=True).click()
        self.close_shared_picker()
        expect(set_dates).to_have_count(0)

        with self.page.expect_request(
            lambda request: request.method == "POST" and "/media_save" in request.url,
        ) as save_request:
            create_modal.get_by_role("button", name="Add", exact=True).click()
        save_request.value.response()
        expect(self.page.get_by_role("dialog")).to_have_count(0)

        new_movie = (
            Movie.objects.filter(item=item, user=self.user).order_by("-id").first()
        )
        self.assertIsNotNone(new_movie)
        self.assertIsNone(new_movie.start_date)
        self.assertIsNone(new_movie.end_date)

        # An in-progress entry leads with its own Edit button.
        self.page.get_by_role("button", name=re.compile(r"^Edit entry")).click()
        edit_modal = self.shared_dialog()
        expect(edit_modal).to_be_visible()
        edit_modal.get_by_role("button", name="Close", exact=True).click()
        expect(self.page.get_by_role("dialog")).to_have_count(0)

    def _stretch_content_container(self):
        """Make the details page long, as the reported scenario describes."""
        self.page.evaluate(
            """() => {
                const container = document.querySelector('main > .container');
                const spacer = document.createElement('div');
                spacer.style.height = '2500px';
                container.appendChild(spacer);
            }"""
        )

    def _assert_visible_overlay_fills_viewport(self):
        """A `fixed inset-0` overlay must cover the viewport, not the document.

        A `transform`, `filter` or `backdrop-filter` on any ancestor makes that
        ancestor the CSS containing block for `position: fixed` descendants, so
        the overlay would be sized to the (document-tall) content container and
        its content centered far below the visible viewport (#1203).
        """
        overlay = self.page.locator("div.fixed.inset-0:visible").first
        expect(overlay).to_be_visible()
        # The page entrance animation temporarily makes its container a fixed
        # containing block. Visibility alone can sample its final moving frame.
        self.page.wait_for_function(
            """overlay => {
                const box = overlay.getBoundingClientRect();
                return overlay.isConnected && Math.abs(box.x) < 2 && Math.abs(box.y) < 2
                    && Math.abs(box.width - window.innerWidth) < 2
                    && Math.abs(box.height - window.innerHeight) < 2;
            }""",
            arg=overlay.element_handle(),
        )
        box = overlay.bounding_box()
        viewport = self.page.viewport_size
        self.assertIsNotNone(box)
        self.assertLess(abs(box["x"]), 2)
        self.assertLess(abs(box["y"]), 2)
        self.assertLess(abs(box["width"] - viewport["width"]), 2)
        self.assertLess(abs(box["height"] - viewport["height"]), 2)

    @patch("app.providers.services.get_media_metadata")
    def test_track_modal_overlay_is_viewport_anchored_on_long_page(
        self,
        mock_get_metadata,
    ):
        """The logging modal must anchor to the viewport on a long page (#1203)."""
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "max_progress": 1,
            "details": {"release_date": "2019-11-08"},
            "related": {},
        }
        item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
        )
        Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=1,
        )

        self.page.goto(
            self.live_server_url
            + reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                    "title": "test-movie",
                },
            ),
        )
        expect(self.page.get_by_role("main")).to_contain_text("Test Movie")
        self._stretch_content_container()

        self.page.get_by_role("button", name="More tracking actions").click()
        self.page.get_by_role("button", name="Add new entry").click()
        expect(self.page.locator("[data-track-modal-root]:visible")).to_be_visible()

        self._assert_visible_overlay_fills_viewport()

    @patch("app.providers.tmdb.carousel_media")
    @patch("app.providers.services.get_media_metadata")
    def test_gallery_lightbox_overlay_is_viewport_anchored_on_long_page(
        self,
        mock_get_metadata,
        mock_carousel_media,
    ):
        """The gallery lightbox must anchor to the viewport on a long page (#1203)."""
        mock_get_metadata.return_value = {
            "media_id": "carousel-1203",
            "title": "Carousel Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "max_progress": 1,
            "details": {"release_date": "2019-11-08"},
            "related": {},
        }
        mock_carousel_media.return_value = {
            "video": {"key": "dQw4w9WgXcQ"},
            "photos": [{"file_path": "/photo-a.jpg"}, {"file_path": "/photo-b.jpg"}],
        }
        item = Item.objects.create(
            media_id="carousel-1203",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Carousel Movie",
            image="http://example.com/image.jpg",
        )
        Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=1,
        )

        self.page.goto(
            self.live_server_url
            + reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "carousel-1203",
                    "title": "carousel-movie",
                },
            ),
        )
        expect(self.page.get_by_role("main")).to_contain_text("Carousel Movie")

        expand = self.page.locator("button[aria-label='View full size']").first
        expect(expand).to_be_visible()
        self._stretch_content_container()
        expand.click()
        expect(self.page.locator("div.fixed.inset-0.z-50:visible")).to_be_visible()

        self._assert_visible_overlay_fills_viewport()

    @patch("app.providers.services.get_media_metadata")
    def test_session_history_calendar_and_shared_date_picker_navigation(
        self,
        mock_get_metadata,
    ):
        """Both calendars expose the shared navigation and activity-day behavior."""
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "max_progress": 1,
            "details": {"release_date": "2019-11-08"},
            "related": {},
        }
        item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
            runtime_minutes=95,
        )
        for end_date in (
            datetime(2026, 3, 1, 14, 0, tzinfo=UTC),
            datetime(2026, 3, 12, 14, 0, tzinfo=UTC),
            datetime(2026, 2, 28, 14, 0, tzinfo=UTC),
        ):
            Movie.objects.create(
                item=item,
                user=self.user,
                status=Status.COMPLETED.value,
                progress=1,
                end_date=end_date,
            )

        # The detail page defers part of itself to a "fragment=secondary" htmx
        # load. Opening the session-history modal while that request is still in
        # flight lets the swap land on top of the modal and re-initialize its
        # calendar, leaving the activity markers empty. Wait for the fragment
        # first, the same way the episode test above does.
        with self.page.expect_response(
            lambda response: "fragment=secondary" in response.url,
        ):
            self.page.goto(
                self.live_server_url
                + reverse(
                    "media_details",
                    kwargs={
                        "source": Sources.TMDB.value,
                        "media_type": MediaTypes.MOVIE.value,
                        "media_id": "238",
                        "title": "test-movie",
                    },
                ),
            )
        expect(self.page.get_by_role("main")).to_contain_text("Test Movie")

        self.page.get_by_role("button", name="View play history").first.click()
        session_modal = self.page.locator(
            '[role="dialog"][aria-label="Activity history"]:visible',
        )
        expect(session_modal).to_be_visible()
        expect(session_modal).to_contain_text("Active days")

        # The activity calendar is the shared React date picker (inline), with
        # one dot per activity level on each day that has plays.
        calendar = session_modal.locator("[data-date-time-picker-root]")

        def day(key):
            return calendar.locator(f'[data-testid="date-time-day-{key}"]')

        expect(calendar.locator('[data-testid^="date-time-day-"]')).to_have_count(42)
        expect(day("2026-03-01")).to_have_count(1)
        expect(day("2026-03-05")).to_have_count(1)
        expect(day("2026-03-01")).not_to_be_disabled()
        expect(day("2026-03-05")).to_be_disabled()
        expect(
            calendar.locator('[data-testid^="date-time-day-"][aria-label^="View activity for"]'),
        ).to_have_count(3)

        active_day = day("2026-03-01")
        active_day.click()
        expect(active_day).to_have_attribute("aria-selected", "true")
        adjacent_active_day = day("2026-02-28")
        expect(adjacent_active_day).not_to_be_disabled()
        adjacent_active_day.click()
        expect(adjacent_active_day).to_have_attribute("aria-selected", "true")

        calendar.get_by_role("button", name="Select month").click()
        calendar.get_by_role("button", name="Select year").click()
        year_input = calendar.get_by_role("textbox", name="Year")
        year_input.fill("2024")
        year_input.press("Enter")
        calendar.get_by_role("button", name="Mar", exact=True).click()
        expect(calendar.get_by_role("button", name="Select month")).to_contain_text(
            "March 2024",
        )
        session_modal.get_by_role(
            "button",
            name="Close activity history",
        ).click()

        self.page.get_by_role("button", name="More tracking actions").click()
        self.page.get_by_role("button", name="Add new entry").click()
        track_modal = self.shared_dialog()
        self.shared_date_buttons(track_modal).nth(1).click()
        date_picker = self.shared_picker()
        date_picker.get_by_role("button", name="Select month").click()
        date_picker.get_by_role("button", name="Select year").click()
        date_picker.get_by_role("textbox", name="Year").fill("2024")
        date_picker.get_by_role("textbox", name="Year").press("Enter")
        date_picker.get_by_role("button", name="Mar", exact=True).click()
        expect(
            date_picker.get_by_role("button", name="Select month"),
        ).to_contain_text("March 2024")
        # February 29th shows as an adjacent day of the March grid.
        date_picker.get_by_role("button", name="2024-02-29", exact=True).click()
        self.close_shared_picker()
        end_dt = self.shared_date_values(track_modal)[-1]
        self.assertEqual(end_dt.date(), date(2024, 2, 29))

    @patch("app.models.Item.fetch_releases")
    @patch("app.views._should_queue_game_lengths_refresh", return_value=False)
    @patch("app.providers.services.get_media_metadata")
    def test_game_progress_live_updates_start_date(
        self,
        mock_get_metadata,
        _mock_should_queue_game_lengths_refresh,
        _mock_fetch_releases,
    ):
        """Game progress should backfill the start date immediately in the modal."""
        mock_get_metadata.return_value = {
            "media_id": "186090",
            "title": "Wordle",
            "media_type": MediaTypes.GAME.value,
            "source": Sources.IGDB.value,
            "image": "http://example.com/wordle.jpg",
            "details": {
                "release_date": "2021-06-21",
            },
            "related": {},
            "score": 8.5,
            "score_count": 17,
        }
        item = Item.objects.create(
            media_id="186090",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Wordle",
            image="http://example.com/wordle.jpg",
            release_datetime=datetime(2021, 6, 21, 12, 0, tzinfo=UTC),
        )
        Game.objects.create(
            item=item,
            user=self.user,
            status=Status.PLANNING.value,
            progress=0,
            end_date=datetime(2026, 5, 12, 19, 47, tzinfo=UTC),
        )

        self.page.goto(
            self.live_server_url
            + reverse(
                "media_details",
                kwargs={
                    "source": Sources.IGDB.value,
                    "media_type": MediaTypes.GAME.value,
                    "media_id": "186090",
                    "title": "wordle",
                },
            ),
        )

        expect(self.page.get_by_role("main")).to_contain_text("Wordle")
        # A game offers Check-in, Add new entry and Edit, so Edit is in the menu.
        self.page.get_by_role("button", name="More tracking actions").click()
        self.page.get_by_role("button", name="Edit", exact=True).click()

        modal = self.shared_dialog()
        expect(modal).to_be_visible()

        # Only the end date is set before progress is typed.
        buttons = modal.get_by_role("button", name=re.compile(r"^Date picker:"))
        expect(buttons).to_have_count(1)
        (end_dt,) = self.shared_date_values(modal)
        self.assertEqual(end_dt, datetime(2026, 5, 12, 19, 47))

        modal.get_by_role("textbox", name="Progress (Time Played)").fill("5min")

        expect(buttons).to_have_count(2)
        start_dt, end_dt_after = self.shared_date_values(modal)
        self.assertEqual(end_dt_after, end_dt)
        self.assertEqual(start_dt, end_dt - timedelta(minutes=5))

    def _open_new_movie_tracker(self, status=None):
        """Open the Add to tracker modal on the movie page, optionally pick a status."""
        self.page.goto(
            self.live_server_url
            + reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                    "title": "test-movie",
                },
            ),
        )
        expect(self.page.get_by_role("main")).to_contain_text("Test Movie")
        self.page.get_by_role("button", name="Add to tracker").click()
        modal = self.shared_dialog()
        expect(modal).to_be_visible()
        if status:
            self.pick_track_status(modal, Status(status).label)
        return modal

    def _create_test_movie_item(self, mock_get_metadata):
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "max_progress": 1,
            "score": 7.6,
            "score_count": 42000,
            "details": {"release_date": "2019-11-08"},
            "related": {},
        }
        return Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
            runtime_minutes=95,
        )

    def _save_movie(self, modal, item):
        """Click Add and return the saved Movie once the server has answered.

        The test must not read app_movie while the save request is still
        running: the live server and the test share one SQLite database, and
        an overlapping read makes the request fail with "table is locked".
        """
        with self.page.expect_response(
            lambda response: (
                response.request.method == "POST" and "/media_save" in response.url
            ),
        ) as save_response:
            modal.get_by_role("button", name="Add", exact=True).click()
        self.assertEqual(save_response.value.status, 200)
        expect(self.page.get_by_role("dialog")).to_have_count(0)
        return Movie.objects.filter(item=item, user=self.user).first()

    def _ms(self, value):
        """Milliseconds since epoch for a datetime-local input value."""
        return self.page.evaluate("value => new Date(value).getTime()", value)

    @patch("app.providers.services.get_media_metadata")
    def test_track_modal_end_date_stays_empty_so_shortcuts_apply(
        self,
        mock_get_metadata,
    ):
        """The modal doesn't pre-fill the End date, so both shortcuts keep working.

        A date the user picked is still left alone.
        """
        self._create_test_movie_item(mock_get_metadata)
        runtime_ms = 95 * 60 * 1000

        # Completed leaves the End date empty, with the shortcuts offered.
        modal = self._open_new_movie_tracker(Status.COMPLETED.value)
        set_dates = modal.get_by_role("button", name=re.compile(r"^Date picker:"))
        expect(set_dates).to_have_count(0)
        expect(modal.get_by_text("Not set", exact=True)).to_have_count(2)
        just_finished = modal.get_by_role("button", name="Just Finished", exact=True)
        expect(just_finished).to_be_visible()

        # Just Finished sets the end to now and the start a runtime earlier.
        before = self.page.evaluate("Date.now()")
        just_finished.click()
        after = self.page.evaluate("Date.now()")
        expect(set_dates).to_have_count(2)
        start_dt, end_dt = self.shared_date_values(modal)
        end_ms = self._ms(end_dt.strftime("%Y-%m-%dT%H:%M:%S"))
        # The field shows whole minutes, so allow a minute either way.
        self.assertGreaterEqual(end_ms, before - 60 * 1000)
        self.assertLessEqual(end_ms, after + 1000)
        self.assertEqual(end_dt - start_dt, timedelta(milliseconds=runtime_ms))

        # Start Now sets the start to now and the end to now plus the runtime.
        modal = self._open_new_movie_tracker(Status.COMPLETED.value)
        before = self.page.evaluate("Date.now()")
        modal.get_by_role("button", name="Start Now", exact=True).click()
        after = self.page.evaluate("Date.now()")
        set_dates = modal.get_by_role("button", name=re.compile(r"^Date picker:"))
        expect(set_dates).to_have_count(2)
        start_dt, end_dt = self.shared_date_values(modal)
        start_ms = self._ms(start_dt.strftime("%Y-%m-%dT%H:%M:%S"))
        self.assertGreaterEqual(start_ms, before - 60 * 1000)
        self.assertLessEqual(start_ms, after + 1000)
        self.assertEqual(end_dt - start_dt, timedelta(milliseconds=runtime_ms))
        self.expect_track_status(modal, "In Progress")

        # An end date the user picked is kept when Start Now is pressed.
        modal = self._open_new_movie_tracker(Status.COMPLETED.value)
        self.shared_date_buttons(modal).nth(1).click()
        self.shared_picker().get_by_role(
            "button", name="Release Date", exact=True,
        ).click()
        self.close_shared_picker()
        set_dates = modal.get_by_role("button", name=re.compile(r"^Date picker:"))
        # Picking a release date back-fills the start from the runtime; clear it
        # so the Start Now shortcut is offered again.
        expect(set_dates).to_have_count(2)
        self.assertEqual(self.shared_date_values(modal)[1].date(), date(2019, 11, 8))
        modal.get_by_role("button", name="Clear date").first.click()
        expect(set_dates).to_have_count(1)
        modal.get_by_role("button", name="Start Now", exact=True).click()
        expect(set_dates).to_have_count(2)
        self.assertEqual(self.shared_date_values(modal)[1].date(), date(2019, 11, 8))

    @patch("app.models.Item.fetch_releases")
    @patch("app.providers.services.get_media_metadata")
    def test_track_modal_untouched_end_date_saves_as_now(
        self,
        mock_get_metadata,
        _mock_fetch_releases,
    ):
        """A new Completed entry still gets an end date of now when saved.

        Choosing None keeps it empty.
        """
        item = self._create_test_movie_item(mock_get_metadata)

        modal = self._open_new_movie_tracker(Status.COMPLETED.value)
        expect(modal.get_by_role("button", name="Just Finished")).to_be_visible()
        before = timezone.now()
        movie = self._save_movie(modal, item)
        self.assertIsNotNone(movie)
        self.assertIsNotNone(movie.end_date)
        self.assertGreaterEqual(movie.end_date, before - timedelta(minutes=1))
        self.assertLessEqual(movie.end_date, timezone.now() + timedelta(minutes=1))
        movie.delete()

        modal = self._open_new_movie_tracker(Status.COMPLETED.value)
        # Choosing None in the End picker keeps the end date empty.
        self.shared_date_buttons(modal).nth(1).click()
        self.shared_picker().get_by_role("button", name="None", exact=True).click()
        self.close_shared_picker()
        expect(modal.get_by_role("button", name="Just Finished")).to_be_visible()
        movie = self._save_movie(modal, item)
        self.assertIsNotNone(movie)
        self.assertIsNone(movie.end_date)

    @patch("app.models.Item.fetch_releases")
    @patch("app.views._should_queue_game_lengths_refresh", return_value=False)
    @patch("app.providers.services.get_media_metadata")
    def test_game_progress_fills_end_and_start_dates(
        self,
        mock_get_metadata,
        _mock_should_queue_game_lengths_refresh,
        _mock_fetch_releases,
    ):
        """New game entry: typing progress sets the end to now and the start before it.

        Just Finished afterwards keeps the start about the progress earlier.
        """
        mock_get_metadata.return_value = {
            "media_id": "186090",
            "title": "Wordle",
            "media_type": MediaTypes.GAME.value,
            "source": Sources.IGDB.value,
            "image": "http://example.com/wordle.jpg",
            "details": {"release_date": "2021-06-21"},
            "related": {},
            "score": 8.5,
            "score_count": 17,
        }
        Item.objects.create(
            media_id="186090",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Wordle",
            image="http://example.com/wordle.jpg",
            release_datetime=datetime(2021, 6, 21, 12, 0, tzinfo=UTC),
        )
        self.page.goto(
            self.live_server_url
            + reverse(
                "media_details",
                kwargs={
                    "source": Sources.IGDB.value,
                    "media_type": MediaTypes.GAME.value,
                    "media_id": "186090",
                    "title": "wordle",
                },
            ),
        )
        expect(self.page.get_by_role("main")).to_contain_text("Wordle")
        self.page.get_by_role("button", name="Add to tracker").click()
        modal = self.shared_dialog()
        expect(modal).to_be_visible()
        self.pick_track_status(modal, "In Progress")

        dates = modal.get_by_role("button", name=re.compile(r"^Date picker:"))
        expect(dates).to_have_count(0)
        before = self.page.evaluate("Date.now()")
        modal.get_by_role("textbox", name="Progress (Time Played)").fill("90min")
        expect(dates).to_have_count(2)
        after = self.page.evaluate("Date.now()")
        start_dt, end_dt = self.shared_date_values(modal)
        # The button shows whole minutes in the browser's local time (UTC in CI).
        end_ms = self._ms(end_dt.strftime("%Y-%m-%dT%H:%M"))
        self.assertGreaterEqual(end_ms, before - 120 * 1000)
        self.assertLessEqual(end_ms, after + 1000)
        self.assertEqual(end_dt - start_dt, timedelta(minutes=90))

    def test_statistics_status_composition_legend_scrolls_without_page_overflow(self):
        """Status legend scrolls inside its card at responsive widths."""
        statuses = (
            Status.COMPLETED.value,
            Status.IN_PROGRESS.value,
            Status.PLANNING.value,
            Status.PAUSED.value,
            Status.DROPPED.value,
        )
        for index, status in enumerate(statuses):
            item = Item.objects.create(
                media_id=f"status-layout-{index}",
                source=Sources.MANUAL.value,
                media_type=MediaTypes.MOVIE.value,
                title=f"Status layout {index}",
            )
            Movie.objects.create(
                item=item,
                user=self.user,
                status=status,
                progress=1 if status == Status.COMPLETED.value else 0,
            )

        # Wider layouts give the legend the card's full width; a phone keeps it
        # beside the chart, where five statuses still need to scroll.
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.goto(
            self.live_server_url
            + reverse("statistics")
            + "?start-date=all&end-date=all&compare=none",
        )

        legend = self.page.locator('[data-testid="status-composition-legend"]')
        expect(legend).to_be_visible()
        expect(legend.locator('[data-testid="statistics-legend-row"]')).to_have_count(5)

        layout = legend.evaluate(
            """element => ({
                overflowX: getComputedStyle(element).overflowX,
                overflowY: getComputedStyle(element).overflowY,
                clientWidth: element.clientWidth,
                scrollWidth: element.scrollWidth,
            })""",
        )
        self.assertIn(layout["overflowX"], ("auto", "scroll"))
        self.assertEqual(layout["overflowY"], "hidden")

        page_layout = self.page.evaluate(
            """() => ({
                viewportWidth: window.innerWidth,
                documentWidth: document.documentElement.scrollWidth,
                bodyWidth: document.body.scrollWidth,
                chartWidth: document.querySelector('[data-testid="status-composition-chart"]')
                    ?.getBoundingClientRect().width,
            })""",
        )
        self.assertLessEqual(page_layout["documentWidth"], page_layout["viewportWidth"])
        self.assertLessEqual(page_layout["bodyWidth"], page_layout["viewportWidth"])
        self.assertEqual(page_layout["chartWidth"], 150)

        self.page.get_by_role("button", name="All media", exact=True).click()
        self.page.get_by_role("button", name="Movies", exact=True).click()
        # The chart's accessible summary replaces the retired subtitle.
        expect(self.page.locator('[data-testid="status-composition-chart"]')).to_have_attribute(
            "aria-label", re.compile(r"^Status Composition\. Total "),
        )
        self.assertIn("media-type=movie", self.page.url)
        filtered_layout = legend.evaluate(
            """element => ({
                overflowX: getComputedStyle(element).overflowX,
                overflowY: getComputedStyle(element).overflowY,
                clientWidth: element.clientWidth,
                scrollWidth: element.scrollWidth,
            })""",
        )
        self.assertEqual(filtered_layout["overflowX"], "auto")
        self.assertEqual(filtered_layout["overflowY"], "hidden")
        # A wider card may fit every row. Constrain the legend to exercise
        # horizontal scrolling without depending on sidebar/card breakpoints.
        legend.evaluate("element => element.style.maxWidth = '180px'")
        self.assertTrue(
            legend.evaluate("element => element.scrollWidth > element.clientWidth")
        )
        legend.evaluate("element => element.style.removeProperty('max-width')")

        self.page.set_viewport_size({"width": 390, "height": 774})
        mobile_layout = self.page.evaluate(
            """() => ({
                viewportWidth: window.innerWidth,
                documentWidth: document.documentElement.scrollWidth,
                bodyWidth: document.body.scrollWidth,
            })""",
        )
        self.assertLessEqual(
            mobile_layout["documentWidth"], mobile_layout["viewportWidth"]
        )
        self.assertLessEqual(mobile_layout["bodyWidth"], mobile_layout["viewportWidth"])

        self.page.set_viewport_size({"width": 1440, "height": 774})
        desktop_layout = self.page.evaluate(
            """() => ({
                viewportWidth: window.innerWidth,
                documentWidth: document.documentElement.scrollWidth,
                chartWidth: document.querySelector('[data-testid="status-composition-chart"]')
                    ?.getBoundingClientRect().width,
            })""",
        )
        self.assertLessEqual(
            desktop_layout["documentWidth"], desktop_layout["viewportWidth"]
        )
        self.assertEqual(desktop_layout["chartWidth"], 150)

    @patch("app.discover_views.discover.get_discover_rows")
    def test_discover_match_signal_wraps_without_page_overflow(
        self, mock_get_discover_rows
    ):
        """Long Discover row metadata stays inside the viewport on mobile."""
        match_signal = (
            "Driven by your current 90-109 Minutes, 2010s, Adventure phase "
            "with recent favorites"
        )
        mock_get_discover_rows.return_value = [
            RowResult(
                key="top_picks_for_you",
                title="Top Picks For You",
                mission="Mission",
                why="New-to-you movies tailored to your taste.",
                source="local",
                items=[],
                match_signal=match_signal,
            ),
        ]

        self.page.set_viewport_size({"width": 390, "height": 774})
        self.page.goto(
            self.live_server_url
            + reverse("discover")
            + "?media_type=movie&discover_debug=1",
        )

        signal = self.page.locator(
            "#discover-row-top_picks_for_you p.shrink-0",
        )
        expect(signal).to_have_text(match_signal)
        mobile_layout = self.page.evaluate(
            """() => ({
                viewportWidth: window.innerWidth,
                documentWidth: document.documentElement.scrollWidth,
                bodyWidth: document.body.scrollWidth,
            })""",
        )
        self.assertLessEqual(
            mobile_layout["documentWidth"], mobile_layout["viewportWidth"]
        )
        self.assertLessEqual(mobile_layout["bodyWidth"], mobile_layout["viewportWidth"])
        mobile_signal_box = signal.bounding_box()
        mobile_row_box = self.page.locator(
            "#discover-row-top_picks_for_you",
        ).bounding_box()
        self.assertIsNotNone(mobile_signal_box)
        self.assertIsNotNone(mobile_row_box)
        self.assertLessEqual(
            mobile_signal_box["x"] + mobile_signal_box["width"],
            mobile_row_box["x"] + mobile_row_box["width"],
        )
        self.assertGreater(mobile_signal_box["height"], 16)

        self.page.set_viewport_size({"width": 1440, "height": 774})
        self.page.reload()
        desktop_layout = self.page.evaluate(
            """() => ({
                viewportWidth: window.innerWidth,
                documentWidth: document.documentElement.scrollWidth,
                bodyWidth: document.body.scrollWidth,
            })""",
        )
        self.assertLessEqual(
            desktop_layout["documentWidth"], desktop_layout["viewportWidth"]
        )
        self.assertLessEqual(
            desktop_layout["bodyWidth"], desktop_layout["viewportWidth"]
        )
        desktop_signal_box = signal.bounding_box()
        desktop_row_box = self.page.locator(
            "#discover-row-top_picks_for_you",
        ).bounding_box()
        self.assertIsNotNone(desktop_signal_box)
        self.assertIsNotNone(desktop_row_box)
        self.assertLessEqual(
            desktop_signal_box["x"] + desktop_signal_box["width"],
            desktop_row_box["x"] + desktop_row_box["width"],
        )
        # Layout boxes are sub-pixel (15.99997 is 16 on screen), so compare
        # to within a rounding error rather than exactly.
        self.assertAlmostEqual(desktop_signal_box["height"], 16, delta=0.5)
