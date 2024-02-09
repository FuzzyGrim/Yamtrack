import datetime
import json
import shutil
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from users.models import User

from app.models import TV, Anime, Episode, Movie, Season
from app.utils import metadata

mock_path = Path(__file__).resolve().parent / "mock_data"


class CreateMedia(TestCase):
    """Test the creation of media objects through views."""

    def setUp(self: "CreateMedia") -> None:
        """Create a user and log in."""

        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)
        self.client.login(**self.credentials)
        Path("create_media").mkdir(exist_ok=True)

    @override_settings(MEDIA_ROOT=("create_media"))
    def test_create_anime(self: "CreateMedia") -> None:
        """Test the creation of a TV object."""

        self.client.post(
            reverse(
                "media_details",
                kwargs={"media_type": "anime", "media_id": 1, "title": "Cowboy Bebop"},
            ),
            {
                "media_id": 1,
                "media_type": "tv",
                "status": "Planning",
                "save": "",
            },
        )
        self.assertEqual(
            TV.objects.filter(media_id=1, user=self.user).exists(),
            True,
        )

    @override_settings(MEDIA_ROOT=("create_media"))
    def test_create_tv(self: "CreateMedia") -> None:
        """Test the creation of a TV object through views."""

        self.client.post(
            reverse(
                "media_details",
                kwargs={"media_type": "tv", "media_id": 5895, "title": "FLCL"},
            ),
            {
                "media_id": 5895,
                "media_type": "tv",
                "status": "Planning",
                "save": "",
            },
        )
        self.assertEqual(
            TV.objects.filter(media_id=5895, user=self.user).exists(),
            True,
        )

    @override_settings(MEDIA_ROOT=("create_media"))
    def test_create_season(self: "CreateMedia") -> None:
        """Test the creation of a Season through views."""

        self.client.post(
            reverse(
                "season_details",
                kwargs={"media_id": 1668, "title": "Friends", "season_number": 1},
            ),
            {
                "media_id": 1668,
                "media_type": "season",
                "season_number": 1,
                "status": "Planning",
                "save": "",
            },
        )
        self.assertEqual(
            Season.objects.filter(media_id=1668, user=self.user).exists(),
            True,
        )

    def test_create_episodes(self: "CreateMedia") -> None:
        """Test the creation of Episode through views."""

        self.client.post(
            reverse(
                "season_details",
                kwargs={"media_id": 1668, "title": "friends", "season_number": 1},
            ),
            {
                "media_id": 1668,
                "season_number": 1,
                "episode_number": 1,
            },
        )
        self.assertEqual(
            Episode.objects.filter(
                related_season__media_id=1668,
                related_season__user=self.user,
                episode_number=1,
            ).exists(),
            True,
        )

    def tearDown(self: "CreateMedia") -> None:
        """Remove the testing directory."""
        shutil.rmtree("create_media")


class EditMedia(TestCase):
    """Test the editing of media objects through views."""

    def setUp(self: "EditMedia") -> None:
        """Create a user and log in."""

        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_edit_movie_score(self: "EditMedia") -> None:
        """Test the editing of a movie score."""
        Movie.objects.create(
            media_id=10494,
            title="Perfect Blue",
            score=9,
            progress=1,
            status="Completed",
            user=self.user,
            notes="Nice",
            start_date=datetime.date(2023, 6, 1),
            end_date=datetime.date(2023, 6, 1),
        )

        self.client.post(
            reverse("medialist", kwargs={"media_type": "movie"}),
            {
                "media_id": 10494,
                "media_type": "movie",
                "score": 10,
                "progress": 1,
                "status": "Completed",
                "notes": "Nice",
                "save": "",
            },
        )
        self.assertEqual(Movie.objects.get(media_id=10494).score, 10)


