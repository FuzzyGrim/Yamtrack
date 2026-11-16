# FORK: tests for the native detail-page endpoints.
from http import HTTPStatus as HTTP  # noqa: N814
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.urls import reverse

from app.models import (
    TV,
    ComicIssue,
    Episode,
    Item,
    MediaTypes,
    Season,
    Sources,
    Status,
)
from integrations import seerr_api

from .base import FloppyApiTestCase


class SeasonEpisodeMetadataTests(FloppyApiTestCase):
    """GET /media/tv/{source}/{id}/{season}[/{episode}]/metadata/ addresses the right Item."""

    def setUp(self):
        """Create two seasons and an episode of one show."""
        super().setUp()
        base = {"source": Sources.TMDB.value, "media_id": "9100"}
        self.season1 = Item.objects.create(
            media_type=MediaTypes.SEASON.value, season_number=1, title="S1", **base
        )
        self.season2 = Item.objects.create(
            media_type=MediaTypes.SEASON.value, season_number=2, title="S2", **base
        )
        self.episode = Item.objects.create(
            media_type=MediaTypes.EPISODE.value,
            season_number=2,
            episode_number=3,
            title="Ep 3",
            **base,
        )
        self.client.force_login(self.user1)

    def test_season_is_addressed_by_number(self):
        """A season request returns that season, not an arbitrary one."""
        response = self.client.get("/api/v1/media/tv/tmdb/9100/2/metadata/")
        self.assertEqual(response.status_code, HTTP.OK)
        self.assertEqual(response.json()["item_id"], self.season2.id)

    def test_episode_is_addressed_by_coordinates(self):
        """An episode request returns the episode Item."""
        response = self.client.get("/api/v1/media/tv/tmdb/9100/2/3/metadata/")
        self.assertEqual(response.status_code, HTTP.OK)
        self.assertEqual(response.json()["item_id"], self.episode.id)

    def test_missing_season_is_not_found(self):
        """An unknown season is a 404."""
        response = self.client.get("/api/v1/media/tv/tmdb/9100/7/metadata/")
        self.assertEqual(response.status_code, HTTP.NOT_FOUND)

    def test_non_tv_is_rejected(self):
        """Seasons exist only under tv."""
        response = self.client.get("/api/v1/media/movie/tmdb/9100/1/metadata/")
        self.assertEqual(response.status_code, HTTP.BAD_REQUEST)


