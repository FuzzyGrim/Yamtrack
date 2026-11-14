# FORK: tests for discover, home, and collection-parity API endpoints.
from http import HTTPStatus as HTTP  # noqa: N814
from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings

from app.discover import tab_cache
from app.discover.schemas import CandidateItem, RowResult
from app.models import CollectionEntry, DiscoverFeedback, Item, MediaTypes, Sources
from app.providers import services

from .base import FloppyApiTestCase


class CollectionStatusTests(FloppyApiTestCase):
    """GET collection/status/{item_id}."""

    def test_status_reflects_collection(self):
        """Items with entries report has_collection_data true."""
        item = self.items_by_type[MediaTypes.MOVIE.value][0]
        no_entry = self.call_api(
            "get",
            "api_collection_status",
            args=(item.id,),
            headers=self.auth_headers,
        )
        self.assertEqual(no_entry.status_code, HTTP.OK)
        self.assertFalse(no_entry.json()["has_collection_data"])

        CollectionEntry.objects.create(user=self.user1, item=item)
        with_entry = self.call_api(
            "get",
            "api_collection_status",
            args=(item.id,),
            headers=self.auth_headers,
        )
        self.assertTrue(with_entry.json()["has_collection_data"])

    def test_unknown_item_not_found(self):
        """Unknown item ids 404."""
        response = self.call_api(
            "get",
            "api_collection_status",
            args=(999999,),
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, HTTP.NOT_FOUND)


class CollectionSeasonTests(FloppyApiTestCase):
    """DELETE collection/seasons/{season_item_id}."""

    def test_no_sonarr_entries_not_found(self):
        """Seasons without Sonarr-backed collected episodes return 404."""
        season_item = self.items_by_type[MediaTypes.SEASON.value][0]
        response = self.call_api(
            "delete",
            "api_collection_season",
            args=(season_item.id,),
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, HTTP.NOT_FOUND)


