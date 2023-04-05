from django.test import TestCase
from django.urls import reverse
from django.contrib import auth

from app.models import User


class DefaultView(TestCase):
    def test_home(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("login") + "?next=/")

    def test_medialist(self):
        response = self.client.get(reverse("medialist", kwargs={"media_type": "tv"}))
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(
            response,
            reverse("login")
            + "?next="
            + reverse("medialist", kwargs={"media_type": "tv"}),
        )

    def test_medialist_completed(self):
        response = self.client.get(
            reverse(
                "medialist",
                kwargs={"media_type": "tv", "status": "completed"},
            )
        )
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(
            response,
            reverse("login")
            + "?next="
            + reverse(
                "medialist",
                kwargs={"media_type": "tv", "status": "completed"},
            ),
        )

    def test_register(self):
        response = self.client.get(reverse("register"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/register.html")

    def test_login(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/login.html")

    def test_logout(self):
        response = self.client.get(reverse("logout"))
        self.assertEqual(response.status_code, 302)

    def test_profile(self):
        response = self.client.get(reverse("profile"))
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("login") + "?next=" + reverse("profile"))

    def test_search_tmdb(self):
        response = self.client.get("/search?api=tmdb&q=flcl")
        self.assertEqual(response.status_code, 302)

    def test_search_mal(self):
        response = self.client.get("/search?api=mal&q=flcl")
        self.assertEqual(response.status_code, 302)

    def test_admin(self):
        response = self.client.get("/admin")
        self.assertEqual(response.status_code, 301)


class LoggedInView(TestCase):
    def setUp(self):
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_home(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/home.html")        

    def test_search_tmdb(self):
        response = self.client.get("/search?api=tmdb&q=friends")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/search.html")
        self.assertContains(response, "Friends")

    def test_search_mal(self):
        response = self.client.get("/search?api=mal&q=flcl")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/search.html")
        self.assertContains(response, "FLCL")

    def test_profile(self):
        response = self.client.get(reverse("profile"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/profile.html")
        self.assertContains(response, "test")

    def test_edit_modal_tmdb(self):
        response = self.client.get(
            reverse("edit"), {"media_type": "tv", "media_id": "1668"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/edit.html")
        self.assertContains(response, "Friends")

    def test_edit_modal_mal(self):
        response = self.client.get(
            reverse("edit"), {"media_type": "anime", "media_id": "227"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/edit.html")
        self.assertContains(response, "FLCL")

    def test_logout(self):
        self.client.get(reverse("logout"))
        assert auth.get_user(self.client).is_anonymous


class AdminView(TestCase):
    def setUp(self):
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_superuser(**self.credentials)
        self.client.login(**self.credentials)

    def test_admin(self):
        response = self.client.get("/admin/app/user/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "default_api")

        response = self.client.get("/admin/app/user/add/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "default_api")

    def test_admin_logout(self):
        self.client.get(reverse("logout"))
        assert auth.get_user(self.client).is_anonymous
