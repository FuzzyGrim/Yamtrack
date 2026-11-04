from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from app.models import (
    TV,
    Item,
    MediaTypes,
    Movie,
    Sources,
    Status,
)
from integrations.imports import (
    imdb,
)

mock_path = Path(__file__).resolve().parent.parent / "mock_data"
app_mock_path = (
    Path(__file__).resolve().parent.parent.parent.parent / "app" / "tests" / "mock_data"
)


class ImportIMDB(TestCase):
    """Test importing media from IMDB CSV."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        with Path(mock_path / "import_imdb.csv").open("rb") as file:
            self.import_results = imdb.importer(file, self.user, "new")

    def test_import_imdb_csv(self):
        """Test importing movies and TV shows from IMDB CSV."""
        imported_counts, warnings = self.import_results

        self.assertEqual(imported_counts[MediaTypes.MOVIE.value], 5)
        self.assertEqual(imported_counts[MediaTypes.TV.value], 2)

        self.assertIn(
            "The Last of Us: Unsupported title type 'Video Game' - skipped",
            warnings,
        )

        movie_1 = Movie.objects.get(item__title="The Shawshank Redemption")
        self.assertEqual(movie_1.score, 9)
        self.assertEqual(movie_1.status, Status.COMPLETED.value)
        self.assertEqual(movie_1.progress, 1)
        self.assertEqual(
            movie_1.end_date,
            datetime(2025, 2, 3, tzinfo=timezone.get_current_timezone()),
        )

        game_of_thrones = TV.objects.get(item__title="Game of Thrones")
        self.assertEqual(game_of_thrones.status, Status.PLANNING.value)

    def test_extract_imdb_id(self):
        """Test IMDB ID extraction and formatting."""
        importer_instance = imdb.IMDBImporter(None, self.user, "new")

        self.assertEqual(
            importer_instance._extract_imdb_id({"Const": "tt0111161"}),
            "tt0111161",
        )
        self.assertEqual(
            importer_instance._extract_imdb_id({"Const": "0111161"}),
            "tt0111161",
        )
        self.assertIsNone(importer_instance._extract_imdb_id({"Const": ""}))
        self.assertIsNone(importer_instance._extract_imdb_id({"Const": "invalid"}))

    def test_parse_rating(self):
        """Test rating parsing."""
        importer_instance = imdb.IMDBImporter(None, self.user, "new")

        # Valid ratings
        self.assertEqual(importer_instance._parse_rating("8.5"), 8.5)
        self.assertEqual(importer_instance._parse_rating("10"), 10.0)
        self.assertEqual(importer_instance._parse_rating("1"), 1.0)

        self.assertIsNone(importer_instance._parse_rating(""))
        self.assertIsNone(importer_instance._parse_rating("invalid"))
        self.assertIsNone(importer_instance._parse_rating("11"))
        self.assertIsNone(importer_instance._parse_rating("0"))

    def test_parse_date_rated(self):
        """Test date parsing."""
        importer_instance = imdb.IMDBImporter(None, self.user, "new")

        # Valid date
        parsed_date = importer_instance._parse_date("2023-01-15")
        self.assertEqual(parsed_date.date(), datetime(2023, 1, 15, tzinfo=UTC).date())

        self.assertIsNone(importer_instance._parse_date(""))
        self.assertIsNone(importer_instance._parse_date("invalid-date"))

    def test_is_supported_type(self):
        """Test title type support checking."""
        importer_instance = imdb.IMDBImporter(None, self.user, "new")
        type_tests = {
            ("Movie", True),
            ("TV Series", True),
            ("Short", True),
            ("TV Mini Series", True),
            ("TV Movie", True),
            ("TV Special", True),
            ("Video", True),
            ("TV Episode", False),
            ("TV Short", False),
            ("Video Game", False),
            ("Music Video", False),
            ("Podcast Series", False),
            ("Podcast Episode", False),
        }

        for media_type, result in type_tests:
            self.assertEqual(importer_instance._is_supported_type(media_type), result)

    @patch("app.providers.tmdb.find")
    def test_lookup_in_tmdb_not_found(self, mock_tmdb_find):
        """Test TMDB lookup when no results are found."""
        mock_tmdb_find.return_value = {}

        importer_instance = imdb.IMDBImporter(None, self.user, "new")
        result = importer_instance._lookup_in_tmdb("tt9999999", "movie")

        self.assertIsNone(result)

    def test_duplicate_handling(self):
        """Test handling of duplicate IMDB entries that map to same TMDB ID."""
        imported_counts, warnings = self.import_results

        # There are six movies in the test CSV, one of them is a duplicate
        # The test CSV file contains a duplicate of The Dark Knight
        self.assertEqual(imported_counts.get(MediaTypes.MOVIE.value, 0), 5)

        self.assertIn("They were matched to the same TMDB ID 155", warnings)


class ImportIMDBNewMode(TestCase):
    """Test that "new" mode only imports what is missing from Yamtrack."""

    HEADER = "Const,Title,Title Type,Created,Modified,Your Rating,Date Rated\n"

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

    def create_media(self, model, media_type, **kwargs):
        """Create existing media without triggering save side effects."""
        item = Item.objects.create(
            media_id="1",
            source=Sources.TMDB.value,
            media_type=media_type,
            title="Title",
            image="https://example.com/image.jpg",
        )
        model.objects.bulk_create([model(item=item, user=self.user, **kwargs)])

    def run_import(self, title_type, rating):
        """Run an IMDB import in "new" mode with one row."""
        csv = self.HEADER + (
            f"tt0000001,Title,{title_type},2025-01-01,2025-01-02,{rating},2025-02-03\n"
        )
        result = {
            "id": 1,
            "title": "Title",
            "name": "Title",
            "poster_path": "/poster.jpg",
        }
        with patch(
            "app.providers.tmdb.find",
            return_value={"movie_results": [result], "tv_results": [result]},
        ):
            return imdb.importer(BytesIO(csv.encode()), self.user, "new")

    def test_rated_planned_movie_is_completed(self):
        """A planned movie rated on IMDB gets the score and is completed."""
        self.create_media(Movie, MediaTypes.MOVIE.value, status=Status.PLANNING.value)

        imported_counts, _ = self.run_import("Movie", 8)

        movie = Movie.objects.get(user=self.user)
        self.assertEqual(imported_counts, {})
        self.assertEqual(movie.score, 8)
        self.assertEqual(movie.status, Status.COMPLETED.value)
        self.assertEqual(movie.progress, 1)
        self.assertEqual(
            movie.end_date,
            datetime(2025, 2, 3, tzinfo=timezone.get_current_timezone()),
        )

    def test_existing_score_is_kept(self):
        """The score tracked in Yamtrack is not overwritten."""
        self.create_media(
            Movie,
            MediaTypes.MOVIE.value,
            status=Status.COMPLETED.value,
            progress=1,
            score=6,
        )

        self.run_import("Movie", 8)

        movie = Movie.objects.get(user=self.user)
        self.assertEqual(movie.score, 6)
        self.assertEqual(Movie.objects.filter(user=self.user).count(), 1)

    def test_rated_show_only_gets_score(self):
        """A rated show gets the score but its status is left untouched."""
        self.create_media(TV, MediaTypes.TV.value, status=Status.IN_PROGRESS.value)

        self.run_import("TV Series", 9)

        tv = TV.objects.get(user=self.user)
        self.assertEqual(tv.score, 9)
        self.assertEqual(tv.status, Status.IN_PROGRESS.value)
