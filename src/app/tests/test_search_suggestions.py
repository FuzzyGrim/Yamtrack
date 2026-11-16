from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from app.models import TV, Item, MediaTypes, Movie, Season, Sources
from app.search_views import _local_media_results, get_saved_suggestions
from users.models import Status


class SearchSuggestionsViewTests(TestCase):
    """Tests for the saved-library autocomplete suggestions endpoint."""

    def setUp(self):
        """Create a user with one saved movie and log them in."""
        self.user = get_user_model().objects.create_user(
            username="suggest-user",
            password="12345",
        )
        self.client.force_login(self.user)

        self.item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="The Godfather",
            image="http://example.com/image.jpg",
        )
        Movie.objects.create(
            item=self.item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=1,
        )

    def _get(self, query, media_type=MediaTypes.MOVIE.value):
        """Request the suggestions fragment for a query/media_type."""
        return self.client.get(
            reverse("search_suggestions"),
            {"q": query, "media_type": media_type},
        )

    def test_matching_saved_item_is_suggested(self):
        """A saved item matching the query is returned with a detail link."""
        response = self._get("godfa")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The Godfather")
        # Links straight to the item detail page.
        self.assertContains(response, "/details/")
        # The footer offers the full online search.
        self.assertContains(response, "Search online for")

    def test_tv_suggestions_do_not_load_tracking_graph_or_availability(self):
        """Typing must not hydrate seasons, episodes or availability JSON."""
        item = Item.objects.create(
            media_id="show", source="tmdb", media_type="tv", title="Saved Show",
            watch_providers={"US": {"flatrate": [{"provider_name": "Unused"}]}},
        )
        TV.objects.create(item=item, user=self.user, status=Status.COMPLETED.value)

        with CaptureQueriesContext(connection) as queries:
            suggestions = get_saved_suggestions(self.user, "tv", "Saved")

        self.assertEqual([row["title"] for row in suggestions], ["Saved Show"])
        sql = "\n".join(query["sql"] for query in queries)
        for column_or_table in ('"watch_providers"', 'FROM "app_season"', 'FROM "app_episode"'):
            self.assertNotIn(column_or_table, sql)
        self.assertFalse(any('SELECT "app_tv"."id"' in query["sql"] for query in queries))

    def test_tv_suggestion_says_tv_and_reads_status_without_the_graph(self):
        """A show reads "TV · Status" without loading any season or episode rows."""
        item = Item.objects.create(
            media_id="show-2", source="tmdb", media_type="tv", title="Status Show",
        )
        TV.objects.create(item=item, user=self.user, status=Status.IN_PROGRESS.value)

        with CaptureQueriesContext(connection) as queries:
            suggestions = get_saved_suggestions(self.user, "tv", "Status Show")

        self.assertEqual([row["subtitle"] for row in suggestions], ["TV · In progress"])
        sql = "\n".join(query["sql"] for query in queries)
        for table in ('FROM "app_season"', 'FROM "app_episode"'):
            self.assertNotIn(table, sql)


    def test_local_tv_cards_do_not_load_availability(self):
        """The full search still hydrates tracking data, without wide JSON."""
        item = Item.objects.create(
            media_id="show", source="tmdb", media_type="tv", title="Saved Show",
        )
        show = TV.objects.create(item=item, user=self.user, status=Status.COMPLETED.value)
        season_item = Item.objects.create(
            media_id="show", source="tmdb", media_type="season", title="Saved Show",
            season_number=1,
        )
        Season.objects.create(
            item=season_item, user=self.user, related_tv=show, status=Status.COMPLETED.value,
        )

        with CaptureQueriesContext(connection) as queries:
            results, total = _local_media_results(self.user, "tv", "Saved", 8)

        self.assertEqual(total, 1)
        self.assertEqual(results[0]["media"], show)
        self.assertNotIn('"watch_providers"', "\n".join(query["sql"] for query in queries))

    def test_search_matches_both_localized_and_original_titles(self):
        self.item.original_title = "The Godfather"
        self.item.localized_title = "El padrino"
        self.item.save(update_fields=["original_title", "localized_title"])
        for query in ("padrino", "godfather"):
            with self.subTest(query=query):
                response = self._get(query)
                self.assertEqual(len(response.context["suggestions"]), 1)
                self.assertEqual(
                    response.context["suggestions"][0]["title"], "El padrino"
                )

    def test_short_query_returns_empty(self):
        """A single-character query renders nothing (below the min length)."""
        response = self._get("g")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "The Godfather")
        self.assertNotContains(response, "search online")

    def test_non_matching_query_shows_keep_typing_hint(self):
        """A query with no saved matches shows the search-online hint."""
        response = self._get("nonexistenttitle")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "The Godfather")
        self.assertContains(response, "search online")

    def test_scoped_to_selected_media_type(self):
        """A saved movie is not suggested when the book type is selected."""
        response = self._get("godfa", media_type=MediaTypes.BOOK.value)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "The Godfather")

    def test_only_owners_items_are_suggested(self):
        """A different user does not see another user's saved items."""
        other = get_user_model().objects.create_user(
            username="other-user",
            password="12345",
        )
        self.client.force_login(other)
        response = self._get("godfa")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "The Godfather")

    def test_requires_authentication(self):
        """Anonymous requests are redirected by the login-required middleware."""
        self.client.logout()
        response = self._get("godfa")
        self.assertEqual(response.status_code, 302)

    def test_json_suggestions_return_data_without_server_html(self):
        """The universal autocomplete gets URLs and text, never rendered HTML."""
        response = self.client.get(
            reverse("search_suggestions"),
            {"q": "godfa", "media_type": MediaTypes.MOVIE.value, "format": "json"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        payload = response.json()
        self.assertEqual([row["title"] for row in payload["suggestions"]], ["The Godfather"])
        self.assertIn("/details/", payload["suggestions"][0]["url"])
        self.assertNotIn("<a", response.content.decode())

    def test_suggestion_detail_line_names_type_year_and_status_once(self):
        """The detail line reads "Type · Year · Status", in a typed scope and under All media."""
        self.item.release_datetime = datetime(1972, 3, 14, tzinfo=UTC)
        self.item.save(update_fields=["release_datetime"])

        for scope in (MediaTypes.MOVIE.value, "all"):
            response = self.client.get(
                reverse("search_suggestions"),
                {"q": "godfa", "media_type": scope, "format": "json"},
            )
            self.assertEqual(
                [row["subtitle"] for row in response.json()["suggestions"]],
                ["Movie · 1972 · Completed"],
                scope,
            )

    def test_json_suggestions_short_query_returns_empty_list(self):
        response = self.client.get(
            reverse("search_suggestions"),
            {"q": "g", "media_type": MediaTypes.MOVIE.value, "format": "json"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"suggestions": []})

    def test_json_suggestions_without_matches_returns_empty_list(self):
        response = self.client.get(
            reverse("search_suggestions"),
            {"q": "nonexistenttitle", "media_type": MediaTypes.MOVIE.value, "format": "json"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"suggestions": []})

    def _enable_two_types(self):
        """Give the user a saved show, so All media has a second type to search."""
        show = Item.objects.create(
            media_id="show-all", source=Sources.TMDB.value, media_type=MediaTypes.TV.value,
            title="The Godfather Saga",
        )
        TV.objects.create(item=show, user=self.user, status=Status.COMPLETED.value)

    def test_all_media_request_stops_once_the_same_box_asks_again(self):
        """The browser cancels a replaced request; the server stops it before its next type."""
        from django.core.cache import cache

        from app.search_views import suggestion_superseded_check

        cache.clear()
        self._enable_two_types()
        older = suggestion_superseded_check(self.user, "box-12345678")
        newer = suggestion_superseded_check(self.user, "box-12345678")

        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(get_saved_suggestions(self.user, "all", "godfather", superseded=older), [])
        self.assertFalse([query for query in queries if 'FROM "app_item"' in query["sql"]])

        titles = [row["title"] for row in get_saved_suggestions(self.user, "all", "godfather", superseded=newer)]
        self.assertEqual(titles, ["The Godfather Saga", "The Godfather"])

    def test_superseded_check_is_per_box_and_needs_a_valid_id(self):
        from django.core.cache import cache

        from app.search_views import suggestion_superseded_check

        cache.clear()
        first = suggestion_superseded_check(self.user, "box-aaaaaaaa")
        # Another tab or device has its own id and does not stop this one.
        suggestion_superseded_check(self.user, "box-bbbbbbbb")
        self.assertFalse(first())
        for missing in (None, "", "short", "has spaces in it", "x" * 65):
            self.assertIsNone(suggestion_superseded_check(self.user, missing))

    def test_json_suggestions_accept_the_search_box_id(self):
        response = self.client.get(
            reverse("search_suggestions"),
            {"q": "godfa", "media_type": "all", "format": "json", "sid": "box-12345678"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["title"] for row in response.json()["suggestions"]], ["The Godfather"])

    def test_api_suggestions_accept_the_search_box_id(self):
        response = self.client.get(
            "/api/v1/search/suggestions/",
            {"q": "godfa", "media_type": "all", "sid": "box-12345678"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["title"] for row in response.json()["suggestions"]], ["The Godfather"])

    def test_suggestions_turn_off_postgres_jit(self):
        """JIT compiling cost more than the query itself; suggestions run without it."""
        from app.search_views import without_postgres_jit

        ran = []
        with without_postgres_jit():
            ran.append(True)
            if connection.vendor == "postgresql":
                with connection.cursor() as cursor:
                    cursor.execute("SHOW jit")
                    self.assertEqual(cursor.fetchone()[0], "off")
        self.assertEqual(ran, [True])
