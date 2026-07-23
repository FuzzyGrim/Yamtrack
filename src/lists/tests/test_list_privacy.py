from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.models import (
    Item,
    MediaTypes,
    Movie,
    Sources,
    Status,
)
from lists.models import CustomList, CustomListItem


class ListPrivacyMediaVisibilityTests(TestCase):
    """Test that items in private lists are hidden from public views.

    Rules:
    - Item in public list only → visible to all
    - Item in private list only → visible only to owner
    - Item in both public and private lists → NOT visible to public (private has priority)
    - Item in no list → visible to all (public by default)
    """

    def setUp(self):
        """Create owner, viewer, items, and lists."""
        self.patcher = patch("app.providers.services.get_media_metadata")
        self.mock_get_media = self.patcher.start()
        self.mock_get_media.return_value = {"max_progress": None}

        self.owner_creds = {"username": "owner", "password": "12345"}
        self.viewer_creds = {"username": "viewer", "password": "12345"}
        self.owner = get_user_model().objects.create_user(**self.owner_creds)
        self.viewer = get_user_model().objects.create_user(**self.viewer_creds)
        self.owner.profile_private = False
        self.owner.save(update_fields=["profile_private"])

        # Items
        self.item_public_only = Item.objects.create(
            media_id="1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Public Only Movie",
            image="http://example.com/1.jpg",
        )
        self.item_private_only = Item.objects.create(
            media_id="2",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Private Only Movie",
            image="http://example.com/2.jpg",
        )
        self.item_both = Item.objects.create(
            media_id="3",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Both Lists Movie",
            image="http://example.com/3.jpg",
        )
        self.item_no_list = Item.objects.create(
            media_id="4",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="No List Movie",
            image="http://example.com/4.jpg",
        )

        # Media instances for owner
        self.movie_public = Movie.objects.create(
            item=self.item_public_only,
            user=self.owner,
            status=Status.IN_PROGRESS.value,
            score=8,
        )
        self.movie_private = Movie.objects.create(
            item=self.item_private_only,
            user=self.owner,
            status=Status.IN_PROGRESS.value,
            score=7,
        )
        self.movie_both = Movie.objects.create(
            item=self.item_both,
            user=self.owner,
            status=Status.IN_PROGRESS.value,
            score=9,
        )
        self.movie_no_list = Movie.objects.create(
            item=self.item_no_list,
            user=self.owner,
            status=Status.PLANNING.value,
            score=6,
        )

        # Lists
        self.public_list = CustomList.objects.create(
            name="Public List",
            owner=self.owner,
            is_public=True,
        )
        self.private_list = CustomList.objects.create(
            name="Private List",
            owner=self.owner,
            is_public=False,
        )

        # Assign items to lists
        CustomListItem.objects.create(
            custom_list=self.public_list,
            item=self.item_public_only,
        )
        CustomListItem.objects.create(
            custom_list=self.private_list,
            item=self.item_private_only,
        )
        # Item in both lists
        CustomListItem.objects.create(
            custom_list=self.public_list,
            item=self.item_both,
        )
        CustomListItem.objects.create(
            custom_list=self.private_list,
            item=self.item_both,
        )

    def tearDown(self):
        self.patcher.stop()

    def test_public_list_item_visible_to_viewer(self):
        """Item in public list only is visible to other users."""
        self.client.login(**self.viewer_creds)
        response = self.client.get(
            reverse("medialist", args=[self.owner.username, MediaTypes.MOVIE.value]),
        )
        self.assertEqual(response.status_code, 200)
        titles = [m.item.title for m in response.context["media_list"]]
        self.assertIn("Public Only Movie", titles)

    def test_private_list_item_hidden_from_viewer(self):
        """Item in private list only is hidden from other users."""
        self.client.login(**self.viewer_creds)
        response = self.client.get(
            reverse("medialist", args=[self.owner.username, MediaTypes.MOVIE.value]),
        )
        self.assertEqual(response.status_code, 200)
        titles = [m.item.title for m in response.context["media_list"]]
        self.assertNotIn("Private Only Movie", titles)

    def test_item_in_both_lists_hidden_from_viewer(self):
        """Item in both public and private lists is hidden (private has priority)."""
        self.client.login(**self.viewer_creds)
        response = self.client.get(
            reverse("medialist", args=[self.owner.username, MediaTypes.MOVIE.value]),
        )
        self.assertEqual(response.status_code, 200)
        titles = [m.item.title for m in response.context["media_list"]]
        self.assertNotIn("Both Lists Movie", titles)

    def test_no_list_item_visible_to_viewer(self):
        """Item in no list is visible to other users (public by default)."""
        self.client.login(**self.viewer_creds)
        response = self.client.get(
            reverse("medialist", args=[self.owner.username, MediaTypes.MOVIE.value]),
        )
        self.assertEqual(response.status_code, 200)
        titles = [m.item.title for m in response.context["media_list"]]
        self.assertIn("No List Movie", titles)

    def test_owner_sees_all_items(self):
        """Owner sees all their items regardless of list privacy."""
        self.client.login(**self.owner_creds)
        response = self.client.get(
            reverse("medialist", args=[self.owner.username, MediaTypes.MOVIE.value]),
        )
        self.assertEqual(response.status_code, 200)
        titles = [m.item.title for m in response.context["media_list"]]
        self.assertIn("Public Only Movie", titles)
        self.assertIn("Private Only Movie", titles)
        self.assertIn("Both Lists Movie", titles)
        self.assertIn("No List Movie", titles)
        self.assertEqual(response.context["media_list"].paginator.count, 4)


