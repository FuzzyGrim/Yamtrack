import base64
import hashlib
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from integrations.imports import trakt


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
        self.assertEqual(redirect.netloc, "auth.trakt.tv")
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

    @override_settings(TRAKT_API="client")
    def test_trakt_oauth_sends_pkce_challenge(self):
        """The authorization request carries an S256 challenge for a stored verifier."""
        response = self.client.post(
            reverse("trakt_oauth"),
            {"mode": "new", "frequency": "once", "time": "14:30"},
        )

        query = parse_qs(urlparse(response["Location"]).query)
        self.assertEqual(query["code_challenge_method"], ["S256"])
        verifier = self.client.session[query["state"][0]]["code_verifier"]
        # RFC 7636: 43 to 128 characters
        self.assertGreaterEqual(len(verifier), 43)
        self.assertLessEqual(len(verifier), 128)
        expected = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        self.assertEqual(query["code_challenge"], [expected])

    def test_trakt_oauth_uses_a_new_verifier_each_time(self):
        """Every sign-in gets its own PKCE verifier."""
        verifiers = set()
        for _ in range(2):
            response = self.client.post(
                reverse("trakt_oauth"),
                {"mode": "new", "frequency": "once", "time": "14:30"},
            )
            state_token = parse_qs(urlparse(response["Location"]).query)["state"][0]
            verifiers.add(self.client.session[state_token]["code_verifier"])

        self.assertEqual(len(verifiers), 2)

    @patch("integrations.views.tasks.import_trakt.delay")
    @patch("integrations.views.trakt.handle_oauth_callback")
    def test_trakt_callback_passes_stored_code_verifier(
        self,
        mock_oauth_callback,
        _mock_import_trakt,
    ):
        """The token exchange receives the verifier stored when sign-in started."""
        session = self.client.session
        session["state-token"] = {
            "mode": "new",
            "frequency": "once",
            "time": "14:30",
            "redirect_uri": "https://yamtrack.example.com/import/trakt/private",
            "code_verifier": "stored-verifier",
        }
        session.save()
        mock_oauth_callback.return_value = {
            "refresh_token": "refresh-token",
            "username": "trakt-user",
        }

        self.client.get(
            reverse("import_trakt_private"),
            {"code": "code", "state": "state-token"},
        )

        self.assertEqual(
            mock_oauth_callback.call_args.kwargs["code_verifier"],
            "stored-verifier",
        )


class TraktTokenExchangeTests(TestCase):
    """Test the parameters sent to Trakt's token endpoint."""

    def exchange(self, code_verifier):
        """Run the token exchange and return the params sent to Trakt."""
        request = RequestFactory().get("/import/trakt/private", {"code": "code"})
        with (
            patch(
                "integrations.imports.trakt.app.providers.services.api_request",
                return_value={"refresh_token": "refresh", "access_token": "access"},
            ) as mock_request,
            patch(
                "integrations.imports.trakt.get_username_from_oauth",
                return_value="trakt-user",
            ),
        ):
            result = trakt.handle_oauth_callback(
                request,
                redirect_uri="https://yamtrack.example.com/import/trakt/private",
                code_verifier=code_verifier,
            )
        self.assertEqual(result["username"], "trakt-user")
        return mock_request.call_args.kwargs["params"]

    def test_sends_code_verifier(self):
        """The PKCE verifier is sent with the authorization code."""
        params = self.exchange("stored-verifier")
        self.assertEqual(params["code_verifier"], "stored-verifier")
        self.assertEqual(params["grant_type"], "authorization_code")

    @override_settings(TRAKT_API_SECRET="")
    def test_omits_client_secret_when_not_configured(self):
        """PKCE apps have no client secret, so none is sent."""
        params = self.exchange("stored-verifier")
        self.assertNotIn("client_secret", params)

    @override_settings(TRAKT_API_SECRET="secret")  # noqa: S106
    def test_sends_configured_client_secret(self):
        """Apps that were issued a secret keep sending it."""
        params = self.exchange("stored-verifier")
        self.assertEqual(params["client_secret"], "secret")

    @override_settings(TRAKT_API_SECRET="")
    def test_refresh_omits_client_secret_when_not_configured(self):
        """Scheduled imports renew their token without a secret for PKCE apps."""
        with (
            patch("integrations.imports.trakt.helpers.decrypt", return_value="r"),
            patch(
                "integrations.imports.trakt.app.providers.services.api_request",
                return_value={"refresh_token": "new", "access_token": "access"},
            ) as mock_request,
        ):
            trakt.get_access_token("encrypted", redirect_uri="https://x/cb")
        params = mock_request.call_args.kwargs["params"]
        self.assertNotIn("client_secret", params)
        self.assertEqual(params["grant_type"], "refresh_token")

    def test_omits_code_verifier_without_one(self):
        """A sign-in started before PKCE was added still exchanges its code."""
        params = self.exchange(None)
        self.assertNotIn("code_verifier", params)


class TraktExportUploadViewTests(TestCase):
    """Test the Trakt export archive upload view."""

    def setUp(self):
        """Create user for the tests."""
        credentials = {"username": "testuser", "password": "testpass123"}
        self.user = get_user_model().objects.create_user(**credentials)
        self.client.login(**credentials)

    @patch("integrations.views.tasks.import_trakt.delay")
    def test_upload_queues_task_with_archive(self, mock_import_trakt):
        """The uploaded archive is forwarded to the import task."""
        upload = SimpleUploadedFile(
            "trakt-export.zip",
            b"archive-contents",
            content_type="application/zip",
        )

        # The upload is closed once the response finishes, so read it while queueing.
        queued = {}
        mock_import_trakt.side_effect = lambda **kwargs: queued.update(
            kwargs,
            contents=kwargs["file"].read(),
        )

        response = self.client.post(
            reverse("import_trakt_export"),
            {"mode": "new", "trakt_export_zip": upload},
        )

        self.assertEqual(response.status_code, 302)
        mock_import_trakt.assert_called_once()
        self.assertEqual(queued["user_id"], self.user.id)
        self.assertEqual(queued["mode"], "new")
        self.assertEqual(queued["contents"], b"archive-contents")

    @patch("integrations.views.tasks.import_trakt.delay")
    def test_missing_file_is_rejected(self, mock_import_trakt):
        """Posting without a file shows an error instead of queueing a task."""
        response = self.client.post(reverse("import_trakt_export"), {"mode": "new"})

        self.assertEqual(response.status_code, 302)
        mock_import_trakt.assert_not_called()
