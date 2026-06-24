from datetime import UTC, datetime
from importlib import import_module
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from api.serializers.common import media_summary_from_item
from app.models import (
    CustomBackdropPreference,
    CustomPosterPreference,
    DiaryEntry,
    Item,
    MediaLike,
    MediaTypes,
    Movie,
    Sources,
    Status,
)
from app.services import set_media_like, update_diary_entry_tags
from lists.models import CustomList, CustomListItem
from social.models import ContentLike


class ApiV1FoundationTests(TestCase):
    """Smoke tests for the v1 mobile API foundation."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def test_health_is_public(self):
        response = self.client.get("/api/v1/health/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "ok")

    def test_meta_is_public(self):
        response = self.client.get("/api/v1/meta/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("movie", response.data["media_types"])
        self.assertIn("Completed", response.data["status_choices"])

    def test_register_returns_tokens_and_user(self):
        response = self.client.post(
            "/api/v1/auth/register/",
            {
                "username": "iosuser",
                "email": "ios@example.com",
                "password": "strong-password-123",
                "password_confirm": "strong-password-123",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertEqual(response.data["user"]["username"], "iosuser")

    def test_login_and_me(self):
        get_user_model().objects.create_user(
            username="mobile",
            email="mobile@example.com",
            password="strong-password-123",
        )

        login = self.client.post(
            "/api/v1/auth/login/",
            {
                "username_or_email": "mobile@example.com",
                "password": "strong-password-123",
            },
            format="json",
        )
        self.assertEqual(login.status_code, status.HTTP_200_OK)

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
        response = self.client.get("/api/v1/me/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["username"], "mobile")

    def test_me_includes_profile_menu_counts(self):
        user = get_user_model().objects.create_user(username="profile-counts", password="strong-password-123")
        completed = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="550",
            title="Completed",
        )
        planned = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="551",
            title="Planned",
        )
        Movie.objects.bulk_create(
            [
                Movie(user=user, item=completed, status=Status.IN_PROGRESS.value),
                Movie(user=user, item=planned, status=Status.PLANNING.value),
            ]
        )
        CustomList.objects.create(owner=user, name="Favorites")
        reviewed = DiaryEntry.objects.create(
            user=user,
            item=completed,
            consumed_at=timezone.now(),
            review="Good.",
            liked=True,
        )
        MediaLike.objects.create(user=user, item=completed)
        update_diary_entry_tags(reviewed, ["great"])
        self.client.force_authenticate(user)

        response = self.client.get("/api/v1/me/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        counts = response.data["counts"]
        self.assertEqual(counts["library_items"], 1)
        self.assertEqual(counts["reviews"], 1)
        self.assertEqual(counts["planned_items"], 1)
        self.assertEqual(counts["liked_items"], 1)
        self.assertEqual(counts["tags"], 1)
        self.assertEqual(counts["lists"], 1)

    def test_lists_include_ordered_capped_preview_items(self):
        user = get_user_model().objects.create_user(username="list-previews", password="strong-password-123")
        custom_list = CustomList.objects.create(owner=user, name="Weekend Watchlist")
        items = self._create_movie_items(13, title_prefix="Preview", media_id_prefix="preview")
        for index, item in enumerate(items):
            CustomListItem.objects.create(
                custom_list=custom_list,
                item=item,
                position=13 - index,
            )
        self.client.force_authenticate(user)

        response = self.client.get("/api/v1/lists/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        result = response.data["results"][0]
        self.assertNotIn("items", result)
        self.assertEqual(result["items_count"], 13)
        self.assertEqual(len(result["preview_items"]), 12)
        self.assertEqual(result["preview_items"][0]["title"], "Preview 12")
        self.assertEqual(result["preview_items"][-1]["title"], "Preview 01")
        self.assertEqual(result["preview_items"][0]["poster_url"], result["preview_items"][0]["image_url"])

    def test_ranked_list_reorder_updates_positions(self):
        user = get_user_model().objects.create_user(username="list-reorder", password="strong-password-123")
        custom_list = CustomList.objects.create(owner=user, name="Ranked", is_ranked=True)
        items = self._create_movie_items(3, title_prefix="Ranked", media_id_prefix="ranked")
        for index, item in enumerate(items, start=1):
            CustomListItem.objects.create(custom_list=custom_list, item=item, position=index)
        self.client.force_authenticate(user)

        response = self.client.patch(
            f"/api/v1/lists/{custom_list.id}/items/reorder/",
            {"item_ids": [items[2].id, items[0].id, items[1].id]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([item["ref"]["item_id"] for item in response.data["items"]], [items[2].id, items[0].id, items[1].id])
        self.assertEqual(
            list(CustomListItem.objects.filter(custom_list=custom_list).values_list("item_id", "position")),
            [(items[2].id, 1), (items[0].id, 2), (items[1].id, 3)],
        )

    def test_reorder_rejects_wrong_item_set(self):
        user = get_user_model().objects.create_user(username="list-reorder-bad", password="strong-password-123")
        custom_list = CustomList.objects.create(owner=user, name="Ranked")
        items = self._create_movie_items(2, title_prefix="Ranked Bad", media_id_prefix="ranked-bad")
        for item in items:
            CustomListItem.objects.create(custom_list=custom_list, item=item)
        self.client.force_authenticate(user)

        response = self.client.patch(
            f"/api/v1/lists/{custom_list.id}/items/reorder/",
            {"item_ids": [items[0].id]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reorder_requires_edit_permission(self):
        owner = get_user_model().objects.create_user(username="list-reorder-owner", password="strong-password-123")
        other = get_user_model().objects.create_user(username="list-reorder-other", password="strong-password-123")
        custom_list = CustomList.objects.create(owner=owner, name="Ranked")
        item = self._create_movie_items(1, title_prefix="Ranked Forbidden", media_id_prefix="ranked-forbidden")[0]
        CustomListItem.objects.create(custom_list=custom_list, item=item)
        self.client.force_authenticate(other)

        response = self.client.patch(
            f"/api/v1/lists/{custom_list.id}/items/reorder/",
            {"item_ids": [item.id]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_ranked_add_assigns_next_position_and_normal_add_leaves_null(self):
        user = get_user_model().objects.create_user(username="list-add-position", password="strong-password-123")
        ranked = CustomList.objects.create(owner=user, name="Ranked", is_ranked=True)
        normal = CustomList.objects.create(owner=user, name="Normal")
        existing, ranked_item, normal_item = self._create_movie_items(3, title_prefix="Add", media_id_prefix="add")
        CustomListItem.objects.create(custom_list=ranked, item=existing, position=4)
        self.client.force_authenticate(user)

        ranked_response = self.client.post(
            f"/api/v1/lists/{ranked.id}/items/",
            {"ref": {"source": ranked_item.source, "media_type": ranked_item.media_type, "media_id": ranked_item.media_id}},
            format="json",
        )
        normal_response = self.client.post(
            f"/api/v1/lists/{normal.id}/items/",
            {"ref": {"source": normal_item.source, "media_type": normal_item.media_type, "media_id": normal_item.media_id}},
            format="json",
        )

        self.assertEqual(ranked_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(normal_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(CustomListItem.objects.get(custom_list=ranked, item=ranked_item).position, 5)
        self.assertIsNone(CustomListItem.objects.get(custom_list=normal, item=normal_item).position)

    def test_ranked_delete_renumbers_remaining_items(self):
        user = get_user_model().objects.create_user(username="list-delete-ranked", password="strong-password-123")
        custom_list = CustomList.objects.create(owner=user, name="Ranked", is_ranked=True)
        items = self._create_movie_items(3, title_prefix="Delete", media_id_prefix="delete")
        for index, item in enumerate(items, start=1):
            CustomListItem.objects.create(custom_list=custom_list, item=item, position=index)
        self.client.force_authenticate(user)

        response = self.client.delete(f"/api/v1/lists/{custom_list.id}/items/{items[1].id}/")

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(
            list(CustomListItem.objects.filter(custom_list=custom_list).values_list("item_id", "position")),
            [(items[0].id, 1), (items[2].id, 2)],
        )

    def test_mode_switch_assigns_and_preserves_positions(self):
        user = get_user_model().objects.create_user(username="list-mode-switch", password="strong-password-123")
        custom_list = CustomList.objects.create(owner=user, name="Mode")
        items = self._create_movie_items(3, title_prefix="Mode", media_id_prefix="mode")
        CustomListItem.objects.create(custom_list=custom_list, item=items[0], position=2)
        CustomListItem.objects.create(custom_list=custom_list, item=items[1])
        CustomListItem.objects.create(custom_list=custom_list, item=items[2])
        self.client.force_authenticate(user)

        ranked = self.client.patch(f"/api/v1/lists/{custom_list.id}/", {"is_ranked": True}, format="json")
        normal = self.client.patch(f"/api/v1/lists/{custom_list.id}/", {"is_ranked": False}, format="json")
        detail = self.client.get(f"/api/v1/lists/{custom_list.id}/")

        self.assertEqual(ranked.status_code, status.HTTP_200_OK)
        self.assertEqual(normal.status_code, status.HTTP_200_OK)
        self.assertFalse(detail.data["is_ranked"])
        self.assertEqual([item["position"] for item in detail.data["items"]], [1, 2, 3])
        self.assertEqual([item["ref"]["item_id"] for item in detail.data["items"]], [items[0].id, items[1].id, items[2].id])

    def test_ranked_backfill_migration_marks_positioned_lists(self):
        user = get_user_model().objects.create_user(username="list-migration", password="strong-password-123")
        custom_list = CustomList.objects.create(owner=user, name="Imported", is_ranked=False)
        item = self._create_movie_items(1, title_prefix="Imported", media_id_prefix="imported")[0]
        CustomListItem.objects.create(custom_list=custom_list, item=item, position=1)

        import_module("lists.migrations.0005_customlist_is_ranked").backfill_ranked_lists(import_module("django.apps").apps, None)

        custom_list.refresh_from_db()
        self.assertTrue(custom_list.is_ranked)

    def test_lists_membership_query_returns_has_item(self):
        user = get_user_model().objects.create_user(username="list-membership", password="strong-password-123")
        item = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="membership",
            title="Membership",
        )
        with_item = CustomList.objects.create(owner=user, name="With")
        without_item = CustomList.objects.create(owner=user, name="Without")
        CustomListItem.objects.create(custom_list=with_item, item=item)
        self.client.force_authenticate(user)

        response = self.client.get(
            "/api/v1/lists/",
            {
                "ref[source]": Sources.TMDB.value,
                "ref[media_type]": MediaTypes.MOVIE.value,
                "ref[media_id]": "membership",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            {result["name"]: result["has_item"] for result in response.data["results"]},
            {with_item.name: True, without_item.name: False},
        )

    def test_tracking_list_paginates_before_serializing_movies(self):
        user = get_user_model().objects.create_user(username="tracking-pages", password="strong-password-123")
        self._create_movies(user, 30)
        self.client.force_authenticate(user)

        with patch("api.views.tracking.media_summary_from_item", wraps=media_summary_from_item) as summary_mock:
            response = self.client.get("/api/v1/tracking/?media_type=movie")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 30)
        self.assertEqual(len(response.data["results"]), 25)
        self.assertIsNotNone(response.data["next"])
        self.assertIsNone(response.data["previous"])
        self.assertEqual(summary_mock.call_count, 25)

    def test_tracking_list_page_two_returns_next_movie_page(self):
        user = get_user_model().objects.create_user(username="tracking-page-two", password="strong-password-123")
        self._create_movies(user, 30)
        self.client.force_authenticate(user)

        response = self.client.get("/api/v1/tracking/?media_type=movie&page=2")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 30)
        self.assertEqual(len(response.data["results"]), 5)
        self.assertIsNone(response.data["next"])
        self.assertIsNotNone(response.data["previous"])

    def test_tracking_list_status_filters_planning_items(self):
        user = get_user_model().objects.create_user(username="tracking-planning", password="strong-password-123")
        completed_items = self._create_movie_items(3, title_prefix="Completed", media_id_prefix="c")
        planned_items = self._create_movie_items(4, title_prefix="Planned", media_id_prefix="p")
        Movie.objects.bulk_create(
            [Movie(user=user, item=item, status=Status.COMPLETED.value) for item in completed_items]
            + [Movie(user=user, item=item, status=Status.PLANNING.value) for item in planned_items]
        )
        self.client.force_authenticate(user)

        response = self.client.get("/api/v1/tracking/?media_type=movie&status=Planning")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 4)
        self.assertEqual(len(response.data["results"]), 4)
        self.assertEqual(
            {item["tracking"]["status"] for item in response.data["results"]},
            {Status.PLANNING.value},
        )

    def test_set_media_like_is_idempotent_and_keeps_social_likes_separate(self):
        user = get_user_model().objects.create_user(username="media-like-service", password="strong-password-123")
        item = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="svc",
            title="Service",
        )
        entry = DiaryEntry.objects.create(user=user, item=item, consumed_at=timezone.now(), liked=True)
        ContentLike.objects.create(user=user, target_type=ContentLike.DIARY_ENTRY, target_id=entry.id)

        set_media_like(user, item, liked=True)
        set_media_like(user, item, liked=True)
        set_media_like(user, item, liked=False)

        entry.refresh_from_db()
        self.assertEqual(MediaLike.objects.filter(user=user, item=item).count(), 0)
        self.assertFalse(entry.liked)
        self.assertEqual(ContentLike.objects.filter(user=user, target_type=ContentLike.DIARY_ENTRY).count(), 1)

    @patch("api.views.profile.provider_services.get_media_metadata")
    def test_liked_media_endpoint_materializes_without_tracking_or_diary(self, metadata_mock):
        user = get_user_model().objects.create_user(username="liked-media", password="strong-password-123")
        self.client.force_authenticate(user)
        metadata_mock.return_value = {"title": "Fight Club", "image": "https://example.com/fight.jpg"}
        payload = {
            "ref": {
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "media_id": "550",
            },
        }

        liked = self.client.post("/api/v1/me/liked-media/", payload, format="json")
        listed = self.client.get("/api/v1/me/liked-media/")
        unliked = self.client.delete("/api/v1/me/liked-media/", payload, format="json")

        item = Item.objects.get(media_id="550", media_type=MediaTypes.MOVIE.value)
        self.assertEqual(liked.status_code, status.HTTP_200_OK)
        self.assertTrue(liked.data["liked"])
        self.assertEqual(listed.status_code, status.HTTP_200_OK)
        self.assertEqual(listed.data["count"], 1)
        self.assertEqual(listed.data["results"][0]["title"], "Fight Club")
        self.assertEqual(unliked.status_code, status.HTTP_200_OK)
        self.assertFalse(unliked.data["liked"])
        self.assertFalse(MediaLike.objects.filter(user=user, item=item).exists())
        self.assertFalse(Movie.objects.filter(user=user, item=item).exists())
        self.assertFalse(DiaryEntry.objects.filter(user=user, item=item).exists())

    def test_liked_media_list_paginates_and_filters_media_type(self):
        user = get_user_model().objects.create_user(username="liked-pages", password="strong-password-123")
        movie_items = self._create_movie_items(30, title_prefix="Liked", media_id_prefix="liked")
        book = Item.objects.create(
            source=Sources.HARDCOVER.value,
            media_type=MediaTypes.BOOK.value,
            media_id="book-liked",
            title="Liked Book",
        )
        for item in [*movie_items, book]:
            MediaLike.objects.create(user=user, item=item)
        self.client.force_authenticate(user)

        response = self.client.get("/api/v1/me/liked-media/", {"media_type": MediaTypes.MOVIE.value})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 30)
        self.assertEqual(len(response.data["results"]), 25)
        self.assertIsNotNone(response.data["next"])

    @patch("api.services.diary.provider_services.get_media_metadata")
    def test_diary_liked_syncs_canonical_media_like(self, metadata_mock):
        user = get_user_model().objects.create_user(username="diary-media-like", password="strong-password-123")
        self.client.force_authenticate(user)
        metadata_mock.return_value = {"title": "Diary Like", "image": "https://example.com/diary.jpg"}
        payload = {
            "ref": {
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "media_id": "diary-like",
            },
            "liked": True,
        }

        created = self.client.post("/api/v1/diary/", payload, format="json")
        item = Item.objects.get(media_id="diary-like", media_type=MediaTypes.MOVIE.value)
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        self.assertTrue(MediaLike.objects.filter(user=user, item=item).exists())

        patched_without_like = self.client.patch(
            f"/api/v1/diary/{created.data['id']}/",
            {"review": "still liked"},
            format="json",
        )
        self.assertEqual(patched_without_like.status_code, status.HTTP_200_OK)
        self.assertTrue(MediaLike.objects.filter(user=user, item=item).exists())

        patched_unliked = self.client.patch(
            f"/api/v1/diary/{created.data['id']}/",
            {"liked": False},
            format="json",
        )
        self.assertEqual(patched_unliked.status_code, status.HTTP_200_OK)
        self.assertFalse(MediaLike.objects.filter(user=user, item=item).exists())

    @patch("app.providers.tmdb.get_title_logo", return_value=None)
    @patch("app.providers.mdblist.get_media_ratings", return_value={})
    @patch("api.services.media.provider_services.get_media_metadata")
    def test_media_detail_includes_canonical_like_state(self, metadata_mock, _ratings_mock, _logo_mock):
        user = get_user_model().objects.create_user(username="liked-detail", password="strong-password-123")
        item = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="detail-liked",
            title="Detail Like",
            image="https://example.com/detail.jpg",
        )
        MediaLike.objects.create(user=user, item=item)
        metadata_mock.return_value = {
            "media_id": "detail-liked",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "title": "Detail Like",
            "image": "https://example.com/detail.jpg",
        }
        self.client.force_authenticate(user)

        response = self.client.get("/api/v1/media/tmdb/movie/detail-liked/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["user_state"]["has_liked"])
        self.assertEqual(response.data["community"]["liked_count"], 1)

    def _create_movies(self, user, count):
        items = self._create_movie_items(count)
        Movie.objects.bulk_create([Movie(user=user, item=item, status=Status.COMPLETED.value) for item in items])
        return items

    def _create_movie_items(self, count, title_prefix="Movie", media_id_prefix="m"):
        return [
            Item.objects.create(
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                media_id=f"{media_id_prefix}{index}",
                title=f"{title_prefix} {index:02d}",
            )
            for index in range(count)
        ]

    @patch("api.views.profile.provider_services.get_media_metadata")
    def test_me_hof_put_materializes_missing_item(self, metadata_mock):
        user = get_user_model().objects.create_user(username="hof", password="strong-password-123")
        self.client.force_authenticate(user)
        metadata_mock.return_value = {
            "title": "Fight Club",
            "image": "https://example.com/fight-club.jpg",
        }

        response = self.client.put(
            "/api/v1/me/hof/movie/",
            {
                "ref": {
                    "item_id": None,
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "550",
                    "season_number": None,
                    "episode_number": None,
                },
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        item = Item.objects.get(source=Sources.TMDB.value, media_type=MediaTypes.MOVIE.value, media_id="550")
        self.assertEqual(user.__class__.objects.get(id=user.id).hof_movie, item)
        self.assertEqual(set(response.data["items"]), {"tv", "movie", "anime", "manga", "game", "book", "comic"})
        self.assertEqual(response.data["items"]["movie"]["ref"]["item_id"], item.id)
        self.assertEqual(response.data["items"]["movie"]["ref"]["media_type"], "movie")
        self.assertEqual(response.data["items"]["movie"]["title"], "Fight Club")
        self.assertEqual(response.data["items"]["movie"]["image_url"], "https://example.com/fight-club.jpg")
        self.assertEqual(response.data["items"]["movie"]["poster_url"], "https://example.com/fight-club.jpg")

    def test_me_hof_put_updates_existing_slot(self):
        user = get_user_model().objects.create_user(username="hof2", password="strong-password-123")
        first = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="550",
            title="Fight Club",
        )
        second = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="680",
            title="Pulp Fiction",
        )
        user.set_hall_of_fame_item(MediaTypes.MOVIE.value, first)
        user.save(update_fields=["hof_movie"])
        self.client.force_authenticate(user)

        response = self.client.put(
            "/api/v1/me/hof/movie/",
            {
                "ref": {
                    "item_id": second.id,
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "680",
                    "season_number": None,
                    "episode_number": None,
                },
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        self.assertEqual(user.hof_movie, second)
        self.assertEqual(response.data["items"]["movie"]["title"], "Pulp Fiction")

    def test_me_hof_delete_clears_slot(self):
        user = get_user_model().objects.create_user(username="hof3", password="strong-password-123")
        item = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            media_id="1399",
            title="Game of Thrones",
        )
        user.set_hall_of_fame_item(MediaTypes.TV.value, item)
        user.save(update_fields=["hof_tv"])
        self.client.force_authenticate(user)

        response = self.client.delete("/api/v1/me/hof/tv/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        self.assertIsNone(user.hof_tv)
        self.assertIsNone(response.data["items"]["tv"])

    def test_me_hof_put_rejects_invalid_and_mismatched_media_type(self):
        user = get_user_model().objects.create_user(username="hof4", password="strong-password-123")
        self.client.force_authenticate(user)

        invalid = self.client.put(
            "/api/v1/me/hof/season/",
            {
                "ref": {
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.SEASON.value,
                    "media_id": "1399",
                    "season_number": 1,
                },
            },
            format="json",
        )
        mismatch = self.client.put(
            "/api/v1/me/hof/movie/",
            {
                "ref": {
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.TV.value,
                    "media_id": "1399",
                },
            },
            format="json",
        )

        self.assertEqual(invalid.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(mismatch.status_code, status.HTTP_400_BAD_REQUEST)

    def test_me_hof_write_requires_auth(self):
        response = self.client.put(
            "/api/v1/me/hof/movie/",
            {
                "ref": {
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "550",
                },
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch("api.services.media.provider_services.search")
    def test_media_search_contract(self, search_mock):
        user = get_user_model().objects.create_user(
            username="searcher",
            password="strong-password-123",
        )
        item = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="550",
            title="Fight Club",
            image="https://example.com/fight-club.jpg",
        )
        CustomPosterPreference.objects.create(
            user=user,
            item=item,
            custom_image_url="https://example.com/custom-fight-club.jpg",
        )
        self.client.force_authenticate(user)
        search_mock.return_value = {
            "results": [
                {
                    "media_id": "550",
                    "title": "Fight Club",
                    "image": "https://example.com/fight-club.jpg",
                    "poster_width": 500,
                    "poster_height": 750,
                    "backdrop_path": "/fight-club-backdrop.jpg",
                    "release_date": "1999-10-15",
                    "ratings_count": 1000,
                    "total_rating_count": 1000,
                },
            ],
        }

        response = self.client.get("/api/v1/media/search/?media_type=movie&q=fight")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["results"][0]["ref"]["source"], "tmdb")
        self.assertEqual(response.data["results"][0]["ref"]["media_type"], "movie")
        self.assertEqual(response.data["results"][0]["title"], "Fight Club")
        self.assertEqual(response.data["results"][0]["image_url"], response.data["results"][0]["poster_url"])
        self.assertEqual(response.data["results"][0]["custom_poster_url"], "https://example.com/custom-fight-club.jpg")
        self.assertEqual(response.data["results"][0]["poster_orientation"], "portrait")
        self.assertEqual(response.data["results"][0]["poster_aspect_ratio"], 0.667)
        self.assertEqual(
            response.data["results"][0]["backdrop_url"],
            "https://image.tmdb.org/t/p/original/fight-club-backdrop.jpg",
        )
        self.assertNotIn("ratings_count", response.data["results"][0])
        self.assertNotIn("total_rating_count", response.data["results"][0])

    @patch("api.services.media.provider_services.discover")
    def test_media_discover_movie_genre_year_contract(self, discover_mock):
        user = get_user_model().objects.create_user(username="discoverer", password="strong-password-123")
        self.client.force_authenticate(user)
        discover_mock.return_value = {
            "per_page": 20,
            "total_results": 1,
            "results": [
                {
                    "media_id": "550",
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "title": "Fight Club",
                    "image": "https://example.com/fight-club.jpg",
                    "release_date": "1999-10-15",
                    "vote_count": 1000,
                },
            ],
        }

        response = self.client.get("/api/v1/media/discover/?media_type=movie&genre=Drama&year=1999")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.assertIsNone(response.data["next"])
        self.assertEqual(response.data["results"][0]["ref"]["source"], Sources.TMDB.value)
        self.assertEqual(response.data["results"][0]["ref"]["media_type"], MediaTypes.MOVIE.value)
        self.assertEqual(response.data["results"][0]["title"], "Fight Club")
        discover_mock.assert_called_once_with(
            MediaTypes.MOVIE.value,
            source=Sources.TMDB.value,
            page=1,
            page_size=25,
            genre="Drama",
            year="1999",
            platform=None,
            sort="vote_count",
        )

    @patch("api.services.media.provider_services.discover")
    def test_media_discover_tv_uses_tmdb_summary_shape(self, discover_mock):
        user = get_user_model().objects.create_user(username="tv-discoverer", password="strong-password-123")
        self.client.force_authenticate(user)
        discover_mock.return_value = {
            "per_page": 20,
            "total_results": 1,
            "results": [
                {
                    "media_id": "1396",
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.TV.value,
                    "title": "Breaking Bad",
                    "image": "https://example.com/breaking-bad.jpg",
                    "release_date": "2008-01-20",
                    "vote_count": 2000,
                },
            ],
        }

        response = self.client.get("/api/v1/media/discover/?media_type=tv&genre=Drama&year=2008")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["results"][0]["ref"]["media_type"], MediaTypes.TV.value)
        self.assertEqual(response.data["results"][0]["title"], "Breaking Bad")
        discover_mock.assert_called_once()

    @patch("api.services.media.provider_services.discover")
    def test_media_discover_game_platform_filter(self, discover_mock):
        user = get_user_model().objects.create_user(username="game-discoverer", password="strong-password-123")
        self.client.force_authenticate(user)
        discover_mock.return_value = {
            "per_page": 10,
            "total_results": 30,
            "results": [
                {
                    "media_id": "1020",
                    "source": Sources.IGDB.value,
                    "media_type": MediaTypes.GAME.value,
                    "title": "Elden Ring",
                    "image": "https://example.com/elden-ring.jpg",
                    "release_date": "2022-02-25",
                    "total_rating_count": 3000,
                },
            ],
        }

        response = self.client.get(
            "/api/v1/media/discover/?media_type=game&platform=PlayStation%205&page_size=10",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 30)
        self.assertIsNotNone(response.data["next"])
        self.assertEqual(response.data["results"][0]["ref"]["source"], Sources.IGDB.value)
        discover_mock.assert_called_once_with(
            MediaTypes.GAME.value,
            source=Sources.IGDB.value,
            page=1,
            page_size=10,
            genre=None,
            year=None,
            platform="PlayStation 5",
            sort="vote_count",
        )

    def test_media_discover_validation_errors(self):
        user = get_user_model().objects.create_user(username="discover-errors", password="strong-password-123")
        self.client.force_authenticate(user)

        missing = self.client.get("/api/v1/media/discover/")
        invalid = self.client.get("/api/v1/media/discover/?media_type=movie&year=99&sort=recent&page=0")

        self.assertEqual(missing.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("media_type", missing.data)
        self.assertIn("non_field_errors", missing.data)
        self.assertEqual(invalid.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("year", invalid.data)
        self.assertIn("sort", invalid.data)
        self.assertIn("page", invalid.data)

    def test_media_discover_unsupported_media_type_fails_clearly(self):
        user = get_user_model().objects.create_user(username="unsupported-discover", password="strong-password-123")
        self.client.force_authenticate(user)

        response = self.client.get("/api/v1/media/discover/?media_type=anime&genre=Drama")

        self.assertEqual(response.status_code, status.HTTP_501_NOT_IMPLEMENTED)
        self.assertIn("Discovery is not supported", response.data["detail"])

    @patch("app.providers.tmdb.get_title_logo", return_value=None)
    @patch("app.providers.mdblist.get_media_ratings")
    @patch("api.services.media.provider_services.get_media_metadata")
    def test_media_detail_includes_synopsis_and_external_ratings(self, metadata_mock, ratings_mock, _logo_mock):
        metadata_mock.return_value = {
            "media_id": "550",
            "media_type": "movie",
            "source": "tmdb",
            "title": "Fight Club",
            "image": "https://example.com/fight-club.jpg",
            "backdrop_path": "/rr7E0NoGKxvbkb89eR1GwfoYjpA.jpg",
            "synopsis": "Soap, clubs, and insomnia.",
            "score": "8.4",
            "score_count": 1000,
            "genres": [{"name": "Drama"}, "Thriller"],
            "details": {"runtime": "2h 19m"},
            "cast": [{"person_id": 819, "name": "Edward Norton", "character": "Narrator", "image": "/ed.jpg"}],
            "crew": [{"person_id": 7467, "name": "David Fincher", "roles": ["Director"], "job": "Director"}],
            "related": {
                "Fight Club Collection": [
                    {
                        "media_id": "551",
                        "media_type": "movie",
                        "source": "tmdb",
                        "title": "Fight Club 2",
                        "image": "https://example.com/fight-club-2.jpg",
                    },
                ],
                "recommendations": [
                    {
                        "media_id": "680",
                        "media_type": "movie",
                        "source": "tmdb",
                        "title": "Pulp Fiction",
                        "image": "https://example.com/pulp.jpg",
                        "poster_width": 500,
                        "poster_height": 750,
                    },
                ],
                "seasons": [
                    {
                        "media_id": "550",
                        "media_type": "season",
                        "source": "tmdb",
                        "season_number": 1,
                        "title": "Season 1",
                    },
                ],
            },
        }
        ratings_mock.return_value = {
            "imdb": {"value": "8.8", "votes": 2300000},
            "letterboxd": {"value": "4.3", "votes": 500000},
            "tomatoes": {"value": "79%", "votes": 100},
        }
        user = get_user_model().objects.create_user(username="viewer", password="strong-password-123")
        self.client.force_authenticate(user)
        item = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="550",
            title="Fight Club",
        )
        consumed_at = timezone.now()
        diary_entry = DiaryEntry.objects.create(
            user=user,
            item=item,
            consumed_at=consumed_at,
            rating="10.0",
            visibility="public",
        )

        response = self.client.get("/api/v1/media/tmdb/movie/550/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["overview"], "Soap, clubs, and insomnia.")
        self.assertEqual(response.data["synopsis"], "Soap, clubs, and insomnia.")
        self.assertEqual(
            response.data["backdrop_url"],
            "https://image.tmdb.org/t/p/original/rr7E0NoGKxvbkb89eR1GwfoYjpA.jpg",
        )
        self.assertIsNone(response.data["custom_backdrop_url"])
        self.assertEqual(
            [(rating["source"], rating["value"]) for rating in response.data["external_ratings"]],
            [("TMDB", "8.4"), ("IMDb", "8.8"), ("Letterboxd", "4.3"), ("Rotten Tomatoes", "79%")],
        )
        self.assertEqual(response.data["details"]["genres"], ["Drama", "Thriller"])
        self.assertEqual(response.data["cast"][0]["name"], "Edward Norton")
        self.assertEqual(response.data["crew"][0]["role"], "Director")
        self.assertEqual(response.data["related_sections"][0]["id"], "collection")
        self.assertEqual(response.data["related_sections"][1]["items"][0]["title"], "Pulp Fiction")
        self.assertEqual(
            response.data["related_sections"][1]["items"][0]["image_url"],
            response.data["related_sections"][1]["items"][0]["poster_url"],
        )
        self.assertEqual(response.data["related_sections"][1]["items"][0]["poster_orientation"], "portrait")
        self.assertNotIn("seasons", [section["id"] for section in response.data["related_sections"]])
        self.assertEqual(response.data["user_state"]["diary_rating"], "10.0")
        self.assertEqual(response.data["user_state"]["diary_consumed_at"], consumed_at)
        self.assertEqual(response.data["user_state"]["diary_entry_id"], diary_entry.id)
        self.assertEqual(response.data["user_state"]["diary_count"], 1)

    @patch("api.services.media.provider_services.get_media_metadata")
    def test_landscape_only_artwork_marks_poster_orientation(self, metadata_mock):
        metadata_mock.return_value = {
            "media_id": "blue-lock-extra",
            "media_type": "anime",
            "source": "mal",
            "title": "Blue Lock Additional Time",
            "image": "https://example.com/banner.jpg",
            "poster_width": 1200,
            "poster_height": 675,
            "backdrop_url": "https://example.com/backdrop.jpg",
        }

        response = self.client.get("/api/v1/media/mal/anime/blue-lock-extra/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["image_url"], response.data["poster_url"])
        self.assertEqual(response.data["poster_orientation"], "landscape")
        self.assertEqual(response.data["poster_aspect_ratio"], 1.778)
        self.assertEqual(response.data["backdrop_url"], "https://example.com/backdrop.jpg")

    @patch("app.providers.tmdb.get_title_logo", return_value=None)
    @patch("app.providers.mdblist.get_media_ratings", return_value={})
    @patch("api.services.media.provider_services.get_media_metadata")
    def test_tv_detail_exposes_seasons(self, metadata_mock, _ratings_mock, _logo_mock):
        metadata_mock.return_value = {
            "media_id": "1399",
            "media_type": "tv",
            "source": "tmdb",
            "title": "Game of Thrones",
            "image": "https://example.com/got.jpg",
            "backdrop_path": "/9xxLWtnFxkpJ2h1uthpvCRK6vta.jpg",
            "related": {
                "seasons": [
                    {
                        "media_id": "1399",
                        "media_type": "season",
                        "source": "tmdb",
                        "season_number": 1,
                        "season_title": "Season 1",
                        "max_progress": 10,
                        "image": "https://example.com/s1.jpg",
                        "first_air_date": "2011-04-17",
                    },
                ],
            },
        }

        detail = self.client.get("/api/v1/media/tmdb/tv/1399/")
        seasons = self.client.get("/api/v1/media/tmdb/tv/1399/seasons/")

        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.data["seasons"][0]["title"], "Season 1")
        self.assertEqual(
            detail.data["backdrop_url"],
            "https://image.tmdb.org/t/p/original/9xxLWtnFxkpJ2h1uthpvCRK6vta.jpg",
        )
        self.assertEqual(seasons.data["seasons"], detail.data["seasons"])

    @patch("app.providers.tmdb.get_title_logo", return_value=None)
    @patch("app.providers.mdblist.get_media_ratings", return_value={})
    @patch("api.services.media.provider_services.get_media_metadata")
    def test_media_detail_backdrop_url_is_null_without_backdrop(self, metadata_mock, _ratings_mock, _logo_mock):
        metadata_mock.return_value = {
            "media_id": "550",
            "media_type": "movie",
            "source": "tmdb",
            "title": "Fight Club",
            "image": "https://example.com/fight-club.jpg",
        }

        response = self.client.get("/api/v1/media/tmdb/movie/550/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["backdrop_url"])
        self.assertIsNone(response.data["custom_backdrop_url"])

    @patch(
        "app.providers.tmdb.get_title_logo",
        return_value={
            "url": "https://image.tmdb.org/t/p/w500/logo.png",
            "width": 1493,
            "height": 482,
            "aspect_ratio": 3.1,
        },
    )
    @patch("app.providers.mdblist.get_media_ratings", return_value={})
    @patch("api.services.media.provider_services.get_media_metadata")
    def test_media_detail_includes_tmdb_logo_fields(self, metadata_mock, _ratings_mock, logo_mock):
        metadata_mock.return_value = {
            "media_id": "550",
            "media_type": "movie",
            "source": "tmdb",
            "title": "Fight Club",
            "image": "https://example.com/fight-club.jpg",
        }

        response = self.client.get("/api/v1/media/tmdb/movie/550/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["logo_url"], "https://image.tmdb.org/t/p/w500/logo.png")
        self.assertEqual(response.data["logo_width"], 1493)
        self.assertEqual(response.data["logo_height"], 482)
        self.assertEqual(response.data["logo_aspect_ratio"], 3.1)
        logo_mock.assert_called_once_with("550", "movie")

    @patch("app.providers.tmdb.get_title_logo")
    @patch("api.services.media.provider_services.get_media_metadata")
    def test_media_detail_logo_fields_are_null_when_unsupported(self, metadata_mock, logo_mock):
        metadata_mock.return_value = {
            "media_id": "1",
            "media_type": "anime",
            "source": "mal",
            "title": "Cowboy Bebop",
            "image": "https://example.com/bebop.jpg",
        }

        response = self.client.get("/api/v1/media/mal/anime/1/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["logo_url"])
        self.assertIsNone(response.data["logo_width"])
        logo_mock.assert_not_called()

    @patch("app.providers.mdblist.get_media_ratings", return_value={})
    @patch("api.services.media.provider_services.get_media_metadata")
    def test_season_detail_exposes_episodes_with_runtime_string(self, metadata_mock, _ratings_mock):
        metadata_mock.return_value = {
            "media_id": "1399",
            "media_type": "season",
            "source": "tmdb",
            "title": "Game of Thrones",
            "season_number": 1,
            "image": "https://example.com/s1.jpg",
            "episodes": [
                {
                    "episode_number": 1,
                    "name": "Winter Is Coming",
                    "overview": "The beginning.",
                    "air_date": "2011-04-17",
                    "runtime": 62,
                    "still_path": "/ep1.jpg",
                    "vote_average": 8.2,
                },
            ],
        }

        detail = self.client.get("/api/v1/media/tmdb/season/1399/?season_number=1")
        episodes = self.client.get("/api/v1/media/tmdb/tv/1399/seasons/1/episodes/")

        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.data["episodes"][0]["runtime"], "1h 2m")
        self.assertEqual(detail.data["episodes"][0]["image_role"], "still")
        self.assertEqual(episodes.data["episodes"], detail.data["episodes"])

    @patch("api.services.media.provider_services.get_media_metadata")
    def test_mal_anime_detail_exposes_genres_related_and_rating(self, metadata_mock):
        metadata_mock.return_value = {
            "media_id": "1",
            "media_type": "anime",
            "source": "mal",
            "title": "Cowboy Bebop",
            "image": "https://example.com/bebop.jpg",
            "genres": ["Action", {"name": "Sci-Fi"}],
            "score": "8.75",
            "score_count": 100,
            "related": {
                "related_anime": [
                    {"media_id": "5", "media_type": "anime", "source": "mal", "title": "Movie"},
                ],
                "recommendations": [
                    {"media_id": "6", "media_type": "anime", "source": "mal", "title": "Champloo"},
                ],
            },
        }

        response = self.client.get("/api/v1/media/mal/anime/1/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["details"]["genres"], ["Action", "Sci-Fi"])
        self.assertEqual([section["id"] for section in response.data["related_sections"]], ["related_anime", "recommendations"])
        self.assertEqual(response.data["external_ratings"][0]["source"], "MAL")

    @patch("api.services.media.provider_services.get_media_metadata")
    def test_openlibrary_book_detail_exposes_other_editions(self, metadata_mock):
        metadata_mock.return_value = {
            "media_id": "OL1M",
            "media_type": "book",
            "source": "openlibrary",
            "title": "A Book",
            "image": "https://example.com/book.jpg",
            "score": "4.1",
            "related": {
                "other_editions": [
                    {"media_id": "OL2M", "media_type": "book", "source": "openlibrary", "title": "Paperback"},
                ],
            },
        }

        response = self.client.get("/api/v1/media/openlibrary/book/OL1M/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["related_sections"][0]["id"], "other_editions")
        self.assertEqual(response.data["external_ratings"][0]["max_value"], "5")
        self.assertEqual(response.data["external_ratings"][0]["value"], "4.1")

    @patch("api.services.media.provider_services.get_media_metadata")
    def test_hardcover_book_detail_exposes_series_before_recommendations(self, metadata_mock):
        metadata_mock.return_value = {
            "media_id": "377193",
            "media_type": "book",
            "source": "hardcover",
            "title": "Harry Potter and the Sorcerer's Stone",
            "image": "https://example.com/hp1.jpg",
            "related": {
                "Harry Potter": [
                    {
                        "media_id": "377193",
                        "source": "hardcover",
                        "media_type": "book",
                        "title": "Harry Potter and the Sorcerer's Stone",
                        "image": "https://example.com/hp1.jpg",
                    },
                    {
                        "media_id": "377194",
                        "source": "hardcover",
                        "media_type": "book",
                        "title": "Harry Potter and the Chamber of Secrets",
                        "image": "https://example.com/hp2.jpg",
                    },
                ],
                "recommendations": [
                    {
                        "media_id": "1",
                        "source": "hardcover",
                        "media_type": "book",
                        "title": "A Recommendation",
                        "image": "https://example.com/rec.jpg",
                    },
                ],
            },
        }

        response = self.client.get("/api/v1/media/hardcover/book/377193/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["related_sections"][0]["id"], "series")
        self.assertEqual(response.data["related_sections"][0]["title"], "Harry Potter")
        self.assertEqual(response.data["related_sections"][0]["items"][1]["ref"]["media_id"], "377194")
        self.assertEqual(response.data["related_sections"][0]["items"][1]["title"], "Harry Potter and the Chamber of Secrets")
        self.assertEqual([section["id"] for section in response.data["related_sections"]], ["series", "recommendations"])

    @patch("api.services.media.provider_services.get_media_metadata")
    def test_hardcover_book_detail_omits_empty_series_section(self, metadata_mock):
        metadata_mock.return_value = {
            "media_id": "377193",
            "media_type": "book",
            "source": "hardcover",
            "title": "Standalone Book",
            "image": "https://example.com/book.jpg",
            "related": {
                "recommendations": [
                    {
                        "media_id": "1",
                        "source": "hardcover",
                        "media_type": "book",
                        "title": "A Recommendation",
                    },
                ],
            },
        }

        response = self.client.get("/api/v1/media/hardcover/book/377193/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([section["id"] for section in response.data["related_sections"]], ["recommendations"])

    @patch("api.services.media.provider_services.get_media_metadata")
    def test_hardcover_book_external_rating_uses_native_five_point_scale(self, metadata_mock):
        metadata_mock.return_value = {
            "media_id": "377193",
            "media_type": "book",
            "source": "hardcover",
            "title": "The Great Gatsby",
            "image": "https://example.com/gatsby.jpg",
            "score": 4.3,
            "score_count": 1234,
        }

        response = self.client.get("/api/v1/media/hardcover/book/377193/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rating = response.data["external_ratings"][0]
        self.assertEqual(rating["source"], "Hardcover")
        self.assertEqual(rating["value"], "4.3")
        self.assertEqual(rating["max_value"], "5")
        self.assertEqual(rating["vote_count"], 1234)

    @patch("api.services.media.provider_services.get_media_metadata")
    def test_game_detail_exposes_all_related_as_related_section(self, metadata_mock):
        metadata_mock.return_value = {
            "media_id": "1020",
            "media_type": "game",
            "source": "igdb",
            "title": "Space Game",
            "image": "https://example.com/space.jpg",
            "artworks": [{"image_id": "wide-art"}],
            "score": "92.7",
            "score_count": 5000,
            "related": {
                "dlcs": [
                    {
                        "media_id": "1022",
                        "media_type": "game",
                        "source": "igdb",
                        "title": "Space Game DLC",
                    },
                ],
                "all_related": [
                    {
                        "media_id": "1021",
                        "media_type": "game",
                        "source": "igdb",
                        "title": "Space Game 2",
                        "image": "https://example.com/space2.jpg",
                    },
                ],
            },
        }

        response = self.client.get("/api/v1/media/igdb/game/1020/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["related_sections"][0]["id"], "dlcs")
        self.assertEqual(response.data["related_sections"][1]["id"], "all_related")
        self.assertEqual(response.data["external_ratings"][0]["source"], "IGDB")
        self.assertEqual(response.data["external_ratings"][0]["value"], "92.7")
        self.assertEqual(response.data["external_ratings"][0]["max_value"], "100")
        self.assertEqual(response.data["external_ratings"][0]["vote_count"], 5000)
        self.assertEqual(
            response.data["backdrop_url"],
            "https://images.igdb.com/igdb/image/upload/t_original/wide-art.jpg",
        )

    def test_community_stats_include_truthful_rating_distribution(self):
        user = get_user_model().objects.create_user(username="rater", password="strong-password-123")
        other = get_user_model().objects.create_user(username="other", password="strong-password-123")
        item = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="550",
            title="Fight Club",
            image="https://example.com/fight-club.jpg",
        )
        now = timezone.now()
        DiaryEntry.objects.create(user=user, item=item, consumed_at=now, rating="8.0", visibility="public")
        DiaryEntry.objects.create(user=other, item=item, consumed_at=now, rating="8.0", visibility="followers")
        DiaryEntry.objects.create(user=other, item=item, consumed_at=now, rating="9.0", visibility="private")
        DiaryEntry.objects.create(user=other, item=item, consumed_at=now, rating=None, visibility="public")

        response = self.client.get("/api/v1/media/tmdb/movie/550/community/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["average_rating"], "8.00")
        self.assertEqual(response.data["rating_count"], 2)
        self.assertEqual(response.data["rating_distribution"], [{"rating": "8.0", "count": 2}])

    def test_media_reviews_endpoint_returns_public_review_cards(self):
        user = get_user_model().objects.create_user(username="reviewer", password="strong-password-123")
        item = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="550",
            title="Fight Club",
            image="https://example.com/fight-club.jpg",
        )
        DiaryEntry.objects.create(
            user=user,
            item=item,
            consumed_at=timezone.now(),
            rating="9.0",
            review="Sharp and strange.",
            review_title="Mayhem",
            visibility="public",
        )
        DiaryEntry.objects.create(
            user=user,
            item=item,
            consumed_at=timezone.now(),
            review="Hidden.",
            visibility="private",
        )

        response = self.client.get("/api/v1/media/tmdb/movie/550/reviews/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["review"], "Sharp and strange.")

    def test_diary_media_embed_includes_artwork_fields(self):
        user = get_user_model().objects.create_user(username="diary-art", password="strong-password-123")
        item = Item.objects.create(
            source=Sources.OPENLIBRARY.value,
            media_type=MediaTypes.BOOK.value,
            media_id="OL1M",
            title="A Book",
            image="https://example.com/book.jpg",
        )
        DiaryEntry.objects.create(user=user, item=item, consumed_at=timezone.now(), visibility="public")
        self.client.force_authenticate(user)

        response = self.client.get("/api/v1/diary/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        media = response.data["results"][0]["media"]
        self.assertEqual(media["image_url"], media["poster_url"])
        self.assertIsNone(media["backdrop_url"])
        self.assertEqual(media["poster_orientation"], "unknown")

    def test_diary_list_orders_by_consumed_at_not_import_creation_time(self):
        user = get_user_model().objects.create_user(username="diary-order", password="strong-password-123")
        recent_item = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="recent",
            title="Recent",
        )
        old_item = Item.objects.create(
            source=Sources.HARDCOVER.value,
            media_type=MediaTypes.BOOK.value,
            media_id="old",
            title="Old Import",
        )
        recent = DiaryEntry.objects.create(user=user, item=recent_item, consumed_at=datetime(2026, 6, 1, tzinfo=UTC))
        old_import = DiaryEntry.objects.create(user=user, item=old_item, consumed_at=datetime(2024, 1, 7, tzinfo=UTC))
        self.client.force_authenticate(user)

        response = self.client.get("/api/v1/diary/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([entry["id"] for entry in response.data["results"]], [recent.id, old_import.id])

    def test_diary_list_filters_by_multi_word_tag(self):
        user = get_user_model().objects.create_user(username="diary-tag", password="strong-password-123")
        theater_item = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="550",
            title="Theater Movie",
        )
        home_item = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="551",
            title="Home Movie",
        )
        theater_entry = DiaryEntry.objects.create(user=user, item=theater_item, consumed_at=timezone.now())
        home_entry = DiaryEntry.objects.create(user=user, item=home_item, consumed_at=timezone.now())
        update_diary_entry_tags(theater_entry, ["in theater"])
        update_diary_entry_tags(home_entry, ["at home"])
        self.client.force_authenticate(user)

        response = self.client.get("/api/v1/diary/", {"tag": "in theater"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], theater_entry.id)
        self.assertEqual(response.data["results"][0]["tags"], ["in theater"])

    def test_diary_profile_menu_filters_reviews_likes_and_my_tags(self):
        user = get_user_model().objects.create_user(username="profile-menu", password="strong-password-123")
        other = get_user_model().objects.create_user(username="other-tags", password="strong-password-123")
        reviewed_item = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="550",
            title="Reviewed",
        )
        plain_item = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="551",
            title="Plain",
        )
        other_item = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="552",
            title="Other",
        )
        reviewed = DiaryEntry.objects.create(
            user=user,
            item=reviewed_item,
            consumed_at=timezone.now(),
            review_title="Title only",
            liked=True,
        )
        plain = DiaryEntry.objects.create(user=user, item=plain_item, consumed_at=timezone.now())
        other_entry = DiaryEntry.objects.create(user=other, item=other_item, consumed_at=timezone.now())
        update_diary_entry_tags(reviewed, ["mine"])
        update_diary_entry_tags(plain, ["also mine"])
        update_diary_entry_tags(other_entry, ["not mine"])
        self.client.force_authenticate(user)

        reviews = self.client.get("/api/v1/diary/", {"has_review": "true"})
        likes = self.client.get("/api/v1/diary/", {"liked": "true"})
        tags = self.client.get("/api/v1/diary/tags/", {"mine": "true"})

        self.assertEqual(reviews.status_code, status.HTTP_200_OK)
        self.assertEqual([entry["id"] for entry in reviews.data["results"]], [reviewed.id])
        self.assertEqual(likes.status_code, status.HTTP_200_OK)
        self.assertEqual([entry["id"] for entry in likes.data["results"]], [reviewed.id])
        self.assertEqual(tags.status_code, status.HTTP_200_OK)
        self.assertEqual({tag["name"] for tag in tags.data["results"]}, {"mine", "also mine"})

    def test_diary_tags_all_returns_more_than_autocomplete_cap(self):
        user = get_user_model().objects.create_user(username="tagged", password="strong-password-123")
        self.client.force_authenticate(user)

        for index in range(11):
            item = Item.objects.create(
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                media_id=str(8000 + index),
                title=f"Tagged {index}",
            )
            entry = DiaryEntry.objects.create(user=user, item=item, consumed_at=timezone.now())
            update_diary_entry_tags(entry, [f"tag-{index:02d}"])

        capped = self.client.get("/api/v1/diary/tags/", {"mine": "true"})
        all_tags = self.client.get("/api/v1/diary/tags/", {"mine": "true", "all": "true"})

        self.assertEqual(capped.status_code, status.HTTP_200_OK)
        self.assertEqual(all_tags.status_code, status.HTTP_200_OK)
        self.assertEqual(len(capped.data["results"]), 10)
        self.assertEqual(len(all_tags.data["results"]), 11)

    @patch("app.providers.tmdb.get_poster_images")
    def test_media_posters_endpoint_requires_auth_and_returns_original_first(self, posters_mock):
        user = get_user_model().objects.create_user(username="poster", password="strong-password-123")
        Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="550",
            title="Fight Club",
            image="https://example.com/original.jpg",
        )
        posters_mock.return_value = [
            {
                "url": "https://example.com/high.jpg",
                "thumbnail_url": "https://example.com/high-thumb.jpg",
                "width": 1000,
                "height": 1500,
                "aspect_ratio": 0.667,
                "vote_average": 8.5,
                "vote_count": 20,
                "language": "en",
            },
            {
                "url": "https://example.com/low.jpg",
                "thumbnail_url": "https://example.com/low-thumb.jpg",
                "width": 1000,
                "height": 1500,
                "aspect_ratio": 0.667,
                "vote_average": 7.0,
                "vote_count": 10,
                "language": None,
            },
        ]

        anonymous = self.client.get("/api/v1/media/tmdb/movie/550/posters/")
        self.client.force_authenticate(user)
        response = self.client.get("/api/v1/media/tmdb/movie/550/posters/")

        self.assertEqual(anonymous.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [poster["url"] for poster in response.data["posters"]],
            [
                "https://example.com/original.jpg",
                "https://example.com/high.jpg",
                "https://example.com/low.jpg",
            ],
        )
        self.assertTrue(response.data["posters"][0]["is_original"])

    @patch("api.services.media.provider_services.get_media_metadata")
    def test_media_book_posters_endpoint_returns_original_and_alternates(self, metadata_mock):
        user = get_user_model().objects.create_user(username="bookposter", password="strong-password-123")
        item = Item.objects.create(
            source=Sources.OPENLIBRARY.value,
            media_type=MediaTypes.BOOK.value,
            media_id="OL7353617M",
            title="The Hobbit",
            image="https://example.com/original-book.jpg",
        )
        CustomPosterPreference.objects.create(
            user=user,
            item=item,
            custom_image_url="https://example.com/alt-book.jpg",
        )
        metadata_mock.return_value = {"details": {"isbn": ["9780547928227"]}}

        async def reliable_covers(*_args, **_kwargs):
            return [
                {"url": "https://example.com/original-book.jpg"},
                {
                    "url": "https://example.com/alt-book.jpg",
                    "thumbnail_url": "https://example.com/alt-book-thumb.jpg",
                    "width": 1000,
                    "height": 1500,
                    "aspect_ratio": 0.667,
                    "language": None,
                },
            ]

        self.client.force_authenticate(user)
        with patch("app.providers.openlibrary.get_reliable_covers_for_book", reliable_covers):
            response = self.client.get("/api/v1/media/openlibrary/book/OL7353617M/posters/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [poster["url"] for poster in response.data["posters"]],
            ["https://example.com/original-book.jpg", "https://example.com/alt-book.jpg"],
        )
        self.assertTrue(response.data["posters"][0]["is_original"])
        self.assertFalse(response.data["posters"][1]["is_original"])
        self.assertFalse(response.data["posters"][0]["is_selected"])
        self.assertTrue(response.data["posters"][1]["is_selected"])

    def test_media_posters_endpoint_rejects_unsupported_media(self):
        user = get_user_model().objects.create_user(username="poster2", password="strong-password-123")
        self.client.force_authenticate(user)

        response = self.client.get("/api/v1/media/mal/anime/1/posters/")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("api.services.media.build_accent_palette", return_value={"accent": "#123456", "contrast": "#ffffff"})
    @patch("api.services.media.compute_and_store_poster_accent", return_value="#123456")
    def test_media_poster_save_updates_preference_and_item(self, _accent_mock, _palette_mock):
        user = get_user_model().objects.create_user(username="poster3", password="strong-password-123")
        item = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            media_id="1399",
            title="Game of Thrones",
            image="https://example.com/original.jpg",
        )
        self.client.force_authenticate(user)

        response = self.client.put(
            "/api/v1/media/tmdb/tv/1399/poster/",
            {"poster_url": "https://example.com/new.jpg"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["custom_poster_url"], "https://example.com/new.jpg")
        item.refresh_from_db()
        self.assertEqual(item.image, "https://example.com/new.jpg")
        self.assertEqual(item.poster_accent_color, "#123456")
        self.assertEqual(
            CustomPosterPreference.objects.get(user=user, item=item).custom_image_url,
            "https://example.com/new.jpg",
        )

    @patch("api.services.media.build_accent_palette", return_value={"accent": "#654321", "contrast": "#ffffff"})
    @patch("api.services.media.compute_and_store_poster_accent", return_value="#654321")
    def test_media_book_poster_save_updates_preference_and_item(self, _accent_mock, _palette_mock):
        user = get_user_model().objects.create_user(username="bookposter2", password="strong-password-123")
        item = Item.objects.create(
            source=Sources.OPENLIBRARY.value,
            media_type=MediaTypes.BOOK.value,
            media_id="OL7353617M",
            title="The Hobbit",
            image="https://example.com/original-book.jpg",
        )
        self.client.force_authenticate(user)

        response = self.client.put(
            "/api/v1/media/openlibrary/book/OL7353617M/poster/",
            {"poster_url": "https://example.com/new-book.jpg"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["custom_poster_url"], "https://example.com/new-book.jpg")
        item.refresh_from_db()
        self.assertEqual(item.image, "https://example.com/new-book.jpg")
        self.assertEqual(item.poster_accent_color, "#654321")
        self.assertEqual(
            CustomPosterPreference.objects.get(user=user, item=item).custom_image_url,
            "https://example.com/new-book.jpg",
        )

    @patch("api.services.media.provider_services.get_media_metadata")
    @patch("app.providers.tmdb.get_backdrop_images")
    def test_media_backdrops_endpoint_requires_auth_and_returns_original_first(self, backdrops_mock, metadata_mock):
        user = get_user_model().objects.create_user(username="backdrop", password="strong-password-123")
        item = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="550",
            title="Fight Club",
            image="https://example.com/poster.jpg",
        )
        CustomBackdropPreference.objects.create(
            user=user,
            item=item,
            custom_image_url="https://example.com/high.jpg",
        )
        metadata_mock.return_value = {
            "title": "Fight Club",
            "image": "https://example.com/poster.jpg",
            "backdrop_path": "/original.jpg",
        }
        backdrops_mock.return_value = [
            {
                "url": "https://example.com/high.jpg",
                "thumbnail_url": "https://example.com/high-thumb.jpg",
                "width": 1920,
                "height": 1080,
                "aspect_ratio": 1.778,
                "vote_average": 8.5,
                "vote_count": 20,
                "language": "en",
            },
        ]

        anonymous = self.client.get("/api/v1/media/tmdb/movie/550/backdrops/")
        self.client.force_authenticate(user)
        response = self.client.get("/api/v1/media/tmdb/movie/550/backdrops/")

        self.assertEqual(anonymous.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [backdrop["url"] for backdrop in response.data["backdrops"]],
            [
                "https://image.tmdb.org/t/p/original/original.jpg",
                "https://example.com/high.jpg",
            ],
        )
        self.assertTrue(response.data["backdrops"][0]["is_original"])
        self.assertFalse(response.data["backdrops"][0]["is_selected"])
        self.assertTrue(response.data["backdrops"][1]["is_selected"])

    def test_media_backdrops_endpoint_rejects_unsupported_media(self):
        user = get_user_model().objects.create_user(username="backdrop2", password="strong-password-123")
        self.client.force_authenticate(user)

        response = self.client.get("/api/v1/media/mal/anime/1/backdrops/")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_media_backdrop_save_updates_preference_without_changing_item_image(self):
        user = get_user_model().objects.create_user(username="backdrop3", password="strong-password-123")
        item = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            media_id="1399",
            title="Game of Thrones",
            image="https://example.com/original-poster.jpg",
        )
        self.client.force_authenticate(user)

        response = self.client.put(
            "/api/v1/media/tmdb/tv/1399/backdrop/",
            {"backdrop_url": "https://example.com/new-backdrop.jpg"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["custom_backdrop_url"], "https://example.com/new-backdrop.jpg")
        item.refresh_from_db()
        self.assertEqual(item.image, "https://example.com/original-poster.jpg")
        self.assertEqual(
            CustomBackdropPreference.objects.get(user=user, item=item).custom_image_url,
            "https://example.com/new-backdrop.jpg",
        )

    @patch("app.providers.tmdb.get_title_logo", return_value=None)
    @patch("app.providers.mdblist.get_media_ratings", return_value={})
    @patch("api.services.media.provider_services.get_media_metadata")
    def test_media_detail_includes_custom_backdrop_url_when_preference_exists(self, metadata_mock, _ratings_mock, _logo_mock):
        user = get_user_model().objects.create_user(username="backdrop4", password="strong-password-123")
        item = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="550",
            title="Fight Club",
            image="https://example.com/poster.jpg",
        )
        CustomBackdropPreference.objects.create(
            user=user,
            item=item,
            custom_image_url="https://example.com/custom-backdrop.jpg",
        )
        metadata_mock.return_value = {
            "media_id": "550",
            "media_type": "movie",
            "source": "tmdb",
            "title": "Fight Club",
            "image": "https://example.com/poster.jpg",
            "backdrop_path": "/default-backdrop.jpg",
        }
        self.client.force_authenticate(user)

        response = self.client.get("/api/v1/media/tmdb/movie/550/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["backdrop_url"], "https://image.tmdb.org/t/p/original/default-backdrop.jpg")
        self.assertEqual(response.data["custom_backdrop_url"], "https://example.com/custom-backdrop.jpg")
