from http import HTTPStatus
from unittest import mock

from django.conf import settings
from django.test import TestCase
from django.urls import resolve, reverse
from health_check.views import HealthCheckView


class HealthCheckTests(TestCase):
    """Test health check endpoints."""

    def test_health_check_is_lightweight_and_unauthenticated(self):
        """Test the default health check skips the deep health check plugins."""
        redis_client = mock.Mock()

        with (
            mock.patch("config.views.cache") as cache,
            mock.patch(
                "config.views.Redis.from_url",
                return_value=redis_client,
            ) as redis_from_url,
        ):
            cache.get.return_value = "ok"
            response = self.client.get(reverse("health"))

        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertEqual(response.content, b"ok")
        redis_from_url.assert_called_once_with(
            settings.REDIS_URL,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        redis_client.ping.assert_called_once_with()
        cache.set.assert_called_once_with("yamtrack:health", "ok", timeout=5)
        cache.get.assert_called_once_with("yamtrack:health")

        resolved = resolve("/health/")
        self.assertIsNot(getattr(resolved.func, "view_class", None), HealthCheckView)

    def test_health_check_returns_unavailable_on_cache_failure(self):
        """Test the fast health check reports dependency failures."""
        with (
            mock.patch("config.views.cache") as cache,
            mock.patch("config.views.Redis.from_url") as redis_from_url,
        ):
            cache.get.return_value = None
            response = self.client.get(reverse("health"))

        self.assertEqual(response.status_code, HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertEqual(response.content, b"cache unavailable")
        redis_from_url.return_value.ping.assert_called_once_with()

    def test_full_health_check_keeps_celery_ping(self):
        """Test the deep django-health-check endpoint is still available."""
        resolved = resolve("/health/full/")

        self.assertIs(resolved.func.view_class, HealthCheckView)
        self.assertIn(
            "health_check.contrib.celery.Ping",
            resolved.func.view_initkwargs["checks"],
        )
