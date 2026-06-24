import io
from unittest.mock import patch

from django.contrib import auth
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from users.forms import UserUpdateForm


class Profile(TestCase):
    """Test profile page."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_change_username(self):
        """Test changing username."""
        self.assertEqual(auth.get_user(self.client).username, "test")
        self.client.post(
            reverse("account"),
            {
                "username": "new_test",
            },
        )
        self.assertEqual(auth.get_user(self.client).username, "new_test")

    def test_change_password(self):
        """Test changing password."""
        self.assertEqual(auth.get_user(self.client).check_password("12345"), True)
        self.client.post(
            reverse("account"),
            {
                "old_password": "12345",
                "new_password1": "*FNoZN64",
                "new_password2": "*FNoZN64",
            },
        )
        self.assertEqual(auth.get_user(self.client).check_password("*FNoZN64"), True)

    def test_invalid_password_change(self):
        """Test password change with incorrect old password."""
        response = self.client.post(
            reverse("account"),
            {
                "old_password": "wrongpass",
                "new_password1": "newpass123",
                "new_password2": "newpass123",
            },
        )
        self.assertTrue(auth.get_user(self.client).check_password("12345"))
        self.assertContains(response, "Your old password was entered incorrectly")

    @patch.object(UserUpdateForm, "save", side_effect=PermissionError)
    def test_profile_picture_upload_permission_error(self, _mock_save):
        """Show a form error instead of 500 when media storage is not writable."""
        image = Image.new("RGB", (10, 10), color="red")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        upload = SimpleUploadedFile(
            "avatar.png",
            buffer.getvalue(),
            content_type="image/png",
        )

        response = self.client.post(
            reverse("account"),
            {
                "username": self.user.username,
                "bio": "",
                "pronouns": "",
                "location": "",
                "profile_picture": upload,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Could not save your profile picture. Please try again later.",
        )
