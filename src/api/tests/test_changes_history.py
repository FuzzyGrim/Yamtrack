from .base import YamtrackApiTestCase
from .helpers import check_changes_history_entry_structure, check_pagination_structure


class ChangesHistoryTests(YamtrackApiTestCase):
    """Validate changes history endpoint contracts."""

    def test_changes_history_get(self):
        """Changes-history list should return paginated entries with expected shape."""
        movie_item = self.items_by_type["movie"][0]
        response = self.call_api(
            "get",
            "api_media_changes_history",
            args=("movie", movie_item.source, movie_item.media_id),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("pagination", payload)
        check_pagination_structure(self, payload["pagination"])
        self.assertIn("results", payload)
        self.assertGreater(len(payload["results"]), 0)

        returned_ids = {str(entry["id"]) for entry in payload["results"]}
        expected_id = str(self.changes_history_entries["movie"].history_id)
        self.assertIn(expected_id, returned_ids)

        for entry in payload["results"]:
            check_changes_history_entry_structure(self, entry)

    def test_changes_history_get_invalid_type_returns_bad_request(self):
        """Changes-history list should reject invalid media types."""
        response = self.call_api(
            "get",
            "api_media_changes_history",
            args=("invalid", "tmdb", "1"),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)

    def test_changes_history_get_invalid_media_id_returns_not_found(self):
        """Changes-history list should return 404 when media is not tracked."""
        movie_item = self.items_by_type["movie"][0]
        response = self.call_api(
            "get",
            "api_media_changes_history",
            args=("movie", movie_item.source, "999999"),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 404)

    def test_season_changes_history_get(self):
        """Season changes-history list should return paginated entries."""
        season_item = self.items_by_type["season"][0]
        response = self.call_api(
            "get",
            "api_media_season_changes_history",
            args=(
                "tv",
                season_item.source,
                season_item.media_id,
                season_item.season_number,
            ),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("pagination", payload)
        self.assertIn("results", payload)
        check_pagination_structure(self, payload["pagination"])
        self.assertGreater(len(payload["results"]), 0)
        for entry in payload["results"]:
            self.assertIn("id", entry)
            self.assertIn("item_id", entry)
            self.assertIn("timestamp", entry)
            self.assertIn("changes", entry)
            self.assertTrue(entry["item_id"].startswith("tv/"))

    def test_season_changes_history_invalid_params(self):
        """Season changes-history should handle invalid type and ids."""
        season_item = self.items_by_type["season"][0]

        invalid_type_response = self.call_api(
            "get",
            "api_media_season_changes_history",
            args=(
                "movie",
                season_item.source,
                season_item.media_id,
                season_item.season_number,
            ),
            headers=self.auth_headers,
        )
        self.assertEqual(invalid_type_response.status_code, 400)

        invalid_media_response = self.call_api(
            "get",
            "api_media_season_changes_history",
            args=("tv", season_item.source, "999999", season_item.season_number),
            headers=self.auth_headers,
        )
        self.assertEqual(invalid_media_response.status_code, 404)

        invalid_season_response = self.call_api(
            "get",
            "api_media_season_changes_history",
            args=("tv", season_item.source, season_item.media_id, "999"),
            headers=self.auth_headers,
        )
        self.assertEqual(invalid_season_response.status_code, 404)

    def test_episode_changes_history_get(self):
        """Episode changes-history list should return paginated entries."""
        episode_item = self.items_by_type["episode"][0]
        response = self.call_api(
            "get",
            "api_media_episode_changes_history",
            args=(
                "tv",
                episode_item.source,
                episode_item.media_id,
                episode_item.season_number,
                episode_item.episode_number,
            ),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("pagination", payload)
        self.assertIn("results", payload)
        check_pagination_structure(self, payload["pagination"])
        for entry in payload["results"]:
            self.assertIn("id", entry)
            self.assertIn("item_id", entry)
            self.assertIn("timestamp", entry)
            self.assertIn("changes", entry)
            self.assertTrue(entry["item_id"].startswith("tv/"))

    def test_episode_changes_history_invalid_params(self):
        """Episode changes-history should handle invalid type and ids."""
        episode_item = self.items_by_type["episode"][0]

        invalid_type_response = self.call_api(
            "get",
            "api_media_episode_changes_history",
            args=(
                "movie",
                episode_item.source,
                episode_item.media_id,
                episode_item.season_number,
                episode_item.episode_number,
            ),
            headers=self.auth_headers,
        )
        self.assertEqual(invalid_type_response.status_code, 400)

        invalid_media_response = self.call_api(
            "get",
            "api_media_episode_changes_history",
            args=(
                "tv",
                episode_item.source,
                "999999",
                episode_item.season_number,
                episode_item.episode_number,
            ),
            headers=self.auth_headers,
        )
        self.assertEqual(invalid_media_response.status_code, 404)

        invalid_season_response = self.call_api(
            "get",
            "api_media_episode_changes_history",
            args=(
                "tv",
                episode_item.source,
                episode_item.media_id,
                "999",
                episode_item.episode_number,
            ),
            headers=self.auth_headers,
        )
        self.assertEqual(invalid_season_response.status_code, 404)

        invalid_episode_response = self.call_api(
            "get",
            "api_media_episode_changes_history",
            args=(
                "tv",
                episode_item.source,
                episode_item.media_id,
                episode_item.season_number,
                "999",
            ),
            headers=self.auth_headers,
        )
        self.assertEqual(invalid_episode_response.status_code, 404)

    def test_changes_history_detail_get(self):
        """Changes-history detail should return an entry with expected shape."""
        history_entry = self.changes_history_entries["movie"]
        response = self.call_api(
            "get",
            "api_media_changes_history_detail",
            args=("movie", str(history_entry.history_id)),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        check_changes_history_entry_structure(self, payload)
        self.assertEqual(str(payload["id"]), str(history_entry.history_id))

    def test_changes_history_detail_delete(self):
        """Changes-history detail delete should remove an existing history entry."""
        history_entry = self.changes_history_entries["movie"]

        delete_response = self.call_api(
            "delete",
            "api_media_changes_history_detail",
            args=("movie", str(history_entry.history_id)),
            headers=self.auth_headers,
        )
        self.assertEqual(delete_response.status_code, 204)
        self.assertEqual(delete_response.content, b"")

        get_response = self.call_api(
            "get",
            "api_media_changes_history_detail",
            args=("movie", str(history_entry.history_id)),
            headers=self.auth_headers,
        )
        self.assertEqual(get_response.status_code, 404)

    def test_changes_history_detail_invalid_type_returns_bad_request(self):
        """Changes-history detail endpoints should reject invalid types."""
        get_response = self.call_api(
            "get",
            "api_media_changes_history_detail",
            args=("invalid", "1"),
            headers=self.auth_headers,
        )
        self.assertEqual(get_response.status_code, 400)

        delete_response = self.call_api(
            "delete",
            "api_media_changes_history_detail",
            args=("invalid", "1"),
            headers=self.auth_headers,
        )
        self.assertEqual(delete_response.status_code, 400)

    def test_changes_history_detail_not_found_returns_404(self):
        """Changes-history detail should return 404 for missing records."""
        get_response = self.call_api(
            "get",
            "api_media_changes_history_detail",
            args=("movie", "99999"),
            headers=self.auth_headers,
        )

        self.assertEqual(get_response.status_code, 404)

        delete_response = self.call_api(
            "delete",
            "api_media_changes_history_detail",
            args=("movie", "99999"),
            headers=self.auth_headers,
        )

        self.assertEqual(delete_response.status_code, 404)

    def test_changes_history_detail_non_numeric_id_returns_404(self):
        """Changes-history detail should return 404 for a non numeric id."""
        for method in ("get", "delete"):
            with self.subTest(method=method):
                response = self.call_api(
                    method,
                    "api_media_changes_history_detail",
                    args=("movie", "abc"),
                    headers=self.auth_headers,
                )

                self.assertEqual(response.status_code, 404)
