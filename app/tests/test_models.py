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



class CreateMedia(TestCase):
    def setUp(self):
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_create_tmdb(self):
        response = self.client.post(
            reverse("search", args=["tmdb", "flcl"]),
            {
                "media_id": 5895,
                "title": "FLCL",
                "image": "/FkgA8CcmiLJGVCRYRQ2g2UfVtF.jpg",
                "api_origin": "tmdb",
                "status": "Completed",
                "score": 4,
                "season": 1,
                "media_type": "tv",
                "num_seasons": 3,
            },
        )
        response = self.client.get(reverse("home"))
        self.assertContains(response, "FLCL")


class EditMedia(TestCase):
    def setUp(self):
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)
        self.client.login(**self.credentials)
        self.client.post(
            reverse("search", args=["tmdb", "flcl"]),
            {
                "media_id": 5895,
                "title": "FLCL",
                "image": "/FkgA8CcmiLJGVCRYRQ2g2UfVtF.jpg",
                "api_origin": "tmdb",
                "status": "Completed",
                "score": 4,
                "season": 1,
                "media_type": "tv",
                "num_seasons": 3,
            },
        )

    def test_edit_tmdb_score(self):
        response = self.client.post(
            reverse("home"),
            {
                "media_id": 5895,
                "api_origin": "tmdb",
                "status": "Watching",
                "score": 4,
                "season": 1,
                "media_type": "tv",
                "num_seasons": 3,
            },
        )

        response = self.client.get(reverse("home"))
        self.assertContains(response, "4.0")

    def test_edit_tmdb_status(self):
        response = self.client.post(
            reverse("home"),
            {
                "media_id": 5895,
                "api_origin": "tmdb",
                "status": "Watching",
                "score": 3,
                "season": 1,
                "media_type": "tv",
                "num_seasons": 3,
            },
        )

        response = self.client.get(reverse("home"))
        self.assertContains(response, "Watching")
