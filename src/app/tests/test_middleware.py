from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

UserModel = get_user_model()


class AutoLoginMiddlewareTest(TestCase):
    """Test cases for AutoLoginMiddleware."""

    def setUp(self):
        """Create test users."""
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

    def test_env_var_unset(self):
        """Test that no auto-login occurs when YAMTRACK_AUTO_LOGIN_USERNAME is unset."""
        response = self.client.get("/")

        # expect redirect to login page
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/accounts/login/?next=/")

    @override_settings(YAMTRACK_AUTO_LOGIN_USERNAME="active_user")
    def test_existing_active_user(self):
        """Test that auto-login works with an existing active user."""
        response = self.client.get("/")

        # expect successful login
        self.assertEqual(response.status_code, 200)

    @override_settings(YAMTRACK_AUTO_LOGIN_USERNAME="missing_user")
    def test_missing_user(self):
        """Test that no auto-login occurs with a missing user."""
        response = self.client.get("/")

        # expect redirect to login page
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/accounts/login/?next=/")

    @override_settings(YAMTRACK_AUTO_LOGIN_USERNAME="inactive_user")
    def test_inactive_user(self):
        """Test that no auto-login occurs with an inactive user."""
        response = self.client.get("/")

        # expect redirect to login page
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/accounts/login/?next=/")
