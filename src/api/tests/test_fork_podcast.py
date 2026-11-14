# FORK: tests for the first-class podcast API endpoints.
import datetime
from http import HTTPStatus as HTTP  # noqa: N814
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.messages import get_messages
from django.core.cache import cache
from django.urls import reverse

from app import fork_services_podcast
from app.models import (
    Podcast,
    PodcastEpisode,
    PodcastShow,
    PodcastShowTracker,
    Status,
)

from .base import FloppyApiTestCase


class PodcastApiTestCase(FloppyApiTestCase):
    """Shared podcast catalog fixtures."""

    def setUp(self):
        """Create a show with two catalog episodes."""
        super().setUp()
        self.show = PodcastShow.objects.create(
            podcast_uuid="show-uuid-1",
            title="Test Show",
            image="https://example.com/show.jpg",
            description="A test podcast description.",
            genres=["Technology", "News"],
            language="en",
            website_url="https://example.com/show",
        )
        self.episode1 = PodcastEpisode.objects.create(
            show=self.show,
            episode_uuid="ep-uuid-1",
            title="Episode One",
            published=datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC),
            duration=1800,
        )
        self.episode2 = PodcastEpisode.objects.create(
            show=self.show,
            episode_uuid="ep-uuid-2",
            title="Episode Two",
            published=datetime.datetime(2024, 2, 1, tzinfo=datetime.UTC),
            duration=2400,
        )


