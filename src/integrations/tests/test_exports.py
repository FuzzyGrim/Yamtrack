import csv
from datetime import date
from io import StringIO

from app.models import TV, Anime, Episode, Manga, Movie, Season
from django.test import TestCase
from django.urls import reverse
from users.models import User


class ExportCSVTest(TestCase):
    """Test exporting media to CSV."""

    def setUp(self):
        """Create necessary data for the tests."""

        self.credentials = {"username": "test", "password": "12345"}
        self.user = User.objects.create_superuser(**self.credentials)
        self.client.login(**self.credentials)

        # Create test data for each model
        tv = TV.objects.create(
            media_id=1668,
            title="Friends",
            score=9,
            status="In progress",
            user=self.user,
            notes="Nice",
        )

        Movie.objects.create(
            media_id=10494,
            title="Perfect Blue",
            score=9,
            status="Completed",
            user=self.user,
            notes="Nice",
            start_date=date(2023, 6, 1),
            end_date=date(2023, 6, 1),
        )

        season = Season.objects.create(
            media_id=1668,
            title="Friends",
            score=9,
            status="In progress",
            season_number=1,
            user=self.user,
            notes="Nice",
            related_tv=tv,
        )

        Episode.objects.create(
            related_season=season,
            episode_number=1,
            watch_date=date(2023, 6, 1),
        )

        Anime.objects.create(
            media_id=1,
            title="Cowboy Bebop",
            status="In progress",
            user=self.user,
            progress=2,
            start_date=date(2021, 6, 1),
        )

        Manga.objects.create(
            media_id=1,
            title="Berserk",
            status="In progress",
            user=self.user,
            progress=2,
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
            TV.objects.values_list("media_id", flat=True).filter(user=self.user),
        )
        db_media_ids.update(
            Movie.objects.values_list("media_id", flat=True).filter(user=self.user),
        )
        db_media_ids.update(
            Season.objects.values_list("media_id", flat=True).filter(user=self.user),
        )
        db_media_ids.update(
            Episode.objects.values_list("related_season__media_id", flat=True).filter(
                related_season__user=self.user,
            ),
        )
        db_media_ids.update(
            Anime.objects.values_list("media_id", flat=True).filter(user=self.user),
        )
        db_media_ids.update(
            Manga.objects.values_list("media_id", flat=True).filter(user=self.user),
        )

        for row in reader:
            media_id = int(row["media_id"])
            self.assertIn(media_id, db_media_ids)
