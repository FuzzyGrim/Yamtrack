from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.models import (
    Episode,
    Item,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Status,
    TV,
)
from lists.models import CustomList, CustomListItem


class ListPrivacyMediaVisibilityTests(TestCase):
    """Test that items in private lists are hidden from public views.

    Rules:
    - Item in public list only → visible to all
    - Item in private list only → visible only to owner
    - Item in both public and private lists → visible (public list takes precedence)
    - Item in no list → visible to all (public by default)
    - Tracking status does not affect privacy
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

    def test_tracked_private_list_item_hidden_from_viewer(self):
        """Item in private list only is hidden from other users even when tracked."""
        self.client.login(**self.viewer_creds)
        response = self.client.get(
            reverse("medialist", args=[self.owner.username, MediaTypes.MOVIE.value]),
        )
        self.assertEqual(response.status_code, 200)
        titles = [m.item.title for m in response.context["media_list"]]
        self.assertNotIn("Private Only Movie", titles)

    def test_item_in_both_lists_visible_to_viewer(self):
        """Item in both public and private lists is visible (public list takes precedence)."""
        self.client.login(**self.viewer_creds)
        response = self.client.get(
            reverse("medialist", args=[self.owner.username, MediaTypes.MOVIE.value]),
        )
        self.assertEqual(response.status_code, 200)
        titles = [m.item.title for m in response.context["media_list"]]
        self.assertIn("Both Lists Movie", titles)

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

    def test_items_in_both_lists_are_excluded_from_private_ids(self):
        """Items in both public and private lists are not in private_ids (public takes precedence)."""
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
        self.assertNotIn(item_both.id, private_ids)

    def test_tracked_items_still_in_private_ids(self):
        """Items with tracking records are still in private_ids (tracking doesn't affect privacy)."""
        with patch(
            "app.providers.services.get_media_metadata",
            return_value={"max_progress": None},
        ):
            Movie.objects.create(
                item=self.item2,
                user=self.user,
                status=Status.IN_PROGRESS.value,
            )
        private_ids = list(CustomList.objects.get_private_item_ids(self.user))
        self.assertIn(self.item2.id, private_ids)
        self.assertIn(self.item3.id, private_ids)


def _mock_get_media_metadata(media_type, *args, **kwargs):
    """Mock get_media_metadata to handle both regular and tv_with_seasons calls."""
    if media_type == "tv_with_seasons":
        season_number = args[2][0] if args[2] else 1
        return {
            f"season/{season_number}": {
                "episodes": [{"episode_number": i} for i in range(1, 20)],
            },
        }
    return {"max_progress": None}


class ListPrivacyTVShowExpansionTests(TestCase):
    """Test that TV show privacy expands to all seasons and episodes."""

    def setUp(self):
        """Create user, TV items, season items, episode items, and lists."""
        self.patcher = patch("app.providers.services.get_media_metadata", side_effect=_mock_get_media_metadata)
        self.mock_get_media = self.patcher.start()

        self.owner_creds = {"username": "owner", "password": "12345"}
        self.viewer_creds = {"username": "viewer", "password": "12345"}
        self.owner = get_user_model().objects.create_user(**self.owner_creds)
        self.viewer = get_user_model().objects.create_user(**self.viewer_creds)
        self.owner.profile_private = False
        self.owner.save(update_fields=["profile_private"])

        # TV item (root)
        self.tv_item = Item.objects.create(
            media_id="550",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Breaking Bad",
            image="http://example.com/tv.jpg",
        )
        # Season items
        self.season1_item = Item.objects.create(
            media_id="550",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Breaking Bad",
            image="http://example.com/s1.jpg",
            season_number=1,
        )
        self.season2_item = Item.objects.create(
            media_id="550",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Breaking Bad",
            image="http://example.com/s2.jpg",
            season_number=2,
        )
        # Episode items
        self.episode1_item = Item.objects.create(
            media_id="550",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Breaking Bad",
            image="http://example.com/e1.jpg",
            season_number=1,
            episode_number=1,
        )

        # Tracking records (TV → Season → Episode) so get_user_media can find them
        self.tv_tracking = TV.objects.create(
            item=self.tv_item, user=self.owner, status=Status.IN_PROGRESS.value,
        )
        self.season_tracking = Season.objects.create(
            item=self.season1_item, user=self.owner,
            related_tv=self.tv_tracking, status=Status.IN_PROGRESS.value,
        )
        Episode.objects.create(
            item=self.episode1_item, related_season=self.season_tracking,
        )

        self.private_list = CustomList.objects.create(
            name="Private TV",
            owner=self.owner,
            is_public=False,
        )
        CustomListItem.objects.create(
            custom_list=self.private_list,
            item=self.tv_item,
        )

    def tearDown(self):
        self.patcher.stop()

    def test_tv_item_in_private_list_hides_all_seasons(self):
        """When TV show is in private list, all seasons are hidden."""
        private_ids = list(CustomList.objects.get_private_item_ids(self.owner))
        self.assertIn(self.tv_item.id, private_ids)
        self.assertIn(self.season1_item.id, private_ids)
        self.assertIn(self.season2_item.id, private_ids)

    def test_tv_item_in_private_list_hides_episodes(self):
        """When TV show is in private list, all episodes are hidden."""
        private_ids = list(CustomList.objects.get_private_item_ids(self.owner))
        self.assertIn(self.episode1_item.id, private_ids)

    def test_tv_show_hidden_from_public_profile(self):
        """TV show and all seasons hidden from public profile."""
        self.client.login(**self.viewer_creds)
        response = self.client.get(
            reverse("medialist", args=[self.owner.username, MediaTypes.SEASON.value]),
        )
        self.assertEqual(response.status_code, 200)
        titles = [m.item.title for m in response.context["media_list"]]
        self.assertNotIn("Breaking Bad", titles)

    def test_owner_sees_all_tv_show_items(self):
        """Owner sees all TV show items regardless of list privacy."""
        self.client.login(**self.owner_creds)
        response = self.client.get(
            reverse("public_profile", args=[self.owner.username]),
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Breaking Bad", content)


class ListPrivacyStatusChangeTests(TestCase):
    """Test that changing tracking status does not affect privacy."""

    def setUp(self):
        """Create owner, viewer, item, and private list."""
        self.patcher = patch("app.providers.services.get_media_metadata")
        self.mock_get_media = self.patcher.start()
        self.mock_get_media.return_value = {"max_progress": None}

        self.owner_creds = {"username": "owner", "password": "12345"}
        self.viewer_creds = {"username": "viewer", "password": "12345"}
        self.owner = get_user_model().objects.create_user(**self.owner_creds)
        self.viewer = get_user_model().objects.create_user(**self.viewer_creds)
        self.owner.profile_private = False
        self.owner.save(update_fields=["profile_private"])

        self.item = Item.objects.create(
            media_id="100",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Status Test Movie",
            image="http://example.com/100.jpg",
        )
        self.movie = Movie.objects.create(
            item=self.item,
            user=self.owner,
            status=Status.PLANNING.value,
        )

        self.private_list = CustomList.objects.create(
            name="Private",
            owner=self.owner,
            is_public=False,
        )
        CustomListItem.objects.create(
            custom_list=self.private_list,
            item=self.item,
        )

    def tearDown(self):
        self.patcher.stop()

    def _check_hidden(self):
        """Helper: verify item is hidden from viewer."""
        self.client.login(**self.viewer_creds)
        response = self.client.get(
            reverse("public_profile", args=[self.owner.username]),
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertNotIn("Status Test Movie", content)

    def test_changing_to_in_progress_preserves_privacy(self):
        """Changing status to In Progress doesn't expose private item."""
        self.movie.status = Status.IN_PROGRESS.value
        self.movie.save()
        self._check_hidden()

    def test_changing_to_completed_preserves_privacy(self):
        """Changing status to Completed doesn't expose private item."""
        self.movie.status = Status.COMPLETED.value
        self.movie.save()
        self._check_hidden()

    def test_changing_to_paused_preserves_privacy(self):
        """Changing status to Paused doesn't expose private item."""
        self.movie.status = Status.PAUSED.value
        self.movie.save()
        self._check_hidden()

    def test_changing_to_dropped_preserves_privacy(self):
        """Changing status to Dropped doesn't expose private item."""
        self.movie.status = Status.DROPPED.value
        self.movie.save()
        self._check_hidden()

    def test_adding_score_preserves_privacy(self):
        """Adding a score doesn't expose private item."""
        self.movie.score = 9
        self.movie.save()
        self._check_hidden()


class ListPrivacyOwnerBypassTests(TestCase):
    """Test that owners always see their own items regardless of privacy."""

    def setUp(self):
        """Create owner, items, and private list."""
        self.patcher = patch("app.providers.services.get_media_metadata")
        self.mock_get_media = self.patcher.start()
        self.mock_get_media.return_value = {"max_progress": None}

        self.owner_creds = {"username": "owner", "password": "12345"}
        self.owner = get_user_model().objects.create_user(**self.owner_creds)

        self.item = Item.objects.create(
            media_id="200",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Owner Test Movie",
            image="http://example.com/200.jpg",
        )
        Movie.objects.create(
            item=self.item,
            user=self.owner,
            status=Status.IN_PROGRESS.value,
        )

        self.private_list = CustomList.objects.create(
            name="Private",
            owner=self.owner,
            is_public=False,
        )
        CustomListItem.objects.create(
            custom_list=self.private_list,
            item=self.item,
        )

    def tearDown(self):
        self.patcher.stop()

    def test_owner_sees_private_item_on_profile(self):
        """Owner sees private items on their own public profile."""
        self.client.login(**self.owner_creds)
        response = self.client.get(
            reverse("public_profile", args=[self.owner.username]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["media_count"].get("movie", 0), 1)

    def test_owner_sees_private_item_in_medialist(self):
        """Owner sees private items in their own media list."""
        self.client.login(**self.owner_creds)
        response = self.client.get(
            reverse("medialist", args=[self.owner.username, MediaTypes.MOVIE.value]),
        )
        self.assertEqual(response.status_code, 200)
        titles = [m.item.title for m in response.context["media_list"]]
        self.assertIn("Owner Test Movie", titles)
