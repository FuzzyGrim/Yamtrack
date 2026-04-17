from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import (
    Anime,
    Episode,
    Item,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Status,
)
from users.models import HomeSortChoices


class HomeViewTests(TestCase):
    """Test the home view."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)
        self.metadata_patcher = patch("app.providers.services.get_media_metadata")
        self.mock_get_media_metadata = self.metadata_patcher.start()
        self.addCleanup(self.metadata_patcher.stop)

        def mock_get_media_metadata(
            media_type,
            _media_id,
            _source,
            season_numbers=None,
            _episode_number=None,
        ):
            if media_type == MediaTypes.TV.value:
                return {
                    "title": "Test TV Show",
                    "image": "http://example.com/image.jpg",
                    "details": {"seasons": 1},
                    "related": {
                        "seasons": [
                            {
                                "season_number": 1,
                                "image": "http://example.com/image.jpg",
                            },
                        ],
                    },
                }

            if media_type == "tv_with_seasons":
                season_number = season_numbers[0]
                return {
                    "title": "Test TV Show",
                    "image": "http://example.com/image.jpg",
                    "details": {"seasons": 1},
                    f"season/{season_number}": {
                        "episodes": [{"id": i} for i in range(1, 11)],
                    },
                    "related": {
                        "seasons": [
                            {
                                "season_number": season_number,
                                "image": "http://example.com/image.jpg",
                            },
                        ],
                    },
                }

            if media_type == MediaTypes.SEASON.value:
                return {
                    "title": "Test TV Show",
                    "image": "http://example.com/image.jpg",
                    "max_progress": 10,
                    "season/1": {
                        "episodes": [{"id": i} for i in range(1, 11)],
                    },
                }

            if media_type == MediaTypes.ANIME.value:
                return {
                    "title": "Test Anime",
                    "image": "http://example.com/image.jpg",
                    "max_progress": 24,
                }

            if media_type == MediaTypes.MOVIE.value:
                return {
                    "title": "Planned Movie",
                    "image": "http://example.com/image.jpg",
                    "max_progress": 1,
                }

            return {"max_progress": None}

        self.mock_get_media_metadata.side_effect = mock_get_media_metadata

        season_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test TV Show",
            image="http://example.com/image.jpg",
            season_number=1,
        )
        season = Season.objects.create(
            item=season_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        for i in range(1, 6):  # Create 5 episodes
            episode_item = Item.objects.create(
                media_id="1668",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title="Test TV Show",
                image="http://example.com/image.jpg",
                season_number=1,
                episode_number=i,
            )
            Episode.objects.create(
                item=episode_item,
                related_season=season,
                end_date=timezone.now() - timezone.timedelta(days=i),
            )

        anime_item = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Test Anime",
            image="http://example.com/image.jpg",
        )
        Anime.objects.create(
            item=anime_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=10,
        )

        movie_item = Item.objects.create(
            media_id="10",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Planned Movie",
            image="http://example.com/image.jpg",
        )
        Movie.objects.create(
            item=movie_item,
            user=self.user,
            status=Status.PLANNING.value,
        )

    def test_home_view(self):
        """Test the home view displays in-progress and planning media."""
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/home.html")

        self.assertIn("home_sections", response.context)

        sections_by_key = {
            section["key"]: section for section in response.context["home_sections"]
        }
        self.assertIn(Status.IN_PROGRESS.value, sections_by_key)
        self.assertIn(Status.PLANNING.value, sections_by_key)

        in_progress_section = sections_by_key[Status.IN_PROGRESS.value]
        planning_section = sections_by_key[Status.PLANNING.value]

        self.assertIn(MediaTypes.SEASON.value, in_progress_section["media_types"])
        self.assertIn(MediaTypes.ANIME.value, in_progress_section["media_types"])
        self.assertIn(MediaTypes.MOVIE.value, planning_section["media_types"])

        self.assertIn("sort_choices", response.context)
        self.assertEqual(response.context["sort_choices"], HomeSortChoices.choices)
        self.assertEqual(in_progress_section["count"], 2)
        self.assertEqual(planning_section["count"], 1)

        season = in_progress_section["media_types"][MediaTypes.SEASON.value]
        self.assertEqual(len(season["items"]), 1)
        self.assertEqual(season["items"][0].progress, 5)

        planning_movies = planning_section["media_types"][MediaTypes.MOVIE.value]
        self.assertEqual(len(planning_movies["items"]), 1)
        self.assertEqual(planning_movies["items"][0].status, Status.PLANNING.value)

    def test_home_view_with_sort(self):
        """Test the home view with sorting parameter."""
        response = self.client.get(reverse("home") + "?sort=completion")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "completion")

        self.user.refresh_from_db()
        self.assertEqual(self.user.home_sort, "completion")

    @patch("app.providers.services.get_media_metadata")
    def test_home_view_htmx_load_more(self, mock_get_media_metadata):
        """Test the HTMX load more functionality."""
        mock_get_media_metadata.return_value = {
            "title": "Test TV Show",
            "image": "http://example.com/image.jpg",
            "season/1": {
                "episodes": [{"id": 1}, {"id": 2}, {"id": 3}],  # 3 episodes
            },
            "related": {
                "seasons": [
                    {"season_number": 1, "image": "http://example.com/image.jpg"},
                ],  # Only one season
            },
        }

        for i in range(6, 20):  # Create 14 more TV shows (we already have 1)
            season_item = Item.objects.create(
                media_id=str(i),
                source=Sources.TMDB.value,
                media_type=MediaTypes.SEASON.value,
                title=f"Test TV Show {i}",
                image="http://example.com/image.jpg",
                season_number=1,
            )
            season = Season.objects.create(
                item=season_item,
                user=self.user,
                status=Status.IN_PROGRESS.value,
            )

            episode_item = Item.objects.create(
                media_id=str(i),
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title=f"Test TV Show {i}",
                image="http://example.com/image.jpg",
                season_number=1,
                episode_number=1,
            )
            Episode.objects.create(
                item=episode_item,
                related_season=season,
                end_date=timezone.now(),
            )

        # Now test the load more functionality
        response = self.client.get(
            reverse("home") + "?load_media_type=season", headers={"hx-request": "true"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/home_grid.html")

        self.assertIn("media_list", response.context)
        self.assertEqual(response.context["home_status"], Status.IN_PROGRESS.value)

        self.assertIn("items", response.context["media_list"])
        self.assertIn("total", response.context["media_list"])

        # Since we're loading more (items after the first 14),
        # we should have at least 1 item in the response
        self.assertEqual(len(response.context["media_list"]["items"]), 1)
        self.assertEqual(
            response.context["media_list"]["total"],
            15,
        )  # 15 TV shows total

    def test_home_view_htmx_load_more_for_planning(self):
        """Test the HTMX load more functionality for planning media."""
        for i in range(1, 16):
            movie_item = Item.objects.create(
                media_id=f"planning-{i}",
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title=f"Planned Movie {i}",
                image="http://example.com/image.jpg",
            )
            Movie.objects.create(
                item=movie_item,
                user=self.user,
                status=Status.PLANNING.value,
            )

        response = self.client.get(
            reverse("home")
            + (
                f"?load_status={Status.PLANNING.value}"
                f"&load_media_type={MediaTypes.MOVIE.value}"
            ),
            headers={"hx-request": "true"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/home_grid.html")
        self.assertEqual(response.context["home_status"], Status.PLANNING.value)
        self.assertIn("media_list", response.context)
        self.assertEqual(len(response.context["media_list"]["items"]), 2)
        self.assertEqual(response.context["media_list"]["total"], 16)