class ShowTrackerTests(PodcastApiTestCase):
    """Show tracker CRUD."""

    def test_detail_includes_show_metadata(self):
        """Show detail exposes the stored discovery metadata."""
        response = self.call_api(
            "get",
            "api_podcast_show_detail",
            args=(self.show.id,),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, HTTP.OK)
        payload = response.json()
        self.assertEqual(payload["description"], "A test podcast description.")
        self.assertEqual(payload["genres"], ["Technology", "News"])
        self.assertEqual(payload["language"], "en")
        self.assertEqual(payload["website_url"], "https://example.com/show")
        self.assertEqual(payload["listened_count"], 0)
        self.assertEqual(payload["episode_count"], self.show.episodes.count())

    def test_show_payloads_never_expose_a_feed_url(self):
        """A private feed one user imported is unreachable for another."""
        secret_url = "https://feeds.example.com/private/secret-token-abc123.xml"
        self.show.rss_feed_url = secret_url
        self.show.save(update_fields=["rss_feed_url"])
        PodcastShowTracker.objects.create(user=self.user1, show=self.show)

        responses = [
            self.call_api(
                "get",
                "api_podcast_show_detail",
                args=(self.show.id,),
                headers=self.auth_headers2,
            ),
            self.call_api(
                "patch",
                "api_podcast_show_detail",
                args=(self.show.id,),
                payload={"status": Status.PLANNING.value},
                headers=self.auth_headers2,
            ),
            self.call_api(
                "get",
                "api_podcast_shows",
                headers=self.auth_headers2,
            ),
        ]
        PodcastShowTracker.objects.filter(user=self.user2).delete()
        responses.append(
            self.call_api(
                "post",
                "api_podcast_shows",
                payload={"show_id": self.show.id},
                headers=self.auth_headers2,
            ),
        )

        for response in responses:
            self.assertIn(response.status_code, (HTTP.OK, HTTP.CREATED))
            body = response.content.decode()
            self.assertNotIn("secret-token-abc123", body)
            self.assertNotIn("rss_feed_url", body)

    def test_generic_media_detail_never_exposes_a_feed_url(self):
        """The generic media route resolves the shared show without its feed."""
        self._metadata_patcher.stop()  # use the real catalog lookup
        self.show.rss_feed_url = (
            "https://feeds.example.com/private/path-secret-abc123/feed.xml"
            "?token=query-secret-xyz789"
        )
        self.show.save(update_fields=["rss_feed_url"])
        PodcastShowTracker.objects.create(user=self.user1, show=self.show)

        response = self.call_api(
            "get",
            "api_media_detail",
            args=("podcast", self.show.source, self.show.podcast_uuid),
            headers=self.auth_headers2,
        )

        self.assertEqual(response.status_code, HTTP.OK)
        body = response.content.decode()
        self.assertNotIn("rss_feed_url", body)
        self.assertNotIn("path-secret-abc123", body)
        self.assertNotIn("query-secret-xyz789", body)
        payload = response.json()
        self.assertEqual(payload["title"], "Test Show")
        self.assertEqual(payload["details"]["show_uuid"], "show-uuid-1")
        self.assertEqual(
            payload["details"]["website_url"], "https://example.com/show"
        )

    def test_track_list_patch_delete(self):
        """Full tracker lifecycle."""
        created = self.call_api(
            "post",
            "api_podcast_shows",
            payload={"show_id": self.show.id, "score": "8.0"},
            headers=self.auth_headers,
        )
        self.assertEqual(created.status_code, HTTP.CREATED)
        self.assertEqual(created.json()["tracker"]["score"], "8.0")
        # The label, like the music endpoints, never the integer status code.
        self.assertEqual(created.json()["tracker"]["status"], Status.IN_PROGRESS.value)

        listed = self.call_api("get", "api_podcast_shows", headers=self.auth_headers)
        self.assertEqual(listed.json()["pagination"]["total"], 1)

        patched = self.call_api(
            "patch",
            "api_podcast_show_detail",
            args=(self.show.id,),
            payload={"notes": "great show"},
            headers=self.auth_headers,
        )
        self.assertEqual(patched.status_code, HTTP.OK)
        tracker = PodcastShowTracker.objects.get(user=self.user1, show=self.show)
        self.assertEqual(tracker.notes, "great show")
        self.assertEqual(str(tracker.score), "8.0")

        deleted = self.call_api(
            "delete",
            "api_podcast_show_detail",
            args=(self.show.id,),
            headers=self.auth_headers,
        )
        self.assertEqual(deleted.status_code, HTTP.NO_CONTENT)
        self.assertFalse(
            PodcastShowTracker.objects.filter(
                user=self.user1,
                show=self.show,
            ).exists(),
        )

    def test_unknown_show_not_found(self):
        """Unknown show ids 404."""
        response = self.call_api(
            "get",
            "api_podcast_show_detail",
            args=(999999,),
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, HTTP.NOT_FOUND)

    def test_itunes_import_error_returns_the_written_message(self):
        """An import refusal answers with the importer's own text."""
        from app.services.podcast_import import PodcastImportError

        with patch(
            "api.fork_views_podcast.podcast_import.import_show_from_itunes_id",
            side_effect=PodcastImportError("No podcast found for iTunes id 12345."),
        ):
            response = self.call_api(
                "post",
                "api_podcast_shows",
                payload={"itunes_id": "12345"},
                headers=self.auth_headers,
            )
        self.assertEqual(response.status_code, HTTP.BAD_REQUEST)
        self.assertEqual(
            response.json(), {"detail": "No podcast found for iTunes id 12345."}
        )

    @patch("api.fork_views_podcast.podcast_import.import_show_from_itunes_id")
    def test_itunes_id_track_rejects_invalid_tracker_before_importing(
        self,
        mock_import,
    ):
        """An invalid tracker payload 400s without importing the show.

        Regression test: the import (RSS fetch, show creation, episode
        catalog import) must not happen for a request that will fail
        tracker validation anyway.
        """
        response = self.call_api(
            "post",
            "api_podcast_shows",
            payload={"itunes_id": "12345", "score": "not-a-number"},
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, HTTP.BAD_REQUEST)
        mock_import.assert_not_called()