class AnimeEpisodePreviewTests(FloppyApiTestCase):
    """The anime preview shares the web's mapping and keeps real episode routes."""

    def test_unwatched_rating_save_reload_clear_is_owned_and_never_a_watch(self):
        """A rating-only row survives preview reload without leaking other viewers."""
        self.client.force_login(self.user1)
        season = self.season_medias[0]
        episode_item = self.episode_medias[2].item
        Episode.objects.filter(related_season=season, item=episode_item).delete()
        row = {"source": episode_item.source, "media_id": episode_item.media_id,
               "season_number": episode_item.season_number, "episode_number": episode_item.episode_number}
        score_url = (f"/api/v1/media/tv/{episode_item.source}/{episode_item.media_id}/{episode_item.season_number}"
                     f"/episodes/{episode_item.episode_number}/score/")
        preview_url = "/api/v1/media/anime/mal/5114/episode-preview/"
        with patch("app.providers.services.get_media_metadata", return_value={"episodes": [row]}):
            response = self.client.patch(score_url, {"score": 6}, format="json")
            self.assertEqual(response.status_code, HTTP.OK, response.content)
        self.assertTrue(Episode.ratings.get(related_season=season, item=episode_item).rating_only)
        other_tv = TV.objects.create(user=self.user2, item=season.related_tv.item)
        other_season = Season.objects.create(user=self.user2, item=season.item, related_tv=other_tv)
        other_rating = Episode.ratings.create(related_season=other_season, item=episode_item, rating_only=True, score=9)
        with patch("app.providers.services.get_media_metadata", return_value={}), patch(
            "app.metadata_sync_views._build_flat_anime_episode_preview", return_value=[row],
        ):
            result = self.client.get(preview_url).json()["results"][0]
        self.assertEqual(float(result["score"]), 6)
        self.assertFalse(result["watched"])
        with patch("app.providers.services.get_media_metadata", return_value={"episodes": [row]}):
            response = self.client.patch(score_url, {"score": None}, format="json")
            self.assertEqual(response.status_code, HTTP.OK, response.content)
        with patch("app.providers.services.get_media_metadata", return_value={}), patch(
            "app.metadata_sync_views._build_flat_anime_episode_preview", return_value=[row],
        ):
            result = self.client.get(preview_url).json()["results"][0]
        self.assertIsNone(result["score"])
        self.assertFalse(result["watched"])
        self.assertTrue(Episode.ratings.filter(pk=other_rating.pk, score=9).exists())

    def test_mapped_provider_coordinates_and_flat_progress_are_preserved(self):
        """Display episode 14 may be provider season 2, episode 1."""
        self.client.force_login(self.user1)
        rows = [{"media_id": "77", "source": "tmdb", "season_number": 2,
                 "episode_number": 1, "display_episode_number": 14, "title": "Return",
                 "watched_by_progress": True, "runtime": "24min"}]
        with patch("app.providers.services.get_media_metadata", return_value={"source": "mal", "media_type": "anime"}), patch(
            "app.metadata_sync_views._build_flat_anime_episode_preview", return_value=rows,
        ) as preview:
            response = self.client.get("/api/v1/media/anime/mal/5114/episode-preview/")
        self.assertEqual(response.status_code, HTTP.OK)
        row = response.json()["results"][0]
        self.assertEqual(row["item"]["source"], "tmdb")
        self.assertEqual(row["item"]["season_number"], 2)
        self.assertEqual(row["display_episode_number"], 14)
        self.assertTrue(row["watched"])
        preview.assert_called_once()

    def test_open_play_is_not_watched_until_it_finishes(self):
        """An open check-in play is history, not a watch, on the native preview too."""
        self.client.force_login(self.user1)
        base = {"media_id": "77", "source": "tmdb", "season_number": 1, "title": "Open"}
        rows = [
            {**base, "episode_number": 1,
             "history": [SimpleNamespace(status=Status.IN_PROGRESS.value, dropped=False, score=None)]},
            {**base, "episode_number": 2,
             "history": [SimpleNamespace(status=Status.COMPLETED.value, dropped=False, score=None)]},
        ]
        with patch("app.providers.services.get_media_metadata", return_value={}), patch(
            "app.metadata_sync_views._build_flat_anime_episode_preview", return_value=rows,
        ):
            results = self.client.get("/api/v1/media/anime/mal/5114/episode-preview/").json()["results"]
        self.assertEqual([row["watched"] for row in results], [False, True])

    def test_empty_preview_and_wrong_type(self):
        """Unavailable optional metadata is an honest empty list."""
        self.client.force_login(self.user1)
        with patch("app.providers.services.get_media_metadata", return_value={}), patch(
            "app.metadata_sync_views._build_flat_anime_episode_preview", return_value=None,
        ):
            payload = self.client.get("/api/v1/media/anime/mal/5114/episode-preview/").json()
            self.assertEqual(payload["results"], [])
            self.assertIsNone(payload["load_more"])
        self.assertEqual(self.client.get("/api/v1/media/movie/tmdb/603/episode-preview/").status_code, HTTP.BAD_REQUEST)

    def test_episode_preview_uses_the_webs_twenty_five_row_batches(self):
        """The next batch preserves its mapped number and provider coordinates."""
        self.client.force_login(self.user1)
        rows = [{"media_id": "77", "source": "tmdb", "season_number": 1,
                 "episode_number": number, "display_episode_number": number, "title": str(number)} for number in range(1, 65)]
        with patch("app.providers.services.get_media_metadata", return_value={}), patch(
            "app.metadata_sync_views._build_flat_anime_episode_preview", return_value=rows,
        ):
            first = self.client.get("/api/v1/media/anime/mal/5114/episode-preview/").json()
            second = self.client.get("/api/v1/media/anime/mal/5114/episode-preview/?episode_page=2").json()
        self.assertEqual(len(first["results"]), 25)
        self.assertEqual(first["load_more"]["label"], "Episodes 26-50")
        self.assertEqual(second["results"][0]["display_episode_number"], 26)
        self.assertEqual(second["results"][0]["item"]["episode_number"], 26)
        self.assertFalse(second["results"][0]["can_list"])
        self.assertFalse(second["results"][0]["can_rate"])
        self.assertEqual(first["related"], {})  # Series layouts do not expose related titles.

    def test_rating_controls_respect_the_viewers_ratings_preference(self):
        """A tracked anime exposes the star only while ratings are enabled."""
        self.client.force_login(self.user1)
        item = self.items_by_type[MediaTypes.ANIME.value][0]
        url = f"/api/v1/media/anime/{item.source}/{item.media_id}/episode-preview/"
        row = {"source": "tmdb", "media_id": "77", "season_number": 1, "episode_number": 1}
        with patch("app.providers.services.get_media_metadata", return_value={}), patch(
            "app.services.metadata_resolution.resolve_detail_metadata", return_value=MagicMock(header_metadata={}),
        ), patch("app.metadata_sync_views._build_flat_anime_episode_preview", return_value=[row]):
            self.assertTrue(self.client.get(url).json()["results"][0]["can_rate"])
            self.user1.rating_scale = "0"
            self.user1.save(update_fields=["rating_scale"])
            self.assertFalse(self.client.get(url).json()["results"][0]["can_rate"])


