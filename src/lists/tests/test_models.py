from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase

from app.models import Item, MediaTypes, Sources
from lists.models import CustomList, CustomListItem


class CustomListModelTest(TestCase):
    """Test case for the CustomList model."""

    def setUp(self):
        """Set up test data for CustomList model."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        self.collaborator_credentials = {
            "username": "collaborator",
            "password": "12345",
        }
        self.collaborator = get_user_model().objects.create_user(
            **self.collaborator_credentials,
        )

        self.custom_list = CustomList.objects.create(
            name="Test List",
            description="Test Description",
            owner=self.user,
            is_public=False,
        )
        self.custom_list.collaborators.add(self.collaborator)

        self.item = Item.objects.create(
            title="Test Item",
            media_id="123",
            media_type=MediaTypes.TV.value,
            source=Sources.TMDB.value,
        )

        self.non_member_credentials = {
            "username": "non_member",
            "password": "12345",
        }
        self.non_member = get_user_model().objects.create_user(
            **self.non_member_credentials,
        )

    def test_custom_list_creation(self):
        """Test the creation of a CustomList instance."""
        self.assertEqual(self.custom_list.name, "Test List")
        self.assertEqual(self.custom_list.description, "Test Description")
        self.assertEqual(self.custom_list.owner, self.user)

    def test_custom_list_str_representation(self):
        """Test the string representation of a CustomList."""
        self.assertEqual(str(self.custom_list), "Test List")

    def test_owner_permissions(self):
        """Test owner permissions on custom list."""
        self.assertTrue(self.custom_list.user_can_view(self.user))
        self.assertTrue(self.custom_list.user_can_edit(self.user))
        self.assertTrue(self.custom_list.user_can_delete(self.user))

    def test_collaborator_permissions(self):
        """Test collaborator permissions on custom list."""
        self.assertTrue(self.custom_list.user_can_view(self.collaborator))
        self.assertTrue(self.custom_list.user_can_edit(self.collaborator))
        self.assertFalse(self.custom_list.user_can_delete(self.collaborator))

    def test_non_member_permissions(self):
        """Test non-member permissions on custom list."""
        self.assertFalse(self.custom_list.user_can_view(self.non_member))
        self.assertFalse(self.custom_list.user_can_edit(self.non_member))
        self.assertFalse(self.custom_list.user_can_delete(self.non_member))

    def test_duplicate_item_constraint(self):
        """Test that an item cannot be added twice to the same list."""
        CustomListItem.objects.create(
            item=self.item,
            custom_list=self.custom_list,
        )

        with self.assertRaises(IntegrityError):
            CustomListItem.objects.create(
                item=self.item,
                custom_list=self.custom_list,
            )


class CustomListManagerTest(TestCase):
    """Test case for the CustomListManager."""

    def setUp(self):
        """Set up test data for CustomListManager tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.other_credentials = {"username": "other", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.other_user = get_user_model().objects.create_user(**self.other_credentials)
        self.list1 = CustomList.objects.create(name="List 1", owner=self.user)
        self.list2 = CustomList.objects.create(name="List 2", owner=self.other_user)
        self.list2.collaborators.add(self.user)

    def test_get_user_lists(self):
        """Test the get_user_lists method of CustomListManager."""
        user_lists = CustomList.objects.get_user_lists(self.user)
        self.assertEqual(user_lists.count(), 2)
        self.assertIn(self.list1, user_lists)
        self.assertIn(self.list2, user_lists)
