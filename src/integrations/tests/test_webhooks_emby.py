import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from app.models import TV, Anime, Episode, Item, MediaTypes, Movie, Season, Status
from integrations.webhooks.emby import EmbyWebhookProcessor


class EmbyWebhookTests(TestCase):
    """Tests for Emby webhook."""

    def setUp(self):
        """Set up test data."""
        self.client = Client()
        self.credentials = {"username": "testuser", "token": "test-token"}
        self.user = get_user_model().objects.create_superuser(**self.credentials)
        self.url = reverse("emby_webhook", kwargs={"token": "test-token"})
        self._patch_provider_calls()

    def _patch_provider_calls(self):
        """Keep webhook unit tests independent from external metadata APIs."""
        patches = [
            patch("integrations.webhooks.tv.tvdb_provider.episode"),
            patch("integrations.webhooks.tv.tvdb_provider.series_tmdb_id"),
            patch("integrations.webhooks.tv.app.providers.tmdb.find"),
            patch("integrations.webhooks.tv.app.providers.tmdb.tv_with_seasons"),
            patch("integrations.webhooks.movie.app.providers.tmdb.movie"),
            patch("integrations.webhooks.anime.app.providers.mal.anime"),
            patch("app.models.providers.services.get_media_metadata"),
            patch(
                "integrations.webhooks.tv.anime_mappings.fetch_mapping_data",
                return_value={},
            ),
            patch(
                "integrations.webhooks.tv.anime_mappings.get_mal_id_from_tvdb",
                side_effect=self._mock_mal_id_from_tvdb,
            ),
            patch(
                "integrations.webhooks.tv.anime_mappings.get_mal_id_from_tmdb_movie",
                side_effect=self._mock_mal_id_from_tmdb_movie,
            ),
            patch(
                "integrations.webhooks.tv.anime_mappings.get_mal_id_from_imdb",
                return_value=None,
            ),
        ]

        (
            self.mock_tvdb_episode,
            self.mock_series_tmdb_id,
            self.mock_tmdb_find,
            self.mock_tv_with_seasons,
            self.mock_tmdb_movie,
            self.mock_mal_anime,
            self.mock_get_media_metadata,
            *_,
        ) = [patcher.start() for patcher in patches]
        for patcher in patches:
            self.addCleanup(patcher.stop)

        self.mock_tvdb_episode.side_effect = self._mock_tvdb_episode
        self.mock_series_tmdb_id.return_value = "1668"
        self.mock_tmdb_find.side_effect = self._mock_tmdb_find
        self.mock_tv_with_seasons.side_effect = self._mock_tv_with_seasons
        self.mock_tmdb_movie.side_effect = self._mock_tmdb_movie
        self.mock_mal_anime.side_effect = self._mock_mal_anime
        self.mock_get_media_metadata.side_effect = self._mock_media_metadata

    def _mock_tvdb_episode(self, episode_id):
        if episode_id == 9350138:
            return {"series_id": 424536, "season_number": 1, "episode_number": 1}
        if episode_id == 303821:
            return {"series_id": 79168, "season_number": 1, "episode_number": 1}
        return None

    def _mock_mal_id_from_tvdb(
        self,
        _mapping_data,
        series_id,
        _season_number,
        episode_number,
    ):
        if series_id == 424536:
            return 52991, episode_number
        return None, None

    def _mock_mal_id_from_tmdb_movie(self, _mapping_data, tmdb_id):
        if str(tmdb_id) == "10494":
            return 437
        return None

    def _mock_tmdb_find(self, external_id, _external_source):
        if external_id in {"303821", "tt0583459"}:
            return {
                "tv_episode_results": [
                    {"show_id": 1668, "season_number": 1, "episode_number": 1},
                ],
            }
        if external_id == "tt0133093":
            return {"movie_results": [{"id": 603}]}
        return {"movie_results": [], "tv_episode_results": []}

    def _mock_tv_with_seasons(self, media_id, seasons):
        return {
            "title": "Friends",
            "image": "friends.jpg",
            f"season/{seasons[0]}": {
                "image": "friends-season-1.jpg",
                "episodes": [
                    {"episode_number": 1, "still_path": "/friends-s01e01.jpg"},
                    {"episode_number": 2, "still_path": "/friends-s01e02.jpg"},
                ],
            },
        }

    def _mock_tmdb_movie(self, media_id):
        titles = {
            "603": "The Matrix",
            603: "The Matrix",
            "10494": "Perfect Blue",
            10494: "Perfect Blue",
        }
        return {"title": titles[media_id], "image": f"{media_id}.jpg", "max_progress": 1}

    def _mock_mal_anime(self, media_id):
        metadata = {
            437: {"title": "Perfect Blue", "max_progress": 1},
            52991: {"title": "Frieren: Beyond Journey's End", "max_progress": 28},
        }
        anime = metadata[int(media_id)]
        return {"image": f"{media_id}.jpg", **anime}

    def _mock_media_metadata(self, media_type, media_id, _source, *_args):
        if media_type in {MediaTypes.TV.value, "tv_with_seasons"} and str(media_id) == "1668":
            return {
                "title": "Friends",
                "max_progress": 1,
                "season/1": {
                    "image": "friends-season-1.jpg",
                    "episodes": [
                        {"episode_number": 1, "still_path": "/friends-s01e01.jpg"},
                        {"episode_number": 2, "still_path": "/friends-s01e02.jpg"},
                    ],
                },
                "related": {
                    "seasons": [
                        {
                            "season_number": 1,
                            "first_air_date": "1994-09-22",
                            "image": "friends-season-1.jpg",
                        },
                    ],
                },
            }
        if media_type == MediaTypes.ANIME.value and str(media_id) == "52991":
            return {"max_progress": 28}
        return {"max_progress": 1}

    def test_invalid_token(self):
        """Test webhook with invalid token returns 401."""
        url = reverse("emby_webhook", kwargs={"token": "invalid-token"})
        response = self.client.post(url, data={}, content_type="application/json")
        self.assertEqual(response.status_code, 401)

    def test_tv_episode_mark_played(self):
        """Test webhook handles TV episode mark played event."""
        payload = {
            "Event": "playback.stop",
            "Item": {
                "Type": "Episode",
                "Name": "The One Where Monica Gets a Roommate",
                "ProductionYear": 1994,
                "ProviderIds": {
                    "Tvdb": "303821",
                    "Imdb": "tt0583459",
                },
                "SeriesName": "Friends",
                "ParentIndexNumber": 1,
                "IndexNumber": 1,
            },
            "PlaybackInfo": {
                "PlayedToCompletion": True,
            },
        }

        data = {
            "data": json.dumps(payload),
        }

        response = self.client.post(
            self.url,
            data=data,
            format="multipart",
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

    def test_anime_episode_mark_played(self):
        """Test webhook handles anime episode mark played event."""
        payload = {
            "Event": "playback.stop",
            "Item": {
                "Type": "Episode",
                "Name": "The Journey's End",
                "ProductionYear": 2003,
                "ProviderIds": {
                    "Tvdb": "9350138",
                    "Imdb": "tt23861604",
                },
                "SeriesName": "Frieren: Beyond Journey's End",
                "ParentIndexNumber": 1,
                "IndexNumber": 1,
            },
            "PlaybackInfo": {
                "PlayedToCompletion": True,
            },
        }

        data = {
            "data": json.dumps(payload),
        }

        response = self.client.post(
            self.url,
            data=data,
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)

        # Verify anime was created and marked as in progress
        anime = Anime.objects.get(
            item__media_id="52991",
            user=self.user,
        )
        self.assertEqual(anime.status, Status.IN_PROGRESS.value)
        self.assertEqual(anime.progress, 1)

    def test_movie_mark_played(self):
        """Test webhook handles movie mark played event."""
        payload = {
            "Event": "playback.stop",
            "Item": {
                "Type": "Movie",
                "Name": "The Matrix",
                "ProductionYear": 1999,
                "ProviderIds": {
                    "Imdb": "tt0133093",
                    "Tmdb": "603",
                    "Tvdb": "169",
                    "Official Website": "http://www.warnerbros.com/matrix",
                    "Wikidata": "Q83495",
                    "Wikipedia": "The_Matrix",
                },
            },
            "PlaybackInfo": {
                "PlayedToCompletion": True,
            },
        }
        data = {
            "data": json.dumps(payload),
        }

        response = self.client.post(
            self.url,
            data=data,
            format="multipart",
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
            "Event": "playback.stop",
            "Item": {
                "Type": "Movie",
                "Name": "Perfect Blue",
                "ProductionYear": 1997,
                "ProviderIds": {
                    "Imdb": "tt0156887",
                    "Tmdb": "10494",
                    "Tvdb": "3807",
                },
            },
            "PlaybackInfo": {
                "PlayedToCompletion": True,
            },
        }

        data = {
            "data": json.dumps(payload),
        }

        response = self.client.post(
            self.url,
            data=data,
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)

        # Verify movie was created and marked as completed
        movie = Anime.objects.get(
            item__media_id="437",
            user=self.user,
        )
        self.assertEqual(movie.status, Status.COMPLETED.value)
        self.assertEqual(movie.progress, 1)

    def test_ignored_event_types(self):
        """Test webhook ignores irrelevant event types."""
        payload = {
            "Event": "playback.something_else",
            "Item": {
                "Type": "Movie",
                "Name": "The Matrix",
                "ProductionYear": 1999,
                "ProviderIds": {
                    "Imdb": "tt0133093",
                    "Tmdb": "603",
                    "Tvdb": "169",
                },
            },
            "PlaybackInfo": {
                "PlayedToCompletion": True,
            },
        }

        data = {
            "data": json.dumps(payload),
        }

        response = self.client.post(
            self.url,
            data=data,
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Movie.objects.count(), 0)

    def test_ignored_media_types(self):
        """Test webhook ignores irrelevant event types."""
        payload = {
            "Event": "playback.stop",
            "Item": {
                "Type": "SomethingElse",
                "Name": "The Matrix",
                "ProductionYear": 1999,
                "ProviderIds": {
                    "Imdb": "tt0133093",
                    "Tmdb": "603",
                    "Tvdb": "169",
                    "Official Website": "http://www.warnerbros.com/matrix",
                    "Wikidata": "Q83495",
                    "Wikipedia": "The_Matrix",
                },
            },
            "PlaybackInfo": {
                "PlayedToCompletion": True,
            },
        }

        data = {
            "data": json.dumps(payload),
        }

        response = self.client.post(
            self.url,
            data=data,
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Movie.objects.count(), 0)

    def test_missing_tmdb_id(self):
        """Test webhook handles missing TMDB ID gracefully."""
        payload = {
            "Event": "playback.stop",
            "Item": {
                "Type": "Movie",
                "Name": "The Matrix",
                "ProductionYear": 1999,
                "ProviderIds": {},
            },
            "PlaybackInfo": {
                "PlayedToCompletion": True,
            },
        }
        data = {
            "data": json.dumps(payload),
        }

        response = self.client.post(
            self.url,
            data=data,
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Movie.objects.count(), 0)

    def test_repeated_watch(self):
        """Test webhook handles repeated watches."""
        payload = {
            "Event": "playback.stop",
            "Item": {
                "Type": "Movie",
                "Name": "The Matrix",
                "ProductionYear": 1999,
                "ProviderIds": {
                    "Imdb": "tt0133093",
                    "Tmdb": "603",
                    "Tvdb": "169",
                    "Official Website": "http://www.warnerbros.com/matrix",
                    "Wikidata": "Q83495",
                    "Wikipedia": "The_Matrix",
                },
            },
            "PlaybackInfo": {
                "PlayedToCompletion": True,
            },
        }

        data = {
            "data": json.dumps(payload),
        }

        # First watch
        response = self.client.post(
            self.url,
            data=data,
            format="multipart",
        )

        # Second watch
        response = self.client.post(
            self.url,
            data=data,
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)
        movie = Movie.objects.filter(item__media_id="603")
        self.assertEqual(movie.count(), 2)
        self.assertEqual(movie[0].status, Status.COMPLETED.value)
        self.assertEqual(movie[1].status, Status.COMPLETED.value)

    def test_extract_external_ids(self):
        """Test extracting external IDs from provider payload."""
        payload = {
            "Event": "playback.something_else",
            "Item": {
                "Type": "Movie",
                "Name": "The Matrix",
                "ProductionYear": 1999,
                "ProviderIds": {
                    "Tmdb": "603",
                    "Tvdb": "169",
                },
            },
            "PlaybackInfo": {
                "PlayedToCompletion": True,
            },
        }

        expected = {
            "tmdb_id": "603",
            "imdb_id": None,
            "tvdb_id": "169",
        }

        result = EmbyWebhookProcessor()._extract_external_ids(payload)
        if result != expected:
            msg = f"Expected {expected}, got {result}"
            raise AssertionError(msg)

    def test_extract_external_ids_empty(self):
        """Test handling empty provider payload."""
        payload = {
            "Event": "playback.something_else",
            "Item": {
                "Type": "Movie",
                "Name": "The Matrix",
                "ProductionYear": 1999,
                "ProviderIds": {},
            },
            "PlaybackInfo": {
                "PlayedToCompletion": True,
            },
        }

        expected = {
            "tmdb_id": None,
            "imdb_id": None,
            "tvdb_id": None,
        }

        result = EmbyWebhookProcessor()._extract_external_ids(payload)
        if result != expected:
            msg = f"Expected {expected}, got {result}"
            raise AssertionError(msg)

    def test_extract_external_ids_missing(self):
        """Test handling missing ProviderIds."""
        payload = {
            "Event": "playback.something_else",
            "Item": {
                "Type": "Movie",
                "Name": "The Matrix",
                "ProductionYear": 1999,
            },
            "PlaybackInfo": {
                "PlayedToCompletion": True,
            },
        }
        expected = {
            "tmdb_id": None,
            "imdb_id": None,
            "tvdb_id": None,
        }

        result = EmbyWebhookProcessor()._extract_external_ids(payload)
        if result != expected:
            msg = f"Expected {expected}, got {result}"
            raise AssertionError(msg)

    def test_get_episode_number(self):
        """Test extracting episode number from Emby payload."""
        payload = {
            "Item": {
                "IndexNumber": 7,
            },
        }

        result = EmbyWebhookProcessor()._get_episode_number(payload)

        self.assertEqual(result, 7)
