from csv import DictReader, DictWriter
from datetime import UTC, datetime
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import (
    Game,
    Item,
    MediaTypes,
    Sources,
    Status,
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
        with Path(mock_path / "import_hltb_game.csv").open("rb") as file:
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


class ImportHowLongToBeatMultilineNotes(TestCase):
    """Test importing HowLongToBeat rows whose quoted fields span several lines."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

    @patch("integrations.imports.hltb.HowLongToBeatImporter._search_game")
    def test_multiline_notes_are_preserved(self, mock_search_game):
        """Test that quoted multiline notes keep embedded newlines."""
        mock_search_game.return_value = {
            "media_id": "19562",
            "title": "Resident Evil 7: Biohazard",
            "image": "https://example.com/image.jpg",
        }

        with Path(mock_path / "import_hltb_game.csv").open(newline="") as file:
            reader = DictReader(file)
            row = next(reader)
        row["General Notes"] = "line one\r\nline two"

        buffer = StringIO(newline="")
        writer = DictWriter(buffer, fieldnames=reader.fieldnames)
        writer.writeheader()
        writer.writerow(row)

        hltb.importer(BytesIO(buffer.getvalue().encode()), self.user, "new")

        game = Game.objects.get(user=self.user)
        self.assertIn("line one\r\nline two", game.notes)


class ImportHowLongToBeatNewMode(TestCase):
    """Test that "new" mode only imports what is missing from Yamtrack."""

    def setUp(self):
        """Create user and an existing game for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        item = Item.objects.create(
            media_id="19562",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Resident Evil 7: Biohazard",
            image="https://example.com/image.jpg",
        )
        Game.objects.bulk_create(
            [
                Game(
                    item=item,
                    user=self.user,
                    status=Status.IN_PROGRESS.value,
                    progress=60,
                ),
            ],
        )

    @patch("integrations.imports.hltb.HowLongToBeatImporter._search_game")
    def run_import(self, mock_search_game, **fields):
        """Run a HowLongToBeat import in "new" mode of the existing game."""
        mock_search_game.return_value = {
            "media_id": "19562",
            "title": "Resident Evil 7: Biohazard",
            "image": "https://example.com/image.jpg",
        }

        with Path(mock_path / "import_hltb_game.csv").open(newline="") as file:
            reader = DictReader(file)
            row = next(reader)
        row.update(fields)

        buffer = StringIO(newline="")
        writer = DictWriter(buffer, fieldnames=reader.fieldnames)
        writer.writeheader()
        writer.writerow(row)

        return hltb.importer(BytesIO(buffer.getvalue().encode()), self.user, "new")

    def test_existing_game_is_synced(self):
        """An existing game gets its playtime, status, score and dates."""
        imported_counts, _ = self.run_import()

        game = Game.objects.get(user=self.user)
        self.assertEqual(imported_counts, {})
        self.assertEqual(game.status, Status.COMPLETED.value)
        self.assertEqual(game.progress, 180)
        self.assertEqual(game.score, 7)
        self.assertIsNotNone(game.start_date)
        self.assertIsNotNone(game.end_date)

    def test_unrated_game_has_no_score(self):
        """A review of 0 means the game wasn't rated."""
        self.run_import(Review="0")

        self.assertIsNone(Game.objects.get(user=self.user).score)
