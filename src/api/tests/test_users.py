from unittest.mock import patch

from .base import YamtrackApiTestCase

REGIONS = [("", "Disabled"), ("IT", "Italy"), ("US", "United States")]


class MeTests(YamtrackApiTestCase):
    """Tests for the current user endpoints."""

    def test_me_get(self):
        """The profile includes the preferences and never the token."""
        response = self.call_api("get", "api_me", headers=self.auth_headers)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["username"], "api-test-user1")
        self.assertTrue(payload["profile_private"])
        self.assertIn("movie", payload["enabled_media_types"])
        self.assertEqual(payload["preferences"]["tv_layout"], "grid")
        self.assertNotIn("token", payload)

    def test_me_patch(self):
        """The username and the visibility can be updated."""
        response = self.call_api(
            "patch",
            "api_me",
            payload={"username": "renamed", "profile_private": False},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        self.user1.refresh_from_db()
        self.assertEqual(self.user1.username, "renamed")
        self.assertFalse(self.user1.profile_private)

    def test_me_patch_duplicate_username(self):
        """A taken username is rejected."""
        response = self.call_api(
            "patch",
            "api_me",
            payload={"username": self.user2.username},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("username", response.json()["errors"])

    def test_me_patch_demo_user(self):
        """Demo accounts are view-only."""
        self.user1.is_demo = True
        self.user1.save()

        response = self.call_api(
            "patch",
            "api_me",
            payload={"profile_private": False},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 403)


class PreferencesTests(YamtrackApiTestCase):
    """Tests for the preferences endpoints."""

    def test_preferences_get(self):
        """All the preferences are returned."""
        response = self.call_api("get", "api_me_preferences", headers=self.auth_headers)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["anime_layout"], "table")
        self.assertEqual(payload["date_format"], "Y-m-d")
        self.assertEqual(payload["watch_provider_region"], "UNSET")

    def test_preferences_patch_is_partial(self):
        """Only the sent fields change."""
        response = self.call_api(
            "patch",
            "api_me_preferences",
            payload={"tv_layout": "table", "movie_enabled": False},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        self.user1.refresh_from_db()
        self.assertEqual(self.user1.tv_layout, "table")
        self.assertFalse(self.user1.movie_enabled)
        self.assertEqual(self.user1.anime_layout, "table")
        self.assertTrue(self.user1.tv_enabled)

    def test_preferences_patch_invalid_choice(self):
        """Invalid values are rejected instead of ignored."""
        response = self.call_api(
            "patch",
            "api_me_preferences",
            payload={"tv_layout": "carousel"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("tv_layout", response.json()["errors"])

    @patch("app.providers.tmdb.watch_provider_regions", return_value=REGIONS)
    def test_preferences_watch_provider_region(self, _regions):
        """The region has to be one of the TMDB ones."""
        response = self.call_api(
            "patch",
            "api_me_preferences",
            payload={"watch_provider_region": "IT"},
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, 200, response.json())
        self.user1.refresh_from_db()
        self.assertEqual(self.user1.watch_provider_region, "IT")

        response = self.call_api(
            "patch",
            "api_me_preferences",
            payload={"watch_provider_region": "ZZ"},
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, 400)

        response = self.call_api(
            "patch",
            "api_me_preferences",
            payload={"watch_provider_region": ""},
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, 200)

    def test_preferences_patch_demo_user(self):
        """Demo accounts are view-only."""
        self.user1.is_demo = True
        self.user1.save()

        response = self.call_api(
            "patch",
            "api_me_preferences",
            payload={"tv_layout": "table"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 403)
