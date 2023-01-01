from django.test import TestCase
from django.urls import reverse
from django.contrib import auth

from app.models import User

class DefaultView(TestCase):
    def test_home(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("login") + "?next=/")

    def test_register(self):
        response = self.client.get(reverse("register"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'app/register.html')

    def test_login(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'app/login.html')
    
    def test_logout(self):
        response = self.client.get(reverse("logout"))
        self.assertEqual(response.status_code, 302)
    
    def test_profile(self):
        response = self.client.get(reverse("profile"))
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("login") + "?next=" + reverse("profile"))
    
    def test_search_tmdb(self):
        response = self.client.get(reverse("search", args = ["tmdb", "flcl"]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'app/search.html')
        self.assertContains(response, "FLCL")
    
    def test_search_mal(self):
        response = self.client.get(reverse("search", args = ["mal", "flcl"]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'app/search.html')
        self.assertContains(response, "FLCL")

class LoggedInView(TestCase):
    def setUp(self):
        self.credentials = {"username": "test","password" : "12345"}
        self.user = User.objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_home(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'app/home.html')
        self.assertContains(response, "TV")
    
    def test_profile(self):
        response = self.client.get(reverse("profile"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'app/profile.html')
        self.assertContains(response, "test")
    
    def test_logout(self):
        self.client.get(reverse("logout"))
        assert auth.get_user(self.client).is_anonymous