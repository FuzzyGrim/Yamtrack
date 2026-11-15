"""Tests for Google Takeout YouTube / YouTube Music import."""

import json
import zipfile
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import Item, MediaTypes, Music, Sources, Status, Video
from integrations.imports import google_takeout
from integrations.imports.helpers import MediaImportError

mock_path = Path(__file__).resolve().parent.parent / "mock_data"
WATCH_HISTORY = mock_path / "import_google_takeout_watch_history.json"


class ImportGoogleTakeout(TestCase):
    """Test importing watch history from Google Takeout JSON."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

    def _import_json(self, mode="new"):
        """Run the importer against the sample watch-history.json."""
        with WATCH_HISTORY.open("rb") as file:
            return google_takeout.importer(file, self.user, mode)

    def test_import_json(self):
        """Import music listens and video watches from watch-history.json."""
        imported_counts, warnings = self._import_json()

        self.assertEqual(imported_counts[MediaTypes.MUSIC.value], 2)
        self.assertEqual(imported_counts[MediaTypes.VIDEO.value], 1)
        self.assertIn("Deleted Video: Missing YouTube video ID - skipped", warnings)

        bohemian = Music.objects.get(item__media_id="fJ9rUzIMcZQ")
        self.assertEqual(bohemian.item.title, "Bohemian Rhapsody")
        self.assertEqual(bohemian.item.source, Sources.MANUAL.value)
        self.assertEqual(
            bohemian.item.image,
            "https://i.ytimg.com/vi/fJ9rUzIMcZQ/hqdefault.jpg",
        )
        self.assertEqual(bohemian.status, Status.COMPLETED.value)
        self.assertEqual(bohemian.progress, 2)
        self.assertEqual(
            bohemian.start_date,
            datetime(2024, 1, 10, 12, 0, tzinfo=UTC),
        )
        self.assertEqual(
            bohemian.end_date,
            datetime(2024, 2, 15, 18, 30, tzinfo=UTC),
        )
        self.assertIn("Queen", bohemian.notes)

        rick = Music.objects.get(item__media_id="dQw4w9WgXcQ")
        self.assertEqual(rick.progress, 1)
        self.assertIn("Rick Astley", rick.notes)

        pasta = Video.objects.get(item__media_id="abcdefghijk")
        self.assertEqual(pasta.item.title, "How to Make Pasta")
        self.assertEqual(pasta.item.media_type, MediaTypes.VIDEO.value)
        self.assertEqual(
            pasta.item.image,
            "https://i.ytimg.com/vi/abcdefghijk/hqdefault.jpg",
        )
        self.assertEqual(pasta.progress, 1)
        self.assertEqual(pasta.status, Status.COMPLETED.value)

    def test_youtube_thumbnail_url(self):
        """Thumbnails use the public YouTube CDN pattern."""
        self.assertEqual(
            google_takeout.youtube_thumbnail_url("dQw4w9WgXcQ"),
            "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
        )

    def test_import_zip(self):
        """Import from a zip archive containing watch-history.json."""
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(
                "Takeout/YouTube and YouTube Music/history/watch-history.json",
                WATCH_HISTORY.read_text(encoding="utf-8"),
            )
        buffer.seek(0)
        buffer.name = "takeout.zip"

        imported_counts, _warnings = google_takeout.importer(
            buffer,
            self.user,
            "new",
        )

        self.assertEqual(imported_counts[MediaTypes.MUSIC.value], 2)
        self.assertEqual(imported_counts[MediaTypes.VIDEO.value], 1)

    def test_new_mode_skips_existing_but_updates_listens(self):
        """Existing music keeps its row and gains higher listen progress."""
        item = Item.objects.create(
            media_id="fJ9rUzIMcZQ",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MUSIC.value,
            title="Bohemian Rhapsody",
            image="http://example.com/image.jpg",
        )
        Music.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=1,
        )

        imported_counts, _warnings = self._import_json("new")

        self.assertEqual(Music.objects.filter(item=item).count(), 1)
        bohemian = Music.objects.get(item=item)
        self.assertEqual(bohemian.progress, 2)
        # Newly imported tracks still count.
        self.assertEqual(imported_counts[MediaTypes.MUSIC.value], 2)
        self.assertEqual(imported_counts[MediaTypes.VIDEO.value], 1)

    def test_extract_video_id(self):
        """Video IDs are pulled from standard and short YouTube URLs."""
        importer = google_takeout.GoogleTakeoutImporter(None, self.user, "new")

        self.assertEqual(
            importer._extract_video_id(
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            ),
            "dQw4w9WgXcQ",
        )
        self.assertEqual(
            importer._extract_video_id("https://youtu.be/dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )
        self.assertEqual(
            importer._extract_video_id(
                "https://www.youtube.com/shorts/abcdefghijk",
            ),
            "abcdefghijk",
        )
        self.assertIsNone(importer._extract_video_id(""))

    def test_invalid_json_raises(self):
        """Non-list JSON is rejected."""
        with self.assertRaises(MediaImportError):
            google_takeout.importer(
                BytesIO(json.dumps({"not": "a list"}).encode()),
                self.user,
                "new",
            )
