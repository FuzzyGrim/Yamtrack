"""Coverage for the Settings > Metadata page."""

from unittest.mock import patch

from bs4 import BeautifulSoup
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from app.models import InstanceProviderCredential
from app.providers import credentials
from users.metadata_views import metadata_context


@patch("app.providers.credentials._probe", return_value=None)
class MetadataSettingsPageTests(TestCase):
    """Credential probes are stubbed: these tests must not reach a provider."""

    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="member",
            password="12345",
        )
        self.admin = get_user_model().objects.create_superuser(
            username="admin",
            password="12345",
        )

    def tearDown(self):
        cache.clear()

    def test_page_lists_every_registered_provider(self, _probe):
        self.client.force_login(self.user)

        for spec in credentials.REGISTRY.values():
            with self.subTest(provider=spec.slug):
                response = self.client.get(
                    reverse("provider_detail", args=[spec.slug]) + "?tab=connection",
                )
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, spec.label)

    def test_provider_context_scopes_credentials_and_shares_source_settings(
        self, _probe
    ):
        """A provider page exposes its own keys and the real global preference."""
        context = metadata_context(self.user, "tmdb")

        self.assertEqual(
            [
                provider["slug"]
                for group in context["credential_groups"]
                for provider in group["providers"]
            ],
            ["tmdb"],
        )
        tv = next(
            entry
            for entry in context["provider_summary"]
            if entry["media_type"] == "tv"
        )
        tvdb = metadata_context(self.user, "tvdb")
        other_tv = next(
            entry for entry in tvdb["provider_summary"] if entry["media_type"] == "tv"
        )
        self.assertEqual(tv, other_tv)
        self.assertEqual(
            {choice["value"] for choice in tv["choices"]}, {"tmdb", "tvdb"}
        )

    def test_passive_metadata_provider_has_source_card_without_credentials(
        self, _probe
    ):
        """A source without API keys does not need a fictional connection."""
        context = metadata_context(self.user, "openlibrary")

        self.assertIsNone(context["metadata_provider"])
        self.assertFalse(context["credential_groups"])
        self.assertTrue(context["provider_summary"])
        self.assertTrue(
            all(
                any(choice["value"] == "openlibrary" for choice in entry["choices"])
                for entry in context["provider_summary"]
            )
        )

    def test_provider_metadata_renders_shared_source_controls_and_tmdb_proxy(
        self, _probe
    ):
        request = RequestFactory().get(
            reverse("provider_detail", args=["tmdb"]) + "?tab=metadata",
        )
        request.user = self.user
        context = metadata_context(self.user, "tmdb")
        context.update(
            user=self.user, provider={"slug": "tmdb", "metadata_role": "primary"}
        )

        html = render_to_string(
            "users/components/provider_metadata.html",
            context,
            request=request,
        )

        self.assertIn(reverse("set_media_type_provider", args=["tv"]), html)
        self.assertIn(reverse("set_media_type_provider", args=["anime"]), html)
        self.assertIn(reverse("update_tmdb_proxy"), html)
        self.assertIn("?tab=metadata", html)

    @override_settings(HARDCOVER_API="")
    def test_provider_connection_keeps_personal_credentials_private(self, _probe):
        credentials.set_user(
            "hardcover", self.user, {"api_key": "private-provider-key"}
        )
        request = RequestFactory().get(
            reverse("provider_detail", args=["hardcover"]) + "?tab=connection",
        )
        request.user = self.user
        context = metadata_context(self.user, "hardcover")
        context["user"] = self.user

        html = render_to_string(
            "users/components/provider_connection.html",
            context,
            request=request,
        )

        self.assertIn(reverse("save_personal_credential", args=["hardcover"]), html)
        self.assertNotIn("private-provider-key", html)
        self.assertNotIn(reverse("save_personal_credential", args=["tmdb"]), html)

    @override_settings(HARDCOVER_API="")
    def test_member_sees_inline_read_only_instance_and_personal_forms(self, _probe):
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("provider_detail", args=["hardcover"]), {"tab": "connection"}
        )

        soup = BeautifulSoup(response.content, "html.parser")
        instance = soup.find("input", id="hardcover-api_key")
        self.assertTrue(instance.has_attr("disabled"))
        self.assertIsNotNone(soup.find("input", id="hardcover-personal-api_key"))
        self.assertIsNotNone(
            soup.find("button", attrs={"aria-label": "Read-only: explain instance credential access"})
        )
        self.assertIsNotNone(soup.find(attrs={"role": "dialog", "aria-modal": "true"}))
        setup_trigger = soup.find("button", attrs={"x-ref": "setupInfoTrigger"})
        self.assertIsNotNone(setup_trigger)
        self.assertEqual(setup_trigger.get_text(strip=True), "Get a key from Hardcover")
        setup_dialog = soup.find(
            attrs={"role": "dialog", "aria-labelledby": "setup-info-title"}
        )
        self.assertIsNotNone(setup_dialog)
        key_link = setup_dialog.find("a", href="https://hardcover.app/account/api")
        self.assertIsNotNone(key_link)
        self.assertNotContains(response, "Back to Providers")

    @override_settings(HARDCOVER_API="")
    def test_hardcover_connection_page_documents_token_scopes(self, _probe):
        """A read:catalog token is enough for metadata; read:me is only for import."""
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("provider_detail", args=["hardcover"]), {"tab": "connection"}
        )

        self.assertContains(response, "read:catalog")
        self.assertContains(response, "read:me")

    @override_settings(IGDB_ID="environment-id", IGDB_SECRET="")
    def test_superuser_can_edit_unlocked_fields_without_exposing_environment(self, _probe):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("provider_detail", args=["igdb"]), {"tab": "connection"}
        )

        soup = BeautifulSoup(response.content, "html.parser")
        self.assertTrue(soup.find("input", id="igdb-client_id").has_attr("disabled"))
        self.assertFalse(soup.find("input", id="igdb-client_secret").has_attr("disabled"))
        self.assertEqual(soup.find("input", id="igdb-client_secret")["type"], "password")
        self.assertContains(response, "IGDB_ID")
        self.assertContains(response, "Twitch Developer Console")
        self.assertNotContains(response, "environment-id")
        setup_button = soup.find("button", attrs={"x-ref": "setupInfoTrigger"})
        self.assertEqual(setup_button.get_text(strip=True), "Get a key from IGDB")
        setup_dialog = soup.find(attrs={"role": "dialog", "aria-labelledby": "setup-info-title"})
        self.assertIn("Twitch Developer Console", str(setup_dialog))
        self.assertIsNotNone(setup_dialog.find("a", href="https://dev.twitch.tv/console/apps/create"))

    def test_personal_credential_save_returns_to_local_provider_tab(self, _probe):
        self.client.force_login(self.user)
        destination = reverse("provider_detail", args=["hardcover"]) + "?tab=connection"

        response = self.client.post(
            reverse("save_personal_credential", args=["hardcover"]),
            {"api_key": "my-token", "next": destination},
        )

        self.assertRedirects(response, destination, fetch_redirect_response=False)

    def test_personal_credential_save_rejects_external_return_url(self, _probe):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("save_personal_credential", args=["hardcover"]),
            {"api_key": "my-token", "next": "https://example.invalid/"},
        )

        self.assertRedirects(
            response, reverse("metadata_settings"), fetch_redirect_response=False
        )

    @override_settings(HARDCOVER_API="env-token")
    def test_an_env_backed_field_renders_locked(self, _probe):
        self.client.force_login(self.admin)

        response = self.client.get(
            reverse("provider_detail", args=["hardcover"]) + "?tab=connection"
        )

        self.assertContains(response, "HARDCOVER_API")
        self.assertContains(response, "Environment")
        self.assertNotContains(response, "env-token")

    @override_settings(HARDCOVER_API="")
    def test_superuser_can_save_and_clear_an_instance_credential(self, _probe):
        self.client.force_login(self.admin)

        self.client.post(
            reverse("save_provider_credential", args=["hardcover"]),
            {"api_key": "stored-token"},
        )
        self.assertEqual(credentials.get("hardcover", "api_key"), "stored-token")

        self.client.post(reverse("clear_provider_credential", args=["hardcover"]))
        self.assertEqual(credentials.get("hardcover", "api_key"), "")

    @override_settings(HARDCOVER_API="")
    def test_saved_instance_credential_is_masked_in_the_settings_page(self, _probe):
        credentials.set_instance("hardcover", {"api_key": "stored-token"})
        self.client.force_login(self.admin)

        response = self.client.get(
            reverse("provider_detail", args=["hardcover"]) + "?tab=connection"
        )

        self.assertContains(response, "••••••••oken")
        self.assertNotContains(response, "stored-token")

    @override_settings(HARDCOVER_API="")
    def test_personal_credential_is_not_rendered_in_the_settings_page(self, _probe):
        credentials.set_user("hardcover", self.user, {"api_key": "personal-token"})
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("provider_detail", args=["hardcover"]) + "?tab=connection"
        )

        self.assertNotContains(response, 'value="personal-token"')

    @override_settings(
        BGG_API_TOKEN="bundled-bgg-AB12",
        COMICVINE_API="bundled-comic-CD34",
        SHARED_DEFAULT_CREDENTIALS={"BGG_API_TOKEN": "bundled-bgg-AB12", "COMICVINE_API": "bundled-comic-CD34"},
    )
    def test_bundled_defaults_are_identified_and_masked_for_members_and_admins(self, _probe):
        for user in (self.user, self.admin):
            self.client.force_login(user)
            for slug, field, secret in (("bgg", "token", "bundled-bgg-AB12"), ("comicvine", "api_key", "bundled-comic-CD34")):
                with self.subTest(user=user.pk, slug=slug):
                    response = self.client.get(reverse("provider_detail", args=[slug]), {"tab": "connection"})
                    self.assertContains(response, "Bundled default")
                    self.assertNotContains(response, secret)
                    soup = BeautifulSoup(response.content, "html.parser")
                    input_field = soup.select_one(f"#{slug}-{field}")
                    self.assertEqual(input_field.get("placeholder") if user.is_superuser else input_field.get("value"), "••••••••" + secret[-4:])
                    self.assertNotContains(response, "Remove stored credentials")
            directory = self.client.get(reverse("providers"), {"format": "json", "capability": "connection"}).json()
            items = {item["slug"]: item for item in directory["items"]}
            for slug in ("bgg", "comicvine"):
                self.assertEqual(items[slug]["status"], {"tone": "success", "label": "Bundled credentials available"})

    @override_settings(HARDCOVER_API="")
    def test_personal_key_hint_shows_a_masked_preview_only(self, _probe):
        secret = "personal-credential-X9K2"
        credentials.set_user("hardcover", self.user, {"api_key": secret})
        self.client.force_login(self.user)
        response = self.client.get(reverse("provider_detail", args=["hardcover"]), {"tab": "connection"})
        soup = BeautifulSoup(response.content, "html.parser")
        self.assertIn("••••••••X9K2", soup.select_one("#hardcover-personal-api_key")["placeholder"])
        self.assertNotContains(response, secret)

    @override_settings(HARDCOVER_API="")
    def test_a_normal_user_cannot_write_instance_credentials(self, _probe):
        self.client.force_login(self.user)

        save = self.client.post(
            reverse("save_provider_credential", args=["hardcover"]),
            {"api_key": "stored-token"},
        )
        clear = self.client.post(
            reverse("clear_provider_credential", args=["hardcover"]),
        )

        self.assertEqual(save.status_code, 403)
        self.assertEqual(clear.status_code, 403)
        self.assertFalse(InstanceProviderCredential.objects.exists())

    @override_settings(HARDCOVER_API="env-token")
    def test_an_env_locked_field_cannot_be_overwritten_by_a_post(self, _probe):
        self.client.force_login(self.admin)

        self.client.post(
            reverse("save_provider_credential", args=["hardcover"]),
            {"api_key": "sneaky-token"},
        )

        self.assertFalse(InstanceProviderCredential.objects.exists())

    def test_a_normal_user_can_save_a_personal_key(self, _probe):
        self.client.force_login(self.user)

        self.client.post(
            reverse("save_personal_credential", args=["hardcover"]),
            {"api_key": "my-token"},
        )

        self.assertEqual(
            credentials.get("hardcover", "api_key", user=self.user),
            "my-token",
        )

    @override_settings(IGDB_SECRET="")
    def test_a_personal_key_marks_the_provider_configured_in_the_summary(self, _probe):
        credentials.set_user(
            "igdb",
            self.user,
            {"client_id": "personal-id", "client_secret": "personal-secret"},
        )
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("provider_detail", args=["igdb"]) + "?tab=metadata"
        )

        game = next(
            entry
            for entry in response.context["provider_summary"]
            if entry["media_type"] == "game"
        )
        igdb = next(choice for choice in game["choices"] if choice["value"] == "igdb")
        self.assertTrue(igdb["configured"])

    def test_an_unknown_provider_slug_is_a_404(self, _probe):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("save_personal_credential", args=["nosuchprovider"]),
            {"api_key": "my-token"},
        )

        self.assertEqual(response.status_code, 404)

    @override_settings(HARDCOVER_API="")
    def test_a_failing_validator_blocks_the_save(self, _probe):
        spec = credentials.REGISTRY["hardcover"]
        patched = {**credentials.REGISTRY}
        patched["hardcover"] = type(spec)(
            **{
                **{f: getattr(spec, f) for f in spec.__slots__},
                "validator": lambda values: "that key was rejected",
            },
        )
        self.client.force_login(self.admin)

        with self.settings():
            original = dict(credentials.REGISTRY)
            credentials.REGISTRY.update(patched)
            try:
                response = self.client.post(
                    reverse("save_provider_credential", args=["hardcover"]),
                    {"api_key": "bad-token"},
                    follow=True,
                )
            finally:
                credentials.REGISTRY.clear()
                credentials.REGISTRY.update(original)

        self.assertContains(response, "that key was rejected")
        self.assertFalse(InstanceProviderCredential.objects.exists())

    def test_a_non_superuser_is_told_how_to_promote_their_account(self, _probe):
        """A silent read-only form is what made this page look broken."""
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("provider_detail", args=["hardcover"]) + "?tab=connection"
        )

        self.assertContains(response, "You are not a superuser")
        self.assertContains(response, "promote_superuser member")

    def test_every_provider_offers_a_personal_key(self, _probe):
        """A 'Shared default' pill reads as an invitation, so it must hold everywhere."""
        self.client.force_login(self.user)

        context = metadata_context(self.user)
        providers = [
            provider
            for group in context["credential_groups"]
            for provider in group["providers"]
        ]

        # A key that belongs to Floppy (bundled_only) is not offered to anyone.
        managed = [spec for spec in credentials.REGISTRY.values() if not spec.bundled_only]
        self.assertEqual(len(providers), len(managed))
        self.assertTrue(all(provider["user_scope"] for provider in providers))

    def test_groups_split_metadata_from_account_credentials(self, _probe):
        self.client.force_login(self.user)

        context = metadata_context(self.user)
        sections = {
            group["key"]: {p["slug"] for p in group["providers"]}
            for group in context["credential_groups"]
        }

        self.assertIn("tmdb", sections["metadata"])
        self.assertIn("comicvine", sections["metadata"])
        self.assertIn("steam", sections["accounts"])
        self.assertIn("trakt", sections["accounts"])

    def test_a_superuser_sees_no_promotion_banner(self, _probe):
        self.client.force_login(self.admin)

        response = self.client.get(
            reverse("provider_detail", args=["hardcover"]) + "?tab=connection"
        )

        self.assertNotContains(response, "You are not a superuser")
        self.assertNotContains(response, "promote_superuser")

    @patch("users.metadata_views.preflight.in_container", return_value=False)
    def test_a_source_install_is_told_to_run_manage_py(self, _in_container, _probe):
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("provider_detail", args=["hardcover"]) + "?tab=connection"
        )

        self.assertContains(response, "python src/manage.py promote_superuser member")
        self.assertNotContains(response, "docker exec")

    @patch.dict("os.environ", {"HOST_CONTAINERNAME": "yamtrack"})
    @patch("users.metadata_views.preflight.in_container", return_value=True)
    def test_a_container_install_is_told_to_use_docker_exec(
        self, _in_container, _probe
    ):
        """Podman counts too: preflight.in_container checks both markers."""
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("provider_detail", args=["hardcover"]) + "?tab=connection"
        )

        self.assertContains(
            response,
            "docker exec -it yamtrack python manage.py promote_superuser member",
        )
        self.assertNotContains(response, "python src/manage.py")

    def test_the_page_leaks_no_template_source(self, _probe):
        """Django's {# #} is single-line only; a multi-line one renders verbatim."""
        self.client.force_login(self.admin)

        page = self.client.get(
            reverse("provider_detail", args=["hardcover"]) + "?tab=connection"
        ).content.decode()

        self.assertNotIn("{#", page)
        self.assertNotIn("{%", page)
