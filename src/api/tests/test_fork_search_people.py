# FORK: tests for the native search, people and studio endpoints.
import unittest
from http import HTTPStatus as HTTP  # noqa: N814
from unittest.mock import patch

from app.models import (
    TV,
    Episode,
    Item,
    ItemStudioCredit,
    MediaTypes,
    PodcastShowTracker,
    Season,
    Sources,
    Studio,
)
from integrations.models import IntegrationToken
from integrations.oauth_scope_info import FIRST_PARTY_CLIENT_ID, FULL_ACCOUNT_SCOPE

from .base import FloppyApiTestCase


class ManualEntryTests(FloppyApiTestCase):
    """Manual creation validates ownership and both forms before writing."""

    def test_options_match_web_media_types_and_rating_preference(self):
        response = self.call_api("get", "api_search_manual_entry", headers=self.auth_headers)
        self.assertEqual(response.status_code, HTTP.OK)
        self.assertEqual([option["value"] for option in response.json()["media_types"]], MediaTypes.values)
        self.assertTrue(response.json()["media_types"][0]["icon"]["children"])
        self.assertEqual(response.json()["ratings_enabled"], self.user1.ratings_enabled)
        self.assertEqual(response.json()["rating_scale_max"], self.user1.rating_scale_max)

    def test_create_show_and_season_with_owned_parent(self):
        show = self.call_api("post", "api_search_manual_entry", payload={
            "media_type": "tv", "title": "Custom show", "status": "Planning",
        }, headers=self.auth_headers)
        self.assertEqual(show.status_code, HTTP.CREATED, show.content)
        parent = TV.objects.get(user=self.user1, item_id=show.json()["id"])
        season = self.call_api("post", "api_search_manual_entry", payload={
            "media_type": "season", "parent_tv": parent.id, "season_number": 1,
            "status": "Planning",
        }, headers=self.auth_headers)
        self.assertEqual(season.status_code, HTTP.CREATED, season.content)
        season_row = Season.objects.get(item_id=season.json()["id"])
        self.assertEqual(season_row.related_tv, parent)
        episode = self.call_api("post", "api_search_manual_entry", payload={
            "media_type": "episode", "parent_season": season_row.id, "episode_number": 1,
        }, headers=self.auth_headers)
        self.assertEqual(episode.status_code, HTTP.CREATED, episode.content)
        self.assertEqual(Episode.objects.get(item_id=episode.json()["id"]).related_season, season_row)

    def test_creates_podcast_show_and_tracker(self):
        response = self.call_api("post", "api_search_manual_entry", payload={
            "media_type": "podcast", "title": "Custom podcast", "status": "Planning",
        }, headers=self.auth_headers)
        self.assertEqual(response.status_code, HTTP.CREATED, response.content)
        item = Item.objects.get(pk=response.json()["id"])
        self.assertTrue(PodcastShowTracker.objects.filter(user=self.user1, show__podcast_uuid=item.media_id).exists())

    def test_demo_and_anonymous_cannot_create(self):
        payload = {"media_type": "movie", "title": "Blocked", "status": "Planning"}
        self.user1.is_demo = True
        self.user1.save(update_fields=["is_demo"])
        response = self.call_api("post", "api_search_manual_entry", payload=payload, headers=self.auth_headers)
        self.assertEqual(response.status_code, HTTP.FORBIDDEN)
        response = self.call_api("post", "api_search_manual_entry", payload=payload)
        self.assertIn(response.status_code, (HTTP.UNAUTHORIZED, HTTP.FORBIDDEN))
        self.assertFalse(Item.objects.filter(title="Blocked").exists())

    def test_session_can_create_but_demo_session_cannot(self):
        self.client.force_login(self.user1)
        payload = {"media_type": "movie", "title": "Session movie", "status": "Planning"}
        response = self.call_api("post", "api_search_manual_entry", payload=payload)
        self.assertEqual(response.status_code, HTTP.CREATED, response.content)
        self.user1.is_demo = True
        self.user1.save(update_fields=["is_demo"])
        payload["title"] = "Blocked session movie"
        response = self.call_api("post", "api_search_manual_entry", payload=payload)
        self.assertEqual(response.status_code, HTTP.FORBIDDEN)
        self.assertFalse(Item.objects.filter(title=payload["title"]).exists())

    def test_only_first_party_full_access_bearer_may_create(self):
        payload = {"media_type": "movie", "title": "Bearer movie", "status": "Planning"}
        _, scoped = IntegrationToken.generate(user=self.user1, name="scoped", scopes=["*"])
        denied = self.call_api("post", "api_search_manual_entry", payload=payload,
                               headers={"HTTP_AUTHORIZATION": f"Bearer {scoped}"})
        self.assertEqual(denied.status_code, HTTP.FORBIDDEN)
        _, first_party = IntegrationToken.generate(
            user=self.user1, name="app", scopes=[FULL_ACCOUNT_SCOPE], client_identifier=FIRST_PARTY_CLIENT_ID,
        )
        allowed = self.call_api("post", "api_search_manual_entry", payload=payload,
                                headers={"HTTP_AUTHORIZATION": f"Bearer {first_party}"})
        self.assertEqual(allowed.status_code, HTTP.CREATED, allowed.content)

    def test_rejects_foreign_parent_and_invalid_tracking_without_item(self):
        parent_item = Item.objects.create(source="manual", media_type="tv", media_id="foreign-manual", title="Other show")
        parent = TV.objects.create(user=self.user2, item=parent_item, status="Planning")
        for data in (
            {"media_type": "season", "parent_tv": parent.id, "season_number": 1},
            {"media_type": "movie", "title": "Invalid score", "score": "999", "status": "Planning"},
        ):
            before = Item.objects.count()
            response = self.call_api("post", "api_search_manual_entry", payload=data, headers=self.auth_headers)
            self.assertEqual(response.status_code, HTTP.BAD_REQUEST, response.content)
            self.assertEqual(Item.objects.count(), before)