class ComicIssueTrackingTests(FloppyApiTestCase):
    """The issue dialog can add and remove an untracked provider issue."""

    def test_create_zero_progress_issue_then_remove_only_own_tracking(self):
        """Progress is required by ComicissueForm, including a planned issue."""
        self.client.force_login(self.user1)
        metadata = {"title": "Batman #1", "source": "comicvine", "media_type": "comicissue", "media_id": "760231"}
        with patch("app.providers.services.get_media_metadata", return_value=metadata):
            response = self.client.post("/api/v1/media/comicissue/", {
                "source": "comicvine", "media_id": "760231", "status": "Planning", "progress": 0,
            }, format="json")
        self.assertEqual(response.status_code, HTTP.CREATED, response.content)
        row = ComicIssue.objects.get(user=self.user1, item__media_id="760231")
        self.assertEqual(row.progress, 0)
        response = self.client.patch("/api/v1/media/comicissue/comicvine/760231/", {
            "status": None, "entry_source": "Import", "notes": "Issue note",
        }, format="json")
        self.assertEqual(response.status_code, HTTP.OK, response.content)
        row.refresh_from_db()
        self.assertIsNone(row.status)
        self.assertEqual(row.entry_source, "Import")
        self.assertEqual(row.notes, "Issue note")
        response = self.client.patch("/api/v1/media/comicissue/comicvine/760231/", {
            "status": "", "entry_source": "", "notes": "",
        }, format="json")
        self.assertEqual(response.status_code, HTTP.OK, response.content)
        row.refresh_from_db()
        self.assertIsNone(row.status)
        self.assertEqual(row.entry_source, "")
        self.assertEqual(row.notes, "")
        other = ComicIssue.objects.create(user=self.user2, item=row.item, status="Planning", progress=0)
        response = self.client.delete("/api/v1/media/comicissue/comicvine/760231/")
        self.assertEqual(response.status_code, HTTP.NO_CONTENT)
        self.assertFalse(ComicIssue.objects.filter(user=self.user1, item=row.item).exists())
        self.assertTrue(ComicIssue.objects.filter(pk=other.pk).exists())


class WatchProvidersMetadataTests(FloppyApiTestCase):
    """The metadata projection carries the viewer's region providers."""

    def _get(self, item):
        self.client.force_login(self.user1)
        return self.client.get(
            f"/api/v1/media/{item.media_type}/{item.source}/{item.media_id}/metadata/",
        ).json()

    def test_region_providers_are_named(self):
        """Flatrate providers of the viewer's region are listed by name."""
        item = self.items_by_type[MediaTypes.MOVIE.value][0]
        item.watch_providers = {
            "US": {"flatrate": [{"provider_id": 1, "provider_name": "Netflix"}]},
        }
        item.save()
        self.user1.watch_provider_region = "US"
        self.user1.save()
        self.assertEqual(self._get(item)["watch_providers"], {"US": ["Netflix"]})

    def test_unset_region_omits_providers(self):
        """No region configured means no watch_providers key."""
        item = self.items_by_type[MediaTypes.MOVIE.value][1]
        item.watch_providers = {
            "US": {"flatrate": [{"provider_id": 1, "provider_name": "Netflix"}]},
        }
        item.save()
        self.assertNotIn("watch_providers", self._get(item))


class SeerrRequestTests(FloppyApiTestCase):
    """GET/POST /media/{type}/tmdb/{id}/request/ mirrors the Seerr panel."""

    URL = "/api/v1/media/movie/tmdb/701/request/"

    def _configure(self):
        self.user1.seerr_url = "https://seerr.example.test"
        self.user1.seerr_api_key = "x"
        self.user1.seerr_user_id = 4
        self.user1.save()
        self.client.force_login(self.user1)

    def test_unconfigured_is_not_found(self):
        """Without Seerr settings the panel does not exist."""
        self.client.force_login(self.user1)
        self.assertEqual(self.client.get(self.URL).status_code, HTTP.NOT_FOUND)

    def test_non_tmdb_is_not_found(self):
        """Only TMDB ids can be requested."""
        self._configure()
        response = self.client.get("/api/v1/media/movie/igdb/701/request/")
        self.assertEqual(response.status_code, HTTP.NOT_FOUND)

    def test_get_returns_summary(self):
        """GET returns Seerr's status for the title."""
        self._configure()
        client = MagicMock()
        client.media.return_value = {"mediaInfo": {"status": 5}}
        with patch.object(seerr_api.SeerrClient, "for_user", return_value=client):
            response = self.client.get(self.URL)
        self.assertEqual(response.status_code, HTTP.OK)
        body = response.json()
        self.assertEqual(body["status"], "available")
        self.assertFalse(body["requestable"])

    def test_post_requests_the_title(self):
        """POST creates the request as the configured Seerr user."""
        self._configure()
        client = MagicMock()
        client.media.return_value = {"mediaInfo": {"status": 2}}
        with patch.object(seerr_api.SeerrClient, "for_user", return_value=client):
            response = self.client.post(self.URL, {}, content_type="application/json")
        self.assertEqual(response.status_code, HTTP.OK)
        client.request.assert_called_once_with("movie", "701", 4, seasons=None)

    def test_seerr_failure_is_a_bad_gateway(self):
        """A Seerr error surfaces with its message."""
        self._configure()
        client = MagicMock()
        client.media.side_effect = seerr_api.SeerrError("offline")
        with patch.object(seerr_api.SeerrClient, "for_user", return_value=client):
            response = self.client.get(self.URL)
        self.assertEqual(response.status_code, HTTP.BAD_GATEWAY)
        self.assertEqual(response.json()["detail"], "offline")


