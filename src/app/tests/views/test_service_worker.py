from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class ServiceWorkerViewTests(TestCase):
    """Test the service worker view."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_service_worker(self):
        """The service worker script should be served as JavaScript."""
        response = self.client.get(reverse("service_worker"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/javascript")
        self.assertEqual(response["Service-Worker-Allowed"], "/")