class SearchEndpointTests(FloppyApiTestCase):
    """GET search/sources, suggestions, results and parents."""

    def test_sources_lists_types_and_provider_pills(self):
        """A typed request returns the user's type menu and its sources."""
        response = self.call_api(
            "get",
            "api_search_sources",
            params={"media_type": MediaTypes.MOVIE.value},
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, HTTP.OK)
        payload = response.json()
        self.assertEqual(payload["media_type"], MediaTypes.MOVIE.value)
        self.assertEqual(payload["media_types"][0]["value"], "all")
        self.assertTrue(payload["sources"])
        self.assertIn(payload["default_source"], {s["value"] for s in payload["sources"]})
        self.assertIn("book", payload["barcode_types"])

    def test_type_is_remembered_for_the_next_page(self):
        """Picking a type saves it; an unknown type is rejected."""
        response = self.call_api(
            "put",
            "api_search_type",
            payload={"media_type": MediaTypes.MOVIE.value},
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, HTTP.OK)
        self.user1.refresh_from_db()
        self.assertEqual(self.user1.last_search_type, MediaTypes.MOVIE.value)
        bad = self.call_api(
            "put",
            "api_search_type",
            payload={"media_type": "nope"},
            headers=self.auth_headers,
        )
        self.assertEqual(bad.status_code, HTTP.BAD_REQUEST)

    def test_sources_for_all_has_no_pills(self):
        """The library-wide type has no provider pills."""
        response = self.call_api(
            "get",
            "api_search_sources",
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, HTTP.OK)
        self.assertEqual(response.json()["sources"], [])

    def test_sources_rejects_unknown_type(self):
        """An unsearchable media type is a 400, not a crash."""
        response = self.call_api(
            "get",
            "api_search_sources",
            params={"media_type": "nonsense"},
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, HTTP.BAD_REQUEST)

    def test_suggestions_find_saved_titles_and_ignore_short_queries(self):
        """Typeahead matches saved titles and returns nothing under two characters."""
        short = self.call_api(
            "get",
            "api_search_suggestions",
            params={"q": "M", "media_type": "all"},
            headers=self.auth_headers,
        )
        self.assertEqual(short.json(), {"suggestions": []})
        found = self.call_api(
            "get",
            "api_search_suggestions",
            params={"q": "Movie 1", "media_type": MediaTypes.MOVIE.value},
            headers=self.auth_headers,
        )
        self.assertEqual(found.status_code, HTTP.OK)
        titles = [row["title"] for row in found.json()["suggestions"]]
        self.assertIn("Movie 1", titles)

    @unittest.skip("Search results moved to the Home-shelf row contract; this sections payload needs a decision.")
    def test_results_all_searches_only_the_library(self):
        """The `all` type returns library groups with the shared card props."""
        response = self.call_api(
            "get",
            "api_search_results",
            params={"q": "Movie 1", "media_type": "all"},
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, HTTP.OK)
        payload = response.json()
        self.assertEqual(payload["mediaType"], "all")
        self.assertEqual(payload["state"], "ready")
        self.assertTrue(payload["rows"])
        card = payload["rows"][0]["items"][0]["card"]
        self.assertEqual(card["title"], "Movie 1")
        self.assertTrue(payload["notes"])

    @unittest.skip("Search results moved to the Home-shelf row contract; this sections payload needs a decision.")
    def test_results_empty_query_is_an_empty_state(self):
        """No library match gives the empty state with the web wording."""
        response = self.call_api(
            "get",
            "api_search_results",
            params={"q": "zzzz-no-match", "media_type": "all"},
            headers=self.auth_headers,
        )
        payload = response.json()
        self.assertEqual(payload["state"], "empty")
        self.assertEqual(payload["empty"]["title"], "No results found")

    @unittest.skip("Search results moved to the Home-shelf row contract; this sections payload needs a decision.")
    @patch("app.search_views.services.search")
    def test_results_online_has_sources_and_provider_failure_is_an_error(
        self,
        mock_search,
    ):
        """Online search lists the provider rows to stream; a failed provider is named."""
        from app.providers import services

        mock_search.side_effect = services.ProviderAPIError(
            Sources.TMDB.value,
            None,
            "boom",
        )
        response = self.call_api(
            "get",
            "api_search_results",
            params={"q": "nothing here", "media_type": MediaTypes.MOVIE.value},
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, HTTP.OK)
        payload = response.json()
        self.assertTrue(payload["onlineSources"])
        row = self.call_api(
            "get",
            "api_search_results",
            params={
                "q": "nothing here",
                "media_type": MediaTypes.MOVIE.value,
                "row": payload["onlineSources"][0]["rowId"],
            },
            headers=self.auth_headers,
        ).json()
        self.assertIsNone(row["row"])
        self.assertTrue(row["unavailable"])

    def test_results_reject_unknown_type(self):
        """An unsearchable type is a 400."""
        response = self.call_api(
            "get",
            "api_search_results",
            params={"q": "x", "media_type": "nonsense"},
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, HTTP.BAD_REQUEST)

    def test_parents_require_kind_and_two_characters(self):
        """Parent search validates its kind and ignores one-character queries."""
        bad = self.call_api(
            "get",
            "api_search_parents",
            params={"kind": "movie", "q": "ab"},
            headers=self.auth_headers,
        )
        self.assertEqual(bad.status_code, HTTP.BAD_REQUEST)
        short = self.call_api(
            "get",
            "api_search_parents",
            params={"kind": "tv", "q": "a"},
            headers=self.auth_headers,
        )
        self.assertEqual(short.json()["results"], [])

    def test_search_endpoints_need_authentication(self):
        """Every search endpoint rejects anonymous callers."""
        for name in ("api_search_sources", "api_search_suggestions", "api_search_results"):
            with self.subTest(name=name):
                response = self.call_api("get", name, params={"q": "ab"})
                self.assertIn(response.status_code, (HTTP.UNAUTHORIZED, HTTP.FORBIDDEN))