class PodcastLookupTests(PodcastApiTestCase):
    """Read-only iTunes preview, ahead of any import."""

    ITUNES_DATA = {
        "feed_url": "https://example.com/feed.xml",
        "title": "Unimported Show",
        "author": "Some Network",
        "artwork_url": "https://example.com/art.jpg",
        "description": "A show nobody's tracked yet.",
        "genres": ["Comedy"],
        "language": "en",
    }

    def setUp(self):
        """Drop the feed cache so previews don't leak between tests."""
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)

    def _lookup(self, itunes_id="999888", **kwargs):
        return self.call_api(
            "get",
            "api_podcast_lookup",
            args=(itunes_id,),
            headers=self.auth_headers,
            **kwargs,
        )

    @patch("integrations.podcast_rss.fetch_episodes_from_rss")
    @patch("app.providers.pocketcasts.lookup_by_itunes_id")
    def test_lookup_returns_itunes_metadata_without_importing(
        self,
        mock_lookup,
        mock_episodes,
    ):
        """A lookup returns the provider's metadata and creates nothing."""
        mock_lookup.return_value = dict(self.ITUNES_DATA)
        mock_episodes.return_value = [
            {
                "title": "Pilot",
                "published": datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC),
                "duration": 4140,
                "episode_number": 1,
            },
        ]

        response = self._lookup()

        self.assertEqual(response.status_code, HTTP.OK)
        payload = response.json()
        self.assertEqual(payload["title"], "Unimported Show")
        self.assertEqual(payload["author"], "Some Network")
        self.assertEqual(payload["image"], "https://example.com/art.jpg")
        self.assertEqual(payload["description"], "A show nobody's tracked yet.")
        self.assertEqual(payload["genres"], ["Comedy"])
        self.assertEqual(payload["rss_feed_url"], "https://example.com/feed.xml")
        self.assertEqual(payload["website_url"], "")
        self.assertIsNone(payload["show_id"], "nothing is imported yet")

        episodes = payload["episodes"]["results"]
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["title"], "Pilot")
        # "1h 9m" — matches the format podcast_views.py sends for a tracked
        # episode's own duration; see PodcastLookupView._format_duration.
        self.assertEqual(episodes[0]["duration"], "1h 9m")
        self.assertEqual(episodes[0]["duration_seconds"], 4140)

        mock_lookup.assert_called_once_with("999888")
        # The whole feed is read, the way every other RSS caller reads it.
        mock_episodes.assert_called_once_with("https://example.com/feed.xml")
        self.assertFalse(
            PodcastShow.objects.filter(podcast_uuid="itunes:999888").exists(),
        )
        self.assertFalse(
            PodcastEpisode.objects.filter(title="Pilot").exists(),
            "a preview episode must not be persisted",
        )

    @patch("integrations.podcast_rss.fetch_episodes_from_rss")
    @patch("app.providers.pocketcasts.lookup_by_itunes_id")
    def test_lookup_paginates_episodes_with_the_standard_envelope(
        self,
        mock_lookup,
        mock_episodes,
    ):
        """A feed's episodes page like every other list in the API."""
        mock_lookup.return_value = dict(self.ITUNES_DATA)
        mock_episodes.return_value = [
            {"title": f"Ep {index}", "duration": 60} for index in range(5)
        ]

        response = self._lookup(params={"limit": 2, "offset": 2})

        self.assertEqual(response.status_code, HTTP.OK)
        episodes = response.json()["episodes"]
        self.assertEqual(episodes["pagination"]["total"], 5)
        self.assertEqual(episodes["pagination"]["limit"], 2)
        self.assertEqual(episodes["pagination"]["offset"], 2)
        self.assertEqual(
            [episode["title"] for episode in episodes["results"]],
            ["Ep 2", "Ep 3"],
        )

    @patch("integrations.podcast_rss.fetch_episodes_from_rss")
    @patch("app.providers.pocketcasts.lookup_by_itunes_id")
    def test_lookup_reports_the_show_an_import_would_reuse(
        self,
        mock_lookup,
        mock_episodes,
    ):
        """An already-imported feed previews with its existing show id."""
        mock_lookup.return_value = dict(self.ITUNES_DATA)
        mock_episodes.return_value = []
        imported = PodcastShow.objects.create(
            podcast_uuid="itunes:999888",
            title="Unimported Show",
            rss_feed_url="https://example.com/feed.xml",
        )

        response = self._lookup()

        self.assertEqual(response.status_code, HTTP.OK)
        self.assertEqual(response.json()["show_id"], imported.id)

    @patch("integrations.podcast_rss.fetch_episodes_from_rss")
    @patch("app.providers.pocketcasts.lookup_by_itunes_id")
    def test_lookup_returns_the_public_feed_not_a_stored_private_one(
        self,
        mock_lookup,
        mock_episodes,
    ):
        """A preview reports iTunes' public feed, never the imported show's."""
        mock_lookup.return_value = dict(self.ITUNES_DATA)
        mock_episodes.return_value = []
        imported = PodcastShow.objects.create(
            podcast_uuid="itunes:999888",
            title="Unimported Show",
            rss_feed_url="https://example.com/private/secret-token-abc123.xml",
        )

        response = self.call_api(
            "get",
            "api_podcast_lookup",
            args=("999888",),
            headers=self.auth_headers2,
        )

        self.assertEqual(response.status_code, HTTP.OK)
        payload = response.json()
        self.assertEqual(payload["show_id"], imported.id)
        self.assertEqual(payload["rss_feed_url"], "https://example.com/feed.xml")
        self.assertNotIn("secret-token-abc123", response.content.decode())

    @patch("integrations.podcast_rss.fetch_episodes_from_rss")
    @patch("integrations.podcast_rss.fetch_show_metadata_from_rss")
    @patch("app.providers.pocketcasts.lookup_by_itunes_id")
    def test_lookup_falls_back_to_rss_for_a_blank_itunes_description(
        self,
        mock_lookup,
        mock_rss_metadata,
        mock_episodes,
    ):
        """ITunes' own lookup often has no description; the RSS feed does."""
        mock_lookup.return_value = {
            **self.ITUNES_DATA,
            "author": "",
            "artwork_url": "",
            "description": "",
            "genres": [],
            "language": "",
        }
        mock_rss_metadata.return_value = {
            "description": "From the feed, not iTunes.",
            "author": "Feed Author",
            "language": "en",
        }
        mock_episodes.return_value = []

        response = self._lookup()

        self.assertEqual(response.status_code, HTTP.OK)
        payload = response.json()
        self.assertEqual(payload["description"], "From the feed, not iTunes.")
        self.assertEqual(payload["author"], "Feed Author")
        self.assertEqual(payload["language"], "en")

    @patch("integrations.podcast_rss.fetch_episodes_from_rss")
    @patch("app.providers.pocketcasts.lookup_by_itunes_id")
    def test_lookup_caches_the_feed_read(self, mock_lookup, mock_episodes):
        """Paging through search results must not re-download a feed."""
        mock_lookup.return_value = dict(self.ITUNES_DATA)
        mock_episodes.return_value = []

        self.assertEqual(self._lookup().status_code, HTTP.OK)
        self.assertEqual(self._lookup().status_code, HTTP.OK)

        mock_episodes.assert_called_once()

    @patch("integrations.podcast_rss.fetch_episodes_from_rss")
    @patch("app.providers.pocketcasts.lookup_by_itunes_id")
    def test_lookup_without_a_feed_previews_no_episodes(
        self,
        mock_lookup,
        mock_episodes,
    ):
        """An iTunes result with no feed still previews its own metadata."""
        mock_lookup.return_value = {**self.ITUNES_DATA, "feed_url": ""}

        response = self._lookup()

        self.assertEqual(response.status_code, HTTP.OK)
        payload = response.json()
        self.assertEqual(payload["title"], "Unimported Show")
        self.assertEqual(payload["rss_feed_url"], "")
        self.assertEqual(payload["website_url"], "")
        self.assertEqual(payload["episodes"]["results"], [])
        mock_episodes.assert_not_called()

    @patch("integrations.podcast_rss.fetch_episodes_from_rss")
    @patch("app.providers.pocketcasts.lookup_by_itunes_id")
    def test_lookup_survives_an_unreadable_feed(self, mock_lookup, mock_episodes):
        """A feed that won't parse previews as no episodes, not a 500."""
        mock_lookup.return_value = dict(self.ITUNES_DATA)
        mock_episodes.side_effect = ValueError("bad feed")

        response = self._lookup()

        self.assertEqual(response.status_code, HTTP.OK)
        self.assertEqual(response.json()["episodes"]["results"], [])

    @patch("app.providers.pocketcasts.lookup_by_itunes_id")
    def test_lookup_of_unknown_id_404s(self, mock_lookup):
        """An id iTunes doesn't recognise 404s rather than 500ing."""
        mock_lookup.side_effect = self._provider_error(HTTP.NOT_FOUND)

        response = self._lookup(itunes_id="0")

        self.assertEqual(response.status_code, HTTP.NOT_FOUND)

    @patch("app.providers.pocketcasts.lookup_by_itunes_id")
    def test_lookup_reports_a_provider_outage_as_a_server_error(self, mock_lookup):
        """A failing provider is the server's problem, not a missing podcast."""
        mock_lookup.side_effect = self._provider_error(HTTP.BAD_GATEWAY)

        response = self._lookup()

        self.assertEqual(response.status_code, HTTP.INTERNAL_SERVER_ERROR)

    @patch("app.providers.pocketcasts.lookup_by_itunes_id")
    def test_lookup_outage_names_the_provider_and_nothing_else(self, mock_lookup):
        """The failure body carries no exception-derived text."""
        mock_lookup.side_effect = self._provider_error(HTTP.BAD_GATEWAY)

        payload = self._lookup().json()

        self.assertEqual(payload, {"detail": "Pocket Casts lookup failed."})

    @staticmethod
    def _provider_error(status_code):
        """Build the ProviderAPIError a failing provider call would raise."""
        from app.providers import services

        return services.ProviderAPIError(
            "pocketcasts",
            SimpleNamespace(response=SimpleNamespace(status_code=status_code)),
        )


