from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from app.models import Item
from lists.forms import CustomListForm
from lists.models import CustomList


class CustomListModelTest(TestCase):
    """Test case for the CustomList model."""

    def setUp(self):
        """Set up test data for CustomList model."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.custom_list = CustomList.objects.create(
            name="Test List",
            description="Test Description",
            owner=self.user,
        )

    def test_custom_list_creation(self):
        """Test the creation of a CustomList instance."""
        self.assertEqual(self.custom_list.name, "Test List")
        self.assertEqual(self.custom_list.description, "Test Description")
        self.assertEqual(self.custom_list.owner, self.user)

    def test_custom_list_str_representation(self):
        """Test the string representation of a CustomList."""
        self.assertEqual(str(self.custom_list), "Test List")

    def test_user_can_edit(self):
        """Test the user_can_edit method of CustomList."""
        self.assertTrue(self.custom_list.user_can_edit(self.user))
        self.credentials = {"username": "test2", "password": "12345"}
        other_user = get_user_model().objects.create_user(**self.credentials)
        self.assertFalse(self.custom_list.user_can_edit(other_user))

    def test_user_can_delete(self):
        """Test the user_can_delete method of CustomList."""
        self.assertTrue(self.custom_list.user_can_delete(self.user))
        self.credentials = {"username": "test2", "password": "12345"}
        other_user = get_user_model().objects.create_user(**self.credentials)
        self.assertFalse(self.custom_list.user_can_delete(other_user))


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


class ListsViewTest(TestCase):
    """Test case for the lists view."""

    def setUp(self):
        """Set up test data for lists view tests."""
        self.client = Client()
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)
        self.list = CustomList.objects.create(name="Test List", owner=self.user)

    def test_lists_view(self):
        """Test the lists view response and context."""
        response = self.client.get(reverse("lists"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "lists/custom_lists.html")
        self.assertIn("custom_lists", response.context)
        self.assertIn("form", response.context)


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
            reverse("create"),
            {"name": "New List", "description": "New Description"},
        )
        self.assertEqual(CustomList.objects.count(), 1)
        new_list = CustomList.objects.first()
        self.assertEqual(new_list.name, "New List")
        self.assertEqual(new_list.description, "New Description")
        self.assertEqual(new_list.owner, self.user)


class EditListViewTest(TestCase):
    """Test case for the edit list view."""

    def setUp(self):
        """Set up test data for edit list view tests."""
        self.client = Client()
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)
        self.list = CustomList.objects.create(name="Test List", owner=self.user)

    def test_edit_list(self):
        """Test editing an existing custom list."""
        self.client.post(
            reverse("edit"),
            {
                "list_id": self.list.id,
                "name": "Updated List",
                "description": "Updated Description",
            },
        )
        self.list.refresh_from_db()
        self.assertEqual(self.list.name, "Updated List")
        self.assertEqual(self.list.description, "Updated Description")


class DeleteListViewTest(TestCase):
    """Test the delete view."""

    def setUp(self):
        """Create a user, log in, and create a list."""
        self.client = Client()
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)
        self.list = CustomList.objects.create(name="Test List", owner=self.user)

    def test_delete_list(self):
        """Test deleting a list."""
        self.client.post(reverse("delete"), {"list_id": self.list.id})
        self.assertEqual(CustomList.objects.count(), 0)


class ListsModalViewTest(TestCase):
    """Test the lists_modal view."""

    def setUp(self):
        """Create a user and log in."""
        self.client = Client()
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_lists_modal_view(self):
        """Test the lists_modal view."""
        response = self.client.get(
            reverse("lists_modal"),
            {
                "media_type": "movie",
                "media_id": "1",
                "title": "Test Movie",
                "image": "http://example.com/image.jpg",
                "source": "tmdb",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "lists/components/fill_lists.html")
        self.assertIn("item", response.context)
        self.assertIn("custom_lists", response.context)


class ListItemToggleViewTest(TestCase):
    """Test the list_item_toggle view."""

    def setUp(self):
        """Create a user, a list, and an item."""
        self.client = Client()
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)
        self.list = CustomList.objects.create(name="Test List", owner=self.user)
        self.item = Item.objects.create(
            media_id=1,
            source="tmdb",
            media_type="movie",
            title="Test Movie",
            image="http://example.com/image.jpg",
        )

    def test_list_item_toggle(self):
        """Test adding an item to a list."""
        response = self.client.post(
            reverse("list_item_toggle"),
            {
                "item_id": self.item.id,
                "custom_list_id": self.list.id,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.item, self.list.items.all())

    def test_list_item_toggle_remove(self):
        """Test removing an item from a list."""
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


class CustomListFormTest(TestCase):
    """Test the Custom List form."""

    def setUp(self):
        """Create a user."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

    def test_custom_list_form_valid(self):
        """Test the form with valid data."""
        form_data = {
            "name": "Test List",
            "description": "Test Description",
        }
        form = CustomListForm(data=form_data)
        self.assertTrue(form.is_valid())

    def test_custom_list_form_invalid(self):
        """Test the form with invalid data."""
        form_data = {
            "name": "",  # Name is required
            "description": "Test Description",
        }
        form = CustomListForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn("name", form.errors)

    def test_custom_list_form_with_collaborators(self):
        """Test the form with collaborators."""
        self.credentials = {"username": "test2", "password": "12345"}
        collaborator = get_user_model().objects.create_user(**self.credentials)
        form_data = {
            "name": "Test List",
            "description": "Test Description",
            "collaborators": [collaborator.id],
        }
        form = CustomListForm(data=form_data)
        self.assertTrue(form.is_valid())

    def test_custom_list_form_unique_name_for_owner(self):
        """Test that the name of the list is unique."""
        # Create a list for the user
        form1 = CustomListForm(
            data={"name": "Existing List", "description": "Test"},
        ).save(commit=False)
        form1.owner = self.user
        form1.save()

        # Try to create another list with the same name
        form2_data = {
            "name": "Existing List",
            "description": "Another Test",
        }
        form2 = CustomListForm(data=form2_data, owner=self.user)
        self.assertFalse(form2.is_valid())
