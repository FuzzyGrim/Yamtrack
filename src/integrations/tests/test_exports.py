import csv
import json
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import Q
from django.test import TestCase
from django.urls import reverse

from app.models import (
    Anime,
    Book,
    Episode,
    Game,
    Item,
    Manga,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Stage,
    Status,
)


class StageExportRestoreTest(TestCase):
    """Own-data endpoints retain work forms and independent attendances."""

    def setUp(self):
        """Keep provider fixtures isolated from other import/export tests."""
        cache.clear()
        self.addCleanup(cache.clear)

    def test_invalid_provider_identity_does_not_block_valid_attendance_restore(self):
        """Reject unusable provider identities without losing valid CSV rows."""
        owner = get_user_model().objects.create_user(username="identity-owner")
        recipient = get_user_model().objects.create_user(username="identity-recipient")
        work = Item.objects.create(
            media_id="Q822850",
            source="wikidata",
            media_type="stage",
            title="The House of Bernarda Alba",
            stage_forms=["play"],
        )
        Stage.objects.create(item=work, user=owner, status="Completed", notes="Keep")
        self.client.force_login(owner)
        content = b"".join(self.client.get(reverse("export_csv")).streaming_content)
        original = next(csv.DictReader(StringIO(content.decode())))
        self.client.force_login(recipient)
        for source, media_id in (
            ("wikidata", "not-a-qid"),
            ("wikidata", ""),
            ("wikidata", "Q0"),
            ("wikidata", "Q0822850"),
            ("wikidata", " Q822850"),
            ("wikidata", "Q822850\n"),
            ("wikidata", "Q" + "8" * 36),
            ("tmdb", "822850"),
            ("unknown", "Q822850"),
        ):
            with self.subTest(source=source, media_id=media_id):
                invalid = {**original, "source": source, "media_id": media_id}
                expected_notes = f"Recovered after {source}:{media_id!r}"
                upload = StringIO()
                writer = csv.DictWriter(upload, fieldnames=original.keys())
                writer.writeheader()
                writer.writerows([invalid, {**original, "notes": expected_notes}])
                with (
                    self.assertLogs("celery.app.trace", level="INFO") as task_logs,
                    patch(
                        "app.providers.services.session.get",
                        side_effect=AssertionError("Restore must stay offline"),
                    ),
                ):
                    response = self.client.post(
                        reverse("import_yamtrack"),
                        {
                            "mode": "overwrite",
                            "yamtrack_csv": SimpleUploadedFile(
                                "identity.csv", upload.getvalue().encode()
                            ),
                        },
                    )
                self.assertEqual(response.status_code, 302)
                self.assertEqual(Item.objects.filter(media_type="stage").count(), 1)
                restored = Stage.objects.get(user=recipient)
                self.assertEqual(restored.item, work)
                self.assertEqual(restored.notes, expected_notes)
                self.assertEqual(Stage.objects.get(user=owner).notes, "Keep")
                result = "\n".join(task_logs.output)
                self.assertIn("Imported 1 Stage.", result)
                self.assertIn(
                    "Stage import requires a valid Wikidata work ID."
                    if source == "wikidata"
                    else "Stage import requires a manual or Wikidata source.",
                    result,
                )

    def test_stage_restore_rejects_season_and_episode_identity(self):
        """Non-applicable series fields cannot split a work or erase attendance."""
        owner = get_user_model().objects.create_user(username="standalone-owner")
        work = Item.objects.create(
            media_id="Q822850",
            source="wikidata",
            media_type="stage",
            title="The House of Bernarda Alba",
            stage_forms=["play"],
        )
        attendance = Stage.objects.create(
            item=work, user=owner, status="Completed", notes="Original visit"
        )
        self.client.force_login(owner)
        content = b"".join(self.client.get(reverse("export_csv")).streaming_content)
        original = next(csv.DictReader(StringIO(content.decode())))
        for field in ("season_number", "episode_number"):
            for value in ("1", "0", "not-a-number"):
                with self.subTest(field=field, value=value):
                    expected_notes = f"Recovered after {field}:{value}"
                    upload = StringIO()
                    writer = csv.DictWriter(upload, fieldnames=original.keys())
                    writer.writeheader()
                    writer.writerow({**original, field: value, "notes": "Invalid"})
                    writer.writerow(
                        {**original, "media_id": "Q822851", "notes": expected_notes}
                    )
                    with (
                        self.assertLogs("celery.app.trace", level="INFO") as task_logs,
                        patch(
                            "app.providers.services.session.get",
                            side_effect=AssertionError("Restore must stay offline"),
                        ),
                    ):
                        response = self.client.post(
                            reverse("import_yamtrack"),
                            {
                                "mode": "overwrite",
                                "yamtrack_csv": SimpleUploadedFile(
                                    "standalone.csv", upload.getvalue().encode()
                                ),
                            },
                        )
                    self.assertEqual(response.status_code, 302)
                    attendance.refresh_from_db()
                    self.assertEqual(attendance.notes, "Original visit")
                    self.assertEqual(
                        Stage.objects.get(user=owner, item__media_id="Q822851").notes,
                        expected_notes,
                    )
                    self.assertEqual(Item.objects.filter(media_type="stage").count(), 2)
                    self.assertEqual(Stage.objects.filter(user=owner).count(), 2)
                    result = "\n".join(task_logs.output)
                    self.assertIn("Imported 1 Stage.", result)
                    self.assertIn(
                        "Stage import cannot include season or episode numbers.",
                        result,
                    )

    def test_provider_artwork_round_trip_offline(self):
        """A licensed provider image keeps its credit through offline restore."""
        fixture = json.loads(
            (
                Path(__file__).parents[2] / "app/tests/mock_data/stage_artwork.json"
            ).read_text()
        )
        owner = get_user_model().objects.create_user(username="artwork-owner")
        self.client.force_login(owner)

        def source_response(url, **_kwargs):
            response = requests.Response()
            response.status_code = 200
            payload = (
                fixture["commons"]
                if url == "https://commons.wikimedia.org/w/api.php"
                else {"entities": {"Q822850": fixture["work"]}}
            )
            response._content = json.dumps(payload).encode()
            return response

        with patch("app.providers.services.session.get", side_effect=source_response):
            self.client.post(
                reverse("media_save"),
                {
                    "media_id": "Q822850",
                    "media_type": "stage",
                    "source": "wikidata",
                    "status": "Completed",
                    "venue": "Local Theatre",
                },
            )
        content = b"".join(self.client.get(reverse("export_csv")).streaming_content)
        Item.objects.get(media_id="Q822850").delete()
        with patch(
            "app.providers.services.session.get",
            side_effect=AssertionError("Restore must not require provider access"),
        ):
            self.client.post(
                reverse("import_yamtrack"),
                {
                    "mode": "new",
                    "yamtrack_csv": SimpleUploadedFile("stage.csv", content),
                },
            )
        work = Item.objects.get(media_id="Q822850")
        self.assertEqual(work.stage_artwork["artist"], "Test Photographer")
        self.assertEqual(work.stage_artwork["work_id"], "Q822850")
        response = self.client.get(
            reverse("medialist", args=[owner.username, "stage"]), {"layout": "table"}
        )
        self.assertContains(response, "Test Photographer")
        self.assertContains(response, "CC BY-SA 4.0")
        rows = list(csv.DictReader(StringIO(content.decode())))
        artwork = json.loads(rows[0]["stage_artwork"])
        artwork["source_url"] = "javascript:alert(1)"
        rows[0]["stage_artwork"] = json.dumps(artwork)
        malicious = StringIO()
        writer = csv.DictWriter(malicious, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
        work.delete()
        with patch(
            "app.providers.services.session.get",
            side_effect=AssertionError("Restore must remain offline"),
        ):
            self.client.post(
                reverse("import_yamtrack"),
                {
                    "mode": "new",
                    "yamtrack_csv": SimpleUploadedFile(
                        "stage.csv", malicious.getvalue().encode()
                    ),
                },
            )
        response = self.client.get(reverse("medialist", args=[owner.username, "stage"]))
        self.assertNotContains(response, "javascript:")
        self.assertNotContains(response, artwork["image"])
        self.assertEqual(Stage.objects.get().venue, "Local Theatre")
        self._assert_invalid_legacy_credits_omit_images(content)

    def _assert_invalid_legacy_credits_omit_images(self, content):
        """Unsupported development records lose imagery, never attendance."""
        for legacy_change in ("missing_schema", "development_schema"):
            with self.subTest(legacy_change=legacy_change):
                rows = list(csv.DictReader(StringIO(content.decode())))
                artwork = json.loads(rows[0]["stage_artwork"])
                if legacy_change == "missing_schema":
                    artwork.pop("schema")
                else:
                    artwork["schema"] = "development-format"
                rows[0]["stage_artwork"] = json.dumps(artwork)
                invalid = StringIO()
                writer = csv.DictWriter(invalid, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
                Item.objects.get(media_id="Q822850").delete()
                self.client.post(
                    reverse("import_yamtrack"),
                    {
                        "mode": "new",
                        "yamtrack_csv": SimpleUploadedFile(
                            "legacy.csv", invalid.getvalue().encode()
                        ),
                    },
                )
                self.assertEqual(Item.objects.get(media_id="Q822850").stage_artwork, {})
                self.assertEqual(Stage.objects.get().venue, "Local Theatre")

    def test_manual_attendance_round_trip(self):
        """A fresh restore needs no external lookup or original catalog rows."""
        owner = get_user_model().objects.create_user(username="owner")
        self.client.force_login(owner)
        self.client.post(
            reverse("create_entry"),
            {
                "title": "Local Hybrid",
                "media_type": "stage",
                "stage_forms": ["play", "musical"],
                "status": "Completed",
                "venue": "First Theatre",
                "end_date": "2026-09-01",
                "notes": "First visit\nSecond line",
            },
        )
        item = Item.objects.get(title="Local Hybrid")
        self.client.post(
            reverse("media_save"),
            {
                "media_id": item.media_id,
                "media_type": "stage",
                "source": "manual",
                "status": "Planning",
                "venue": "Second Theatre",
                "location": "Paris",
                "production": "Local Company",
                "notes": "Return visit",
                "score": 8,
            },
        )
        response = self.client.get(reverse("export_csv"))
        content = b"".join(response.streaming_content)
        rows = list(csv.DictReader(StringIO(content.decode())))
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["media_type"] for row in rows}, {"stage"})
        self.assertEqual(json.loads(rows[0]["stage_forms"]), ["play", "musical"])
        item.delete()
        recipient = get_user_model().objects.create_user(username="recipient")
        self.client.force_login(recipient)
        response = self.client.post(
            reverse("import_yamtrack"),
            {
                "mode": "new",
                "yamtrack_csv": SimpleUploadedFile(
                    "stage.csv", content, content_type="text/csv"
                ),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Stage.objects.filter(user=recipient).count(), 2)
        self.assertEqual(Stage.objects.filter(user=owner).count(), 0)
        restored = Item.objects.get(title="Local Hybrid")
        self.assertEqual(restored.stage_forms, ["play", "musical"])
        first = Stage.objects.get(user=recipient, venue="First Theatre")
        self.assertEqual(first.end_date.date().isoformat(), "2026-09-01")
        self.assertEqual(first.notes, "First visit\nSecond line")
        second = Stage.objects.get(user=recipient, venue="Second Theatre")
        self.assertEqual(second.production, "Local Company")
        self.assertEqual(second.location, "Paris")
        self.assertEqual(second.score, 8)
        broken = StringIO()
        writer = csv.DictWriter(broken, fieldnames=rows[0].keys())
        writer.writeheader()
        for row in rows:
            row["stage_forms"] = '["film"]'
            writer.writerow(row)
        self.client.post(
            reverse("import_yamtrack"),
            {
                "mode": "overwrite",
                "yamtrack_csv": SimpleUploadedFile(
                    "invalid.csv", broken.getvalue().encode()
                ),
            },
        )
        self.assertEqual(Stage.objects.filter(user=recipient).count(), 2)
        self.assertEqual(
            Item.objects.get(pk=restored.pk).stage_forms, ["play", "musical"]
        )
        broken = StringIO()
        writer = csv.DictWriter(broken, fieldnames=rows[0].keys())
        writer.writeheader()
        for row in rows:
            row.update(title="", stage_forms='["play"]')
            writer.writerow(row)
        self.client.post(
            reverse("import_yamtrack"),
            {
                "mode": "overwrite",
                "yamtrack_csv": SimpleUploadedFile(
                    "invalid.csv", broken.getvalue().encode()
                ),
            },
        )
        self.assertEqual(Stage.objects.filter(user=recipient).count(), 2)
        self.assertEqual(Item.objects.get(pk=restored.pk).title, "Local Hybrid")


class ExportCSVTest(TestCase):
    """Test exporting media to CSV."""

    def setUp(self):
        """Create necessary data for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_superuser(**self.credentials)
        self.client.login(**self.credentials)

        item_movie = Item.objects.create(
            media_id="10494",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Perfect Blue",
            image="https://image.url",
        )
        Movie.objects.create(
            item=item_movie,
            user=self.user,
            score=9,
            status=Status.COMPLETED.value,
            notes="Nice",
            start_date=datetime(2023, 6, 1, 0, 0, tzinfo=UTC),
            end_date=datetime(2023, 6, 1, 0, 0, tzinfo=UTC),
        )

        item_season = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="https://image.url",
            season_number=1,
        )

        season = Season.objects.create(
            item=item_season,
            user=self.user,
            score=9,
            status=Status.IN_PROGRESS.value,
            notes="Nice",
        )

        item_episode = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="https://image.url",
            season_number=1,
            episode_number=1,
        )
        Episode.objects.create(
            item=item_episode,
            related_season=season,
            end_date=datetime(2023, 6, 1, 0, 0, tzinfo=UTC),
        )

        item_anime = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Cowboy Bebop",
            image="https://image.url",
        )
        Anime.objects.create(
            item=item_anime,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=2,
            start_date=datetime(2021, 6, 1, 0, 0, tzinfo=UTC),
        )

        item_manga = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.MANGA.value,
            title="Berserk",
            image="https://image.url",
        )
        Manga.objects.create(
            item=item_manga,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=2,
            start_date=datetime(2021, 6, 1, 0, 0, tzinfo=UTC),
        )

        item_game = Item.objects.create(
            media_id="1",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="The Witcher 3: Wild Hunt",
            image="https://image.url",
        )
        Game.objects.create(
            item=item_game,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=120,
            start_date=datetime(2021, 6, 1, 0, 0, tzinfo=UTC),
        )

        item_book = Item.objects.create(
            media_id="OL21733390M",
            source=Sources.OPENLIBRARY.value,
            media_type=MediaTypes.BOOK.value,
            title="Fantastic Mr. Fox",
            image="https://image.url",
        )
        Book.objects.create(
            item=item_book,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=120,
            start_date=datetime(2021, 6, 1, 0, 0, tzinfo=UTC),
        )

    def test_export_csv(self):
        """Basic test exporting media to CSV."""
        # Generate the CSV file by accessing the export view
        response = self.client.get(reverse("export_csv"))

        # Assert that the response is successful (status code 200)
        self.assertEqual(response.status_code, 200)

        # Assert that the response content type is text/csv
        self.assertEqual(response["Content-Type"], "text/csv")

        # Read the streaming content and decode it
        content = b"".join(response.streaming_content).decode("utf-8")

        # Create a CSV reader from the CSV content
        reader = csv.DictReader(StringIO(content))

        db_media_ids = set(
            Item.objects.filter(
                Q(tv__user=self.user)
                | Q(movie__user=self.user)
                | Q(season__user=self.user)
                | Q(episode__related_season__user=self.user)
                | Q(anime__user=self.user)
                | Q(manga__user=self.user)
                | Q(game__user=self.user)
                | Q(book__user=self.user),
            ).values_list("media_id", flat=True),
        )

        # Verify each row in the CSV exists in the database
        for row in reader:
            media_id = row["media_id"]
            self.assertIn(media_id, db_media_ids)
