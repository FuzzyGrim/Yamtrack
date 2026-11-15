"""Provider tabs keep account setup, outgoing writes and results separate."""

from html.parser import HTMLParser
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase
from django.urls import reverse


class FormParser(HTMLParser):
    """Collect actual form targets, ignoring URLs in Alpine expressions."""

    def __init__(self, html):
        """Parse the supplied fragment."""
        super().__init__()
        self.actions = []
        self.div_depth = 0
        self.minimum_div_depth = 0
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "form":
            self.actions.append(dict(attrs).get("action"))
        if tag == "div":
            self.div_depth += 1

    def handle_endtag(self, tag):
        if tag == "div":
            self.div_depth -= 1
            self.minimum_div_depth = min(self.minimum_div_depth, self.div_depth)


class ProviderSyncTemplateTests(SimpleTestCase):
    """Rendering a tab must expose only its provider's deliberate actions."""

    def setUp(self):
        self.request = RequestFactory().get("/settings/providers/mal?tab=sync")
        self.user = get_user_model()(username="member")
        self.request.user = self.user

    def render_mal(self, tab, account=None):
        """Render MAL without performing a provider or database lookup."""
        return render_to_string(
            "users/providers/mal_sync.html",
            {
                "user": self.user,
                "provider_slug": "mal",
                "provider_tab": tab,
                "mal_sync_initial": None,
                "mal_ignored_mappings": [],
                "mal_manual_mappings": [],
                "mal_sync_configured": True,
                "mal_account": account,
            },
            request=self.request,
        )

    def test_mal_sync_keeps_mapping_controls_without_other_providers(self):
        html = self.render_mal("sync")

        self.assertIn("Manual episode mappings", html)
        self.assertIn("Ignored anime", html)
        self.assertIn("?tab=connection", html)
        actions = FormParser(html).actions
        self.assertNotIn(reverse("mal_oauth"), actions)
        self.assertNotIn(reverse("tmdb_connect"), actions)
        self.assertNotIn(reverse("wetrakr_connect"), actions)
        self.assertNotIn(reverse("update_group_scrobbled_anime"), actions)

    def test_mal_activity_keeps_results_and_mapping_queues(self):
        html = self.render_mal("activity")

        self.assertIn("Recent MyAnimeList sync", html)
        self.assertIn("Anime with mapping issues", html)
        self.assertNotIn("Sync Filters", html)
        self.assertNotIn(reverse("mal_oauth"), FormParser(html).actions)

    def test_connected_mal_keeps_sync_options_and_balanced_panels(self):
        account = SimpleNamespace(is_connected=True, sync_enabled=True)
        for tab in ("sync", "activity"):
            with self.subTest(tab=tab):
                html = self.render_mal(tab, account)
                parser = FormParser(html)
                self.assertEqual(parser.div_depth, 0)
                self.assertEqual(parser.minimum_div_depth, 0)
                self.assertEqual(reverse("mal_toggle") in parser.actions, tab == "sync")
                self.assertEqual(
                    reverse("mal_sync_filters_save") in parser.actions, tab == "sync"
                )
                self.assertNotIn(reverse("mal_disconnect"), parser.actions)

    def test_mal_authorization_explicitly_opts_into_outbound_sync(self):
        html = render_to_string(
            "users/providers/mal_connection.html",
            {"mal_sync_configured": True},
            request=self.request,
        )

        self.assertIn("Authorize and enable MyAnimeList sync", html)
        self.assertIn("Adding API credentials alone does not enable sync", html)
        self.assertIn(reverse("mal_oauth"), FormParser(html).actions)

    def test_tmdb_tabs_separate_authorization_from_rating_writes(self):
        account = SimpleNamespace(username="member")
        context = {"tmdb_rating_sync": {"account": account, "enabled": True}}
        for tab in ("connection", "sync", "activity"):
            with self.subTest(tab=tab):
                html = render_to_string(
                    "users/components/tmdb_rating_sync.html",
                    {**context, "provider_tab": tab},
                    request=self.request,
                )
                actions = FormParser(html).actions
                self.assertEqual(
                    reverse("tmdb_connect") in actions, tab == "connection"
                )
                self.assertEqual(reverse("tmdb_settings") in actions, tab == "sync")
                self.assertEqual(
                    reverse("tmdb_export_ratings") in actions, tab == "sync"
                )

    def test_wetrakr_tabs_separate_delivery_settings_from_authorization(self):
        context = {
            "wetrakr_sync": {
                "account": SimpleNamespace(is_connected=True),
                "enabled": {},
                "binding": SimpleNamespace(delivery_schedule="immediate"),
                "schedules": [],
            },
        }
        for tab in ("connection", "sync", "activity"):
            with self.subTest(tab=tab):
                html = render_to_string(
                    "users/components/wetrakr_sync.html",
                    {**context, "provider_tab": tab},
                    request=self.request,
                )
                actions = FormParser(html).actions
                # Linking and unlinking live on the shared Connection card.
                self.assertNotIn(reverse("wetrakr_disconnect"), actions)
                self.assertNotIn(reverse("wetrakr_connect"), actions)
                self.assertEqual(reverse("wetrakr_settings") in actions, tab == "sync")
