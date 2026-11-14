"""Permission-aware structured detail endpoint tests."""

from http import HTTPStatus as HTTP  # noqa: N814
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps

from api.fork_views_detail import _child_row, _media_card
from app.models import (
    CreditRoleType,
    Item,
    ItemPersonCredit,
    ItemProviderLink,
    ItemStudioCredit,
    MediaTypes,
    Person,
    Sources,
    Status,
    Studio,
)
from app.providers.services import ProviderAPIError
from app.services import metadata_resolution
from integrations.models import IntegrationToken
from users.appearance import default_detail_layouts

from .base import FloppyApiTestCase


class DetailPageTests(FloppyApiTestCase):
    """The detail projection separates public metadata from viewer state."""

    def setUp(self):
        super().setUp()
        self.carousel_patch = patch(
            "api.fork_views_detail.carousel.resolve_carousel_media",
            return_value=None,
        )
        self.carousel_patch.start()

    def tearDown(self):
        self.carousel_patch.stop()
        super().tearDown()

    @patch("api.fork_views_detail._seerr_configured", return_value=True)
    @patch("api.fork_views_detail.services.get_media_metadata", return_value={"title": "Film"})
    def test_seerr_capability_is_private_and_does_not_contact_seerr(self, _metadata, configured):
        item = self.items_by_type[MediaTypes.MOVIE.value][0]
        path = f"/api/v1/media/movie/{item.source}/{item.media_id}/detail/"
        with patch("api.fork_views_detail.seerr_api.SeerrClient.for_user") as client:
            private = self.client.get(path, **self.auth_headers).json()
            public = self.client.get(path, {"public_view": "1"}, **self.auth_headers).json()
        self.assertTrue(private["capabilities"]["can_request_seerr"])
        self.assertFalse(public["capabilities"]["can_request_seerr"])
        configured.assert_called()
        client.assert_not_called()

    @patch("app.templatetags.app_tags.next_episode_url", return_value="/details/next/")
    def test_detail_season_card_reuses_home_progress_and_next_episode(self, _next):
        self.user1.quick_season_update_mobile = "next_episode"
        tracking = SimpleNamespace(status=Status.IN_PROGRESS.value, progress=2, max_progress=8,
                                   progress_percentage=25, next_event=None)
        raw = {"item": {"season_number": 1, "title": "Series", "release_date": "2022-01-01"},
               "media": tracking}
        child = _child_row(raw, media_type="season", parent_media_type="tv", source="tmdb",
                           media_id="1", public_view=False, can_write=True, viewer=self.user1)
        self.assertEqual(child["dateLabel"], "2022-01-01")
        self.assertEqual(child["card"]["progressBar"]["percent"], 25)
        self.assertEqual(child["card"]["cornerChips"][0]["href"], "/details/next/")
        public = _child_row(raw, media_type="season", parent_media_type="tv", source="tmdb",
                            media_id="1", public_view=True, can_write=False, viewer=None)
        self.assertIsNone(public["card"]["progressBar"])
        self.assertEqual(public["card"]["cornerChips"], [])

    def test_card_and_child_rating_preferences_do_not_disable_watch_actions(self):
        self.user1.rating_scale = "5"
        raw = {"item": {"media_type": "movie", "media_id": "1", "source": "tmdb", "title": "Film"},
               "media": SimpleNamespace(score=8, status="Completed")}
        card = _media_card(raw, public_view=False, viewer=self.user1, can_write=True)["model"]
        self.assertEqual(card["rating"]["value"], 4)
        self.assertEqual(card["rating"]["scaleMax"], 5)
        self.assertTrue(card["ratingEditable"])
        self.assertFalse(_media_card(raw, public_view=False, viewer=self.user1, can_write=False)["model"]["ratingEditable"])
        args = {"media_type": "episode", "parent_media_type": "tv", "source": "tmdb", "media_id": "1", "public_view": False, "can_write": True, "viewer": self.user1}
        child = _child_row({"score": 8, "episode_number": 1}, **args)
        self.assertEqual((child["score"], child["scaleMax"], child["canRate"]), (4, 5, True))
        self.user1.rating_scale = "0"
        card = _media_card(raw, public_view=False, viewer=self.user1, can_write=True)["model"]
        self.assertIsNone(card["rating"])
        self.assertFalse(card["ratingEditable"])
        child = _child_row({"score": 8, "episode_number": 1}, **args)
        self.assertIsNone(child["score"])
        self.assertFalse(child["canRate"])
        self.assertEqual(child["actions"][0]["key"], "watch")

    @patch("api.fork_views_detail.services.get_media_metadata", return_value={"title": "Game"})
    def test_tracking_entries_are_bounded_actual_owned_repeats_not_audit_rows(self, _metadata):
        item = self.items_by_type[MediaTypes.GAME.value][0]
        model = apps.get_model("app", "game")
        model.objects.bulk_create([model(item=item, user=self.user1, score=8) for _ in range(51)])
        self.user1.rating_scale = "5"
        self.user1.save(update_fields=["rating_scale"])
        path = f"/api/v1/media/game/{item.source}/{item.media_id}/detail/"
        body = self.client.get(path, **self.auth_headers).json()
        self.assertEqual(body["tracking_entries_count"], 52)
        self.assertEqual(len(body["tracking_entries"]), 50)
        self.assertTrue(body["tracking_entries_has_more"])
        entry = body["tracking_entries"][0]
        self.assertEqual((entry["score"], entry["display_score"]), (8, 4))
        self.assertEqual(entry["identity"]["instanceId"], entry["id"])
        self.assertTrue(entry["can_edit"])
        public = self.client.get(path, {"public_view": "1"}, **self.auth_headers).json()
        other = self.client.get(path, **self.auth_headers2).json()
        self.assertEqual(public["tracking_entries"], [])
        self.assertEqual(other["tracking_entries_count"], 0)

    @patch(
        "api.fork_views_detail.services.get_media_metadata",
        return_value={
            "title": "Movie One",
            "original_title": "Film One",
            "synopsis": "A test synopsis.",
            "image": "https://example.com/poster.jpg",
            "genres": ["Drama"],
            "details": {"Release date": "2024-01-01"},
            "cast": [{"name": "Actor One"}],
            "related": {"seasons": []},
        },
    )
    def test_tracked_user_gets_metadata_and_only_own_tracking_state(self, _metadata):
        item = self.items_by_type[MediaTypes.MOVIE.value][0]
        response = self.client.get(
            f"/api/v1/media/{item.media_type}/{item.source}/{item.media_id}/detail/",
            **self.auth_headers,
        )

        self.assertEqual(response.status_code, HTTP.OK)
        body = response.json()
        self.assertEqual(body["title"], "Movie One")
        self.assertEqual(body["synopsis"], "A test synopsis.")
        self.assertEqual(body["sections"][0]["items"], ["Drama"])
        self.assertTrue(body["capabilities"]["can_update"])
        self.assertFalse(body["read_only"])
        self.assertIsNotNone(body["tracking"])
        self.assertIn("history", body)
        self.assertIn("collections", body)

    @patch("api.fork_views_detail.services.get_media_metadata", return_value={
        "title": "Movie", "cast": [{"person_id": "12", "name": "Actor", "role": "Lead", "image": "https://example.com/actor.jpg"}],
    })
    def test_viewer_rating_uses_preferences_and_people_use_shared_card_props(self, _metadata):
        item = self.items_by_type[MediaTypes.MOVIE.value][0]
        tracked = apps.get_model("app", "movie").objects.get(item=item, user=self.user1)
        tracked.score = 8
        tracked.save(update_fields=["score"])
        self.user1.rating_scale = "5"
        self.user1.save(update_fields=["rating_scale"])
        path = f"/api/v1/media/movie/{item.source}/{item.media_id}/detail/"
        body = self.client.get(path, **self.auth_headers).json()
        self.assertEqual(body["viewer_rating"], {"enabled": True, "scale_max": 5, "value": 4})
        self.assertEqual(body["people"]["cast"][0]["imageUrl"], "https://example.com/actor.jpg")
        self.assertEqual(body["people"]["cast"][0]["personId"], "12")
        self.assertIn("cardLines", body["people"]["cast"][0])
        public = self.client.get(path, {"public_view": "1"}, **self.auth_headers).json()
        self.assertEqual(public["viewer_rating"], {"enabled": False, "scale_max": 10, "value": None})

    @patch(
        "api.fork_views_detail.services.get_media_metadata",
        return_value={"title": "Movie One", "genres": ["Drama"]},
    )
    def test_untracked_signed_in_user_gets_no_other_users_tracking(self, _metadata):
        item = self.items_by_type[MediaTypes.MOVIE.value][0]
        response = self.client.get(
            f"/api/v1/media/{item.media_type}/{item.source}/{item.media_id}/detail/",
            **self.auth_headers2,
        )

        self.assertEqual(response.status_code, HTTP.OK)
        body = response.json()
        self.assertIsNone(body["tracking"])
        self.assertTrue(body["capabilities"]["can_track"])
        self.assertFalse(body["read_only"])

    @patch(
        "api.fork_views_detail.services.get_media_metadata",
        return_value={"title": "Movie One", "genres": ["Drama"]},
    )
    def test_public_view_is_read_only_even_for_a_tracked_item(self, _metadata):
        item = self.items_by_type[MediaTypes.MOVIE.value][0]
        response = self.client.get(
            f"/api/v1/media/{item.media_type}/{item.source}/{item.media_id}/detail/?public_view=1",
            **self.auth_headers,
        )

        self.assertEqual(response.status_code, HTTP.OK)
        body = response.json()
        self.assertIsNone(body["tracking"])
        self.assertTrue(body["read_only"])
        self.assertFalse(body["capabilities"]["can_track"])
        self.assertFalse(body["capabilities"]["can_update"])
        self.assertEqual(body["history"], [])
        self.assertEqual(body["collections"], [])

    @patch(
        "api.fork_views_detail.services.get_media_metadata",
        return_value={"title": "Public Movie", "genres": ["Drama"]},
    )
    def test_anonymous_request_gets_public_metadata_without_actions(self, _metadata):
        response = self.client.get(
            f"/api/v1/media/{MediaTypes.MOVIE.value}/{Sources.TMDB.value}/public-id/detail/"
        )

        self.assertEqual(response.status_code, HTTP.OK)
        body = response.json()
        self.assertEqual(body["title"], "Public Movie")
        self.assertTrue(body["read_only"])
        self.assertFalse(body["capabilities"]["can_track"])

    @patch(
        "api.fork_views_detail.services.get_media_metadata",
        return_value={"title": "Public video", "synopsis": "A video."},
    )
    def test_video_detail_uses_the_public_provider_projection(self, _metadata):
        response = self.client.get(
            f"/api/v1/media/{MediaTypes.VIDEO.value}/{Sources.YOUTUBE.value}/video-id/detail/"
        )

        self.assertEqual(response.status_code, HTTP.OK)
        self.assertEqual(response.json()["title"], "Public video")
        self.assertTrue(response.json()["read_only"])

    @patch(
        "api.fork_views_detail.services.get_media_metadata",
        return_value={"title": "TV Show 1", "season/2": {
            "title": "TV Show 1", "season_number": 2,
            "synopsis": "Season synopsis", "episodes": [],
        }},
    )
    def test_season_detail_keeps_parent_identity_and_season_coordinate(self, metadata):
        response = self.client.get(
            "/api/v1/media/tv/tmdb/1001/detail/?detail_type=season&season_number=2",
            **self.auth_headers,
        )

        self.assertEqual(response.status_code, HTTP.OK)
        self.assertEqual(response.json()["identity"]["media_type"], "season")
        self.assertEqual(response.json()["layout"]["family"], "series")
        self.assertEqual(response.json()["identity"]["parent_media_type"], "tv")
        self.assertEqual(response.json()["synopsis"], "Season synopsis")
        metadata.assert_called_once()
        self.assertEqual(metadata.call_args.args[0], "tv_with_seasons")
        self.assertEqual(metadata.call_args.kwargs["season_numbers"], [2])

    @patch("api.fork_views_detail.tmdb.process_episodes", return_value=[{
        "title": "Blood Money", "episode_number": 1, "season_number": 5,
        "overview": "An episode synopsis.", "image": "https://example.com/still.jpg",
        "history": [],
    }])
    @patch("api.fork_views_detail.services.get_media_metadata", return_value={
        "title": "Breaking Bad", "season/5": {
            "title": "Breaking Bad", "season_number": 5, "media_id": "1396",
            "synopsis": "Season synopsis.", "episodes": [{"episode_number": 1}],
        },
    })
    def test_season_and_episode_project_the_selected_provider_content(self, _metadata, _episodes):
        season = self.client.get(
            "/api/v1/media/tv/tmdb/1396/detail/?detail_type=season&season_number=5",
            **self.auth_headers,
        )
        self.assertEqual(season.status_code, HTTP.OK)
        self.assertEqual(season.json()["synopsis"], "Season synopsis.")
        self.assertEqual(season.json()["children"]["episodes"][0]["title"], "Blood Money")
        episode = self.client.get(
            "/api/v1/media/tv/tmdb/1396/detail/?detail_type=episode&season_number=5&episode_number=1",
            **self.auth_headers,
        )
        self.assertEqual(episode.status_code, HTTP.OK)
        self.assertEqual(episode.json()["title"], "Blood Money")
        self.assertEqual(episode.json()["synopsis"], "An episode synopsis.")
        self.assertEqual(episode.json()["layout"]["family"], "episode")

    @patch(
        "api.fork_views_detail.carousel.resolve_carousel_media",
        return_value={
            "hero": {"url": "https://example.com/backdrop.jpg", "logo_url": "https://example.com/logo.png"},
            "overview": {"url": "https://example.com/backdrop.jpg", "logo_url": "https://example.com/logo.png"},
            "photos": [{"url": "https://example.com/still.jpg", "thumb_url": "https://example.com/still-small.jpg"}],
            "video": {"key": "trailer-id"},
        },
    )
    @patch(
        "api.fork_views_detail.services.get_media_metadata",
        return_value={
            "title": "Movie One",
            "synopsis": "A full synopsis.",
            "genres": ["Drama"],
            "related": {
                "recommendations": [{
                    "media_type": "movie",
                    "source": "tmdb",
                    "media_id": "related-1",
                    "title": "Related Movie",
                    "image": "https://example.com/related.jpg",
                }],
            },
        },
    )
    def test_projection_includes_hero_chips_and_recommendation_cards(self, _metadata, _gallery):
        item = self.items_by_type[MediaTypes.MOVIE.value][0]
        response = self.client.get(
            f"/api/v1/media/{item.media_type}/{item.source}/{item.media_id}/detail/",
            **self.auth_headers,
        )

        self.assertEqual(response.status_code, HTTP.OK)
        body = response.json()
        self.assertEqual(body["artwork"]["backdrop"], "https://example.com/backdrop.jpg")
        self.assertEqual(body["artwork"]["logo"], "https://example.com/logo.png")
        self.assertEqual(body["artwork"]["gallery"]["video"]["key"], "trailer-id")
        self.assertEqual(body["synopsis"], "A full synopsis.")
        self.assertEqual(body["related_sections"][0]["label"], "Recommendations")
        self.assertEqual(body["related_sections"][0]["items"][0]["model"]["title"], "Related Movie")

    @patch(
        "api.fork_views_detail.enrich_detail_seasons",
        return_value=(None, []),
    )
    @patch(
        "api.fork_views_detail.enrich_detail_related_cards",
        return_value=None,
    )
    @patch(
        "api.fork_views_detail.services.get_media_metadata",
        return_value={
            "title": "TV Show",
            "episodes": [
                {"title": f"Episode {number}", "episode_number": number, "season_number": 1}
                for number in range(1, 36)
            ],
        },
    )
    def test_episode_projection_paginates_and_accepts_next_page(self, _metadata, _related, _seasons):
        first = self.client.get("/api/v1/media/tv/tmdb/show-id/detail/", **self.auth_headers)
        second = self.client.get(
            "/api/v1/media/tv/tmdb/show-id/detail/?episode_page=2",
            **self.auth_headers,
        )

        self.assertEqual(first.status_code, HTTP.OK)
        self.assertEqual(second.status_code, HTTP.OK)
        self.assertEqual(first.json()["children"]["next_page"], 2)
        self.assertLess(len(first.json()["children"]["episodes"]), 35)
        self.assertNotEqual(
            first.json()["children"]["episodes"][0]["title"],
            second.json()["children"]["episodes"][0]["title"],
        )

    @patch(
        "api.fork_views_detail.enrich_detail_seasons",
        return_value=(None, []),
    )
    @patch(
        "api.fork_views_detail.services.get_media_metadata",
        return_value={
            "title": "TV Show",
            "episodes": [{
                "title": "Episode 1",
                "season_number": 1,
                "episode_number": 1,
            }],
        },
    )
    def test_episode_actions_are_permission_gated(self, _metadata, _seasons):
        endpoint = "/api/v1/media/tv/tmdb/show-id/detail/"
        signed_in = self.client.get(endpoint, **self.auth_headers)
        public = self.client.get(f"{endpoint}?public_view=1", **self.auth_headers)

        self.assertEqual(signed_in.status_code, HTTP.OK)
        self.assertEqual(public.status_code, HTTP.OK)
        editable_child = signed_in.json()["children"]["episodes"][0]
        readonly_child = public.json()["children"]["episodes"][0]
        self.assertTrue(editable_child["canRate"])
        self.assertEqual(editable_child["actions"][0]["key"], "watch")
        self.assertFalse(readonly_child["canRate"])
        self.assertEqual(readonly_child["actions"], [])

    @patch(
        "api.fork_views_detail.enrich_detail_seasons",
        return_value=(None, []),
    )
    @patch(
        "api.fork_views_detail.services.get_media_metadata",
        return_value={"title": "TV Show", "episodes": [{
            "title": "Episode 1", "season_number": 1, "episode_number": 1,
        }]},
    )
    def test_read_only_integration_token_gets_no_episode_actions(self, _metadata, _seasons):
        _token, raw = IntegrationToken.generate(
            user=self.user1,
            name="Metadata reader",
            scopes=["watchlist:read"],
        )
        response = self.client.get(
            "/api/v1/media/tv/tmdb/show-id/detail/",
            HTTP_AUTHORIZATION=f"Bearer {raw}",
        )

        self.assertEqual(response.status_code, HTTP.OK)
        child = response.json()["children"]["episodes"][0]
        self.assertFalse(child["canRate"])
        self.assertEqual(child["actions"], [])

    @patch("api.fork_views_detail._build_game_lengths_context", return_value=None)
    @patch("api.fork_views_detail.build_book_formats", return_value=[])
    @patch("api.fork_views_detail.enrich_detail_related_cards", return_value=None)
    @patch("api.fork_views_detail.enrich_detail_seasons", return_value=(None, []))
    @patch(
        "api.fork_views_detail.services.get_media_metadata",
        return_value={
            "title": "Structured title",
            "synopsis": "Structured summary.",
            "details": {"Format": "Paperback"},
            "related": {"seasons": [], "recommendations": []},
            "episodes": [],
            "cast": [],
            "crew": [],
        },
    )
    def test_projection_shape_covers_video_book_game_comic_anime_and_manga(
        self, _metadata, _seasons, _related, _book_formats, _game_lengths,
    ):
        identities = [
            (MediaTypes.VIDEO.value, Sources.YOUTUBE.value, "public-video-id"),
            (MediaTypes.BOOK.value, self.items_by_type[MediaTypes.BOOK.value][0].source,
             self.items_by_type[MediaTypes.BOOK.value][0].media_id),
            (MediaTypes.GAME.value, self.items_by_type[MediaTypes.GAME.value][0].source,
             self.items_by_type[MediaTypes.GAME.value][0].media_id),
            (MediaTypes.COMIC.value, self.items_by_type[MediaTypes.COMIC.value][0].source,
             self.items_by_type[MediaTypes.COMIC.value][0].media_id),
            (MediaTypes.ANIME.value, self.items_by_type[MediaTypes.ANIME.value][0].source,
             self.items_by_type[MediaTypes.ANIME.value][0].media_id),
            (MediaTypes.MANGA.value, self.items_by_type[MediaTypes.MANGA.value][0].source,
             self.items_by_type[MediaTypes.MANGA.value][0].media_id),
        ]

        for media_type, source, media_id in identities:
            with self.subTest(media_type=media_type):
                response = self.client.get(
                    f"/api/v1/media/{media_type}/{source}/{media_id}/detail/",
                    **self.auth_headers,
                )
                self.assertEqual(response.status_code, HTTP.OK)
                body = response.json()
                self.assertEqual(body["title"], "Structured title")
                self.assertEqual(body["synopsis"], "Structured summary.")
                self.assertIn("artwork", body)
                self.assertIn("score_chips", body)
                self.assertIn("related_sections", body)
                self.assertNotIn("<", str(body))

    @patch(
        "api.fork_views_detail.carousel.resolve_carousel_media",
        return_value={"hero": {"url": "https://example.com/backdrop.jpg"}, "photos": []},
    )
    def test_deferred_gallery_endpoint_returns_structured_media(self, _gallery):
        response = self.client.get(
            "/api/v1/media/movie/tmdb/123/detail/gallery/",
        )

        self.assertEqual(response.status_code, HTTP.OK)
        self.assertEqual(response.json()["gallery"]["hero"]["url"], "https://example.com/backdrop.jpg")

    @patch("api.fork_views_detail.services.get_media_metadata", return_value={"title": "Movie"})
    def test_projection_resolves_only_the_viewers_saved_layout(self, _metadata):
        saved = {"sidebar": ["details"], "content": ["crew", "notes"]}
        self.user1.detail_page_layouts = {"media": saved}
        self.user1.save(update_fields=["detail_page_layouts"])
        item = self.items_by_type[MediaTypes.MOVIE.value][0]
        path = f"/api/v1/media/movie/{item.source}/{item.media_id}/detail/"

        own = self.client.get(path, **self.auth_headers).json()["layout"]
        other = self.client.get(path, **self.auth_headers2).json()["layout"]
        public = self.client.get(path, {"public_view": "1"}, **self.auth_headers).json()["layout"]

        self.assertEqual(own, {"family": "media", "zones": saved})
        self.assertEqual(other["zones"], default_detail_layouts()["media"])
        self.assertEqual(public["zones"], default_detail_layouts()["media"])

    @patch("api.fork_views_detail.services.get_media_metadata", return_value={
        "title": "Movie",
        "details": {"release_date": "2024-01-01", "runtime": "2h", "languages": ["English"], "country": "United States", "language": "en"},
        "studios_full": [{"studio_id": "20", "name": "Studio One", "logo": "https://example.com/studio.png"}],
    })
    def test_projection_preserves_structured_promoted_facts_and_studio_links(self, _metadata):
        item = self.items_by_type[MediaTypes.MOVIE.value][0]
        body = self.client.get(
            f"/api/v1/media/movie/{item.source}/{item.media_id}/detail/",
            **self.auth_headers,
        ).json()["secondary"]

        self.assertEqual(body["details"]["release_date"], "2024-01-01")
        fields = {field["key"]: field for field in body["promotion"]["fields"]}
        self.assertEqual(fields["locale"]["value"], {"languages": ["English"], "country": "United States"})
        self.assertEqual(fields["locale"]["display_value"], "English (United States)")
        self.assertEqual(body["display_details"]["language"], "English")
        self.assertIn("languages", body["promotion"]["suppressed_keys"])
        self.assertEqual(body["studios_linked"][0]["href"], f"/studio/{item.source}/20/studio-one")
        self.assertEqual(body["studio_detail_keys"], ["studios", "companies"])

    @patch("api.fork_views_detail.build_book_formats", return_value=[{"label": "eBook", "href": "/ebook"}])
    @patch("api.fork_views_detail.services.get_media_metadata", return_value={
        "title": "Book",
        "details": {"author": "Author One", "format": "Paperback"},
        "authors_full": [{"person_id": "11", "name": "Author One"}],
        "studios_full": [{"studio_id": "20", "name": "Publisher"}],
    })
    def test_projection_links_authors_and_prefers_existing_credits_without_writes(self, _metadata, _formats):
        item = self.items_by_type[MediaTypes.BOOK.value][0]
        path = f"/api/v1/media/book/{item.source}/{item.media_id}/detail/"
        fallback = self.client.get(path, **self.auth_headers).json()["secondary"]
        self.assertEqual(fallback["authors_linked"][0]["href"], f"/person/{item.source}/11/author-one")
        self.assertEqual(fallback["book_formats_replaces"], "format")

        person = Person.objects.create(source=item.source, source_person_id="12", name="Stored Author")
        studio = Studio.objects.create(source=item.source, source_studio_id="21", name="Stored Publisher")
        ItemPersonCredit.objects.create(item=item, person=person, role_type=CreditRoleType.AUTHOR.value)
        ItemStudioCredit.objects.create(item=item, studio=studio)
        stored = self.client.get(path, **self.auth_headers).json()["secondary"]
        self.assertEqual([entry["name"] for entry in stored["authors_linked"]], ["Stored Author"])
        self.assertEqual([entry["name"] for entry in stored["studios_linked"]], ["Stored Publisher"])
        self.assertEqual(item.person_credits.count(), 1)
        self.assertEqual(item.studio_credits.count(), 1)

    @patch("api.fork_views_detail.services.get_media_metadata", return_value={
        "title": "Movie",
        "providers": {"US": {"flatrate": [
            {"provider_id": 1, "provider_name": "Netflix", "logo_path": "/netflix.png", "display_priority": 2},
        ], "free": [
            {"provider_id": 1, "provider_name": "Netflix", "logo_path": "/netflix.png", "display_priority": 2},
        ]}},
    })
    def test_projection_uses_region_and_pins_for_structured_watch_providers(self, _metadata):
        item = self.items_by_type[MediaTypes.MOVIE.value][0]
        item.watch_providers = {"JP": {"flatrate": [
            {"provider_id": 2, "provider_name": "Global", "logo_path": "/global.png", "display_priority": 1},
        ]}}
        item.save(update_fields=["watch_providers"])
        self.user1.watch_provider_region = "US"
        self.user1.pinned_watch_providers = ["Global"]
        self.user1.save(update_fields=["watch_provider_region", "pinned_watch_providers"])

        body = self.client.get(
            f"/api/v1/media/movie/{item.source}/{item.media_id}/detail/",
            **self.auth_headers,
        ).json()
        providers = body["secondary"]["watch_providers"]
        self.assertEqual(body["secondary"]["watch_provider_region"], "US")
        self.assertEqual([provider["provider_name"] for provider in providers], ["Global", "Netflix"])
        self.assertTrue(providers[0]["image"].endswith("/global.png"))
        self.assertNotIn("image", body["providers"]["US"]["flatrate"][0])

    @patch("api.fork_views_detail.services.get_media_metadata", return_value={"title": "Movie", "providers": {}})
    def test_projection_distinguishes_hidden_and_configured_empty_watch_providers(self, _metadata):
        item = self.items_by_type[MediaTypes.MOVIE.value][0]
        path = f"/api/v1/media/movie/{item.source}/{item.media_id}/detail/"
        for region, expected in (("UNSET", None), ("US", [])):
            with self.subTest(region=region):
                self.user1.watch_provider_region = region
                self.user1.save(update_fields=["watch_provider_region"])
                body = self.client.get(path, **self.auth_headers).json()["secondary"]
                self.assertEqual(body["watch_providers"], expected)
                self.assertEqual(body["watch_provider_region"], region)
        public = self.client.get(path, {"public_view": "1"}, **self.auth_headers).json()["secondary"]
        self.assertIsNone(public["watch_providers"])
        self.assertIsNone(public["watch_provider_region"])

    @patch("api.fork_views_detail.enrich_detail_seasons", return_value=(None, []))
    @patch("api.fork_views_detail.enrich_detail_related_cards", return_value=None)
    def test_projection_enriches_mal_anime_watch_providers_through_exact_tmdb_type(self, _related, _seasons):
        item = self.items_by_type[MediaTypes.ANIME.value][0]
        self.user1.watch_provider_region = "US"
        self.user1.save(update_fields=["watch_provider_region"])
        before_links = ItemProviderLink.objects.filter(item=item).count()
        mapped = {"providers": {"US": {"flatrate": [{"provider_id": 9, "provider_name": "Mapped Stream"}]}}}
        with (
            patch("api.fork_views_detail.services.get_media_metadata", side_effect=[{"title": "Anime Film"}, mapped]) as metadata,
            patch("api.fork_views_detail.metadata_resolution.resolve_mal_tmdb_identity", return_value=metadata_resolution.AnimeTMDBIdentity("81", "movie")) as resolve,
        ):
            response = self.client.get(
                f"/api/v1/media/anime/{item.source}/{item.media_id}/detail/",
                **self.auth_headers,
            )
        self.assertEqual(response.status_code, HTTP.OK)
        self.assertEqual(response.json()["secondary"]["watch_providers"][0]["provider_name"], "Mapped Stream")
        resolve.assert_called_once_with(item.media_id)
        self.assertEqual(metadata.call_args_list[1].args, ("movie", "81", "tmdb"))
        self.assertEqual(ItemProviderLink.objects.filter(item=item).count(), before_links)

    @patch("api.fork_views_detail.enrich_detail_seasons", return_value=(None, []))
    @patch("api.fork_views_detail.enrich_detail_related_cards", return_value=None)
    def test_projection_enriches_tvdb_watch_providers_without_persisting_links(self, _related, _seasons):
        item = Item.objects.create(media_type="tv", source="tvdb", media_id="91234", title="TVDB show")
        ItemProviderLink.objects.create(item=item, provider="tmdb", provider_media_type="tv", provider_media_id="91")
        self.user1.watch_provider_region = "US"
        self.user1.save(update_fields=["watch_provider_region"])
        mapped = {"providers": {"US": {"free": [{"provider_id": 10, "provider_name": "Mapped Free"}]}}}
        with (
            patch("api.fork_views_detail.services.get_media_metadata", side_effect=[{"title": "TVDB show"}, mapped]) as metadata,
            patch("api.fork_views_detail.metadata_resolution.resolve_provider_media_id", wraps=metadata_resolution.resolve_provider_media_id) as resolve,
        ):
            response = self.client.get("/api/v1/media/tv/tvdb/91234/detail/", **self.auth_headers)
        self.assertEqual(response.status_code, HTTP.OK)
        self.assertEqual(response.json()["secondary"]["watch_providers"][0]["provider_name"], "Mapped Free")
        self.assertFalse(resolve.call_args.kwargs["persist_links"])
        self.assertEqual(metadata.call_args_list[1].args, ("tv", "91", "tmdb"))
        self.assertEqual(ItemProviderLink.objects.filter(item=item).count(), 1)
        item.refresh_from_db()
        self.assertEqual(item.watch_providers, {})

    @patch("api.fork_views_detail.enrich_detail_seasons", return_value=(None, []))
    @patch("api.fork_views_detail.enrich_detail_related_cards", return_value=None)
    def test_optional_watch_provider_enrichment_failure_keeps_primary_detail(self, _related, _seasons):
        item = self.items_by_type[MediaTypes.ANIME.value][0]
        self.user1.watch_provider_region = "US"
        self.user1.save(update_fields=["watch_provider_region"])
        with (
            patch("api.fork_views_detail.services.get_media_metadata", side_effect=[{"title": "Available Anime"}, ProviderAPIError("tmdb", RuntimeError("offline"))]),
            patch("api.fork_views_detail.metadata_resolution.resolve_mal_tmdb_identity", return_value=metadata_resolution.AnimeTMDBIdentity("81", "movie")),
        ):
            response = self.client.get(
                f"/api/v1/media/anime/{item.source}/{item.media_id}/detail/",
                **self.auth_headers,
            )
        self.assertEqual(response.status_code, HTTP.OK)
        self.assertEqual(response.json()["title"], "Available Anime")
        self.assertIsNone(response.json()["secondary"]["watch_providers"])

