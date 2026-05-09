from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse


class TraktOAuthViewTests(TestCase):
    """Test Trakt OAuth redirect URL handling."""

    def setUp(self):
        """Create user for the tests."""
        credentials = {"username": "testuser", "password": "testpass123"}
        self.user = get_user_model().objects.create_user(**credentials)
        self.client.login(**credentials)

    @override_settings(URLS=["https://yamtrack.example.com:8924"], TRAKT_API="client")
    def test_trakt_oauth_uses_configured_public_url(self):
        """Test Trakt authorization uses the configured public URL."""
        response = self.client.post(
            reverse("trakt_oauth"),
            {"mode": "new", "frequency": "once", "time": "14:30"},
        )

        self.assertEqual(response.status_code, 302)
        redirect = urlparse(response["Location"])
        query = parse_qs(redirect.query)
        self.assertEqual(redirect.scheme, "https")
        self.assertEqual(redirect.netloc, "trakt.tv")
        self.assertEqual(query["client_id"], ["client"])
        self.assertEqual(
            query["redirect_uri"],
            ["https://yamtrack.example.com:8924/import/trakt/private"],
        )

        state = self.client.session[query["state"][0]]
        self.assertEqual(
            state["redirect_uri"],
            "https://yamtrack.example.com:8924/import/trakt/private",
        )

    @override_settings(URLS=["https://yamtrack.example.com:8924"])
    @patch("integrations.views.tasks.import_trakt.delay")
    @patch("integrations.views.trakt.handle_oauth_callback")
    def test_trakt_callback_reuses_stored_redirect_uri(
        self,
        mock_oauth_callback,
        mock_import_trakt,
    ):
        """Test the token exchange and import task reuse the original redirect URI."""
        redirect_uri = "https://yamtrack.example.com:8924/import/trakt/private"
        session = self.client.session
        session["state-token"] = {
            "mode": "new",
            "frequency": "once",
            "time": "14:30",
            "redirect_uri": redirect_uri,
        }
        session.save()
        mock_oauth_callback.return_value = {
            "refresh_token": "refresh-token",
            "username": "trakt-user",
        }

        response = self.client.get(
            reverse("import_trakt_private"),
            {"code": "code", "state": "state-token"},
        )

        self.assertRedirects(response, reverse("import_data"))
        mock_oauth_callback.assert_called_once()
        self.assertEqual(
            mock_oauth_callback.call_args.kwargs["redirect_uri"],
            redirect_uri,
        )
        mock_import_trakt.assert_called_once()
        self.assertEqual(
            mock_import_trakt.call_args.kwargs["redirect_uri"],
            redirect_uri,
        )

    @patch("integrations.views.trakt.handle_oauth_callback")
    def test_trakt_callback_rejects_missing_state(self, mock_oauth_callback):
        """Test missing OAuth state is handled without a server error."""
        response = self.client.get(
            reverse("import_trakt_private"),
            {"code": "code"},
        )

        self.assertRedirects(response, reverse("import_data"))
        mock_oauth_callback.assert_not_called()
