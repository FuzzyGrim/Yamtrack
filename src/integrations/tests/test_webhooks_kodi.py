import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from app.models import TV, Episode, Item, MediaTypes, Movie, Season, Status
from integrations.webhooks.kodi import KodiWebhookProcessor


class KodiWebhookTests(TestCase):
    """Tests for Kodi webhook."""

    def setUp(self):
        """Set up test data."""
        self.client = Client()
        self.credentials = {"username": "testuser", "token": "test-token"}
        self.user = get_user_model().objects.create_superuser(**self.credentials)
        self.url = reverse("kodi_webhook", kwargs={"token": "test-token"})

    def test_invalid_token(self):
        """Test webhook with invalid token returns 401."""
        url = reverse("kodi_webhook", kwargs={"token": "invalid-token"})
        response = self.client.post(url, data={}, content_type="application/json")
        self.assertEqual(response.status_code, 401)

    def test_tv_episode_start_event(self):
        """Test webhook handles TV episode start playback event."""
        payload = {
            "event": "start",
            "dbId": 11328,
            "title": "YABA",
            "mediaType": "episode",
            "year": 2026,
            "uniqueIds": {"imdb": "tt35947243", "tvdb": "10991548"},
            "duration": 2172,
            "progress": {"time": 0, "percent": 0.0},
            "tvShowTitle": "Maximum Pleasure Guaranteed",
            "season": 1,
            "episode": 2,
            "firstAired": "2026-05-20",
            "tvShowUniqueIds": {"imdb": "tt35946742", "tvdb": "460793"},
        }

        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)

        # Verify objects were created
        tv_item = Item.objects.get(media_type=MediaTypes.TV.value, media_id="285404")
        self.assertEqual(tv_item.title, "Maximum Pleasure Guaranteed")

        tv = TV.objects.get(item=tv_item, user=self.user)
        self.assertEqual(tv.status, Status.IN_PROGRESS.value)

        season = Season.objects.get(
            item__media_id="285404",
            item__season_number=1,
        )
        self.assertEqual(season.status, Status.IN_PROGRESS.value)

        episodes = Episode.objects.filter(
            item__media_id="285404",
            item__season_number=1,
            item__episode_number=2,
        )
        self.assertFalse(episodes)

    def test_tv_episode_stop_event_pre_threshold(self):
        """Test webhook handles TV episode stop playback event before the threshold."""
        payload = {
            "event": "stop",
            "dbId": 11328,
            "title": "YABA",
            "mediaType": "episode",
            "year": 2026,
            "uniqueIds": {"imdb": "tt35947243", "tvdb": "10991548"},
            "duration": 2172,
            "progress": {"time": 1123, "percent": 51.703499079},
            "tvShowTitle": "Maximum Pleasure Guaranteed",
            "season": 1,
            "episode": 2,
            "firstAired": "2026-05-20",
            "tvShowUniqueIds": {"imdb": "tt35946742", "tvdb": "460793"},
        }

        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)

        # Verify objects were created
        tv_item = Item.objects.get(media_type=MediaTypes.TV.value, media_id="285404")
        self.assertEqual(tv_item.title, "Maximum Pleasure Guaranteed")

        tv = TV.objects.get(item=tv_item, user=self.user)
        self.assertEqual(tv.status, Status.IN_PROGRESS.value)

        season = Season.objects.get(
            item__media_id="285404",
            item__season_number=1,
        )
        self.assertEqual(season.status, Status.IN_PROGRESS.value)

        episodes = Episode.objects.filter(
            item__media_id="285404",
            item__season_number=1,
            item__episode_number=2,
        )
        self.assertFalse(episodes)

    def test_tv_episode_stop_event_post_threshold(self):
        """Test webhook handles TV episode stop playback event after the threshold."""
        payload = {
            "event": "stop",
            "dbId": 11328,
            "title": "YABA",
            "mediaType": "episode",
            "year": 2026,
            "uniqueIds": {"imdb": "tt35947243", "tvdb": "10991548"},
            "duration": 2172,
            "progress": {"time": 1801, "percent": 82.91896869244935},
            "tvShowTitle": "Maximum Pleasure Guaranteed",
            "season": 1,
            "episode": 2,
            "firstAired": "2026-05-20",
            "tvShowUniqueIds": {"imdb": "tt35946742", "tvdb": "460793"},
        }

        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)

        # Verify objects were created
        tv_item = Item.objects.get(media_type=MediaTypes.TV.value, media_id="285404")
        self.assertEqual(tv_item.title, "Maximum Pleasure Guaranteed")

        tv = TV.objects.get(item=tv_item, user=self.user)
        self.assertEqual(tv.status, Status.IN_PROGRESS.value)

        season = Season.objects.get(
            item__media_id="285404",
            item__season_number=1,
        )
        self.assertEqual(season.status, Status.IN_PROGRESS.value)

        episodes = Episode.objects.filter(
            item__media_id="285404",
            item__season_number=1,
            item__episode_number=2,
        )
        self.assertTrue(episodes)
        self.assertEqual(episodes.count(), 1)
        episode = episodes[0]
        self.assertIsNotNone(episode.end_date)

    def test_tv_episode_repeat_stop_events_post_threshold(self):
        """Test webhook handles TV episode stop playback event after the threshold."""
        payload = {
            "event": "stop",
            "dbId": 11328,
            "title": "YABA",
            "mediaType": "episode",
            "year": 2026,
            "uniqueIds": {"imdb": "tt35947243", "tvdb": "10991548"},
            "duration": 2172,
            "progress": {"time": 1801, "percent": 82.91896869244935},
            "tvShowTitle": "Maximum Pleasure Guaranteed",
            "season": 1,
            "episode": 2,
            "firstAired": "2026-05-20",
            "tvShowUniqueIds": {"imdb": "tt35946742", "tvdb": "460793"},
        }

        # First watch
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)

        # Second watch
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)

        # Verify objects were created
        tv_item = Item.objects.get(media_type=MediaTypes.TV.value, media_id="285404")
        self.assertEqual(tv_item.title, "Maximum Pleasure Guaranteed")

        tv = TV.objects.get(item=tv_item, user=self.user)
        self.assertEqual(tv.status, Status.IN_PROGRESS.value)

        season = Season.objects.get(
            item__media_id="285404",
            item__season_number=1,
        )
        self.assertEqual(season.status, Status.IN_PROGRESS.value)

        episodes = Episode.objects.filter(
            item__media_id="285404",
            item__season_number=1,
            item__episode_number=2,
        )
        self.assertTrue(episodes)
        self.assertEqual(episodes.count(), 1)
        episode = episodes[0]
        self.assertIsNotNone(episode.end_date)

        history = episode.history.all()
        self.assertTrue(history)

        self.assertEqual(history.count(), 1)
        self.assertIsNotNone(history[0].end_date)

    def test_tv_episode_end_event(self):
        """Test webhook handles TV episode stop playback event after the threshold."""
        payload = {
            "event": "end",
            "dbId": 11328,
            "title": "YABA",
            "mediaType": "episode",
            "year": 2026,
            "uniqueIds": {"imdb": "tt35947243", "tvdb": "10991548"},
            "duration": 2172,
            "progress": {"time": 2172, "percent": 100},
            "tvShowTitle": "Maximum Pleasure Guaranteed",
            "season": 1,
            "episode": 2,
            "firstAired": "2026-05-20",
            "tvShowUniqueIds": {"imdb": "tt35946742", "tvdb": "460793"},
        }

        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)

        # Verify objects were created
        tv_item = Item.objects.get(media_type=MediaTypes.TV.value, media_id="285404")
        self.assertEqual(tv_item.title, "Maximum Pleasure Guaranteed")

        tv = TV.objects.get(item=tv_item, user=self.user)
        self.assertEqual(tv.status, Status.IN_PROGRESS.value)

        season = Season.objects.get(
            item__media_id="285404",
            item__season_number=1,
        )
        self.assertEqual(season.status, Status.IN_PROGRESS.value)

        episodes = Episode.objects.filter(
            item__media_id="285404",
            item__season_number=1,
            item__episode_number=2,
        )
        self.assertTrue(episodes)
        self.assertEqual(episodes.count(), 1)
        episode = episodes[0]
        self.assertIsNotNone(episode.end_date)

    def test_tv_episode_repeat_end_events(self):
        """Test webhook handles TV episode stop playback event after the threshold."""
        payload = {
            "event": "end",
            "dbId": 11328,
            "title": "YABA",
            "mediaType": "episode",
            "year": 2026,
            "uniqueIds": {"imdb": "tt35947243", "tvdb": "10991548"},
            "duration": 2172,
            "progress": {"time": 2172, "percent": 100},
            "tvShowTitle": "Maximum Pleasure Guaranteed",
            "season": 1,
            "episode": 2,
            "firstAired": "2026-05-20",
            "tvShowUniqueIds": {"imdb": "tt35946742", "tvdb": "460793"},
        }

        # First watch
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)

        # Second watch
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)

        # Verify objects were created
        tv_item = Item.objects.get(media_type=MediaTypes.TV.value, media_id="285404")
        self.assertEqual(tv_item.title, "Maximum Pleasure Guaranteed")

        tv = TV.objects.get(item=tv_item, user=self.user)
        self.assertEqual(tv.status, Status.IN_PROGRESS.value)

        season = Season.objects.get(
            item__media_id="285404",
            item__season_number=1,
        )
        self.assertEqual(season.status, Status.IN_PROGRESS.value)

        episodes = Episode.objects.filter(
            item__media_id="285404",
            item__season_number=1,
            item__episode_number=2,
        )
        self.assertTrue(episodes)
        self.assertEqual(episodes.count(), 1)
        episode = episodes[0]
        self.assertIsNotNone(episode.end_date)

        history = episode.history.all()
        self.assertTrue(history)

        self.assertEqual(history.count(), 1)
        self.assertIsNotNone(history[0].end_date)

    def test_movie_start_event(self):
        """Test webhook handles movie start event."""
        payload = {
            "event": "start",
            "dbId": 593,
            "title": "The Machine",
            "mediaType": "movie",
            "year": 2023,
            "uniqueIds": {"imdb": "tt11040844", "tmdb": "647250"},
            "duration": 6744,
            "progress": {"time": 0, "percent": 0.0},
            "premiered": "2023-05-25",
        }

        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)

        # Verify movie was created and marked as in progress
        movie = Movie.objects.get(
            item__media_id="647250",
            user=self.user,
        )
        self.assertEqual(movie.status, Status.IN_PROGRESS.value)
        self.assertEqual(movie.progress, 0)

    def test_movie_stop_event_pre_threshold(self):
        """Test webhook handles movie stop pre threshold."""
        payload = {
            "event": "stop",
            "dbId": 593,
            "title": "The Machine",
            "mediaType": "movie",
            "year": 2023,
            "uniqueIds": {"imdb": "tt11040844", "tmdb": "647250"},
            "duration": 6744,
            "progress": {"time": 3123, "percent": 46.3078292},
            "premiered": "2023-05-25",
        }

        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)

        # Verify movie was created and marked as in progress
        movie = Movie.objects.get(
            item__media_id="647250",
            user=self.user,
        )
        self.assertEqual(movie.status, Status.IN_PROGRESS.value)
        self.assertEqual(movie.progress, 0)

    def test_movie_stop_event_post_threshold(self):
        """Test webhook handles movie stop post threshold."""
        payload = {
            "event": "stop",
            "dbId": 593,
            "title": "The Machine",
            "mediaType": "movie",
            "year": 2023,
            "uniqueIds": {"imdb": "tt11040844", "tmdb": "647250"},
            "duration": 6744,
            "progress": {"time": 6123, "percent": 90.7918149},
            "premiered": "2023-05-25",
        }

        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)

        # Verify movie was created and marked as completed
        movie = Movie.objects.get(
            item__media_id="647250",
            user=self.user,
        )
        self.assertEqual(movie.status, Status.COMPLETED.value)
        self.assertEqual(movie.progress, 1)

    def test_movie_repeat_stop_events_post_threshold(self):
        """Test webhook handles multiple movie stop post threshold."""
        payload = {
            "event": "stop",
            "dbId": 593,
            "title": "The Machine",
            "mediaType": "movie",
            "year": 2023,
            "uniqueIds": {"imdb": "tt11040844", "tmdb": "647250"},
            "duration": 6744,
            "progress": {"time": 6123, "percent": 90.7918149},
            "premiered": "2023-05-25",
        }

        # First watch
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)

        # Second watch
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)

        # Verify movie was created and marked as completed
        movie = Movie.objects.filter(item__media_id="647250")
        self.assertEqual(movie.count(), 2)
        self.assertEqual(movie[0].status, Status.COMPLETED.value)
        self.assertEqual(movie[1].status, Status.COMPLETED.value)

    def test_movie_end_event(self):
        """Test webhook handles movie stop end threshold."""
        payload = {
            "event": "end",
            "dbId": 593,
            "title": "The Machine",
            "mediaType": "movie",
            "year": 2023,
            "uniqueIds": {"imdb": "tt11040844", "tmdb": "647250"},
            "duration": 6744,
            "progress": {"time": 6744, "percent": 100.0},
            "premiered": "2023-05-25",
        }

        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)

        # Verify movie was created and marked as completed
        movie = Movie.objects.get(
            item__media_id="647250",
            user=self.user,
        )
        self.assertEqual(movie.status, Status.COMPLETED.value)
        self.assertEqual(movie.progress, 1)

    def test_movie_repeat_end_events(self):
        """Test webhook handles multiple movie end post threshold."""
        payload = {
            "event": "end",
            "dbId": 593,
            "title": "The Machine",
            "mediaType": "movie",
            "year": 2023,
            "uniqueIds": {"imdb": "tt11040844", "tmdb": "647250"},
            "duration": 6744,
            "progress": {"time": 6744, "percent": 100.0},
            "premiered": "2023-05-25",
        }

        # First watch
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)

        # Second watch
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)

        # Verify movie was created and marked as completed
        movie = Movie.objects.filter(item__media_id="647250")
        self.assertEqual(movie.count(), 2)
        self.assertEqual(movie[0].status, Status.COMPLETED.value)
        self.assertEqual(movie[1].status, Status.COMPLETED.value)

    def test_ignored_event_types(self):
        """Test webhook ignores irrelevant event types."""
        payload = {
            "event": "somethingelse",
            "dbId": 593,
            "title": "The Machine",
            "mediaType": "movie",
            "year": 2023,
            "uniqueIds": {"imdb": "tt11040844", "tmdb": "647250"},
            "duration": 6744,
            "progress": {"time": 6738, "percent": 99.91103202846975},
            "premiered": "2023-05-25",
        }

        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Movie.objects.count(), 0)

    def test_ignored_media_types(self):
        """Test webhook ignores irrelevant event types."""
        payload = {
            "event": "end",
            "dbId": 593,
            "title": "The Machine",
            "mediaType": "somethingelse",
            "year": 2023,
            "uniqueIds": {"imdb": "tt11040844", "tmdb": "647250"},
            "duration": 6744,
            "progress": {"time": 6738, "percent": 99.91103202846975},
            "premiered": "2023-05-25",
        }

        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Movie.objects.count(), 0)

    def test_missing_tmdb_id(self):
        """Test webhook handles missing TMDB ID gracefully."""
        payload = {
            "event": "end",
            "dbId": 593,
            "title": "The Machine",
            "mediaType": "movie",
            "year": 2023,
            "uniqueIds": {},
            "duration": 6744,
            "progress": {"time": 6738, "percent": 99.91103202846975},
            "premiered": "2023-05-25",
        }

        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Movie.objects.count(), 0)

    def test_extract_external_ids(self):
        """Test extracting external IDs from uniqueIds payload."""
        payload = {
            "event": "end",
            "dbId": 593,
            "title": "The Machine",
            "mediaType": "movie",
            "year": 2023,
            "uniqueIds": {"imdb": "tt11040844", "tmdb": "647250"},
            "duration": 6744,
            "progress": {"time": 6738, "percent": 99.91103202846975},
            "premiered": "2023-05-25",
        }

        expected = {
            "tmdb_id": "647250",
            "imdb_id": "tt11040844",
            "tvdb_id": None,
        }

        result = KodiWebhookProcessor()._extract_external_ids(payload)
        if result != expected:
            msg = f"Expected {expected}, got {result}"
            raise AssertionError(msg)

    def test_extract_external_ids_empty(self):
        """Test handling empty uniqueIds payload."""
        payload = {
            "event": "end",
            "dbId": 593,
            "title": "The Machine",
            "mediaType": "movie",
            "year": 2023,
            "uniqueIds": {},
            "duration": 6744,
            "progress": {"time": 6738, "percent": 99.91103202846975},
            "premiered": "2023-05-25",
        }

        expected = {
            "tmdb_id": None,
            "imdb_id": None,
            "tvdb_id": None,
        }

        result = KodiWebhookProcessor()._extract_external_ids(payload)
        if result != expected:
            msg = f"Expected {expected}, got {result}"
            raise AssertionError(msg)

    def test_extract_external_ids_missing(self):
        """Test handling missing uniqueIds."""
        payload = {
            "event": "end",
            "dbId": 593,
            "title": "The Machine",
            "mediaType": "movie",
            "year": 2023,
            "duration": 6744,
            "progress": {"time": 6738, "percent": 99.91103202846975},
            "premiered": "2023-05-25",
        }

        expected = {
            "tmdb_id": None,
            "imdb_id": None,
            "tvdb_id": None,
        }

        result = KodiWebhookProcessor()._extract_external_ids(payload)
        if result != expected:
            msg = f"Expected {expected}, got {result}"
            raise AssertionError(msg)

    def test_get_episode_number(self):
        """Test extracting episode number from Kodi payload."""
        payload = {
            "episode": 7,
        }

        result = KodiWebhookProcessor()._get_episode_number(payload)

        self.assertEqual(result, 7)