class ShowEpisodesTests(PodcastApiTestCase):
    """GET podcasts/shows/{id}/episodes uses the standard limit/offset envelope."""

    def test_episodes_listing(self):
        """Episodes are returned with pagination, newest first."""
        response = self.call_api(
            "get",
            "api_podcast_show_episodes",
            args=(self.show.id,),
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, HTTP.OK)
        payload = response.json()
        self.assertEqual(payload["pagination"]["total"], 2)
        self.assertEqual(payload["pagination"]["limit"], 20)
        self.assertEqual(payload["pagination"]["offset"], 0)
        self.assertEqual(payload["results"][0]["title"], "Episode Two")

    def test_episodes_listing_respects_limit_offset(self):
        """limit/offset page through the catalog like the rest of the API."""
        response = self.call_api(
            "get",
            "api_podcast_show_episodes",
            args=(self.show.id,),
            params={"limit": 1, "offset": 1},
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, HTTP.OK)
        payload = response.json()
        self.assertEqual(payload["pagination"]["total"], 2)
        self.assertEqual(payload["pagination"]["limit"], 1)
        self.assertEqual(payload["pagination"]["offset"], 1)
        self.assertEqual(len(payload["results"]), 1)
        self.assertEqual(payload["results"][0]["title"], "Episode One")
        self.assertIsNone(payload["pagination"]["next"])
        self.assertIsNotNone(payload["pagination"]["previous"])


