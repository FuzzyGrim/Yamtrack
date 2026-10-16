from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from app import tmdb_rating_sync


class RefreshTmdbRatingsViewTests(TestCase):
    """Tests for the manual 'Refresh TMDB ratings' advanced-settings trigger."""

    def setUp(self):
        """Create and log in a user for the tests."""
        self.credentials = {"username": "testuser", "password": "testpass123"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)
        cache.delete(tmdb_rating_sync.REFRESH_LOCK_KEY)
        self.addCleanup(cache.delete, tmdb_rating_sync.REFRESH_LOCK_KEY)

    def test_post_queues_forced_refresh(self):
        """POSTing queues the task with force=True."""
        with patch("users.views.app_tasks.refresh_tmdb_ratings.delay") as mock_delay:
            response = self.client.post(reverse("refresh_tmdb_ratings"))

        mock_delay.assert_called_once_with(force=True)
        self.assertRedirects(response, reverse("advanced"))

    def test_post_while_already_running_does_not_requeue(self):
        """POSTing while the lock is held informs the user instead of requeueing."""
        cache.set(tmdb_rating_sync.REFRESH_LOCK_KEY, True, timeout=60)  # noqa: FBT003

        with patch("users.views.app_tasks.refresh_tmdb_ratings.delay") as mock_delay:
            response = self.client.post(reverse("refresh_tmdb_ratings"))

        mock_delay.assert_not_called()
        self.assertRedirects(response, reverse("advanced"))

    def test_get_not_allowed(self):
        """GET requests are rejected."""
        response = self.client.get(reverse("refresh_tmdb_ratings"))

        self.assertEqual(response.status_code, 405)