class ListPrivacyPublicProfileTests(TestCase):
    """Test that private list items are hidden from the public profile page."""

    def setUp(self):
        """Create owner, viewer, items, and lists."""
        self.patcher = patch("app.providers.services.get_media_metadata")
        self.mock_get_media = self.patcher.start()
        self.mock_get_media.return_value = {"max_progress": None}

        self.owner_creds = {"username": "owner", "password": "12345"}
        self.viewer_creds = {"username": "viewer", "password": "12345"}
        self.owner = get_user_model().objects.create_user(**self.owner_creds)
        self.viewer = get_user_model().objects.create_user(**self.viewer_creds)
        self.owner.profile_private = False
        self.owner.save(update_fields=["profile_private"])

        self.item_public = Item.objects.create(
            media_id="1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Public Movie",
            image="http://example.com/1.jpg",
        )
        self.item_private = Item.objects.create(
            media_id="2",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Private Movie",
            image="http://example.com/2.jpg",
        )

        Movie.objects.create(
            item=self.item_public,
            user=self.owner,
            status=Status.IN_PROGRESS.value,
        )
        Movie.objects.create(
            item=self.item_private,
            user=self.owner,
            status=Status.IN_PROGRESS.value,
        )

        self.public_list = CustomList.objects.create(
            name="Public List",
            owner=self.owner,
            is_public=True,
        )
        self.private_list = CustomList.objects.create(
            name="Private List",
            owner=self.owner,
            is_public=False,
        )
        CustomListItem.objects.create(
            custom_list=self.public_list,
            item=self.item_public,
        )
        CustomListItem.objects.create(
            custom_list=self.private_list,
            item=self.item_private,
        )

    def tearDown(self):
        self.patcher.stop()

    def test_public_profile_hides_private_items(self):
        """Private list items don't appear in public profile stats."""
        self.client.login(**self.viewer_creds)
        response = self.client.get(
            reverse("public_profile", args=[self.owner.username]),
        )
        self.assertEqual(response.status_code, 200)
        # Total movies should only count the public item
        self.assertEqual(response.context["media_count"].get("movie", 0), 1)

    def test_public_profile_owner_sees_all(self):
        """Owner sees all their items on their own profile."""
        self.client.login(**self.owner_creds)
        response = self.client.get(
            reverse("public_profile", args=[self.owner.username]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["media_count"].get("movie", 0), 2)

    def test_public_profile_anonymous_hides_private_items(self):
        """Anonymous users don't see private list items."""
        self.client.logout()
        response = self.client.get(
            reverse("public_profile", args=[self.owner.username]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["media_count"].get("movie", 0), 1)


class ListPrivacyGetPrivateItemIdsTests(TestCase):
    """Test the get_private_item_ids manager method."""

    def setUp(self):
        """Create user, items, and lists."""
        self.user = get_user_model().objects.create_user(
            username="test",
            password="12345",
        )

        self.item1 = Item.objects.create(
            media_id="1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Item 1",
        )
        self.item2 = Item.objects.create(
            media_id="2",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Item 2",
        )
        self.item3 = Item.objects.create(
            media_id="3",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Item 3",
        )

        self.public_list = CustomList.objects.create(
            name="Public",
            owner=self.user,
            is_public=True,
        )
        self.private_list = CustomList.objects.create(
            name="Private",
            owner=self.user,
            is_public=False,
        )

        CustomListItem.objects.create(
            custom_list=self.public_list,
            item=self.item1,
        )
        CustomListItem.objects.create(
            custom_list=self.private_list,
            item=self.item2,
        )
        CustomListItem.objects.create(
            custom_list=self.private_list,
            item=self.item3,
        )

    def test_returns_only_private_list_item_ids(self):
        """get_private_item_ids returns IDs from private lists only."""
        private_ids = list(CustomList.objects.get_private_item_ids(self.user))
        self.assertIn(self.item2.id, private_ids)
        self.assertIn(self.item3.id, private_ids)
        self.assertNotIn(self.item1.id, private_ids)

    def test_empty_for_user_with_no_private_lists(self):
        """Returns empty for user with no private lists."""
        other_user = get_user_model().objects.create_user(
            username="other",
            password="12345",
        )
        private_ids = list(CustomList.objects.get_private_item_ids(other_user))
        self.assertEqual(private_ids, [])

    def test_items_in_both_lists_are_in_private_ids(self):
        """Items in both public and private lists are in private_ids."""
        item_both = Item.objects.create(
            media_id="4",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Both",
        )
        CustomListItem.objects.create(
            custom_list=self.public_list,
            item=item_both,
        )
        CustomListItem.objects.create(
            custom_list=self.private_list,
            item=item_both,
        )
        private_ids = list(CustomList.objects.get_private_item_ids(self.user))
        self.assertIn(item_both.id, private_ids)
