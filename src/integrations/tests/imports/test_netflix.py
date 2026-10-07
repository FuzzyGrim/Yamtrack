from datetime import datetime
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import (
    MediaTypes,
    Status,
)
from integrations.imports import (
    netflix,
)

mock_path = Path(__file__).resolve().parent.parent / "mock_data"
app_mock_path = (
    Path(__file__).resolve().parent.parent.parent.parent / "app" / "tests" / "mock_data"
)


class ImportNetflix(TestCase):
    """Test importing media from Netflix watch history CSV."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        with Path(mock_path / "import_netflix.csv").open("rb") as file:
            self.import_results = netflix.importer(file, self.user, "new")

    def test_import_netflix_watch_history(self):
        """Test importing movies and TV shows from Netflix watch history CSV."""
        imported_counts, warnings = self.import_results

        self.assertEqual(imported_counts[MediaTypes.MOVIE.value], 2)
        self.assertEqual(imported_counts[MediaTypes.TV.value], 3)
        self.assertEqual(imported_counts[MediaTypes.SEASON.value], 4)
        self.assertEqual(imported_counts[MediaTypes.EPISODE.value], 5)

        self.assertIn(
            "'Infinite' is ambiguous, multiple movies found. Watched 10/10/24",
            warnings,
        )
        self.assertIn(
            "': Episode 7, 8/8/22' information removed by Netflix",
            warnings,
        )
        self.assertIn(
            "No exact match found: 'Twice Upon a Time' (Doctor Who S10)",
            warnings,
        )
        self.assertIn(
            "'dummy entry to not get a result' could not identify movie. "
            "Watched on 4/4/18",
            warnings,
        )

    def test_parse_date(self):
        """Test date parsing."""
        # None input
        self.assertEqual(netflix._parse_date(None), None)
        # unparsable string input
        self.assertEqual(netflix._parse_date("50/52/50"), None)
        # parsable string input
        expected = (
            datetime(2011, 1, 1)
            .astimezone()
            .replace(hour=0, minute=0, second=0, microsecond=0)
        )
        self.assertEqual(netflix._parse_date("1/1/11"), expected)

    def test_series_completion_status(self):
        """Test series completion status."""
        # in progress tests
        self.assertEqual(
            netflix._series_completion_status(1, []), Status.IN_PROGRESS.value
        )
        self.assertEqual(
            netflix._series_completion_status(
                1, [{"number_of_episodes": 1, "watched_episodes": []}]
            ),
            Status.IN_PROGRESS.value,
        )
        # completed test
        self.assertEqual(
            netflix._series_completion_status(
                1, [{"number_of_episodes": 1, "watched_episodes": [{}]}]
            ),
            Status.COMPLETED.value,
        )