class EpisodePlayTests(PodcastApiTestCase):
    """POST podcasts/episodes/plays records plays."""

    def test_play_creates_podcast_row(self):
        """The first play creates a Completed Podcast row."""
        response = self.call_api(
            "post",
            "api_podcast_episode_play",
            payload={
                "show_id": self.show.id,
                "episode_id": self.episode1.id,
                "end_date": "2024-03-01T10:00:00",
            },
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, HTTP.CREATED)
        payload = response.json()
        self.assertEqual(payload["progress"], 30)
        self.assertEqual(payload["progress_unit"], "minutes")
        podcast = Podcast.objects.get(user=self.user1, episode=self.episode1)
        self.assertEqual(podcast.item.media_id, "ep-uuid-1")
        self.assertEqual(podcast.progress, 30)
        self.assertEqual(podcast.formatted_progress, "30m")

    def test_blank_date_uses_current_server_time(self):
        """A blank date records a completed play at the current server time."""
        completed_at = datetime.datetime(2025, 1, 15, 12, tzinfo=datetime.UTC)

        with patch(
            "app.fork_services_podcast.timezone.now",
            return_value=completed_at,
        ):
            response = self.call_api(
                "post",
                "api_podcast_episode_play",
                payload={
                    "show_id": self.show.id,
                    "episode_id": self.episode1.id,
                    "end_date": "",
                },
                headers=self.auth_headers,
            )

        self.assertEqual(response.status_code, HTTP.CREATED)
        podcast = Podcast.objects.get(user=self.user1, episode=self.episode1)
        self.assertEqual(podcast.status, Status.COMPLETED.value)
        self.assertEqual(podcast.end_date, completed_at)

    def test_blank_and_omitted_dates_share_duplicate_protection(self):
        """Repeated date-free plays create one completed activity entry."""
        completed_at = datetime.datetime(2025, 1, 15, 12, tzinfo=datetime.UTC)

        with patch(
            "app.fork_services_podcast.timezone.now",
            return_value=completed_at,
        ):
            first = self.call_api(
                "post",
                "api_podcast_episode_play",
                payload={
                    "show_id": self.show.id,
                    "episode_uuid": self.episode2.episode_uuid,
                    "end_date": "",
                },
                headers=self.auth_headers,
            )
            second = self.call_api(
                "post",
                "api_podcast_episode_play",
                payload={
                    "show_id": self.show.id,
                    "episode_uuid": self.episode2.episode_uuid,
                },
                headers=self.auth_headers,
            )

        self.assertEqual(first.status_code, HTTP.CREATED)
        self.assertEqual(second.status_code, HTTP.OK)
        self.assertTrue(second.json()["duplicate"])
        self.assertEqual(
            Podcast.objects.filter(user=self.user1, episode=self.episode2).count(),
            1,
        )

    def test_legacy_null_date_play_can_be_deleted(self):
        """A legacy completed row with no date does not crash on deletion."""
        response = self.call_api(
            "post",
            "api_podcast_episode_play",
            payload={
                "show_id": self.show.id,
                "episode_id": self.episode1.id,
                "end_date": "2024-03-01T10:00:00",
            },
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, HTTP.CREATED)

        podcast = Podcast.objects.get(user=self.user1, episode=self.episode1)
        Podcast.objects.filter(pk=podcast.pk).update(end_date=None)

        self.client.force_login(self.user1)
        deleted = self.client.post(
            reverse("media_delete"),
            {"instance_id": podcast.pk, "media_type": "podcast"},
            HTTP_REFERER="/",
        )

        self.assertEqual(deleted.status_code, HTTP.FOUND)
        self.assertFalse(Podcast.objects.filter(pk=podcast.pk).exists())

    def test_duplicate_play_within_window(self):
        """A second play within five minutes reports duplicate."""
        for _ in range(2):
            response = self.call_api(
                "post",
                "api_podcast_episode_play",
                payload={
                    "show_id": self.show.id,
                    "episode_uuid": "ep-uuid-2",
                    "end_date": "2024-03-01T10:00:00",
                },
                headers=self.auth_headers,
            )
        self.assertEqual(response.status_code, HTTP.OK)
        self.assertTrue(response.json()["duplicate"])

    def test_missing_identifiers_rejected(self):
        """A play without episode identifiers returns 400."""
        response = self.call_api(
            "post",
            "api_podcast_episode_play",
            payload={"show_id": self.show.id},
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, HTTP.BAD_REQUEST)