class DiscoverTests(FloppyApiTestCase):
    """Discover rows, refresh, and hidden toggles."""

    @patch("api.fork_views_discover._discover_response_rows", return_value=[])
    def test_rows_endpoint(self, _mock_rows):
        """GET discover returns the resolved media type and rows."""
        response = self.call_api(
            "get",
            "api_discover",
            params={"media_type": MediaTypes.MOVIE.value},
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, HTTP.OK)
        payload = response.json()
        self.assertEqual(payload["media_type"], MediaTypes.MOVIE.value)
        self.assertEqual(payload["rows"], [])

    def test_rows_carry_loading_media_options_and_match_wording(self):
        """Rows expose the rebuild flag, the server's media chips and match labels."""
        row = RowResult(
            key="top_picks_for_you",
            title="Top Picks For You",
            mission="Taste",
            why="why",
            source="tmdb",
            items=[],
        )
        with patch(
            "api.fork_views_discover._discover_response_rows",
            return_value=[row],
        ):
            payload = self.call_api(
                "get",
                "api_discover",
                params={"media_type": MediaTypes.MOVIE.value},
                headers=self.auth_headers,
            ).json()
        self.assertIn("loading", payload)
        self.assertEqual(payload["media_options"][0]["value"], "all")
        self.assertEqual(payload["media_options"][-1]["value"], "hidden")
        self.assertEqual(payload["rows"][0]["match_label"], "Taste match")

    def test_status_reports_rebuild_state(self):
        """GET discover/status answers with the cache flags the web polls."""
        response = self.call_api(
            "get",
            "api_discover_status",
            params={"media_type": MediaTypes.MOVIE.value},
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, HTTP.OK)
        payload = response.json()
        for key in ("exists", "is_stale", "is_refreshing", "recently_built"):
            self.assertIn(key, payload)

    def test_hidden_entries_carry_artwork_and_identity(self):
        """Hidden entries return image, source and media id for their cards."""
        item = self.items_by_type[MediaTypes.MOVIE.value][0]
        self.call_api(
            "post",
            "api_discover_hidden",
            payload={"item_id": item.id, "action": "hide"},
            headers=self.auth_headers,
        )
        entry = self.call_api(
            "get",
            "api_discover_hidden",
            headers=self.auth_headers,
        ).json()["results"][0]
        self.assertEqual(entry["media_id"], str(item.media_id))
        self.assertEqual(entry["source"], item.source)
        self.assertIn("image", entry)

    @patch("api.fork_views_discover.discover_tab_cache.schedule_tab_refresh")
    def test_refresh_queues_rebuild(self, mock_schedule):
        """POST discover/refresh returns 202 and schedules a rebuild."""
        response = self.call_api(
            "post",
            "api_discover_refresh",
            payload={"media_type": MediaTypes.MOVIE.value},
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, HTTP.ACCEPTED)
        mock_schedule.assert_called_once()

    def test_endpoints_404_when_discover_turned_off(self):
        """Discover off applies to the REST endpoints too, not just the web page."""
        self.user1.show_discover = False
        self.user1.save(update_fields=["show_discover"])

        for method, name in (
            ("get", "api_discover"),
            ("post", "api_discover_refresh"),
            ("get", "api_discover_hidden"),
        ):
            with self.subTest(name=name):
                response = self.call_api(method, name, headers=self.auth_headers)
                self.assertEqual(response.status_code, HTTP.NOT_FOUND)

    def test_hide_and_unhide_item(self):
        """POST discover/hidden toggles NOT_INTERESTED feedback."""
        item = self.items_by_type[MediaTypes.MOVIE.value][0]
        hidden = self.call_api(
            "post",
            "api_discover_hidden",
            payload={"item_id": item.id, "action": "hide"},
            headers=self.auth_headers,
        )
        self.assertEqual(hidden.status_code, HTTP.OK)
        self.assertTrue(
            DiscoverFeedback.objects.filter(user=self.user1, item=item).exists(),
        )

        listed = self.call_api(
            "get",
            "api_discover_hidden",
            headers=self.auth_headers,
        )
        self.assertEqual(len(listed.json()["results"]), 1)

        unhidden = self.call_api(
            "post",
            "api_discover_hidden",
            payload={"item_id": item.id, "action": "unhide"},
            headers=self.auth_headers,
        )
        self.assertEqual(unhidden.status_code, HTTP.OK)
        self.assertFalse(
            DiscoverFeedback.objects.filter(user=self.user1, item=item).exists(),
        )

    def test_hide_by_identity_creates_untracked_item(self):
        """POST discover/hidden with an identity hides an untracked candidate."""
        hidden = self.call_api(
            "post",
            "api_discover_hidden",
            payload={
                "action": "hide",
                "source": "tmdb",
                "media_type": MediaTypes.MOVIE.value,
                "media_id": "discover-hide-identity",
                "title": "Dismissed film",
                "image": "https://example.com/art.jpg",
            },
            headers=self.auth_headers,
        )
        self.assertEqual(hidden.status_code, HTTP.OK)
        item = Item.objects.get(
            source="tmdb",
            media_type=MediaTypes.MOVIE.value,
            media_id="discover-hide-identity",
        )
        self.assertEqual(item.title, "Dismissed film")
        self.assertTrue(
            DiscoverFeedback.objects.filter(user=self.user1, item=item).exists(),
        )

    def test_hide_rejects_unknown_media_type(self):
        """An identity hide with a bad media_type is a 400."""
        rejected = self.call_api(
            "post",
            "api_discover_hidden",
            payload={
                "action": "hide",
                "source": "tmdb",
                "media_type": "not-a-type",
                "media_id": "whatever",
            },
            headers=self.auth_headers,
        )
        self.assertEqual(rejected.status_code, HTTP.BAD_REQUEST)

    def test_invalid_action_rejected(self):
        """Unknown hidden actions return 400."""
        response = self.call_api(
            "post",
            "api_discover_hidden",
            payload={"item_id": 1, "action": "nope"},
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, HTTP.BAD_REQUEST)


