import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from app.models import TV, Anime, Episode, Item, MediaTypes, Movie, Season, Status
from integrations.webhooks.jellyfin import JellyfinWebhookProcessor


class JellyfinWebhookTests(TestCase):
    """Tests for Jellyfin webhook."""

    def setUp(self):
        """Set up test data."""
        self.client = Client()
        self.credentials = {"username": "testuser", "token": "test-token"}
        self.user = get_user_model().objects.create_superuser(**self.credentials)
        self.url = reverse("jellyfin_webhook", kwargs={"token": "test-token"})

    def test_invalid_token(self):
        """Test webhook with invalid token returns 401."""
        url = reverse("jellyfin_webhook", kwargs={"token": "invalid-token"})
        response = self.client.post(url, data={}, content_type="application/json")
        self.assertEqual(response.status_code, 401)

    def test_tv_episode_mark_played(self):
        """Test webhook handles TV episode mark played event."""
        payload = {
            "Event": "Stop",
            "Item": {
                "Type": "Episode",
                "Name": "The One Where Monica Gets a Roommate",
                "ProviderIds": {
                    "Tvdb": "303821",
                    "Imdb": "tt0583459",
                },
                "SeriesName": "Friends",
                "ParentIndexNumber": 1,
                "IndexNumber": 1,
                "UserData": {"Played": True},
            },
        }

        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)

        # Verify objects were created
        tv_item = Item.objects.get(media_type=MediaTypes.TV.value, media_id="1668")
        self.assertEqual(tv_item.title, "Friends")

        tv = TV.objects.get(item=tv_item, user=self.user)
        self.assertEqual(tv.status, Status.IN_PROGRESS.value)

        season = Season.objects.get(
            item__media_id="1668",
            item__season_number=1,
        )
        self.assertEqual(season.status, Status.IN_PROGRESS.value)

        episode = Episode.objects.get(
            item__media_id="1668",
            item__season_number=1,
            item__episode_number=1,
        )
        self.assertIsNotNone(episode.end_date)

    def test_movie_mark_played(self):
        """Test webhook handles movie mark played event."""
        payload = {
            "Event": "Stop",
            "Item": {
                "Name": "The Matrix",
                "ProductionYear": 1999,
                "Type": "Movie",
                "ProviderIds": {"Tmdb": "603"},
                "UserData": {"Played": True},
            },
        }

        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)

        # Verify movie was created and marked as completed
        movie = Movie.objects.get(
            item__media_id="603",
            user=self.user,
        )
        self.assertEqual(movie.status, Status.COMPLETED.value)
        self.assertEqual(movie.progress, 1)

    def test_anime_movie_mark_played(self):
        """Test webhook handles movie mark played event."""
        payload = {
            "Event": "Stop",
            "Item": {
                "Name": "Perfect Blue",
                "ProductionYear": 1997,
                "Type": "Movie",
                "ProviderIds": {"Imdb": "tt0156887"},
                "UserData": {"Played": True},
            },
        }

        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)

        # Verify movie was created and marked as completed
        movie = Anime.objects.get(
            item__media_id="437",
            user=self.user,
        )
        self.assertEqual(movie.status, Status.COMPLETED.value)
        self.assertEqual(movie.progress, 1)

    def test_anime_episode_mark_played(self):
        """Test webhook handles anime episode mark played event."""
        payload = {
            "Event": "Stop",
            "Item": {
                "Type": "Episode",
                "Name": "The Journey's End",
                "ProviderIds": {
                    "Tvdb": "9350138",
                    "Imdb": "tt23861604",
                },
                "UserData": {"Played": True},
                "SeriesName": "Frieren: Beyond Journey's End",
                "ParentIndexNumber": 1,
                "IndexNumber": 1,
            },
        }

        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)

        # Verify anime was created and marked as in progress
        anime = Anime.objects.get(
            item__media_id="52991",
            user=self.user,
        )
        self.assertEqual(anime.status, Status.IN_PROGRESS.value)
        self.assertEqual(anime.progress, 1)

    def test_ignored_event_types(self):
        """Test webhook ignores irrelevant event types."""
        payload = {
            "Event": "SomeOtherEvent",
            "Item": {
                "Type": "Movie",
                "ProviderIds": {"Tmdb": "12345"},
                "UserData": {"Played": True},
            },
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
            "Event": "Stop",
            "Item": {
                "Type": "Movie",
                "ProviderIds": {},
                "UserData": {"Played": True},
            },
        }

        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Movie.objects.count(), 0)

    def test_mark_unplayed(self):
        """Test webhook handles not finished events."""
        payload = {
            "Event": "Stop",
            "Item": {
                "Name": "The Matrix",
                "ProductionYear": 1999,
                "Type": "Movie",
                "ProviderIds": {"Tmdb": "603"},
                "UserData": {"Played": False},
            },
        }
        self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        movie = Movie.objects.get(item__media_id="603")
        self.assertEqual(movie.progress, 0)
        self.assertEqual(movie.status, Status.IN_PROGRESS.value)

    def test_repeated_watch(self):
        """Test webhook handles repeated watches."""
        payload = {
            "Event": "Stop",
            "Item": {
                "Type": "Movie",
                "ProductionYear": 1999,
                "Name": "The Matrix",
                "ProviderIds": {"Tmdb": "603"},
                "UserData": {"Played": True},
            },
        }

        # First watch
        self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        # Second watch
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        movie = Movie.objects.filter(item__media_id="603")
        self.assertEqual(movie.count(), 2)
        self.assertEqual(movie[0].status, Status.COMPLETED.value)
        self.assertEqual(movie[1].status, Status.COMPLETED.value)

    def test_extract_external_ids(self):
        """Test extracting external IDs from provider payload."""
        payload = {
            "Event": "Stop",
            "Item": {
                "Type": "Movie",
                "Name": "The Matrix",
                "ProductionYear": 1999,
                "ProviderIds": {
                    "Tmdb": "603",
                    "Tvdb": "169",
                },
            },
        }

        expected = {
            "tmdb_id": "603",
            "imdb_id": None,
            "tvdb_id": "169",
        }

        result = JellyfinWebhookProcessor()._extract_external_ids(payload)
        if result != expected:
            msg = f"Expected {expected}, got {result}"
            raise AssertionError(msg)

    def test_extract_external_ids_empty(self):
        """Test handling empty provider payload."""
        payload = {
            "Event": "Stop",
            "Item": {
                "Type": "Movie",
                "Name": "The Matrix",
                "ProductionYear": 1999,
                "ProviderIds": {},
            },
        }

        expected = {
            "tmdb_id": None,
            "imdb_id": None,
            "tvdb_id": None,
        }

        result = JellyfinWebhookProcessor()._extract_external_ids(payload)
        if result != expected:
            msg = f"Expected {expected}, got {result}"
            raise AssertionError(msg)

    def test_extract_external_ids_missing(self):
        """Test handling missing ProviderIds."""
        payload = {
            "Event": "Stop",
            "Item": {
                "Type": "Movie",
                "Name": "The Matrix",
                "ProductionYear": 1999,
            },
        }
        expected = {
            "tmdb_id": None,
            "imdb_id": None,
            "tvdb_id": None,
        }

        result = JellyfinWebhookProcessor()._extract_external_ids(payload)
        if result != expected:
            msg = f"Expected {expected}, got {result}"
            raise AssertionError(msg)

    def test_get_episode_number(self):
        """Test extracting episode number from Jellyfin payload."""
        payload = {
            "Item": {
                "IndexNumber": 7,
            },
        }

        result = JellyfinWebhookProcessor()._get_episode_number(payload)

        self.assertEqual(result, 7)

    def test_get_mal_id_from_tvdb_uses_exact_season_match(self):
        """Test TVDB lookups respect exact season mappings and episode offsets."""
        mapping_data = {
            "2369": {
                "tvdb_id": 74796,
                "tvdb_season": -1,
                "tvdb_epoffset": 0,
                "mal_id": 269,
            },
            "15449": {
                "tvdb_id": 74796,
                "tvdb_season": 17,
                "tvdb_epoffset": 0,
                "mal_id": 41467,
            },
            "17765": {
                "tvdb_id": 74796,
                "tvdb_season": 17,
                "tvdb_epoffset": 13,
                "mal_id": 53998,
            },
            "18220": {
                "tvdb_id": 74796,
                "tvdb_season": 17,
                "tvdb_epoffset": 26,
                "mal_id": 56784,
            },
        }

        mal_id, episode_offset = JellyfinWebhookProcessor()._get_mal_id_from_tvdb(
            mapping_data,
            74796,
            17,
            14,
            None,
        )

        self.assertEqual(mal_id, 53998)
        self.assertEqual(episode_offset, 1)

    def test_get_mal_id_from_tvdb_falls_back_to_absolute_order(self):
        """Test season 1 episodes can resolve through Kometa absolute mappings."""
        mapping_data = {
            "2369": {
                "tvdb_id": 74796,
                "tvdb_season": -1,
                "tvdb_epoffset": 0,
                "mal_id": 269,
            },
        }

        mal_id, episode_offset = JellyfinWebhookProcessor()._get_mal_id_from_tvdb(
            mapping_data,
            74796,
            1,
            9,
            9,
        )

        self.assertEqual(mal_id, 269)
        self.assertEqual(episode_offset, 9)

    def test_get_mal_id_from_tvdb_prefers_exact_season_over_absolute_order(self):
        """Test exact season matches win over absolute-order fallback mappings."""
        mapping_data = {
            "2369": {
                "tvdb_id": 74796,
                "tvdb_season": -1,
                "tvdb_epoffset": 0,
                "mal_id": 269,
            },
            "99999": {
                "tvdb_id": 74796,
                "tvdb_season": 1,
                "tvdb_epoffset": 0,
                "mal_id": 12345,
            },
        }

        mal_id, episode_offset = JellyfinWebhookProcessor()._get_mal_id_from_tvdb(
            mapping_data,
            74796,
            1,
            9,
            9,
        )

        self.assertEqual(mal_id, 12345)
        self.assertEqual(episode_offset, 9)

    def test_get_mal_id_from_tvdb_uses_absolute_episode_number_for_fallback(self):
        """Test cross-season absolute-order mappings use TVDB absolute numbering."""
        mapping_data = {
            "2369": {
                "tvdb_id": 74796,
                "tvdb_season": -1,
                "tvdb_epoffset": 0,
                "mal_id": 269,
            },
        }

        mal_id, episode_offset = JellyfinWebhookProcessor()._get_mal_id_from_tvdb(
            mapping_data,
            74796,
            2,
            2,
            absolute_episode_number=22,
        )

        self.assertEqual(mal_id, 269)
        self.assertEqual(episode_offset, 22)

    @patch("integrations.webhooks.base.BaseWebhookProcessor._handle_anime")
    @patch("integrations.webhooks.base.tvdb_provider.episode")
    @patch("integrations.webhooks.base.BaseWebhookProcessor._find_tv_media_id")
    @patch("integrations.webhooks.base.BaseWebhookProcessor._fetch_mapping_data")
    def test_process_tv_uses_tvdb_absolute_order_for_cross_season_match(
        self,
        mock_fetch_mapping_data,
        mock_find_tv_media_id,
        mock_tvdb_episode,
        mock_handle_anime,
    ):
        """Test webhook anime matching uses TVDB absolute numbering across seasons."""
        mock_fetch_mapping_data.return_value = {
            "2369": {
                "tvdb_id": 74796,
                "tvdb_season": -1,
                "tvdb_epoffset": 0,
                "mal_id": 269,
            },
        }
        mock_find_tv_media_id.return_value = ("1668", 2, 2)
        mock_tvdb_episode.return_value = {
            "episode_id": 12345,
            "series_id": 74796,
            "season_number": 2,
            "episode_number": 2,
            "absolute_number": 22,
        }

        payload = {
            "Event": "Stop",
            "Item": {
                "Type": "Episode",
                "Name": "Test Episode",
                "ProviderIds": {
                    "Tvdb": "12345",
                },
                "UserData": {"Played": True},
                "SeriesName": "Bleach",
                "ParentIndexNumber": 2,
                "IndexNumber": 2,
            },
        }

        JellyfinWebhookProcessor().process_payload(payload, self.user)

        mock_tvdb_episode.assert_called_once_with(12345)
        mock_handle_anime.assert_called_once_with(269, 22, payload, self.user)