class CollectionEntriesTests(FloppyApiTestCase):
    """GET /media/{type}/{source}/{id}/collection/entries/ finds one title's copies."""

    def test_season_disclosure_includes_missing_episodes_for_current_user(self):
        from app.models import CollectionEntry

        show = self.items_by_type[MediaTypes.TV.value][0]
        season = Item.objects.create(media_type="season", source=show.source, media_id=show.media_id,
                                     title="Season", season_number=79)
        episodes = [Item.objects.create(media_type="episode", source=show.source, media_id=show.media_id,
                                       title=f"Episode {number}", season_number=79, episode_number=number)
                    for number in (1, 2)]
        CollectionEntry.objects.create(user=self.user1, item=episodes[0], resolution="720p")
        CollectionEntry.objects.create(user=self.user2, item=episodes[1], resolution="1080p")
        self.client.force_login(self.user1)
        response = self.client.get(
            f"/api/v1/media/tv/{show.source}/{show.media_id}/collection/entries/",
        )
        self.assertEqual(response.status_code, HTTP.OK)
        row = next(row for row in response.json()["audit_entries"] if row["id"] == season.id)
        self.assertEqual([(episode["id"], episode["resolutions"]) for episode in row["episodes"]],
                         [(episodes[0].id, ["720p"]), (episodes[1].id, [])])

    def test_episode_resolution_counts_follow_coverage_not_record_count(self):
        from api.fork_views_detail import _collection_episode_resolution_counts
        from app.models import CollectionEntry, Item

        show = self.items_by_type[MediaTypes.TV.value][0]
        season = Item.objects.create(media_type="season", source=show.source,
                                     media_id=show.media_id, title="Season", season_number=77)
        episodes = [Item.objects.create(media_type="episode", source=show.source,
                                       media_id=show.media_id, title=f"Episode {number}",
                                       season_number=77, episode_number=number)
                    for number in range(1, 4)]
        special = Item.objects.create(media_type="episode", source=show.source,
                                      media_id=show.media_id, title="Special",
                                      season_number=0, episode_number=1)
        CollectionEntry.objects.create(user=self.user1, item=season)
        CollectionEntry.objects.create(user=self.user1, item=episodes[0], resolution="720p")
        CollectionEntry.objects.create(user=self.user1, item=episodes[0], resolution="1080p")
        CollectionEntry.objects.create(user=self.user1, item=episodes[1], resolution="720p")
        CollectionEntry.objects.create(user=self.user1, item=special)
        CollectionEntry.objects.create(user=self.user2, item=episodes[2], resolution="4k")

        counts = _collection_episode_resolution_counts(self.user1, show, {"collected_episodes": 3})
        self.assertEqual({row["resolution"]: row["count"] for row in counts},
                         {"1080p": 1, "720p": 1, "": 1})
        counts = _collection_episode_resolution_counts(self.user1, season, {"collected_episodes": 2})
        self.assertEqual({row["resolution"]: row["count"] for row in counts},
                         {"1080p": 1, "720p": 1})

    def test_entries_are_found_past_the_first_page(self):
        """Only this item's copies come back, whatever else the collection holds."""
        from app.models import CollectionEntry

        target = self.items_by_type[MediaTypes.MOVIE.value][0]
        other = self.items_by_type[MediaTypes.MOVIE.value][1]
        CollectionEntry.objects.create(user=self.user1, item=other, media_type="Blu-ray")
        mine = CollectionEntry.objects.create(user=self.user1, item=target, media_type="Digital")
        CollectionEntry.objects.create(user=self.user2, item=target, media_type="DVD")
        self.client.force_login(self.user1)
        response = self.client.get(
            f"/api/v1/media/movie/{target.source}/{target.media_id}/collection/entries/",
        )
        self.assertEqual(response.status_code, HTTP.OK)
        self.assertEqual([row["id"] for row in response.json()["results"]], [mine.id])

    def test_untracked_title_is_empty(self):
        """A title with no copies is an empty list, not an error."""
        self.client.force_login(self.user1)
        response = self.client.get("/api/v1/media/movie/tmdb/nope/collection/entries/")
        self.assertEqual(response.json(), {"results": [], "context_copies": []})

    def test_context_includes_owned_descendants_but_not_other_users(self):
        """Resolution breakdown includes episode copies without changing edit scope."""
        from app.models import CollectionEntry, Item

        show = self.items_by_type[MediaTypes.TV.value][0]
        episode = Item.objects.create(
            media_type=MediaTypes.EPISODE.value, library_media_type=MediaTypes.EPISODE.value,
            source=show.source, media_id=show.media_id, title="Episode",
            season_number=40, episode_number=40,
        )
        own = CollectionEntry.objects.create(
            user=self.user1, item=episode, resolution="1080p", audio_codec="DTS",
        )
        CollectionEntry.objects.create(user=self.user2, item=episode, resolution="4k")
        self.client.force_login(self.user1)
        response = self.client.get(
            f"/api/v1/media/tv/{show.source}/{show.media_id}/collection/entries/",
        )
        self.assertEqual(response.json()["results"], [])
        context_copies = response.json()["context_copies"]
        self.assertEqual([copy["id"] for copy in context_copies], [own.id])
        self.assertEqual(context_copies[0]["audio_codec"], "DTS")

    def test_show_carries_the_collected_counts(self):
        """A show answers with the web card's seasons and episodes counts."""
        show = self.items_by_type[MediaTypes.TV.value][0]
        self.client.force_login(self.user1)
        response = self.client.get(
            f"/api/v1/media/tv/{show.source}/{show.media_id}/collection/entries/",
        )
        self.assertEqual(response.status_code, HTTP.OK)
        stats = response.json()["stats"]
        self.assertEqual(stats["collected_seasons"], 0)
        self.assertEqual(stats["collected_episodes"], 0)
        self.assertIn("total_episodes", stats)

    def test_season_audit_updates_and_removes_only_the_owners_episodes(self):
        """Bulk season actions keep other users and other seasons untouched."""
        from app.models import CollectionEntry, Item

        show = self.items_by_type[MediaTypes.TV.value][0]
        season = Item.objects.create(
            media_type=MediaTypes.SEASON.value, source=show.source, media_id=show.media_id,
            title="Audit season", season_number=77,
        )
        episode = Item.objects.create(
            media_type=MediaTypes.EPISODE.value, source=show.source, media_id=show.media_id,
            title="Audit episode", season_number=77, episode_number=77,
        )
        mine = CollectionEntry.objects.create(user=self.user1, item=episode, resolution="720p")
        other = CollectionEntry.objects.create(user=self.user2, item=episode, resolution="720p")
        self.client.force_login(self.user1)
        url = reverse("api_collection_season_audit", args=[season.id])
        rejected = self.client.patch(url, data={"bitrate": "invalid"}, content_type="application/json")
        self.assertEqual(rejected.status_code, HTTP.BAD_REQUEST)
        mine.refresh_from_db()
        self.assertEqual(mine.resolution, "720p")
        response = self.client.patch(url, data={"resolution": "1080p"}, content_type="application/json")
        self.assertEqual(response.status_code, HTTP.OK)
        mine.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(mine.resolution, "1080p")
        self.assertEqual(other.resolution, "720p")
        self.assertEqual(self.client.delete(url).status_code, HTTP.NO_CONTENT)
        self.assertFalse(CollectionEntry.objects.filter(id=mine.id).exists())
        self.assertTrue(CollectionEntry.objects.filter(id=other.id).exists())

    def test_season_requires_tv(self):
        """Season coordinates only exist under tv."""
        self.client.force_login(self.user1)
        response = self.client.get("/api/v1/media/movie/tmdb/1/2/collection/entries/")
        self.assertEqual(response.status_code, HTTP.BAD_REQUEST)


