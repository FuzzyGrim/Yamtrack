from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from django_celery_results.models import TaskResult
from rest_framework import status
from rest_framework.test import APIClient

from app.models import Book, DiaryEntry, Item, MediaTypes, Movie, Sources, Status
from integrations.imports.storygraph.importer import StoryGraphImporter
from integrations.imports.storygraph.parser import parse_export
from integrations.imports.storygraph.resolver import StoryGraphResolver

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "storygraph" / "minimal.csv"


class FakeResolver:
    IDS = {
        "Rich Book": ("101", Sources.HARDCOVER.value, 321),
        "Current Book": ("102", Sources.HARDCOVER.value, None),
        "TBR Book": ("103", Sources.HARDCOVER.value, None),
        "Paused Book": ("104", Sources.HARDCOVER.value, None),
        "DNF Book": ("105", Sources.HARDCOVER.value, None),
    }

    def resolve_rows(self, rows):
        return {
            row.index: {
                "media_id": media_id,
                "source": source,
                "title": row.title,
                "image": f"https://example.com/{media_id}.jpg",
                "max_progress": max_progress,
            }
            for row in rows
            for media_id, source, max_progress in [self.IDS[row.title]]
        }


class ImportStoryGraphTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="storygraph", password="password")

    def _import(self, mode="new"):
        with FIXTURE.open("rb") as file:
            with patch("app.models.providers.services.get_media_metadata", return_value={"max_progress": 321}):
                with patch("app.services.update_daily_statistics.delay"):
                    with patch("app.signals.update_daily_statistics.delay"):
                        return StoryGraphImporter(file.read(), self.user, mode, resolver=FakeResolver()).import_data()

    def test_parse_csv(self):
        with FIXTURE.open("rb") as file:
            rows = parse_export(file.read())

        rich = rows[0]
        self.assertEqual(rich.status, Status.COMPLETED.value)
        self.assertEqual(rich.isbn, "9780306406157")
        self.assertEqual(rich.rating, 9)
        self.assertEqual(rich.tags, ["favorite", "migration"])
        self.assertEqual([date.end.date().isoformat() for date in rich.read_dates], ["2025-01-03", "2025-02-05"])
        self.assertEqual(rows[1].isbn, "")

    def test_full_import(self):
        counts, warnings = self._import()

        self.assertIsNone(warnings)
        self.assertEqual(counts[MediaTypes.BOOK.value], 5)
        self.assertEqual(counts["diary"], 2)
        self.assertEqual(counts["ratings"], 2)
        self.assertEqual(counts["reviews"], 2)
        self.assertEqual(Book.objects.count(), 5)

        rich = Book.objects.get(item__media_id="101")
        self.assertEqual(rich.status, Status.COMPLETED.value)
        self.assertEqual(rich.score, 9)
        self.assertEqual(rich.progress, 321)
        self.assertIn("Date added: 2024/12/31", rich.notes)
        self.assertIn("Dates read: 2025/01/01-2025/01/03;2025/02/05", rich.notes)
        self.assertIn("Undated reads: 1", rich.notes)
        self.assertIn("Content Warnings: Violence", rich.notes)

        entries = list(DiaryEntry.objects.filter(item=rich.item).order_by("consumed_at"))
        self.assertEqual(len(entries), 2)
        self.assertFalse(entries[0].is_rewatch)
        self.assertTrue(entries[1].is_rewatch)
        self.assertEqual(entries[1].review, "Great review.")
        self.assertEqual(entries[1].rating, 9)
        self.assertFalse(entries[1].contains_spoilers)
        self.assertEqual(sorted(tag.name for tag in entries[1].tags.all()), ["favorite", "migration"])
        self.assertEqual(rich.completion_diary_entry, entries[1])

        self.assertEqual(Book.objects.get(item__media_id="102").status, Status.IN_PROGRESS.value)
        self.assertIn("ISBN/UID: B00F3HJ8FS", Book.objects.get(item__media_id="102").notes)
        self.assertEqual(Book.objects.get(item__media_id="103").status, Status.PLANNING.value)
        self.assertEqual(Book.objects.get(item__media_id="104").status, Status.PAUSED.value)
        dnf = Book.objects.get(item__media_id="105")
        self.assertEqual(dnf.status, Status.DROPPED.value)
        self.assertIn("Review: Not for me.", dnf.notes)
        self.assertFalse(DiaryEntry.objects.filter(item=dnf.item).exists())

    def test_overwrite_deletes_book_state_only(self):
        old_item = Item.objects.create(
            media_id="old",
            source=Sources.HARDCOVER.value,
            media_type=MediaTypes.BOOK.value,
            title="Old Book",
            image="https://example.com/old.jpg",
        )
        Book.objects.create(user=self.user, item=old_item, status=Status.PLANNING.value)
        movie_item = Item.objects.create(
            media_id="movie",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Movie",
            image="https://example.com/movie.jpg",
        )
        Movie.objects.create(user=self.user, item=movie_item, status=Status.PLANNING.value)
        with patch("app.signals.update_daily_statistics.delay"):
            old_entry = DiaryEntry.objects.create(user=self.user, item=old_item, consumed_at=timezone.now())
            movie_entry = DiaryEntry.objects.create(user=self.user, item=movie_item, consumed_at=timezone.now())

        self._import(mode="overwrite")

        self.assertFalse(Book.objects.filter(item=old_item).exists())
        self.assertFalse(DiaryEntry.objects.filter(id=old_entry.id).exists())
        self.assertTrue(DiaryEntry.objects.filter(id=movie_entry.id).exists())

    def test_new_mode_keeps_existing_book_and_adds_missing_diary_once(self):
        item = Item.objects.create(
            media_id="101",
            source=Sources.HARDCOVER.value,
            media_type=MediaTypes.BOOK.value,
            title="Existing Rich Book",
            image="https://example.com/existing.jpg",
        )
        Book.objects.create(
            user=self.user,
            item=item,
            status=Status.PLANNING.value,
            score=1,
            notes="keep me",
        )

        self._import()
        self._import()

        book = Book.objects.get(item=item)
        self.assertEqual(book.status, Status.PLANNING.value)
        self.assertEqual(book.score, 1)
        self.assertEqual(book.notes, "keep me")
        self.assertEqual(DiaryEntry.objects.filter(item=item).count(), 2)

    def test_unresolved_warns_and_skips(self):
        with FIXTURE.open("rb") as file:
            with patch("app.services.update_daily_statistics.delay"):
                counts, warnings = StoryGraphImporter(
                    file.read(),
                    self.user,
                    "new",
                    resolver=SimpleNamespace(resolve_rows=lambda rows: {}),
                ).import_data()

        self.assertEqual(counts, {})
        self.assertIn("Couldn't resolve StoryGraph book", warnings)


