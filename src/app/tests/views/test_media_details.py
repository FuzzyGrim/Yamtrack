from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.models import (
    Item,
    MediaTypes,
    Movie,
    Sources,
    Status,
)


class MediaDetailsViewTests(TestCase):
    """Test the media details views."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_loads_critic_scores(self, mock_get_metadata):
        """Test that the details page loads the critic scores after rendering."""
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                    "title": "test-movie",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'hx-get="'
            + reverse(
                "critic_scores",
                args=[Sources.TMDB.value, MediaTypes.MOVIE.value, "238"],
            )
            + '"',
        )
        self.assertContains(response, "CRITIC SCORES")
        # nothing is fetched while rendering the page
        self.assertNotContains(response, "TOMATOMETER")

        # media types without critic scores have no placeholder
        mock_get_metadata.return_value["media_type"] = MediaTypes.GAME.value
        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.IGDB.value,
                    "media_type": MediaTypes.GAME.value,
                    "media_id": "1",
                    "title": "test-game",
                },
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "CRITIC SCORES")

    @patch("app.providers.rottentomatoes.media_scores")
    @patch("app.providers.omdb.ratings")
    @patch("app.providers.services.get_media_metadata")
    def test_critic_scores(self, mock_get_metadata, mock_ratings, mock_rt_scores):
        """Test the critic score cards loaded into the details page."""
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "imdb_id": "tt0000238",
        }
        mock_ratings.return_value = {
            "rotten_tomatoes": {"value": "83", "suffix": "%"},
            "imdb": {"value": "7.6", "suffix": "/10", "votes": "228,380"},
            "metacritic": {"value": "68", "suffix": "/100"},
        }
        mock_rt_scores.return_value = {
            "title": "Test Movie: The Sequel",
            "url": "https://www.rottentomatoes.com/m/test_movie_the_sequel",
            "year": "2023",
            "exact": False,
            "tomatometer": {"value": "85", "suffix": "%", "count": "250 reviews"},
            "popcornmeter": {"value": "91", "suffix": "%", "count": "5,000+ Ratings"},
        }

        response = self.client.get(
            reverse(
                "critic_scores",
                args=[Sources.TMDB.value, MediaTypes.MOVIE.value, "238"],
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/critic_scores.html")
        mock_ratings.assert_called_once_with("tt0000238")
        mock_rt_scores.assert_called_once_with(mock_get_metadata.return_value)
        self.assertContains(response, "IMDB SCORE")
        self.assertContains(response, "228,380 votes")
        self.assertContains(response, "METASCORE")
        self.assertContains(response, ">68<")
        # scraped scores are preferred over the OMDb Tomatometer
        self.assertContains(response, "TOMATOMETER")
        self.assertContains(response, ">85<")
        self.assertNotContains(response, ">83<")
        self.assertContains(response, "POPCORNMETER")
        self.assertContains(response, ">91<")
        # a non-exact title match shows the matched title, linked to its page
        self.assertContains(response, "Test Movie: The Sequel")
        self.assertContains(response, mock_rt_scores.return_value["url"])
        self.assertNotContains(response, "250 reviews")

    @patch("app.providers.rottentomatoes.media_scores")
    @patch("app.providers.omdb.ratings")
    @patch("app.providers.services.get_media_metadata")
    def test_critic_scores_omdb_tomatometer_fallback(
        self, mock_get_metadata, mock_ratings, mock_rt_scores
    ):
        """Test that the OMDb Tomatometer is used without a Rotten Tomatoes match."""
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "imdb_id": "tt0000238",
        }
        mock_ratings.return_value = {"rotten_tomatoes": {"value": "83", "suffix": "%"}}
        mock_rt_scores.return_value = {}

        response = self.client.get(
            reverse(
                "critic_scores",
                args=[Sources.TMDB.value, MediaTypes.MOVIE.value, "238"],
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "TOMATOMETER")
        self.assertContains(response, ">83<")
        self.assertNotContains(response, "POPCORNMETER")

    @patch("app.providers.rottentomatoes.media_scores")
    @patch("app.providers.omdb.ratings")
    @patch("app.providers.services.get_media_metadata")
    def test_critic_scores_empty(self, mock_get_metadata, mock_ratings, mock_rt_scores):
        """Test that no cards are returned without scores, removing the placeholder."""
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
        }
        mock_ratings.return_value = {}
        mock_rt_scores.return_value = {}

        response = self.client.get(
            reverse(
                "critic_scores",
                args=[Sources.TMDB.value, MediaTypes.MOVIE.value, "238"],
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.strip(), b"")

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_view(self, mock_get_metadata):
        """Test the media details view."""
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "overview": "Test overview",
            "release_date": "2023-01-01",
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                    "title": "test-movie",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/media_details.html")

        self.assertIn("media", response.context)
        self.assertEqual(response.context["media"]["title"], "Test Movie")

        mock_get_metadata.assert_called_once_with(
            MediaTypes.MOVIE.value,
            "238",
            Sources.TMDB.value,
        )

    @patch("app.providers.rottentomatoes.season_scores")
    @patch("app.providers.services.get_media_metadata")
    def test_critic_scores_season(self, mock_get_metadata, mock_season_scores):
        """Test the critic score cards for a season."""
        tv_metadata = {
            "title": "Test TV Show",
            "media_id": "1668",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "season/2": {"title": "Season 2", "episodes": []},
        }
        mock_get_metadata.return_value = tv_metadata
        mock_season_scores.return_value = {
            "title": "Test TV Show",
            "url": "https://www.rottentomatoes.com/tv/test_tv_show/s02",
            "year": None,
            "season": 2,
            "exact": True,
            "tomatometer": {"value": "97", "suffix": "%", "count": "99 reviews"},
            "popcornmeter": {"value": "98", "suffix": "%", "count": "5,000+ Ratings"},
        }

        response = self.client.get(
            reverse(
                "critic_scores",
                args=[Sources.TMDB.value, MediaTypes.SEASON.value, "1668", 2],
            ),
        )

        self.assertEqual(response.status_code, 200)
        mock_get_metadata.assert_called_once_with(
            "tv_with_seasons", "1668", Sources.TMDB.value, [2]
        )
        mock_season_scores.assert_called_once_with(tv_metadata, 2)
        self.assertContains(response, "TOMATOMETER")
        self.assertContains(response, ">97<")
        self.assertContains(response, "POPCORNMETER")
        self.assertContains(response, ">98<")
        self.assertContains(response, "5,000+ Ratings")
        self.assertContains(response, "Test TV Show Season 2 on Rotten Tomatoes")

    @patch("app.providers.services.get_media_metadata")
    @patch("app.providers.tmdb.process_episodes")
    def test_season_details_view(self, mock_process_episodes, mock_get_metadata):
        """Test the season details view."""
        self.user.obfuscate_unseen_episodes = True
        self.user.save(update_fields=["obfuscate_unseen_episodes"])

        mock_get_metadata.return_value = {
            "title": "Test TV Show",
            "media_id": "1668",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "image": "http://example.com/image.jpg",
            "season/1": {
                "title": "Season 1",
                "media_id": "1668",
                "media_type": MediaTypes.SEASON.value,
                "source": Sources.TMDB.value,
                "image": "http://example.com/season.jpg",
                "episodes": [],
            },
        }

        mock_process_episodes.return_value = [
            {
                "media_id": "1668",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.EPISODE.value,
                "season_number": 1,
                "episode_number": 1,
                "title": "Episode 1",
                "name": "Episode 1",
                "air_date": "2023-01-01",
                "watched": False,
            },
        ]

        response = self.client.get(
            reverse(
                "season_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "1668",
                    "title": "test-tv-show",
                    "season_number": 1,
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/media_details.html")

        self.assertIn("media", response.context)
        self.assertEqual(response.context["media"]["title"], "Season 1")
        self.assertEqual(len(response.context["media"]["episodes"]), 1)
        self.assertContains(response, "line-clamp-1 blur cursor-pointer")
        self.assertContains(
            response,
            'hx-get="'
            + reverse(
                "critic_scores",
                args=[Sources.TMDB.value, MediaTypes.SEASON.value, "1668", 1],
            )
            + '"',
        )

        mock_get_metadata.assert_called_once_with(
            "tv_with_seasons",
            "1668",
            Sources.TMDB.value,
            [1],
        )

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_refreshes_missing_item_image(self, mock_get_metadata):
        """Item.image is updated when missing and live metadata has one."""
        live_image = "http://example.com/fresh.jpg"
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": live_image,
            "max_progress": 1,
            "overview": "Test overview",
            "release_date": "2023-01-01",
        }

        item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image=settings.IMG_NONE,
        )
        Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                    "title": "test-movie",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.image, live_image)

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_keeps_existing_item_image(self, mock_get_metadata):
        """Item.image is left alone when already set."""
        existing_image = "http://example.com/stored.jpg"
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/fresh.jpg",
            "max_progress": 1,
            "overview": "Test overview",
            "release_date": "2023-01-01",
        }

        item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image=existing_image,
        )
        Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                    "title": "test-movie",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.image, existing_image)
