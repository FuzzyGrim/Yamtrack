from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from app.models import (
    CustomBackdropPreference,
    CustomPosterPreference,
    DiaryEntry,
    Item,
    MediaTypes,
    Sources,
)


class ApiV1FoundationTests(TestCase):
    """Smoke tests for the v1 mobile API foundation."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def test_health_is_public(self):
        response = self.client.get("/api/v1/health/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "ok")

    def test_meta_is_public(self):
        response = self.client.get("/api/v1/meta/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("movie", response.data["media_types"])
        self.assertIn("Completed", response.data["status_choices"])

    def test_register_returns_tokens_and_user(self):
        response = self.client.post(
            "/api/v1/auth/register/",
            {
                "username": "iosuser",
                "email": "ios@example.com",
                "password": "strong-password-123",
                "password_confirm": "strong-password-123",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertEqual(response.data["user"]["username"], "iosuser")

    def test_login_and_me(self):
        get_user_model().objects.create_user(
            username="mobile",
            email="mobile@example.com",
            password="strong-password-123",
        )

        login = self.client.post(
            "/api/v1/auth/login/",
            {
                "username_or_email": "mobile@example.com",
                "password": "strong-password-123",
            },
            format="json",
        )
        self.assertEqual(login.status_code, status.HTTP_200_OK)

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
        response = self.client.get("/api/v1/me/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["username"], "mobile")

    @patch("api.services.media.provider_services.search")
    def test_media_search_contract(self, search_mock):
        user = get_user_model().objects.create_user(
            username="searcher",
            password="strong-password-123",
        )
        self.client.force_authenticate(user)
        search_mock.return_value = {
            "results": [
                {
                    "media_id": "550",
                    "title": "Fight Club",
                    "image": "https://example.com/fight-club.jpg",
                    "release_date": "1999-10-15",
                },
            ],
        }

        response = self.client.get("/api/v1/media/search/?media_type=movie&q=fight")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["results"][0]["ref"]["source"], "tmdb")
        self.assertEqual(response.data["results"][0]["ref"]["media_type"], "movie")
        self.assertEqual(response.data["results"][0]["title"], "Fight Club")

    @patch("app.providers.mdblist.get_media_ratings")
    @patch("api.services.media.provider_services.get_media_metadata")
    def test_media_detail_includes_synopsis_and_external_ratings(self, metadata_mock, ratings_mock):
        metadata_mock.return_value = {
            "media_id": "550",
            "media_type": "movie",
            "source": "tmdb",
            "title": "Fight Club",
            "image": "https://example.com/fight-club.jpg",
            "backdrop_path": "/rr7E0NoGKxvbkb89eR1GwfoYjpA.jpg",
            "synopsis": "Soap, clubs, and insomnia.",
            "score": "8.4",
            "score_count": 1000,
            "genres": [{"name": "Drama"}, "Thriller"],
            "details": {"runtime": "2h 19m"},
            "cast": [{"person_id": 819, "name": "Edward Norton", "character": "Narrator", "image": "/ed.jpg"}],
            "crew": [{"person_id": 7467, "name": "David Fincher", "roles": ["Director"], "job": "Director"}],
            "related": {
                "Fight Club Collection": [
                    {
                        "media_id": "551",
                        "media_type": "movie",
                        "source": "tmdb",
                        "title": "Fight Club 2",
                        "image": "https://example.com/fight-club-2.jpg",
                    },
                ],
                "recommendations": [
                    {
                        "media_id": "680",
                        "media_type": "movie",
                        "source": "tmdb",
                        "title": "Pulp Fiction",
                        "image": "https://example.com/pulp.jpg",
                    },
                ],
                "seasons": [
                    {
                        "media_id": "550",
                        "media_type": "season",
                        "source": "tmdb",
                        "season_number": 1,
                        "title": "Season 1",
                    },
                ],
            },
        }
        ratings_mock.return_value = {
            "imdb": {"value": "8.8", "votes": 2300000},
            "letterboxd": {"value": "4.3", "votes": 500000},
            "tomatoes": {"value": "79%", "votes": 100},
        }
        user = get_user_model().objects.create_user(username="viewer", password="strong-password-123")
        self.client.force_authenticate(user)
        item = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="550",
            title="Fight Club",
        )
        consumed_at = timezone.now()
        DiaryEntry.objects.create(user=user, item=item, consumed_at=consumed_at, rating="10.0", visibility="public")

        response = self.client.get("/api/v1/media/tmdb/movie/550/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["overview"], "Soap, clubs, and insomnia.")
        self.assertEqual(response.data["synopsis"], "Soap, clubs, and insomnia.")
        self.assertEqual(
            response.data["backdrop_url"],
            "https://image.tmdb.org/t/p/original/rr7E0NoGKxvbkb89eR1GwfoYjpA.jpg",
        )
        self.assertIsNone(response.data["custom_backdrop_url"])
        self.assertEqual(
            [(rating["source"], rating["value"]) for rating in response.data["external_ratings"]],
            [("TMDB", "8.4"), ("IMDb", "8.8"), ("Letterboxd", "4.3"), ("Rotten Tomatoes", "79%")],
        )
        self.assertEqual(response.data["details"]["genres"], ["Drama", "Thriller"])
        self.assertEqual(response.data["cast"][0]["name"], "Edward Norton")
        self.assertEqual(response.data["crew"][0]["role"], "Director")
        self.assertEqual(response.data["related_sections"][0]["id"], "collection")
        self.assertEqual(response.data["related_sections"][1]["items"][0]["title"], "Pulp Fiction")
        self.assertNotIn("seasons", [section["id"] for section in response.data["related_sections"]])
        self.assertEqual(response.data["user_state"]["diary_rating"], "10.0")
        self.assertEqual(response.data["user_state"]["diary_consumed_at"], consumed_at)

    @patch("app.providers.mdblist.get_media_ratings", return_value={})
    @patch("api.services.media.provider_services.get_media_metadata")
    def test_tv_detail_exposes_seasons(self, metadata_mock, _ratings_mock):
        metadata_mock.return_value = {
            "media_id": "1399",
            "media_type": "tv",
            "source": "tmdb",
            "title": "Game of Thrones",
            "image": "https://example.com/got.jpg",
            "backdrop_path": "/9xxLWtnFxkpJ2h1uthpvCRK6vta.jpg",
            "related": {
                "seasons": [
                    {
                        "media_id": "1399",
                        "media_type": "season",
                        "source": "tmdb",
                        "season_number": 1,
                        "season_title": "Season 1",
                        "max_progress": 10,
                        "image": "https://example.com/s1.jpg",
                        "first_air_date": "2011-04-17",
                    },
                ],
            },
        }

        detail = self.client.get("/api/v1/media/tmdb/tv/1399/")
        seasons = self.client.get("/api/v1/media/tmdb/tv/1399/seasons/")

        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.data["seasons"][0]["title"], "Season 1")
        self.assertEqual(
            detail.data["backdrop_url"],
            "https://image.tmdb.org/t/p/original/9xxLWtnFxkpJ2h1uthpvCRK6vta.jpg",
        )
        self.assertEqual(seasons.data["seasons"], detail.data["seasons"])

    @patch("app.providers.mdblist.get_media_ratings", return_value={})
    @patch("api.services.media.provider_services.get_media_metadata")
    def test_media_detail_backdrop_url_is_null_without_backdrop(self, metadata_mock, _ratings_mock):
        metadata_mock.return_value = {
            "media_id": "550",
            "media_type": "movie",
            "source": "tmdb",
            "title": "Fight Club",
            "image": "https://example.com/fight-club.jpg",
        }

        response = self.client.get("/api/v1/media/tmdb/movie/550/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["backdrop_url"])
        self.assertIsNone(response.data["custom_backdrop_url"])

    @patch("app.providers.mdblist.get_media_ratings", return_value={})
    @patch("api.services.media.provider_services.get_media_metadata")
    def test_season_detail_exposes_episodes_with_runtime_string(self, metadata_mock, _ratings_mock):
        metadata_mock.return_value = {
            "media_id": "1399",
            "media_type": "season",
            "source": "tmdb",
            "title": "Game of Thrones",
            "season_number": 1,
            "image": "https://example.com/s1.jpg",
            "episodes": [
                {
                    "episode_number": 1,
                    "name": "Winter Is Coming",
                    "overview": "The beginning.",
                    "air_date": "2011-04-17",
                    "runtime": 62,
                    "still_path": "/ep1.jpg",
                    "vote_average": 8.2,
                },
            ],
        }

        detail = self.client.get("/api/v1/media/tmdb/season/1399/?season_number=1")
        episodes = self.client.get("/api/v1/media/tmdb/tv/1399/seasons/1/episodes/")

        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.data["episodes"][0]["runtime"], "1h 2m")
        self.assertEqual(episodes.data["episodes"], detail.data["episodes"])

    @patch("api.services.media.provider_services.get_media_metadata")
    def test_mal_anime_detail_exposes_genres_related_and_rating(self, metadata_mock):
        metadata_mock.return_value = {
            "media_id": "1",
            "media_type": "anime",
            "source": "mal",
            "title": "Cowboy Bebop",
            "image": "https://example.com/bebop.jpg",
            "genres": ["Action", {"name": "Sci-Fi"}],
            "score": "8.75",
            "score_count": 100,
            "related": {
                "related_anime": [
                    {"media_id": "5", "media_type": "anime", "source": "mal", "title": "Movie"},
                ],
                "recommendations": [
                    {"media_id": "6", "media_type": "anime", "source": "mal", "title": "Champloo"},
                ],
            },
        }

        response = self.client.get("/api/v1/media/mal/anime/1/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["details"]["genres"], ["Action", "Sci-Fi"])
        self.assertEqual([section["id"] for section in response.data["related_sections"]], ["related_anime", "recommendations"])
        self.assertEqual(response.data["external_ratings"][0]["source"], "MAL")

    @patch("api.services.media.provider_services.get_media_metadata")
    def test_openlibrary_book_detail_exposes_other_editions(self, metadata_mock):
        metadata_mock.return_value = {
            "media_id": "OL1M",
            "media_type": "book",
            "source": "openlibrary",
            "title": "A Book",
            "image": "https://example.com/book.jpg",
            "score": "4.1",
            "related": {
                "other_editions": [
                    {"media_id": "OL2M", "media_type": "book", "source": "openlibrary", "title": "Paperback"},
                ],
            },
        }

        response = self.client.get("/api/v1/media/openlibrary/book/OL1M/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["related_sections"][0]["id"], "other_editions")
        self.assertEqual(response.data["external_ratings"][0]["max_value"], "5")

    @patch("api.services.media.provider_services.get_media_metadata")
    def test_game_detail_exposes_all_related_as_related_section(self, metadata_mock):
        metadata_mock.return_value = {
            "media_id": "1020",
            "media_type": "game",
            "source": "igdb",
            "title": "Space Game",
            "image": "https://example.com/space.jpg",
            "score": "8.1",
            "related": {
                "dlcs": [
                    {
                        "media_id": "1022",
                        "media_type": "game",
                        "source": "igdb",
                        "title": "Space Game DLC",
                    },
                ],
                "all_related": [
                    {
                        "media_id": "1021",
                        "media_type": "game",
                        "source": "igdb",
                        "title": "Space Game 2",
                        "image": "https://example.com/space2.jpg",
                    },
                ],
            },
        }

        response = self.client.get("/api/v1/media/igdb/game/1020/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["related_sections"][0]["id"], "dlcs")
        self.assertEqual(response.data["related_sections"][1]["id"], "all_related")
        self.assertEqual(response.data["external_ratings"][0]["source"], "IGDB")

    def test_community_stats_include_truthful_rating_distribution(self):
        user = get_user_model().objects.create_user(username="rater", password="strong-password-123")
        other = get_user_model().objects.create_user(username="other", password="strong-password-123")
        item = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="550",
            title="Fight Club",
            image="https://example.com/fight-club.jpg",
        )
        now = timezone.now()
        DiaryEntry.objects.create(user=user, item=item, consumed_at=now, rating="8.0", visibility="public")
        DiaryEntry.objects.create(user=other, item=item, consumed_at=now, rating="8.0", visibility="followers")
        DiaryEntry.objects.create(user=other, item=item, consumed_at=now, rating="9.0", visibility="private")
        DiaryEntry.objects.create(user=other, item=item, consumed_at=now, rating=None, visibility="public")

        response = self.client.get("/api/v1/media/tmdb/movie/550/community/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["average_rating"], "8.00")
        self.assertEqual(response.data["rating_count"], 2)
        self.assertEqual(response.data["rating_distribution"], [{"rating": "8.0", "count": 2}])

    def test_media_reviews_endpoint_returns_public_review_cards(self):
        user = get_user_model().objects.create_user(username="reviewer", password="strong-password-123")
        item = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="550",
            title="Fight Club",
            image="https://example.com/fight-club.jpg",
        )
        DiaryEntry.objects.create(
            user=user,
            item=item,
            consumed_at=timezone.now(),
            rating="9.0",
            review="Sharp and strange.",
            review_title="Mayhem",
            visibility="public",
        )
        DiaryEntry.objects.create(
            user=user,
            item=item,
            consumed_at=timezone.now(),
            review="Hidden.",
            visibility="private",
        )

        response = self.client.get("/api/v1/media/tmdb/movie/550/reviews/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["review"], "Sharp and strange.")

    @patch("app.providers.tmdb.get_poster_images")
    def test_media_posters_endpoint_requires_auth_and_returns_original_first(self, posters_mock):
        user = get_user_model().objects.create_user(username="poster", password="strong-password-123")
        Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="550",
            title="Fight Club",
            image="https://example.com/original.jpg",
        )
        posters_mock.return_value = [
            {
                "url": "https://example.com/high.jpg",
                "thumbnail_url": "https://example.com/high-thumb.jpg",
                "width": 1000,
                "height": 1500,
                "aspect_ratio": 0.667,
                "vote_average": 8.5,
                "vote_count": 20,
                "language": "en",
            },
            {
                "url": "https://example.com/low.jpg",
                "thumbnail_url": "https://example.com/low-thumb.jpg",
                "width": 1000,
                "height": 1500,
                "aspect_ratio": 0.667,
                "vote_average": 7.0,
                "vote_count": 10,
                "language": None,
            },
        ]

        anonymous = self.client.get("/api/v1/media/tmdb/movie/550/posters/")
        self.client.force_authenticate(user)
        response = self.client.get("/api/v1/media/tmdb/movie/550/posters/")

        self.assertEqual(anonymous.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [poster["url"] for poster in response.data["posters"]],
            [
                "https://example.com/original.jpg",
                "https://example.com/high.jpg",
                "https://example.com/low.jpg",
            ],
        )
        self.assertTrue(response.data["posters"][0]["is_original"])

    def test_media_posters_endpoint_rejects_unsupported_media(self):
        user = get_user_model().objects.create_user(username="poster2", password="strong-password-123")
        self.client.force_authenticate(user)

        response = self.client.get("/api/v1/media/mal/anime/1/posters/")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("api.services.media.build_accent_palette", return_value={"accent": "#123456", "contrast": "#ffffff"})
    @patch("api.services.media.compute_and_store_poster_accent", return_value="#123456")
    def test_media_poster_save_updates_preference_and_item(self, _accent_mock, _palette_mock):
        user = get_user_model().objects.create_user(username="poster3", password="strong-password-123")
        item = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            media_id="1399",
            title="Game of Thrones",
            image="https://example.com/original.jpg",
        )
        self.client.force_authenticate(user)

        response = self.client.put(
            "/api/v1/media/tmdb/tv/1399/poster/",
            {"poster_url": "https://example.com/new.jpg"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["custom_poster_url"], "https://example.com/new.jpg")
        item.refresh_from_db()
        self.assertEqual(item.image, "https://example.com/new.jpg")
        self.assertEqual(item.poster_accent_color, "#123456")
        self.assertEqual(
            CustomPosterPreference.objects.get(user=user, item=item).custom_image_url,
            "https://example.com/new.jpg",
        )

    @patch("api.services.media.provider_services.get_media_metadata")
    @patch("app.providers.tmdb.get_backdrop_images")
    def test_media_backdrops_endpoint_requires_auth_and_returns_original_first(self, backdrops_mock, metadata_mock):
        user = get_user_model().objects.create_user(username="backdrop", password="strong-password-123")
        item = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="550",
            title="Fight Club",
            image="https://example.com/poster.jpg",
        )
        CustomBackdropPreference.objects.create(
            user=user,
            item=item,
            custom_image_url="https://example.com/high.jpg",
        )
        metadata_mock.return_value = {
            "title": "Fight Club",
            "image": "https://example.com/poster.jpg",
            "backdrop_path": "/original.jpg",
        }
        backdrops_mock.return_value = [
            {
                "url": "https://example.com/high.jpg",
                "thumbnail_url": "https://example.com/high-thumb.jpg",
                "width": 1920,
                "height": 1080,
                "aspect_ratio": 1.778,
                "vote_average": 8.5,
                "vote_count": 20,
                "language": "en",
            },
        ]

        anonymous = self.client.get("/api/v1/media/tmdb/movie/550/backdrops/")
        self.client.force_authenticate(user)
        response = self.client.get("/api/v1/media/tmdb/movie/550/backdrops/")

        self.assertEqual(anonymous.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [backdrop["url"] for backdrop in response.data["backdrops"]],
            [
                "https://image.tmdb.org/t/p/original/original.jpg",
                "https://example.com/high.jpg",
            ],
        )
        self.assertTrue(response.data["backdrops"][0]["is_original"])
        self.assertFalse(response.data["backdrops"][0]["is_selected"])
        self.assertTrue(response.data["backdrops"][1]["is_selected"])

    def test_media_backdrops_endpoint_rejects_unsupported_media(self):
        user = get_user_model().objects.create_user(username="backdrop2", password="strong-password-123")
        self.client.force_authenticate(user)

        response = self.client.get("/api/v1/media/mal/anime/1/backdrops/")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_media_backdrop_save_updates_preference_without_changing_item_image(self):
        user = get_user_model().objects.create_user(username="backdrop3", password="strong-password-123")
        item = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            media_id="1399",
            title="Game of Thrones",
            image="https://example.com/original-poster.jpg",
        )
        self.client.force_authenticate(user)

        response = self.client.put(
            "/api/v1/media/tmdb/tv/1399/backdrop/",
            {"backdrop_url": "https://example.com/new-backdrop.jpg"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["custom_backdrop_url"], "https://example.com/new-backdrop.jpg")
        item.refresh_from_db()
        self.assertEqual(item.image, "https://example.com/original-poster.jpg")
        self.assertEqual(
            CustomBackdropPreference.objects.get(user=user, item=item).custom_image_url,
            "https://example.com/new-backdrop.jpg",
        )

    @patch("app.providers.mdblist.get_media_ratings", return_value={})
    @patch("api.services.media.provider_services.get_media_metadata")
    def test_media_detail_includes_custom_backdrop_url_when_preference_exists(self, metadata_mock, _ratings_mock):
        user = get_user_model().objects.create_user(username="backdrop4", password="strong-password-123")
        item = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="550",
            title="Fight Club",
            image="https://example.com/poster.jpg",
        )
        CustomBackdropPreference.objects.create(
            user=user,
            item=item,
            custom_image_url="https://example.com/custom-backdrop.jpg",
        )
        metadata_mock.return_value = {
            "media_id": "550",
            "media_type": "movie",
            "source": "tmdb",
            "title": "Fight Club",
            "image": "https://example.com/poster.jpg",
            "backdrop_path": "/default-backdrop.jpg",
        }
        self.client.force_authenticate(user)

        response = self.client.get("/api/v1/media/tmdb/movie/550/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["backdrop_url"], "https://image.tmdb.org/t/p/original/default-backdrop.jpg")
        self.assertEqual(response.data["custom_backdrop_url"], "https://example.com/custom-backdrop.jpg")
