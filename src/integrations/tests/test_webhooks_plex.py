import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from app.models import TV, Anime, Episode, Item, MediaTypes, Movie, Season, Status
from integrations.webhooks.plex import PlexWebhookProcessor


class PlexWebhookTests(TestCase):
    """Tests for Plex webhook."""

    def setUp(self):
        """Set up test data."""
        self.client = Client()
        self.credentials = {
            "username": "testuser",
            "token": "test-token",
            "plex_usernames": "testuser",
        }
        self.user = get_user_model().objects.create_superuser(**self.credentials)
        self.url = reverse("plex_webhook", kwargs={"token": "test-token"})

    def test_invalid_token(self):
        """Test webhook with invalid token returns 401."""
        url = reverse("plex_webhook", kwargs={"token": "invalid-token"})
        response = self.client.post(url, data={}, content_type="application/json")
        self.assertEqual(response.status_code, 401)

    def test_tv_episode_mark_played(self):
        """Test webhook handles TV episode mark played event."""
        payload = {
            "event": "media.scrobble",
            "Account": {
                "title": "testuser",
            },
            "Metadata": {
                "type": "episode",
                "grandparentTitle": "Friends",
                "index": 1,
                "parentIndex": 1,
                "Guid": [
                    {
                        "id": "imdb://tt0583459",
                    },
                    {
                        "id": "tmdb://85987",
                    },
                    {
                        "id": "tvdb://303821",
                    },
                ],
            },
        }

        data = {
            "payload": json.dumps(payload),
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

    def test_movie_mark_played(self):
        """Test webhook handles movie mark played event."""
        payload = {
            "event": "media.scrobble",
            "Account": {
                "title": "testuser",
            },
            "Metadata": {
                "type": "movie",
                "title": "The Matrix",
                "Guid": [
                    {
                        "id": "imdb://tt0133093",
                    },
                    {
                        "id": "tmdb://603",
                    },
                    {
                        "id": "tvdb://169",
                    },
                ],
            },
        }

        data = {
            "payload": json.dumps(payload),
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
            "event": "media.scrobble",
            "Account": {
                "title": "testuser",
            },
            "Metadata": {
                "type": "movie",
                "title": "Perfect Blue",
                "Guid": [
                    {
                        "id": "imdb://tt0156887",
                    },
                    {
                        "id": "tmdb://10494",
                    },
                    {
                        "id": "tvdb://3807",
                    },
                ],
            },
        }

        data = {
            "payload": json.dumps(payload),
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

    def test_anime_episode_mark_played(self):
        """Test webhook handles anime episode mark played event."""
        payload = {
            "event": "media.scrobble",
            "Account": {
                "title": "testuser",
            },
            "Metadata": {
                "type": "episode",
                "grandparentTitle": "Frieren: Beyond Journey's End",
                "index": 1,
                "parentIndex": 1,
                "Guid": [
                    {
                        "id": "imdb://tt23861604",
                    },
                    {
                        "id": "tmdb://3946240",
                    },
                    {
                        "id": "tvdb://9350138",
                    },
                ],
            },
        }

        data = {
            "payload": json.dumps(payload),
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

    def test_ignored_event_types(self):
        """Test webhook ignores irrelevant event types."""
        payload = {
            "event": "media.something_else",
            "Account": {
                "title": "testuser",
            },
            "Metadata": {
                "type": "movie",
                "title": "Movie",
                "Guid": [
                    {
                        "id": "imdb://tt12345",
                    },
                    {
                        "id": "tmdb://12345",
                    },
                    {
                        "id": "tvdb://12345",
                    },
                ],
            },
        }

        data = {
            "payload": json.dumps(payload),
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
            "event": "media.scrobble",
            "Account": {
                "title": "testuser",
            },
            "Metadata": {
                "type": "movie",
                "title": "The Matrix",
                "Guid": [],
            },
        }
        data = {
            "payload": json.dumps(payload),
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
            "event": "media.scrobble",
            "Account": {
                "title": "testuser",
            },
            "Metadata": {
                "type": "movie",
                "title": "The Matrix",
                "Guid": [
                    {
                        "id": "imdb://tt0133093",
                    },
                    {
                        "id": "tmdb://603",
                    },
                    {
                        "id": "tvdb://169",
                    },
                ],
            },
        }

        data = {
            "payload": json.dumps(payload),
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

    def test_username_matching(self):
        """Test Plex username matching functionality."""
        test_cases = [
            # stored, incoming, should_match
            ("testuser", "testuser", True),  # Exact match
            ("testuser", "TestUser", True),  # Case insensitive
            ("testuser", " testuser ", True),  # Whitespace handling
            ("testuser", "testuser2", False),  # Different username
            ("testuser1,testuser2", "testuser1", True),  # First in list
            ("testuser1, testuser2", "testuser1", True),  # comma and space
            ("testuser1,testuser2", "testuser3", False),  # Not in list
        ]

        base_payload = {
            "event": "media.scrobble",
            "Metadata": {
                "type": "movie",
                "title": "Test Movie",
                "Guid": [{"id": "tmdb://123"}],
            },
        }

        for i, (stored_usernames, incoming_username, should_match) in enumerate(
            test_cases,
        ):
            with self.subTest(
                f"Case {i + 1}: {stored_usernames} vs {incoming_username}",
            ):
                self.user.plex_usernames = stored_usernames
                self.user.save()
                payload = base_payload.copy()
                payload["Account"] = {"title": incoming_username}

                response = self.client.post(
                    self.url,
                    data={"payload": json.dumps(payload)},
                    format="multipart",
                )

                if should_match:
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(Movie.objects.count(), 1)
                    Movie.objects.all().delete()  # Clean up for next test
                else:
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(Movie.objects.count(), 0)

    def test_anime_episode_anidb_guid_mark_played(self):
        """Test webhook handles anime episode with anidb guid."""
        payload = {
            "event": "media.scrobble",
            "Account": {"title": "testuser"},
            "Metadata": {
                "type": "episode",
                "index": 1,
                "parentIndex": 1,
                "guid": "com.plexapp.agents.hama://anidb-3651/1/1?lang=en",
            },
        }

        data = {"payload": json.dumps(payload)}

        response = self.client.post(
            self.url,
            data=data,
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)

        # Verify anime was created and marked as in progress
        anime = Anime.objects.get(
            item__media_id="849",
            user=self.user,
        )
        self.assertEqual(anime.status, Status.IN_PROGRESS.value)
        self.assertEqual(anime.progress, 1)

    def test_extract_external_ids(self):
        """Test extraction of external IDs from Plex webhook payload."""
        # Setup test payload
        payload = {
            "Metadata": {
                "Guid": [
                    {"id": "tmdb://12345"},
                    {"id": "imdb://tt67890"},
                    {"id": "tvdb://98765"},
                ],
            },
        }

        # Execute
        result = PlexWebhookProcessor()._extract_external_ids(payload)

        # Assert
        expected = {
            "tmdb_id": "12345",
            "imdb_id": "tt67890",
            "tvdb_id": "98765",
            "anidb_id": None,
        }

        self.assertEqual(result, expected)

    def test_extract_external_ids_from_guid_string(self):
        """Test extraction of external IDs from Plex webhook payload."""
        # Setup test payload
        payload = {
            "Metadata": {
                "guid": "com.plexapp.agents.hama://anidb-12345/1/1?lang=en",
            },
        }

        # Execute
        result = PlexWebhookProcessor()._extract_external_ids(payload)

        # Assert
        expected = {
            "tmdb_id": None,
            "imdb_id": None,
            "tvdb_id": None,
            "anidb_id": "12345",
        }

        self.assertEqual(result, expected)

    def test_extract_external_ids_missing_data(self):
        """Test handling of missing or empty data."""
        payload = {"Metadata": {"Guid": []}}

        result = PlexWebhookProcessor()._extract_external_ids(payload)

        expected = {
            "tmdb_id": None,
            "imdb_id": None,
            "tvdb_id": None,
            "anidb_id": None,
        }
        self.assertEqual(result, expected)

    def test_movie_rating(self):
        """Test webhook handles movie rating event."""
        movie_item = Item.objects.create(
            media_id="603",
            source="tmdb",
            media_type=MediaTypes.MOVIE.value,
            title="The Matrix",
            image="https://example.com/matrix.jpg",
        )
        Movie.objects.create(
            item=movie_item,
            user=self.user,
            progress=1,
            status=Status.COMPLETED.value,
            score=None,
        )

        payload = {
            "event": "media.rate",
            "Account": {
                "title": "testuser",
            },
            "Metadata": {
                "type": "movie",
                "title": "The Matrix",
                "userRating": 9,
                "Guid": [
                    {
                        "id": "imdb://tt0133093",
                    },
                    {
                        "id": "tmdb://603",
                    },
                ],
            },
        }

        data = {"payload": json.dumps(payload)}

        response = self.client.post(
            self.url,
            data=data,
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)

        movie = Movie.objects.get(item__media_id="603", user=self.user)
        self.assertEqual(movie.score, 9.0)

    def test_tv_rating(self):
        """Test webhook handles TV show rating event."""
        tv_item = Item.objects.create(
            media_id="1668",
            source="tmdb",
            media_type=MediaTypes.TV.value,
            title="Friends",
            image="https://example.com/friends.jpg",
        )
        TV.objects.create(
            item=tv_item,
            user=self.user,
            progress=1,
            status=Status.COMPLETED.value,
            score=None,
        )

        payload = {
            "event": "media.rate",
            "Account": {"title": "testuser"},
            "Metadata": {
                "type": "show",
                "title": "Friends",
                "userRating": 8,
                "Guid": [{"id": "tmdb://1668"}, {"id": "tvdb://10097"}],
            },
        }

        data = {"payload": json.dumps(payload)}

        response = self.client.post(
            self.url,
            data=data,
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)

        tv = TV.objects.get(item__media_id="1668", user=self.user)
        self.assertEqual(tv.score, 8.0)

    def test_anime_rating(self):
        """Test webhook handles anime rating event."""
        anime_item = Item.objects.create(
            media_id="437",
            source="mal",
            media_type=MediaTypes.ANIME.value,
            title="Perfect Blue",
            image="https://example.com/perfectblue.jpg",
        )
        Anime.objects.create(
            item=anime_item,
            user=self.user,
            progress=1,
            status=Status.COMPLETED.value,
            score=None,
        )

        payload = {
            "event": "media.rate",
            "Account": {"title": "testuser"},
            "Metadata": {
                "type": "movie",
                "title": "Perfect Blue",
                "userRating": 10,
                "Guid": [
                    {"id": "tmdb://10494"},
                    {"id": "imdb://tt0156887"},
                ],
            },
        }

        data = {"payload": json.dumps(payload)}

        response = self.client.post(
            self.url,
            data=data,
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)

        # Verify rating was saved
        anime = Anime.objects.get(item__media_id="437", user=self.user)
        self.assertEqual(anime.score, 10.0)

    def test_rating_untracked_media(self):
        """Test rating media that is not in user's list - should log but not create."""
        payload = {
            "event": "media.rate",
            "Account": {"title": "testuser"},
            "Metadata": {
                "type": "movie",
                "title": "Unknown Movie",
                "userRating": 7,
                "Guid": [{"id": "tmdb://99999999"}],
            },
        }

        data = {"payload": json.dumps(payload)}

        response = self.client.post(
            self.url,
            data=data,
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)

        self.assertEqual(Movie.objects.filter(user=self.user).count(), 0)

    def test_rating_update(self):
        """Test that rating can be updated (changed)."""
        movie_item = Item.objects.create(
            media_id="603",
            source="tmdb",
            media_type=MediaTypes.MOVIE.value,
            title="The Matrix",
            image="https://example.com/matrix.jpg",
        )
        Movie.objects.create(
            item=movie_item,
            user=self.user,
            progress=1,
            status=Status.COMPLETED.value,
            score=5.0,
        )

        payload = {
            "event": "media.rate",
            "Account": {"title": "testuser"},
            "Metadata": {
                "type": "movie",
                "title": "The Matrix",
                "userRating": 9,
                "Guid": [{"id": "tmdb://603"}],
            },
        }

        data = {"payload": json.dumps(payload)}

        response = self.client.post(
            self.url,
            data=data,
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)

        movie = Movie.objects.get(item__media_id="603", user=self.user)
        self.assertEqual(movie.score, 9.0)

    def test_get_rating(self):
        """Test extraction of rating from payload."""
        payload_with_rating = {"Metadata": {"userRating": 8}}
        payload_without_rating = {"Metadata": {"userRating": None}}
        payload_no_field = {"Metadata": {}}

        processor = PlexWebhookProcessor()

        self.assertEqual(processor._get_rating_from_payload(payload_with_rating), 8.0)
        self.assertIsNone(processor._get_rating_from_payload(payload_without_rating))
        self.assertIsNone(processor._get_rating_from_payload(payload_no_field))

    def test_is_rating_event(self):
        """Test detection of rating events."""
        rating_payload = {"event": "media.rate"}
        scrobble_payload = {"event": "media.scrobble"}
        play_payload = {"event": "media.play"}

        processor = PlexWebhookProcessor()

        self.assertTrue(processor._is_rating_event(rating_payload))
        self.assertFalse(processor._is_rating_event(scrobble_payload))
        self.assertFalse(processor._is_rating_event(play_payload))

    def test_anime_tv_rating(self):
        """Test webhook handles anime TV show rating event via TVDB mapping."""
        anime_item = Item.objects.create(
            media_id="52991",
            source="mal",
            media_type=MediaTypes.ANIME.value,
            title="Frieren: Beyond Journey's End",
            image="https://example.com/frieren.jpg",
        )
        Anime.objects.create(
            item=anime_item,
            user=self.user,
            progress=1,
            status=Status.IN_PROGRESS.value,
            score=None,
        )

        payload = {
            "event": "media.rate",
            "Account": {"title": "testuser"},
            "Metadata": {
                "type": "show",
                "title": "Frieren: Beyond Journey's End",
                "userRating": 9,
                "parentIndex": 1,
                "Guid": [
                    {"id": "tmdb://39462"},
                    {"id": "tvdb://9350138"},
                ],
            },
        }

        data = {"payload": json.dumps(payload)}

        response = self.client.post(
            self.url,
            data=data,
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)

        anime = Anime.objects.get(item__media_id="52991", user=self.user)
        self.assertEqual(anime.score, 9.0)

    def test_movie_played_with_rating(self):
        """Test webhook handles movie playback with rating."""
        payload = {
            "event": "media.scrobble",
            "Account": {"title": "testuser"},
            "Metadata": {
                "type": "movie",
                "title": "The Matrix",
                "userRating": 8,
                "Guid": [
                    {"id": "imdb://tt0133093"},
                    {"id": "tmdb://603"},
                ],
            },
        }

        data = {"payload": json.dumps(payload)}

        response = self.client.post(
            self.url,
            data=data,
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)

        movie = Movie.objects.get(item__media_id="603", user=self.user)
        self.assertEqual(movie.status, Status.COMPLETED.value)
        self.assertEqual(movie.progress, 1)
        self.assertEqual(movie.score, 8.0)

    def test_episode_played_with_rating(self):
        """Test webhook handles TV episode playback with rating."""
        payload = {
            "event": "media.scrobble",
            "Account": {"title": "testuser"},
            "Metadata": {
                "type": "episode",
                "grandparentTitle": "Friends",
                "index": 1,
                "parentIndex": 1,
                "userRating": 7,
                "Guid": [
                    {"id": "imdb://tt0583459"},
                    {"id": "tmdb://85987"},
                    {"id": "tvdb://303821"},
                ],
            },
        }

        data = {"payload": json.dumps(payload)}

        response = self.client.post(
            self.url,
            data=data,
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)

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

    def test_anime_episode_played_with_rating(self):
        """Test webhook handles anime episode playback with rating."""
        payload = {
            "event": "media.scrobble",
            "Account": {"title": "testuser"},
            "Metadata": {
                "type": "episode",
                "grandparentTitle": "Frieren: Beyond Journey's End",
                "index": 1,
                "parentIndex": 1,
                "userRating": 9,
                "Guid": [
                    {"id": "imdb://tt23861604"},
                    {"id": "tmdb://3946240"},
                    {"id": "tvdb://9350138"},
                ],
            },
        }

        data = {"payload": json.dumps(payload)}

        response = self.client.post(
            self.url,
            data=data,
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)

        anime = Anime.objects.get(item__media_id="52991", user=self.user)
        self.assertEqual(anime.status, Status.IN_PROGRESS.value)
        self.assertEqual(anime.progress, 1)
        self.assertEqual(anime.score, 9.0)

    def test_anime_movie_played_with_rating(self):
        """Test webhook handles anime movie playback with rating."""
        payload = {
            "event": "media.scrobble",
            "Account": {"title": "testuser"},
            "Metadata": {
                "type": "movie",
                "title": "Perfect Blue",
                "userRating": 10,
                "Guid": [
                    {"id": "imdb://tt0156887"},
                    {"id": "tmdb://10494"},
                ],
            },
        }

        data = {"payload": json.dumps(payload)}

        response = self.client.post(
            self.url,
            data=data,
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)

        anime = Anime.objects.get(item__media_id="437", user=self.user)
        self.assertEqual(anime.status, Status.COMPLETED.value)
        self.assertEqual(anime.progress, 1)
        self.assertEqual(anime.score, 10.0)

    def test_movie_rating_zero(self):
        """Test webhook handles movie rating of 0."""
        movie_item = Item.objects.create(
            media_id="603",
            source="tmdb",
            media_type=MediaTypes.MOVIE.value,
            title="The Matrix",
            image="https://example.com/matrix.jpg",
        )
        Movie.objects.create(
            item=movie_item,
            user=self.user,
            progress=1,
            status=Status.COMPLETED.value,
            score=None,
        )

        payload = {
            "event": "media.rate",
            "Account": {"title": "testuser"},
            "Metadata": {
                "type": "movie",
                "title": "The Matrix",
                "userRating": 0,
                "Guid": [{"id": "tmdb://603"}],
            },
        }

        data = {"payload": json.dumps(payload)}
        response = self.client.post(self.url, data=data, format="multipart")

        self.assertEqual(response.status_code, 200)
        movie = Movie.objects.get(item__media_id="603", user=self.user)
        self.assertEqual(movie.score, 0.0)

    def test_rating_out_of_range(self):
        """Test that out-of-range ratings are ignored."""
        payload_high = {"Metadata": {"userRating": 15}}
        payload_negative = {"Metadata": {"userRating": -1}}

        processor = PlexWebhookProcessor()

        self.assertIsNone(processor._get_rating_from_payload(payload_high))
        self.assertIsNone(processor._get_rating_from_payload(payload_negative))

    def test_rating_on_completed_movie(self):
        """Test that rating a completed movie updates the score.

        Regression test for issue where rating updates were not applied
        to already-completed media items.
        """
        movie_item = Item.objects.create(
            media_id="603",
            source="tmdb",
            media_type=MediaTypes.MOVIE.value,
            title="The Matrix",
            image="https://example.com/matrix.jpg",
        )
        Movie.objects.create(
            item=movie_item,
            user=self.user,
            progress=1,
            status=Status.COMPLETED.value,
            score=None,
        )

        payload = {
            "event": "media.rate",
            "Account": {"title": "testuser"},
            "Metadata": {
                "type": "movie",
                "title": "The Matrix",
                "userRating": 8,
                "Guid": [{"id": "tmdb://603"}],
            },
        }

        data = {"payload": json.dumps(payload)}
        response = self.client.post(self.url, data=data, format="multipart")

        self.assertEqual(response.status_code, 200)
        movie = Movie.objects.get(item__media_id="603", user=self.user)
        self.assertEqual(movie.score, 8.0)

    def test_rating_on_completed_anime(self):
        """Test that rating a completed anime updates the score.

        Regression test for issue where rating updates were not applied
        to already-completed anime items.
        """
        anime_item = Item.objects.create(
            media_id="437",
            source="mal",
            media_type=MediaTypes.ANIME.value,
            title="Perfect Blue",
            image="https://example.com/perfectblue.jpg",
        )
        Anime.objects.create(
            item=anime_item,
            user=self.user,
            progress=1,
            status=Status.COMPLETED.value,
            score=None,
        )

        payload = {
            "event": "media.rate",
            "Account": {"title": "testuser"},
            "Metadata": {
                "type": "movie",
                "title": "Perfect Blue",
                "userRating": 9,
                "Guid": [
                    {"id": "tmdb://10494"},
                    {"id": "imdb://tt0156887"},
                ],
            },
        }

        data = {"payload": json.dumps(payload)}
        response = self.client.post(self.url, data=data, format="multipart")

        self.assertEqual(response.status_code, 200)
        anime = Anime.objects.get(item__media_id="437", user=self.user)
        self.assertEqual(anime.score, 9.0)
