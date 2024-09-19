import csv
from datetime import date
from io import StringIO

from app.models import TV, Anime, Episode, Game, Item, Manga, Movie, Season
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class ExportCSVTest(TestCase):
    """Test exporting media to CSV."""

    def setUp(self):
        """Create necessary data for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_superuser(**self.credentials)
        self.client.login(**self.credentials)

        item_tv = Item.objects.create(
            media_id=1668,
            source="tmdb",
            media_type="tv",
            title="Friends",
            image="https://image.url",
        )

        # Create test data for each model
        tv = TV.objects.create(
            item=item_tv,
            user=self.user,
            score=9,
            status="In progress",
            notes="Nice",
        )

        item_movie = Item.objects.create(
            media_id=10494,
            source="tmdb",
            media_type="movie",
            title="Perfect Blue",
            image="https://image.url",
        )
        Movie.objects.create(
            item=item_movie,
            user=self.user,
            score=9,
            status="Completed",
            notes="Nice",
            start_date=date(2023, 6, 1),
            end_date=date(2023, 6, 1),
        )

        item_season = Item.objects.create(
            media_id=1668,
            source="tmdb",
            media_type="season",
            title="Friends",
            image="https://image.url",
            season_number=1,
        )

        season = Season.objects.create(
            item=item_season,
            related_tv=tv,
            user=self.user,
            score=9,
            status="In progress",
            notes="Nice",
        )

        item_episode = Item.objects.create(
            media_id=1668,
            source="tmdb",
            media_type="episode",
            title="Friends",
            image="https://image.url",
            season_number=1,
            episode_number=1,
        )
        Episode.objects.create(
            item=item_episode,
            related_season=season,
            watch_date=date(2023, 6, 1),
        )

        item_anime = Item.objects.create(
            media_id=1,
            source="mal",
            media_type="anime",
            title="Cowboy Bebop",
            image="https://image.url",
        )
        Anime.objects.create(
            item=item_anime,
            user=self.user,
            status="In progress",
            progress=2,
            start_date=date(2021, 6, 1),
        )

        item_manga = Item.objects.create(
            media_id=1,
            source="mal",
            media_type="manga",
            title="Berserk",
            image="https://image.url",
        )
        Manga.objects.create(
            item=item_manga,
            user=self.user,
            status="In progress",
            progress=2,
            start_date=date(2021, 6, 1),
        )

        item_game = Item.objects.create(
            media_id=1,
            source="igdb",
            media_type="game",
            title="The Witcher 3: Wild Hunt",
            image="https://image.url",
        )
        Game.objects.create(
            item=item_game,
            user=self.user,
            status="In progress",
            progress=120,
            start_date=date(2021, 6, 1),
        )

    def test_export_csv(self):
        """Basic test exporting media to CSV."""
        # Generate the CSV file by accessing the export view
        response = self.client.get(reverse("export_csv"))

        # Assert that the response is successful (status code 200)
        self.assertEqual(response.status_code, 200)

        # Assert that the response content type is text/csv
        self.assertEqual(response["Content-Type"], "text/csv")

        # Read the CSV content from the response
        csv_content = response.content.decode("utf-8")

        # Create a CSV reader from the CSV content
        reader = csv.DictReader(StringIO(csv_content))

        # Get all media IDs from the database
        db_media_ids = set(
            TV.objects.values_list("item__media_id", flat=True).filter(user=self.user),
        )
        db_media_ids.update(
            Movie.objects.values_list("item__media_id", flat=True).filter(
                user=self.user,
            ),
        )
        db_media_ids.update(
            Season.objects.values_list("item__media_id", flat=True).filter(
                user=self.user,
            ),
        )
        db_media_ids.update(
            Episode.objects.values_list("item__media_id", flat=True).filter(
                related_season__user=self.user,
            ),
        )
        db_media_ids.update(
            Anime.objects.values_list("item__media_id", flat=True).filter(
                user=self.user,
            ),
        )
        db_media_ids.update(
            Manga.objects.values_list("item__media_id", flat=True).filter(
                user=self.user,
            ),
        )
        db_media_ids.update(
            Game.objects.values_list("item__media_id", flat=True).filter(
                user=self.user,
            ),
        )

        for row in reader:
            media_id = int(row["media_id"])
            self.assertIn(media_id, db_media_ids)