class HomeTests(FloppyApiTestCase):
    """GET /home returns serialized home groups."""

    def test_home_groups(self):
        """Groups include the user's tracked media rows, fully serialized."""
        response = self.call_api("get", "api_home", headers=self.auth_headers)
        self.assertEqual(response.status_code, HTTP.OK)
        groups = response.json()["groups"]
        self.assertTrue(groups)
        first_rows = groups[0]["rows"]
        self.assertTrue(first_rows)
        self.assertIn("items", first_rows[0])
        row_item = first_rows[0]["items"][0]
        # Regression guard for the HomeRowEntry serializer_map gap: items must
        # not fall back to a bare {"title": ...} dict.
        self.assertIn("id", row_item)
        self.assertIn("item", row_item)
        self.assertIn("url", row_item["item"])
        self.assertIn("ids", row_item["item"])
        self.assertIn("image", row_item["item"])

    def test_home_hides_seasons_group_when_seasons_disabled(self):
        """Home follows the sidebar: no Seasons group once Seasons is off (#1560)."""

        def group_types():
            response = self.call_api("get", "api_home", headers=self.auth_headers)
            self.assertEqual(response.status_code, HTTP.OK)
            return [group["media_type"] for group in response.json()["groups"]]

        self.assertIn(MediaTypes.SEASON.value, group_types())
        self.user1.season_enabled = False
        self.user1.save(update_fields=["season_enabled"])
        self.assertNotIn(MediaTypes.SEASON.value, group_types())

    def test_home_invalid_limit_rejected(self):
        """Non-numeric limits return 400."""
        response = self.call_api(
            "get",
            "api_home",
            params={"limit": "abc"},
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, HTTP.BAD_REQUEST)

    def test_home_presentation_returns_shared_shelf_models(self):
        """Presentation mode returns shared card models and deferred row ids."""
        response = self.call_api(
            "get",
            "api_home",
            params={"presentation": 1, "deferred": 1},
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, HTTP.OK)
        payload = response.json()
        self.assertIn("showMediaTypeHeaders", payload)
        self.assertIn("labels", payload)
        built_rows = [
            row for group in payload["groups"] for row in group["rows"]
        ]
        deferred_ids = [
            row_id
            for group in payload["deferredGroups"]
            for row_id in group["rowIds"]
        ]
        self.assertTrue(built_rows or deferred_ids)
        if built_rows:
            item = built_rows[0]["items"][0]
            self.assertIn("card", item)
            self.assertIn("title", item["card"])
            self.assertIn("href", item["card"])
        if built_rows:
            row_id = built_rows[0]["rowId"]
            row_response = self.call_api(
                "get",
                "api_home_row",
                args=(row_id,),
                headers=self.auth_headers,
            )
            self.assertEqual(row_response.status_code, HTTP.OK)
            self.assertEqual(row_response.json()["row"]["rowId"], row_id)

    def test_home_row_accepts_signed_in_session(self):
        """The web Home shelves fetch rows with the session cookie, not a token."""
        self.client.force_login(self.user1)
        response = self.call_api("get", "api_home_row", args=(1,))
        self.assertEqual(response.status_code, HTTP.OK)

    def _paged_row(self):
        """Return (row_id, total) of a built Home row with at least two items."""
        payload = self.call_api(
            "get",
            "api_home",
            params={"presentation": 1, "limit": 50},
            headers=self.auth_headers,
        ).json()
        paged = [
            (row["rowId"], row["total"])
            for group in payload["groups"]
            for row in group["rows"]
            if (row.get("total") or 0) >= 2
        ]
        if not paged:
            self.skipTest("fixture has no Home row with two items")
        return paged[0]

    def test_home_row_continuation_inside_the_row_returns_more(self):
        """B13: a continuation request below the row total must return items."""
        row_id, total = self._paged_row()
        response = self.call_api(
            "get",
            "api_home_row",
            args=(row_id,),
            params={"limit": 1, "offset": 1},
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, HTTP.OK)
        row = response.json()["row"]
        self.assertIsNotNone(row, f"total={total}")
        self.assertTrue(row["items"])

    def test_home_row_past_the_end_reports_exhaustion(self):
        """B13: running off the end returns an empty window with loadedCount at the total."""
        row_id, total = self._paged_row()
        response = self.call_api(
            "get",
            "api_home_row",
            args=(row_id,),
            params={"limit": 1, "offset": total + 5},
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, HTTP.OK)
        row = response.json()["row"]
        self.assertEqual(row["items"], [])
        self.assertEqual(row["loadedCount"], total)

    def test_home_row_hides_a_row_the_user_cannot_see(self):
        """A missing/disabled row returns an empty window, not an error."""
        response = self.call_api(
            "get",
            "api_home_row",
            args=(999999,),
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, HTTP.OK)
        self.assertIsNone(response.json()["row"])

    def test_home_row_sort_hides_demo_write_for_demo_accounts(self):
        """Demo accounts stay view-only, matching the web Home toggle."""
        self.user1.is_demo = True
        self.user1.save(update_fields=["is_demo"])
        response = self.call_api(
            "post",
            "api_home_row_sort",
            args=(1,),
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, HTTP.FORBIDDEN)

    def test_home_artwork_poll_returns_rows_and_poll_ids(self):
        """The coalesced cover poll returns presentation rows and poll ids."""
        home = self.call_api(
            "get",
            "api_home",
            params={"presentation": 1, "deferred": 1},
            headers=self.auth_headers,
        )
        row_ids = [
            row["rowId"]
            for group in home.json()["groups"]
            for row in group["rows"]
        ]
        response = self.call_api(
            "get",
            "api_home_artwork",
            params={"ids": ",".join(str(row_id) for row_id in row_ids[:2])},
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, HTTP.OK)
        payload = response.json()
        self.assertIn("rows", payload)
        self.assertIn("pollRowIds", payload)