class ProgressCardTests(FloppyApiTestCase):
    """GET /media/tv/{source}/{id}/progress-card/ carries the web card and breakdown."""

    def test_show_and_season_answer_with_the_card(self):
        """A tracked show and its season both return a percent and a breakdown."""
        show = self.items_by_type[MediaTypes.TV.value][0]
        season = self.items_by_type[MediaTypes.SEASON.value][0]
        self.client.force_login(self.user1)
        base = f"/api/v1/media/tv/{show.source}/{show.media_id}"

        def catalogue(media_type, media_id, source, season_numbers=None, **kwargs):
            if media_type == "tv":
                return {"related": {"seasons": [{"season_number": season.season_number}]}}
            return {
                f"season/{number}": {"season_title": "Season", "episodes": []}
                for number in season_numbers
            }

        with patch("app.providers.services.get_media_metadata", side_effect=catalogue):
            for path in (f"{base}/progress-card/", f"{base}/{season.season_number}/progress-card/"):
                response = self.client.get(path)
                self.assertEqual(response.status_code, HTTP.OK, path)
                body = response.json()
                self.assertIn("percent", body["card"])
                self.assertIn("seasons", body["model"])

    def test_show_carries_the_watched_duration_the_web_hero_prints(self):
        """The show card says "2h 48min" from the web's play stats; a season has none."""
        show = self.items_by_type[MediaTypes.TV.value][0]
        season = self.items_by_type[MediaTypes.SEASON.value][0]
        self.client.force_login(self.user1)
        base = f"/api/v1/media/tv/{show.source}/{show.media_id}"

        def catalogue(media_type, media_id, source, season_numbers=None, **kwargs):
            if media_type == "tv":
                return {"related": {"seasons": [{"season_number": season.season_number}]}}
            return {
                f"season/{number}": {"season_title": "Season", "episodes": []}
                for number in season_numbers
            }

        with (
            patch("app.providers.services.get_media_metadata", side_effect=catalogue),
            patch(
                "app.activity_builders._build_detail_activity_state",
                return_value=({"total_minutes": 168}, None),
            ),
        ):
            self.assertEqual(self.client.get(f"{base}/progress-card/").json()["watched_text"], "2h 48min")
            self.assertEqual(
                self.client.get(f"{base}/{season.season_number}/progress-card/").json()["watched_text"],
                "",
            )

    def test_untracked_title_is_not_found(self):
        """No tracker, no card."""
        self.client.force_login(self.user1)
        response = self.client.get("/api/v1/media/tv/tmdb/nope/progress-card/")
        self.assertEqual(response.status_code, HTTP.NOT_FOUND)

    def test_movies_have_no_progress_card(self):
        """Only shows and seasons have the card."""
        self.client.force_login(self.user1)
        response = self.client.get("/api/v1/media/movie/tmdb/1/progress-card/")
        self.assertEqual(response.status_code, HTTP.NOT_FOUND)


