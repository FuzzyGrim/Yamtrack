from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import (
    TV,
    Episode,
    Movie,
    Season,
    Status,
)
from integrations.imports import (
    watcharr,
)

mock_path = Path(__file__).resolve().parent.parent / "mock_data"
app_mock_path = (
    Path(__file__).resolve().parent.parent.parent.parent / "app" / "tests" / "mock_data"
)


class ImportWatcharr(TestCase):
    """Test importing media from Watcharr JSON."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        with Path(mock_path / "import_watcharr.json").open("rb") as file:
            self.import_results = watcharr.importer(file, self.user, "new")

    def test_import_counts(self):
        """Test basic counts of imported media."""
        self.assertEqual(TV.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Movie.objects.filter(user=self.user).count(), 2)
        self.assertEqual(Season.objects.filter(user=self.user).count(), 3)
        self.assertEqual(
            Episode.objects.filter(related_season__user=self.user).count(),
            34,
        )

    def test_import_records(self):
        """Test basic records of imported media."""
        jojo = Movie.objects.get(item__title="Jojo Rabbit", user=self.user)
        self.assertEqual(jojo.status, Status.COMPLETED.value)
        self.assertEqual(jojo.score, 10)

        avatar = Movie.objects.get(
            item__title="Avatar: The Way of Water", user=self.user
        )
        self.assertEqual(avatar.status, Status.DROPPED.value)
        self.assertEqual(avatar.score, 3)

        ted = TV.objects.get(item__title="Ted Lasso", user=self.user)
        self.assertEqual(ted.status, Status.COMPLETED.value)
        self.assertEqual(ted.score, 10)
