import datetime

from django.test import TestCase, override_settings
from django.urls import reverse
from users.models import User

from app.models import TV, Anime, Episode, Item, Movie, Season


class CreateMedia(TestCase):
    """Test the creation of media objects through views."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    @override_settings(MEDIA_ROOT=("create_media"))
    def test_create_anime(self):
        """Test the creation of a TV object."""
        item = Item.objects.create(
            media_id=1,
            media_type="anime",
            title="Test Anime",
            image="http://example.com/image.jpg",
        )
        self.client.post(
            reverse("media_save"),
            {
                "item": item.id,
                "status": "Planning",
            },
        )
        self.assertEqual(
            Anime.objects.filter(item__media_id=1, user=self.user).exists(),
            True,
        )

    @override_settings(MEDIA_ROOT=("create_media"))
    def test_create_tv(self):
        """Test the creation of a TV object through views."""
        item = Item.objects.create(
            media_id=5895,
            media_type="tv",
            title="Friends",
            image="http://example.com/image.jpg",
        )
        self.client.post(
            reverse("media_save"),
            {
                "item": item.id,
                "status": "Planning",
            },
        )
        self.assertEqual(
            TV.objects.filter(item__media_id=5895, user=self.user).exists(),
            True,
        )

    def test_create_season(self):
        """Test the creation of a Season through views."""
        item = Item.objects.create(
            media_id=1668,
            media_type="season",
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
        )
        self.client.post(
            reverse("media_save"),
            {
                "item": item.id,
                "status": "Planning",
            },
        )
        self.assertEqual(
            Season.objects.filter(item__media_id=1668, user=self.user).exists(),
            True,
        )

    def test_create_episodes(self):
        """Test the creation of Episode through views."""
        item = Item.objects.create(
            media_id=1668,
            media_type="episode",
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=1,
        )
        self.client.post(
            reverse("episode_handler"),
            {
                "item": item.id,
                "date": "2023-06-01",
            },
        )
        self.assertEqual(
            Episode.objects.filter(
                item__media_id=1668,
                related_season__user=self.user,
                episode_number=1,
            ).exists(),
            True,
        )


class EditMedia(TestCase):
    """Test the editing of media objects through views."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_edit_movie_score(self):
        """Test the editing of a movie score."""
        item = Item.objects.create(
            media_id=10494,
            media_type="movie",
            title="Perfect Blue",
            image="http://example.com/image.jpg",
        )
        Movie.objects.create(
            item=item,
            user=self.user,
            score=9,
            progress=1,
            status="Completed",
            notes="Nice",
            start_date=datetime.date(2023, 6, 1),
            end_date=datetime.date(2023, 6, 1),
        )

        self.client.post(
            reverse("media_save"),
            {
                "item": item.id,
                "score": 10,
                "progress": 1,
                "status": "Completed",
                "repeats": 0,
                "notes": "Nice",
            },
        )
        self.assertEqual(Movie.objects.get(item__media_id=10494).score, 10)


class DeleteMedia(TestCase):
    """Test the deletion of media objects through views."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        self.item_tv = Item.objects.create(
            media_id=1668,
            media_type="tv",
            title="Friends",
            image="http://example.com/image.jpg",
        )
        related_tv = TV.objects.create(
            item=self.item_tv,
            user=self.user,
            status="In progress",
        )

        self.item_season = Item.objects.create(
            media_id=1668,
            media_type="season",
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
        )
        season = Season.objects.create(
            item=self.item_season,
            user=self.user,
            related_tv=related_tv,
            status="In progress",
        )

        self.item_ep = Item.objects.create(
            media_id=1668,
            media_type="episode",
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=1,
        )
        Episode.objects.create(
            item=self.item_ep,
            related_season=season,
            watch_date=datetime.date(2023, 6, 1),
        )

    def test_delete_movie(self):
        """Test the deletion of a movie through views."""
        self.assertEqual(TV.objects.filter(user=self.user).count(), 1)

        self.client.post(reverse("media_delete"), {"item": self.item_tv.id})

        self.assertEqual(Movie.objects.filter(user=self.user).count(), 0)

    def test_delete_season(self):
        """Test the deletion of a season through views."""
        self.client.post(reverse("media_delete"), {"item": self.item_season.id})

        self.assertEqual(Season.objects.filter(user=self.user).count(), 0)
        self.assertEqual(
            Episode.objects.filter(related_season__user=self.user).count(),
            0,
        )

    def test_unwatch_episode(self):
        """Test unwatching of an episode through views."""
        self.client.post(
            reverse(
                "episode_handler",
            ),
            {
                "item": self.item_ep.id,
                "unwatch": "",
            },
        )

        self.assertEqual(
            Episode.objects.filter(related_season__user=self.user).count(),
            0,
        )


class ProgressEditSeason(TestCase):
    """Test for editing a season progress through views."""

    def setUp(self):
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

    def test_progress_increase(self):
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

    def test_progress_decrease(self):
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

    def setUp(self):
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

    def test_progress_increase(self):
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

    def test_progress_decrease(self):
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
