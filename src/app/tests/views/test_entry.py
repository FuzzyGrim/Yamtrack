
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import (
    TV,
    Episode,
    Item,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Status,
)


class CreateEntryViewTests(TestCase):
    """Test the create entry view."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_create_entry_get(self):
        """Test the GET method of create_entry view."""
        response = self.client.get(reverse("create_entry"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/create_entry.html")
        self.assertIn("media_types", response.context)

        self.assertEqual(response.context["media_types"], MediaTypes.values)

    def test_create_entry_post_movie(self):
        """Test creating a movie entry."""
        form_data = {
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "status": Status.COMPLETED.value,
            "score": 8,
            "progress": 1,
            "start_date": "2023-01-01T00:00",
            "end_date": "2023-01-02T00:00",
        }

        response = self.client.post(reverse("create_entry"), form_data, follow=True)

        self.assertRedirects(response, reverse("create_entry"))

        self.assertTrue(
            Item.objects.filter(
                title="Test Movie",
                media_type=MediaTypes.MOVIE.value,
            ).exists(),
        )

        movie = Movie.objects.get(item__title="Test Movie")
        self.assertEqual(movie.status, Status.COMPLETED.value)
        self.assertEqual(movie.score, 8)
        self.assertEqual(movie.progress, 1)
        self.assertEqual(movie.user, self.user)

    def test_create_entry_post_tv(self):
        """Test creating a TV show entry."""
        form_data = {
            "title": "Test TV Show",
            "media_type": MediaTypes.TV.value,
            "status": Status.IN_PROGRESS.value,
            "score": 7,
        }

        response = self.client.post(reverse("create_entry"), form_data, follow=True)

        self.assertRedirects(response, reverse("create_entry"))

        self.assertTrue(
            Item.objects.filter(
                title="Test TV Show",
                media_type=MediaTypes.TV.value,
            ).exists(),
        )

        tv = TV.objects.get(item__title="Test TV Show")
        self.assertEqual(tv.status, Status.IN_PROGRESS.value)
        self.assertEqual(tv.score, 7)
        self.assertEqual(tv.user, self.user)

    def test_create_entry_post_season(self):
        """Test creating a season entry with parent TV."""
        tv_item = Item.objects.create(
            media_id="1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.TV.value,
            title="TV Show",
        )
        parent_tv = TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        form_data = {
            "title": "TV Show",
            "media_type": MediaTypes.SEASON.value,
            "season_number": 1,
            "parent_tv": parent_tv.id,
            "status": Status.IN_PROGRESS.value,
            "score": 7,
        }

        response = self.client.post(reverse("create_entry"), form_data, follow=True)

        self.assertRedirects(response, reverse("create_entry"))

        self.assertTrue(
            Item.objects.filter(
                title="TV Show",
                media_type=MediaTypes.SEASON.value,
                season_number=1,
            ).exists(),
        )

        season = Season.objects.get(item__title="TV Show")
        self.assertEqual(season.status, Status.IN_PROGRESS.value)
        self.assertEqual(season.score, 7)
        self.assertEqual(season.user, self.user)
        self.assertEqual(season.related_tv, parent_tv)

    def test_create_entry_post_episode(self):
        """Test creating an episode entry with parent season."""
        tv_item = Item.objects.create(
            media_id="1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.TV.value,
            title="TV Show",
        )
        parent_tv = TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        season_item = Item.objects.create(
            media_id="1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.SEASON.value,
            title="TV Show",
            season_number=1,
        )
        parent_season = Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=parent_tv,
            status=Status.IN_PROGRESS.value,
        )

        form_data = {
            "title": "TV Show",
            "media_type": MediaTypes.EPISODE.value,
            "season_number": 1,
            "episode_number": 1,
            "parent_season": parent_season.id,
            "end_date": "2023-01-02T00:00",
        }

        response = self.client.post(reverse("create_entry"), form_data, follow=True)

        self.assertRedirects(response, reverse("create_entry"))

        self.assertTrue(
            Item.objects.filter(
                title="TV Show",
                media_type=MediaTypes.EPISODE.value,
                season_number=1,
                episode_number=1,
            ).exists(),
        )

        episode = Episode.objects.get(item__title="TV Show")
        self.assertEqual(episode.related_season, parent_season)
        end_date_local = timezone.localtime(episode.end_date)
        self.assertEqual(end_date_local.strftime("%Y-%m-%d %H:%M"), "2023-01-02 00:00")

    def test_create_entry_post_duplicate_item(self):
        """Test creating a duplicate item."""
        tv_item = Item.objects.create(
            media_id="1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.TV.value,
            title="TV Show",
        )
        parent_tv = TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        season_item = Item.objects.create(
            media_id="1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.SEASON.value,
            title="TV Show",
            season_number=1,
        )
        Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=parent_tv,
            status=Status.IN_PROGRESS.value,
        )

        initial_count = Item.objects.count()

        form_data = {
            "title": "TV Show",
            "media_type": MediaTypes.SEASON.value,
            "season_number": 1,
            "parent_tv": parent_tv.id,
            "status": Status.IN_PROGRESS.value,
            "score": 7,
            "repeats": 0,
        }

        with transaction.atomic():
            self.client.post(reverse("create_entry"), form_data)

        self.assertEqual(Item.objects.count(), initial_count)


