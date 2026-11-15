"""A linked account is not the same as credentials, and a bundled key is not the user's to replace."""

import json
from datetime import timedelta

from bs4 import BeautifulSoup
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from integrations.imports.helpers import encrypt
from integrations.models import WeTrakrAccount
from users import metadata_views


def card(response, key="connection"):
    """Return one Overview card from the provider page model."""
    return next(c for c in response.context["provider_page"]["overviewCards"] if c["key"] == key)


class ProviderAccountStateTests(TestCase):
    """Overview and Connection describe the account first and credentials second."""

    def setUp(self):
        """Log in an owner who is also the instance admin."""
        cache.clear()
        self.user = get_user_model().objects.create_superuser(username="owner", password="x")
        self.client.force_login(self.user)

    def link_wetrakr(self, **fields):
        return WeTrakrAccount.objects.create(user=self.user, profile_key="p", client_id="app", **fields)

    def test_unlinked_wetrakr_says_so_and_never_mentions_the_bundled_key(self):
        response = self.client.get(reverse("provider_detail", args=["wetrakr"]))

        overview = card(response)
        self.assertEqual(overview["subtitle"], "Account not linked")
        self.assertEqual(overview["badge"], "Inactive")
        self.assertEqual(overview["detail"], "")
        self.assertNotIn("credentials", json.dumps(response.context["provider_page"]).lower())

    def test_linked_wetrakr_is_active(self):
        self.link_wetrakr(refresh_token=encrypt("refresh"))

        overview = card(self.client.get(reverse("provider_detail", args=["wetrakr"])))

        self.assertEqual((overview["subtitle"], overview["badge"]), ("Account linked", "Active"))

    def test_a_pending_approval_needs_attention_not_a_green_badge(self):
        self.link_wetrakr(
            device_code=encrypt("dev"), user_code="AB-12", verification_url="https://wetrakr.com/device",
            device_expires_at=timezone.now() + timedelta(minutes=5),
        )

        overview = card(self.client.get(reverse("provider_detail", args=["wetrakr"])))

        self.assertEqual(overview["subtitle"], "Waiting for you to approve")
        self.assertEqual(overview["tone"], "warning")

    @override_settings(MAL_API="mal-client", SHARED_DEFAULT_CREDENTIALS={})
    def test_credentials_are_a_second_line_on_providers_with_an_account(self):
        url = reverse("provider_detail", args=["mal"])

        unlinked = card(self.client.get(url))
        self.assertEqual(unlinked["subtitle"], "Account not linked")
        self.assertEqual(unlinked["detail"], "Instance credentials active")
        self.assertEqual(unlinked["badge"], "Inactive")


class BundledOnlyKeyTests(TestCase):
    """Nothing in Settings offers, edits or explains a key users cannot get."""

    def setUp(self):
        """Log in an admin, who could manage any other provider's credentials."""
        cache.clear()
        self.user = get_user_model().objects.create_superuser(username="admin", password="x")
        self.client.force_login(self.user)

    def test_the_connection_tab_has_no_credential_forms_or_key_link(self):
        response = self.client.get(reverse("provider_detail", args=["wetrakr"]), {"tab": "connection"})
        soup = BeautifulSoup(response.content, "html.parser")

        self.assertNotContains(response, "Get a key")
        self.assertNotContains(response, "Provider credentials")
        self.assertIsNone(soup.find(attrs={"data-provider-credentials-root": True}))
        self.assertIsNotNone(soup.find(attrs={"data-account-connection-root": True}))

    def test_the_account_card_uses_the_shared_device_link_flow(self):
        account = self.client.get(
            reverse("provider_detail", args=["wetrakr"]), {"tab": "connection"},
        ).context["account_connection"]

        self.assertEqual(account["link"]["label"], "WeTrakr")
        self.assertEqual(account["link"]["start"], reverse("api_provider_connect_start", kwargs={"slug": "wetrakr"}))
        self.assertIsNone(account["device"])

    def test_a_pending_code_resumes_after_a_reload(self):
        WeTrakrAccount.objects.create(
            user=self.user, profile_key="p", client_id="app", device_code=encrypt("dev"), user_code="AB-12",
            verification_url="https://wetrakr.com/device", device_interval=5,
            device_expires_at=timezone.now() + timedelta(minutes=5),
        )

        account = self.client.get(
            reverse("provider_detail", args=["wetrakr"]), {"tab": "connection"},
        ).context["account_connection"]

        self.assertEqual(account["device"]["userCode"], "AB-12")
        self.assertFalse(account["saved"])

    def test_no_route_saves_or_clears_a_key_for_it(self):
        for name in ("save_provider_credential", "clear_provider_credential", "save_personal_credential"):
            with self.subTest(route=name):
                response = self.client.post(reverse(name, args=["wetrakr"]), {"client_id": "mine"})
                self.assertIn(response.status_code, (403, 404))
                if response.status_code == 403:
                    self.fail("An admin was refused, so the spec guard was never reached.")

    def test_it_is_left_out_of_every_credential_listing(self):
        context = metadata_views.metadata_context(self.user)

        slugs = {p["slug"] for group in context["credential_groups"] for p in group["providers"]}
        self.assertNotIn("wetrakr", slugs)
        self.assertIn("tmdb", slugs)
