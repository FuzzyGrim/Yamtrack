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

from app.models import DiaryEntry, Item, MediaTypes, Movie, Sources, Status
from integrations.imports.letterboxd.importer import LetterboxdImporter
from integrations.imports.letterboxd.parser import parse_export
from integrations.imports.letterboxd.resolver import LetterboxdResolver
from integrations.models import LetterboxdUriCache
from lists.models import CustomList, CustomListItem

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "letterboxd" / "minimal.zip"


class FakeResolver:
    """Test resolver keyed by the synthetic fixture URIs."""

    IDS = {
        "https://boxd.it/film-one-diary": "101",
        "https://boxd.it/film-one-watched": "101",
        "https://boxd.it/film-two": "202",
        "https://boxd.it/film-three": "303",
    }

    def resolve_rows(self, rows):
        return {
            uri: {"tmdb_id": tmdb_id, "confidence": "scrape"}
            for uri, tmdb_id in self.IDS.items()
        }


def tmdb_movie(tmdb_id):
    return {
        "101": {"title": "Film One", "image": "https://example.com/film-one.jpg"},
        "202": {"title": "Film Two", "image": "https://example.com/film-two.jpg"},
        "303": {"title": "Film Three", "image": "https://example.com/film-three.jpg"},
    }[str(tmdb_id)]


