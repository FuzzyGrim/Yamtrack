import datetime as dt
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import (
    Anime,
    Item,
    MediaTypes,
    Sources,
    Status,
)

mock_path = Path(__file__).resolve().parent.parent / "mock_data"


class MediaModel(TestCase):
    """Test the custom save of the Media model."""

    def setUp(self):
        """Create a user."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        item_anime = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Cowboy Bebop",
            image="http://example.com/image.jpg",
        )

        self.anime = Anime.objects.create(
            item=item_anime,
            user=self.user,
            status=Status.PLANNING.value,
        )

    def test_completed_progress(self):
        """When completed, the progress should be the total number of episodes."""
        self.anime.status = Status.COMPLETED.value
        self.anime.save()
        self.assertEqual(
            Anime.objects.get(item__media_id="1", user=self.user).progress,
            26,
        )

    def test_progress_is_max(self):
        """When progress is maximum number of episodes.

        Status should be completed and end_date the current date if not specified.
        """
        self.anime.status = Status.IN_PROGRESS.value
        self.anime.progress = 26
        self.anime.save()

        self.assertEqual(
            Anime.objects.get(item__media_id="1", user=self.user).status,
            Status.COMPLETED.value,
        )
        self.assertIsNotNone(
            Anime.objects.get(item__media_id="1", user=self.user).end_date,
        )

    def test_progress_is_max_keeps_given_end_date(self):
        """When progress reaches the maximum, an end_date already set is kept."""
        end_date = dt.datetime(2026, 10, 28, 21, 0, tzinfo=dt.UTC)
        self.anime.status = Status.IN_PROGRESS.value
        self.anime.progress = 26
        self.anime.end_date = end_date
        self.anime.save()

        anime = Anime.objects.get(item__media_id="1", user=self.user)
        self.assertEqual(anime.status, Status.COMPLETED.value)
        self.assertEqual(anime.end_date, end_date)

    def test_progress_bigger_than_max(self):
        """When progress is bigger than max, it should be set to max."""
        self.anime.status = Status.IN_PROGRESS.value
        self.anime.progress = 30
        self.anime.save()
        self.assertEqual(
            Anime.objects.get(item__media_id="1", user=self.user).progress,
            26,
        )