class DeleteMedia(TestCase):
    """Test the deletion of media objects through views."""

    def setUp(self: "DeleteMedia") -> None:
        """Create a user and log in."""

        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_delete_movie(self: "DeleteMedia") -> None:
        """Test the deletion of a movie through views."""

        Movie.objects.create(
            media_id=10494,
            title="Perfect Blue",
            status="Completed",
            user=self.user,
        )

        self.assertEqual(Movie.objects.filter(user=self.user).count(), 1)

        self.client.post(
            reverse("medialist", kwargs={"media_type": "movie"}),
            {
                "media_id": 10494,
                "media_type": "movie",
                "delete": "",
            },
        )

        self.assertEqual(Movie.objects.filter(user=self.user).count(), 0)

    def test_delete_season(self: "DeleteMedia") -> None:
        """Test the deletion of a season through views."""

        related_tv = TV.objects.create(
            media_id=1668,
            title="Friends",
            status="In progress",
            user=self.user,
        )
        season = Season.objects.create(
            media_id=1668,
            title="Friends",
            status="In progress",
            season_number=1,
            user=self.user,
            related_tv=related_tv,
        )
        Episode.objects.create(
            related_season=season,
            episode_number=1,
            watch_date=datetime.date(2023, 6, 1),
        )

        self.client.post(
            reverse("medialist", kwargs={"media_type": "tv"}),
            {
                "media_id": 1668,
                "media_type": "season",
                "season_number": 1,
                "delete": "",
            },
        )

        self.assertEqual(Season.objects.filter(user=self.user).count(), 0)
        self.assertEqual(
            Episode.objects.filter(related_season__user=self.user).count(),
            0,
        )

    def test_unwatch_episode(self: "DeleteMedia") -> None:
        """Test unwatching of an episode through views."""

        related_tv = TV.objects.create(
            media_id=1668,
            title="Friends",
            status="In progress",
            user=self.user,
        )
        season = Season.objects.create(
            media_id=1668,
            title="Friends",
            status="In progress",
            season_number=1,
            user=self.user,
            related_tv=related_tv,
        )
        Episode.objects.create(
            related_season=season,
            episode_number=1,
            watch_date=datetime.date(2023, 6, 1),
        )

        self.client.post(
            reverse(
                "season_details",
                kwargs={"media_id": 1668, "title": "friends", "season_number": 1},
            ),
            {
                "media_id": 1668,
                "season_number": 1,
                "episode_number": 1,
                "unwatch": "",
            },
        )

        self.assertEqual(
            Episode.objects.filter(related_season__user=self.user).count(),
            0,
        )


class DetailsMedia(TestCase):
    """Test the external API calls for media details."""

    def test_anime(self: "DetailsMedia") -> None:
        """Test the metadata method for anime."""
        response = metadata.anime_manga("anime", "1")
        self.assertEqual(response["title"], "Cowboy Bebop")
        self.assertEqual(response["start_date"], "1998-04-03")
        self.assertEqual(response["status"], "Finished")
        self.assertEqual(response["num_episodes"], 26)

    @patch("requests.get")
    def test_anime_unknown(self: "DetailsMedia", mock_data: "patch") -> None:
        """Test the metadata method for anime with mostly unknown data."""
        with open(mock_path / "metadata_anime_unknown.json") as file:
            anime_response = json.load(file)
        mock_data.return_value.json.return_value = anime_response
        mock_data.return_value.status_code = 200

        # anime without picture, synopsis, duration and genres
        response = metadata.anime_manga("anime", "0")
        self.assertEqual(response["title"], "Unknown Example")
        self.assertEqual(response["image"], settings.IMG_NONE)
        self.assertEqual(response["synopsis"], "No synopsis available.")
        self.assertEqual(response["runtime"], "Unknown")
        self.assertEqual(response["genres"], [{"name": "Unknown"}])

    def test_manga(self: "DetailsMedia") -> None:
        """Test the metadata method for manga."""

        response = metadata.anime_manga("manga", "1")
        self.assertEqual(response["title"], "Monster")
        self.assertEqual(response["start_date"], "1994-12-05")
        self.assertEqual(response["status"], "Finished")
        self.assertEqual(response["num_chapters"], 162)

    def test_tv(self: "DetailsMedia") -> None:
        """Test the metadata method for TV shows."""
        response = metadata.tv("1396")
        self.assertEqual(response["title"], "Breaking Bad")
        self.assertEqual(response["start_date"], "2008-01-20")
        self.assertEqual(response["status"], "Ended")
        self.assertEqual(response["num_episodes"], 62)

    def test_movie(self: "DetailsMedia") -> None:
        """Test the metadata method for movies."""
        response = metadata.movie("10494")
        self.assertEqual(response["title"], "Perfect Blue")
        self.assertEqual(response["start_date"], "1998-02-28")
        self.assertEqual(response["status"], "Released")
        self.assertEqual(response["num_episodes"], 1)

    @patch("requests.get")
    def test_movie_unknown(self: "DetailsMedia", mock_data: "patch") -> None:
        """Test the metadata method for movies with mostly unknown data."""
        with open(mock_path / "metadata_movie_unknown.json") as file:
            movie_response = json.load(file)
        mock_data.return_value.json.return_value = movie_response
        mock_data.return_value.status_code = 200

        response = metadata.movie("0")
        self.assertEqual(response["title"], "Unknown Movie")
        self.assertEqual(response["image"], settings.IMG_NONE)
        self.assertEqual(response["start_date"], "Unknown")
        self.assertEqual(response["synopsis"], "No synopsis available.")
        self.assertEqual(response["runtime"], "Unknown")
        self.assertEqual(response["genres"], [{"name": "Unknown"}])


