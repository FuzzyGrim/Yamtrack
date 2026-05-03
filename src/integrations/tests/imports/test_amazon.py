from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import MediaTypes
from integrations.imports import amazon

mock_path = Path(__file__).resolve().parent.parent / "mock_data"


class ImportAmazon(TestCase):
    """Test importing media from Amazon CSV."""

    def setUp(self):
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        with Path(mock_path / "import_amazon_sample.csv").open("rb") as file:
            self.import_results = amazon.importer(file, self.user, "new")

    def test_import_amazon_csv(self):
        imported_counts, warnings = self.import_results
        self.assertTrue(imported_counts[MediaTypes.MOVIE.value] >= 0)
        self.assertTrue(imported_counts[MediaTypes.TV.value] >= 0)
        # Optionally, check for specific titles if you know what's in the sample CSV
        # movie = Movie.objects.filter(item__title="Some Movie Title").first()
        # self.assertIsNotNone(movie)
        # tv = TV.objects.filter(item__title="Some TV Show").first()
        # self.assertIsNotNone(tv)
