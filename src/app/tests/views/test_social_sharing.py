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


class ProfileViewMediaDetailsTests(TestCase):
    """Test media_details view in profile (read-only) mode."""

    def setUp(self):
        """Create two users and log in as the viewer."""
        self.credentials = {"username": "viewer", "password": "12345"}
        self.owner_credentials = {"username": "owner", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.owner = get_user_model().objects.create_user(**self.owner_credentials)
        self.client.login(**self.credentials)

    @patch("app.providers.services.get_media_metadata")
    def test_normal_view_no_profile_context(self, mock_get_metadata):
        """Without from_profile param, is_profile_view is False."""
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "overview": "Test overview",
            "release_date": "2023-01-01",
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                    "title": "test-movie",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["is_profile_view"])
        self.assertIsNone(response.context["profile_user"])
        self.assertIsNone(response.context["profile_medias"])

    @patch("app.providers.services.get_media_metadata")
    def test_profile_view_sets_is_profile_view_true(self, mock_get_metadata):
        """from_profile with a different user sets is_profile_view to True."""
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "overview": "Test overview",
            "release_date": "2023-01-01",
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                    "title": "test-movie",
                },
            )
            + f"?from_profile={self.owner.username}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["is_profile_view"])
        self.assertEqual(response.context["profile_user"], self.owner)

    @patch("app.providers.services.get_media_metadata")
    def test_profile_view_own_profile_not_read_only(self, mock_get_metadata):
        """Viewing own profile via from_profile does not set read-only."""
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "overview": "Test overview",
            "release_date": "2023-01-01",
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                    "title": "test-movie",
                },
            )
            + f"?from_profile={self.user.username}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["is_profile_view"])

    @patch("app.providers.services.get_media_metadata")
    def test_profile_view_shows_profile_user_medias(self, mock_get_metadata):
        """from_profile populates profile_medias with the owner's data."""
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "overview": "Test overview",
            "release_date": "2023-01-01",
        }

        item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
        )
        Movie.objects.create(
            item=item,
            user=self.owner,
            status=Status.PLANNING.value,
            progress=0,
        )

        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "overview": "Test overview",
            "release_date": "2023-01-01",
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                    "title": "test-movie",
                },
            )
            + f"?from_profile={self.owner.username}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.context["profile_medias"])
        self.assertEqual(response.context["profile_medias"].count(), 1)
        self.assertEqual(response.context["profile_medias"][0].user, self.owner)

    @patch("app.providers.services.get_media_metadata")
    def test_profile_view_nonexistent_user(self, mock_get_metadata):
        """from_profile with nonexistent user gracefully sets profile_user to None."""
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "overview": "Test overview",
            "release_date": "2023-01-01",
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                    "title": "test-movie",
                },
            )
            + "?from_profile=nonexistent_user",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["profile_user"])
        self.assertFalse(response.context["is_profile_view"])