class CheckInTests(FloppyApiTestCase):
    """/media/{type}/{source}/{id}/checkin/ offers and runs the web Check-in button."""

    def setUp(self):
        """Sign in and address the first tracked book."""
        super().setUp()
        self.item = self.items_by_type[MediaTypes.BOOK.value][0]
        self.path = f"/api/v1/media/book/{self.item.source}/{self.item.media_id}/checkin/"
        self.client.force_login(self.user1)

    def test_book_offers_check_in_then_stop(self):
        """A tracked book starts a session; the actions then offer Stop."""
        keys = [action["key"] for action in self.client.get(self.path).json()["actions"]]
        self.assertIn("start", keys)
        self.assertEqual(keys[-1], "edit")
        started = self.client.post(self.path, {"action": "start"}, format="json")
        self.assertEqual(started.status_code, HTTP.OK)
        self.assertEqual(started.json()["actions"][0]["key"], "stop")
        cancelled = self.client.post(self.path, {"action": "cancel"}, format="json")
        self.assertIn("start", [action["key"] for action in cancelled.json()["actions"]])

    def test_unknown_action_and_untracked_title_are_refused(self):
        """Only the web's actions run, and only for an entry the caller tracks."""
        refused = self.client.post(self.path, {"action": "explode"}, format="json")
        self.assertEqual(refused.status_code, HTTP.BAD_REQUEST)
        missing = self.client.get("/api/v1/media/book/manual/not-tracked/checkin/")
        self.assertEqual(missing.status_code, HTTP.NOT_FOUND)


class ShowCollectTests(FloppyApiTestCase):
    """PUT /media/tv/{source}/{id}[/{season}]/collect/ collects every episode once."""

    def setUp(self):
        """Give one show a season and two episodes."""
        super().setUp()
        base = {"source": Sources.TMDB.value, "media_id": "9300"}
        Item.objects.create(media_type=MediaTypes.TV.value, title="Show", **base)
        Item.objects.create(media_type=MediaTypes.SEASON.value, season_number=1, title="S1", **base)
        self.episodes = [
            Item.objects.create(
                media_type=MediaTypes.EPISODE.value,
                season_number=1,
                episode_number=number,
                title=f"Ep {number}",
                **base,
            )
            for number in (1, 2)
        ]
        self.client.force_login(self.user1)

    def test_season_and_show_collect_each_episode_once(self):
        """The second call adds nothing and reports what was already there."""
        from app.models import CollectionEntry

        path = "/api/v1/media/tv/tmdb/9300/1/collect/"
        first = self.client.put(path, {"media_type": "Digital"}, format="json")
        self.assertEqual(first.json(), {"created": 2, "already_collected": 0})
        self.assertEqual(
            CollectionEntry.objects.filter(user=self.user1, item__in=self.episodes).count(), 2,
        )
        again = self.client.put("/api/v1/media/tv/tmdb/9300/collect/", {}, format="json")
        self.assertEqual(again.json(), {"created": 0, "already_collected": 2})

    def test_unknown_title_and_movies_are_refused(self):
        """Only shows are collected this way, and only ones Floppy has."""
        missing = self.client.put("/api/v1/media/tv/tmdb/none/collect/", {}, format="json")
        self.assertEqual(missing.status_code, HTTP.NOT_FOUND)
        movie = self.client.put("/api/v1/media/movie/tmdb/9300/collect/", {}, format="json")
        self.assertEqual(movie.status_code, HTTP.BAD_REQUEST)