class StoryGraphResolverTests(TestCase):
    def test_uses_isbn_before_title_and_openlibrary_fallback(self):
        with FIXTURE.open("rb") as file:
            row = parse_export(file.read())[0]
        calls = []

        def search(_media_type, query, _page, source):
            calls.append((query, source))
            if source == Sources.OPENLIBRARY.value and query == "9780306406157":
                return {"results": [{"media_id": "ol-1", "title": "Rich Book", "image": ""}]}
            return {"results": []}

        with patch("integrations.imports.storygraph.resolver.services.search", side_effect=search):
            result = StoryGraphResolver().resolve_one(row)

        self.assertEqual(result["media_id"], "ol-1")
        self.assertEqual(
            calls,
            [
                ("9780306406157", Sources.HARDCOVER.value),
                ("Rich Book Author One", Sources.HARDCOVER.value),
                ("9780306406157", Sources.OPENLIBRARY.value),
            ],
        )


class StoryGraphApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="api", password="password")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_post_returns_task_and_poll_returns_status(self):
        task = Mock()
        task.delay.return_value = SimpleNamespace(id="storygraph-task")
        task_map = {"storygraph": task}

        with FIXTURE.open("rb") as file:
            upload = SimpleUploadedFile("storygraph.csv", file.read(), content_type="text/csv")

        with patch.dict("api.views.imports.TASKS_BY_SOURCE", task_map, clear=True):
            with patch.dict("api.services.imports.TASKS_BY_SOURCE", task_map, clear=True):
                response = self.client.post(
                    "/api/v1/imports/storygraph/",
                    {"mode": "new", "file": upload},
                    format="multipart",
                )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data["task_id"], "storygraph-task")
        self.assertTrue(task.delay.call_args.kwargs["file_path"].endswith(".csv"))

        TaskResult.objects.create(
            task_id="storygraph-task",
            task_name="Import from StoryGraph",
            task_kwargs=f"{{'file_path': '/tmp/storygraph.csv', 'user_id': {self.user.id}, 'mode': 'new'}}",
            status="SUCCESS",
            result="Imported 1 Book.",
        )

        poll = self.client.get("/api/v1/imports/tasks/storygraph-task/")

        self.assertEqual(poll.status_code, status.HTTP_200_OK)
        self.assertEqual(poll.data["status"], "SUCCESS")
