from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from app.models import MediaTypes
from lists.models import CustomList, CustomListItem

from .base import YamtrackApiTestCase
from .helpers import check_complete_lists_structure, check_pagination_structure


class ListsTests(YamtrackApiTestCase):
    """Validate list endpoints and basic side effects."""

    def test_lists_get_returns_paginated_payload(self):
        """Lists endpoint should return the standard pagination envelope."""
        response = self.call_api("get", "api_lists", headers=self.auth_headers)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("pagination", payload)
        self.assertIn("results", payload)
        check_pagination_structure(self, payload["pagination"])
        for lst in payload["results"]:
            check_complete_lists_structure(self, lst)

    def test_lists_get_search_filter_returns_filtered_results(self):
        """Lists endpoint should filter by search query."""
        response = self.call_api(
            "get",
            "api_lists",
            params={"search": "Favorites"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["results"]), 1)
        self.assertEqual(payload["results"][0]["name"], "Favorites")

    def test_lists_get_sort_filter_returns_sorted_results(self):
        """Lists endpoint should sort results when requested."""
        response = self.call_api(
            "get",
            "api_lists",
            params={"sort": "name_desc"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        names = [item["name"] for item in payload["results"]]
        self.assertEqual(names, sorted(names, reverse=True))

    def test_lists_get_items_sort_uses_item_count(self):
        """Lists endpoint should sort by number of items, not by join order."""
        response = self.call_api(
            "get",
            "api_lists",
            params={"sort": "items_desc"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        names = [item["name"] for item in payload["results"]]
        self.assertEqual(
            names,
            ["Favorites", "Shared Picks", "Watching Now"],
        )
        self.assertEqual(
            [item["items_count"] for item in payload["results"]],
            [3, 3, 2],
        )

    def test_lists_post_without_name_returns_bad_request(self):
        """Creating a list without name must fail with 400."""
        response = self.call_api(
            "post",
            "api_lists",
            payload={"description": "missing-name"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("name", response.json().get("detail", "").lower())

    def test_lists_post_with_name_creates_list(self):
        """Creating a list with name should succeed and return 201."""
        response = self.call_api(
            "post",
            "api_lists",
            payload={"name": "New List", "description": "Test list"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["name"], "New List")
        self.assertEqual(payload["description"], "Test list")
        self.assertTrue(
            CustomList.objects.filter(name="New List", owner=self.user1).exists()
        )

    def test_lists_post_with_invalid_collaborators_type_returns_bad_request(self):
        """POST lists with non-array collaborators should fail with 400."""
        response = self.call_api(
            "post",
            "api_lists",
            payload={"name": "New List", "collaborators": "not-array"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("collaborators", response.json().get("detail", "").lower())

    def test_lists_post_with_invalid_collaborator_ids_returns_bad_request(self):
        """POST lists with invalid collaborator IDs should fail with 400."""
        response = self.call_api(
            "post",
            "api_lists",
            payload={"name": "New List", "collaborators": [99999]},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("collaborator", response.json().get("detail", "").lower())

    def test_lists_post_with_valid_collaborators_adds_them(self):
        """POST lists with valid collaborators should create list with collaborators."""
        response = self.call_api(
            "post",
            "api_lists",
            payload={"name": "Collab List", "collaborators": [self.user2.id]},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["name"], "Collab List")
        new_list = CustomList.objects.get(name="Collab List")
        self.assertTrue(new_list.collaborators.filter(id=self.user2.id).exists())

    def test_list_detail_delete_removes_list(self):
        """List delete should remove the owned list and return 204."""
        custom_list = self.lists_by_name["favorites"]
        response = self.call_api(
            "delete",
            "api_list_detail",
            args=(custom_list.id,),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(CustomList.objects.filter(id=custom_list.id).exists())

    def test_list_detail_delete_not_found_returns_404(self):
        """List delete with non-existent ID should return 404."""
        response = self.call_api(
            "delete",
            "api_list_detail",
            args=(99999,),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 404)

    def test_list_detail_delete_without_permission_returns_403(self):
        """List delete without permission should return 403."""
        other_list = self.lists_by_name["user2_private"]
        response = self.call_api(
            "delete",
            "api_list_detail",
            args=(other_list.id,),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 403)

    def _count_list_queries(self, url_name, custom_list):
        """Return the number of queries made to serve a list endpoint."""
        with CaptureQueriesContext(connection) as queries:
            response = self.call_api(
                "get",
                url_name,
                args=(custom_list.id,),
                headers=self.auth_headers,
            )
        self.assertEqual(response.status_code, 200)
        return len(queries)

    def test_list_endpoints_queries_do_not_grow_with_items(self):
        """List endpoints should not run queries for each item of the list."""
        custom_list = CustomList.objects.create(name="Queries", owner=self.user1)
        movies = self.items_by_type[MediaTypes.MOVIE.value]
        animes = self.items_by_type[MediaTypes.ANIME.value]

        for item in (movies[0], animes[0]):
            CustomListItem.objects.create(custom_list=custom_list, item=item)
        few_items_queries = {
            url_name: self._count_list_queries(url_name, custom_list)
            for url_name in ("api_list_detail", "api_list_add_item")
        }

        for item in (*movies[1:], *animes[1:]):
            CustomListItem.objects.create(custom_list=custom_list, item=item)
        for url_name, expected in few_items_queries.items():
            with self.subTest(url_name=url_name):
                self.assertEqual(
                    self._count_list_queries(url_name, custom_list),
                    expected,
                )

    def test_list_detail_get_returns_list_with_paginated_items(self):
        """List detail GET should return list info and paginated items."""
        custom_list = self.lists_by_name["favorites"]
        response = self.call_api(
            "get",
            "api_list_detail",
            args=(custom_list.id,),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("name", payload)
        self.assertEqual(payload["name"], "Favorites")
        self.assertIn("items", payload)
        self.assertIsInstance(payload["items"], dict)
        self.assertIn("pagination", payload["items"])
        self.assertIn("results", payload["items"])

    def test_list_detail_get_search_filter_returns_filtered_results(self):
        """List detail GET should filter items by search query."""
        custom_list = self.lists_by_name["favorites"]
        response = self.call_api(
            "get",
            "api_list_detail",
            args=(custom_list.id,),
            params={"search": "Movie 1"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["items"]["results"]), 1)
        self.assertIn("Movie 1", payload["items"]["results"][0]["item"]["title"])

    def test_list_detail_get_sort_filter_returns_sorted_results(self):
        """List detail GET should sort items when requested."""
        custom_list = self.lists_by_name["favorites"]
        response = self.call_api(
            "get",
            "api_list_detail",
            args=(custom_list.id,),
            params={"sort": "title_desc"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        titles = [item["item"]["title"] for item in payload["items"]["results"]]
        self.assertEqual(titles, sorted(titles, reverse=True))

    def test_list_detail_get_not_found_returns_404(self):
        """List detail GET with non-existent ID should return 404."""
        response = self.call_api(
            "get",
            "api_list_detail",
            args=(99999,),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 404)

    def test_list_detail_get_without_permission_returns_403(self):
        """List detail GET without permission should return 403."""
        other_list = self.lists_by_name["user2_private"]
        response = self.call_api(
            "get",
            "api_list_detail",
            args=(other_list.id,),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 403)

    def test_list_detail_patch_name(self):
        """List PATCH should update name and return 200."""
        custom_list = self.lists_by_name["favorites"]
        response = self.call_api(
            "patch",
            "api_list_detail",
            args=(custom_list.id,),
            payload={"name": "Updated Name"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        custom_list.refresh_from_db()
        self.assertEqual(custom_list.name, "Updated Name")

    def test_list_detail_patch_description(self):
        """List PATCH should update description and return 200."""
        custom_list = self.lists_by_name["favorites"]
        response = self.call_api(
            "patch",
            "api_list_detail",
            args=(custom_list.id,),
            payload={"description": "New description"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        custom_list.refresh_from_db()
        self.assertEqual(custom_list.description, "New description")

    def test_list_detail_patch_collaborators(self):
        """List PATCH should update collaborators and return 200."""
        custom_list = self.lists_by_name["favorites"]
        response = self.call_api(
            "patch",
            "api_list_detail",
            args=(custom_list.id,),
            payload={"collaborators": [self.user2.id]},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        custom_list.refresh_from_db()
        self.assertTrue(custom_list.collaborators.filter(id=self.user2.id).exists())

    def test_list_detail_patch_not_found_returns_404(self):
        """List PATCH with non-existent ID should return 404."""
        response = self.call_api(
            "patch",
            "api_list_detail",
            args=(99999,),
            payload={"name": "Updated"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 404)

    def test_list_detail_patch_without_permission_returns_403(self):
        """List PATCH without permission should return 403."""
        other_list = self.lists_by_name["user2_private"]
        response = self.call_api(
            "patch",
            "api_list_detail",
            args=(other_list.id,),
            payload={"name": "Hacked Name"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 403)

    def test_list_detail_patch_collaborators_only_by_owner(self):
        """Only the list owner should be able to change the collaborators."""
        shared_list = self.lists_by_name["shared"]

        response = self.call_api(
            "patch",
            "api_list_detail",
            args=(shared_list.id,),
            payload={"collaborators": []},
            headers=self.auth_headers2,
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(shared_list.collaborators.filter(id=self.user2.id).exists())

        # Collaborators can still edit the other list fields
        response = self.call_api(
            "patch",
            "api_list_detail",
            args=(shared_list.id,),
            payload={"name": "Renamed by collaborator"},
            headers=self.auth_headers2,
        )
        self.assertEqual(response.status_code, 200)

        response = self.call_api(
            "patch",
            "api_list_detail",
            args=(shared_list.id,),
            payload={"collaborators": []},
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(shared_list.collaborators.exists())

    def test_lists_post_with_duplicated_collaborators_creates_list(self):
        """Duplicated collaborator ids shouldn't be reported as invalid."""
        response = self.call_api(
            "post",
            "api_lists",
            payload={
                "name": "New List",
                "collaborators": [self.user2.id, self.user2.id],
            },
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 201)
        custom_list = CustomList.objects.get(name="New List", owner=self.user1)
        self.assertEqual(list(custom_list.collaborators.all()), [self.user2])

    def test_lists_post_with_invalid_collaborators_does_not_create_list(self):
        """A list shouldn't be created if the collaborators are invalid."""
        response = self.call_api(
            "post",
            "api_lists",
            payload={"name": "New List", "collaborators": [99999]},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("collaborators", response.json()["errors"])
        self.assertFalse(CustomList.objects.filter(name="New List").exists())

    def test_list_detail_patch_invalid_collaborators_returns_400(self):
        """List PATCH with invalid collaborators type should return 400."""
        custom_list = self.lists_by_name["favorites"]
        response = self.call_api(
            "patch",
            "api_list_detail",
            args=(custom_list.id,),
            payload={"collaborators": "not-array"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)

    def test_list_detail_patch_invalid_collaborator_ids_returns_400(self):
        """List PATCH with invalid collaborator IDs should return 400."""
        custom_list = self.lists_by_name["favorites"]
        response = self.call_api(
            "patch",
            "api_list_detail",
            args=(custom_list.id,),
            payload={"collaborators": [99999]},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)

    def test_list_items_get_returns_paginated_payload(self):
        """List items endpoint should return paginated list items."""
        custom_list = self.lists_by_name["favorites"]
        response = self.call_api(
            "get",
            "api_list_add_item",
            args=(custom_list.id,),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("pagination", payload)
        self.assertIn("results", payload)
        check_pagination_structure(self, payload["pagination"])

    def test_list_items_sort_filter_returns_sorted_results(self):
        """List items endpoint should sort items when requested."""
        custom_list = self.lists_by_name["favorites"]
        response = self.call_api(
            "get",
            "api_list_add_item",
            args=(custom_list.id,),
            params={"sort": "title_desc"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        titles = [item["item"]["title"] for item in payload["results"]]
        self.assertEqual(titles, sorted(titles, reverse=True))

    def test_list_items_not_found_returns_404(self):
        """List items endpoint with non-existent ID should return 404."""
        response = self.call_api(
            "get",
            "api_list_add_item",
            args=(99999,),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 404)

    def test_list_items_without_permission_returns_403(self):
        """List items endpoint without permission should return 403."""
        other_list = self.lists_by_name["user2_private"]
        response = self.call_api(
            "get",
            "api_list_add_item",
            args=(other_list.id,),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 403)

    def test_list_items_search_query(self):
        """List items endpoint should filter by search query."""
        custom_list = self.lists_by_name["favorites"]
        # Add second movie to list; search for first item which contains "Movie"
        other_item = self.items_by_type["movie"][1]
        custom_list.items.add(other_item)

        # Search for item title that matches first item (which contains "Movie 1")
        response = self.client.get(
            reverse("api_list_add_item", args=(custom_list.id,)) + "?search=Movie%201",
            **self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        # Should find only Movie 1, not Movie 2
        self.assertEqual(len(payload["results"]), 1)

    def test_list_items_invalid_sort_returns_bad_request(self):
        """List items endpoint with invalid sort should return 400."""
        custom_list = self.lists_by_name["favorites"]
        response = self.client.get(
            reverse("api_list_add_item", args=(custom_list.id,)) + "?sort=invalid_sort",
            **self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)

    def test_list_item_delete_removes_item(self):
        """Deleting a list item should return 204 and remove relation."""
        custom_list = self.lists_by_name["favorites"]
        list_item = custom_list.get_list_item(1, include_item=True)
        response = self.call_api(
            "delete",
            "api_list_remove_item",
            args=(custom_list.id, 1),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(
            CustomListItem.objects.filter(
                custom_list=custom_list,
                item=list_item.item,
            ).exists(),
        )

    def test_list_item_delete_not_found_returns_404(self):
        """Deleting non-existent list item should return 404."""
        custom_list = self.lists_by_name["favorites"]
        response = self.call_api(
            "delete",
            "api_list_remove_item",
            args=(custom_list.id, 99999),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 404)

    def test_list_item_delete_list_not_found_returns_404(self):
        """Deleting item from non-existent list should return 404."""
        response = self.call_api(
            "delete",
            "api_list_remove_item",
            args=(99999, 1),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 404)

    def test_list_item_delete_without_permission_returns_403(self):
        """Deleting item without permission should return 403."""
        other_list = self.lists_by_name["user2_private"]
        response = self.call_api(
            "delete",
            "api_list_remove_item",
            args=(other_list.id, 1),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 403)
