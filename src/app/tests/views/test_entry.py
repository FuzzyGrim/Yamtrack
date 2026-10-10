from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import (
    TV,
    Episode,
    Item,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Status,
)
from lists.models import CustomList


class CreateEntryViewTests(TestCase):
    """Test the create entry view."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_create_entry_get(self):
        """Test the GET method of create_entry view."""
        response = self.client.get(reverse("create_entry"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/create_entry.html")
        self.assertIn("media_types", response.context)

        self.assertEqual(response.context["media_types"], MediaTypes.values)

    def test_create_entry_post_movie(self):
        """Test creating a movie entry."""
        form_data = {
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "status": Status.COMPLETED.value,
            "score": 8,
            "progress": 1,
            "start_date": "2023-01-01T00:00",
            "end_date": "2023-01-02T00:00",
        }

        response = self.client.post(reverse("create_entry"), form_data, follow=True)

        self.assertRedirects(response, reverse("create_entry"))

        self.assertTrue(
            Item.objects.filter(
                title="Test Movie",
                media_type=MediaTypes.MOVIE.value,
            ).exists(),
        )

        movie = Movie.objects.get(item__title="Test Movie")
        self.assertEqual(movie.status, Status.COMPLETED.value)
        self.assertEqual(movie.score, 8)
        self.assertEqual(movie.progress, 1)
        self.assertEqual(movie.user, self.user)

    def test_create_manual_stage_with_multiple_forms(self):
        """An imageless hybrid stage work can be saved and tracked."""
        response = self.client.post(
            reverse("create_entry"),
            {
                "title": "Local Stage Work",
                "media_type": "stage",
                "stage_forms": ["play", "musical"],
                "status": Status.COMPLETED.value,
                "score": 8,
                "notes": "Opening night",
            },
            follow=True,
        )

        self.assertContains(response, "Local Stage Work added successfully.")
        item = Item.objects.get(title="Local Stage Work")
        self.assertEqual(item.stage_forms, ["play", "musical"])
        self.assertEqual(item.stage_set.model._meta.verbose_name_plural, "stage")
        self.assertEqual(item.stage_set.get(user=self.user).notes, "Opening night")
        library = self.client.get(reverse("medialist", args=["test", "stage"]))
        self.assertContains(library, "Local Stage Work")
        self.assertContains(library, "Stage - Yamtrack")
        self.assertNotContains(library, "Stages")
        self.assertContains(library, "/test/stage")
        self.assertContains(library, "Search stage works in your list...")
        details = self.client.get(
            reverse(
                "media_details",
                args=["manual", "stage", item.media_id, "local-stage-work"],
            ),
        )
        self.assertContains(details, "Play, Musical")

    def test_repeat_stage_attendance_preserves_personal_details(self):
        """Repeat visits retain independent details and enforce ownership."""
        self.client.post(
            reverse("create_entry"),
            {
                "title": "Repeated Work",
                "media_type": "stage",
                "stage_forms": ["ballet"],
                "status": Status.COMPLETED.value,
                "end_date": "2026-09-01",
                "venue": "First Theatre",
                "location": "London",
                "production": "First Company",
                "notes": "First visit",
            },
        )
        item = Item.objects.get(title="Repeated Work")
        first = item.stage_set.get(user=self.user)
        payload = {
            "media_id": item.media_id,
            "source": "manual",
            "media_type": "stage",
            "status": Status.COMPLETED.value,
            "venue": "Second Theatre",
            "production": "Second Company",
            "notes": "Second visit",
            "score": 9,
        }
        self.client.post(reverse("media_save"), payload)
        self.assertEqual(item.stage_set.count(), 2)
        first.refresh_from_db()
        self.assertEqual(first.venue, "First Theatre")
        self.assertEqual(first.end_date.date().isoformat(), "2026-09-01")
        second = item.stage_set.exclude(pk=first.pk).get()
        self.assertEqual(second.venue, "Second Theatre")
        self.assertIsNone(second.end_date)
        payload.update(instance_id=second.pk, venue="", production="Updated Company")
        self.client.post(reverse("media_save"), payload)
        second.refresh_from_db()
        self.assertEqual(second.venue, "")
        self.assertEqual(second.history.first().production, "Updated Company")
        stranger = get_user_model().objects.create_user(username="stranger")
        self.client.force_login(stranger)
        self.assertEqual(
            self.client.post(reverse("media_save"), payload).status_code, 404
        )
        self.assertEqual(
            self.client.post(reverse("media_delete"), payload).status_code,
            404,
        )
        self.client.force_login(self.user)
        self.client.post(reverse("media_delete"), payload)
        self.assertEqual(item.stage_set.count(), 1)
        self.assertTrue(Item.objects.filter(pk=item.pk).exists())
        for name, arguments in [
            ("medialist", ["test", "stage"]),
            ("statistics", []),
            ("journal", []),
        ]:
            with self.subTest(view=name):
                response = self.client.get(reverse(name, args=arguments))
                self.assertEqual(response.status_code, 200)

    @patch("app.providers.services.session.get")
    def test_stage_organization_keeps_works_and_visits_distinct(self, provider_get):
        """Library, lists, preferences and statistics retain their native semantics."""
        provider_response = requests.Response()
        provider_response.status_code = 200
        provider_response._content = b'{"results":[]}'
        provider_get.return_value = provider_response
        for title, status in [("Alpha Stage", "Completed"), ("Beta Stage", "Planning")]:
            self.client.post(
                reverse("create_entry"),
                {
                    "title": title,
                    "media_type": "stage",
                    "stage_forms": ["play", "musical"],
                    "status": status,
                    "score": 8,
                },
            )
        work = Item.objects.get(title="Alpha Stage")
        self.client.post(
            reverse("media_save"),
            {
                "media_id": work.media_id,
                "source": "manual",
                "media_type": "stage",
                "status": "Completed",
                "notes": "Return attendance",
            },
        )
        listing = self.client.get(
            reverse("medialist", args=["test", "stage"]),
            {"layout": "table", "sort": "title", "status": "Completed"},
        )
        self.assertContains(listing, "Alpha Stage")
        self.assertContains(listing, "Play, Musical")
        self.assertEqual(
            [entry.item.title for entry in listing.context["media_list"]],
            ["Alpha Stage"],
        )
        self.assertEqual(listing.context["media_list"].paginator.count, 1)
        self.user.refresh_from_db()
        self.assertEqual(self.user.stage_layout, "table")
        self.assertEqual(self.user.stage_sort, "title")
        self.assertEqual(self.user.stage_status, "Completed")
        grid = self.client.get(
            reverse("medialist", args=["test", "stage"]), {"layout": "grid"}
        )
        self.assertContains(grid, "Play, Musical")
        self.client.post(reverse("list_create"), {"name": "Stage Library"})
        custom_list = CustomList.objects.get(name="Stage Library")
        toggle = {"item_id": work.pk, "custom_list_id": custom_list.pk}
        self.client.post(reverse("list_item_toggle"), toggle)
        details = self.client.get(reverse("list_detail", args=[custom_list.pk]))
        self.assertContains(details, "Alpha Stage")
        self.assertEqual(details.context["items_count"], 1)
        self.client.post(reverse("list_item_toggle"), toggle)
        self.assertEqual(custom_list.items.count(), 0)
        statistics = self.client.get(
            reverse("statistics"), {"start-date": "all", "end-date": "all"}
        )
        self.assertEqual(statistics.context["media_count"]["stage"], 3)
        self.assertContains(self.client.get(reverse("journal")), "Stage")
        enabled = [
            value for value in MediaTypes.values if value not in {"episode", "stage"}
        ]
        self.client.post(reverse("preferences"), {"media_types_checkboxes": enabled})
        self.user.refresh_from_db()
        self.assertFalse(self.user.stage_enabled)
        self.client.post(
            reverse("preferences"), {"media_types_checkboxes": [*enabled, "stage"]}
        )
        self.user.refresh_from_db()
        self.assertTrue(self.user.stage_enabled)
        stranger = get_user_model().objects.create_user(username="library-stranger")
        self.client.force_login(stranger)
        self.assertEqual(
            self.client.get(reverse("medialist", args=["test", "stage"])).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(reverse("list_item_toggle"), toggle).status_code, 404
        )

    def test_manual_stage_requires_valid_forms(self):
        """Missing and unknown classifications cannot create a stage work."""
        for classifications in ([], ["film"]):
            with self.subTest(classifications=classifications):
                self.client.post(
                    reverse("create_entry"),
                    {
                        "title": "Invalid Work",
                        "media_type": "stage",
                        "stage_forms": classifications,
                        "status": Status.PLANNING.value,
                    },
                )
                self.assertFalse(Item.objects.filter(title="Invalid Work").exists())

    def test_create_entry_post_tv(self):
        """Test creating a TV show entry."""
        form_data = {
            "title": "Test TV Show",
            "media_type": MediaTypes.TV.value,
            "status": Status.IN_PROGRESS.value,
            "score": 7,
        }

        response = self.client.post(reverse("create_entry"), form_data, follow=True)

        self.assertRedirects(response, reverse("create_entry"))

        self.assertTrue(
            Item.objects.filter(
                title="Test TV Show",
                media_type=MediaTypes.TV.value,
            ).exists(),
        )

        tv = TV.objects.get(item__title="Test TV Show")
        self.assertEqual(tv.status, Status.IN_PROGRESS.value)
        self.assertEqual(tv.score, 7)
        self.assertEqual(tv.user, self.user)

    def test_create_entry_post_season(self):
        """Test creating a season entry with parent TV."""
        tv_item = Item.objects.create(
            media_id="1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.TV.value,
            title="TV Show",
        )
        parent_tv = TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        form_data = {
            "title": "TV Show",
            "media_type": MediaTypes.SEASON.value,
            "season_number": 1,
            "parent_tv": parent_tv.id,
            "status": Status.IN_PROGRESS.value,
            "score": 7,
        }

        response = self.client.post(reverse("create_entry"), form_data, follow=True)

        self.assertRedirects(response, reverse("create_entry"))

        self.assertTrue(
            Item.objects.filter(
                title="TV Show",
                media_type=MediaTypes.SEASON.value,
                season_number=1,
            ).exists(),
        )

        season = Season.objects.get(item__title="TV Show")
        self.assertEqual(season.status, Status.IN_PROGRESS.value)
        self.assertEqual(season.score, 7)
        self.assertEqual(season.user, self.user)
        self.assertEqual(season.related_tv, parent_tv)

    def test_create_entry_post_episode(self):
        """Test creating an episode entry with parent season."""
        tv_item = Item.objects.create(
            media_id="1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.TV.value,
            title="TV Show",
        )
        parent_tv = TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        season_item = Item.objects.create(
            media_id="1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.SEASON.value,
            title="TV Show",
            season_number=1,
        )
        parent_season = Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=parent_tv,
            status=Status.IN_PROGRESS.value,
        )

        form_data = {
            "title": "TV Show",
            "media_type": MediaTypes.EPISODE.value,
            "season_number": 1,
            "episode_number": 1,
            "parent_season": parent_season.id,
            "end_date": "2023-01-02T00:00",
        }

        response = self.client.post(reverse("create_entry"), form_data, follow=True)

        self.assertRedirects(response, reverse("create_entry"))

        self.assertTrue(
            Item.objects.filter(
                title="TV Show",
                media_type=MediaTypes.EPISODE.value,
                season_number=1,
                episode_number=1,
            ).exists(),
        )

        episode = Episode.objects.get(item__title="TV Show")
        self.assertEqual(episode.related_season, parent_season)
        end_date_local = timezone.localtime(episode.end_date)
        self.assertEqual(end_date_local.strftime("%Y-%m-%d %H:%M"), "2023-01-02 00:00")

    def test_create_entry_post_duplicate_item(self):
        """Test creating a duplicate item."""
        tv_item = Item.objects.create(
            media_id="1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.TV.value,
            title="TV Show",
        )
        parent_tv = TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        season_item = Item.objects.create(
            media_id="1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.SEASON.value,
            title="TV Show",
            season_number=1,
        )
        Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=parent_tv,
            status=Status.IN_PROGRESS.value,
        )

        initial_count = Item.objects.count()

        form_data = {
            "title": "TV Show",
            "media_type": MediaTypes.SEASON.value,
            "season_number": 1,
            "parent_tv": parent_tv.id,
            "status": Status.IN_PROGRESS.value,
            "score": 7,
            "repeats": 0,
        }

        with transaction.atomic():
            self.client.post(reverse("create_entry"), form_data)

        self.assertEqual(Item.objects.count(), initial_count)