class ArtworkAndScoresTests(FloppyApiTestCase):
    """GET .../detail/gallery/ and .../scores/ expose what the web chip row and carousel read."""

    def setUp(self):
        """Create a rated anime show and sign in."""
        super().setUp()
        self.item = Item.objects.create(
            media_type=MediaTypes.TV.value,
            library_media_type=MediaTypes.ANIME.value,
            source=Sources.MAL.value,
            media_id="9200",
            title="Rated",
            imdb_rating=8.46,
            imdb_rating_count=1200,
            mal_rating=7.9,
            mal_rating_count=50,
        )
        self.client.force_login(self.user1)

    def test_scores_list_stored_chips(self):
        """IMDb and MAL chips carry the stored rating truncated to one decimal."""
        response = self.client.get("/api/v1/media/tv/mal/9200/scores/")
        self.assertEqual(response.status_code, HTTP.OK)
        chips = {chip["key"]: chip for chip in response.json()["chips"]}
        self.assertEqual(chips["imdb"]["rating"], 8.4)
        self.assertEqual(chips["mal"]["count"], 50)
        self.assertNotIn("opencritic", chips)

    def test_scores_name_the_source_logos(self):
        """The response carries absolute static logo URLs for the cards."""
        response = self.client.get("/api/v1/media/tv/mal/9200/scores/")
        logos = response.json()["logos"]
        self.assertTrue(logos["provider"].endswith("myanimelist-logo.svg"))
        self.assertTrue(logos["imdb"].startswith("http"))

    def test_issues_page_a_comic_volume(self):
        """A comic volume lists its provider issues with the viewer's read state."""
        issues = [
            {"media_id": str(n), "source": "comicvine", "issue_number": str(n), "title": f"Issue {n}",
             "image": "", "cover_date": None}
            for n in range(1, 4)
        ]
        with patch("app.providers.services.get_media_metadata", return_value={"issues": issues}):
            response = self.client.get("/api/v1/media/comic/comicvine/55/issues/?limit=2&offset=1")
        self.assertEqual(response.status_code, HTTP.OK)
        body = response.json()
        self.assertEqual(body["pagination"], {"total": 3, "limit": 2, "offset": 1})
        self.assertEqual([row["issue_number"] for row in body["results"]], ["2", "3"])
        self.assertFalse(body["results"][0]["read"])
        self.assertEqual(
            self.client.get("/api/v1/media/movie/tmdb/1/issues/").status_code, HTTP.BAD_REQUEST,
        )

    def test_scores_empty_for_unknown_item(self):
        """An untracked, unstored title has no chips."""
        response = self.client.get("/api/v1/media/movie/tmdb/1/scores/")
        body = response.json()
        self.assertEqual((body["chips"], body["graphs"], body["gameLengths"]), ([], {}, None))

    def test_artwork_unavailable_for_manual_source(self):
        """Sources without a gallery answer with no gallery rather than an error."""
        response = self.client.get("/api/v1/media/movie/manual/m1/detail/gallery/")
        self.assertEqual(response.status_code, HTTP.OK)
        self.assertIsNone(response.json()["gallery"])

    def test_artwork_maps_carousel_media(self):
        """The carousel payload is passed through for supported sources."""
        data = {"video": None, "photos": [{"url": "u", "thumb_url": "t"}],
                "overview": {"url": "o", "thumb_url": "o", "logo_url": "l"},
                "hero": {"url": "o", "logo_url": "l"}}
        with patch("app.carousel.resolve_carousel_media", return_value=data):
            body = self.client.get("/api/v1/media/movie/tmdb/5/detail/gallery/").json()
        self.assertEqual(body["gallery"]["overview"]["logo_url"], "l")
        self.assertEqual(body["gallery"]["photos"][0]["thumb_url"], "t")


class ActivityTimelineTests(FloppyApiTestCase):
    """GET .../activity-timeline/ groups the web history entries by day."""

    def test_entries_grouped_by_day_with_descriptions(self):
        """A tracked movie yields day groups of formatted change lines."""
        from app.models import Movie, Status

        item = Item.objects.create(
            media_type=MediaTypes.MOVIE.value, source=Sources.MANUAL.value,
            media_id="tl1", title="Timeline",
        )
        Movie.objects.create(item=item, user=self.user1, status=Status.COMPLETED.value)
        self.client.force_login(self.user1)
        body = self.client.get("/api/v1/media/movie/manual/tl1/activity-timeline/").json()
        self.assertFalse(body["multipleInstances"])
        self.assertTrue(body["days"])
        first = body["days"][0]["entries"][0]
        self.assertTrue(first["changes"])
        self.assertTrue(first["date"])

    def test_untracked_title_is_empty(self):
        """No tracked rows means no days."""
        self.client.force_login(self.user1)
        body = self.client.get("/api/v1/media/movie/manual/nothing/activity-timeline/").json()
        self.assertEqual(body["days"], [])


