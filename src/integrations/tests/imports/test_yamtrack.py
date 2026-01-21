from datetime import UTC, datetime
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import (
    TV,
    Anime,
    Book,
    Episode,
    Manga,
    Movie,
    Season,
)
from integrations.imports import (
    yamtrack,
)

mock_path = Path(__file__).resolve().parent.parent / "mock_data"
app_mock_path = (
    Path(__file__).resolve().parent.parent.parent.parent / "app" / "tests" / "mock_data"
)


class ImportYamtrack(TestCase):
    """Test importing media from Yamtrack CSV."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        with Path(mock_path / "import_yamtrack.csv").open("rb") as file:
            self.import_results = yamtrack.importer(file, self.user, "new")

    def test_import_counts(self):
        """Test basic counts of imported media."""
        self.assertEqual(Anime.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Manga.objects.filter(user=self.user).count(), 1)
        self.assertEqual(TV.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Movie.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Season.objects.filter(user=self.user).count(), 1)
        self.assertEqual(
            Episode.objects.filter(related_season__user=self.user).count(),
            24,
        )

    def test_historical_records(self):
        """Test historical records creation during import."""
        anime = Anime.objects.filter(user=self.user).first()
        self.assertEqual(anime.history.count(), 1)
        self.assertEqual(
            anime.history.first().history_date,
            datetime(2024, 2, 9, 10, 0, 0, tzinfo=UTC),
        )

        movie = Movie.objects.filter(user=self.user).first()
        self.assertEqual(movie.history.count(), 1)
        self.assertEqual(
            movie.history.first().history_date,
            datetime(2024, 2, 9, 15, 30, 0, tzinfo=UTC),
        )

        tv = TV.objects.filter(user=self.user).first()
        self.assertEqual(tv.history.count(), 1)
        self.assertEqual(
            tv.history.first().history_date,
            datetime(2024, 2, 9, 12, 0, 0, tzinfo=UTC),
        )

    def test_missing_metadata_handling(self):
        """Test _handle_missing_metadata method directly."""
        test_rows = [
            # TV Show
            {
                "media_id": "1668",
                "source": "tmdb",
                "media_type": "tv",
                "title": "",
                "image": "",
                "season_number": "",
                "episode_number": "",
            },
            {
                "media_id": "1668",
                "source": "tmdb",
                "media_type": "season",
                "title": "",
                "image": "",
                "season_number": "2",
                "episode_number": "",
            },
            # Episode
            {
                "media_id": "1668",
                "source": "tmdb",
                "media_type": "episode",
                "title": "",
                "image": "",
                "season_number": "2",
                "episode_number": "5",
            },
        ]

        importer = yamtrack.YamtrackImporter(None, self.user, "new")

        for row in test_rows:
            # Make copies of original rows to verify they're modified
            original_row = row.copy()

            # Call the method directly
            importer._handle_missing_metadata(
                row,
                row["media_type"],
                row["season_number"],
                row["episode_number"],
            )

            self.assertNotEqual(row["title"], original_row["title"])
            self.assertNotEqual(row["image"], original_row["image"])


class ImportYamtrackPartials(TestCase):
    """Test importing yamtrack media with no ID."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        with Path(mock_path / "import_yamtrack_partials.csv").open("rb") as file:
            self.import_results = yamtrack.importer(file, self.user, "new")

    def test_import_counts(self):
        """Test basic counts of imported media."""
        self.assertEqual(Book.objects.filter(user=self.user).count(), 3)
        self.assertEqual(Movie.objects.filter(user=self.user).count(), 1)

    def test_end_dates(self):
        """Test end dates during import."""
        book = Book.objects.filter(user=self.user).first()
        self.assertEqual(book.history.count(), 1)
        bookqs = Book.objects.filter(
            user=self.user,
            item__title="Warlock",
        ).order_by("-end_date")
        books = list(bookqs)

        self.assertEqual(len(books), 3)
        self.assertEqual(
            books[0].end_date,
            datetime(2024, 5, 9, 0, 0, 0, tzinfo=UTC),
        )
        self.assertEqual(
            books[1].end_date,
            datetime(2024, 4, 9, 0, 0, 0, tzinfo=UTC),
        )
        self.assertEqual(
            books[2].end_date,
            datetime(2024, 3, 9, 0, 0, 0, tzinfo=UTC),
        )


