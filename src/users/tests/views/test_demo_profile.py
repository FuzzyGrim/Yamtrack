
from django.contrib import auth
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class DemoProfileTests(TestCase):
    """Extended profile tests."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "testuser", "password": "testpass123"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_demo_user_cannot_change_username(self):
        """Test that demo users cannot change their username."""
        self.user.is_demo = True
        self.user.save()

        response = self.client.post(
            reverse("account"),
            {
                "username": "new_username",
            },
        )
        self.assertEqual(auth.get_user(self.client).username, "testuser")
        self.assertContains(response, "not allowed for the demo account")

    def test_demo_user_cannot_change_password(self):
        """Test that demo users cannot change their password."""
        self.user.is_demo = True
        self.user.save()

        response = self.client.post(
            reverse("account"),
            {
                "old_password": "testpass123",
                "new_password1": "newpass123",
                "new_password2": "newpass123",
            },
        )
        self.assertTrue(auth.get_user(self.client).check_password("testpass123"))
        self.assertContains(response, "not allowed for the demo account")