class CoverAndEditionTests(FloppyApiTestCase):
    """The cover and Hardcover edition endpoints enforce ownership and rules."""

    def setUp(self):
        """Track a Hardcover book."""
        super().setUp()
        from app.models import Book, Status

        self.item = Item.objects.create(
            media_type=MediaTypes.BOOK.value, source=Sources.HARDCOVER.value,
            media_id="hc1", title="Book",
        )
        Book.objects.create(item=self.item, user=self.user1, status=Status.PLANNING.value)
        self.client.force_login(self.user1)

    def test_cover_requires_a_choice(self):
        """POST without a file or link is a 400 with the web message."""
        response = self.client.post(f"/api/v1/metadata/items/{self.item.id}/cover/", {})
        self.assertEqual(response.status_code, HTTP.BAD_REQUEST)
        self.assertIn("Choose an image", response.json()["detail"])

    def test_cover_hidden_from_other_users(self):
        """An item the caller does not track is a 404."""
        self.client.force_login(self.user2)
        response = self.client.get(f"/api/v1/metadata/items/{self.item.id}/cover/")
        self.assertEqual(response.status_code, HTTP.NOT_FOUND)

    def test_cover_link_is_stored(self):
        """A fetched link becomes the item's cover."""
        with patch("app.local_covers.fetch_cover", return_value="/covers/abc.jpg"):
            response = self.client.post(
                f"/api/v1/metadata/items/{self.item.id}/cover/", {"cover_url": "https://x.test/a.jpg"},
            )
        self.assertEqual(response.status_code, HTTP.OK)
        self.item.refresh_from_db()
        self.assertEqual(self.item.image, "/covers/abc.jpg")

    def test_edition_choice_is_saved_and_listed(self):
        """Choosing an edition persists it and the list reports it selected."""
        url = f"/api/v1/metadata/items/{self.item.id}/hardcover-editions/"
        self.assertEqual(self.client.post(url, {}).status_code, HTTP.BAD_REQUEST)
        self.assertEqual(self.client.post(url, {"edition_id": "77"}).status_code, HTTP.OK)
        edition = {"id": "77", "title": "First", "image": ""}
        with patch("app.providers.hardcover.editions", return_value=[edition]):
            body = self.client.get(url).json()
        self.assertEqual(body["selected"], "77")
        self.assertEqual(len(body["results"]), 1)

    def test_cover_base64_rejects_garbage_and_stores_bytes(self):
        """A JSON base64 body is decoded, and invalid data is a 400."""
        url = f"/api/v1/metadata/items/{self.item.id}/cover/"
        bad = self.client.post(url, {"cover_base64": "***"}, content_type="application/json")
        self.assertEqual(bad.status_code, HTTP.BAD_REQUEST)
        with patch("app.local_covers.store_bytes", return_value="/covers/b64.png") as store:
            ok = self.client.post(url, {"cover_base64": "aGk="}, content_type="application/json")
        self.assertEqual(ok.status_code, HTTP.OK)
        store.assert_called_once_with(b"hi")

    @patch("api.helpers.BODY_SLACK_BYTES", 0)
    @patch("app.local_covers.MAX_COVER_BYTES", 6)
    @patch("rest_framework.parsers.JSONParser.parse")
    def test_oversized_cover_body_is_rejected_before_parsing(self, parse):
        """The declared length is checked first: a 413 without reading the body."""
        response = self.client.post(
            f"/api/v1/metadata/items/{self.item.id}/cover/",
            {"cover_base64": "A" * 64}, content_type="application/json",
        )
        self.assertEqual(response.status_code, HTTP.REQUEST_ENTITY_TOO_LARGE)
        parse.assert_not_called()

    @patch("app.local_covers.MAX_COVER_BYTES", 2)
    @patch("api.fork_views_metadata.base64.b64decode")
    def test_large_cover_is_rejected_before_decoding(self, decode):
        """An oversized encoded image never allocates decoded image bytes."""
        response = self.client.post(
            f"/api/v1/metadata/items/{self.item.id}/cover/",
            {"cover_base64": "aGVsbG8="}, content_type="application/json",
        )
        self.assertEqual(response.status_code, HTTP.BAD_REQUEST)
        decode.assert_not_called()

    def test_game_lengths_cards_are_listed(self):
        """A game with stored lengths yields label, value and submission count."""
        game = Item.objects.create(
            media_type=MediaTypes.GAME.value, source=Sources.IGDB.value, media_id="777", title="G",
        )
        ctx = {"available": True, "source_label": "How Long to Beat", "source_url": "u",
               "cards": [{"label": "Main Story", "value": "10h", "count": 3, "icon_template": "x"}]}
        with patch("app.detail_builders._build_game_lengths_context", return_value=ctx):
            body = self.client.get(f"/api/v1/media/game/igdb/{game.media_id}/scores/").json()
        self.assertEqual(body["gameLengths"]["cards"], [{"label": "Main Story", "value": "10h", "count": 3}])
