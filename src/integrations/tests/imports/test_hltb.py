from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from app.models import (
    Game,
)
from integrations.imports import (
    hltb,
)

mock_path = Path(__file__).resolve().parent.parent / "mock_data"
app_mock_path = (
    Path(__file__).resolve().parent.parent.parent.parent / "app" / "tests" / "mock_data"
)


class ImportHowLongToBeat(TestCase):
    """Test importing media from HowLongToBeat CSV."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        game = {
            "media_id": "119133",
            "title": "Resident Evil 7: Biohazard",
            "image": "https://example.com/game.jpg",
        }
        with (
            patch.object(
                hltb.HowLongToBeatImporter,
                "_search_game",
                return_value=game,
            ),
            Path(mock_path / "import_hltb_game.csv").open("rb") as file,
        ):
            self.import_results = hltb.importer(file, self.user, "new")

    def test_import_counts(self):
        """Test basic counts of imported games."""
        self.assertEqual(Game.objects.filter(user=self.user).count(), 1)

    def test_historical_records(self):
        """Test historical records creation during import."""
        game = Game.objects.filter(user=self.user).first()
        self.assertEqual(game.history.count(), 1)
        self.assertEqual(
            game.history.first().history_date,
            datetime(2024, 2, 9, 15, 54, 48, tzinfo=UTC),
        )

    def test_partial_completion_date(self):
        """Test importing a game with an unknown completion month and day."""
        game = Game.objects.get(user=self.user)

        self.assertEqual(
            game.end_date,
            datetime(2012, 1, 1, tzinfo=timezone.get_current_timezone()),
        )

    def test_parse_hltb_date(self):
        """Test parsing complete, partial, empty, and invalid HLTB dates."""
        importer_instance = hltb.HowLongToBeatImporter(None, self.user, "new")
        current_timezone = timezone.get_current_timezone()
        test_cases = (
            ("2012-05-17", datetime(2012, 5, 17, tzinfo=current_timezone)),
            ("2012-00-00", datetime(2012, 1, 1, tzinfo=current_timezone)),
            ("2012-05-00", datetime(2012, 5, 1, tzinfo=current_timezone)),
            ("", None),
            ("0000-00-00", None),
        )

        for date_str, expected in test_cases:
            with self.subTest(date_str=date_str):
                self.assertEqual(
                    importer_instance._parse_hltb_date(date_str),
                    expected,
                )

        with self.assertRaises(ValueError):
            importer_instance._parse_hltb_date("2012-02-30")