class ImportLetterboxdTests(TestCase):
    """Test Letterboxd ZIP imports."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="letterboxd",
            password="password",
        )

    def _import(self, mode="new"):
        with FIXTURE.open("rb") as file:
            with patch("integrations.imports.letterboxd.importer.tmdb.movie", side_effect=tmdb_movie):
                with patch("app.models.providers.services.get_media_metadata", return_value={"max_progress": 1}):
                    with patch("app.services.update_daily_statistics.delay"):
                        with patch("app.signals.update_daily_statistics.delay"):
                            return LetterboxdImporter(
                                file.read(),
                                self.user,
                                mode,
                                resolver=FakeResolver(),
                            ).import_data()

    def test_parse_zip_and_list_csv(self):
        with FIXTURE.open("rb") as file:
            export = parse_export(file.read())

        self.assertEqual(len(export.diary), 1)
        self.assertEqual(len(export.watched), 2)
        self.assertEqual(export.diary[0]["rating"], 9)
        self.assertEqual(export.diary[0]["rewatch"], True)
        self.assertEqual(export.diary[0]["tags"], ["classic", "noir"])
        self.assertEqual(export.lists[0].name, "Favorites")
        self.assertEqual([row["position"] for row in export.lists[0].rows], [1, 2])

    def test_full_import(self):
        counts, warnings = self._import()

        self.assertIsNone(warnings)
        self.assertEqual(counts[MediaTypes.MOVIE.value], 1)
        self.assertEqual(Movie.objects.count(), 3)

        film_one = Movie.objects.get(item__media_id="101")
        self.assertEqual(film_one.status, Status.COMPLETED.value)
        self.assertEqual(film_one.progress, 1)
        self.assertEqual(film_one.score, 9)

        entry = DiaryEntry.objects.get(item=film_one.item)
        self.assertEqual(DiaryEntry.objects.filter(item=film_one.item).count(), 1)
        self.assertEqual(entry.review, "Great review.")
        self.assertEqual(entry.rating, 9)
        self.assertEqual(entry.is_rewatch, True)
        self.assertEqual(sorted(tag.name for tag in entry.tags.all()), ["classic", "noir"])

        film_two = Movie.objects.get(item__media_id="202")
        self.assertEqual(film_two.status, Status.COMPLETED.value)
        self.assertEqual(film_two.score, 7)
        self.assertEqual(film_two.liked, True)

        film_three = Movie.objects.get(item__media_id="303")
        self.assertEqual(film_three.status, Status.PLANNING.value)

        custom_list = CustomList.objects.get(name="Favorites")
        list_items = list(CustomListItem.objects.filter(custom_list=custom_list))
        self.assertEqual([item.position for item in list_items], [1, 2])
        self.assertEqual([item.item.media_id for item in list_items], ["101", "202"])

    def test_overwrite_deletes_only_letterboxd_data(self):
        manual_list = CustomList.objects.create(owner=self.user, name="Manual")
        old_letterboxd_list = CustomList.objects.create(
            owner=self.user,
            name="Old Letterboxd",
            import_source="letterboxd",
        )
        item = Item.objects.create(
            media_id="999",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Old Movie",
            image="https://example.com/old.jpg",
        )
        Movie.objects.create(user=self.user, item=item, status=Status.PLANNING.value)
        with patch("app.signals.update_daily_statistics.delay"):
            DiaryEntry.objects.create(user=self.user, item=item, consumed_at=timezone.now())

        self._import(mode="overwrite")

        self.assertTrue(CustomList.objects.filter(id=manual_list.id).exists())
        self.assertFalse(CustomList.objects.filter(id=old_letterboxd_list.id).exists())
        self.assertFalse(Movie.objects.filter(item=item).exists())
        self.assertFalse(DiaryEntry.objects.filter(item=item).exists())

    def test_new_mode_skips_duplicate_diary_and_list_items(self):
        self._import()
        self._import()

        self.assertEqual(DiaryEntry.objects.filter(item__media_id="101").count(), 1)
        self.assertEqual(CustomListItem.objects.count(), 2)


class LetterboxdResolverTests(TestCase):
    """Test URI resolution cascade."""

    def test_cache_hit_skips_http(self):
        LetterboxdUriCache.objects.create(uri="https://boxd.it/cache", tmdb_id="10")
        resolver = LetterboxdResolver()

        with patch.object(resolver.session, "get") as get_mock:
            resolved = resolver.resolve_many(["https://boxd.it/cache"])

        self.assertEqual(resolved["https://boxd.it/cache"]["tmdb_id"], "10")
        get_mock.assert_not_called()

    def test_scrape_tmdb_id(self):
        resolver = LetterboxdResolver()
        html = '<a class="micro-button" data-track-action="TMDB" href="https://www.themoviedb.org/movie/42">TMDB</a>'

        with patch.object(
            resolver.session,
            "get",
            return_value=SimpleNamespace(text=html, raise_for_status=lambda: None),
        ):
            resolved = resolver.resolve_many(["https://boxd.it/scrape"])

        self.assertEqual(resolved["https://boxd.it/scrape"]["tmdb_id"], "42")
        self.assertEqual(resolved["https://boxd.it/scrape"]["confidence"], "scrape")

    def test_imdb_fallback(self):
        resolver = LetterboxdResolver()
        html = '<a href="https://www.imdb.com/title/tt1234567/">IMDb</a>'

        with patch.object(
            resolver.session,
            "get",
            return_value=SimpleNamespace(text=html, raise_for_status=lambda: None),
        ):
            with patch(
                "integrations.imports.letterboxd.resolver.tmdb.find",
                return_value={"movie_results": [{"id": 77}]},
            ):
                resolved = resolver.resolve_many(["https://boxd.it/imdb"])

        self.assertEqual(resolved["https://boxd.it/imdb"]["tmdb_id"], "77")
        self.assertEqual(resolved["https://boxd.it/imdb"]["imdb_id"], "tt1234567")

    def test_search_fallback_matches_year(self):
        resolver = LetterboxdResolver()

        with patch.object(
            resolver.session,
            "get",
            return_value=SimpleNamespace(text="<html></html>", raise_for_status=lambda: None),
        ):
            with patch(
                "integrations.imports.letterboxd.resolver.tmdb.search",
                return_value={"results": [{"media_id": "88", "release_date": "1999-03-01"}]},
            ):
                resolved = resolver.resolve_many(
                    ["https://boxd.it/search"],
                    [{"uri": "https://boxd.it/search", "name": "Search Film", "year": 1999}],
                )

        self.assertEqual(resolved["https://boxd.it/search"]["tmdb_id"], "88")
        self.assertEqual(resolved["https://boxd.it/search"]["confidence"], "search")


class LetterboxdApiTests(TestCase):
    """Test mobile API import queueing."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="api", password="password")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_post_returns_task_and_poll_returns_status(self):
        task = Mock()
        task.delay.return_value = SimpleNamespace(id="letterboxd-task")
        task_map = {"letterboxd": task}

        with FIXTURE.open("rb") as file:
            upload = SimpleUploadedFile(
                "letterboxd.zip",
                file.read(),
                content_type="application/zip",
            )

        with patch.dict("api.views.imports.TASKS_BY_SOURCE", task_map, clear=True):
            with patch.dict("api.services.imports.TASKS_BY_SOURCE", task_map, clear=True):
                response = self.client.post(
                    "/api/v1/imports/letterboxd/",
                    {"mode": "new", "file": upload},
                    format="multipart",
                )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data["task_id"], "letterboxd-task")
        self.assertIsInstance(task.delay.call_args.kwargs["file_path"], str)

        TaskResult.objects.create(
            task_id="letterboxd-task",
            task_name="Import from Letterboxd",
            task_kwargs=f"{{'file_path': '/tmp/letterboxd.zip', 'user_id': {self.user.id}, 'mode': 'new'}}",
            status="SUCCESS",
            result="Imported 1 Movie.",
        )

        poll = self.client.get("/api/v1/imports/tasks/letterboxd-task/")

        self.assertEqual(poll.status_code, status.HTTP_200_OK)
        self.assertEqual(poll.data["status"], "SUCCESS")