class MarkAllPlayedTests(PodcastApiTestCase):
    """POST podcasts/shows/{id}/mark-all-played."""

    @patch("app.fork_services_podcast.refresh_show_episodes_from_rss")
    def test_marks_unplayed_episodes(self, _mock_rss):
        """All catalog episodes without plays are marked completed."""
        response = self.call_api(
            "post",
            "api_podcast_mark_all_played",
            args=(self.show.id,),
            payload={},
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, HTTP.OK)
        self.assertEqual(response.json()["marked_played"], 2)
        self.assertEqual(
            Podcast.objects.filter(user=self.user1, show=self.show).count(),
            2,
        )

        again = self.call_api(
            "post",
            "api_podcast_mark_all_played",
            args=(self.show.id,),
            payload={},
            headers=self.auth_headers,
        )
        self.assertEqual(again.json()["marked_played"], 0)


class PodcastPlayDeduplicationTests(PodcastApiTestCase):
    """Duplicate suppression across imports, replays, and the window edge."""

    def _record(self, end_date=None):
        return fork_services_podcast.record_podcast_play(
            self.user1,
            self.show,
            episode=self.episode1,
            episode_uuid=self.episode1.episode_uuid,
            end_date=end_date,
        )

    def test_reimported_play_stays_a_duplicate_after_a_dateless_play(self):
        """A play recorded "now" must not unmask an already-imported old play.

        Duplicate detection once compared against the newest play only. One
        dateless play then sat at the top of the history and every re-imported
        old play looked new, so a repeated import multiplied the play count.
        """
        old = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
        now = datetime.datetime(2025, 6, 1, 12, tzinfo=datetime.UTC)

        self._record(end_date=old)
        with patch("app.fork_services_podcast.timezone.now", return_value=now):
            self._record()
        _, duplicate = self._record(end_date=old)

        podcast = Podcast.objects.get(user=self.user1, episode=self.episode1)
        self.assertTrue(duplicate)
        self.assertEqual(podcast.completed_play_count, 2)

    def test_play_just_outside_the_window_is_a_new_play(self):
        """A play five minutes and one second later counts separately."""
        first = datetime.datetime(2025, 6, 1, 12, 0, 0, tzinfo=datetime.UTC)
        outside = first + datetime.timedelta(seconds=301)

        self._record(end_date=first)
        _, duplicate = self._record(end_date=outside)

        podcast = Podcast.objects.get(user=self.user1, episode=self.episode1)
        self.assertFalse(duplicate)
        self.assertEqual(podcast.completed_play_count, 2)

    def test_play_just_inside_the_window_is_a_duplicate(self):
        """A play four minutes and fifty-nine seconds later is suppressed."""
        first = datetime.datetime(2025, 6, 1, 12, 0, 0, tzinfo=datetime.UTC)
        inside = first + datetime.timedelta(seconds=299)

        self._record(end_date=first)
        _, duplicate = self._record(end_date=inside)

        podcast = Podcast.objects.get(user=self.user1, episode=self.episode1)
        self.assertTrue(duplicate)
        self.assertEqual(podcast.completed_play_count, 1)