class ProfileViewSeasonDetailsTests(TestCase):
    """Test season_details view in profile (read-only) mode."""

    def setUp(self):
        """Create two users and log in as the viewer."""
        self.credentials = {"username": "viewer", "password": "12345"}
        self.owner_credentials = {"username": "owner", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.owner = get_user_model().objects.create_user(**self.owner_credentials)
        self.client.login(**self.credentials)

    @patch("app.providers.services.get_media_metadata")
    @patch("app.providers.tmdb.process_episodes")
    def test_season_profile_view_sets_context(
        self,
        mock_process_episodes,
        mock_get_metadata,
    ):
        """from_profile on season_details sets is_profile_view and profile_user."""
        mock_get_metadata.return_value = {
            "title": "Test TV Show",
            "media_id": "1668",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "image": "http://example.com/image.jpg",
            "season/1": {
                "title": "Season 1",
                "media_id": "1668",
                "media_type": MediaTypes.SEASON.value,
                "source": Sources.TMDB.value,
                "image": "http://example.com/season.jpg",
                "episodes": [],
            },
        }
        mock_process_episodes.return_value = []

        response = self.client.get(
            reverse(
                "season_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "1668",
                    "title": "test-tv-show",
                    "season_number": 1,
                },
            )
            + f"?from_profile={self.owner.username}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["is_profile_view"])
        self.assertEqual(response.context["profile_user"], self.owner)

    @patch("app.providers.services.get_media_metadata")
    @patch("app.providers.tmdb.process_episodes")
    def test_season_normal_view_no_profile_context(
        self,
        mock_process_episodes,
        mock_get_metadata,
    ):
        """Without from_profile, season_details has no profile context."""
        mock_get_metadata.return_value = {
            "title": "Test TV Show",
            "media_id": "1668",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "image": "http://example.com/image.jpg",
            "season/1": {
                "title": "Season 1",
                "media_id": "1668",
                "media_type": MediaTypes.SEASON.value,
                "source": Sources.TMDB.value,
                "image": "http://example.com/season.jpg",
                "episodes": [],
            },
        }
        mock_process_episodes.return_value = []

        response = self.client.get(
            reverse(
                "season_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "1668",
                    "title": "test-tv-show",
                    "season_number": 1,
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["is_profile_view"])
        self.assertIsNone(response.context["profile_user"])


class ListPrivacyTests(TestCase):
    """Test list privacy: public lists visible, private lists hidden."""

    def setUp(self):
        """Create two users, one public and one private list."""
        self.credentials = {"username": "viewer", "password": "12345"}
        self.owner_credentials = {"username": "owner", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.owner = get_user_model().objects.create_user(**self.owner_credentials)

        self.public_list = CustomList.objects.create(
            name="Public List",
            description="A public list",
            owner=self.owner,
            is_public=True,
        )
        self.private_list = CustomList.objects.create(
            name="Private List",
            description="A private list",
            owner=self.owner,
            is_public=False,
        )

        self.item = Item.objects.create(
            media_id="1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
        )
        CustomListItem.objects.create(
            custom_list=self.public_list,
            item=self.item,
        )
        CustomListItem.objects.create(
            custom_list=self.private_list,
            item=self.item,
        )

    def test_public_list_detail_accessible_by_other_user(self):
        """Other users can view public list detail."""
        self.client.login(**self.credentials)
        response = self.client.get(
            reverse("list_detail", args=[self.public_list.id]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "lists/list_detail.html")

    def test_private_list_detail_returns_404_for_other_user(self):
        """Other users get 404 when trying to view private list."""
        self.client.login(**self.credentials)
        response = self.client.get(
            reverse("list_detail", args=[self.private_list.id]),
        )
        self.assertEqual(response.status_code, 404)

    def test_private_list_detail_accessible_by_owner(self):
        """Owner can view their own private list."""
        self.client.login(**self.owner_credentials)
        response = self.client.get(
            reverse("list_detail", args=[self.private_list.id]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "lists/list_detail.html")

    def test_public_list_accessible_by_unauthenticated_user(self):
        """Public lists are accessible without login (via public_list_detail)."""
        response = self.client.get(
            reverse("public_list_detail", args=[self.public_list.id]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "lists/public_list_detail.html")

    def test_private_list_accessible_by_unauthenticated_user(self):
        """Private lists return 404 for unauthenticated users."""
        response = self.client.get(
            reverse("public_list_detail", args=[self.private_list.id]),
        )
        self.assertEqual(response.status_code, 404)


class ListDetailCanEditCanDeleteTests(TestCase):
    """Test that list_detail passes can_edit and can_delete context correctly."""

    def setUp(self):
        """Create owner, collaborator, and viewer users with lists."""
        self.owner_credentials = {"username": "owner", "password": "12345"}
        self.collaborator_credentials = {"username": "collab", "password": "12345"}
        self.viewer_credentials = {"username": "viewer", "password": "12345"}
        self.owner = get_user_model().objects.create_user(**self.owner_credentials)
        self.collaborator = get_user_model().objects.create_user(
            **self.collaborator_credentials,
        )
        self.viewer = get_user_model().objects.create_user(**self.viewer_credentials)

        self.private_list = CustomList.objects.create(
            name="Private List",
            owner=self.owner,
            is_public=False,
        )
        self.private_list.collaborators.add(self.collaborator)

        self.item = Item.objects.create(
            media_id="1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
        )
        CustomListItem.objects.create(
            custom_list=self.private_list,
            item=self.item,
        )

    def test_owner_has_can_edit_and_can_delete(self):
        """Owner sees can_edit=True and can_delete=True."""
        self.client.login(**self.owner_credentials)
        response = self.client.get(
            reverse("list_detail", args=[self.private_list.id]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_edit"])
        self.assertTrue(response.context["can_delete"])

    def test_collaborator_has_can_edit_no_can_delete(self):
        """Collaborator sees can_edit=True and can_delete=False."""
        self.client.login(**self.collaborator_credentials)
        response = self.client.get(
            reverse("list_detail", args=[self.private_list.id]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_edit"])
        self.assertFalse(response.context["can_delete"])

    def test_viewer_no_access(self):
        """Viewer with no access gets 404."""
        self.client.login(**self.viewer_credentials)
        response = self.client.get(
            reverse("list_detail", args=[self.private_list.id]),
        )
        self.assertEqual(response.status_code, 404)


class ProfileAddToMyLibraryTests(TestCase):
    """Test that Add to My Library works via media_save without instance_id."""

    def setUp(self):
        """Create two users."""
        self.credentials = {"username": "viewer", "password": "12345"}
        self.owner_credentials = {"username": "owner", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.owner = get_user_model().objects.create_user(**self.owner_credentials)
        self.client.login(**self.credentials)

    @patch("app.providers.services.get_media_metadata")
    def test_add_to_my_library_creates_new_entry_for_viewer(self, mock_get_metadata):
        """Posting to media_save without instance_id creates a new entry for viewer."""
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "overview": "Test overview",
            "release_date": "2023-01-01",
            "max_progress": 1,
        }
        item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
        )
        Movie.objects.create(
            item=item,
            user=self.owner,
            status=Status.PLANNING.value,
            progress=0,
        )

        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "overview": "Test overview",
            "release_date": "2023-01-01",
            "max_progress": 1,
        }

        self.client.post(
            reverse("media_save"),
            {
                "media_id": "238",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "status": Status.PLANNING.value,
            },
        )

        viewer_movie = Movie.objects.filter(
            item__media_id="238",
            user=self.user,
        )
        self.assertEqual(viewer_movie.count(), 1)
        self.assertEqual(viewer_movie.first().status, Status.PLANNING.value)

        owner_movie = Movie.objects.filter(
            item__media_id="238",
            user=self.owner,
        )
        self.assertEqual(owner_movie.count(), 1)

    @patch("app.providers.services.get_media_metadata")
    def test_add_to_my_library_does_not_modify_owner_data(self, mock_get_metadata):
        """Adding to own library does not modify the original owner's data."""
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "overview": "Test overview",
            "release_date": "2023-01-01",
            "max_progress": 1,
        }
        item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
        )
        Movie.objects.create(
            item=item,
            user=self.owner,
            status=Status.COMPLETED.value,
            score=9,
            notes="Original notes",
        )

        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "overview": "Test overview",
            "release_date": "2023-01-01",
            "max_progress": 1,
        }

        self.client.post(
            reverse("media_save"),
            {
                "media_id": "238",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "status": Status.PLANNING.value,
            },
        )

        owner_movie = Movie.objects.get(item__media_id="238", user=self.owner)
        self.assertEqual(owner_movie.score, 9)
        self.assertEqual(owner_movie.notes, "Original notes")
        self.assertEqual(owner_movie.status, Status.COMPLETED.value)