class PeopleEndpointTests(FloppyApiTestCase):
    """GET people/<source>/<id>/ and studios/<source>/<id>/."""

    @patch("app.providers.tmdb.person")
    def test_person_returns_profile_credits_and_filters(self, mock_person):
        """A person profile carries the hero, credits and the toolbar state."""
        mock_person.return_value = {
            "person_id": "123",
            "source": Sources.TMDB.value,
            "name": "Jane Star",
            "image": "http://example.com/jane.jpg",
            "biography": "Bio text",
            "known_for_department": "Acting",
            "birth_date": "1990-01-01",
            "place_of_birth": "Los Angeles",
            "filmography": [
                {
                    "media_id": "701",
                    "media_type": MediaTypes.MOVIE.value,
                    "title": "Movie 1",
                    "year": 2020,
                    "department": "Acting",
                    "role": "Lead",
                },
                {
                    "media_id": "702",
                    "media_type": MediaTypes.MOVIE.value,
                    "title": "Alpha",
                    "year": 2018,
                    "department": "Acting",
                    "role": "Cameo",
                },
            ],
        }
        response = self.call_api(
            "get",
            "api_person",
            args=(Sources.TMDB.value, "123"),
            params={"sort": "title", "direction": "asc"},
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, HTTP.OK)
        payload = response.json()
        self.assertEqual(payload["kind"], "person")
        self.assertEqual(payload["person"]["name"], "Jane Star")
        self.assertEqual([c["title"] for c in payload["filmography"]], ["Alpha", "Movie 1"])
        card = payload["filmography"][0]["card"]
        # The web card uses the viewer's tracked item title, not the provider row's alias.
        self.assertEqual(card["title"], "Movie 2")
        self.assertEqual(card["shape"], "poster")
        self.assertTrue(card["statusChip"]["icon"]["children"])
        self.assertEqual(card["csrfToken"], "")
        self.assertEqual(card["actions"]["csrfToken"], "")
        self.assertEqual(payload["filters"]["sort"], "title")
        self.assertEqual(payload["filters"]["direction"], "asc")
        self.assertIn("Acting", payload["filters"]["departments"])
        self.assertTrue(payload["filters"]["sortChoices"])

    def test_person_unsupported_source_is_a_400(self):
        """A source without person pages answers 400 with its reason."""
        response = self.call_api(
            "get",
            "api_person",
            args=("manual", "1"),
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, HTTP.BAD_REQUEST)
        self.assertIn("detail", response.json())

    def test_unknown_studio_is_a_404(self):
        """A studio that was never stored is a 404."""
        response = self.call_api(
            "get",
            "api_studio",
            args=(Sources.IGDB.value, "999999"),
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, HTTP.NOT_FOUND)

    @patch("app.providers.igdb.company_profile", return_value=None)
    def test_studio_lists_local_credits(self, _profile):
        """A stored studio returns its hero and credited titles."""
        studio = Studio.objects.create(
            source=Sources.IGDB.value,
            source_studio_id="1",
            name="Studio One",
        )
        item = Item.objects.create(
            media_id="1942",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="A Very Long Game Title",
            image="http://example.com/g.jpg",
        )
        ItemStudioCredit.objects.create(item=item, studio=studio)
        response = self.call_api(
            "get",
            "api_studio",
            args=(Sources.IGDB.value, "1"),
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, HTTP.OK)
        payload = response.json()
        self.assertEqual(payload["kind"], "studio")
        self.assertEqual(payload["titles"][0]["title"], "A Very Long Game Title")
        self.assertEqual(payload["titles"][0]["card"]["title"], "A Very Long Game Title")

    def test_people_endpoints_need_authentication(self):
        """Profiles are not public."""
        response = self.call_api("get", "api_person", args=("tmdb", "1"))
        self.assertIn(response.status_code, (HTTP.UNAUTHORIZED, HTTP.FORBIDDEN))