class PodcastWebPlayDateTests(PodcastApiTestCase):
    """The web form and the REST API must read a date the same way."""

    def _post(self, end_date):
        self.client.force_login(self.user1)
        return self.client.post(
            reverse("podcast_save"),
            {
                "show_id": self.show.id,
                "episode_id": self.episode1.id,
                "episode_uuid": self.episode1.episode_uuid,
                "end_date": end_date,
            },
            HTTP_REFERER="/",
        )

    def test_blank_date_records_the_current_server_time(self):
        """A blank date on the web form behaves like a blank date on the API."""
        completed_at = datetime.datetime(2025, 1, 15, 12, tzinfo=datetime.UTC)

        with patch(
            "app.fork_services_podcast.timezone.now",
            return_value=completed_at,
        ):
            response = self._post("")

        podcast = Podcast.objects.get(user=self.user1, episode=self.episode1)
        self.assertEqual(response.status_code, HTTP.FOUND)
        self.assertEqual(podcast.status, Status.COMPLETED.value)
        self.assertEqual(podcast.end_date, completed_at)
        self.assertIsNotNone(podcast.updated_at)
        self.assertEqual(podcast.plays.count(), 1)

    def test_unreadable_date_records_nothing_and_reports_the_problem(self):
        """An unreadable date must not become "now" behind the user's back.

        The API answers 400 for the same input. The web form used to discard
        the value and record the current time under a success message, so the
        user kept a timestamp they never chose.
        """
        response = self._post("not-a-date")

        self.assertEqual(response.status_code, HTTP.FOUND)
        self.assertFalse(
            Podcast.objects.filter(user=self.user1, episode=self.episode1).exists(),
        )
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(any("YYYY-MM-DD" in message for message in messages))

    def test_unreadable_date_keeps_the_modal_open_for_htmx(self):
        """The modal must survive the error so the typed date can be fixed."""
        self.client.force_login(self.user1)
        response = self.client.post(
            reverse("podcast_save"),
            {
                "show_id": self.show.id,
                "episode_id": self.episode1.id,
                "episode_uuid": self.episode1.episode_uuid,
                "end_date": "not-a-date",
            },
            HTTP_REFERER="/",
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, HTTP.OK)
        self.assertEqual(response["HX-Reswap"], "none")
        self.assertNotIn("closeModal", response["HX-Trigger"])
        self.assertIn("showToast", response["HX-Trigger"])
        self.assertFalse(
            Podcast.objects.filter(user=self.user1, episode=self.episode1).exists(),
        )

    def test_explicit_date_is_kept_exactly(self):
        """An explicit date must survive unchanged."""
        response = self._post("2024-03-01T10:00:00")

        podcast = Podcast.objects.get(user=self.user1, episode=self.episode1)
        self.assertEqual(response.status_code, HTTP.FOUND)
        self.assertEqual(
            podcast.end_date,
            datetime.datetime(2024, 3, 1, 10, tzinfo=datetime.UTC),
        )

    def test_repeat_play_reports_that_nothing_was_added(self):
        """A suppressed play must not read as success on the web form.

        "Play already recorded" told the user the action worked while the play
        count stayed where it was.
        """
        completed_at = datetime.datetime(2025, 1, 15, 12, tzinfo=datetime.UTC)

        with patch(
            "app.fork_services_podcast.timezone.now",
            return_value=completed_at,
        ):
            self._post("")
            response = self._post("")

        podcast = Podcast.objects.get(user=self.user1, episode=self.episode1)
        self.assertEqual(podcast.completed_play_count, 1)
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(
            any("did not add a second play" in message for message in messages),
            messages,
        )


class EpisodeNotesApiTests(PodcastApiTestCase):
    """Private podcast episode notes write contract."""

    def test_notes_create_without_a_play(self):
        """Saving notes creates the Podcast row but no history record."""
        response = self.call_api(
            "patch",
            "api_podcast_episode_notes",
            args=(self.episode1.id,),
            payload={"notes": "Start here"},
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, HTTP.OK)
        self.assertEqual(response.json()["notes"], "Start here")
        podcast = Podcast.objects.get(user=self.user1, episode=self.episode1)
        self.assertEqual(podcast.notes, "Start here")
        self.assertFalse(podcast.history.exists())

    def test_blank_note_leaves_untracked_episode_alone(self):
        """A blank note does not create a tracker row."""
        response = self.call_api(
            "patch",
            "api_podcast_episode_notes",
            args=(self.episode1.id,),
            payload={"notes": ""},
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, HTTP.OK)
        self.assertFalse(
            Podcast.objects.filter(user=self.user1, episode=self.episode1).exists(),
        )
