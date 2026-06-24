import shutil
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from social.models import SocialAuditLog
from users.models import DateFormatChoices, QuickWatchDateChoices


class ApiProfileSettingsTests(TestCase):
    """Current-user settings API contracts for mobile clients."""

    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media_root)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(lambda: shutil.rmtree(self.media_root, ignore_errors=True))
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(
            username="settings",
            email="settings@example.com",
            password="strong-password-123",
        )
        self.client.force_authenticate(self.user)

    def test_patch_profile_fields_and_read_back(self):
        response = self.client.patch(
            "/api/v1/me/",
            {
                "username": "settings2",
                "display_name": "Settings User",
                "bio": "Tracking everything.",
                "pronouns": "they/them",
                "location": "Portland",
                "is_private": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["username"], "settings2")
        self.assertEqual(response.data["display_name"], "Settings User")
        self.assertEqual(response.data["bio"], "Tracking everything.")
        self.assertEqual(response.data["pronouns"], "they/them")
        self.assertEqual(response.data["location"], "Portland")
        self.assertTrue(response.data["is_private"])
        self.user.refresh_from_db()
        self.assertTrue(self.user.profile_private)
        self.assertTrue(
            SocialAuditLog.objects.filter(
                actor=self.user,
                action="profile_visibility_update",
                target_user=self.user,
            ).exists(),
        )

        read_back = self.client.get("/api/v1/me/")
        self.assertEqual(read_back.data["username"], "settings2")
        self.assertTrue(read_back.data["is_private"])

    def test_username_conflict_returns_400(self):
        get_user_model().objects.create_user(username="taken", password="strong-password-123")

        response = self.client.patch("/api/v1/me/", {"username": "taken"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("username", response.data)

    def test_demo_user_cannot_change_username(self):
        self.user.is_demo = True
        self.user.save(update_fields=["is_demo"])

        response = self.client.patch("/api/v1/me/", {"username": "demo2"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("username", response.data)

    def test_avatar_post_and_delete(self):
        upload = SimpleUploadedFile(
            "avatar.png",
            b"not-really-a-png-but-storage-does-not-care",
            content_type="image/png",
        )

        posted = self.client.post("/api/v1/me/avatar/", {"avatar": upload}, format="multipart")

        self.assertEqual(posted.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(posted.data["avatar_url"])
        self.user.refresh_from_db()
        avatar_path = self.user.profile_picture.path
        self.assertTrue(Path(avatar_path).exists())

        deleted = self.client.delete("/api/v1/me/avatar/")

        self.assertEqual(deleted.status_code, status.HTTP_200_OK)
        self.assertIsNone(deleted.data["avatar_url"])
        self.user.refresh_from_db()
        self.assertFalse(self.user.profile_picture)
        self.assertFalse(Path(avatar_path).exists())

    def test_avatar_rejects_missing_bad_type_and_large_file(self):
        missing = self.client.post("/api/v1/me/avatar/", {}, format="multipart")
        bad_type = self.client.post(
            "/api/v1/me/avatar/",
            {"avatar": SimpleUploadedFile("avatar.gif", b"gif", content_type="image/gif")},
            format="multipart",
        )
        large = self.client.post(
            "/api/v1/me/avatar/",
            {"avatar": SimpleUploadedFile("avatar.png", b"x" * (5 * 1024 * 1024 + 1), content_type="image/png")},
            format="multipart",
        )

        self.assertEqual(missing.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(bad_type.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(large.status_code, status.HTTP_400_BAD_REQUEST)

    def test_preferences_patch_valid_and_invalid_enum_values(self):
        valid = self.client.patch(
            "/api/v1/me/preferences/",
            {
                "enabled_media_types": ["movie", "book"],
                "date_format": DateFormatChoices.US,
                "quick_watch_date": QuickWatchDateChoices.NO_DATE,
                "release_notifications_enabled": False,
            },
            format="json",
        )

        self.assertEqual(valid.status_code, status.HTTP_200_OK)
        self.assertEqual(valid.data["enabled_media_types"], ["movie", "book"])
        self.assertEqual(valid.data["date_format"], DateFormatChoices.US)
        self.assertEqual(valid.data["quick_watch_date"], QuickWatchDateChoices.NO_DATE)
        self.assertFalse(valid.data["release_notifications_enabled"])

        invalid = self.client.patch("/api/v1/me/preferences/", {"date_format": "bad"}, format="json")

        self.assertEqual(invalid.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("date_format", invalid.data)

    def test_preferences_require_one_enabled_media_type(self):
        response = self.client.patch(
            "/api/v1/me/preferences/",
            {"enabled_media_types": []},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("enabled_media_types", response.data)

    def test_password_change_success_and_failure(self):
        failed = self.client.post(
            "/api/v1/me/password/",
            {
                "old_password": "wrong",
                "new_password": "new-strong-password-456",
                "new_password_confirm": "new-strong-password-456",
            },
            format="json",
        )
        self.assertEqual(failed.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("old_password", failed.data)

        changed = self.client.post(
            "/api/v1/me/password/",
            {
                "old_password": "strong-password-123",
                "new_password": "new-strong-password-456",
                "new_password_confirm": "new-strong-password-456",
            },
            format="json",
        )

        self.assertEqual(changed.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("new-strong-password-456"))

    def test_meta_includes_preference_choices(self):
        response = self.client.get("/api/v1/meta/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn({"value": DateFormatChoices.ISO, "label": "2026-01-18 (ISO)"}, response.data["date_formats"])
        self.assertIn("time_formats", response.data)
        self.assertIn("week_start_days", response.data)
        self.assertIn("quick_watch_dates", response.data)