class ProgressEditSeason(TestCase):
    """Test for editing a season progress through views."""

    def setUp(self: "ProgressEditSeason") -> None:
        """Prepare the database with a season and an episode."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        tv = TV.objects.create(
            media_id=1668,
            title="Friends",
            status="In progress",
            user=self.user,
            notes="",
        )

        Season.objects.create(
            media_id=1668,
            title="Friends",
            status="In progress",
            season_number=1,
            user=self.user,
            related_tv=tv,
        )

        Episode.objects.create(
            related_season=Season.objects.get(media_id=1668),
            episode_number=1,
            watch_date=datetime.date(2023, 6, 1),
        )

    def test_progress_increase(self: "ProgressEditSeason") -> None:
        """Test the increase of progress for a season."""
        self.client.post(
            reverse("progress_edit"),
            {
                "media_id": 1668,
                "media_type": "season",
                "operation": "increase",
                "season_number": 1,
            },
        )

        self.assertEqual(
            Episode.objects.filter(related_season__media_id=1668).count(),
            2,
        )

        # episode with media_id 1668 and episode_number 2 should exist
        self.assertTrue(
            Episode.objects.filter(
                related_season__media_id=1668,
                episode_number=2,
            ).exists(),
        )

    def test_progress_decrease(self: "ProgressEditSeason") -> None:
        """Test the decrease of progress for a season."""

        self.client.post(
            reverse("progress_edit"),
            {
                "media_id": 1668,
                "media_type": "season",
                "operation": "decrease",
                "season_number": 1,
            },
        )

        self.assertEqual(
            Episode.objects.filter(related_season__media_id=1668).count(),
            0,
        )


class ProgressEditAnime(TestCase):
    """Test for editing an anime progress through views."""

    def setUp(self: "ProgressEditAnime") -> None:
        """Prepare the database with an anime."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        Anime.objects.create(
            media_id=1,
            title="Cowboy Bebop",
            status="In progress",
            progress=2,
            user=self.user,
        )

    def test_progress_increase(self: "ProgressEditAnime") -> None:
        """Test the increase of progress for an anime."""
        self.client.post(
            reverse("progress_edit"),
            {
                "media_id": 1,
                "media_type": "anime",
                "operation": "increase",
            },
        )

        self.assertEqual(Anime.objects.get(media_id=1).progress, 3)

    def test_progress_decrease(self: "ProgressEditAnime") -> None:
        """Test the decrease of progress for an anime."""
        self.client.post(
            reverse("progress_edit"),
            {
                "media_id": 1,
                "media_type": "anime",
                "operation": "decrease",
            },
        )

        self.assertEqual(Anime.objects.get(media_id=1).progress, 1)
