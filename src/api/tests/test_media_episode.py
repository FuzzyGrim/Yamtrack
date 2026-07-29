from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from app.models import TV, Episode, Item, MediaTypes, Season, Sources

from .base import YamtrackApiTestCase
from .helpers import (
    check_changes_history_entry_structure,
    check_consumption_structure,
    check_minimized_lists_structure,
    check_pagination_structure,
)


class MediaEpisodeTests(YamtrackApiTestCase):
    """Validate episode endpoint contracts."""

    @staticmethod
    def build_watch_metadata(
        *,
        media_id,
        source=Sources.TMDB.value,
        season_number=1,
        episode_numbers=(1, 2, 3),
    ):
        """Build provider metadata used by the episode-watch workflow."""
        episodes = [
            {
                "episode_number": episode_number,
                "season_number": season_number,
                "name": f"Episode {episode_number}",
                "image": f"https://example.com/episode-{episode_number}.jpg",
                "still_path": None,
            }
            for episode_number in episode_numbers
        ]
        season_metadata = {
            "media_id": media_id,
            "source": source,
            "media_type": MediaTypes.SEASON.value,
            "title": "API Test Show",
            "season_title": f"Season {season_number}",
            "image": "https://example.com/season.jpg",
            "season_number": season_number,
            "episodes": episodes,
        }
        return {
            "media_id": media_id,
            "source": source,
            "media_type": MediaTypes.TV.value,
            "title": "API Test Show",
            "image": "https://example.com/tv.jpg",
            "details": {"seasons": 1},
            "related": {
                "seasons": [{"season_number": season_number}],
            },
            f"season/{season_number}": season_metadata,
        }

    def test_episode_endpoints_reject_non_tv_media_type(self):
        """Episode endpoints should return 400 when media_type is not tv."""
        requests = [
            (
                "get",
                "api_media_episode_detail",
                ("movie", "tmdb", 1, 1, 1),
                None,
            ),
            (
                "patch",
                "api_media_episode_detail",
                ("movie", "tmdb", 1, 1, 1),
                {"notes": "x"},
            ),
            (
                "delete",
                "api_media_episode_detail",
                ("movie", "tmdb", 1, 1, 1),
                None,
            ),
            (
                "get",
                "api_media_episode_changes_history",
                ("movie", "tmdb", 1, 1, 1),
                None,
            ),
            (
                "get",
                "api_media_episode_consumption_history",
                ("movie", "tmdb", 1, 1, 1),
                None,
            ),
            (
                "post",
                "api_media_episode_consumption_history",
                ("movie", "tmdb", 1, 1, 1),
                {},
            ),
            (
                "get",
                "api_media_episode_consumption_entry_detail",
                ("movie", "tmdb", 1, 1, 1, 1),
                None,
            ),
            (
                "patch",
                "api_media_episode_consumption_entry_detail",
                ("movie", "tmdb", 1, 1, 1, 1),
                {"notes": "x"},
            ),
            (
                "delete",
                "api_media_episode_consumption_entry_detail",
                ("movie", "tmdb", 1, 1, 1, 1),
                None,
            ),
            (
                "get",
                "api_media_episode_lists",
                ("movie", "tmdb", 1, 1, 1),
                None,
            ),
            (
                "put",
                "api_media_episode_list_detail",
                ("movie", "tmdb", 1, 1, 1, 1),
                {},
            ),
            (
                "delete",
                "api_media_episode_list_detail",
                ("movie", "tmdb", 1, 1, 1, 1),
                None,
            ),
            (
                "post",
                "api_media_episode_sync",
                ("movie", "tmdb", 1, 1, 1),
                None,
            ),
        ]

        for method, url_name, args, payload in requests:
            response = self.call_api(
                method,
                url_name,
                args=args,
                payload=payload,
                headers=self.auth_headers,
            )
            self.assertEqual(response.status_code, 400)

    @patch("api.views.services.get_media_metadata")
    def test_episode_detail_get_returns_expected_shape(self, mock_metadata):
        """Episode detail GET should return complete serialized payload."""
        tv_item = self.items_by_type[MediaTypes.TV.value][0]
        season_item = self.items_by_type[MediaTypes.SEASON.value][0]
        episode_item = self.items_by_type[MediaTypes.EPISODE.value][0]

        mock_metadata.return_value = self.build_episode_metadata(
            tv_item=tv_item,
            season_number=season_item.season_number,
            episode_number=episode_item.episode_number,
            title=episode_item.title,
            image=episode_item.image,
        )

        response = self.call_api(
            "get",
            "api_media_episode_detail",
            args=(
                MediaTypes.TV.value,
                tv_item.source,
                tv_item.media_id,
                season_item.season_number,
                episode_item.episode_number,
            ),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["media_id"], tv_item.media_id)
        self.assertEqual(payload["source"], tv_item.source)
        self.assertIn("consumptions", payload)
        self.assertIn("lists", payload)

    @patch("api.views.services.get_media_metadata")
    def test_episode_detail_patch_with_invalid_field_returns_400(self, mock_metadata):
        """Episode PATCH with unknown field should return 400."""
        tv_item = self.items_by_type[MediaTypes.TV.value][0]
        season_item = self.items_by_type[MediaTypes.SEASON.value][0]
        episode_item = self.items_by_type[MediaTypes.EPISODE.value][0]

        mock_metadata.return_value = self.build_episode_metadata(
            tv_item=tv_item,
            season_number=season_item.season_number,
            episode_number=episode_item.episode_number,
            title=episode_item.title,
            image=episode_item.image,
        )

        response = self.call_api(
            "patch",
            "api_media_episode_detail",
            args=(
                MediaTypes.TV.value,
                tv_item.source,
                tv_item.media_id,
                season_item.season_number,
                episode_item.episode_number,
            ),
            payload={"invalid_field": "value"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)

    @patch("api.views.services.get_media_metadata")
    def test_episode_detail_delete_not_tracked_returns_404(self, mock_metadata):
        """Episode delete of untracked episode should return 404."""
        tv_item = self.items_by_type[MediaTypes.TV.value][1]

        mock_metadata.return_value = self.build_episode_metadata(
            tv_item=tv_item,
            season_number=99,
            episode_number=99,
            title="Unknown Episode",
            image=None,
            synopsis="unknown",
            score=0,
            score_count=0,
        )

        response = self.call_api(
            "delete",
            "api_media_episode_detail",
            args=(MediaTypes.TV.value, tv_item.source, tv_item.media_id, 99, 99),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 404)

    def test_episode_consumption_history_requires_authentication(self):
        """Episode consumption history should require authentication."""
        tv_item = self.items_by_type[MediaTypes.TV.value][0]
        season_item = self.items_by_type[MediaTypes.SEASON.value][0]
        episode_item = self.items_by_type[MediaTypes.EPISODE.value][0]

        response = self.client.get(
            reverse(
                "api_media_episode_consumption_history",
                args=(
                    MediaTypes.TV.value,
                    tv_item.source,
                    tv_item.media_id,
                    season_item.season_number,
                    episode_item.episode_number,
                ),
            )
        )

        self.assertEqual(response.status_code, 403)

    def test_episode_consumption_history_post_requires_authentication(self):
        """Creating an episode consumption should require authentication."""
        response = self.call_api(
            "post",
            "api_media_episode_consumption_history",
            args=(MediaTypes.TV.value, Sources.TMDB.value, "94997", 1, 1),
            payload={},
        )

        self.assertEqual(response.status_code, 403)

    def test_episode_lists_requires_authentication(self):
        """Episode lists endpoint should require authentication."""
        tv_item = self.items_by_type[MediaTypes.TV.value][0]
        season_item = self.items_by_type[MediaTypes.SEASON.value][0]
        episode_item = self.items_by_type[MediaTypes.EPISODE.value][0]

        response = self.client.get(
            reverse(
                "api_media_episode_lists",
                args=(
                    MediaTypes.TV.value,
                    tv_item.source,
                    tv_item.media_id,
                    season_item.season_number,
                    episode_item.episode_number,
                ),
            )
        )

        self.assertEqual(response.status_code, 403)

    def test_episode_sync_requires_valid_season(self):
        """Episode sync should reject on non-existent parent season."""
        response = self.call_api(
            "post",
            "api_media_episode_sync",
            args=(MediaTypes.TV.value, "tmdb", "99999", 1, 1),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 500)

    @patch("api.views.services.get_media_metadata", side_effect=Exception("boom"))
    def test_episode_detail_get_invalid_media_id_returns_internal_server_error(
        self,
        _mock_metadata,
    ):
        """Episode detail GET should surface provider lookup failures."""
        response = self.call_api(
            "get",
            "api_media_episode_detail",
            args=(MediaTypes.TV.value, "tmdb", 999999, 1, 1),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 500)

    @patch("api.views.services.get_media_metadata", side_effect=Exception("boom"))
    def test_episode_detail_get_invalid_season_id_returns_internal_server_error(
        self,
        _mock_metadata,
    ):
        """Episode detail GET should surface provider errors on invalid season."""
        tv_item = self.items_by_type[MediaTypes.TV.value][0]
        episode_item = self.items_by_type[MediaTypes.EPISODE.value][0]

        response = self.call_api(
            "get",
            "api_media_episode_detail",
            args=(
                MediaTypes.TV.value,
                tv_item.source,
                tv_item.media_id,
                999,
                episode_item.episode_number,
            ),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 500)

    @patch("api.views.services.get_media_metadata")
    def test_episode_detail_get_invalid_episode_id_returns_not_found(
        self, mock_metadata
    ):
        """Episode detail GET should return 404 for unknown episode number."""
        tv_item = self.items_by_type[MediaTypes.TV.value][0]
        season_item = self.items_by_type[MediaTypes.SEASON.value][0]
        mock_metadata.return_value = self.build_episode_metadata(
            tv_item=tv_item,
            season_number=season_item.season_number,
            episode_number=1,
            title="Episode 1",
            image="https://example.com/ep-1.jpg",
        )

        response = self.call_api(
            "get",
            "api_media_episode_detail",
            args=(
                MediaTypes.TV.value,
                tv_item.source,
                tv_item.media_id,
                season_item.season_number,
                999,
            ),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 404)

    def test_episode_detail_patch_updates_episode_fields(self):
        """Episode detail PATCH should update tracked episode fields."""
        tv_item = self.items_by_type[MediaTypes.TV.value][0]
        season_item = self.items_by_type[MediaTypes.SEASON.value][0]
        episode_item = self.items_by_type[MediaTypes.EPISODE.value][0]

        def _metadata_side_effect(media_type, *_args, **_kwargs):
            if media_type == "tv_with_seasons":
                return {
                    f"season/{season_item.season_number}": {"episodes": [{}, {}]},
                    "related": {
                        "seasons": [{"season_number": season_item.season_number}]
                    },
                }
            if media_type == "season":
                return self.build_episode_metadata(
                    tv_item=tv_item,
                    season_number=season_item.season_number,
                    episode_number=episode_item.episode_number,
                    title=episode_item.title,
                    image=episode_item.image,
                )
            return {}

        with patch(
            "app.models.providers.services.get_media_metadata",
            side_effect=_metadata_side_effect,
        ):
            response = self.call_api(
                "patch",
                "api_media_episode_detail",
                args=(
                    MediaTypes.TV.value,
                    tv_item.source,
                    tv_item.media_id,
                    season_item.season_number,
                    episode_item.episode_number,
                ),
                payload={"end_date": None},
                headers=self.auth_headers,
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["media_type"], MediaTypes.TV.value)
        self.assertIn("consumptions", payload)
        self.assertIn("details", payload)
        self.assertIsNone(payload["consumptions"][0]["end_date"])

    @patch("api.views.services.get_media_metadata")
    def test_episode_detail_delete_tracked_episode_returns_204(self, mock_metadata):
        """Episode detail DELETE should remove tracked episode."""
        tv_item = self.items_by_type[MediaTypes.TV.value][0]
        season_item = self.items_by_type[MediaTypes.SEASON.value][0]
        episode_item = self.items_by_type[MediaTypes.EPISODE.value][0]
        mock_metadata.return_value = self.build_episode_metadata(
            tv_item=tv_item,
            season_number=season_item.season_number,
            episode_number=episode_item.episode_number,
            title=episode_item.title,
            image=episode_item.image,
        )

        response = self.call_api(
            "delete",
            "api_media_episode_detail",
            args=(
                MediaTypes.TV.value,
                tv_item.source,
                tv_item.media_id,
                season_item.season_number,
                episode_item.episode_number,
            ),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 204)

    def test_episode_consumption_history_get_returns_paginated_payload(self):
        """Episode history endpoint should return paginated consumptions."""
        tv_item = self.items_by_type[MediaTypes.TV.value][0]
        season_item = self.items_by_type[MediaTypes.SEASON.value][0]
        episode_item = self.items_by_type[MediaTypes.EPISODE.value][0]

        response = self.call_api(
            "get",
            "api_media_episode_consumption_history",
            args=(
                MediaTypes.TV.value,
                tv_item.source,
                tv_item.media_id,
                season_item.season_number,
                episode_item.episode_number,
            ),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("pagination", payload)
        self.assertIn("results", payload)
        check_pagination_structure(
            self, payload["pagination"], total=1, limit=20, offset=0
        )
        for entry in payload["results"]:
            check_consumption_structure(self, entry)

    @patch("app.providers.services.get_media_metadata")
    def test_episode_consumption_history_post_creates_consumption_and_reuses_season(
        self,
        mock_metadata,
    ):
        """POST should preserve end_date and reuse the authenticated user's Season."""
        tv_item = self.items_by_type[MediaTypes.TV.value][0]
        season = self.season_medias[0]
        episode_number = 2
        supplied_end_date = "2026-07-29T17:30:00+02:00"
        mock_metadata.return_value = self.build_watch_metadata(
            media_id=tv_item.media_id,
            source=tv_item.source,
            season_number=season.item.season_number,
        )
        season_count = Season.objects.count()

        response = self.call_api(
            "post",
            "api_media_episode_consumption_history",
            args=(
                MediaTypes.TV.value,
                tv_item.source,
                tv_item.media_id,
                season.item.season_number,
                episode_number,
            ),
            payload={"end_date": supplied_end_date},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertIn("consumption_id", payload)
        consumption = Episode.objects.get(pk=payload["consumption_id"])
        self.assertEqual(consumption.related_season_id, season.id)
        self.assertEqual(Season.objects.count(), season_count)
        self.assertEqual(
            consumption.end_date,
            parse_datetime(supplied_end_date),
        )
        self.assertEqual(
            parse_datetime(payload["end_date"]),
            parse_datetime(supplied_end_date),
        )

    @patch("app.providers.services.get_media_metadata")
    def test_episode_consumption_history_post_defaults_to_current_aware_time(
        self,
        mock_metadata,
    ):
        """Omitted end_date should use the current timezone-aware minute."""
        tv_item = self.items_by_type[MediaTypes.TV.value][0]
        season = self.season_medias[0]
        mock_metadata.return_value = self.build_watch_metadata(
            media_id=tv_item.media_id,
            source=tv_item.source,
            season_number=season.item.season_number,
        )
        expected_minute = timezone.now().replace(second=0, microsecond=0)

        response = self.call_api(
            "post",
            "api_media_episode_consumption_history",
            args=(
                MediaTypes.TV.value,
                tv_item.source,
                tv_item.media_id,
                season.item.season_number,
                2,
            ),
            payload={},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 201)
        watched_at = parse_datetime(response.json()["end_date"])
        self.assertTrue(timezone.is_aware(watched_at))
        self.assertEqual(watched_at, expected_minute)
        self.assertEqual(watched_at.second, 0)
        self.assertEqual(watched_at.microsecond, 0)

    @patch("app.providers.services.get_media_metadata")
    def test_episode_consumption_history_post_creates_missing_parents(
        self,
        mock_metadata,
    ):
        """POST should create source-correct TV and Season parents for the user."""
        media_id = "94997"
        source = Sources.MANUAL.value
        mock_metadata.return_value = self.build_watch_metadata(
            media_id=media_id,
            source=source,
        )

        response = self.call_api(
            "post",
            "api_media_episode_consumption_history",
            args=(MediaTypes.TV.value, source, media_id, 1, 1),
            payload={},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 201)
        consumption = Episode.objects.select_related(
            "item",
            "related_season__item",
            "related_season__related_tv__item",
        ).get(pk=response.json()["consumption_id"])
        season = consumption.related_season
        self.assertEqual(season.user, self.user1)
        self.assertEqual(season.item.source, source)
        self.assertEqual(season.related_tv.user, self.user1)
        self.assertEqual(season.related_tv.item.source, source)
        self.assertEqual(consumption.item.source, source)
        self.assertTrue(
            TV.objects.filter(
                user=self.user1,
                item__media_id=media_id,
                item__source=source,
            ).exists()
        )

    @patch("app.providers.services.get_media_metadata")
    def test_episode_consumption_history_post_creates_repeat_consumptions(
        self,
        mock_metadata,
    ):
        """Every POST should create another Episode row for the same episode."""
        tv_item = self.items_by_type[MediaTypes.TV.value][0]
        season = self.season_medias[0]
        mock_metadata.return_value = self.build_watch_metadata(
            media_id=tv_item.media_id,
            source=tv_item.source,
            season_number=season.item.season_number,
        )
        args = (
            MediaTypes.TV.value,
            tv_item.source,
            tv_item.media_id,
            season.item.season_number,
            2,
        )

        first_response = self.call_api(
            "post",
            "api_media_episode_consumption_history",
            args=args,
            payload={},
            headers=self.auth_headers,
        )
        second_response = self.call_api(
            "post",
            "api_media_episode_consumption_history",
            args=args,
            payload={},
            headers=self.auth_headers,
        )

        self.assertEqual(first_response.status_code, 201)
        self.assertEqual(second_response.status_code, 201)
        self.assertNotEqual(
            first_response.json()["consumption_id"],
            second_response.json()["consumption_id"],
        )
        self.assertEqual(
            Episode.objects.filter(
                related_season=season,
                item__episode_number=2,
            ).count(),
            3,
        )

    @patch("app.providers.services.get_media_metadata")
    def test_episode_consumption_history_post_is_scoped_to_authenticated_user(
        self,
        mock_metadata,
    ):
        """A consumption created by user1 should not be visible as user2's."""
        media_id = "94998"
        mock_metadata.return_value = self.build_watch_metadata(media_id=media_id)

        create_response = self.call_api(
            "post",
            "api_media_episode_consumption_history",
            args=(MediaTypes.TV.value, Sources.TMDB.value, media_id, 1, 1),
            payload={},
            headers=self.auth_headers,
        )

        self.assertEqual(create_response.status_code, 201)
        consumption_id = create_response.json()["consumption_id"]
        self.assertTrue(
            Episode.objects.filter(
                id=consumption_id,
                related_season__user=self.user1,
            ).exists()
        )
        self.assertFalse(
            Episode.objects.filter(
                id=consumption_id,
                related_season__user=self.user2,
            ).exists()
        )

        user2_response = self.call_api(
            "get",
            "api_media_episode_consumption_history",
            args=(MediaTypes.TV.value, Sources.TMDB.value, media_id, 1, 1),
            headers=self.auth_headers2,
        )
        self.assertEqual(user2_response.status_code, 200)
        self.assertEqual(user2_response.json()["results"], [])

    def test_episode_consumption_history_post_rejects_invalid_end_date(self):
        """Malformed end_date should return a field-level validation error."""
        episode_count = Episode.objects.count()

        response = self.call_api(
            "post",
            "api_media_episode_consumption_history",
            args=(MediaTypes.TV.value, Sources.TMDB.value, "94997", 1, 1),
            payload={"end_date": "not-a-date"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("end_date", response.json()["errors"])
        self.assertEqual(Episode.objects.count(), episode_count)

    @patch("app.providers.services.get_media_metadata")
    def test_episode_consumption_history_post_rejects_unknown_episode(
        self,
        mock_metadata,
    ):
        """Provider-confirmed unknown episodes should not create database rows."""
        media_id = "94999"
        unknown_episode_number = 999
        mock_metadata.return_value = self.build_watch_metadata(
            media_id=media_id,
            episode_numbers=(1, 2, 3),
        )

        response = self.call_api(
            "post",
            "api_media_episode_consumption_history",
            args=(
                MediaTypes.TV.value,
                Sources.TMDB.value,
                media_id,
                1,
                unknown_episode_number,
            ),
            payload={},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(
            Item.objects.filter(
                media_id=media_id,
                source=Sources.TMDB.value,
                episode_number=unknown_episode_number,
            ).exists()
        )
        self.assertFalse(
            Episode.objects.filter(
                item__media_id=media_id,
                item__episode_number=unknown_episode_number,
            ).exists()
        )

    def test_episode_consumption_history_invalid_media_id_returns_empty_results(self):
        """Episode history endpoint should return empty results for unknown media."""
        response = self.call_api(
            "get",
            "api_media_episode_consumption_history",
            args=(MediaTypes.TV.value, "tmdb", 999999, 1, 1),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"], [])

    def test_episode_consumption_history_invalid_season_id_returns_empty_results(self):
        """Episode history endpoint should return empty results for unknown season."""
        tv_item = self.items_by_type[MediaTypes.TV.value][0]
        response = self.call_api(
            "get",
            "api_media_episode_consumption_history",
            args=(MediaTypes.TV.value, tv_item.source, tv_item.media_id, 999, 1),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"], [])

    def test_episode_consumption_history_invalid_episode_id_returns_empty_results(self):
        """Episode history endpoint should return empty results for unknown episode."""
        tv_item = self.items_by_type[MediaTypes.TV.value][0]
        season_item = self.items_by_type[MediaTypes.SEASON.value][0]
        response = self.call_api(
            "get",
            "api_media_episode_consumption_history",
            args=(
                MediaTypes.TV.value,
                tv_item.source,
                tv_item.media_id,
                season_item.season_number,
                999,
            ),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"], [])

    def test_episode_consumption_entry_detail_get_returns_expected_structure(self):
        """Episode history entry-detail GET should return serialized consumption."""
        tv_item = self.items_by_type[MediaTypes.TV.value][0]
        season_item = self.items_by_type[MediaTypes.SEASON.value][0]
        episode_item = self.items_by_type[MediaTypes.EPISODE.value][0]
        consumption_id = self.episode_medias[0].id

        response = self.call_api(
            "get",
            "api_media_episode_consumption_entry_detail",
            args=(
                MediaTypes.TV.value,
                tv_item.source,
                tv_item.media_id,
                season_item.season_number,
                episode_item.episode_number,
                consumption_id,
            ),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        check_consumption_structure(self, response.json())

    def test_episode_consumption_entry_detail_delete_removes_history_entry(self):
        """Episode history entry-detail DELETE should remove an existing row."""
        tv_item = self.items_by_type[MediaTypes.TV.value][0]
        season_item = self.items_by_type[MediaTypes.SEASON.value][0]
        episode_item = self.items_by_type[MediaTypes.EPISODE.value][0]
        consumption_id = self.episode_medias[0].id

        response = self.call_api(
            "delete",
            "api_media_episode_consumption_entry_detail",
            args=(
                MediaTypes.TV.value,
                tv_item.source,
                tv_item.media_id,
                season_item.season_number,
                episode_item.episode_number,
                consumption_id,
            ),
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, 204)

    def test_episode_consumption_entry_detail_patch_updates_history_entry(self):
        """Episode history entry-detail PATCH should persist valid updates."""
        tv_item = self.items_by_type[MediaTypes.TV.value][0]
        season_item = self.items_by_type[MediaTypes.SEASON.value][0]
        episode_item = self.items_by_type[MediaTypes.EPISODE.value][0]
        consumption_id = self.episode_medias[0].id

        with patch(
            "app.models.providers.services.get_media_metadata",
            return_value={
                f"season/{season_item.season_number}": {"episodes": [{}, {}]},
                "related": {"seasons": [{"season_number": season_item.season_number}]},
            },
        ):
            response = self.call_api(
                "patch",
                "api_media_episode_consumption_entry_detail",
                args=(
                    MediaTypes.TV.value,
                    tv_item.source,
                    tv_item.media_id,
                    season_item.season_number,
                    episode_item.episode_number,
                    consumption_id,
                ),
                payload={"end_date": None},
                headers=self.auth_headers,
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        check_consumption_structure(self, payload)
        self.assertIsNone(payload["end_date"])

    def test_episode_consumption_entry_detail_patch_invalid_payload_returns_bad_request(
        self,
    ):
        """Episode history entry-detail PATCH should reject invalid values."""
        tv_item = self.items_by_type[MediaTypes.TV.value][0]
        season_item = self.items_by_type[MediaTypes.SEASON.value][0]
        episode_item = self.items_by_type[MediaTypes.EPISODE.value][0]
        consumption_id = self.episode_medias[0].id

        response = self.call_api(
            "patch",
            "api_media_episode_consumption_entry_detail",
            args=(
                MediaTypes.TV.value,
                tv_item.source,
                tv_item.media_id,
                season_item.season_number,
                episode_item.episode_number,
                consumption_id,
            ),
            payload={"end_date": "invalid-date"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)

    def test_episode_lists_get_returns_lists(self):
        """Episode lists endpoint should return associated lists."""
        tv_item = self.items_by_type[MediaTypes.TV.value][0]
        season_item = self.items_by_type[MediaTypes.SEASON.value][0]
        episode_item = self.items_by_type[MediaTypes.EPISODE.value][0]
        list_id = self.lists_by_name["favorites"].id

        self.call_api(
            "put",
            "api_media_episode_list_detail",
            args=(
                MediaTypes.TV.value,
                tv_item.source,
                tv_item.media_id,
                season_item.season_number,
                episode_item.episode_number,
                list_id,
            ),
            payload={},
            headers=self.auth_headers,
        )

        response = self.call_api(
            "get",
            "api_media_episode_lists",
            args=(
                MediaTypes.TV.value,
                tv_item.source,
                tv_item.media_id,
                season_item.season_number,
                episode_item.episode_number,
            ),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("results", payload)
        self.assertGreaterEqual(len(payload["results"]), 1)
        for entry in payload["results"]:
            check_minimized_lists_structure(self, entry)

    def test_episode_lists_get_invalid_ids_returns_empty_results(self):
        """Episode lists endpoint should return empty results for unknown ids."""
        tv_item = self.items_by_type[MediaTypes.TV.value][0]
        season_item = self.items_by_type[MediaTypes.SEASON.value][0]

        requests = [
            (MediaTypes.TV.value, "tmdb", 999999, 1, 1),
            (MediaTypes.TV.value, tv_item.source, tv_item.media_id, 999, 1),
            (
                MediaTypes.TV.value,
                tv_item.source,
                tv_item.media_id,
                season_item.season_number,
                999,
            ),
        ]
        for args in requests:
            response = self.call_api(
                "get",
                "api_media_episode_lists",
                args=args,
                headers=self.auth_headers,
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["results"], [])

    def test_episode_list_detail_put_adds_media_to_list(self):
        """Episode list-detail PUT should add episode to list."""
        tv_item = self.items_by_type[MediaTypes.TV.value][0]
        season_item = self.items_by_type[MediaTypes.SEASON.value][0]
        episode_item = self.items_by_type[MediaTypes.EPISODE.value][0]
        list_id = self.lists_by_name["favorites"].id

        response = self.call_api(
            "put",
            "api_media_episode_list_detail",
            args=(
                MediaTypes.TV.value,
                tv_item.source,
                tv_item.media_id,
                season_item.season_number,
                episode_item.episode_number,
                list_id,
            ),
            payload={},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        for entry in response.json():
            check_minimized_lists_structure(self, entry)

    def test_episode_list_detail_delete_removes_media_from_list(self):
        """Episode list-detail DELETE should remove episode from list."""
        tv_item = self.items_by_type[MediaTypes.TV.value][0]
        season_item = self.items_by_type[MediaTypes.SEASON.value][0]
        episode_item = self.items_by_type[MediaTypes.EPISODE.value][0]
        list_id = self.lists_by_name["favorites"].id

        self.call_api(
            "put",
            "api_media_episode_list_detail",
            args=(
                MediaTypes.TV.value,
                tv_item.source,
                tv_item.media_id,
                season_item.season_number,
                episode_item.episode_number,
                list_id,
            ),
            payload={},
            headers=self.auth_headers,
        )

        response = self.call_api(
            "delete",
            "api_media_episode_list_detail",
            args=(
                MediaTypes.TV.value,
                tv_item.source,
                tv_item.media_id,
                season_item.season_number,
                episode_item.episode_number,
                list_id,
            ),
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, 204)

    def test_episode_changes_history_get_returns_paginated_payload(self):
        """Episode changes-history endpoint should return paginated entries."""
        tv_item = self.items_by_type[MediaTypes.TV.value][0]
        season_item = self.items_by_type[MediaTypes.SEASON.value][0]
        episode_item = self.items_by_type[MediaTypes.EPISODE.value][0]

        response = self.call_api(
            "get",
            "api_media_episode_changes_history",
            args=(
                MediaTypes.TV.value,
                tv_item.source,
                tv_item.media_id,
                season_item.season_number,
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
            check_changes_history_entry_structure(self, entry)

    @patch("api.views.tmdb.process_episodes", return_value=[])
    @patch("api.views.services.get_media_metadata")
    def test_episode_sync_returns_accepted(
        self,
        mock_metadata,
        _mock_process_episodes,
    ):
        """Episode sync endpoint should proxy to season sync and return accepted."""
        tv_item = self.items_by_type[MediaTypes.TV.value][0]
        season_item = self.items_by_type[MediaTypes.SEASON.value][0]
        episode_item = self.items_by_type[MediaTypes.EPISODE.value][0]
        mock_metadata.return_value = {
            "title": "TV Show 1 - Season 1 Synced",
            "image": "https://example.com/season-1-synced.jpg",
            "episodes": [
                {
                    "episode_number": episode_item.episode_number,
                    "image": "https://example.com/episode-1-synced.jpg",
                }
            ],
        }

        response = self.call_api(
            "post",
            "api_media_episode_sync",
            args=(
                MediaTypes.TV.value,
                tv_item.source,
                tv_item.media_id,
                season_item.season_number,
                episode_item.episode_number,
            ),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 202)

    @patch("api.views.services.get_media_metadata", side_effect=Exception("boom"))
    def test_episode_sync_invalid_ids_return_internal_server_error(
        self,
        _mock_metadata,
    ):
        """Episode sync should surface provider failures for invalid ids."""
        tv_item = self.items_by_type[MediaTypes.TV.value][0]
        season_item = self.items_by_type[MediaTypes.SEASON.value][0]

        requests = [
            (MediaTypes.TV.value, "tmdb", 999999, 1, 1),
            (MediaTypes.TV.value, tv_item.source, tv_item.media_id, 999, 1),
            (
                MediaTypes.TV.value,
                tv_item.source,
                tv_item.media_id,
                season_item.season_number,
                999,
            ),
        ]
        for args in requests:
            response = self.call_api(
                "post",
                "api_media_episode_sync",
                args=args,
                headers=self.auth_headers,
            )
            self.assertEqual(response.status_code, 500)

    def test_episode_sync_rejects_manual_source(self):
        """Episode sync endpoint should reject manual source."""
        response = self.call_api(
            "post",
            "api_media_episode_sync",
            args=(MediaTypes.TV.value, Sources.MANUAL.value, 701, 1, 1),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)
