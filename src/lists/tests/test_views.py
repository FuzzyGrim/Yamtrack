import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import OperationalError
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import (
    TV,
    Album,
    AlbumTracker,
    Anime,
    Artist,
    Episode,
    Game,
    Item,
    MediaTypes,
    Movie,
    PodcastShow,
    Season,
    Sources,
    Status,
    Video,
)
from app.templatetags.app_tags import item_slot_id
from app.tests.card_props import card_models
from lists import smart_rules
from lists.feeds import FloppyRssFeed
from lists.models import CustomList, CustomListItem, ListActivity, ListRecommendation
from lists.views_recommendations import get_or_create_recommendation_item
from users.models import DateFormatChoices


class ListsViewTests(TestCase):
    """Tests for the lists view."""

    def setUp(self):
        """Set up test data for lists view tests."""
        self.factory = RequestFactory()
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        self.collaborator_credentials = {
            "username": "collaborator",
            "password": "12345",
        }
        self.collaborator = get_user_model().objects.create_user(
            **self.collaborator_credentials,
        )

        # Create some test lists
        self.list1 = CustomList.objects.create(
            name="Test List 1",
            description="Description 1",
            owner=self.user,
        )
        self.list2 = CustomList.objects.create(
            name="Test List 2",
            description="Description 2",
            owner=self.user,
        )

        # Add collaborator to one list
        self.list1.collaborators.add(self.collaborator)

        # Create some items
        self.item1 = Item.objects.create(
            media_id="1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
        )
        self.item2 = Item.objects.create(
            media_id="2",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test TV Show",
        )

        # Add items to lists
        CustomListItem.objects.create(
            custom_list=self.list1,
            item=self.item1,
        )
        CustomListItem.objects.create(
            custom_list=self.list2,
            item=self.item2,
        )

    def test_lists_owner_view(self):
        """Test the lists view response and context for owner."""
        self.client.login(**self.credentials)
        response = self.client.get(reverse("lists"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "lists/custom_lists.html")
        self.assertIn("custom_lists", response.context)
        self.assertIn("form", response.context)

    def test_list_detail_prefills_release_year_for_known_dates(self):
        """List detail should render known release years without waiting for async fetches."""
        self.client.login(**self.credentials)
        self.item1.release_datetime = datetime(2024, 4, 5, 12, 0, tzinfo=UTC)
        self.item1.save(update_fields=["release_datetime"])

        response = self.client.get(
            reverse("list_detail", args=[self.list1.public_reference]),
        )

        self.assertEqual(response.status_code, 200)
        first_item = response.context["items"].object_list[0]
        self.assertEqual(first_item.id, self.item1.id)
        self.assertEqual(first_item.display_release_year, 2024)
        self.assertContains(response, "2024", html=False)

    def test_lists_collaborator_view(self):
        """Test the lists view response and context for a collaborator."""
        self.client.login(**self.collaborator_credentials)
        response = self.client.get(reverse("lists"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "lists/custom_lists.html")
        self.assertIn("custom_lists", response.context)
        self.assertIn("form", response.context)

    def test_cards_do_not_materialize_memberships(self):
        """Card summaries stay in SQL, including watched sorting and completion."""
        self.client.force_login(self.user)
        Movie.objects.bulk_create(
            [
                Movie(
                    item=self.item1,
                    user=self.user,
                    status=Status.COMPLETED.value,
                    end_date=timezone.now(),
                ),
            ]
        )
        for sort in ("name", "last_watched"):
            with (
                self.subTest(sort=sort),
                patch.object(
                    CustomListItem,
                    "from_db",
                    wraps=CustomListItem.from_db,
                ) as load_membership,
            ):
                response = self.client.get(reverse("lists"), {"sort": sort})
                self.assertEqual(response.status_code, 200)
                cards = {card.id: card for card in response.context["custom_lists"]}
                self.assertEqual(cards[self.list1.id].completed_count, 1)
                self.assertEqual(cards[self.list1.id].completion_percent, 100)
                load_membership.assert_not_called()

    def test_last_watched_paginated_before_card_hydration(self):
        """Only one page of full list rows is loaded even for a Python sort."""
        CustomList.objects.bulk_create(
            [
                CustomList(owner=self.user, name=f"Extra {index:03}")
                for index in range(30)
            ]
        )
        self.client.force_login(self.user)
        with patch.object(CustomList, "from_db", wraps=CustomList.from_db) as load:
            response = self.client.get(reverse("lists"), {"sort": "last_watched"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["custom_lists"]), 20)
        self.assertEqual(response.context["custom_lists"].paginator.count, 32)
        self.assertEqual(load.call_count, 20)

    def test_cover_loads_only_first_membership(self):
        """The cover endpoint must not prefetch the entire list."""
        cache.clear()
        CustomListItem.objects.create(
            custom_list=self.list1,
            item=self.item2,
            added_by=self.user,
        )
        self.client.force_login(self.user)
        with (
            patch.object(
                CustomListItem,
                "from_db",
                wraps=CustomListItem.from_db,
            ) as load,
            patch.object(CustomList, "_get_tmdb_backdrop", return_value=None),
        ):
            response = self.client.get(
                reverse("list_cover_image", args=[self.list1.id])
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(load.call_count, 1)
        self.assertContains(response, self.item1.image)

    def test_warm_covers_render_without_per_card_requests(self):
        """A repeat hub visit has the resolved URL without another provider call."""
        cache.clear()
        self.client.force_login(self.user)
        cover = "https://image.tmdb.org/t/p/w1280/cover.jpg"
        with patch.object(
            CustomList, "_get_tmdb_backdrop", return_value=cover
        ) as fetch:
            self.client.get(reverse("list_cover_image", args=[self.list1.id]))
            response = self.client.get(reverse("lists"))
            self.client.get(reverse("list_cover_image", args=[self.list1.id]))
        fetch.assert_called_once()
        self.assertContains(response, f'src="{cover}"')
        self.assertNotContains(
            response, f'hx-get="{reverse("list_cover_image", args=[self.list1.id])}"'
        )

    def test_cover_cache_changes_with_first_item_or_artwork(self):
        """Removing the first member or replacing its image cannot reuse its cover."""
        cache.clear()
        self.client.force_login(self.user)
        with patch.object(CustomList, "_get_tmdb_backdrop", return_value=None):
            self.client.get(reverse("list_cover_image", args=[self.list1.id]))
        self.item1.image = "https://example.com/replacement.jpg"
        self.item1.save(update_fields=["image"])
        response = self.client.get(reverse("lists"))
        self.assertContains(
            response, f'hx-get="{reverse("list_cover_image", args=[self.list1.id])}"'
        )
        self.list1.items.clear()
        self.list1.items.add(self.item2)
        response = self.client.get(reverse("lists"))
        self.assertContains(
            response, f'hx-get="{reverse("list_cover_image", args=[self.list1.id])}"'
        )

    def test_cached_cover_still_checks_visibility(self):
        """A resolved private cover does not make the fragment public."""
        cache.clear()
        self.client.force_login(self.user)
        with patch.object(CustomList, "_get_tmdb_backdrop", return_value=None):
            self.client.get(reverse("list_cover_image", args=[self.list1.id]))
        self.client.logout()
        response = self.client.get(reverse("list_cover_image", args=[self.list1.id]))
        self.assertEqual(response.status_code, 403)

    @patch.object(get_user_model(), "update_preference")
    def test_lists_view_search_filter(self, mock_update_preference):
        """Test the lists view with search filter."""
        mock_update_preference.return_value = "name"
        self.client.login(**self.credentials)

        # Test search by name
        response = self.client.get(reverse("lists") + "?q=List 1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["custom_lists"]), 1)
        self.assertEqual(response.context["custom_lists"][0].name, "Test List 1")

        # Test search by description
        response = self.client.get(reverse("lists") + "?q=Description 2")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["custom_lists"]), 1)
        self.assertEqual(response.context["custom_lists"][0].name, "Test List 2")

    @patch.object(get_user_model(), "update_preference")
    def test_lists_view_sorting(self, mock_update_preference):
        """Test the lists view with different sorting options."""
        self.client.login(**self.credentials)

        # Test name sorting
        mock_update_preference.return_value = "name"
        response = self.client.get(reverse("lists") + "?sort=name")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "name")
        self.assertEqual(response.context["current_direction"], "asc")

        # Test items_count sorting
        mock_update_preference.return_value = "items_count"
        response = self.client.get(reverse("lists") + "?sort=items_count")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "items_count")
        self.assertEqual(response.context["current_direction"], "desc")

        # Test newest_first sorting
        mock_update_preference.return_value = "newest_first"
        response = self.client.get(reverse("lists") + "?sort=newest_first")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "newest_first")
        self.assertEqual(response.context["current_direction"], "desc")

        # Test last_watched sorting
        mock_update_preference.return_value = "last_watched"
        response = self.client.get(reverse("lists") + "?sort=last_watched")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "last_watched")
        self.assertEqual(response.context["current_direction"], "desc")

        # Test default sorting (last_item_added)
        mock_update_preference.return_value = "last_item_added"
        response = self.client.get(reverse("lists"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "last_item_added")
        self.assertEqual(response.context["current_direction"], "desc")

    @patch.object(get_user_model(), "update_preference")
    def test_lists_view_name_sort_honors_direction(self, mock_update_preference):
        """Name sorting should flip ordering when direction changes."""
        mock_update_preference.return_value = "name"
        self.client.login(**self.credentials)

        asc_response = self.client.get(reverse("lists") + "?sort=name")
        self.assertEqual(asc_response.status_code, 200)
        self.assertEqual(asc_response.context["current_direction"], "asc")
        self.assertEqual(
            [custom_list.name for custom_list in asc_response.context["custom_lists"]],
            ["Test List 1", "Test List 2"],
        )

        desc_response = self.client.get(reverse("lists") + "?sort=name&direction=desc")
        self.assertEqual(desc_response.status_code, 200)
        self.assertEqual(desc_response.context["current_direction"], "desc")
        self.assertEqual(
            [custom_list.name for custom_list in desc_response.context["custom_lists"]],
            ["Test List 2", "Test List 1"],
        )

    @patch.object(get_user_model(), "update_preference")
    def test_lists_view_last_watched_sort_orders_by_latest_content_watch(
        self,
        mock_update_preference,
    ):
        """Hub sorting should use the latest watched date across each list's contents."""
        mock_update_preference.return_value = "last_watched"
        self.client.login(**self.credentials)
        self.user.date_format = DateFormatChoices.ISO_8601
        self.user.save(update_fields=["date_format"])

        older_watch = datetime(2026, 4, 5, 18, 0, tzinfo=UTC)
        newer_watch = datetime(2026, 4, 8, 18, 0, tzinfo=UTC)

        Movie.objects.bulk_create(
            [
                Movie(
                    item=self.item1,
                    user=self.user,
                    status=Status.COMPLETED.value,
                    end_date=older_watch,
                ),
            ],
        )

        tv = TV.objects.create(
            item=self.item2,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        season_item = Item.objects.create(
            media_id="2",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test TV Show",
            season_number=1,
            image="http://example.com/season.jpg",
        )
        episode_item = Item.objects.create(
            media_id="2",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Pilot",
            season_number=1,
            episode_number=1,
            image="http://example.com/episode.jpg",
        )
        season = Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=tv,
            status=Status.IN_PROGRESS.value,
        )
        Episode.objects.bulk_create(
            [
                Episode(
                    item=episode_item,
                    related_season=season,
                    end_date=newer_watch,
                ),
            ],
        )

        response = self.client.get(reverse("lists") + "?sort=last_watched")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_direction"], "desc")
        self.assertEqual(
            [custom_list.name for custom_list in response.context["custom_lists"]],
            ["Test List 2", "Test List 1"],
        )
        self.assertContains(
            response, timezone.localtime(newer_watch).strftime("%Y-%m-%d")
        )
        self.assertContains(
            response, timezone.localtime(older_watch).strftime("%Y-%m-%d")
        )

        asc_response = self.client.get(
            reverse("lists") + "?sort=last_watched&direction=asc"
        )

        self.assertEqual(asc_response.status_code, 200)
        self.assertEqual(asc_response.context["current_direction"], "asc")
        self.assertEqual(
            [custom_list.name for custom_list in asc_response.context["custom_lists"]],
            ["Test List 1", "Test List 2"],
        )

    @patch.object(get_user_model(), "update_preference")
    def test_lists_view_last_watched_htmx_request_shows_helper_dates(
        self,
        mock_update_preference,
    ):
        """HTMX list-grid refresh should render the last-watched helper row."""
        mock_update_preference.return_value = "last_watched"
        self.client.login(**self.credentials)
        self.user.date_format = DateFormatChoices.ISO_8601
        self.user.save(update_fields=["date_format"])

        watched_at = datetime(2026, 4, 8, 18, 0, tzinfo=UTC)

        Movie.objects.bulk_create(
            [
                Movie(
                    item=self.item1,
                    user=self.user,
                    status=Status.COMPLETED.value,
                    end_date=watched_at,
                ),
            ],
        )

        response = self.client.get(
            reverse("lists") + "?sort=last_watched",
            headers={"hx-request": "true"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "lists/components/list_grid.html")
        self.assertEqual(response.context["current_sort"], "last_watched")
        self.assertEqual(response.context["current_direction"], "desc")
        self.assertContains(
            response, timezone.localtime(watched_at).strftime("%Y-%m-%d")
        )
        self.assertContains(response, "No watched items")

    @patch.object(get_user_model(), "update_preference")
    def test_lists_view_htmx_request(self, mock_update_preference):
        """Test the lists view with HTMX request."""
        mock_update_preference.return_value = "name"
        self.client.login(**self.credentials)

        # Make an HTMX request
        response = self.client.get(reverse("lists"), headers={"hx-request": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "lists/components/list_grid.html")

        self.assertIn("custom_lists", response.context)
        self.assertEqual(response.context["current_direction"], "asc")

    @patch.object(get_user_model(), "update_preference")
    def test_lists_view_pagination(self, mock_update_preference):
        """Test the lists view pagination."""
        mock_update_preference.return_value = "name"
        self.client.login(**self.credentials)

        # Create more lists to test pagination
        for i in range(25):  # Create 25 more lists (27 total)
            CustomList.objects.create(
                name=f"Paginated List {i}",
                owner=self.user,
            )

        # Test first page
        response = self.client.get(reverse("lists"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["custom_lists"]), 20)  # 20 per page
        self.assertContains(
            response,
            'hx-trigger="revealed threshold:200px once"',
            html=False,
        )

        # Test second page
        response = self.client.get(reverse("lists") + "?page=2")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["custom_lists"]), 7)  # 7 remaining items

    @patch.object(get_user_model(), "update_preference")
    def test_lists_view_keeps_card_actions_in_options_menu(
        self,
        mock_update_preference,
    ):
        """List cards expose Settings and pinning through an options menu."""
        mock_update_preference.return_value = "name"
        self.client.login(**self.credentials)

        smart_list = CustomList.objects.create(
            name="Smart List",
            description="Automatic",
            owner=self.user,
            is_smart=True,
        )

        response = self.client.get(reverse("lists"))

        self.assertContains(response, 'aria-label="Options for ')
        self.assertContains(response, "Settings")
        self.assertNotContains(response, 'title="Edit list"')
        self.assertNotContains(response, reverse("list_add_item", args=[self.list1.id]))
        self.assertNotContains(
            response,
            f"{reverse('list_detail', args=[smart_list.id])}?edit_smart_rules=1",
        )
        self.assertContains(response, "Pin to sidebar")

    @patch.object(get_user_model(), "update_preference")
    def test_lists_view_marks_only_smart_lists_with_badge(
        self,
        mock_update_preference,
    ):
        """Smart list cards carry the bolt badge; manual list cards do not."""
        mock_update_preference.return_value = "name"
        self.client.login(**self.credentials)

        response = self.client.get(reverse("lists"))
        self.assertNotContains(response, 'aria-label="Smart List"')

        CustomList.objects.create(
            name="Smart List",
            owner=self.user,
            is_smart=True,
        )

        response = self.client.get(reverse("lists"))
        self.assertContains(response, 'aria-label="Smart List"', count=1)


class ListDetailViewTests(TestCase):
    """Tests for the list_detail view."""

    def setUp(self):
        """Set up test data."""
        self.factory = RequestFactory()
        self.credentials = {"username": "testuser", "password": "testpassword"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        self.other_credentials = {
            "username": "otheruser",
            "password": "testpassword",
        }
        self.other_user = get_user_model().objects.create_user(
            **self.other_credentials,
        )
        self.client.login(**self.credentials)

        # Create a test list
        self.custom_list = CustomList.objects.create(
            name="Test List",
            description="Test Description",
            owner=self.user,
        )

        # Create some items with different media types
        self.movie_item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
        )
        self.tv_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test TV Show",
        )
        self.anime_item = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Test Anime",
        )

        # Add items to the list
        CustomListItem.objects.create(
            custom_list=self.custom_list,
            item=self.movie_item,
        )
        CustomListItem.objects.create(
            custom_list=self.custom_list,
            item=self.tv_item,
        )
        CustomListItem.objects.create(
            custom_list=self.custom_list,
            item=self.anime_item,
        )

    def test_public_list_exposes_kometa_episode_identity(self):
        """Expose an episode-aware TVDB anchor without changing its visible link."""
        self.custom_list.visibility = "public"
        self.custom_list.save(update_fields=["visibility"])
        self.tv_item.provider_external_ids = {"tvdb_id": "81189"}
        self.tv_item.save(update_fields=["provider_external_ids"])
        episode_item = Item.objects.create(
            media_id=self.tv_item.media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Pilot",
            season_number=1,
            episode_number=2,
        )
        CustomListItem.objects.create(
            custom_list=self.custom_list,
            item=episode_item,
        )
        self.client.logout()

        response = self.client.get(reverse("list_detail", args=[self.custom_list.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "/details/tvdb/tv/81189/test-tv-show/season/1/episode/2",
        )
        self.assertContains(
            response,
            "/details/tmdb/tv/1668/pilot/season/1/episode/2",
        )

    def test_public_list_exposes_kometa_anchor_for_non_tmdb_episode(self):
        """Anime (non-TMDB) episode sources should also get a TVDB anchor."""
        self.custom_list.visibility = "public"
        self.custom_list.save(update_fields=["visibility"])
        self.anime_item.provider_external_ids = {"tvdb_id": "12345"}
        self.anime_item.save(update_fields=["provider_external_ids"])
        anime_episode_item = Item.objects.create(
            media_id=self.anime_item.media_id,
            source=Sources.MAL.value,
            media_type=MediaTypes.EPISODE.value,
            title="Episode 1",
            season_number=1,
            episode_number=1,
        )
        CustomListItem.objects.create(
            custom_list=self.custom_list,
            item=anime_episode_item,
        )
        self.client.logout()

        response = self.client.get(reverse("list_detail", args=[self.custom_list.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "/details/tvdb/tv/12345/test-anime/season/1/episode/1",
        )

    def test_public_list_table_layout_exposes_kometa_anchor(self):
        """Table layout must also emit the hidden Kometa episode anchor."""
        self.custom_list.visibility = "public"
        self.custom_list.save(update_fields=["visibility"])
        self.tv_item.provider_external_ids = {"tvdb_id": "81189"}
        self.tv_item.save(update_fields=["provider_external_ids"])
        episode_item = Item.objects.create(
            media_id=self.tv_item.media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Pilot",
            season_number=1,
            episode_number=2,
        )
        CustomListItem.objects.create(
            custom_list=self.custom_list,
            item=episode_item,
        )
        self.client.logout()

        response = self.client.get(
            reverse("list_detail", args=[self.custom_list.id]),
            {"layout": "table"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "/details/tvdb/tv/81189/test-tv-show/season/1/episode/2",
        )

    @patch("app.tasks_external_ids.enqueue_external_ids_backfill_items")
    def test_public_list_queues_backfill_when_tvdb_id_missing(self, mock_enqueue):
        """Missing TVDB id for a parent show should trigger a best-effort backfill."""
        self.custom_list.visibility = "public"
        self.custom_list.save(update_fields=["visibility"])
        episode_item = Item.objects.create(
            media_id=self.tv_item.media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Pilot",
            season_number=1,
            episode_number=2,
        )
        CustomListItem.objects.create(
            custom_list=self.custom_list,
            item=episode_item,
        )
        self.client.logout()

        response = self.client.get(reverse("list_detail", args=[self.custom_list.id]))

        self.assertEqual(response.status_code, 200)
        mock_enqueue.assert_called_once()
        (queued_ids,), _kwargs = mock_enqueue.call_args
        self.assertEqual(list(queued_ids), [self.tv_item.id])

    @patch.object(get_user_model(), "update_preference")
    @patch.object(CustomList, "user_can_view")
    def test_list_detail_view(
        self,
        mock_user_can_view,
        mock_update_preference,
    ):
        """Test the list_detail view."""
        mock_update_preference.side_effect = ["date_added", None]
        mock_user_can_view.return_value = True

        # Create Movie instance
        Movie.objects.create(
            item=self.movie_item,
            status=Status.COMPLETED.value,
            user=self.user,
        )

        # Create TV instance
        TV.objects.create(
            item=self.tv_item,
            status=Status.IN_PROGRESS.value,
            user=self.user,
        )

        # Create Anime instance
        Anime.objects.create(
            item=self.anime_item,
            status=Status.PLANNING.value,
            user=self.user,
        )

        # Test the view
        response = self.client.get(reverse("list_detail", args=[self.custom_list.id]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "lists/list_detail.html")

        # Check context data
        self.assertEqual(response.context["custom_list"], self.custom_list)
        self.assertEqual(len(response.context["items"]), 3)
        self.assertEqual(response.context["current_sort"], "date_added")
        self.assertEqual(response.context["items_count"], 3)

    @patch.object(get_user_model(), "update_preference")
    @patch.object(CustomList, "user_can_view")
    def test_list_detail_view_end_date_label_remains_end_date(
        self,
        mock_user_can_view,
        mock_update_preference,
    ):
        """List-detail end_date sort should still be labeled End Date."""
        mock_update_preference.side_effect = ["date_added", None]
        mock_user_can_view.return_value = True

        response = self.client.get(reverse("list_detail", args=[self.custom_list.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(dict(response.context["sort_choices"])["end_date"], "End Date")

    @patch.object(get_user_model(), "update_preference")
    @patch.object(CustomList, "user_can_view")
    def test_list_detail_view_unauthorized(
        self,
        mock_user_can_view,
        mock_update_preference,
    ):
        """Test the list_detail view when user is not authorized."""
        mock_update_preference.side_effect = ["date_added", None]
        mock_user_can_view.return_value = False

        response = self.client.get(reverse("list_detail", args=[self.custom_list.id]))
        self.assertEqual(response.status_code, 404)

    @patch.object(get_user_model(), "update_preference")
    @patch.object(CustomList, "user_can_view")
    def test_list_detail_view_filter_by_media_type(
        self,
        mock_user_can_view,
        mock_update_preference,
    ):
        """Test the list_detail view with media type filter."""
        mock_update_preference.side_effect = ["date_added", None]
        mock_user_can_view.return_value = True

        # Create model instances
        Movie.objects.create(
            item=self.movie_item,
            status=Status.COMPLETED.value,
            user=self.user,
        )

        TV.objects.create(
            item=self.tv_item,
            status=Status.IN_PROGRESS.value,
            user=self.user,
        )

        Anime.objects.create(
            item=self.anime_item,
            status=Status.PLANNING.value,
            user=self.user,
        )

        # Test the view with media type filter
        response = self.client.get(
            reverse("list_detail", args=[self.custom_list.id])
            + f"?type={MediaTypes.MOVIE.value}",
        )
        self.assertEqual(response.status_code, 200)

        # Should only have the movie item
        self.assertEqual(len(response.context["items"]), 1)
        self.assertEqual(
            response.context["items"][0].media_type,
            MediaTypes.MOVIE.value,
        )

    @patch.object(get_user_model(), "update_preference")
    @patch.object(CustomList, "user_can_view")
    def test_list_detail_view_filter_by_status(
        self,
        mock_user_can_view,
        mock_update_preference,
    ):
        """Test the list_detail view with status filter."""
        mock_update_preference.side_effect = [
            "date_added",
            Status.PLANNING.value,
            None,
        ]
        mock_user_can_view.return_value = True

        # Create model instances
        Movie.objects.create(
            item=self.movie_item,
            status=Status.COMPLETED.value,
            user=self.user,
        )

        TV.objects.create(
            item=self.tv_item,
            status=Status.IN_PROGRESS.value,
            user=self.user,
        )

        Anime.objects.create(
            item=self.anime_item,
            status=Status.PLANNING.value,
            user=self.user,
        )

        # Test the view with status filter
        response = self.client.get(
            reverse("list_detail", args=[self.custom_list.id])
            + f"?status={Status.PLANNING.value}",
        )
        self.assertEqual(response.status_code, 200)

        # Check that filters are applied
        self.assertEqual(
            response.context["current_statuses"],
            (Status.PLANNING.value,),
        )
        # Should only have the PLANNING item of media type ANIME
        self.assertEqual(len(response.context["items"]), 1)
        self.assertEqual(
            response.context["items"][0].media_type,
            MediaTypes.ANIME.value,
        )

    @patch.object(get_user_model(), "update_preference")
    @patch.object(CustomList, "user_can_view")
    def test_list_detail_view_filter_by_no_status(
        self,
        mock_user_can_view,
        mock_update_preference,
    ):
        """No Status includes untracked items and rows with a null status."""
        mock_update_preference.side_effect = ["date_added", None, "grid"]
        mock_user_can_view.return_value = True

        Movie.objects.create(
            item=self.movie_item,
            status=Status.COMPLETED.value,
            user=self.user,
        )
        TV.objects.create(item=self.tv_item, status=None, user=self.user)

        response = self.client.get(
            reverse("list_detail", args=[self.custom_list.id]) + "?status=no_status",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_statuses"], ("no_status",))
        self.assertEqual(
            {item.id for item in response.context["items"]},
            {self.tv_item.id, self.anime_item.id},
        )
        self.assertIn(("no_status", "No Status"), response.context["status_choices"])

    @patch.object(get_user_model(), "update_preference")
    @patch.object(CustomList, "user_can_view")
    def test_list_detail_view_sort_by_status(
        self,
        mock_user_can_view,
        mock_update_preference,
    ):
        """Status sort defaults to descending: Completed -> In progress -> Planning."""
        mock_update_preference.side_effect = ["status", None]
        mock_user_can_view.return_value = True

        Movie.objects.create(
            item=self.movie_item,
            status=Status.COMPLETED.value,
            user=self.user,
        )
        TV.objects.create(
            item=self.tv_item,
            status=Status.IN_PROGRESS.value,
            user=self.user,
        )
        Anime.objects.create(
            item=self.anime_item,
            status=Status.PLANNING.value,
            user=self.user,
        )

        response = self.client.get(
            reverse("list_detail", args=[self.custom_list.id]) + "?sort=status",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item.media_type for item in response.context["items"]],
            [
                MediaTypes.MOVIE.value,
                MediaTypes.TV.value,
                MediaTypes.ANIME.value,
            ],
        )

        # Ascending should reverse the order.
        mock_update_preference.side_effect = ["status", None]
        response = self.client.get(
            reverse("list_detail", args=[self.custom_list.id])
            + "?sort=status&direction=asc",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item.media_type for item in response.context["items"]],
            [
                MediaTypes.ANIME.value,
                MediaTypes.TV.value,
                MediaTypes.MOVIE.value,
            ],
        )

    @patch.object(CustomList, "user_can_view")
    def test_list_detail_view_status_filter_uses_owner_data_for_public_view(
        self,
        mock_user_can_view,
    ):
        """A public list's status filter must use the owner's status, not a viewer's."""
        mock_user_can_view.return_value = True
        self.custom_list.visibility = "public"
        self.custom_list.save(update_fields=["visibility"])

        # Owner has the movie marked Completed.
        Movie.objects.create(
            item=self.movie_item,
            status=Status.COMPLETED.value,
            user=self.user,
        )
        # A different (non-collaborator) user has the same movie marked Planning.
        Movie.objects.create(
            item=self.movie_item,
            status=Status.PLANNING.value,
            user=self.other_user,
        )

        self.client.logout()
        response = self.client.get(
            reverse("list_detail", args=[self.custom_list.id])
            + f"?status={Status.COMPLETED.value}",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["items"]), 1)
        self.assertEqual(
            response.context["items"][0].media_type,
            MediaTypes.MOVIE.value,
        )

        response = self.client.get(
            reverse("list_detail", args=[self.custom_list.id])
            + f"?status={Status.PLANNING.value}",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["items"]), 0)

    def test_list_detail_view_anonymous_public(self):
        """Ensure anonymous users can view public lists without preference errors."""
        self.custom_list.visibility = "public"
        self.custom_list.save(update_fields=["visibility"])

        self.client.logout()
        response = self.client.get(reverse("list_detail", args=[self.custom_list.id]))
        self.assertEqual(response.status_code, 200)

    @patch("app.providers.services.get_media_metadata")
    @patch.object(CustomList, "_get_tmdb_backdrop", return_value=None)
    def test_public_list_table_hides_and_shows_owner_notes(
        self,
        _mock_backdrop,
        mock_get_media_metadata,
    ):
        """Public table notes follow the originating list's Include Notes setting."""
        mock_get_media_metadata.return_value = {
            "max_progress": 1,
            "related": {"seasons": []},
            "title": "Test Movie",
        }
        Movie.objects.create(
            item=self.movie_item,
            status=Status.COMPLETED.value,
            user=self.user,
            notes="Owner-only public-list note",
        )
        self.custom_list.visibility = "public"
        self.custom_list.include_notes = False
        self.custom_list.save(update_fields=["visibility", "include_notes"])

        self.client.logout()
        url = reverse("list_detail", args=[self.custom_list.id]) + "?layout=table"
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["show_public_notes"])
        self.assertNotContains(response, "Owner-only public-list note")
        self.assertContains(
            response,
            f"public_view=1&amp;public_list={self.custom_list.id}",
            html=False,
        )

        self.custom_list.include_notes = True
        self.custom_list.save(update_fields=["include_notes"])
        response = self.client.get(url)

        self.assertTrue(response.context["show_public_notes"])
        self.assertContains(response, "Owner-only public-list note")

    @patch("app.providers.services.get_media_metadata")
    @patch.object(CustomList, "_get_tmdb_backdrop", return_value=None)
    def test_public_list_table_hides_owner_entry_source(
        self,
        _mock_backdrop,
        mock_get_media_metadata,
    ):
        """Anonymous and signed-in visitors never see the owner's entry source (issue #1258)."""
        mock_get_media_metadata.return_value = {
            "max_progress": 1,
            "related": {"seasons": []},
            "title": "Test Movie",
        }
        Movie.objects.create(
            item=self.movie_item,
            status=Status.COMPLETED.value,
            user=self.user,
            entry_source="plex",
        )
        url = reverse("list_detail", args=[self.custom_list.id]) + "?layout=table"

        # The owner sees the source.
        response = self.client.get(url)
        self.assertContains(response, ">Plex</div>")

        self.custom_list.visibility = "public"
        self.custom_list.save(update_fields=["visibility"])
        viewer = get_user_model().objects.create_user(
            username="source-viewer",
            password="12345",
        )
        for logged_in_viewer in (None, viewer):
            self.client.logout()
            if logged_in_viewer:
                self.client.force_login(logged_in_viewer)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, ">Plex</div>")

    @patch("app.providers.services.get_media_metadata")
    @patch.object(CustomList, "_get_tmdb_backdrop", return_value=None)
    def test_public_smart_list_table_hides_and_shows_owner_notes(
        self,
        _mock_backdrop,
        mock_get_media_metadata,
    ):
        """Public smart-list tables honor the same Include Notes setting."""
        mock_get_media_metadata.return_value = {
            "max_progress": 1,
            "related": {"seasons": []},
            "title": "Test Movie",
        }
        Movie.objects.create(
            item=self.movie_item,
            status=Status.COMPLETED.value,
            user=self.user,
            notes="Owner-only smart-list note",
        )
        smart_list = CustomList.objects.create(
            name="Public Smart List",
            owner=self.user,
            is_smart=True,
            visibility="public",
            include_notes=False,
            smart_media_types=[MediaTypes.MOVIE.value],
            smart_filters={"status": "all"},
        )

        self.client.logout()
        url = reverse("list_detail", args=[smart_list.id]) + "?layout=table"
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["show_public_notes"])
        self.assertNotContains(response, "Owner-only smart-list note")

        smart_list.include_notes = True
        smart_list.save(update_fields=["include_notes"])
        response = self.client.get(url)

        self.assertTrue(response.context["show_public_notes"])
        self.assertContains(response, "Owner-only smart-list note")

    def test_public_list_detail_reorders_header_and_removes_public_banner(self):
        """Public manual lists should show the owner on the metadata line without the banner."""
        self.custom_list.visibility = "public"
        self.custom_list.allow_recommendations = True
        self.custom_list.save(update_fields=["visibility", "allow_recommendations"])

        self.client.logout()
        response = self.client.get(reverse("list_detail", args=[self.custom_list.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "3 items")
        self.assertContains(response, self.user.username)
        self.assertNotContains(response, "View Profile")
        self.assertNotContains(response, "You're viewing a public list.")

        content = response.content.decode()
        self.assertIn(
            'class="inline-flex items-center text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-link-hover)]"',
            content,
        )
        self.assertLess(content.index("3 items"), content.index(self.user.username))
        self.assertLess(content.index(self.user.username), content.index("Links"))
        self.assertLess(content.index("Links"), content.index("Recommend Item"))
        self.assertLess(
            content.index("Recommend Item"), content.index("Test Description")
        )

    def test_public_list_detail_resolves_custom_slug(self):
        """Public lists should resolve their custom slug in the detail route."""
        self.custom_list.visibility = "public"
        self.custom_list.public_slug = "favorite-movies"
        self.custom_list.save(update_fields=["visibility", "public_slug"])

        self.client.logout()
        response = self.client.get(
            reverse("list_detail", args=[self.custom_list.public_slug])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["custom_list"], self.custom_list)

    @patch.object(get_user_model(), "update_preference")
    @patch.object(CustomList, "user_can_view")
    def test_list_detail_view_search(
        self,
        mock_user_can_view,
        mock_update_preference,
    ):
        """Test the list_detail view with search filter."""
        mock_update_preference.side_effect = ["date_added", None]
        mock_user_can_view.return_value = True

        # Create model instances
        Movie.objects.create(
            item=self.movie_item,
            status=Status.COMPLETED.value,
            user=self.user,
        )

        TV.objects.create(
            item=self.tv_item,
            status=Status.IN_PROGRESS.value,
            user=self.user,
        )

        Anime.objects.create(
            item=self.anime_item,
            status=Status.PLANNING.value,
            user=self.user,
        )

        # Test the view with search filter
        response = self.client.get(
            reverse("list_detail", args=[self.custom_list.id]) + "?q=Anime",
        )
        self.assertEqual(response.status_code, 200)

        # Should only have the anime item
        self.assertEqual(len(response.context["items"]), 1)
        self.assertEqual(response.context["items"][0].title, "Test Anime")

    @patch.object(get_user_model(), "update_preference")
    @patch.object(CustomList, "user_can_view")
    def test_list_detail_view_sorting(
        self,
        mock_user_can_view,
        mock_update_preference,
    ):
        """Test the list_detail view with different sorting options."""
        mock_user_can_view.return_value = True

        # Create model instances
        Movie.objects.create(
            item=self.movie_item,
            status=Status.COMPLETED.value,
            user=self.user,
        )

        TV.objects.create(
            item=self.tv_item,
            status=Status.IN_PROGRESS.value,
            user=self.user,
        )

        Anime.objects.create(
            item=self.anime_item,
            status=Status.PLANNING.value,
            user=self.user,
        )

        # Test title sorting
        mock_update_preference.side_effect = ["title", None]
        response = self.client.get(
            reverse("list_detail", args=[self.custom_list.id]) + "?sort=title",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "title")

        # Test media_type sorting
        mock_update_preference.side_effect = ["media_type", None]
        response = self.client.get(
            reverse("list_detail", args=[self.custom_list.id]) + "?sort=media_type",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "media_type")

        # Test rating sorting
        mock_update_preference.side_effect = None
        mock_update_preference.return_value = "rating"
        response = self.client.get(
            reverse("list_detail", args=[self.custom_list.id]) + "?sort=rating",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "rating")

        # Test progress sorting
        mock_update_preference.return_value = "progress"
        response = self.client.get(
            reverse("list_detail", args=[self.custom_list.id]) + "?sort=progress",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "progress")

        # Test start_date sorting
        mock_update_preference.return_value = "start_date"
        response = self.client.get(
            reverse("list_detail", args=[self.custom_list.id]) + "?sort=start_date",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "start_date")

        # Test end_date sorting
        mock_update_preference.return_value = "end_date"
        response = self.client.get(
            reverse("list_detail", args=[self.custom_list.id]) + "?sort=end_date",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "end_date")

        # Test release_date sorting
        mock_update_preference.return_value = "release_date"
        response = self.client.get(
            reverse("list_detail", args=[self.custom_list.id]) + "?sort=release_date",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "release_date")

        # Test custom sorting
        mock_update_preference.return_value = "custom"
        response = self.client.get(
            reverse("list_detail", args=[self.custom_list.id]) + "?sort=custom",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "custom")

        # Test status sorting
        mock_update_preference.return_value = "status"
        response = self.client.get(
            reverse("list_detail", args=[self.custom_list.id]) + "?sort=status",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "status")

    def test_release_date_sort_orders_items_and_renders_subtitles(self):
        """Release-date sort should persist, order items, and render full dates."""
        self.user.date_format = DateFormatChoices.ISO_8601
        self.user.save(update_fields=["date_format"])

        self.tv_item.release_datetime = datetime(2019, 3, 10, 12, 0, 0, tzinfo=UTC)
        self.movie_item.release_datetime = datetime(2020, 1, 1, 12, 0, 0, tzinfo=UTC)
        self.anime_item.release_datetime = datetime(2021, 7, 15, 12, 0, 0, tzinfo=UTC)
        self.tv_item.save(update_fields=["release_datetime"])
        self.movie_item.save(update_fields=["release_datetime"])
        self.anime_item.save(update_fields=["release_datetime"])

        response = self.client.get(
            reverse("list_detail", args=[self.custom_list.id]) + "?sort=release_date",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "release_date")

        ordered_titles = [item.title for item in response.context["items"]]
        self.assertEqual(ordered_titles, ["Test TV Show", "Test Movie", "Test Anime"])

        self.user.refresh_from_db()
        self.assertEqual(self.user.list_detail_sort, "release_date")

        self.assertContains(response, "2019-03-10")
        self.assertContains(response, "2020-01-01")
        self.assertContains(response, "2021-07-15")

    def test_release_date_sort_honors_direction(self):
        """Release-date sort should reverse ordering when direction switches."""
        self.tv_item.release_datetime = datetime(2019, 3, 10, 12, 0, 0, tzinfo=UTC)
        self.movie_item.release_datetime = datetime(2020, 1, 1, 12, 0, 0, tzinfo=UTC)
        self.anime_item.release_datetime = datetime(2021, 7, 15, 12, 0, 0, tzinfo=UTC)
        self.tv_item.save(update_fields=["release_datetime"])
        self.movie_item.save(update_fields=["release_datetime"])
        self.anime_item.save(update_fields=["release_datetime"])

        asc_response = self.client.get(
            reverse("list_detail", args=[self.custom_list.id])
            + "?sort=release_date&direction=asc",
        )
        self.assertEqual(asc_response.status_code, 200)
        self.assertEqual(asc_response.context["current_direction"], "asc")
        self.assertEqual(
            [item.title for item in asc_response.context["items"]],
            ["Test TV Show", "Test Movie", "Test Anime"],
        )

        desc_response = self.client.get(
            reverse("list_detail", args=[self.custom_list.id])
            + "?sort=release_date&direction=desc",
        )
        self.assertEqual(desc_response.status_code, 200)
        self.assertEqual(desc_response.context["current_direction"], "desc")
        self.assertEqual(
            [item.title for item in desc_response.context["items"]],
            ["Test Anime", "Test Movie", "Test TV Show"],
        )

    @patch.object(get_user_model(), "update_preference")
    @patch.object(CustomList, "user_can_view")
    @patch("app.providers.services.get_media_metadata")
    def test_list_detail_view_rating_sorting(
        self,
        mock_get_media_metadata,
        mock_user_can_view,
        mock_update_preference,
    ):
        """Test the list_detail view with rating sorting."""
        mock_user_can_view.return_value = True
        mock_update_preference.return_value = "rating"

        # Mock the media metadata to avoid API calls
        mock_get_media_metadata.return_value = {
            "max_progress": 1,
            "related": {"seasons": []},
            "title": "Test Media",
        }

        # Create model instances with different ratings
        Movie.objects.create(
            item=self.movie_item,
            status=Status.COMPLETED.value,
            user=self.user,
            score=8.5,
        )

        TV.objects.create(
            item=self.tv_item,
            status=Status.IN_PROGRESS.value,
            user=self.user,
            score=9.0,
        )

        Anime.objects.create(
            item=self.anime_item,
            status=Status.PLANNING.value,
            user=self.user,
            score=7.5,
        )

        # Test rating sorting - should be in descending order (highest first)
        response = self.client.get(
            reverse("list_detail", args=[self.custom_list.id]) + "?sort=rating",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "rating")

        # Check that items are sorted by rating (highest first)
        items = response.context["items"]
        self.assertEqual(len(items), 3)
        # First item should have highest rating (9.0)
        self.assertEqual(items[0].media.score, 9.0)
        # Second item should have second highest rating (8.5)
        self.assertEqual(items[1].media.score, 8.5)
        # Third item should have lowest rating (7.5)
        self.assertEqual(items[2].media.score, 7.5)

    def test_python_sort_paginates_after_batched_media_scan(self):
        """Derived list sorts hydrate batches, then only the requested page."""
        items = [
            Item(
                media_id=f"batch-movie-{index}",
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title=f"Batch Movie {index:02d}",
            )
            for index in range(20)
        ]
        Item.objects.bulk_create(items)
        items = list(
            Item.objects.filter(media_id__startswith="batch-movie-").order_by("id")
        )
        Movie.objects.bulk_create(
            [
                Movie(
                    item=item,
                    user=self.user,
                    status=Status.IN_PROGRESS.value,
                    score=index,
                )
                for index, item in enumerate(items)
            ]
        )
        CustomListItem.objects.bulk_create(
            [CustomListItem(custom_list=self.custom_list, item=item) for item in items]
        )

        response = self.client.get(
            reverse("list_detail", args=[self.custom_list.public_reference])
            + "?sort=rating&page=2",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["items_count"], 23)
        self.assertEqual(response.context["filtered_items_count"], 23)
        self.assertEqual(len(response.context["items"].object_list), 7)
        self.assertEqual(response.context["items"].object_list[0].media.score, 3)

    def test_smart_python_sort_keeps_count_and_page_size(self):
        """Smart-list derived sorts preserve count while paging page-sized media."""
        items = [
            Item(
                media_id=f"smart-batch-movie-{index}",
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title=f"Smart Batch Movie {index:02d}",
            )
            for index in range(20)
        ]
        Item.objects.bulk_create(items)
        items = list(
            Item.objects.filter(media_id__startswith="smart-batch-movie-").order_by(
                "id"
            )
        )
        Movie.objects.bulk_create(
            [
                Movie(
                    item=item,
                    user=self.user,
                    status=Status.IN_PROGRESS.value,
                    score=index,
                )
                for index, item in enumerate(items)
            ]
        )
        smart_list = CustomList.objects.create(
            name="Smart Batch List",
            owner=self.user,
            is_smart=True,
            smart_media_types=[MediaTypes.MOVIE.value],
            smart_filters={"status": "all", "sort": "rating"},
        )

        response = self.client.get(
            reverse("list_detail", args=[smart_list.public_reference]) + "?page=2",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["items_count"], 20)
        self.assertEqual(response.context["filtered_items_count"], 20)
        self.assertEqual(len(response.context["items"].object_list), 4)
        self.assertEqual(response.context["items"].object_list[0].media.score, 3)

    @patch.object(get_user_model(), "update_preference")
    @patch.object(CustomList, "user_can_view")
    def test_list_detail_view_htmx_request(
        self,
        mock_user_can_view,
        mock_update_preference,
    ):
        """Test the list_detail view with HTMX request."""
        mock_update_preference.side_effect = ["date_added", None]
        mock_user_can_view.return_value = True

        # Create model instances
        Movie.objects.create(
            item=self.movie_item,
            status=Status.COMPLETED.value,
            user=self.user,
        )

        TV.objects.create(
            item=self.tv_item,
            status=Status.IN_PROGRESS.value,
            user=self.user,
        )

        Anime.objects.create(
            item=self.anime_item,
            status=Status.PLANNING.value,
            user=self.user,
        )

        # Make an HTMX request
        response = self.client.get(
            reverse("list_detail", args=[self.custom_list.id]),
            headers={"hx-request": "true"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "lists/components/media_grid.html")
        self.assertNotIn("form", response.context)
        trigger = json.loads(response["HX-Trigger"])
        self.assertEqual(trigger["listCountUpdated"]["count"], 3)
        self.assertEqual(trigger["listCountUpdated"]["label"], "3 items")

    def test_a_video_can_join_a_custom_list(self):
        """A video is added to a list like any other item and the list renders."""
        video_item = Item.objects.create(
            media_id="vid1",
            source=Sources.YOUTUBE.value,
            media_type=MediaTypes.VIDEO.value,
            title="A Listed Video",
            image="http://example.com/video.jpg",
        )
        Video.objects.create(
            item=video_item, user=self.user, status=Status.COMPLETED.value
        )

        response = self.client.post(
            reverse("list_item_toggle"),
            {"item_id": video_item.id, "custom_list_id": self.custom_list.id},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            CustomListItem.objects.filter(
                custom_list=self.custom_list, item=video_item
            ).exists()
        )

        response = self.client.get(
            reverse("list_detail", args=[self.custom_list.public_reference])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A Listed Video")

    def test_list_detail_htmx_count_tracks_membership_toggle(self):
        """A refreshed manual-list response reports the committed item count."""
        response = self.client.post(
            reverse("list_item_toggle"),
            {
                "item_id": self.movie_item.id,
                "custom_list_id": self.custom_list.id,
            },
        )
        self.assertEqual(response.status_code, 200)

        response = self.client.get(
            reverse("list_detail", args=[self.custom_list.public_reference]),
            headers={"hx-request": "true"},
        )
        trigger = json.loads(response["HX-Trigger"])
        self.assertEqual(trigger["listCountUpdated"]["count"], 2)
        self.assertEqual(trigger["listCountUpdated"]["label"], "2 items")

        response = self.client.post(
            reverse("list_item_toggle"),
            {
                "item_id": self.movie_item.id,
                "custom_list_id": self.custom_list.id,
            },
        )
        self.assertEqual(response.status_code, 200)

        response = self.client.get(
            reverse("list_detail", args=[self.custom_list.public_reference]),
            headers={"hx-request": "true"},
        )
        trigger = json.loads(response["HX-Trigger"])
        self.assertEqual(trigger["listCountUpdated"]["count"], 3)
        self.assertEqual(trigger["listCountUpdated"]["label"], "3 items")

    @patch.object(get_user_model(), "update_preference")
    @patch.object(CustomList, "user_can_view")
    @patch("app.providers.services.get_media_metadata")
    def test_list_detail_view_table_layout_full_render(
        self,
        mock_get_media_metadata,
        mock_user_can_view,
        mock_update_preference,
    ):
        """Full list detail renders the table layout when requested."""
        mock_update_preference.side_effect = ["date_added", "table"]
        mock_user_can_view.return_value = True
        mock_get_media_metadata.return_value = {
            "max_progress": 1,
            "related": {"seasons": []},
            "title": "Test Movie",
        }

        self.movie_item.genres = ["Drama"]
        self.movie_item.save(update_fields=["genres"])
        Movie.objects.create(
            item=self.movie_item,
            status=Status.COMPLETED.value,
            user=self.user,
        )

        response = self.client.get(
            reverse("list_detail", args=[self.custom_list.id]) + "?layout=table",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_layout"], "table")
        self.assertEqual(
            [column.key for column in response.context["resolved_columns"]],
            [
                "image",
                "title",
                "media_type",
                "score",
                "critic_rating",
                "progress",
                "genres",
                "tags",
                "status",
                "release_date",
                "date_added",
                "start_date",
                "end_date",
                "entry_source",
                "notes",
                "synopsis",
            ],
        )
        self.assertContains(response, 'id="list-table-body"')
        self.assertContains(response, "min-w-10 w-10 h-10 object-cover rounded-md")
        self.assertContains(response, 'id="media-column-config-data"')
        self.assertContains(response, "Drama")

    @patch.object(get_user_model(), "update_preference")
    @patch.object(CustomList, "user_can_view")
    @patch("app.providers.services.get_media_metadata")
    def test_list_detail_view_table_partial(
        self,
        mock_get_media_metadata,
        mock_user_can_view,
        mock_update_preference,
    ):
        """HTMX table layout requests should return the list-table partial."""
        mock_update_preference.side_effect = ["date_added", "table"]
        mock_user_can_view.return_value = True
        mock_get_media_metadata.return_value = {
            "max_progress": 1,
            "related": {"seasons": []},
            "title": "Test Movie",
        }

        Movie.objects.create(
            item=self.movie_item,
            status=Status.COMPLETED.value,
            user=self.user,
        )

        response = self.client.get(
            reverse("list_detail", args=[self.custom_list.id]) + "?layout=table",
            headers={"hx-request": "true"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "lists/components/list_table.html")
        self.assertContains(
            response, 'class="w-full bg-[var(--color-surface)] media-table"'
        )

    @patch.object(get_user_model(), "update_preference")
    @patch.object(CustomList, "user_can_view")
    def test_list_detail_view_table_platform_column_for_game_list(
        self,
        mock_user_can_view,
        mock_update_preference,
    ):
        """Table view shows real platform values for a game-only list."""
        mock_update_preference.side_effect = ["date_added", "table"]
        mock_user_can_view.return_value = True

        game_list = CustomList.objects.create(
            name="Game List",
            description="Games only",
            owner=self.user,
        )
        pc_item = Item.objects.create(
            media_id="pc-game",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="PC Game",
            platforms=["PC"],
        )
        with (
            patch(
                "app.models.providers.services.get_media_metadata",
                return_value={"max_progress": None},
            ),
            patch("app.models.Item.fetch_releases"),
        ):
            Game.objects.create(
                item=pc_item,
                user=self.user,
                status=Status.PLANNING.value,
            )
        CustomListItem.objects.create(custom_list=game_list, item=pc_item)

        response = self.client.get(
            reverse("list_detail", args=[game_list.id]) + "?layout=table",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "platform",
            [column.key for column in response.context["resolved_columns"]],
        )
        self.assertContains(response, "PC")

    @patch.object(get_user_model(), "update_preference")
    @patch.object(CustomList, "user_can_view")
    def test_list_detail_view_sort_by_platform(
        self,
        mock_user_can_view,
        mock_update_preference,
    ):
        """Sorting by platform orders game items alphabetically, no-platform last."""
        mock_update_preference.side_effect = ["platform", None]
        mock_user_can_view.return_value = True

        game_list = CustomList.objects.create(
            name="Game List",
            description="Games only",
            owner=self.user,
        )
        xbox_item = Item.objects.create(
            media_id="xbox-game",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Xbox Game",
            platforms=["Xbox"],
        )
        pc_item = Item.objects.create(
            media_id="pc-game",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="PC Game",
            platforms=["PC"],
        )
        no_platform_item = Item.objects.create(
            media_id="no-platform-game",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Unknown Platform Game",
        )
        with (
            patch(
                "app.models.providers.services.get_media_metadata",
                return_value={"max_progress": None},
            ),
            patch("app.models.Item.fetch_releases"),
        ):
            for item in (xbox_item, pc_item, no_platform_item):
                Game.objects.create(
                    item=item,
                    user=self.user,
                    status=Status.PLANNING.value,
                )
        for item in (xbox_item, pc_item, no_platform_item):
            CustomListItem.objects.create(custom_list=game_list, item=item)

        response = self.client.get(
            reverse("list_detail", args=[game_list.id]) + "?sort=platform",
        )
        self.assertEqual(response.status_code, 200)
        titles = [item.title for item in response.context["items"]]
        self.assertEqual(
            titles,
            ["PC Game", "Xbox Game", "Unknown Platform Game"],
        )

    @patch.object(get_user_model(), "update_preference")
    @patch.object(CustomList, "user_can_view")
    def test_list_detail_view_sort_choices_platform_scoped_to_game_lists(
        self,
        mock_user_can_view,
        mock_update_preference,
    ):
        """Platform sort option only appears for lists that resolve to games."""
        mock_update_preference.side_effect = ["date_added", None]
        mock_user_can_view.return_value = True

        response = self.client.get(reverse("list_detail", args=[self.custom_list.id]))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("platform", dict(response.context["sort_choices"]))

        game_list = CustomList.objects.create(
            name="Game List",
            description="Games only",
            owner=self.user,
        )
        pc_item = Item.objects.create(
            media_id="pc-game",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="PC Game",
            platforms=["PC"],
        )
        with (
            patch(
                "app.models.providers.services.get_media_metadata",
                return_value={"max_progress": None},
            ),
            patch("app.models.Item.fetch_releases"),
        ):
            Game.objects.create(
                item=pc_item,
                user=self.user,
                status=Status.PLANNING.value,
            )
        CustomListItem.objects.create(custom_list=game_list, item=pc_item)

        mock_update_preference.side_effect = ["date_added", None]
        response = self.client.get(reverse("list_detail", args=[game_list.id]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("platform", dict(response.context["sort_choices"]))

    def test_list_detail_view_layout_persists_without_query_param(self):
        """A saved layout preference applies even without a ?layout= param."""
        self.user.list_detail_layout = "table"
        self.user.save(update_fields=["list_detail_layout"])

        response = self.client.get(reverse("list_detail", args=[self.custom_list.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_layout"], "table")
        self.assertRegex(response.content.decode(), r'id="items-view"\s+class=""')

        response = self.client.get(
            reverse("list_detail", args=[self.custom_list.id]) + "?layout=grid",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_layout"], "grid")
        self.assertRegex(
            response.content.decode(), r'id="items-view"\s+class="media-grid"'
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.list_detail_layout, "grid")

    @patch.object(get_user_model(), "update_preference")
    @patch.object(CustomList, "user_can_view")
    def test_list_table_column_preferences_are_scoped_to_lists(
        self, mock_user_can_view, mock_update_preference
    ):
        mock_update_preference.side_effect = ["date_added", None]
        mock_user_can_view.return_value = True

        self.client.post(
            reverse("list_detail_columns", args=[self.custom_list.id]),
            {
                "table_type": "list",
                "media_type_key": MediaTypes.MOVIE.value,
                "sort": "rating",
                "order": json.dumps(["media_type", "status"]),
                "hidden": json.dumps(["status"]),
            },
            headers={"hx-request": "true"},
        )

        self.user.refresh_from_db()
        self.assertEqual(
            self.user.table_column_prefs[MediaTypes.MOVIE.value]["list"],
            {
                "order": [
                    "media_type",
                    "status",
                    "score",
                    "critic_rating",
                    "runtime",
                    "time_watched",
                    "popularity",
                    "genres",
                    "tags",
                    "release_date",
                    "date_added",
                    "start_date",
                    "end_date",
                    "entry_source",
                    "notes",
                    "synopsis",
                ],
                "hidden": ["status"],
            },
        )

    def test_smart_list_detail_uses_smart_template(self):
        """Smart lists should render the dedicated smart detail view."""
        Movie.objects.create(
            item=self.movie_item,
            status=Status.COMPLETED.value,
            user=self.user,
        )
        smart_list = CustomList.objects.create(
            name="Smart List",
            owner=self.user,
            is_smart=True,
            smart_media_types=[MediaTypes.MOVIE.value],
            smart_filters={"status": "all"},
        )

        response = self.client.get(reverse("list_detail", args=[smart_list.id]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "lists/smart_list_detail.html")
        self.assertTrue(response.context["is_smart_list"])
        self.assertContains(response, "data-list-item-count", html=False)
        self.assertContains(response, "list-count-updated.camel.window", html=False)

    def test_smart_list_htmx_count_tracks_linked_manual_membership(self):
        """Smart-list partials report counts after linked-list membership changes."""
        manual_list = CustomList.objects.create(
            name="Linked Manual List",
            owner=self.user,
        )
        CustomListItem.objects.create(
            custom_list=manual_list,
            item=self.movie_item,
        )
        smart_list = CustomList.objects.create(
            name="Linked Smart List",
            owner=self.user,
            is_smart=True,
            smart_media_types=[MediaTypes.MOVIE.value],
            smart_filters={"list": [manual_list.id]},
        )

        response = self.client.get(
            reverse("list_detail", args=[smart_list.public_reference]),
            headers={"hx-request": "true"},
        )
        trigger = json.loads(response["HX-Trigger"])
        self.assertEqual(trigger["listCountUpdated"]["count"], 1)
        self.assertEqual(trigger["listCountUpdated"]["label"], "1 item")

        response = self.client.post(
            reverse("list_item_toggle"),
            {
                "item_id": self.movie_item.id,
                "custom_list_id": manual_list.id,
            },
        )
        self.assertEqual(response.status_code, 200)

        response = self.client.get(
            reverse("list_detail", args=[smart_list.public_reference]),
            headers={"hx-request": "true"},
        )
        trigger = json.loads(response["HX-Trigger"])
        self.assertEqual(trigger["listCountUpdated"]["count"], 0)
        self.assertEqual(trigger["listCountUpdated"]["label"], "0 items")

    def test_smart_filter_form_carries_every_persisted_rule_key(self):
        """Every saved rule must have a form input, or it silently saves empty.

        The hidden form is both the hx-include payload and the shape the save
        payload mirrors, so a rule key with no input previews correctly and is
        then dropped on reload (this is how implied_genre was being lost).
        """
        smart_list = CustomList.objects.create(
            name="Rule Coverage",
            owner=self.user,
            is_smart=True,
            smart_media_types=[MediaTypes.MOVIE.value],
        )

        response = self.client.get(
            f"{reverse('list_detail', args=[smart_list.id])}?edit_smart_rules=1",
        )
        html = response.content.decode()

        # Keys whose form field is deliberately named differently.
        field_names = {
            "search": "q",
            "sort_direction": "direction",
        }
        # Keys the page only offers when its filter_data carries options for
        # them, so their absence here is the gating working, not a dropped
        # field. `provider` needs a watch region; this fixture has none.
        conditionally_offered = {"provider"}
        # Stored on the list and kept by every save; never edited in the form.
        not_editable = {"semantics_version"}
        missing = [
            key
            for key in smart_rules.SMART_FILTER_KEYS
            if key not in conditionally_offered
            and key not in not_editable
            and f'name="{field_names.get(key, key)}"' not in html
        ]
        self.assertEqual(missing, [])

    def test_smart_list_detail_shares_layout_preference_with_manual_lists(self):
        """Smart lists respect the same saved list_detail_layout preference."""
        self.user.list_detail_layout = "table"
        self.user.save(update_fields=["list_detail_layout"])

        smart_list = CustomList.objects.create(
            name="Smart List",
            owner=self.user,
            is_smart=True,
            smart_media_types=[MediaTypes.MOVIE.value],
            smart_filters={"status": "all"},
        )

        response = self.client.get(reverse("list_detail", args=[smart_list.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_layout"], "table")
        self.assertRegex(response.content.decode(), r'id="items-view"\s+class=""')

        response = self.client.get(
            reverse("list_detail", args=[smart_list.id]) + "?layout=grid",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_layout"], "grid")
        self.assertRegex(
            response.content.decode(), r'id="items-view"\s+class="media-grid"'
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.list_detail_layout, "grid")

    def test_smart_list_detail_sort_choices_include_platform_for_game_list(self):
        """A game-only smart list exposes Platform as a sort choice."""
        smart_list = CustomList.objects.create(
            name="Smart Game List",
            owner=self.user,
            is_smart=True,
            smart_media_types=[MediaTypes.GAME.value],
            smart_filters={"status": "all"},
        )

        response = self.client.get(reverse("list_detail", args=[smart_list.id]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("platform", dict(response.context["sort_choices"]))

    def test_manual_list_detail_exposes_quick_add_split_actions(self):
        """Editable manual lists should use quick-add actions in the detail header."""
        response = self.client.get(reverse("list_detail", args=[self.custom_list.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, reverse("list_add_item", args=[self.custom_list.id])
        )
        self.assertContains(response, "Add New Item")
        self.assertContains(response, "More list actions")
        self.assertNotContains(response, 'aria-label="Edit list"')

    def test_manual_list_detail_reorders_internal_header_like_public_layout(self):
        """Owner views should keep the same header stack as public manual lists."""
        self.custom_list.visibility = "public"
        self.custom_list.allow_recommendations = True
        self.custom_list.save(update_fields=["visibility", "allow_recommendations"])

        response = self.client.get(reverse("list_detail", args=[self.custom_list.id]))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertLess(content.index("3 items"), content.index("Links"))
        self.assertLess(content.index("Links"), content.index("Add New Item"))
        self.assertLess(
            content.index("Add New Item"), content.index("Test Description")
        )

    @patch("app.providers.services.get_media_metadata")
    def test_smart_list_detail_exposes_smart_rule_split_actions(
        self,
        mock_get_media_metadata,
    ):
        """Editable smart lists should use the smart-rule split button in the header."""
        mock_get_media_metadata.return_value = {
            "max_progress": 1,
            "related": {"seasons": []},
            "title": "Test Movie",
        }
        Movie.objects.create(
            item=self.movie_item,
            status=Status.COMPLETED.value,
            user=self.user,
        )
        smart_list = CustomList.objects.create(
            name="Smart List",
            owner=self.user,
            is_smart=True,
            smart_media_types=[MediaTypes.MOVIE.value],
            smart_filters={"status": "all"},
        )

        response = self.client.get(reverse("list_detail", args=[smart_list.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f"{reverse('list_detail', args=[smart_list.id])}?edit_smart_rules=1",
        )
        self.assertContains(response, "Smart Rules")
        self.assertContains(response, "More list actions")
        self.assertNotContains(response, 'aria-label="Edit list metadata"')

    @patch("app.providers.services.get_media_metadata")
    def test_smart_list_detail_reorders_internal_header_like_public_layout(
        self,
        mock_get_media_metadata,
    ):
        """Owner smart-list views should keep description above the editable rules UI."""
        mock_get_media_metadata.return_value = {
            "max_progress": 1,
            "related": {"seasons": []},
            "title": "Test Movie",
        }
        Movie.objects.create(
            item=self.movie_item,
            status=Status.COMPLETED.value,
            user=self.user,
        )
        smart_list = CustomList.objects.create(
            name="Internal Smart List",
            description="Smart Description",
            owner=self.user,
            is_smart=True,
            visibility="public",
            allow_recommendations=True,
            smart_media_types=[MediaTypes.MOVIE.value],
            smart_filters={"status": "all"},
        )

        response = self.client.get(reverse("list_detail", args=[smart_list.id]))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertLess(content.index("1 item"), content.index("Links"))
        self.assertLess(content.index("Links"), content.index("Edit Smart Rules"))
        self.assertLess(
            content.index("Edit Smart Rules"), content.index("Smart Description")
        )
        self.assertLess(
            content.index("Smart Description"), content.rindex("Smart Rules")
        )

    @patch("app.providers.services.get_media_metadata")
    def test_public_smart_list_reorders_header_and_removes_public_banner(
        self,
        mock_get_media_metadata,
    ):
        """Public smart lists should match the manual-list header ordering."""
        mock_get_media_metadata.return_value = {
            "max_progress": 1,
            "related": {"seasons": []},
            "title": "Test Movie",
        }
        Movie.objects.create(
            item=self.movie_item,
            status=Status.COMPLETED.value,
            user=self.user,
        )
        smart_list = CustomList.objects.create(
            name="Public Smart List",
            description="Smart Description",
            owner=self.user,
            is_smart=True,
            visibility="public",
            allow_recommendations=True,
            smart_media_types=[MediaTypes.MOVIE.value],
            smart_filters={"status": "all"},
        )

        self.client.logout()
        response = self.client.get(reverse("list_detail", args=[smart_list.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1 item")
        self.assertContains(response, self.user.username)
        self.assertNotContains(response, "View Profile")
        self.assertNotContains(response, "You're viewing a public list.")
        self.assertNotContains(response, "Smart Rules")

        content = response.content.decode()
        self.assertIn("border-indigo-400/18 bg-indigo-500/[0.07]", content)
        self.assertLess(content.index("1 item"), content.index(self.user.username))
        self.assertLess(content.index(self.user.username), content.index("Links"))
        self.assertLess(content.index("Links"), content.index("Recommend Item"))
        self.assertLess(
            content.index("Recommend Item"), content.index("Smart Description")
        )

    @patch("app.providers.services.get_media_metadata")
    def test_smart_list_detail_table_partial(self, mock_get_media_metadata):
        """Smart list table layout should return list-table partials for HTMX."""
        mock_get_media_metadata.return_value = {
            "max_progress": 1,
            "related": {"seasons": []},
            "title": "Test Movie",
        }
        Movie.objects.create(
            item=self.movie_item,
            status=Status.COMPLETED.value,
            user=self.user,
        )
        smart_list = CustomList.objects.create(
            name="Smart List",
            owner=self.user,
            is_smart=True,
            smart_media_types=[MediaTypes.MOVIE.value],
            smart_filters={"status": "all"},
        )

        response = self.client.get(
            reverse("list_detail", args=[smart_list.id])
            + "?edit_smart_rules=1&layout=table",
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "lists/components/list_table.html")

    @patch("app.providers.services.get_media_metadata")
    def test_public_smart_list_filters_without_persisting_rules(
        self,
        mock_get_media_metadata,
    ):
        """Public smart-list filtering should not mutate saved rules."""
        mock_get_media_metadata.return_value = {
            "max_progress": 1,
            "related": {"seasons": []},
            "title": "Test Media",
        }
        Movie.objects.create(
            item=self.movie_item,
            status=Status.COMPLETED.value,
            user=self.user,
        )
        TV.objects.create(
            item=self.tv_item,
            status=Status.IN_PROGRESS.value,
            user=self.user,
        )

        smart_list = CustomList.objects.create(
            name="Public Smart List",
            owner=self.user,
            is_smart=True,
            visibility="public",
            smart_media_types=[MediaTypes.MOVIE.value, MediaTypes.TV.value],
            smart_filters={"status": "all"},
        )

        self.client.logout()

        response = self.client.get(reverse("list_detail", args=[smart_list.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="smart-filter-form"')
        self.assertEqual(len(response.context["items"]), 2)

        filtered_response = self.client.get(
            reverse("list_detail", args=[smart_list.id])
            + f"?status={Status.COMPLETED.value}",
        )
        self.assertEqual(filtered_response.status_code, 200)
        self.assertFalse(filtered_response.context["smart_edit_mode"])
        self.assertEqual(
            filtered_response.context["active_smart_rules"]["status"],
            [Status.COMPLETED.value],
        )
        self.assertEqual(
            filtered_response.context["saved_smart_rules"]["status"],
            [],
        )
        self.assertEqual(len(filtered_response.context["items"]), 1)
        self.assertEqual(
            filtered_response.context["items"][0].id,
            self.movie_item.id,
        )

        smart_list.refresh_from_db()
        self.assertEqual(smart_list.smart_filters.get("status"), "all")

    @patch("app.providers.services.get_media_metadata")
    def test_public_smart_list_anonymous_view_preserves_saved_media_types(
        self,
        mock_get_media_metadata,
    ):
        """Anonymous public smart-list loads must honor saved media-type rules."""
        mock_get_media_metadata.return_value = {
            "max_progress": 1,
            "related": {"seasons": []},
            "title": "Test Media",
        }
        Movie.objects.create(
            item=self.movie_item,
            status=Status.COMPLETED.value,
            user=self.user,
        )
        TV.objects.create(
            item=self.tv_item,
            status=Status.COMPLETED.value,
            user=self.user,
        )

        smart_list = CustomList.objects.create(
            name="Public Movies",
            owner=self.user,
            is_smart=True,
            visibility="public",
            smart_media_types=[MediaTypes.MOVIE.value],
            smart_filters={"status": "all"},
        )

        self.client.logout()

        response = self.client.get(reverse("list_detail", args=[smart_list.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["saved_smart_rules"]["media_types"],
            [MediaTypes.MOVIE.value],
        )
        self.assertEqual(
            response.context["active_smart_rules"]["media_types"],
            [MediaTypes.MOVIE.value],
        )
        self.assertEqual(
            [item.id for item in response.context["items"]],
            [self.movie_item.id],
        )

        filtered_response = self.client.get(
            reverse("list_detail", args=[smart_list.id])
            + f"?status={Status.COMPLETED.value}",
        )

        self.assertEqual(filtered_response.status_code, 200)
        self.assertEqual(
            filtered_response.context["active_smart_rules"]["media_types"],
            [MediaTypes.MOVIE.value],
        )
        self.assertEqual(
            [item.id for item in filtered_response.context["items"]],
            [self.movie_item.id],
        )

    def test_smart_list_release_date_sort_honors_direction(self):
        """Smart-list release-date sort should reverse ordering by direction."""
        self.movie_item.release_datetime = datetime(2020, 1, 1, 12, 0, 0, tzinfo=UTC)
        self.tv_item.release_datetime = datetime(2021, 1, 1, 12, 0, 0, tzinfo=UTC)
        self.anime_item.release_datetime = datetime(2019, 1, 1, 12, 0, 0, tzinfo=UTC)
        self.movie_item.save(update_fields=["release_datetime"])
        self.tv_item.save(update_fields=["release_datetime"])
        self.anime_item.save(update_fields=["release_datetime"])

        Movie.objects.create(
            item=self.movie_item, status=Status.COMPLETED.value, user=self.user
        )
        TV.objects.create(
            item=self.tv_item, status=Status.IN_PROGRESS.value, user=self.user
        )
        Anime.objects.create(
            item=self.anime_item, status=Status.PLANNING.value, user=self.user
        )

        smart_list = CustomList.objects.create(
            name="Smart List",
            owner=self.user,
            is_smart=True,
            smart_media_types=[
                MediaTypes.MOVIE.value,
                MediaTypes.TV.value,
                MediaTypes.ANIME.value,
            ],
            smart_filters={"status": "all"},
        )

        asc_response = self.client.get(
            reverse("list_detail", args=[smart_list.id])
            + "?sort=release_date&direction=asc",
        )
        self.assertEqual(asc_response.status_code, 200)
        self.assertEqual(asc_response.context["current_direction"], "asc")
        self.assertEqual(
            [item.title for item in asc_response.context["items"]],
            ["Test Anime", "Test Movie", "Test TV Show"],
        )

        desc_response = self.client.get(
            reverse("list_detail", args=[smart_list.id])
            + "?sort=release_date&direction=desc",
        )
        self.assertEqual(desc_response.status_code, 200)
        self.assertEqual(desc_response.context["current_direction"], "desc")
        self.assertEqual(
            [item.title for item in desc_response.context["items"]],
            ["Test TV Show", "Test Movie", "Test Anime"],
        )

    def test_smart_list_uses_saved_sort_defaults(self):
        """Smart-list detail should default to the saved sort and direction."""
        Movie.objects.create(
            item=self.movie_item,
            status=Status.COMPLETED.value,
            user=self.user,
            score=8.0,
        )
        TV.objects.create(
            item=self.tv_item, status=Status.IN_PROGRESS.value, user=self.user
        )

        smart_list = CustomList.objects.create(
            name="Sorted Smart List",
            owner=self.user,
            is_smart=True,
            smart_media_types=[MediaTypes.MOVIE.value, MediaTypes.TV.value],
            smart_filters={
                "status": "all",
                "sort": "rating",
                "sort_direction": "asc",
            },
        )

        response = self.client.get(reverse("list_detail", args=[smart_list.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "rating")
        self.assertEqual(response.context["current_direction"], "asc")

    @patch("app.providers.services.get_media_metadata")
    @patch.object(get_user_model(), "update_preference")
    @patch.object(CustomList, "user_can_view")
    def test_list_detail_with_episode_rating_sort(
        self,
        mock_user_can_view,
        mock_update_preference,
        mock_get_metadata,
    ):
        """Episode items in lists must not cause 500 when sorted by rating (issue #93)."""
        mock_update_preference.return_value = "rating"
        mock_user_can_view.return_value = True
        # Make the API call fail gracefully so Episode.save() doesn't error
        mock_get_metadata.side_effect = KeyError("no api in tests")

        season_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            season_number=1,
        )
        season = Season.objects.create(
            item=season_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        episode_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends S1E1",
            season_number=1,
            episode_number=1,
        )
        Episode.objects.create(
            item=episode_item,
            related_season=season,
        )

        episode_list = CustomList.objects.create(
            name="Episode List",
            owner=self.user,
        )
        CustomListItem.objects.create(
            custom_list=episode_list,
            item=episode_item,
        )

        response = self.client.get(
            reverse("list_detail", args=[episode_list.id]) + "?sort=rating",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["items"]), 1)
        self.assertContains(response, "S01 E01")

    @patch.object(get_user_model(), "update_preference")
    @patch.object(CustomList, "user_can_view")
    def test_list_detail_untracked_episode_cards_use_season_poster_and_visible_identity_subtitle(
        self,
        mock_user_can_view,
        mock_update_preference,
    ):
        """Episode list cards should still show season art and Sxx Exx without tracking rows."""
        mock_update_preference.side_effect = ["date_added", None]
        mock_user_can_view.return_value = True

        season_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            season_number=1,
            image="http://example.com/season.jpg",
        )
        episode_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="The One Where It Starts",
            season_number=1,
            episode_number=1,
            image=settings.IMG_NONE,
        )

        episode_list = CustomList.objects.create(
            name="Episode List",
            owner=self.user,
        )
        CustomListItem.objects.create(
            custom_list=episode_list,
            item=episode_item,
        )

        response = self.client.get(reverse("list_detail", args=[episode_list.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "S01 E01")
        self.assertContains(response, "http://example.com/season.jpg")
        self.assertContains(response, "media-card-subtitle-always")

    @patch.object(get_user_model(), "update_preference")
    @patch.object(CustomList, "user_can_view")
    def test_list_detail_episode_cards_show_lists_button_on_hover(
        self,
        mock_user_can_view,
        mock_update_preference,
    ):
        """Episode cards in a list grid must expose the "add to lists" action.

        Previously the episode branch of media_card.html only rendered the
        "mark watched" toggle, so an episode already in a custom list (e.g. a
        "watch together" list of specific episodes) had no way to be removed
        from the list overview screen — the Lists button only existed on the
        episode's own detail page.
        """
        mock_update_preference.side_effect = ["date_added", None]
        mock_user_can_view.return_value = True

        episode_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="The One Where It Starts",
            season_number=1,
            episode_number=1,
            image=settings.IMG_NONE,
        )

        episode_list = CustomList.objects.create(
            name="Episode List",
            owner=self.user,
        )
        CustomListItem.objects.create(
            custom_list=episode_list,
            item=episode_item,
        )

        response = self.client.get(reverse("list_detail", args=[episode_list.id]))

        self.assertEqual(response.status_code, 200)
        titles = [
            item["title"]
            for model in card_models(response.content)
            for item in (model["actions"] or {}).get("items", [])
        ]
        self.assertIn("Add to custom lists", titles)

    @patch.object(get_user_model(), "update_preference")
    @patch.object(CustomList, "user_can_view")
    def test_list_detail_episode_watch_button_loads_real_modal_and_reflects_state(
        self,
        mock_user_can_view,
        mock_update_preference,
    ):
        """Episode cards' watch-toggle button must actually load the track modal.

        Previously this button only set trackOpen=true with no hx-get, so
        clicking it opened a permanently empty modal overlay (verified live
        in a browser) — Alpine had nothing to load into the target div. It
        also always said "Mark watched" even when the episode was already
        watched, unlike every other media type's tracking button which
        reflects an already-in-progress/edit state.
        """
        mock_update_preference.side_effect = ["date_added", None]
        mock_user_can_view.return_value = True

        unwatched_item = Item.objects.create(
            media_id="9001",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="The One Where It Starts",
            season_number=1,
            episode_number=1,
            image=settings.IMG_NONE,
        )
        watched_item = Item.objects.create(
            media_id="9001",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="The One With Two Parts",
            season_number=1,
            episode_number=2,
            image=settings.IMG_NONE,
        )
        season_item = Item.objects.create(
            media_id="9001",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=1,
            image=settings.IMG_NONE,
        )
        tv_item = Item.objects.create(
            media_id="9001",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            image=settings.IMG_NONE,
        )
        tv = TV.objects.create(
            item=tv_item, user=self.user, status=Status.IN_PROGRESS.value
        )
        season = Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=tv,
            status=Status.IN_PROGRESS.value,
        )
        watched_episode = Episode.objects.create(
            item=watched_item,
            related_season=season,
        )

        episode_list = CustomList.objects.create(name="Episode List", owner=self.user)
        CustomListItem.objects.create(custom_list=episode_list, item=unwatched_item)
        CustomListItem.objects.create(custom_list=episode_list, item=watched_item)

        response = self.client.get(reverse("list_detail", args=[episode_list.id]))

        self.assertEqual(response.status_code, 200)
        expected_url = reverse(
            "track_modal",
            kwargs={
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.EPISODE.value,
                "media_id": "9001",
                "season_number": 1,
            },
        )
        actions = [
            item
            for model in card_models(response.content)
            for item in (model["actions"] or {}).get("items", [])
        ]
        track = [a for a in actions if a.get("modal") == "track"]
        self.assertIn(expected_url, [a["url"] for a in track])
        self.assertTrue(any(a["vals"].get("is_create") == "1" for a in track))
        self.assertTrue(
            any(
                a["vals"].get("instance_id") == str(watched_episode.id)
                for a in track
            )
        )
        titles = [a["title"] for a in actions]
        self.assertIn("Mark watched", titles)
        self.assertIn("Watched", titles)

        # Regression: Django's {# #} comment syntax is single-line only —
        # spreading one across multiple lines silently stops it being
        # recognized as a comment at all, and the literal text (including
        # this internal implementation note) renders straight into the
        # page. Caught live: it showed up as visible text on an episode
        # card. `{% comment %}...{% endcomment %}` is the multi-line form.
        self.assertNotContains(response, "hero_track_button")
        self.assertNotContains(response, "empty overlay")

    def test_select_items_button_sits_inside_the_bulk_selection_scope(self):
        """The header's Select Items button must be inside bulkSelection, or it is dead.

        Alpine reads selectMode from the closest component; a button above it
        threw "selectMode is not defined" and did nothing.
        """
        smart_list = CustomList.objects.create(
            name="Smart Scope",
            owner=self.user,
            is_smart=True,
        )
        for custom_list in (self.custom_list, smart_list):
            with self.subTest(smart=custom_list.is_smart):
                content = self.client.get(
                    reverse("list_detail", args=[custom_list.id]),
                ).content.decode()
                scope = content.index('x-data="bulkSelection(')
                self.assertGreater(content.index("toggleSelectMode()"), scope)
                # $refs cannot see a ref inside the nested bulkSelection component.
                self.assertNotIn("$refs.itemsView", content)

    def test_viewer_who_can_only_recommend_gets_no_select_items_button(self):
        """Bulk actions edit the list, so a recommend-only viewer has no selection."""
        self.custom_list.visibility = "public"
        self.custom_list.allow_recommendations = True
        self.custom_list.save()
        self.client.login(**self.other_credentials)

        response = self.client.get(reverse("list_detail", args=[self.custom_list.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Recommend Item")
        self.assertNotContains(response, "toggleSelectMode()")

    @patch.object(get_user_model(), "update_preference")
    @patch.object(CustomList, "user_can_view")
    def test_list_detail_episode_card_shows_rating(
        self,
        mock_user_can_view,
        mock_update_preference,
    ):
        """A rated episode's card shows its rating, like every other card."""
        mock_update_preference.side_effect = ["date_added", None]
        mock_user_can_view.return_value = True

        episode_item = Item.objects.create(
            media_id="9002",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="The Rated One",
            season_number=1,
            episode_number=1,
            image=settings.IMG_NONE,
        )
        season_item = Item.objects.create(
            media_id="9002",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=1,
            image=settings.IMG_NONE,
        )
        tv_item = Item.objects.create(
            media_id="9002",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            image=settings.IMG_NONE,
        )
        tv = TV.objects.create(
            item=tv_item, user=self.user, status=Status.IN_PROGRESS.value
        )
        season = Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=tv,
            status=Status.IN_PROGRESS.value,
        )
        episode = Episode.objects.create(
            item=episode_item,
            related_season=season,
            score=7.5,
        )
        episode_list = CustomList.objects.create(name="Rated Episodes", owner=self.user)
        CustomListItem.objects.create(custom_list=episode_list, item=episode_item)

        response = self.client.get(reverse("list_detail", args=[episode_list.id]))

        ratings = {
            m["ratingId"]: m["rating"] for m in card_models(response.content)
        }
        rating = ratings[item_slot_id("media-card-rating", episode_item)]
        self.assertEqual(rating["label"], "7.5")

    @patch("lists.views.services.get_media_metadata")
    @patch.object(get_user_model(), "update_preference")
    @patch.object(CustomList, "user_can_view")
    def test_list_detail_zero_season_episode_cards_show_identity_and_backfilled_episode_title(
        self,
        mock_user_can_view,
        mock_update_preference,
        mock_get_metadata,
    ):
        """Season 0 episode cards should still show S00 Exx and the episode title."""
        mock_update_preference.side_effect = ["date_added", None]
        mock_user_can_view.return_value = True
        mock_get_metadata.return_value = {
            "title": "Death Note",
            "season_title": "Specials",
            "episodes": [
                {
                    "episode_number": 1,
                    "name": "Rebirth",
                    "image": settings.IMG_NONE,
                },
            ],
            "image": settings.IMG_NONE,
        }

        season_item = Item.objects.create(
            media_id="13916",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Death Note",
            season_number=0,
            image="http://example.com/season-zero.jpg",
        )
        episode_item = Item.objects.create(
            media_id="13916",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Death Note",
            season_number=0,
            episode_number=1,
            image=settings.IMG_NONE,
        )
        season_entry = Season.objects.create(
            item=season_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        Episode.objects.create(
            item=episode_item,
            related_season=season_entry,
        )

        episode_list = CustomList.objects.create(
            name="Death Note Episodes",
            owner=self.user,
        )
        CustomListItem.objects.create(
            custom_list=episode_list,
            item=episode_item,
        )

        response = self.client.get(reverse("list_detail", args=[episode_list.id]))
        episode_item.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "S00 E01")
        self.assertContains(response, "Rebirth")
        self.assertEqual(episode_item.title, "Rebirth")
        self.assertContains(response, "http://example.com/season-zero.jpg")


class CreateListViewTest(TestCase):
    """Test case for the create list view."""

    def setUp(self):
        """Set up test data for create list view tests."""
        self.client = Client()
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_create_list(self):
        """Test creating a new custom list."""
        self.client.post(
            reverse("list_create"),
            {"name": "New List", "description": "New Description"},
        )
        self.assertEqual(CustomList.objects.count(), 1)
        new_list = CustomList.objects.first()
        self.assertEqual(new_list.name, "New List")
        self.assertEqual(new_list.description, "New Description")
        self.assertEqual(new_list.owner, self.user)

    def test_create_smart_list_redirects_to_builder(self):
        """Smart-create flow should land on detail page in smart edit mode."""
        response = self.client.post(
            reverse("list_create"),
            {
                "name": "Smart List",
                "description": "",
                "is_smart": "on",
                "smart_create_flow": "1",
            },
        )
        self.assertEqual(response.status_code, 302)
        smart_list = CustomList.objects.get(name="Smart List")
        self.assertEqual(
            response.url,
            reverse("list_detail", args=[smart_list.id]) + "?edit_smart_rules=1",
        )


class SmartRulesUpdateViewTest(TestCase):
    """Tests for smart rules autosave endpoint."""

    def setUp(self):
        self.client = Client()
        self.owner = get_user_model().objects.create_user(
            username="owner",
            password="12345",
        )
        self.collaborator = get_user_model().objects.create_user(
            username="collab",
            password="12345",
        )
        self.outsider = get_user_model().objects.create_user(
            username="outsider",
            password="12345",
        )
        self.item = Item.objects.create(
            media_id="500",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Smart Match",
            image="https://example.com/smart.jpg",
        )
        Movie.objects.create(
            item=self.item,
            user=self.owner,
            status=Status.COMPLETED.value,
        )

        self.smart_list = CustomList.objects.create(
            name="Smart",
            owner=self.owner,
            is_smart=True,
        )
        self.smart_list.collaborators.add(self.collaborator)

        self.manual_list = CustomList.objects.create(
            name="Manual",
            owner=self.owner,
            is_smart=False,
        )

    def test_owner_can_update_smart_rules(self):
        self.client.login(username="owner", password="12345")
        response = self.client.post(
            reverse("list_smart_rules_update", args=[self.smart_list.id]),
            data=json.dumps(
                {
                    "media_types": [MediaTypes.MOVIE.value],
                    "status": "all",
                    "rating": "all",
                    "collection": "all",
                    "search": "Smart",
                },
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.smart_list.refresh_from_db()
        self.assertEqual(self.smart_list.smart_media_types, [MediaTypes.MOVIE.value])
        self.assertEqual(self.smart_list.smart_filters["search"], "Smart")
        self.assertTrue(self.smart_list.items.filter(id=self.item.id).exists())

    def test_owner_can_update_smart_rules_with_ranges_and_sort(self):
        self.client.login(username="owner", password="12345")
        response = self.client.post(
            reverse("list_smart_rules_update", args=[self.smart_list.id]),
            data=json.dumps(
                {
                    "media_types": [MediaTypes.MOVIE.value],
                    "rating_min": "7.0",
                    "rating_max": "9.0",
                    "release_date_from": "2000-01-01",
                    "release_date_to": "2009-12-31",
                    "date_added_from": "2026-01-01",
                    "date_added_to": "2026-01-31",
                    "sort": "rating",
                    "sort_direction": "desc",
                },
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.smart_list.refresh_from_db()
        self.assertEqual(self.smart_list.smart_filters["rating_min"], "7.0")
        self.assertEqual(self.smart_list.smart_filters["rating_max"], "9.0")
        self.assertEqual(
            self.smart_list.smart_filters["release_date_from"], "2000-01-01"
        )
        self.assertEqual(self.smart_list.smart_filters["release_date_to"], "2009-12-31")
        self.assertEqual(self.smart_list.smart_filters["date_added_from"], "2026-01-01")
        self.assertEqual(self.smart_list.smart_filters["date_added_to"], "2026-01-31")
        self.assertEqual(self.smart_list.smart_filters["sort"], "rating")
        self.assertEqual(self.smart_list.smart_filters["sort_direction"], "desc")

    def test_collaborator_can_update_smart_rules(self):
        self.client.login(username="collab", password="12345")
        response = self.client.post(
            reverse("list_smart_rules_update", args=[self.smart_list.id]),
            data=json.dumps({"media_types": [MediaTypes.MOVIE.value]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

    def test_outsider_cannot_update_smart_rules(self):
        self.client.login(username="outsider", password="12345")
        response = self.client.post(
            reverse("list_smart_rules_update", args=[self.smart_list.id]),
            data=json.dumps({"media_types": [MediaTypes.MOVIE.value]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_manual_list_rejects_smart_rule_updates(self):
        self.client.login(username="owner", password="12345")
        response = self.client.post(
            reverse("list_smart_rules_update", args=[self.manual_list.id]),
            data=json.dumps({"media_types": [MediaTypes.MOVIE.value]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)


class ShareViewTest(TestCase):
    """Tests for the Share View endpoint."""

    def setUp(self):
        self.client = Client()
        self.user = get_user_model().objects.create_user(
            username="owner",
            password="12345",
        )
        self.client.login(username="owner", password="12345")

    def test_share_view_creates_public_smart_list(self):
        response = self.client.post(
            reverse("list_share_view"),
            {"media_type": MediaTypes.TV.value, "status": "In progress"},
        )
        self.assertEqual(response.status_code, 200)
        custom_list = CustomList.objects.get()
        self.assertTrue(custom_list.is_smart)
        self.assertEqual(custom_list.visibility, "public")
        self.assertEqual(custom_list.owner, self.user)
        self.assertEqual(custom_list.smart_media_types, [MediaTypes.TV.value])
        self.assertEqual(custom_list.smart_filters["status"], ["In progress"])
        self.assertEqual(
            response.json()["url"],
            custom_list.get_absolute_url(),
        )

    def test_share_view_reuses_existing_matching_list(self):
        first = self.client.post(
            reverse("list_share_view"),
            {"media_type": MediaTypes.TV.value, "status": "In progress"},
        ).json()
        second = self.client.post(
            reverse("list_share_view"),
            {"media_type": MediaTypes.TV.value, "status": "In progress"},
        ).json()
        self.assertEqual(first["url"], second["url"])
        self.assertEqual(CustomList.objects.count(), 1)

    def test_share_view_different_filters_create_different_lists(self):
        self.client.post(
            reverse("list_share_view"),
            {"media_type": MediaTypes.TV.value, "status": "In progress"},
        )
        self.client.post(
            reverse("list_share_view"),
            {"media_type": MediaTypes.TV.value, "status": "Completed"},
        )
        self.assertEqual(CustomList.objects.count(), 2)

    def test_share_view_rejects_invalid_media_type(self):
        response = self.client.post(
            reverse("list_share_view"),
            {"media_type": "not-a-real-type"},
        )
        self.assertEqual(response.status_code, 400)

    def test_share_view_requires_login(self):
        self.client.logout()
        response = self.client.post(
            reverse("list_share_view"),
            {"media_type": MediaTypes.TV.value},
        )
        self.assertEqual(response.status_code, 302)


class EditListViewTest(TestCase):
    """Test case for the edit list view."""

    def setUp(self):
        """Set up test data for edit list view tests."""
        self.client = Client()
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        self.collaborator_credentials = {
            "username": "collaborator",
            "password": "12345",
        }
        self.collaborator = get_user_model().objects.create_user(
            **self.collaborator_credentials,
        )
        self.list = CustomList.objects.create(name="Test List", owner=self.user)
        self.list.collaborators.add(self.collaborator)

    def test_edit_list(self):
        """Test editing an existing custom list."""
        self.client.login(**self.credentials)
        self.client.post(
            reverse("list_edit"),
            {
                "list_id": self.list.id,
                "name": "Updated List",
                "description": "Updated Description",
            },
        )
        self.list.refresh_from_db()
        self.assertEqual(self.list.name, "Updated List")
        self.assertEqual(self.list.description, "Updated Description")

    def test_edit_list_collaborator(self):
        """Test editing an existing custom list as a collaborator."""
        self.client.login(**self.collaborator_credentials)
        self.client.post(
            reverse("list_edit"),
            {
                "list_id": self.list.id,
                "name": "Updated List",
                "description": "Updated Description",
            },
        )
        self.list.refresh_from_db()
        self.assertEqual(self.list.name, "Updated List")
        self.assertEqual(self.list.description, "Updated Description")

    def test_edit_list_updates_public_slug(self):
        """Test editing a list to publish it with a custom URL."""
        self.client.login(**self.credentials)
        self.client.post(
            reverse("list_edit"),
            {
                "list_id": self.list.id,
                "name": "Updated List",
                "description": "Updated Description",
                "is_public": "on",
                "public_slug": "Favorite Movies",
            },
        )

        self.list.refresh_from_db()
        self.assertEqual(self.list.visibility, "public")
        self.assertEqual(self.list.public_slug, "favorite-movies")

    def test_edit_list_invalid_form_reports_errors(self):
        """An invalid edit is not saved silently; the user sees why."""
        CustomList.objects.create(name="Other", owner=self.user, public_slug="taken")
        self.client.login(**self.credentials)
        response = self.client.post(
            reverse("list_edit") + "?next=/lists",
            {
                "list_id": self.list.id,
                "name": "Updated List",
                "is_public": "on",
                "public_slug": "taken",
            },
            follow=True,
        )

        self.list.refresh_from_db()
        self.assertEqual(self.list.name, "Test List")
        self.assertIn(
            "Public Slug: That URL is already in use.",
            [str(message) for message in response.context["messages"]],
        )

    def test_edit_form_fragment_returns_to_lists_page(self):
        """The lazily fetched modal must not redirect back to its own URL."""
        self.client.login(**self.credentials)
        response = self.client.get(reverse("list_edit_form", args=[self.list.id]))

        self.assertContains(response, f'{reverse("list_edit")}?next=/lists"')
        self.assertNotContains(response, "?next=/list/")


class DeleteListViewTest(TestCase):
    """Test the delete view."""

    def setUp(self):
        """Create a user, log in, and create a list."""
        self.client = Client()
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        self.collaborator_credentials = {
            "username": "collaborator",
            "password": "12345",
        }
        self.collaborator = get_user_model().objects.create_user(
            **self.collaborator_credentials,
        )
        self.list = CustomList.objects.create(name="Test List", owner=self.user)
        self.list.collaborators.add(self.collaborator)

    def test_delete_list(self):
        """Test deleting a list."""
        self.client.login(**self.credentials)
        self.client.post(reverse("list_delete"), {"list_id": self.list.id})
        self.assertEqual(CustomList.objects.count(), 0)

    def test_delete_list_collaborator(self):
        """Test deleting a list as a collaborator."""
        self.client.login(**self.collaborator_credentials)
        self.client.post(reverse("list_delete"), {"list_id": self.list.id})
        self.assertEqual(CustomList.objects.count(), 1)


class ReorderListItemViewTests(TestCase):
    """Tests for reordering items on a custom list."""

    def setUp(self):
        self.client = Client()
        self.owner = get_user_model().objects.create_user(
            username="owner",
            password="12345",
        )
        self.collaborator = get_user_model().objects.create_user(
            username="collab",
            password="12345",
        )
        self.outsider = get_user_model().objects.create_user(
            username="outsider",
            password="12345",
        )

        self.custom_list = CustomList.objects.create(
            name="Order Test",
            owner=self.owner,
        )
        self.custom_list.collaborators.add(self.collaborator)

        self.item_one = Item.objects.create(
            media_id="101",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="One",
        )
        self.item_two = Item.objects.create(
            media_id="102",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Two",
        )
        self.item_three = Item.objects.create(
            media_id="103",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Three",
        )

        custom_items = [
            CustomListItem.objects.create(
                custom_list=self.custom_list, item=self.item_one
            ),
            CustomListItem.objects.create(
                custom_list=self.custom_list, item=self.item_two
            ),
            CustomListItem.objects.create(
                custom_list=self.custom_list, item=self.item_three
            ),
        ]
        start = timezone.now().replace(microsecond=0)
        for offset, custom_item in enumerate(custom_items):
            custom_item.date_added = start + timedelta(seconds=offset)
        CustomListItem.objects.bulk_update(custom_items, ["date_added"])

    def test_owner_can_move_item_to_first(self):
        self.client.login(username="owner", password="12345")
        response = self.client.post(
            reverse("list_reorder_item", args=[self.custom_list.id]),
            {"item_id": self.item_three.id, "action": "first"},
        )
        self.assertEqual(response.status_code, 204)

        ordered_ids = list(
            CustomListItem.objects.filter(custom_list=self.custom_list)
            .order_by("date_added", "id")
            .values_list("item_id", flat=True),
        )
        self.assertEqual(
            ordered_ids,
            [self.item_three.id, self.item_one.id, self.item_two.id],
        )

    def test_collaborator_can_reorder_items(self):
        self.client.login(username="collab", password="12345")
        response = self.client.post(
            reverse("list_reorder_item", args=[self.custom_list.id]),
            {"item_id": self.item_one.id, "action": "last"},
        )
        self.assertEqual(response.status_code, 204)

    def test_outsider_cannot_reorder_items(self):
        self.client.login(username="outsider", password="12345")
        response = self.client.post(
            reverse("list_reorder_item", args=[self.custom_list.id]),
            {"item_id": self.item_two.id, "action": "first"},
        )
        self.assertEqual(response.status_code, 403)


class ListsModalViewTests(TestCase):
    """Tests for the lists_modal view."""

    def setUp(self):
        """Set up test data."""
        self.client = Client()
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        self.item = Item.objects.create(
            media_id="10494",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
        )

        # Create some test lists
        self.list1 = CustomList.objects.create(
            name="Test List 1",
            owner=self.user,
        )
        self.list2 = CustomList.objects.create(
            name="Test List 2",
            owner=self.user,
        )

    def test_lists_modal_json_form_model(self):
        """format=json returns the form model the React Lists form renders."""
        self.list1.tags = ["Movies", "Night"]
        self.list1.save()
        self.list1.items.add(self.item)
        url = reverse(
            "lists_modal",
            args=[Sources.TMDB.value, MediaTypes.MOVIE.value, 10494],
        )
        response = self.client.get(url, {"format": "json"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        data = response.json()
        self.assertEqual(data["itemId"], self.item.id)
        self.assertEqual(data["title"], "Test Movie")
        self.assertEqual(data["tags"], ["Movies", "Night"])
        self.assertEqual(data["toggleUrl"], reverse("list_item_toggle"))
        self.assertTrue(data["csrfToken"])
        by_name = {entry["name"]: entry for entry in data["lists"]}
        self.assertTrue(by_name["Test List 1"]["hasItem"])
        self.assertFalse(by_name["Test List 2"]["hasItem"])
        self.assertEqual(by_name["Test List 1"]["tags"], ["Movies", "Night"])

    def test_lists_modal_json_omits_smart_lists(self):
        """Smart lists cannot be edited by hand, so the form never offers them."""
        CustomList.objects.create(name="Smart", owner=self.user, is_smart=True)
        response = self.client.get(
            reverse(
                "lists_modal",
                args=[Sources.TMDB.value, MediaTypes.MOVIE.value, 10494],
            ),
            {"format": "json"},
        )
        names = [entry["name"] for entry in response.json()["lists"]]
        self.assertNotIn("Smart", names)

    def test_lists_modal_json_does_not_list_other_users_lists(self):
        """A person only sees the lists they own or collaborate on."""
        other = get_user_model().objects.create_user(username="other", password="x")
        CustomList.objects.create(name="Private", owner=other)
        response = self.client.get(
            reverse(
                "lists_modal",
                args=[Sources.TMDB.value, MediaTypes.MOVIE.value, 10494],
            ),
            {"format": "json"},
        )
        names = [entry["name"] for entry in response.json()["lists"]]
        self.assertNotIn("Private", names)

    def test_lists_modal_anonymous_is_redirected(self):
        """A signed-out request gets the login redirect, not a form model."""
        response = Client().get(
            reverse(
                "lists_modal",
                args=[Sources.TMDB.value, MediaTypes.MOVIE.value, 10494],
            ),
            {"format": "json"},
        )
        self.assertEqual(response.status_code, 302)

    def test_lists_modal_view(self):
        """Test the basic lists_modal view."""
        response = self.client.get(
            reverse(
                "lists_modal",
                args=[Sources.TMDB.value, MediaTypes.MOVIE.value, 10494],
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "lists/components/fill_lists.html")
        self.assertIn("item", response.context)
        self.assertIn("custom_lists", response.context)
        self.assertIn("list_tags", response.context)

    @patch("app.services.podcast_import.import_show_from_itunes_id")
    def test_lists_modal_resolves_podcast_itunes_search_result(self, mock_import):
        """A podcast search result's iTunes id adds the show under its uuid."""
        show = PodcastShow.objects.create(
            podcast_uuid="itunes:1532744254",
            title="Sternstunde Philosophie",
            image="http://example.com/podcast.jpg",
            rss_feed_url="",
        )
        mock_import.return_value = show

        response = self.client.get(
            reverse(
                "lists_modal",
                args=[
                    Sources.POCKETCASTS.value,
                    MediaTypes.PODCAST.value,
                    "1532744254",
                ],
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "lists/components/fill_lists.html")
        item = Item.objects.get(
            media_id=show.podcast_uuid,
            source=Sources.POCKETCASTS.value,
            media_type=MediaTypes.PODCAST.value,
        )
        self.assertEqual(response.context["item"], item)
        self.assertEqual(item.title, "Sternstunde Philosophie")
        mock_import.assert_called_once_with("1532744254")

    @patch("app.services.podcast_import.import_show_from_itunes_id")
    def test_quick_add_podcast_search_result_uses_show_uuid_once(self, mock_import):
        """Quick add files a searched podcast under its uuid, never twice."""
        show = PodcastShow.objects.create(
            podcast_uuid="itunes:1532744254",
            title="The Belles",
            image="http://example.com/podcast.jpg",
            rss_feed_url="",
        )
        mock_import.return_value = show
        custom_list = CustomList.objects.create(name="Pods", owner=self.user)
        data = {
            "media_id": "1532744254",
            "media_type": MediaTypes.PODCAST.value,
            "source": Sources.POCKETCASTS.value,
        }

        for _ in range(2):
            self.client.post(
                reverse("list_add_item_submit", args=[custom_list.id]),
                data,
            )

        item = Item.objects.get(
            media_id=show.podcast_uuid,
            media_type=MediaTypes.PODCAST.value,
        )
        self.assertFalse(Item.objects.filter(media_id="1532744254").exists())
        self.assertEqual(custom_list.items.filter(id=item.id).count(), 1)

    def test_list_detail_merges_stray_podcast_item_into_tracked_show(self):
        """A show filed under its iTunes id folds into the tracked show's Item."""
        show = PodcastShow.objects.create(
            podcast_uuid="itunes:1532744254",
            title="The Belles",
            image="http://example.com/podcast.jpg",
            rss_feed_url="",
        )
        kwargs = {
            "source": Sources.POCKETCASTS.value,
            "media_type": MediaTypes.PODCAST.value,
            "title": "The Belles",
            "image": "http://example.com/podcast.jpg",
        }
        real = Item.objects.create(media_id=show.podcast_uuid, **kwargs)
        stray = Item.objects.create(media_id="1532744254", **kwargs)
        custom_list = CustomList.objects.create(name="Pods", owner=self.user)
        CustomListItem.objects.create(custom_list=custom_list, item=real)
        CustomListItem.objects.create(custom_list=custom_list, item=stray)

        response = self.client.get(
            reverse("list_detail", args=[custom_list.public_reference]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Item.objects.filter(pk=stray.pk).exists())
        self.assertEqual(
            list(custom_list.items.values_list("pk", flat=True)),
            [real.pk],
        )

    def test_merge_stray_podcast_items_rekeys_when_show_item_missing(self):
        """Without a tracked Item the stray is re-keyed to the show's uuid."""
        from app.services.podcast_import import merge_stray_podcast_items

        show = PodcastShow.objects.create(
            podcast_uuid="itunes:42",
            title="Serial",
            image="",
            rss_feed_url="",
        )
        stray = Item.objects.create(
            media_id="42",
            source=Sources.POCKETCASTS.value,
            media_type=MediaTypes.PODCAST.value,
            title="Serial",
            image="",
        )
        unknown = Item.objects.create(
            media_id="999",
            source=Sources.POCKETCASTS.value,
            media_type=MediaTypes.PODCAST.value,
            title="Unknown",
            image="",
        )

        self.assertEqual(merge_stray_podcast_items(), 1)

        stray.refresh_from_db()
        unknown.refresh_from_db()
        self.assertEqual(stray.media_id, show.podcast_uuid)
        self.assertEqual(unknown.media_id, "999")

    @patch("app.providers.services.get_media_metadata")
    @patch("lists.models.CustomList.objects.get_user_lists_with_item")
    def test_lists_modal_view_with_existing_item(
        self,
        mock_get_lists,
        mock_get_metadata,
    ):
        """Test the lists_modal view with an existing item."""
        # Create an existing item
        Item.objects.create(
            media_id="123",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Existing Movie",
            image="http://example.com/image.jpg",
        )

        # Mock the get_user_lists_with_item method
        mock_get_lists.return_value = [self.list1, self.list2]

        # Mock the get_media_metadata method
        mock_get_metadata.return_value = {
            "title": "Existing Movie",
            "image": "http://example.com/image.jpg",
        }

        # Test the view
        response = self.client.get(
            reverse(
                "lists_modal",
                args=[Sources.TMDB.value, MediaTypes.MOVIE.value, "123"],
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "lists/components/fill_lists.html")

        # Check context data
        self.assertEqual(response.context["item"].media_id, "123")
        self.assertEqual(response.context["item"].title, "Existing Movie")
        self.assertEqual(len(response.context["custom_lists"]), 2)

    @patch("app.providers.services.get_media_metadata")
    @patch("lists.models.CustomList.objects.get_user_lists_with_item")
    def test_lists_modal_view_with_new_item(self, mock_get_lists, mock_get_metadata):
        """Test the lists_modal view with a new item."""
        # Mock the get_user_lists_with_item method
        mock_get_lists.return_value = [self.list1, self.list2]

        # Mock the get_media_metadata method
        mock_get_metadata.return_value = {
            "title": "New Movie",
            "image": "http://example.com/new_image.jpg",
        }

        # Test the view
        response = self.client.get(
            reverse(
                "lists_modal",
                args=[Sources.TMDB.value, MediaTypes.MOVIE.value, "999"],
            ),
        )
        self.assertEqual(response.status_code, 200)

        # Check that a new item was created
        self.assertTrue(
            Item.objects.filter(media_id="999", source=Sources.TMDB.value).exists(),
        )
        new_item = Item.objects.get(media_id="999", source=Sources.TMDB.value)
        self.assertEqual(new_item.title, "New Movie")
        self.assertEqual(new_item.image, "http://example.com/new_image.jpg")

    @patch("app.providers.services.get_media_metadata")
    @patch("lists.models.CustomList.objects.get_user_lists_with_item")
    def test_lists_modal_view_with_season(self, mock_get_lists, mock_get_metadata):
        """Test the lists_modal view with a season."""
        # Mock the get_user_lists_with_item method
        mock_get_lists.return_value = [self.list1, self.list2]

        # Mock the get_media_metadata method
        mock_get_metadata.return_value = {
            "title": "TV Show Season 1",
            "image": "http://example.com/season.jpg",
        }

        # Test the view
        response = self.client.get(
            reverse(
                "lists_modal",
                args=[Sources.TMDB.value, MediaTypes.SEASON.value, "123", "1"],
            ),
        )
        self.assertEqual(response.status_code, 200)

        # Check that a new item was created with season_number
        self.assertTrue(
            Item.objects.filter(
                media_id="123",
                source=Sources.TMDB.value,
                media_type=MediaTypes.SEASON.value,
                season_number=1,
            ).exists(),
        )

    @patch("app.providers.services.get_media_metadata")
    @patch("lists.models.CustomList.objects.get_user_lists_with_item")
    def test_lists_modal_view_with_episode_uses_episode_title(
        self,
        mock_get_lists,
        mock_get_metadata,
    ):
        """Episode list items should store the episode title instead of the show title."""
        mock_get_lists.return_value = [self.list1, self.list2]
        mock_get_metadata.return_value = {
            "title": "Death Note",
            "episode_title": "Rebirth",
            "image": "http://example.com/episode.jpg",
        }

        response = self.client.get(
            reverse(
                "lists_modal",
                args=[Sources.TMDB.value, MediaTypes.EPISODE.value, "13916", "0", "1"],
            ),
        )

        self.assertEqual(response.status_code, 200)
        created_item = Item.objects.get(
            media_id="13916",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=0,
            episode_number=1,
        )
        self.assertEqual(created_item.title, "Rebirth")

    @patch("app.providers.services.get_media_metadata")
    @patch("lists.models.CustomList.objects.get_user_lists_with_item")
    def test_lists_modal_view_with_existing_episode_repairs_episode_title(
        self,
        mock_get_lists,
        mock_get_metadata,
    ):
        """Existing tracked episode rows should be repaired before rendering the modal."""
        mock_get_lists.return_value = [self.list1, self.list2]
        existing_item = Item.objects.create(
            media_id="13916",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Death Note",
            season_number=0,
            episode_number=1,
            image=settings.IMG_NONE,
        )
        mock_get_metadata.return_value = {
            "title": "Death Note",
            "season_title": "Specials",
            "episodes": [
                {
                    "episode_number": 1,
                    "name": "Rebirth",
                    "image": settings.IMG_NONE,
                },
            ],
            "image": settings.IMG_NONE,
        }

        response = self.client.get(
            reverse(
                "lists_modal",
                args=[Sources.TMDB.value, MediaTypes.EPISODE.value, "13916", "0", "1"],
            ),
        )

        existing_item.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(existing_item.title, "Rebirth")

    @patch("app.providers.services.get_media_metadata")
    def test_lists_modal_music_route_creates_music_item(self, mock_get_metadata):
        """Music details list button should resolve to a track-backed Item row."""
        mock_get_metadata.return_value = {
            "title": "Neon Lights - Test Artist",
            "image": "https://example.com/music.jpg",
            "details": {},
        }

        media_id = "123e4567-e89b-12d3-a456-426614174000"
        response = self.client.get(
            reverse(
                "lists_modal",
                args=[Sources.MUSICBRAINZ.value, MediaTypes.MUSIC.value, media_id],
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            Item.objects.filter(
                media_id=media_id,
                source=Sources.MUSICBRAINZ.value,
                media_type=MediaTypes.MUSIC.value,
            ).exists(),
        )

    @patch("app.providers.services.get_media_metadata")
    def test_lists_modal_album_page_id_uses_stored_album(self, mock_get_metadata):
        """Issue #1575: an album page's id is not a recording, so ask no provider."""
        artist = Artist.objects.create(name="Alan Braxe")
        cases = {
            "release": Album(
                title="Running",
                artist=artist,
                image="https://example.com/running.jpg",
                musicbrainz_release_id="123e4567-e89b-12d3-a456-426614174001",
            ),
            "release group": Album(
                title="Group Only",
                artist=artist,
                musicbrainz_release_group_id="123e4567-e89b-12d3-a456-426614174002",
            ),
            "no MusicBrainz id": Album(title="Unmatched", artist=artist),
        }
        for label, album in cases.items():
            with self.subTest(label):
                album.save()
                media_id = (
                    album.musicbrainz_release_id
                    or album.musicbrainz_release_group_id
                    or f"album-{album.id}"
                )

                response = self.client.get(
                    reverse(
                        "lists_modal",
                        args=[
                            Sources.MUSICBRAINZ.value,
                            MediaTypes.MUSIC.value,
                            media_id,
                        ],
                    ),
                )

                self.assertEqual(response.status_code, 200)
                item = Item.objects.get(
                    media_id=media_id,
                    source=Sources.MUSICBRAINZ.value,
                    media_type=MediaTypes.MUSIC.value,
                )
                self.assertEqual(item.title, album.title)
                self.assertEqual(item.image, album.image or settings.IMG_NONE)
        mock_get_metadata.assert_not_called()

    @patch("app.providers.services.get_media_metadata")
    def test_album_item_can_be_added_to_list_and_opens_album_page(
        self,
        mock_get_metadata,
    ):
        """An album saved to a list shows there and links back to its album page."""
        artist = Artist.objects.create(name="Alan Braxe")
        album = Album.objects.create(
            title="Running",
            artist=artist,
            musicbrainz_release_id="123e4567-e89b-12d3-a456-426614174001",
        )
        media_id = album.musicbrainz_release_id
        self.client.get(
            reverse(
                "lists_modal",
                args=[Sources.MUSICBRAINZ.value, MediaTypes.MUSIC.value, media_id],
            ),
        )
        item = Item.objects.get(media_id=media_id, source=Sources.MUSICBRAINZ.value)

        response = self.client.post(
            reverse("list_item_toggle"),
            {"item_id": item.id, "custom_list_id": self.list1.id},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            CustomListItem.objects.filter(custom_list=self.list1, item=item).exists(),
        )

        response = self.client.get(
            reverse("list_detail", args=[self.list1.public_reference]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Running")

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.MUSICBRAINZ.value,
                    "media_type": MediaTypes.MUSIC.value,
                    "media_id": media_id,
                    "title": "running",
                },
            ),
        )
        self.assertRedirects(
            response,
            reverse(
                "music_album_details",
                kwargs={
                    "artist_id": artist.id,
                    "artist_slug": "alan-braxe",
                    "album_id": album.id,
                    "album_slug": "running",
                },
            ),
            fetch_redirect_response=False,
        )

        # The card's "Add to tracker" button opens the album's own tracking form.
        response = self.client.get(
            reverse(
                "track_modal",
                args=[Sources.MUSICBRAINZ.value, MediaTypes.MUSIC.value, media_id],
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("album_save"))
        mock_get_metadata.assert_not_called()

    def test_list_album_tracker_is_displayed_and_filterable(self):
        album = Album.objects.create(title="Tracked album", musicbrainz_release_id="album-test")
        item = Item.objects.create(
            title=album.title, media_type=MediaTypes.MUSIC.value,
            source=Sources.MUSICBRAINZ.value, media_id="album-test",
        )
        CustomListItem.objects.create(custom_list=self.list1, item=item)
        tracker = AlbumTracker.objects.create(
            user=self.user, album=album, status=Status.COMPLETED.value, score=8,
        )
        url = reverse("list_detail", args=[self.list1.public_reference])
        response = self.client.get(url, {"status": Status.COMPLETED.value})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, album.title)
        from lists.views_helpers import (
            _attach_media_with_aggregation,
            _get_completed_item_ids,
        )

        self.assertEqual(_get_completed_item_ids(self.user, {item.pk}), {item.pk})
        _attach_media_with_aggregation([item], self.user)
        self.assertEqual(item.media.pk, tracker.pk)
        self.assertEqual(item.media.score, 8)

        from api.serializers import serialize_data

        serialized = serialize_data(item.media)
        self.assertEqual(serialized["score"], 8.0)
        self.assertIsNone(serialized["consumption_id"])
        self.assertEqual(serialized["album_id"], album.pk)

    def test_list_album_tracker_resolves_duplicate_release_group(self):
        group_id = "123e4567-e89b-12d3-a456-426614174100"
        release_id = "123e4567-e89b-12d3-a456-426614174101"
        Album.objects.create(title="Album", musicbrainz_release_id=release_id, musicbrainz_release_group_id=group_id)
        tracked = Album.objects.create(title="Album", musicbrainz_release_group_id=group_id)
        item = Item.objects.create(title="Album", media_type=MediaTypes.MUSIC.value, source=Sources.MUSICBRAINZ.value, media_id=release_id)
        AlbumTracker.objects.create(user=self.user, album=tracked, status=Status.COMPLETED.value)
        from lists.views_helpers import (
            _attach_media_with_aggregation,
            _get_completed_item_ids,
        )

        _attach_media_with_aggregation([item], self.user)
        self.assertEqual(item.media.album_id, tracked.pk)
        self.assertEqual(_get_completed_item_ids(self.user, {item.pk}), {item.pk})
        other = get_user_model().objects.create_user(username="other")
        self.assertEqual(_get_completed_item_ids(other, {item.pk}), set())

    @patch("app.providers.services.get_media_metadata")
    def test_album_id_routes_resolve_to_users_canonical_duplicate(
        self,
        mock_get_metadata,
    ):
        """Duplicate rows for one release open the one the user already tracks."""
        release_id = "123e4567-e89b-12d3-a456-426614174003"
        untracked = Album.objects.create(
            title="Running",
            artist=Artist.objects.create(name="Alan Braxe"),
            musicbrainz_release_id=release_id,
        )
        tracked = Album.objects.create(
            title="Running",
            artist=Artist.objects.create(name="Alan Braxe & Fred Falke"),
            musicbrainz_release_id=release_id,
        )
        AlbumTracker.objects.create(
            user=self.user,
            album=tracked,
            status=Status.COMPLETED.value,
        )
        self.assertLess(untracked.id, tracked.id)

        response = self.client.get(
            reverse(
                "track_modal",
                args=[Sources.MUSICBRAINZ.value, MediaTypes.MUSIC.value, release_id],
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'value="{tracked.id}"')
        self.assertNotContains(response, f'value="{untracked.id}"')
        mock_get_metadata.assert_not_called()

    @patch("app.providers.services.get_media_metadata")
    def test_album_release_id_considers_duplicate_without_release_group(
        self,
        mock_get_metadata,
    ):
        """A tracked duplicate that lacks the group id still wins for its release id."""
        release_id = "123e4567-e89b-12d3-a456-426614174005"
        with_group = Album.objects.create(
            title="Running",
            artist=Artist.objects.create(name="Alan Braxe"),
            musicbrainz_release_id=release_id,
            musicbrainz_release_group_id="123e4567-e89b-12d3-a456-426614174006",
        )
        tracked = Album.objects.create(
            title="Running",
            artist=Artist.objects.create(name="Alan Braxe & Fred Falke"),
            musicbrainz_release_id=release_id,
        )
        AlbumTracker.objects.create(
            user=self.user,
            album=tracked,
            status=Status.COMPLETED.value,
        )
        self.assertLess(with_group.id, tracked.id)

        response = self.client.get(
            reverse(
                "track_modal",
                args=[Sources.MUSICBRAINZ.value, MediaTypes.MUSIC.value, release_id],
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'value="{tracked.id}"')
        self.assertNotContains(response, f'value="{with_group.id}"')
        mock_get_metadata.assert_not_called()

    @patch("app.providers.services.get_media_metadata")
    def test_anonymous_album_link_goes_to_album_page(self, mock_get_metadata):
        """A public list's album card reaches the album route, not the provider."""
        artist = Artist.objects.create(name="Alan Braxe")
        album = Album.objects.create(
            title="Running",
            artist=artist,
            musicbrainz_release_id="123e4567-e89b-12d3-a456-426614174004",
        )
        self.client.logout()

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.MUSICBRAINZ.value,
                    "media_type": MediaTypes.MUSIC.value,
                    "media_id": album.musicbrainz_release_id,
                    "title": "running",
                },
            ),
        )

        self.assertRedirects(
            response,
            reverse(
                "music_album_details",
                kwargs={
                    "artist_id": artist.id,
                    "artist_slug": "alan-braxe",
                    "album_id": album.id,
                    "album_slug": "running",
                },
            ),
            fetch_redirect_response=False,
        )
        mock_get_metadata.assert_not_called()

    def test_lists_modal_view_filters_lists_by_tag(self):
        """Tag query param should filter list options in the modal."""
        self.list1.tags = ["Active"]
        self.list1.save(update_fields=["tags"])
        self.list2.tags = ["Archive"]
        self.list2.save(update_fields=["tags"])

        response = self.client.get(
            reverse(
                "lists_modal",
                args=[Sources.TMDB.value, MediaTypes.MOVIE.value, 10494],
            )
            + "?tag=Active",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_list_tag"], "Active")
        self.assertEqual(list(response.context["custom_lists"]), [self.list1])
        self.assertContains(response, "Test List 1")
        self.assertNotContains(response, "Test List 2")

    def test_lists_modal_view_shows_no_count_when_not_on_any_list(self):
        """No count line should render when the item isn't on any list."""
        response = self.client.get(
            reverse(
                "lists_modal",
                args=[Sources.TMDB.value, MediaTypes.MOVIE.value, 10494],
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["on_list_count"], 0)
        self.assertNotContains(response, "On 0 lists")

    def test_lists_modal_view_shows_count_when_on_lists(self):
        """Count should reflect how many of the user's lists include the item."""
        CustomListItem.objects.create(custom_list=self.list1, item=self.item)
        CustomListItem.objects.create(custom_list=self.list2, item=self.item)

        response = self.client.get(
            reverse(
                "lists_modal",
                args=[Sources.TMDB.value, MediaTypes.MOVIE.value, 10494],
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["on_list_count"], 2)
        self.assertContains(response, "On 2 lists")

    def test_lists_modal_view_count_unaffected_by_tag_filter(self):
        """Count reflects total membership even when tag filtering narrows the list."""
        self.list1.tags = ["Active"]
        self.list1.save(update_fields=["tags"])
        self.list2.tags = ["Archive"]
        self.list2.save(update_fields=["tags"])
        CustomListItem.objects.create(custom_list=self.list1, item=self.item)
        CustomListItem.objects.create(custom_list=self.list2, item=self.item)

        response = self.client.get(
            reverse(
                "lists_modal",
                args=[Sources.TMDB.value, MediaTypes.MOVIE.value, 10494],
            )
            + "?tag=Active",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["custom_lists"]), 1)
        self.assertEqual(response.context["on_list_count"], 2)
        self.assertContains(response, "On 2 lists")


class ListItemToggleTests(TestCase):
    """Tests for the list_item_toggle view."""

    def setUp(self):
        """Set up test data."""
        self.client = Client()

        # Create users
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        self.collaborator_credentials = {
            "username": "collaborator",
            "password": "12345",
        }
        self.collaborator = get_user_model().objects.create_user(
            **self.collaborator_credentials,
        )

        self.other_credentials = {
            "username": "otheruser",
            "password": "testpassword",
        }
        self.other_user = get_user_model().objects.create_user(
            **self.other_credentials,
        )

        # Create lists
        self.list = CustomList.objects.create(name="Test List", owner=self.user)
        self.list.collaborators.add(self.collaborator)

        # Create an item
        self.item = Item.objects.create(
            media_id=1,
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
        )

    def test_list_item_owner_toggle(self):
        """Test adding an item to a list as owner."""
        self.client.login(**self.credentials)
        response = self.client.post(
            reverse("list_item_toggle"),
            {
                "item_id": self.item.id,
                "custom_list_id": self.list.id,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.item, self.list.items.all())

    def test_json_toggle_reports_membership(self):
        """format=json answers with the new membership instead of a button."""
        self.client.login(**self.credentials)
        post = {
            "item_id": self.item.id,
            "custom_list_id": self.list.id,
            "format": "json",
        }
        response = self.client.post(reverse("list_item_toggle"), post)
        self.assertEqual(response.json(), {"success": True, "hasItem": True})
        response = self.client.post(reverse("list_item_toggle"), post)
        self.assertEqual(response.json(), {"success": True, "hasItem": False})
        self.assertNotIn(self.item, self.list.items.all())

    def test_json_toggle_rejects_smart_list(self):
        """Smart lists answer 403 with a message the form can show."""
        self.client.login(**self.credentials)
        smart = CustomList.objects.create(
            name="Smart", owner=self.user, is_smart=True
        )
        response = self.client.post(
            reverse("list_item_toggle"),
            {"item_id": self.item.id, "custom_list_id": smart.id, "format": "json"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.json()["success"])

    def test_json_toggle_not_found_for_stranger(self):
        """A list the person cannot edit is a 404, as for the HTML button."""
        self.client.login(**self.other_credentials)
        response = self.client.post(
            reverse("list_item_toggle"),
            {
                "item_id": self.item.id,
                "custom_list_id": self.list.id,
                "format": "json",
            },
        )
        self.assertEqual(response.status_code, 404)

    def test_json_toggle_failure_returns_error_message(self):
        """A failed write returns JSON the form shows, with the list unchanged."""
        self.client.login(**self.credentials)
        with patch(
            "lists.views_list_actions._toggle_list_membership",
            side_effect=RuntimeError("boom"),
        ):
            response = self.client.post(
                reverse("list_item_toggle"),
                {
                    "item_id": self.item.id,
                    "custom_list_id": self.list.id,
                    "format": "json",
                },
            )
        self.assertEqual(response.status_code, 500)
        self.assertFalse(response.json()["success"])
        self.assertTrue(response.json()["error"])
        self.assertNotIn(self.item, self.list.items.all())

    def test_list_item_owner_toggle_remove(self):
        """Test removing an item from a list as owner."""
        self.client.login(**self.credentials)
        self.list.items.add(self.item)
        response = self.client.post(
            reverse("list_item_toggle"),
            {
                "item_id": self.item.id,
                "custom_list_id": self.list.id,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(self.item, self.list.items.all())

    def test_list_detail_page_refreshes_its_grid_after_list_item_toggle(self):
        """The list's own page must refresh its grid after a toggle.

        The toggle button's hx-swap replaces itself (hx-swap="outerHTML"),
        so an hx-on::after-request on that same button doesn't reliably fire
        — verified live: htmx doesn't rebind it once the element carrying it
        has replaced itself with its own response. Without some refresh,
        removing an item while viewing that list's own page leaves the
        item's card on screen even though it was removed from the DB —
        looking exactly like the toggle silently did nothing (reported:
        removing Daredevil: Born Again from a Watchlist custom list via the
        hover list-icon modal never made the card disappear).

        The working fix listens for htmx:afterRequest on the persistent body,
        filters to list_item_toggle, and replaces its prior handler after
        boosted navigation so revisiting a list cannot multiply refreshes.
        """
        self.client.login(**self.credentials)
        response = self.client.get(reverse("list_detail", args=[self.list.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "addEventListener('htmx:afterRequest'")
        self.assertContains(response, "endsWith('/list_item_toggle')")
        self.assertContains(response, "__floppyListToggleRefreshHandler")
        self.assertContains(response, "removeEventListener(")
        self.assertContains(response, "itemsView.isConnected")
        self.assertContains(response, "data-list-item-count", html=False)
        self.assertContains(response, "__floppyListCountUpdatedHandler")
        self.assertContains(response, "listCountUpdated")

    def test_list_item_toggle_response_no_longer_relies_on_self_swap_hx_on(self):
        """The toggle button must not re-introduce the non-firing hx-on hook.

        hx-on::after-request on a button that swaps itself out via
        hx-swap="outerHTML" was verified (via a live browser session) to
        never actually invoke its handler, even though the attribute renders
        correctly and the underlying htmx:afterRequest event does fire and
        bubble to the document. Guards against silently regressing back to
        that dead approach.
        """
        self.client.login(**self.credentials)
        response = self.client.post(
            reverse("list_item_toggle"),
            {
                "item_id": self.item.id,
                "custom_list_id": self.list.id,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "hx-on::after-request")

    def test_list_item_collaborator_toggle(self):
        """Test adding an item to a list as collaborator."""
        self.client.login(**self.collaborator_credentials)
        response = self.client.post(
            reverse("list_item_toggle"),
            {
                "item_id": self.item.id,
                "custom_list_id": self.list.id,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.item, self.list.items.all())

    def test_list_item_collaborator_toggle_remove(self):
        """Test removing an item from a list as collaborator."""
        self.client.login(**self.collaborator_credentials)
        self.list.items.add(self.item)
        response = self.client.post(
            reverse("list_item_toggle"),
            {
                "item_id": self.item.id,
                "custom_list_id": self.list.id,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(self.item, self.list.items.all())

    def test_list_item_toggle_nonexistent_list(self):
        """Test toggling an item on a nonexistent list."""
        self.client.login(**self.credentials)
        response = self.client.post(
            reverse("list_item_toggle"),
            {
                "item_id": self.item.id,
                "custom_list_id": 999,  # Nonexistent list
            },
        )
        self.assertEqual(response.status_code, 404)

    def test_list_item_toggle_nonexistent_item(self):
        """Test toggling a nonexistent item."""
        self.client.login(**self.credentials)
        response = self.client.post(
            reverse("list_item_toggle"),
            {
                "item_id": 999,  # Nonexistent item
                "custom_list_id": self.list.id,
            },
        )
        self.assertEqual(response.status_code, 404)

    def test_list_item_toggle_unauthorized_list(self):
        """Test toggling an item on a list the user doesn't have access to."""
        self.client.login(**self.credentials)

        # Create a list owned by another user
        other_list = CustomList.objects.create(
            name="Other User's List",
            owner=self.other_user,
        )

        response = self.client.post(
            reverse("list_item_toggle"),
            {
                "item_id": self.item.id,
                "custom_list_id": other_list.id,
            },
        )
        self.assertEqual(response.status_code, 404)

    def test_list_item_toggle_template_context(self):
        """Test the context data in the response template."""
        self.client.login(**self.credentials)
        response = self.client.post(
            reverse("list_item_toggle"),
            {
                "item_id": self.item.id,
                "custom_list_id": self.list.id,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "lists/components/list_item_button.html")

        # Check context data
        self.assertEqual(response.context["custom_list"], self.list)
        self.assertEqual(response.context["item"], self.item)
        self.assertTrue(response.context["has_item"])  # Item was added

        # Toggle again to remove
        response = self.client.post(
            reverse("list_item_toggle"),
            {
                "item_id": self.item.id,
                "custom_list_id": self.list.id,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["has_item"])  # Item was removed

    def test_list_item_toggle_remove_failure_logs_and_toasts_error(self):
        """An unexpected failure while removing must be logged and surfaced.

        Regression coverage for the class of bug that produced the real
        UniqueViolation crash: whatever throws here, the user must see an
        error toast (not a dead redirect) and the failure must land in the
        server logs with enough context to diagnose it, since the browser
        gives no useful detail on a bare 500.
        """
        self.client.login(**self.credentials)
        self.list.items.add(self.item)

        with (
            patch(
                "lists.models.CustomListItem.delete",
                side_effect=RuntimeError("boom"),
            ),
            self.assertLogs("lists.views_list_actions", level="ERROR") as logs,
        ):
            response = self.client.post(
                reverse("list_item_toggle"),
                {
                    "item_id": self.item.id,
                    "custom_list_id": self.list.id,
                },
            )

        self.assertEqual(response.status_code, 500)
        self.assertIn(str(self.item.id), "".join(logs.output))
        self.assertIn(str(self.list.id), "".join(logs.output))

        trigger = json.loads(response.headers["HX-Trigger"])
        self.assertEqual(trigger["showToast"]["type"], "error")
        self.assertIn("try again", trigger["showToast"]["message"])
        self.assertNotIn("listCountUpdated", trigger)

        # Nothing committed: the item is still in the list.
        self.assertIn(self.item, self.list.items.all())

    def test_list_item_toggle_add_failure_logs_and_toasts_error(self):
        """Same guarantee on the add path, not just remove."""
        self.client.login(**self.credentials)

        with (
            patch(
                "lists.models.CustomListItem.objects.create",
                side_effect=RuntimeError("boom"),
            ),
            self.assertLogs("lists.views_list_actions", level="ERROR"),
        ):
            response = self.client.post(
                reverse("list_item_toggle"),
                {
                    "item_id": self.item.id,
                    "custom_list_id": self.list.id,
                },
            )

        self.assertEqual(response.status_code, 500)
        trigger = json.loads(response.headers["HX-Trigger"])
        self.assertEqual(trigger["showToast"]["type"], "error")
        self.assertNotIn("listCountUpdated", trigger)

        # Nothing committed: the item was never added.
        self.assertNotIn(self.item, self.list.items.all())

    def _toggle_post(self):
        return self.client.post(
            reverse("list_item_toggle"),
            {"item_id": self.item.id, "custom_list_id": self.list.id},
        )

    @patch("lists.views_list_actions.time.sleep")
    @patch("lists.views_list_actions._toggle_list_membership")
    def test_list_item_toggle_retries_an_immediate_lock_error(
        self, mock_toggle, _sleep
    ):
        """A stale-snapshot lock error is instant, so a quick retry succeeds."""
        self.client.login(**self.credentials)
        mock_toggle.side_effect = [OperationalError("database is locked"), True]

        response = self._toggle_post()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_toggle.call_count, 2)

    @patch("lists.views_list_actions.time.sleep")
    @patch(
        "lists.views_list_actions._toggle_list_membership",
        side_effect=OperationalError("database is locked"),
    )
    def test_list_item_toggle_lock_failure_is_a_busy_toast_with_diagnostics(
        self,
        mock_toggle,
        _sleep,
    ):
        self.client.login(**self.credentials)

        with self.assertLogs("lists.views_list_actions", level="ERROR") as logs:
            response = self._toggle_post()

        self.assertEqual(response.status_code, 503)
        trigger = json.loads(response.headers["HX-Trigger"])
        self.assertEqual(trigger["showToast"]["type"], "error")
        self.assertIn("busy", trigger["showToast"]["message"])
        self.assertEqual(mock_toggle.call_count, 3)
        output = "".join(logs.output)
        self.assertIn("contention=True", output)
        self.assertIn("attempts=3", output)
        self.assertIn("elapsed_ms=", output)

    @patch("lists.views_list_actions.LIST_TOGGLE_QUICK_FAILURE_SECONDS", -1)
    @patch(
        "lists.views_list_actions._toggle_list_membership",
        side_effect=OperationalError("database is locked"),
    )
    def test_list_item_toggle_does_not_retry_a_lock_that_waited_out_the_timeout(
        self,
        mock_toggle,
    ):
        self.client.login(**self.credentials)

        with self.assertLogs("lists.views_list_actions", level="ERROR"):
            response = self._toggle_post()

        self.assertEqual(response.status_code, 503)
        self.assertEqual(mock_toggle.call_count, 1)

    def test_list_item_toggle_rolls_back_membership_when_activity_fails(self):
        """Membership and its activity record must commit or roll back together."""
        self.client.login(**self.credentials)

        with (
            patch(
                "lists.views_list_actions.ListActivity.objects.create",
                side_effect=RuntimeError("activity failed"),
            ),
            self.assertLogs("lists.views_list_actions", level="ERROR"),
        ):
            response = self.client.post(
                reverse("list_item_toggle"),
                {
                    "item_id": self.item.id,
                    "custom_list_id": self.list.id,
                },
            )

        self.assertEqual(response.status_code, 500)
        self.assertNotIn(self.item, self.list.items.all())

    def test_list_item_toggle_rolls_back_removal_when_activity_fails(self):
        """A failed removal activity must restore the deleted membership."""
        self.client.login(**self.credentials)
        self.list.items.add(self.item)

        with (
            patch(
                "lists.views_list_actions.ListActivity.objects.create",
                side_effect=RuntimeError("activity failed"),
            ),
            self.assertLogs("lists.views_list_actions", level="ERROR"),
        ):
            response = self.client.post(
                reverse("list_item_toggle"),
                {
                    "item_id": self.item.id,
                    "custom_list_id": self.list.id,
                },
            )

        self.assertEqual(response.status_code, 500)
        self.assertIn(self.item, self.list.items.all())


class ListRssFeedTests(TestCase):
    """Tests for the public list RSS feed."""

    def setUp(self):
        """Set up test data."""
        self.user = get_user_model().objects.create_user(
            username="rssuser",
            password="testpassword",
        )
        self.custom_list = CustomList.objects.create(
            name="Public RSS List",
            description="Test RSS list",
            owner=self.user,
            visibility="public",
        )
        self.movie_item = Item.objects.create(
            media_id="rss-1",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="RSS Movie",
            status="Released",
            image="https://example.com/rss-movie.jpg",
            manual_metadata={"synopsis": "Brick-built galactic co-op."},
        )
        with (
            patch(
                "app.models.providers.services.get_media_metadata",
                return_value={"max_progress": None},
            ),
            patch("app.models.Item.fetch_releases"),
        ):
            Game.objects.create(
                item=self.movie_item,
                user=self.user,
                status=Status.COMPLETED.value,
            )
        CustomListItem.objects.create(
            custom_list=self.custom_list,
            item=self.movie_item,
        )

    def test_public_list_rss_feed(self):
        """Return RSS feed for a public list."""
        response = self.client.get(reverse("list_rss", args=[self.custom_list.id]))
        root = ET.fromstring(response.content)
        namespaces = {"yamtrack": FloppyRssFeed.yamtrack_namespace}
        item = root.find("./channel/item")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"rss", response.content)
        self.assertIn(b"RSS Movie", response.content)
        self.assertIsNotNone(item)
        self.assertEqual(item.findtext("description"), "Brick-built galactic co-op.")
        self.assertEqual(
            item.findtext("yamtrack:status", namespaces=namespaces),
            Status.COMPLETED.value,
        )
        self.assertEqual(
            item.findtext("yamtrack:description", namespaces=namespaces),
            "Brick-built galactic co-op.",
        )
        self.assertEqual(
            item.findtext("yamtrack:image_url", namespaces=namespaces),
            "https://example.com/rss-movie.jpg",
        )

    def test_public_list_rss_feed_preserves_custom_list_order(self):
        """Return feed items in the list's persisted custom order."""
        second_item = Item.objects.create(
            media_id="rss-2",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="RSS Second Movie",
        )
        CustomListItem.objects.create(
            custom_list=self.custom_list,
            item=second_item,
        )
        CustomListItem.objects.filter(
            custom_list=self.custom_list,
            item=self.movie_item,
        ).update(date_added=timezone.now() + timedelta(minutes=1))

        response = self.client.get(reverse("list_rss", args=[self.custom_list.id]))
        root = ET.fromstring(response.content)
        titles = [item.findtext("title") for item in root.findall("./channel/item")]

        self.assertEqual(titles, ["RSS Second Movie", "RSS Movie"])

    def test_public_list_rss_feed_accepts_slug(self):
        """RSS feeds should resolve custom public slugs."""
        self.custom_list.public_slug = "public-rss-list"
        self.custom_list.save(update_fields=["public_slug"])

        response = self.client.get(
            reverse("list_rss", args=[self.custom_list.public_slug])
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"RSS Movie", response.content)

    def test_private_list_rss_feed_returns_404(self):
        """Return 404 for private lists."""
        self.custom_list.visibility = "private"
        self.custom_list.save(update_fields=["visibility"])

        response = self.client.get(reverse("list_rss", args=[self.custom_list.id]))

        self.assertEqual(response.status_code, 404)


class ListJsonExportTests(TestCase):
    """Tests for the public list JSON export endpoints."""

    def setUp(self):
        """Set up test data."""
        self.user = get_user_model().objects.create_user(
            username="jsonuser",
            password="testpassword",
        )
        self.custom_list = CustomList.objects.create(
            name="Public JSON List",
            description="Test JSON list",
            owner=self.user,
            visibility="public",
        )
        # Create TMDB movie
        self.movie_item = Item.objects.create(
            media_id="12345",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
        )
        CustomListItem.objects.create(
            custom_list=self.custom_list,
            item=self.movie_item,
        )
        # Create TMDB TV show
        self.tv_item = Item.objects.create(
            media_id="67890",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test TV Show",
        )
        CustomListItem.objects.create(
            custom_list=self.custom_list,
            item=self.tv_item,
        )
        # Create non-TMDB item (should be excluded)
        self.manual_item = Item.objects.create(
            media_id="manual-1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MOVIE.value,
            title="Manual Movie",
        )
        CustomListItem.objects.create(
            custom_list=self.custom_list,
            item=self.manual_item,
        )

    def test_radarr_json_format(self):
        """Return JSON in Radarr format for public list."""
        response = self.client.get(
            reverse("list_json", args=[self.custom_list.id]) + "?arr=radarr",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        data = response.json()
        self.assertIsInstance(data, list)
        # Should only include TMDB movies
        self.assertEqual(len(data), 1)
        self.assertIn({"id": 12345}, data)

    def test_radarr_json_preserves_custom_list_order(self):
        """Return public movie IDs in the list's persisted custom order."""
        second_movie = Item.objects.create(
            media_id="99999",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Second Movie",
        )
        CustomListItem.objects.create(
            custom_list=self.custom_list,
            item=second_movie,
        )
        CustomListItem.objects.filter(
            custom_list=self.custom_list,
            item=self.movie_item,
        ).update(date_added=timezone.now() + timedelta(minutes=1))

        response = self.client.get(
            reverse("list_json", args=[self.custom_list.id]) + "?arr=radarr",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [{"id": 99999}, {"id": 12345}])

    def test_radarr_json_accepts_slug(self):
        """JSON exports should resolve custom public slugs."""
        self.custom_list.public_slug = "public-json-list"
        self.custom_list.save(update_fields=["public_slug"])

        response = self.client.get(
            reverse("list_json", args=[self.custom_list.public_slug]) + "?arr=radarr",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [{"id": 12345}])

    def test_sonarr_json_format(self):
        """Return JSON in Sonarr format for public list."""
        # Mock TMDB TV metadata with TVDB ID
        from unittest.mock import patch

        mock_metadata = {
            "media_id": "67890",
            "title": "Test TV Show",
            "tvdb_id": 81189,
        }

        with patch("lists.feeds.tmdb.tv", return_value=mock_metadata):
            response = self.client.get(
                reverse("list_json", args=[self.custom_list.id]) + "?arr=sonarr",
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Content-Type"], "application/json")
            data = response.json()
            self.assertIsInstance(data, list)
            # Should only include TMDB TV shows with TVDB IDs
            self.assertEqual(len(data), 1)
            self.assertIn({"tvdbId": 81189}, data)

    def test_sonarr_skips_items_without_tvdb_id(self):
        """Sonarr endpoint skips TV shows without TVDB ID mapping."""
        from unittest.mock import patch

        # Mock TMDB TV metadata without TVDB ID
        mock_metadata = {
            "media_id": "67890",
            "title": "Test TV Show",
            "tvdb_id": None,
        }

        with patch("lists.feeds.tmdb.tv", return_value=mock_metadata):
            response = self.client.get(
                reverse("list_json", args=[self.custom_list.id]) + "?arr=sonarr",
            )

            self.assertEqual(response.status_code, 200)
            data = response.json()
            # Should be empty since TV show has no TVDB ID
            self.assertEqual(len(data), 0)

    def test_private_list_json_returns_404(self):
        """Return 404 for private lists."""
        self.custom_list.visibility = "private"
        self.custom_list.save(update_fields=["visibility"])

        response = self.client.get(
            reverse("list_json", args=[self.custom_list.id]) + "?arr=radarr",
        )

        self.assertEqual(response.status_code, 404)

    def test_json_filters_by_media_type(self):
        """JSON endpoints filter items by media type correctly."""
        # Radarr should only return movies
        response = self.client.get(
            reverse("list_json", args=[self.custom_list.id]) + "?arr=radarr",
        )
        data = response.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["id"], 12345)

        # Sonarr should only return TV shows
        from unittest.mock import patch

        mock_metadata = {
            "media_id": "67890",
            "title": "Test TV Show",
            "tvdb_id": 81189,
        }

        with patch("lists.feeds.tmdb.tv", return_value=mock_metadata):
            response = self.client.get(
                reverse("list_json", args=[self.custom_list.id]) + "?arr=sonarr",
            )
            data = response.json()
            self.assertEqual(len(data), 1)
            self.assertIn("tvdbId", data[0])

    def test_json_only_includes_tmdb_items(self):
        """JSON endpoints only include items from TMDB source."""
        response = self.client.get(
            reverse("list_json", args=[self.custom_list.id]) + "?arr=radarr",
        )
        data = response.json()
        # Should not include manual item
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["id"], 12345)

    def test_missing_arr_parameter_returns_error(self):
        """Missing arr parameter returns 400 error."""
        response = self.client.get(reverse("list_json", args=[self.custom_list.id]))

        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("error", data)

    def test_invalid_arr_parameter_returns_error(self):
        """Invalid arr parameter returns 400 error."""
        response = self.client.get(
            reverse("list_json", args=[self.custom_list.id]) + "?arr=invalid",
        )

        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("error", data)


class RecommendationRedirectTests(TestCase):
    """Tests for recommendation flow redirect behavior."""

    def setUp(self):
        self.client = Client()
        self.custom_list = CustomList.objects.create(
            name="Public Recs",
            owner=get_user_model().objects.create_user(
                "owner", "owner@example.com", "pw"
            ),
            visibility="public",
            allow_recommendations=True,
        )
        self.item = Item.objects.create(
            media_id="100",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Recommendation Target",
            image="https://example.com/poster.jpg",
        )

    def test_submit_recommendation_redirects_to_next_search_page(self):
        """Recommendation submit should preserve recommendation search page via next."""
        next_url = f"{reverse('recommend_item', args=[self.custom_list.id])}?q=dark&media_type=movie&page=2"
        response = self.client.post(
            reverse("submit_recommendation", args=[self.custom_list.id]),
            {
                "media_id": self.item.media_id,
                "media_type": self.item.media_type,
                "source": self.item.source,
                "next": next_url,
            },
        )

        self.assertRedirects(response, next_url, fetch_redirect_response=False)

    def test_submit_recommendation_ignores_external_next_url(self):
        """External next URLs should be rejected for security."""
        response = self.client.post(
            reverse("submit_recommendation", args=[self.custom_list.id]),
            {
                "media_id": self.item.media_id,
                "media_type": self.item.media_type,
                "source": self.item.source,
                "next": "https://evil.example/path",
            },
        )

        self.assertRedirects(
            response,
            reverse("list_detail", args=[self.custom_list.id]),
            fetch_redirect_response=False,
        )

    @patch("lists.views.services.search")
    def test_recommend_search_uses_track_results_from_music_combined_payload(
        self,
        mock_search,
    ):
        """Recommendation search should render nested music track hits."""
        mock_search.return_value = {
            "artists": [{"artist_id": "artist-1", "name": "Test Artist"}],
            "releases": [{"release_id": "release-1", "title": "Test Album"}],
            "tracks": {
                "results": [
                    {
                        "media_id": "recording-1",
                        "source": Sources.MUSICBRAINZ.value,
                        "media_type": MediaTypes.MUSIC.value,
                        "title": "Recommendation Track - Test Artist",
                        "image": "https://example.com/track.jpg",
                    },
                ],
                "total_pages": 2,
            },
        }

        response = self.client.get(
            reverse("recommend_search", args=[self.custom_list.id]),
            {
                "q": "test",
                "media_type": MediaTypes.MUSIC.value,
                "page": 1,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Recommendation Track - Test Artist")
        self.assertContains(response, "Showing page 1 of 2")


class QuickAddListItemTests(TestCase):
    """Tests for the owner quick-add list search flow."""

    def setUp(self):
        self.client = Client()
        self.user = get_user_model().objects.create_user(
            "owner", "owner@example.com", "pw"
        )
        self.client.force_login(self.user)
        self.custom_list = CustomList.objects.create(
            name="Manual List",
            owner=self.user,
        )
        self.smart_list = CustomList.objects.create(
            name="Smart List",
            owner=self.user,
            is_smart=True,
        )
        self.item = Item.objects.create(
            media_id="100",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Quick Add Target",
            image="https://example.com/poster.jpg",
        )

    def test_add_list_item_page_uses_add_template(self):
        """Editable manual lists should render the quick-add search page."""
        response = self.client.get(reverse("list_add_item", args=[self.custom_list.id]))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "lists/add_item.html")

    def test_add_list_item_page_redirects_for_smart_lists(self):
        """Smart lists should redirect back to detail instead of opening quick add."""
        response = self.client.get(reverse("list_add_item", args=[self.smart_list.id]))

        self.assertRedirects(
            response,
            reverse("list_detail", args=[self.smart_list.id]),
            fetch_redirect_response=False,
        )

    @patch("lists.views.services.get_media_metadata")
    def test_add_list_item_search_preview_renders_owner_add_modal(
        self, mock_get_metadata
    ):
        """Preview requests should use the direct-add modal instead of recommendations."""
        mock_get_metadata.return_value = {
            "title": "Preview Movie",
            "image": "https://example.com/preview.jpg",
            "details": {},
            "genres": [],
            "synopsis": "",
        }

        response = self.client.get(
            reverse("list_add_item_search", args=[self.custom_list.id]),
            {
                "show_preview": "true",
                "media_id": self.item.media_id,
                "media_type": self.item.media_type,
                "source": self.item.source,
                "q": "dark",
                "search_media_type": self.item.media_type,
                "page": 2,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response, "lists/components/add_item_preview_modal.html"
        )
        self.assertContains(
            response, reverse("list_add_item_submit", args=[self.custom_list.id])
        )
        self.assertContains(
            response,
            f"{reverse('list_add_item', args=[self.custom_list.id])}?q=dark&amp;media_type=movie&amp;page=2",
        )

    @patch("lists.views.services.get_media_metadata")
    def test_add_list_item_search_preview_preserves_episode_identity_fields(
        self,
        mock_get_metadata,
    ):
        """Episode preview forms should keep season/episode identity on submit."""
        mock_get_metadata.return_value = {
            "title": "Death Note",
            "episode_title": "Rebirth",
            "image": "https://example.com/episode.jpg",
            "details": {},
            "genres": [],
            "synopsis": "",
        }

        response = self.client.get(
            reverse("list_add_item_search", args=[self.custom_list.id]),
            {
                "show_preview": "true",
                "media_id": "1668",
                "media_type": MediaTypes.EPISODE.value,
                "source": Sources.TMDB.value,
                "season_number": 1,
                "episode_number": 1,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="season_number" value="1"', html=False)
        self.assertContains(response, 'name="episode_number" value="1"', html=False)

    def test_add_list_item_submit_redirects_to_next_search_page(self):
        """Quick-add submit should preserve the search page via next."""
        next_url = (
            f"{reverse('list_add_item', args=[self.custom_list.id])}"
            "?q=dark&media_type=movie&page=2"
        )
        response = self.client.post(
            reverse("list_add_item_submit", args=[self.custom_list.id]),
            {
                "media_id": self.item.media_id,
                "media_type": self.item.media_type,
                "source": self.item.source,
                "next": next_url,
            },
        )

        self.assertRedirects(response, next_url, fetch_redirect_response=False)
        self.assertTrue(
            CustomListItem.objects.filter(
                custom_list=self.custom_list, item=self.item
            ).exists(),
        )
        self.assertTrue(
            ListActivity.objects.filter(
                custom_list=self.custom_list,
                item=self.item,
            ).exists(),
        )

    @patch("lists.views.services.get_media_metadata")
    def test_add_list_item_submit_creates_episode_item_with_identity_fields(
        self,
        mock_get_metadata,
    ):
        """Episode quick-add submits should create the episode-specific Item row."""
        mock_get_metadata.return_value = {
            "title": "Death Note",
            "episode_title": "Rebirth",
            "image": "https://example.com/episode.jpg",
            "details": {},
            "genres": [],
            "synopsis": "",
        }
        next_url = (
            f"{reverse('list_add_item', args=[self.custom_list.id])}?q=death+note"
        )

        response = self.client.post(
            reverse("list_add_item_submit", args=[self.custom_list.id]),
            {
                "media_id": "1668",
                "media_type": MediaTypes.EPISODE.value,
                "source": Sources.TMDB.value,
                "season_number": 1,
                "episode_number": 1,
                "next": next_url,
            },
        )

        episode_item = Item.objects.get(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=1,
        )

        self.assertRedirects(response, next_url, fetch_redirect_response=False)
        self.assertEqual(episode_item.title, "Rebirth")
        self.assertTrue(
            CustomListItem.objects.filter(
                custom_list=self.custom_list,
                item=episode_item,
            ).exists(),
        )

    def test_add_list_item_submit_ignores_external_next_url(self):
        """External next URLs should be rejected for security."""
        response = self.client.post(
            reverse("list_add_item_submit", args=[self.custom_list.id]),
            {
                "media_id": self.item.media_id,
                "media_type": self.item.media_type,
                "source": self.item.source,
                "next": "https://evil.example/path",
            },
        )

        self.assertRedirects(
            response,
            reverse("list_detail", args=[self.custom_list.id]),
            fetch_redirect_response=False,
        )

    def _create_show_in_two_buckets(self):
        """Create one TV identity stored as both a 'tv' and a 'season' row (#1530)."""
        identity = {
            "media_id": "1396",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "title": "Breaking Bad",
            "image": "https://example.com/bb.jpg",
        }
        other_user = get_user_model().objects.create_user(
            "other", "o@example.com", "pw"
        )
        other_row = Item.objects.create(
            library_media_type=MediaTypes.TV.value, **identity
        )
        mine = Item.objects.create(
            library_media_type=MediaTypes.SEASON.value, **identity
        )
        TV.objects.create(item=other_row, user=other_user, status="Planning")
        TV.objects.create(item=mine, user=self.user, status="Planning")
        return identity, mine

    def test_add_list_item_submit_survives_item_in_two_library_buckets(self):
        """A title stored in two buckets adds the user's own row instead of a 500."""
        identity, mine = self._create_show_in_two_buckets()

        response = self.client.post(
            reverse("list_add_item_submit", args=[self.custom_list.id]),
            {
                "media_id": identity["media_id"],
                "media_type": identity["media_type"],
                "source": identity["source"],
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            list(self.custom_list.items.values_list("id", flat=True)),
            [mine.id],
        )
        self.assertEqual(Item.objects.filter(media_id="1396").count(), 2)

    def test_add_list_item_submit_rejects_title_already_in_list_via_other_bucket(self):
        """The other bucket's row already on the list must block a second add."""
        identity, mine = self._create_show_in_two_buckets()
        other_row = Item.objects.get(
            media_id=identity["media_id"],
            library_media_type=MediaTypes.TV.value,
        )
        CustomListItem.objects.create(custom_list=self.custom_list, item=other_row)

        self.client.post(
            reverse("list_add_item_submit", args=[self.custom_list.id]),
            {
                "media_id": identity["media_id"],
                "media_type": identity["media_type"],
                "source": identity["source"],
            },
        )

        self.assertEqual(
            list(self.custom_list.items.values_list("id", flat=True)),
            [other_row.id],
        )

    def test_recommendation_item_survives_item_in_two_library_buckets(self):
        """Recommending a title stored in two buckets reuses a row, not a crash."""
        identity, mine = self._create_show_in_two_buckets()

        item = get_or_create_recommendation_item(
            identity["media_id"],
            identity["media_type"],
            identity["source"],
            user=self.user,
        )

        self.assertEqual(item, mine)
        self.assertEqual(Item.objects.filter(media_id="1396").count(), 2)

    @patch("lists.views.services.search")
    def test_add_list_item_search_uses_track_results_from_music_combined_payload(
        self,
        mock_search,
    ):
        """Music quick-add search should render nested track hits from combined payloads."""
        mock_search.return_value = {
            "artists": [{"artist_id": "artist-1", "name": "Test Artist"}],
            "releases": [{"release_id": "release-1", "title": "Test Album"}],
            "tracks": {
                "results": [
                    {
                        "media_id": "recording-1",
                        "source": Sources.MUSICBRAINZ.value,
                        "media_type": MediaTypes.MUSIC.value,
                        "title": "Test Track - Test Artist",
                        "image": "https://example.com/track.jpg",
                    },
                ],
                "total_pages": 3,
            },
        }

        response = self.client.get(
            reverse("list_add_item_search", args=[self.custom_list.id]),
            {
                "q": "test",
                "media_type": MediaTypes.MUSIC.value,
                "page": 1,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Track - Test Artist")
        self.assertContains(response, "Showing page 1 of 3")


class ListRecommendationsViewTests(TestCase):
    """Tests for the list recommendations queue."""

    def setUp(self):
        """Create a list owner with one tracked and one untracked recommendation."""
        self.credentials = {"username": "owner", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.custom_list = CustomList.objects.create(name="Queue", owner=self.user)
        self.tracked_item = Item.objects.create(
            media_id="rec-tracked",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Tracked Recommendation",
        )
        self.untracked_item = Item.objects.create(
            media_id="rec-untracked",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Untracked Recommendation",
        )
        ListRecommendation.objects.create(
            custom_list=self.custom_list,
            item=self.tracked_item,
            anonymous_name="Friend",
        )
        ListRecommendation.objects.create(
            custom_list=self.custom_list,
            item=self.untracked_item,
            anonymous_name="Friend",
        )

    def test_card_shows_viewer_rating_and_status_for_tracked_items(self):
        """A recommended item the owner already tracks shows their rating and status."""
        movie = Movie.objects.create(
            item=self.tracked_item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=1,
            score=9,
        )
        self.client.login(**self.credentials)

        response = self.client.get(
            reverse("list_recommendations", args=[self.custom_list.id]),
        )

        content = response.content.decode()
        self.assertIn("Untracked Recommendation", content)
        models = {m["ratingId"]: m for m in card_models(content)}
        tracked = models[item_slot_id("media-card-rating", movie.item)]
        self.assertEqual(tracked["statusId"], item_slot_id("media-status-chip", movie.item))
        self.assertEqual(sum(1 for m in models.values() if m["statusChip"]), 1)