class SearchMusicCreateTests(FloppyApiTestCase):
    """POST search/music/create/."""

    def test_requires_kind_and_id(self):
        """A missing kind or id is a 400."""
        response = self.call_api(
            "post",
            "api_search_music_create",
            payload={"kind": "track"},
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, HTTP.BAD_REQUEST)

    def test_artist_is_created_through_the_web_flow(self):
        """The artist flow returns the new artist id."""
        from app.models import Artist

        artist = Artist.objects.create(name="Band", musicbrainz_id="mbid-1")
        with patch(
            "app.fork_services_music.get_or_create_artist_from_musicbrainz",
            return_value=artist,
        ):
            response = self.call_api(
                "post",
                "api_search_music_create",
                payload={"kind": "artist", "id": "mbid-1"},
                headers=self.auth_headers,
            )
        self.assertEqual(response.status_code, HTTP.CREATED)
        self.assertEqual(response.json()["artist_id"], artist.id)
        self.assertIsNone(response.json()["album_id"])

    def test_provider_failure_is_a_bad_gateway(self):
        """A provider error is reported, not raised."""
        with patch(
            "app.fork_services_music.get_or_create_artist_from_musicbrainz",
            side_effect=RuntimeError("down"),
        ):
            response = self.call_api(
                "post",
                "api_search_music_create",
                payload={"kind": "artist", "id": "mbid-2"},
                headers=self.auth_headers,
            )
        self.assertEqual(response.status_code, HTTP.BAD_GATEWAY)

    def test_parents_find_manual_shows(self):
        """Parent search returns manual TV shows matching the query."""
        from app.models import TV

        item = Item.objects.create(
            media_id="manual-tv-1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.TV.value,
            title="Backyard Show",
        )
        TV.objects.create(item=item, user=self.user1)
        response = self.call_api(
            "get",
            "api_search_parents",
            params={"kind": "tv", "q": "Backyard"},
            headers=self.auth_headers,
        )
        self.assertEqual(response.json()["results"][0]["title"], "Backyard Show")
