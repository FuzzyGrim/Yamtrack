from unittest.mock import patch

from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.test import Client

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


class TokenAndPasswordTests(YamtrackApiTestCase):
    """Tests for the token, password and search cache endpoints."""

    def test_regenerate_token(self):
        """The old token stops working and the new one works."""
        old_token = self.user1.token

        response = self.call_api("post", "api_me_token", headers=self.auth_headers)

        self.assertEqual(response.status_code, 200)
        new_token = response.json()["token"]
        self.assertNotEqual(new_token, old_token)

        response = self.call_api("get", "api_me", headers=self.auth_headers)
        self.assertEqual(response.status_code, 401)

        response = self.call_api(
            "get",
            "api_me",
            headers={"HTTP_X_API_KEY": new_token},
        )
        self.assertEqual(response.status_code, 200)

    def test_change_password(self):
        """The password changes, the token stays and web sessions are revoked."""
        self.user1.set_password("old-pass-12345")
        self.user1.save()
        web_client = Client()
        self.assertTrue(
            web_client.login(username="api-test-user1", password="old-pass-12345")  # noqa: S106
        )
        self.assertEqual(Session.objects.count(), 1)
        token = self.user1.token

        response = self.call_api(
            "post",
            "api_me_password",
            payload={
                "old_password": "old-pass-12345",
                "new_password": "n3w-str0ng-pass",
            },
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        self.user1.refresh_from_db()
        self.assertTrue(self.user1.check_password("n3w-str0ng-pass"))
        self.assertEqual(self.user1.token, token)
        self.assertEqual(Session.objects.count(), 0)

    def test_change_password_wrong_old_password(self):
        """The current password is required."""
        response = self.call_api(
            "post",
            "api_me_password",
            payload={"old_password": "nope", "new_password": "n3w-str0ng-pass"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("old_password", response.json()["errors"])

    def test_change_password_weak_password(self):
        """The password validators are applied."""
        self.user1.set_password("old-pass-12345")
        self.user1.save()

        response = self.call_api(
            "post",
            "api_me_password",
            payload={"old_password": "old-pass-12345", "new_password": "123"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("new_password", response.json()["errors"])

    def test_change_password_demo_user(self):
        """Demo accounts are view-only."""
        self.user1.is_demo = True
        self.user1.save()

        response = self.call_api(
            "post",
            "api_me_password",
            payload={"old_password": "a", "new_password": "b"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 403)

    def test_clear_search_cache(self):
        """The cached searches are deleted."""
        cache.set("search_movie_test", "x")
        cache.set("other_key", "y")

        response = self.call_api(
            "delete",
            "api_me_search_cache",
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"deleted": 1})
        self.assertIsNone(cache.get("search_movie_test"))
        self.assertEqual(cache.get("other_key"), "y")


class PublicProfileTests(YamtrackApiTestCase):
    """Tests for the public profile endpoints."""

    def test_private_profile_is_not_found(self):
        """Private profiles look like missing users."""
        for url_name, args in (
            ("api_user_profile", ("api-test-user2",)),
            ("api_user_media_type_list", ("api-test-user2", "movie")),
        ):
            with self.subTest(url_name=url_name):
                response = self.call_api("get", url_name, args=args)
                self.assertEqual(response.status_code, 404)

                response = self.call_api(
                    "get",
                    url_name,
                    args=args,
                    headers=self.auth_headers,
                )
                self.assertEqual(response.status_code, 404)

    def test_owner_sees_private_profile(self):
        """The owner can see their own profile."""
        response = self.call_api(
            "get",
            "api_user_profile",
            args=("api-test-user1",),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)

    def test_public_profile(self):
        """Anonymous users can read the profile and media of public users."""
        self.user2.profile_private = False
        self.user2.save()

        response = self.call_api("get", "api_user_profile", args=("api-test-user2",))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["username"], "api-test-user2")

        response = self.call_api(
            "get",
            "api_user_media_type_list",
            args=("api-test-user2", "movie"),
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("pagination", payload)
        for media in payload["results"]:
            self.assertEqual(media["lists"], [])

    def test_unknown_user(self):
        """Missing users answer 404."""
        response = self.call_api("get", "api_user_profile", args=("nobody",))

        self.assertEqual(response.status_code, 404)

    def test_disabled_media_type(self):
        """Media types the user disabled answer 404."""
        self.user2.profile_private = False
        self.user2.movie_enabled = False
        self.user2.save()

        response = self.call_api(
            "get",
            "api_user_media_type_list",
            args=("api-test-user2", "movie"),
        )

        self.assertEqual(response.status_code, 404)

    def test_invalid_media_type(self):
        """Invalid media types are a bad request."""
        self.user2.profile_private = False
        self.user2.save()

        response = self.call_api(
            "get",
            "api_user_media_type_list",
            args=("api-test-user2", "invalid"),
        )

        self.assertEqual(response.status_code, 400)
