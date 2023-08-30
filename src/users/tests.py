from django.contrib import auth
from django.test import TestCase
from django.urls import reverse

from users.models import User


class Profile(TestCase):
    """Test profile page."""

    def setUp(self: "Profile") -> None:
        """Create user for the tests."""

        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_change_username(self: "Profile") -> None:
        """Test changing username."""

        self.assertEqual(auth.get_user(self.client).username, "test")
        self.client.post(
            reverse("profile"),
            {
                "username": "new_test",
            },
        )
        self.assertEqual(auth.get_user(self.client).username, "new_test")

    def test_change_password(self: "Profile") -> None:
        """Test changing password."""

        self.assertEqual(auth.get_user(self.client).check_password("12345"), True)
        self.client.post(
            reverse("profile"),
            {
                "old_password": "12345",
                "new_password1": "*FNoZN64",
                "new_password2": "*FNoZN64",
            },
        )
        self.assertEqual(auth.get_user(self.client).check_password("*FNoZN64"), True)
