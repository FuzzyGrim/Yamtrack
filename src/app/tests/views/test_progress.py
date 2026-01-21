import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.models import (
    Anime,
    Episode,
    Item,
    MediaTypes,
    Season,
    Sources,
    Status,
)


class ProgressEditSeason(TestCase):
    """Test for editing a season progress through views."""

    def setUp(self):
        """Prepare the database with a season and an episode."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        self.item_season = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
        )
        self.season = Season.objects.create(
            item=self.item_season,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        item_ep = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=1,
        )
        Episode.objects.create(
            item=item_ep,
            related_season=self.season,
            end_date=datetime.datetime(2023, 6, 1, 0, 0, tzinfo=datetime.UTC),
        )

    def test_progress_increase(self):
        """Test the increase of progress for a season."""
        self.client.post(
            reverse(
                "progress_edit",
                kwargs={
                    "media_type": MediaTypes.SEASON.value,
                    "instance_id": self.season.id,
                },
            ),
            {
                "operation": "increase",
            },
        )

        self.assertEqual(
            Episode.objects.filter(item__media_id="1668").count(),
            2,
        )

        self.assertTrue(
            Episode.objects.filter(
                item__media_id="1668",
                item__episode_number=2,
            ).exists(),
        )

    def test_progress_decrease(self):
        """Test the decrease of progress for a season."""
        self.client.post(
            reverse(
                "progress_edit",
                kwargs={
                    "media_type": MediaTypes.SEASON.value,
                    "instance_id": self.season.id,
                },
            ),
            {
                "operation": "decrease",
            },
        )

        self.assertEqual(
            Episode.objects.filter(item__media_id="1668").count(),
            0,
        )


class ProgressEditAnime(TestCase):
    """Test for editing an anime progress through views."""

    def setUp(self):
        """Prepare the database with an anime."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        self.item = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Cowboy Bebop",
            image="http://example.com/image.jpg",
        )
        self.anime = Anime.objects.create(
            item=self.item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=2,
        )

    def test_progress_increase(self):
        """Test the increase of progress for an anime."""
        self.client.post(
            reverse(
                "progress_edit",
                kwargs={
                    "media_type": MediaTypes.ANIME.value,
                    "instance_id": self.anime.id,
                },
            ),
            {
                "operation": "increase",
            },
        )

        self.assertEqual(Anime.objects.get(item__media_id="1").progress, 3)

    def test_progress_decrease(self):
        """Test the decrease of progress for an anime."""
        self.client.post(
            reverse(
                "progress_edit",
                kwargs={
                    "media_type": MediaTypes.ANIME.value,
                    "instance_id": self.anime.id,
                },
            ),
            {
                "operation": "decrease",
            },
        )

        self.assertEqual(Anime.objects.get(item__media_id="1").progress, 1)


