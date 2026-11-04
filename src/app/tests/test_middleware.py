from django.contrib.auth import get_user_model
from django.contrib.auth.middleware import AuthenticationMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import translation

from app.middleware import AutoLoginMiddleware

UserModel = get_user_model()


class AutoLoginMiddlewareTest(TestCase):
    """Test cases for AutoLoginMiddleware."""

    def setUp(self):
        """Create test users."""
        self.factory = RequestFactory()
        self.existing_active_user = UserModel.objects.create_user(
            username="active_user",
            password="active_user_password",  # noqa: S106
            is_active=True,
        )
        self.existing_inactive_user = UserModel.objects.create_user(
            username="inactive_user",
            password="inactive_user_password",  # noqa: S106
            is_active=False,
        )

    def get_request(self):
        """Return a request with session and user middleware applied."""
        request = self.factory.get("/")
        SessionMiddleware(lambda _request: None).process_request(request)
        AuthenticationMiddleware(lambda _request: None).process_request(request)
        return request

    def run_middleware(self, request):
        """Run auto-login middleware against the request."""
        middleware = AutoLoginMiddleware(lambda _request: None)
        middleware(request)

    @override_settings(YAMTRACK_AUTO_LOGIN_USERNAME=None)
    def test_env_var_unset(self):
        """Test that no auto-login occurs when YAMTRACK_AUTO_LOGIN_USERNAME is unset."""
        request = self.get_request()

        self.run_middleware(request)

        self.assertFalse(request.user.is_authenticated)

    @override_settings(YAMTRACK_AUTO_LOGIN_USERNAME="active_user")
    def test_existing_active_user(self):
        """Test that auto-login works with an existing active user."""
        request = self.get_request()

        self.run_middleware(request)

        self.assertTrue(request.user.is_authenticated)
        self.assertEqual(request.user, self.existing_active_user)

    @override_settings(YAMTRACK_AUTO_LOGIN_USERNAME="missing_user")
    def test_missing_user(self):
        """Test that no auto-login occurs with a missing user."""
        request = self.get_request()

        self.run_middleware(request)

        self.assertFalse(request.user.is_authenticated)

    @override_settings(YAMTRACK_AUTO_LOGIN_USERNAME="inactive_user")
    def test_inactive_user(self):
        """Test that no auto-login occurs with an inactive user."""
        request = self.get_request()

        self.run_middleware(request)

        self.assertFalse(request.user.is_authenticated)


class UserLanguageMiddlewareTest(TestCase):
    """Test cases for UserLanguageMiddleware."""

    def setUp(self):
        """Create a test user."""
        # Requests activate a language that would otherwise leak into later tests
        self.addCleanup(translation.deactivate)
        self.credentials = {"username": "test", "password": "12345"}
        self.user = UserModel.objects.create_user(**self.credentials)

    def test_authenticated_user_language(self):
        """Test the user's saved language is used for authenticated users."""
        self.user.language = "it-it"
        self.user.save()
        self.client.login(**self.credentials)

        response = self.client.get(reverse("lists"), headers={"accept-language": "en"})

        self.assertEqual(response.headers["Content-Language"], "it-it")

    def test_authenticated_user_ignores_accept_language(self):
        """Test the browser language doesn't override the saved language."""
        self.client.login(**self.credentials)

        response = self.client.get(reverse("lists"), headers={"accept-language": "it"})

        self.assertEqual(response.headers["Content-Language"], "en")

    def test_anonymous_user_accept_language(self):
        """Test anonymous users still get the browser language."""
        response = self.client.get(
            reverse("account_login"), headers={"accept-language": "it"}
        )

        self.assertEqual(response.headers["Content-Language"], "it-it")