class RecommendationsTests(FloppyApiTestCase):
    """GET recommendations: the flat Top Picks list with provider ids."""

    def _row(self, key, *items):
        return RowResult(
            key=key,
            title=key,
            mission="",
            why="",
            source="local",
            items=list(items),
        )

    def _pick(self, media_id, title, media_type=MediaTypes.MOVIE.value):
        return CandidateItem(
            media_type=media_type,
            source=Sources.TMDB.value,
            media_id=media_id,
            title=title,
            release_date="2020-01-01",
            genres=["Drama"],
        )

    def _get(self, params=None):
        return self.call_api(
            "get",
            "api_recommendations",
            params=params,
            headers=self.auth_headers,
        )

    def test_returns_top_picks_with_ids(self):
        """Only the Top Picks row is returned, with stored ids when known."""
        stored = self.items_by_type[MediaTypes.MOVIE.value][0]
        stored.provider_external_ids = {"imdb_id": "tt0111161", "tmdb_id": "701"}
        stored.save(update_fields=["provider_external_ids"])
        rows = [
            self._row("trending_right_now", self._pick("1", "Trending")),
            self._row(
                "top_picks_for_you",
                self._pick(stored.media_id, "Stored"),
                self._pick("9001", "Fetched"),
            ),
        ]
        metadata = {"provider_external_ids": {"imdb_id": "tt9999999"}}
        with (
            patch("api.fork_views_discover._discover_response_rows", return_value=rows),
            patch(
                "api.fork_views_discover.services.get_media_metadata",
                return_value=metadata,
            ) as mock_metadata,
        ):
            response = self._get({"media_type": MediaTypes.MOVIE.value})

        self.assertEqual(response.status_code, HTTP.OK)
        results = response.json()["results"]
        self.assertEqual([r["title"] for r in results], ["Stored", "Fetched"])
        self.assertEqual(results[0]["ids"], {"tmdb": "701", "imdb": "tt0111161"})
        self.assertEqual(results[1]["ids"], {"imdb": "tt9999999", "tmdb": "9001"})
        mock_metadata.assert_called_once()

    @override_settings(DISCOVER_EAGER_REBUILD_DAYS=3)
    def test_a_call_counts_as_using_discover(self):
        """A plugin reading picks keeps the user's Discover rebuilding after writes."""
        cache.clear()
        self.assertFalse(tab_cache.recently_used(self.user1.id))

        # The tab is cold: the request queues its build and returns no picks.
        with patch("app.discover.tab_cache.schedule_tab_refresh", return_value=True):
            response = self._get({"media_type": MediaTypes.MOVIE.value})

        self.assertEqual(response.status_code, HTTP.OK)
        self.assertTrue(tab_cache.recently_used(self.user1.id))

    def test_tv_ids_include_tvdb(self):
        """TV picks carry the TVDB id a media-server plugin matches on."""
        rows = [
            self._row(
                "top_picks_for_you",
                self._pick("555", "Show", media_type=MediaTypes.TV.value),
            ),
        ]
        metadata = {"provider_external_ids": {"tvdb_id": 81189, "imdb_id": "tt0903747"}}
        with (
            patch("api.fork_views_discover._discover_response_rows", return_value=rows),
            patch(
                "api.fork_views_discover.services.get_media_metadata",
                return_value=metadata,
            ),
        ):
            response = self._get({"media_type": MediaTypes.TV.value})

        self.assertEqual(
            response.json()["results"][0]["ids"],
            {"imdb": "tt0903747", "tvdb": "81189", "tmdb": "555"},
        )

    def test_tvdb_source_keeps_its_id_when_lookup_fails(self):
        """A TVDB-sourced pick still reports its TVDB id without provider data."""
        pick = CandidateItem(
            media_type=MediaTypes.TV.value,
            source=Sources.TVDB.value,
            media_id="81189",
            title="TVDB Show",
        )
        rows = [self._row("top_picks_for_you", pick)]
        with (
            patch("api.fork_views_discover._discover_response_rows", return_value=rows),
            patch(
                "api.fork_views_discover.services.get_media_metadata",
                side_effect=services.ProviderAPIError(Sources.TVDB.value, None),
            ),
        ):
            response = self._get({"media_type": MediaTypes.TV.value})

        self.assertEqual(response.json()["results"][0]["ids"], {"tvdb": "81189"})

    def test_provider_failure_keeps_pick(self):
        """A provider outage drops the extra ids but keeps the recommendation."""
        rows = [self._row("top_picks_for_you", self._pick("42", "Offline"))]
        with (
            patch("api.fork_views_discover._discover_response_rows", return_value=rows),
            patch(
                "api.fork_views_discover.services.get_media_metadata",
                side_effect=services.ProviderAPIError(Sources.TMDB.value, None),
            ),
        ):
            response = self._get()

        self.assertEqual(response.status_code, HTTP.OK)
        self.assertEqual(response.json()["results"][0]["ids"], {"tmdb": "42"})

    def test_pagination(self):
        """Limit and offset page through the picks."""
        rows = [
            self._row(
                "top_picks_for_you",
                *[self._pick(str(n), f"Movie {n}") for n in range(1, 4)],
            ),
        ]
        with (
            patch("api.fork_views_discover._discover_response_rows", return_value=rows),
            patch(
                "api.fork_views_discover.services.get_media_metadata",
                return_value={},
            ),
        ):
            response = self._get({"limit": 2, "offset": 2})

        payload = response.json()
        self.assertEqual(payload["pagination"]["total"], 3)
        self.assertEqual([r["title"] for r in payload["results"]], ["Movie 3"])

    def test_no_top_picks_row_is_empty(self):
        """A tab without a Top Picks row returns an empty list."""
        with patch(
            "api.fork_views_discover._discover_response_rows",
            return_value=[],
        ):
            response = self._get()

        self.assertEqual(response.status_code, HTTP.OK)
        self.assertEqual(response.json()["results"], [])

    def test_unsupported_media_type_rejected(self):
        """Only movie and tv are supported."""
        response = self._get({"media_type": MediaTypes.GAME.value})
        self.assertEqual(response.status_code, HTTP.BAD_REQUEST)

    def test_discover_off_is_not_found(self):
        """Users who turned Discover off get a 404, like the Discover API."""
        self.user1.show_discover = False
        self.user1.save(update_fields=["show_discover"])
        response = self._get()
        self.assertEqual(response.status_code, HTTP.NOT_FOUND)
