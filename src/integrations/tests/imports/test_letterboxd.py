import io
import zipfile
from datetime import timedelta
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

from app.models import DiaryEntry, Item, MediaLike, MediaTypes, Movie, Sources, Status
from integrations.imports.letterboxd.importer import LetterboxdImporter
from integrations.imports.letterboxd.parser import parse_export
from integrations.imports.letterboxd.resolver import LetterboxdResolver
from integrations.models import LetterboxdUriCache
from integrations.tasks import format_import_message
from lists.models import CustomList, CustomListItem
from social.models import Activity

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "letterboxd" / "minimal.zip"


class FakeResolver:
    """Test resolver keyed by the synthetic fixture URIs."""

    IDS = {
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


def export_zip(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


class ImportLetterboxdTests(TestCase):
    """Test Letterboxd ZIP imports."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="letterboxd",
            password="password",
        )

    def _import(self, mode="new"):
        with FIXTURE.open("rb") as file:
            return self._import_bytes(file.read(), mode=mode)

    def _import_bytes(self, payload, mode="new"):
        with patch("integrations.imports.letterboxd.importer.tmdb.movie", side_effect=tmdb_movie):
            with patch("app.models.providers.services.get_media_metadata", return_value={"max_progress": 1}):
                with patch("app.services.update_daily_statistics.delay"):
                    with patch("app.signals.update_daily_statistics.delay"):
                        return LetterboxdImporter(
                            payload,
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
        self.assertEqual(counts["diary"], 1)
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
        self.assertFalse(film_two.liked)
        self.assertTrue(MediaLike.objects.filter(user=self.user, item=film_two.item).exists())

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
        MediaLike.objects.create(user=self.user, item=item)
        with patch("app.signals.update_daily_statistics.delay"):
            entry = DiaryEntry.objects.create(user=self.user, item=item, consumed_at=timezone.now())
            Activity.objects.create(
                actor=self.user,
                verb="diary_created",
                target_type="diary",
                target_id=entry.id,
                item=item,
            )
            tv_item = Item.objects.create(
                media_id="tv-999",
                source=Sources.TMDB.value,
                media_type=MediaTypes.TV.value,
                title="Old TV",
                image="https://example.com/tv.jpg",
            )
            tv_entry = DiaryEntry.objects.create(user=self.user, item=tv_item, consumed_at=timezone.now())
            Activity.objects.create(
                actor=self.user,
                verb="diary_created",
                target_type="diary",
                target_id=tv_entry.id,
                item=tv_item,
            )

        self._import(mode="overwrite")

        self.assertTrue(CustomList.objects.filter(id=manual_list.id).exists())
        self.assertFalse(CustomList.objects.filter(id=old_letterboxd_list.id).exists())
        self.assertFalse(Movie.objects.filter(item=item).exists())
        self.assertFalse(DiaryEntry.objects.filter(item=item).exists())
        self.assertFalse(Activity.objects.filter(target_id=entry.id, target_type="diary").exists())
        self.assertTrue(Activity.objects.filter(target_id=tv_entry.id, target_type="diary").exists())
        self.assertFalse(MediaLike.objects.filter(item=item).exists())
        self.assertTrue(DiaryEntry.objects.filter(id=tv_entry.id).exists())

    def test_new_mode_skips_duplicate_diary_and_list_items(self):
        self._import()
        counts, _ = self._import()

        self.assertEqual(DiaryEntry.objects.filter(item__media_id="101").count(), 1)
        self.assertEqual(CustomListItem.objects.count(), 2)
        self.assertEqual(counts.get("likes", 0), 0)

    def test_import_keeps_multiple_diary_logs_for_same_movie(self):
        payload = export_zip(
            {
                "diary.csv": (
                    "Date,Name,Year,Letterboxd URI,Rating,Rewatch,Tags,Watched Date\n"
                    "2024-01-02,Film One,1999,https://boxd.it/film-one-watched,4,No,,2024-01-02\n"
                    "2025-02-06,Film One,1999,https://boxd.it/film-one-watched,5,Yes,,2025-02-06\n"
                ),
            },
        )

        self._import_bytes(payload)

        entries = DiaryEntry.objects.filter(item__media_id="101").order_by("consumed_at")
        self.assertEqual(entries.count(), 2)
        self.assertEqual([entry.consumed_at.date().isoformat() for entry in entries], ["2024-01-02", "2025-02-06"])
        self.assertEqual(Movie.objects.get(item__media_id="101").end_date.date().isoformat(), "2024-01-02")

    def test_likes_do_not_upgrade_watchlist_to_completed(self):
        payload = export_zip(
            {
                "watchlist.csv": (
                    "Date,Name,Year,Letterboxd URI\n"
                    "2024-01-05,Film Three,2001,https://boxd.it/film-three\n"
                ),
                "likes/films.csv": (
                    "Date,Name,Year,Letterboxd URI\n"
                    "2024-01-08,Film Three,2001,https://boxd.it/film-three\n"
                ),
            },
        )

        self._import_bytes(payload)

        film_three = Movie.objects.get(item__media_id="303")
        self.assertEqual(film_three.status, Status.PLANNING.value)
        self.assertFalse(film_three.liked)
        self.assertTrue(MediaLike.objects.filter(user=self.user, item=film_three.item).exists())

    def test_likes_import_does_not_create_tracking_row(self):
        payload = export_zip(
            {
                "likes/films.csv": (
                    "Date,Name,Year,Letterboxd URI\n"
                    "2024-01-08,Film Two,2000,https://boxd.it/film-two\n"
                ),
            },
        )

        counts, _ = self._import_bytes(payload)

        item = Item.objects.get(media_id="202")
        self.assertEqual(counts["likes"], 1)
        self.assertTrue(MediaLike.objects.filter(user=self.user, item=item).exists())
        self.assertFalse(Movie.objects.filter(user=self.user, item=item).exists())

    def test_rating_uses_name_year_fallback_and_single_existing_diary(self):
        payload = export_zip(
            {
                "diary.csv": (
                    "Date,Name,Year,Letterboxd URI,Rating,Rewatch,Tags,Watched Date\n"
                    "2024-01-02,Film One,1999,https://boxd.it/film-one-diary,,No,,2024-01-01\n"
                ),
                "watched.csv": (
                    "Date,Name,Year,Letterboxd URI\n"
                    "2024-01-03,Film One,1999,https://boxd.it/film-one-watched\n"
                ),
                "ratings.csv": (
                    "Date,Name,Year,Letterboxd URI,Rating\n"
                    "2024-02-02,Film One,1999,https://boxd.it/film-one-diary,4\n"
                ),
            },
        )

        self._import_bytes(payload)

        entry = DiaryEntry.objects.get(item__media_id="101")
        self.assertEqual(entry.rating, 8)
        self.assertIsNone(Movie.objects.get(item__media_id="101").score)

    def test_api_surfaces_imported_letterboxd_data(self):
        self._import()
        client = APIClient()
        client.force_authenticate(self.user)

        tracking = client.get("/api/v1/tracking/?media_type=movie")
        diary = client.get("/api/v1/diary/")
        liked = client.get("/api/v1/me/liked-media/")
        profile = client.get("/api/v1/me/")
        lists = client.get("/api/v1/lists/")

        self.assertEqual(tracking.data["count"], 3)
        self.assertEqual(diary.data["count"], 1)
        self.assertEqual(liked.data["count"], 1)
        self.assertEqual(profile.data["counts"]["diary_entries"], 1)
        self.assertEqual(profile.data["counts"]["liked_items"], 1)
        self.assertEqual(profile.data["counts"]["lists"], CustomList.objects.filter(owner=self.user).count())
        self.assertEqual(lists.data["count"], 1)

    def test_diary_api_returns_all_entries_for_import_scale(self):
        item = Item.objects.create(
            media_id="bulk",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Bulk",
            image="https://example.com/bulk.jpg",
        )
        now = timezone.now()
        with patch("app.signals.update_daily_statistics.delay"):
            DiaryEntry.objects.bulk_create(
                [
                    DiaryEntry(user=self.user, item=item, consumed_at=now + timedelta(days=index))
                    for index in range(101)
                ],
            )
        client = APIClient()
        client.force_authenticate(self.user)

        response = client.get("/api/v1/diary/")

        self.assertEqual(response.data["count"], 101)
        self.assertEqual(len(response.data["results"]), 101)

    def test_import_message_formats_letterboxd_counts(self):
        message = format_import_message(
            {
                "movie": 2,
                "diary": 1,
                "watchlist": 3,
                "list_items": 4,
                "likes": 1,
            },
        )

        self.assertEqual(
            message,
            "Imported 2 Movies, 1 diary entry, 3 watchlist items, 4 list items and 1 like.",
        )


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

    def test_user_diary_redirect_canonicalized(self):
        resolver = LetterboxdResolver()
        user_page = SimpleNamespace(
            text="<html></html>",
            url="https://letterboxd.com/armaandave/film/star-wars/",
            raise_for_status=lambda: None,
        )
        film_page = SimpleNamespace(
            text='<body data-tmdb-id="1893"></body>',
            url="https://letterboxd.com/film/star-wars/",
            raise_for_status=lambda: None,
        )

        def fake_get(url, **kwargs):
            if "armaandave/film/" in url:
                return user_page
            if "letterboxd.com/film/" in url:
                return film_page
            return user_page

        with patch.object(resolver.session, "get", side_effect=fake_get):
            resolved = resolver.resolve_many(["https://boxd.it/diary-link"])

        self.assertEqual(resolved["https://boxd.it/diary-link"]["tmdb_id"], "1893")
        self.assertEqual(resolved["https://boxd.it/diary-link"]["confidence"], "scrape")


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
