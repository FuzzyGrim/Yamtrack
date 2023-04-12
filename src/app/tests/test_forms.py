from django.test import TestCase
from django.urls import reverse
from django.contrib import auth

from app.models import User


class RegisterLoginUser(TestCase):
    def test_create_user(self):
        response = self.client.post(
            reverse("register"),
            {
                "username": "test",
                "default_api": "tmdb",
                "password1": "SMk5noPnqs",
                "password2": "SMk5noPnqs",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("login"))
        self.assertEqual(User.objects.count(), 1)

    def test_login_user(self):
        self.client.post(
            reverse("register"),
            {
                "username": "test",
                "default_api": "tmdb",
                "password1": "SMk5noPnqs",
                "password2": "SMk5noPnqs",
            },
        )

        response = self.client.post(
            reverse("login"),
            {
                "username": "test",
                "password": "SMk5noPnqs",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("home"))
        self.assertEqual(auth.get_user(self.client).username, "test")


class Profile(TestCase):
    def setUp(self):
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_change_username(self):
        self.assertEqual(auth.get_user(self.client).username, "test")
        self.client.post(
            reverse("profile"),
            {
                "username": "new_test",
                "default_api": "tmdb",
            },
        )
        self.assertEqual(auth.get_user(self.client).username, "new_test")

    def test_change_password(self):
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


class Search(TestCase):
    def setUp(self):
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_search_tmdb(self):
        response = self.client.get(
            reverse("search"),
            {
                "api": "tmdb",
                "q": "The Matrix",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/search.html")
        self.assertContains(response, "The Matrix")

    def test_search_mal(self):
        response = self.client.get(
            reverse("search"),
            {
                "api": "mal",
                "q": "Perfect Blue",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/search.html")
        self.assertContains(response, "Perfect Blue")
