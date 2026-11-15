from datetime import UTC, datetime
from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.db.utils import OperationalError
from django.test import TestCase
from django.urls import reverse
from django_celery_beat.models import PeriodicTask

from app.helpers import get_tv_show_collection_stats
from app.models import CollectionEntry, Item, MediaTypes, Sources
from integrations import tasks
from integrations.imports import helpers, radarr, sonarr
from integrations.models import CollectionSourceState, RadarrInstance, SonarrInstance
from integrations.source_sync import (
    prune_collection_source_states,
    remove_collection_source_state,
    upsert_collection_source_state,
)


class ArrImportViewTests(TestCase):
    """Cover Radarr/Sonarr connect flows and recurring schedules."""

    def setUp(self):
        """Create an authenticated user for ARR view requests."""
        self.user = get_user_model().objects.create_user(
            username="arr-user",
        )
        self.client.force_login(self.user)

    @patch("integrations.views.tasks.import_radarr.delay")
    @patch("integrations.views.RadarrClient.healthcheck")
    def test_radarr_connect_creates_schedule_and_queues_initial_import(
        self,
        mock_healthcheck,
        mock_delay,
    ):
        """Connecting Radarr should persist credentials and enable recurring sync."""
        response = self.client.post(
            reverse("radarr_connect"),
            {
                "base_url": "https://radarr.local:7878",
                "api_key": "radarr-key",
            },
        )

        self.assertEqual(response.status_code, 302)
        instance = RadarrInstance.objects.get(user=self.user)
        self.assertEqual(instance.base_url, "https://radarr.local:7878")
        task = PeriodicTask.objects.get(task="Import from Radarr (Recurring)")
        self.assertTrue(task.enabled)
        self.assertEqual(
            task.name,
            f"Import from Radarr (Radarr #{instance.id}) for "
            f"{self.user.username} (every 2 hours)",
        )
        self.assertIn(f'"instance_id": {instance.id}', task.kwargs)
        mock_healthcheck.assert_called_once()
        mock_delay.assert_called_once_with(
            user_id=self.user.id, mode="new", instance_id=instance.id
        )

    @patch("integrations.views.tasks.import_radarr.delay")
    @patch("integrations.views.RadarrClient.healthcheck")
    @patch("integrations.views.timezone.now")
    def test_radarr_connect_starts_recurring_sync_on_next_boundary(
        self,
        mock_now,
        mock_healthcheck,
        mock_delay,
    ):
        """Connecting Radarr should not trigger the recurring task immediately."""
        mock_now.return_value = datetime(2026, 4, 24, 21, 10, tzinfo=UTC)

        response = self.client.post(
            reverse("radarr_connect"),
            {
                "base_url": "https://radarr.local:7878",
                "api_key": "radarr-key",
            },
        )

        self.assertEqual(response.status_code, 302)
        instance = RadarrInstance.objects.get(user=self.user)
        task = PeriodicTask.objects.get(task="Import from Radarr (Recurring)")
        self.assertEqual(task.start_time, datetime(2026, 4, 24, 22, 0, tzinfo=UTC))
        mock_healthcheck.assert_called_once()
        mock_delay.assert_called_once_with(
            user_id=self.user.id, mode="new", instance_id=instance.id
        )

    @patch("integrations.views.tasks.import_sonarr.delay")
    @patch("integrations.views.SonarrClient.healthcheck")
    def test_sonarr_connect_creates_schedule_and_queues_initial_import(
        self,
        mock_healthcheck,
        mock_delay,
    ):
        """Connecting Sonarr should persist credentials and enable recurring sync."""
        response = self.client.post(
            reverse("sonarr_connect"),
            {
                "base_url": "https://sonarr.local:8989",
                "api_key": "sonarr-key",
            },
        )

        self.assertEqual(response.status_code, 302)
        instance = SonarrInstance.objects.get(user=self.user)
        self.assertEqual(instance.base_url, "https://sonarr.local:8989")
        task = PeriodicTask.objects.get(task="Import from Sonarr (Recurring)")
        self.assertTrue(task.enabled)
        self.assertEqual(
            task.name,
            f"Import from Sonarr (Sonarr #{instance.id}) for "
            f"{self.user.username} (every 2 hours)",
        )
        self.assertIn(f'"instance_id": {instance.id}', task.kwargs)
        mock_healthcheck.assert_called_once()
        mock_delay.assert_called_once_with(
            user_id=self.user.id, mode="new", instance_id=instance.id
        )


class ArrMultiInstanceTests(TestCase):
    """Cover connecting, disconnecting, and scoping multiple Radarr instances."""

    def setUp(self):
        """Create an authenticated user for multi-instance ARR requests."""
        self.user = get_user_model().objects.create_user(username="arr-multi-user")
        self.other_user = get_user_model().objects.create_user(username="arr-other-user")
        self.client.force_login(self.user)

    @patch("integrations.views.tasks.import_radarr.delay")
    @patch("integrations.views.RadarrClient.healthcheck")
    def test_connect_two_instances_with_different_urls(
        self, _mock_healthcheck, _mock_delay
    ):
        """A user can connect more than one Radarr instance."""
        self.client.post(
            reverse("radarr_connect"),
            {"base_url": "https://radarr-4k.local:7878", "api_key": "key-1", "name": "4K"},
        )
        self.client.post(
            reverse("radarr_connect"),
            {"base_url": "https://radarr-anime.local:7878", "api_key": "key-2", "name": "Anime"},
        )

        self.assertEqual(RadarrInstance.objects.filter(user=self.user).count(), 2)

    @patch("integrations.views.tasks.import_radarr.delay")
    @patch("integrations.views.RadarrClient.healthcheck")
    def test_connect_duplicate_url_is_rejected(self, _mock_healthcheck, _mock_delay):
        """Connecting the same base_url twice for one user is rejected."""
        self.client.post(
            reverse("radarr_connect"),
            {"base_url": "https://radarr.local:7878", "api_key": "key-1"},
        )
        response = self.client.post(
            reverse("radarr_connect"),
            {"base_url": "https://radarr.local:7878", "api_key": "key-2"},
            follow=True,
        )

        self.assertEqual(RadarrInstance.objects.filter(user=self.user).count(), 1)
        messages = [str(m) for m in response.context["messages"]]
        self.assertTrue(
            any("already have a Radarr instance" in m for m in messages)
        )

    @patch("integrations.views.tasks.import_radarr.delay")
    @patch("integrations.views.RadarrClient.healthcheck")
    def test_disconnect_one_instance_leaves_sibling_untouched(
        self, _mock_healthcheck, _mock_delay
    ):
        """Disconnecting one instance must not affect another instance's schedule/state."""
        self.client.post(
            reverse("radarr_connect"),
            {"base_url": "https://radarr-4k.local:7878", "api_key": "key-1"},
        )
        self.client.post(
            reverse("radarr_connect"),
            {"base_url": "https://radarr-anime.local:7878", "api_key": "key-2"},
        )
        first, second = RadarrInstance.objects.filter(user=self.user).order_by("id")
        item = Item.objects.create(
            media_id="1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Movie",
        )
        CollectionSourceState.objects.create(
            user=self.user, item=item, source="radarr", source_instance_id=first.id
        )
        CollectionSourceState.objects.create(
            user=self.user, item=item, source="radarr", source_instance_id=second.id
        )

        self.client.post(
            reverse("radarr_disconnect"), {"instance_id": first.id}
        )

        self.assertFalse(RadarrInstance.objects.filter(pk=first.id).exists())
        self.assertTrue(RadarrInstance.objects.filter(pk=second.id).exists())
        self.assertFalse(
            PeriodicTask.objects.filter(
                task="Import from Radarr (Recurring)",
                kwargs__contains=f'"instance_id": {first.id}',
            ).exists()
        )
        self.assertTrue(
            PeriodicTask.objects.filter(
                task="Import from Radarr (Recurring)",
                kwargs__contains=f'"instance_id": {second.id}',
            ).exists()
        )
        self.assertFalse(
            CollectionSourceState.objects.filter(
                user=self.user, source="radarr", source_instance_id=first.id
            ).exists()
        )
        self.assertTrue(
            CollectionSourceState.objects.filter(
                user=self.user, source="radarr", source_instance_id=second.id
            ).exists()
        )

    def test_disconnect_removes_copies_only_that_instance_created(self):
        """A synced-only copy goes; one another instance still backs stays."""
        only = RadarrInstance.objects.create(
            user=self.user, base_url="https://radarr-a.local", api_key=helpers.encrypt("k")
        )
        other = RadarrInstance.objects.create(
            user=self.user, base_url="https://radarr-b.local", api_key=helpers.encrypt("k")
        )
        lone, shared = (
            Item.objects.create(
                media_id=media_id,
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title=media_id,
            )
            for media_id in ("10", "11")
        )
        upsert_collection_source_state(
            user=self.user, item=lone, source="radarr", source_instance_id=only.id
        )
        for instance in (only, other):
            upsert_collection_source_state(
                user=self.user,
                item=shared,
                source="radarr",
                source_instance_id=instance.id,
            )

        self.client.post(reverse("radarr_disconnect"), {"instance_id": only.id})

        self.assertFalse(CollectionEntry.objects.filter(item=lone).exists())
        self.assertTrue(CollectionEntry.objects.filter(item=shared).exists())

    @patch("integrations.views.tasks.remove_collection_source_states.delay")
    def test_disconnect_queues_cleanup_instead_of_looping_in_request(self, mock_delay):
        """Row cleanup runs in a task so a big library cannot 504 (#1911)."""
        instance = SonarrInstance.objects.create(
            user=self.user, base_url="https://sonarr.local:8989", api_key="k"
        )

        response = self.client.post(
            reverse("sonarr_disconnect"), {"instance_id": instance.id}
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(SonarrInstance.objects.filter(pk=instance.pk).exists())
        mock_delay.assert_called_once_with(self.user.id, "sonarr", instance.id)

    def test_disconnect_scoped_to_owner(self):
        """A user cannot disconnect another user's Radarr instance."""
        other_instance = RadarrInstance.objects.create(
            user=self.other_user,
            base_url="https://radarr.local:7878",
            api_key=helpers.encrypt("key"),
        )

        response = self.client.post(
            reverse("radarr_disconnect"), {"instance_id": other_instance.id}
        )

        self.assertEqual(response.status_code, 404)
        self.assertTrue(RadarrInstance.objects.filter(pk=other_instance.id).exists())


class ArrImporterTests(TestCase):
    """Cover Radarr/Sonarr metadata resolution during imports."""

    def setUp(self):
        """Create a user with connected ARR accounts."""
        self.user = get_user_model().objects.create_user(username="arr-importer")
        self.radarr_instance = RadarrInstance.objects.create(
            user=self.user,
            base_url="https://radarr.local:7878",
            api_key=helpers.encrypt("radarr-key"),
        )
        self.sonarr_instance = SonarrInstance.objects.create(
            user=self.user,
            base_url="https://sonarr.local:8989",
            api_key=helpers.encrypt("sonarr-key"),
        )

    @patch(
        "integrations.imports.radarr.sync_collection_source_state",
        return_value=(None, True),
    )
    @patch("integrations.imports.radarr.services.get_media_metadata")
    @patch("integrations.imports.radarr.RadarrClient.movies")
    def test_radarr_import_uses_media_type_first_metadata_lookup(
        self,
        mock_movies,
        mock_get_media_metadata,
        _mock_sync_state,
    ):
        """Radarr imports should call metadata lookup with the standard signature."""
        mock_movies.return_value = [
            {
                "title": "Arrival",
                "hasFile": True,
                "tmdbId": 26718,
                "movieFile": {"quality": {"quality": {"name": "Bluray-1080p"}}},
            },
        ]
        mock_get_media_metadata.return_value = {
            "title": "Arrival",
            "image": "https://example.com/arrival.jpg",
            "genres": [],
        }

        imported_counts, warnings = radarr.importer(None, self.user, "new")

        self.assertEqual(imported_counts["collection"], 1)
        self.assertEqual(warnings, "")
        mock_get_media_metadata.assert_called_once_with(
            MediaTypes.MOVIE.value,
            "26718",
            Sources.TMDB.value,
        )

    @patch(
        "integrations.imports.sonarr.sync_collection_source_state",
        return_value=(None, True),
    )
    @patch("integrations.imports.sonarr.services.get_media_metadata")
    @patch("integrations.imports.sonarr.SonarrClient.series")
    def test_sonarr_import_uses_media_type_first_metadata_lookup(
        self,
        mock_series,
        mock_get_media_metadata,
        _mock_sync_state,
    ):
        """Sonarr imports should call metadata lookup with the standard signature."""
        mock_series.return_value = [
            {
                "title": "Severance",
                "statistics": {"episodeFileCount": 9},
                "tmdbId": 95396,
            },
        ]
        mock_get_media_metadata.return_value = {
            "title": "Severance",
            "image": "https://example.com/severance.jpg",
            "genres": [],
        }

        imported_counts, warnings = sonarr.importer(None, self.user, "new")

        self.assertEqual(imported_counts["collection"], 1)
        self.assertEqual(warnings, "")
        mock_get_media_metadata.assert_called_once_with(
            MediaTypes.TV.value,
            "95396",
            Sources.TMDB.value,
        )

    @patch("integrations.imports.sonarr.services.get_media_metadata")
    @patch("integrations.imports.sonarr.SonarrClient.episodes", return_value=[])
    @patch("integrations.imports.sonarr.SonarrClient.series")
    def test_tvdb_user_reuses_tracked_tvdb_show_not_tmdb(
        self, mock_series, _mock_episodes, mock_get_media_metadata
    ):
        """A TVDB-preferring user keeps one show: no TMDB item is created (#1911)."""
        self.user.tv_metadata_source_default = Sources.TVDB.value
        self.user.save()
        tracked = Item.objects.create(
            media_id="371980",
            source=Sources.TVDB.value,
            media_type=MediaTypes.TV.value,
            title="Severance",
            image="x",
        )
        mock_series.return_value = [
            {
                "title": "Severance",
                "statistics": {"episodeFileCount": 9},
                "tmdbId": 95396,
                "tvdbId": 371980,
            },
        ]

        sonarr.importer(None, self.user, "new")

        mock_get_media_metadata.assert_not_called()
        self.assertFalse(Item.objects.filter(source=Sources.TMDB.value).exists())
        self.assertTrue(
            CollectionSourceState.objects.filter(item=tracked, source="sonarr").exists()
        )

    @patch("integrations.imports.sonarr.services.get_media_metadata")
    @patch("integrations.imports.sonarr.SonarrClient.episodes", return_value=[])
    @patch("integrations.imports.sonarr.SonarrClient.series")
    def test_tvdb_user_creates_new_show_on_tvdb(
        self, mock_series, _mock_episodes, mock_get_media_metadata
    ):
        """An untracked show is created on TheTVDB for a TVDB-preferring user."""
        self.user.tv_metadata_source_default = Sources.TVDB.value
        self.user.save()
        mock_series.return_value = [
            {
                "title": "Severance",
                "statistics": {"episodeFileCount": 9},
                "tmdbId": 95396,
                "tvdbId": 371980,
            },
        ]
        mock_get_media_metadata.return_value = {"title": "Severance", "genres": []}

        sonarr.importer(None, self.user, "new")

        mock_get_media_metadata.assert_called_once_with(
            MediaTypes.TV.value, "371980", Sources.TVDB.value
        )
        self.assertFalse(Item.objects.filter(source=Sources.TMDB.value).exists())
        self.assertTrue(
            Item.objects.filter(
                source=Sources.TVDB.value, media_id="371980"
            ).exists()
        )

    @patch("integrations.imports.sonarr.services.get_media_metadata")
    @patch("integrations.imports.sonarr.SonarrClient.episodes")
    @patch("integrations.imports.sonarr.SonarrClient.series")
    def test_sonarr_import_syncs_episode_collection_and_keeps_show_entry(
        self,
        mock_series,
        mock_episodes,
        mock_get_media_metadata,
    ):
        """Sonarr should add episode rows and keep one show entry for TV Shows."""
        show_item = Item.objects.create(
            media_id="95396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Severance",
            image="https://example.com/severance.jpg",
        )
        CollectionEntry.objects.create(
            user=self.user,
            item=show_item,
            media_type="digital",
            audio_codec="AAC",
            audio_channels="5.1",
            bitrate=1653,
        )
        CollectionSourceState.objects.create(
            user=self.user,
            item=show_item,
            source="sonarr",
            source_instance_id=self.sonarr_instance.id,
        )

        mock_series.return_value = [
            {
                "id": 77,
                "title": "Severance",
                "statistics": {"episodeFileCount": 1},
                "tmdbId": 95396,
                "seasons": [{"seasonNumber": 1}],
            },
        ]
        mock_episodes.return_value = [
            {
                "seasonNumber": 1,
                "episodeNumber": 1,
                "title": "Good News About Hell",
                "airDateUtc": "2022-02-18T00:00:00Z",
                "episodeFileId": 11,
            },
            {
                "seasonNumber": 1,
                "episodeNumber": 2,
                "title": "Half Loop",
                "airDateUtc": "2022-02-25T00:00:00Z",
                "episodeFileId": 0,
            },
        ]
        mock_get_media_metadata.return_value = {
            "title": "Severance",
            "image": "https://example.com/severance.jpg",
            "genres": [],
        }

        imported_counts, warnings = sonarr.importer(None, self.user, "new")

        self.assertEqual(imported_counts["collection"], 1)
        self.assertEqual(warnings, "")
        self.assertTrue(
            CollectionSourceState.objects.filter(
                user=self.user,
                item=show_item,
                source="sonarr",
            ).exists(),
        )
        show_entries = CollectionEntry.objects.filter(
            user=self.user,
            item=show_item,
        )
        self.assertEqual(show_entries.count(), 1)
        self.assertEqual(show_entries.get().audio_codec, "AAC")

        season_item = Item.objects.get(
            media_id="95396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=1,
        )
        self.assertEqual(season_item.title, "Severance Season 1")
        self.assertIsNotNone(season_item.metadata_fetched_at)

        collected_episode = Item.objects.get(
            media_id="95396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=1,
        )
        uncollected_episode = Item.objects.get(
            media_id="95396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=2,
        )
        self.assertIsNotNone(collected_episode.metadata_fetched_at)
        self.assertIsNotNone(uncollected_episode.metadata_fetched_at)
        self.assertTrue(
            CollectionEntry.objects.filter(
                user=self.user,
                item=collected_episode,
            ).exists(),
        )
        self.assertFalse(
            CollectionEntry.objects.filter(
                user=self.user,
                item=uncollected_episode,
            ).exists(),
        )

        self.assertEqual(
            get_tv_show_collection_stats(
                self.user,
                show_item,
                metadata_episode_count=2,
            ),
            {
                "collected_seasons": 1,
                "total_seasons": 1,
                "collected_episodes": 1,
                "total_episodes": 2,
            },
        )

    @patch("integrations.imports.sonarr.services.get_media_metadata")
    @patch("integrations.imports.sonarr.SonarrClient.episodes")
    @patch("integrations.imports.sonarr.SonarrClient.series")
    def test_sonarr_import_adds_show_entry_once_and_drops_it_with_the_files(
        self,
        mock_series,
        mock_episodes,
        mock_get_media_metadata,
    ):
        """A series with files lists once as a show and leaves with its files."""
        mock_get_media_metadata.return_value = {
            "title": "Severance",
            "image": "https://example.com/severance.jpg",
            "genres": [],
        }
        mock_episodes.return_value = [
            {
                "seasonNumber": 1,
                "episodeNumber": 1,
                "title": "Good News About Hell",
                "airDateUtc": "2022-02-18T00:00:00Z",
                "episodeFileId": 11,
            },
        ]
        row = {
            "id": 77,
            "title": "Severance",
            "statistics": {"episodeFileCount": 1},
            "tmdbId": 95396,
            "seasons": [{"seasonNumber": 1}],
        }
        mock_series.return_value = [row]

        sonarr.importer(None, self.user, "new")
        sonarr.importer(None, self.user, "new")

        show_entries = CollectionEntry.objects.filter(
            user=self.user,
            item__media_type=MediaTypes.TV.value,
            item__media_id="95396",
        )
        self.assertEqual(show_entries.count(), 1)

        mock_series.return_value = [{**row, "statistics": {"episodeFileCount": 0}}]
        sonarr.importer(None, self.user, "new")

        self.assertFalse(show_entries.exists())
        self.assertFalse(
            CollectionEntry.objects.filter(
                user=self.user,
                item__media_type=MediaTypes.EPISODE.value,
            ).exists(),
        )

    @patch("integrations.imports.sonarr.services.get_media_metadata")
    @patch("integrations.imports.sonarr.SonarrClient.episodes")
    @patch("integrations.imports.sonarr.SonarrClient.series")
    def test_sonarr_import_reuses_episode_items_from_another_bucket(
        self,
        mock_series,
        mock_episodes,
        mock_get_media_metadata,
    ):
        """Sonarr should attach to the tracked episode row, not fork a new one."""
        show_item = Item.objects.create(
            media_id="95396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Severance",
            image="https://example.com/severance.jpg",
        )
        # Episodes auto-created for a tracked season land in the show's bucket
        # rather than the default 'episode' one.
        tracked_episode = Item.objects.create(
            media_id="95396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            library_media_type=MediaTypes.TV.value,
            season_number=1,
            episode_number=1,
            title="Good News About Hell",
            image="https://example.com/severance-s1e1.jpg",
        )

        mock_series.return_value = [
            {
                "id": 77,
                "title": "Severance",
                "statistics": {"episodeFileCount": 1},
                "tmdbId": 95396,
                "seasons": [{"seasonNumber": 1}],
            },
        ]
        mock_episodes.return_value = [
            {
                "seasonNumber": 1,
                "episodeNumber": 1,
                "title": "Good News About Hell",
                "airDateUtc": "2022-02-18T00:00:00Z",
                "episodeFileId": 11,
            },
        ]
        mock_get_media_metadata.return_value = {
            "title": "Severance",
            "image": "https://example.com/severance.jpg",
            "genres": [],
        }

        sonarr.importer(None, self.user, "new")

        episode_items = Item.objects.filter(
            media_id="95396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=1,
        )
        self.assertEqual([item.pk for item in episode_items], [tracked_episode.pk])
        self.assertTrue(
            CollectionSourceState.objects.filter(
                user=self.user,
                item=tracked_episode,
                source="sonarr",
            ).exists(),
        )
        self.assertEqual(
            get_tv_show_collection_stats(
                self.user,
                show_item,
                metadata_episode_count=1,
            )["collected_episodes"],
            1,
        )

    @patch("integrations.imports.sonarr.services.get_media_metadata")
    @patch("integrations.imports.sonarr.SonarrClient.episodes")
    @patch("integrations.imports.sonarr.SonarrClient.series")
    def test_sonarr_import_survives_existing_bucket_duplicates(
        self,
        mock_series,
        mock_episodes,
        mock_get_media_metadata,
    ):
        """A library already split across buckets must not crash the import."""
        Item.objects.create(
            media_id="95396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Severance",
            image="https://example.com/severance.jpg",
        )
        for bucket in (MediaTypes.TV.value, MediaTypes.EPISODE.value):
            Item.objects.create(
                media_id="95396",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                library_media_type=bucket,
                season_number=1,
                episode_number=1,
                title="Good News About Hell",
                image="https://example.com/severance-s1e1.jpg",
            )

        mock_series.return_value = [
            {
                "id": 77,
                "title": "Severance",
                "statistics": {"episodeFileCount": 1},
                "tmdbId": 95396,
                "seasons": [{"seasonNumber": 1}],
            },
        ]
        mock_episodes.return_value = [
            {
                "seasonNumber": 1,
                "episodeNumber": 1,
                "title": "Good News About Hell",
                "airDateUtc": "2022-02-18T00:00:00Z",
                "episodeFileId": 11,
            },
        ]
        mock_get_media_metadata.return_value = {
            "title": "Severance",
            "image": "https://example.com/severance.jpg",
            "genres": [],
        }

        _imported_counts, warnings = sonarr.importer(None, self.user, "new")

        self.assertEqual(warnings, "")
        self.assertEqual(
            Item.objects.filter(
                media_id="95396",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                season_number=1,
                episode_number=1,
            ).count(),
            2,
        )

    @patch("integrations.imports.sonarr.services.get_media_metadata")
    @patch("integrations.imports.sonarr.SonarrClient.episodes")
    @patch("integrations.imports.sonarr.SonarrClient.series")
    def test_sonarr_import_keeps_grouped_anime_in_its_bucket(
        self,
        mock_series,
        mock_episodes,
        mock_get_media_metadata,
    ):
        """Seasons and episodes of a grouped anime show stay on anime rows."""
        Item.objects.create(
            media_id="95396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            library_media_type=MediaTypes.ANIME.value,
            title="Severance",
            image="https://example.com/severance.jpg",
        )

        mock_series.return_value = [
            {
                "id": 77,
                "title": "Severance",
                "statistics": {"episodeFileCount": 1},
                "tmdbId": 95396,
                "seasons": [{"seasonNumber": 1}],
            },
        ]
        mock_episodes.return_value = [
            {
                "seasonNumber": 1,
                "episodeNumber": 1,
                "title": "Good News About Hell",
                "airDateUtc": "2022-02-18T00:00:00Z",
                "episodeFileId": 11,
            },
        ]
        mock_get_media_metadata.return_value = {
            "title": "Severance",
            "image": "https://example.com/severance.jpg",
            "genres": [],
        }

        sonarr.importer(None, self.user, "new")

        season_item = Item.objects.get(
            media_id="95396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=1,
        )
        episode_item = Item.objects.get(
            media_id="95396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=1,
        )
        self.assertEqual(season_item.library_media_type, MediaTypes.ANIME.value)
        self.assertEqual(episode_item.library_media_type, MediaTypes.ANIME.value)

    @patch("integrations.imports.sonarr.services.get_media_metadata")
    @patch("integrations.imports.sonarr.SonarrClient.episodes")
    @patch("integrations.imports.sonarr.SonarrClient.series")
    def test_sonarr_import_marks_existing_seeded_rows_as_fetched(
        self,
        mock_series,
        mock_episodes,
        mock_get_media_metadata,
    ):
        """Sonarr imports should stamp pre-existing season/episode rows as seeded."""
        Item.objects.create(
            media_id="95396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Severance",
            image="https://example.com/severance.jpg",
        )
        season_item = Item.objects.create(
            media_id="95396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=1,
            title="Severance Season 1",
            image="https://example.com/season1.jpg",
        )
        episode_item = Item.objects.create(
            media_id="95396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=1,
            title="Good News About Hell",
            image="https://example.com/episode1.jpg",
        )

        mock_series.return_value = [
            {
                "id": 77,
                "title": "Severance",
                "statistics": {"episodeFileCount": 1},
                "tmdbId": 95396,
                "seasons": [{"seasonNumber": 1}],
            },
        ]
        mock_episodes.return_value = [
            {
                "seasonNumber": 1,
                "episodeNumber": 1,
                "title": "Good News About Hell",
                "airDateUtc": "2022-02-18T00:00:00Z",
                "episodeFileId": 11,
            },
        ]
        mock_get_media_metadata.return_value = {
            "title": "Severance",
            "image": "https://example.com/severance.jpg",
            "genres": [],
        }

        sonarr.importer(None, self.user, "new")

        season_item.refresh_from_db()
        episode_item.refresh_from_db()

        self.assertIsNotNone(season_item.metadata_fetched_at)
        self.assertIsNotNone(episode_item.metadata_fetched_at)

    @patch("integrations.imports.sonarr.SonarrClient.series")
    def test_sonarr_import_removes_episode_collection_when_series_has_no_files(
        self,
        mock_series,
    ):
        """A Sonarr series with no files should clear stale episode ownership."""
        Item.objects.create(
            media_id="95396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Severance",
            image="https://example.com/severance.jpg",
        )
        episode_item = Item.objects.create(
            media_id="95396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=1,
            title="Good News About Hell",
            image="https://example.com/severance.jpg",
        )
        CollectionEntry.objects.create(user=self.user, item=episode_item)
        CollectionSourceState.objects.create(
            user=self.user,
            item=episode_item,
            source="sonarr",
            source_instance_id=self.sonarr_instance.id,
        )

        mock_series.return_value = [
            {
                "id": 77,
                "title": "Severance",
                "statistics": {"episodeFileCount": 0},
                "tmdbId": 95396,
            },
        ]

        imported_counts, warnings = sonarr.importer(None, self.user, "new")

        self.assertEqual(imported_counts, {})
        self.assertEqual(warnings, "")
        self.assertFalse(
            CollectionSourceState.objects.filter(
                user=self.user,
                item=episode_item,
                source="sonarr",
            ).exists(),
        )
        self.assertFalse(
            CollectionEntry.objects.filter(
                user=self.user,
                item=episode_item,
            ).exists(),
        )

    @patch("integrations.imports.radarr.RadarrClient.movies", return_value=[])
    def test_radarr_import_removes_movies_deleted_from_radarr(self, _mock_movies):
        """A movie no longer in Radarr loses its synced collection copy."""
        item = Item.objects.create(
            media_id="26718",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Arrival",
        )
        CollectionSourceState.objects.create(
            user=self.user,
            item=item,
            source="radarr",
            source_instance_id=self.radarr_instance.id,
        )
        CollectionEntry.objects.create(user=self.user, item=item)

        imported_counts, _warnings = radarr.importer(None, self.user, "new")

        self.assertEqual(imported_counts["removed"], 1)
        self.assertFalse(
            CollectionSourceState.objects.filter(
                user=self.user,
                item=item,
                source="radarr",
                source_instance_id=self.radarr_instance.id,
            ).exists()
        )
        self.assertFalse(CollectionEntry.objects.filter(user=self.user, item=item).exists())

    @patch("integrations.imports.radarr.RadarrClient.movies")
    def test_radarr_import_removes_movie_without_a_file(self, mock_movies):
        """A movie whose file Radarr deleted is no longer owned."""
        item = Item.objects.create(
            media_id="26718",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Arrival",
        )
        CollectionSourceState.objects.create(
            user=self.user,
            item=item,
            source="radarr",
            source_instance_id=self.radarr_instance.id,
        )
        mock_movies.return_value = [
            {"title": "Arrival", "hasFile": False, "tmdbId": 26718},
        ]

        imported_counts, _warnings = radarr.importer(None, self.user, "new")

        self.assertEqual(imported_counts["removed"], 1)
        self.assertFalse(
            CollectionSourceState.objects.filter(
                user=self.user,
                item=item,
                source="radarr",
            ).exists()
        )

    @patch("integrations.imports.sonarr.SonarrClient.series", return_value=[])
    def test_sonarr_import_removes_series_deleted_from_sonarr(self, _mock_series):
        """A series no longer in Sonarr loses its synced collection copies."""
        show_item = Item.objects.create(
            media_id="95396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Severance",
        )
        episode_item = Item.objects.create(
            media_id="95396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=1,
            title="Good News About Hell",
        )
        for item in (show_item, episode_item):
            CollectionSourceState.objects.create(
                user=self.user,
                item=item,
                source="sonarr",
                source_instance_id=self.sonarr_instance.id,
            )
            CollectionEntry.objects.create(user=self.user, item=item)

        imported_counts, _warnings = sonarr.importer(None, self.user, "new")

        self.assertEqual(imported_counts["removed"], 2)
        self.assertFalse(
            CollectionSourceState.objects.filter(
                user=self.user,
                source="sonarr",
                source_instance_id=self.sonarr_instance.id,
            ).exists()
        )
        self.assertFalse(
            CollectionEntry.objects.filter(
                user=self.user,
                item__in=[show_item, episode_item],
            ).exists()
        )

    @patch("integrations.imports.sonarr.SonarrClient.episodes")
    @patch("integrations.imports.sonarr.SonarrClient.series")
    def test_sonarr_import_keeps_copies_when_episode_fetch_fails(
        self,
        mock_series,
        mock_episodes,
    ):
        """A partial Sonarr run must not treat unread servers as deletions."""
        Item.objects.create(
            media_id="95396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Severance",
        )
        stale_item = Item.objects.create(
            media_id="1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Still owned elsewhere",
        )
        CollectionSourceState.objects.create(
            user=self.user,
            item=stale_item,
            source="sonarr",
            source_instance_id=self.sonarr_instance.id,
        )
        mock_series.return_value = [
            {
                "id": 77,
                "title": "Severance",
                "statistics": {"episodeFileCount": 1},
                "tmdbId": 95396,
            },
        ]
        mock_episodes.side_effect = helpers.MediaImportError("timed out")

        _counts, warnings = sonarr.importer(None, self.user, "new")

        self.assertIn("failed to fetch Sonarr episodes", warnings)
        self.assertTrue(
            CollectionSourceState.objects.filter(
                user=self.user,
                item=stale_item,
                source="sonarr",
                source_instance_id=self.sonarr_instance.id,
            ).exists()
        )

    @patch("integrations.imports.radarr.requests.get")
    def test_radarr_import_records_timeout_without_marking_broken(self, mock_get):
        """A Radarr timeout is recorded but says nothing about the API key."""
        mock_get.side_effect = requests.exceptions.ConnectTimeout("connect timed out")

        with self.assertRaises(helpers.MediaImportError) as cm:
            radarr.importer(None, self.user, "new")

        self.radarr_instance.refresh_from_db()
        self.assertFalse(self.radarr_instance.connection_broken)
        self.assertIn("Could not reach Radarr", str(cm.exception))
        self.assertIn("Could not reach Radarr", self.radarr_instance.last_error_message)

    @patch("integrations.imports.sonarr.requests.get")
    def test_sonarr_import_records_timeout_without_marking_broken(self, mock_get):
        """A Sonarr timeout is recorded but says nothing about the API key."""
        mock_get.side_effect = requests.exceptions.ConnectTimeout("connect timed out")

        with self.assertRaises(helpers.MediaImportError) as cm:
            sonarr.importer(None, self.user, "new")

        self.sonarr_instance.refresh_from_db()
        self.assertFalse(self.sonarr_instance.connection_broken)
        self.assertIn("Could not reach Sonarr", str(cm.exception))
        self.assertIn("Could not reach Sonarr", self.sonarr_instance.last_error_message)


    @patch("integrations.imports.radarr.requests.get")
    def test_radarr_import_marks_connection_broken_on_rejected_key(self, mock_get):
        """Only a rejected Radarr API key marks the instance broken."""
        mock_get.return_value.status_code = 401

        with self.assertRaises(helpers.ConnectionAuthError):
            radarr.importer(None, self.user, "new")

        self.radarr_instance.refresh_from_db()
        self.assertTrue(self.radarr_instance.connection_broken)


    @patch("integrations.imports.sonarr.requests.get")
    def test_sonarr_import_marks_connection_broken_on_rejected_key(self, mock_get):
        """Only a rejected Sonarr API key marks the instance broken."""
        mock_get.return_value.status_code = 401

        with self.assertRaises(helpers.ConnectionAuthError):
            sonarr.importer(None, self.user, "new")

        self.sonarr_instance.refresh_from_db()
        self.assertTrue(self.sonarr_instance.connection_broken)


class ArrImportTaskTests(TestCase):
    """Cover handled task failures for ARR imports."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="arr-task-user")

    @patch("integrations.tasks._media_imports.import_media")
    def test_import_radarr_task_returns_failure_message_for_expected_errors(
        self, mock_import_media
    ):
        mock_import_media.side_effect = helpers.MediaImportError(
            "Could not reach Radarr: connect timed out"
        )

        result = tasks.import_radarr(user_id=self.user.id)

        self.assertEqual(
            result, "Radarr import failed: Could not reach Radarr: connect timed out"
        )

    @patch("integrations.tasks._media_imports.import_media")
    def test_import_sonarr_task_returns_failure_message_for_expected_errors(
        self, mock_import_media
    ):
        mock_import_media.side_effect = helpers.MediaImportError(
            "Could not reach Sonarr: connect timed out"
        )

        result = tasks.import_sonarr(user_id=self.user.id)

        self.assertEqual(
            result, "Sonarr import failed: Could not reach Sonarr: connect timed out"
        )


class CollectionSourceSyncTests(TestCase):
    """Cover collection source sync durability helpers."""

    def setUp(self):
        """Create a user/item pair for source sync tests."""
        self.user = get_user_model().objects.create_user(username="source-sync-user")
        self.item = Item.objects.create(
            media_id="95396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=1,
            title="Good News About Hell",
            image="https://example.com/severance.jpg",
        )

    @patch("integrations.source_sync._reconcile_collection_entry", return_value="ok")
    @patch("integrations.source_sync.CollectionSourceState.objects.update_or_create")
    def test_upsert_collection_source_state_retries_sqlite_locks(
        self,
        mock_update_or_create,
        mock_reconcile,
    ):
        """Source sync should retry Sonarr state writes when SQLite is locked."""
        mock_update_or_create.side_effect = [
            OperationalError("database is locked"),
            (CollectionSourceState(), True),
        ]

        result = upsert_collection_source_state(
            user=self.user,
            item=self.item,
            source="sonarr",
            quality_label="WEBDL-1080p",
        )

        self.assertEqual(result, "ok")
        self.assertEqual(mock_update_or_create.call_count, 2)
        mock_reconcile.assert_called_once_with(user=self.user, item=self.item)

    def test_a_state_without_quality_neither_blanks_nor_outranks_a_label(self):
        """An empty label is unknown: an older Radarr label still wins and stays."""
        upsert_collection_source_state(
            user=self.user,
            item=self.item,
            source="radarr",
            quality_label="DVD",
            source_updated_at=datetime(2020, 1, 1, tzinfo=UTC),
        )
        upsert_collection_source_state(
            user=self.user,
            item=self.item,
            source="jellyfin",
        )

        entry = CollectionEntry.objects.get(user=self.user, item=self.item)
        self.assertEqual(entry.resolution, "DVD")

        remove_collection_source_state(
            user=self.user,
            item=self.item,
            source="radarr",
        )

        entry.refresh_from_db()
        self.assertEqual(entry.resolution, "DVD")

    def test_two_radarr_instances_own_independent_states(self):
        """Two Radarr instances reporting the same item get separate rows.

        The better-quality instance's label should win the resolved collection
        entry, and disconnecting one instance's state must not affect the
        other's.
        """
        first = RadarrInstance.objects.create(
            user=self.user,
            base_url="https://radarr-1.local:7878",
            api_key=helpers.encrypt("key-1"),
        )
        second = RadarrInstance.objects.create(
            user=self.user,
            base_url="https://radarr-2.local:7878",
            api_key=helpers.encrypt("key-2"),
        )

        upsert_collection_source_state(
            user=self.user,
            item=self.item,
            source="radarr",
            source_instance_id=first.id,
            quality_label="720p",
        )
        upsert_collection_source_state(
            user=self.user,
            item=self.item,
            source="radarr",
            source_instance_id=second.id,
            quality_label="2160p",
        )

        states = CollectionSourceState.objects.filter(
            user=self.user, item=self.item, source="radarr"
        )
        self.assertEqual(states.count(), 2)
        entry = CollectionEntry.objects.get(user=self.user, item=self.item)
        self.assertEqual(entry.resolution, "2160p")

        from integrations.source_sync import remove_collection_source_state

        remove_collection_source_state(
            user=self.user, item=self.item, source="radarr", source_instance_id=second.id
        )
        entry.refresh_from_db()
        self.assertEqual(entry.resolution, "720p")
        self.assertTrue(
            CollectionSourceState.objects.filter(
                user=self.user, item=self.item, source="radarr", source_instance_id=first.id
            ).exists()
        )

    def test_prune_is_scoped_to_the_calling_source_and_instance(self):
        """A Radarr run may only remove states Radarr itself reported."""
        radarr = RadarrInstance.objects.create(
            user=self.user,
            base_url="https://radarr.local:7878",
            api_key=helpers.encrypt("radarr-key"),
        )
        sibling = RadarrInstance.objects.create(
            user=self.user,
            base_url="https://radarr-2.local:7878",
            api_key=helpers.encrypt("radarr-key-2"),
        )
        for source, instance_id in (
            ("radarr", radarr.id),
            ("radarr", sibling.id),
            ("sonarr", None),
            ("jellyfin", None),
        ):
            CollectionSourceState.objects.create(
                user=self.user,
                item=self.item,
                source=source,
                source_instance_id=instance_id,
            )

        removed = prune_collection_source_states(
            user=self.user,
            source="radarr",
            source_instance_id=radarr.id,
            keep_item_ids=set(),
        )

        self.assertEqual(removed, 1)
        remaining = set(
            CollectionSourceState.objects.filter(
                user=self.user, item=self.item
            ).values_list("source", "source_instance_id")
        )
        self.assertEqual(
            remaining,
            {
                ("radarr", sibling.id),
                ("sonarr", None),
                ("jellyfin", None),
            },
        )

    def test_prune_keeps_a_manual_collection_entry(self):
        """Pruning a source snapshot must not erase the user's own copy row."""
        radarr = RadarrInstance.objects.create(
            user=self.user,
            base_url="https://radarr.local:7878",
            api_key=helpers.encrypt("radarr-key"),
        )
        CollectionSourceState.objects.create(
            user=self.user,
            item=self.item,
            source="radarr",
            source_instance_id=radarr.id,
        )
        CollectionEntry.objects.create(
            user=self.user,
            item=self.item,
            media_type="digital",
            audio_codec="AAC",
            resolution="1080p",
        )

        prune_collection_source_states(
            user=self.user,
            source="radarr",
            source_instance_id=radarr.id,
            keep_item_ids=set(),
        )

        self.assertFalse(
            CollectionSourceState.objects.filter(
                user=self.user, item=self.item, source="radarr"
            ).exists()
        )
        entry = CollectionEntry.objects.get(user=self.user, item=self.item)
        self.assertEqual(entry.media_type, "digital")
        self.assertEqual(entry.resolution, "1080p")


class SonarrSeriesTypeIsNotAnimeRoutingTests(TestCase):
    """Sonarr's `seriesType` must not decide Floppy's anime routing.

    It selects a release-parsing and episode-numbering mode for the downloader
    - users set it on absolutely-numbered Western cartoons and leave it
    `standard` on seasonally-numbered anime - so it says nothing about the
    title's identity and carries no MAL mapping. Routing on it would be the
    same class of heuristic error as routing on a Plex folder name.
    """

    def setUp(self):
        """Create a user with the Anime library enabled."""
        self.user = get_user_model().objects.create_user(
            username="sonarr-series-type",
            password="12345",
        )

    def test_series_type_anime_does_not_change_the_bucket(self):
        """A row flagged `anime` by Sonarr still resolves to a plain TV item."""
        importer = sonarr.SonarrImporter.__new__(sonarr.SonarrImporter)
        importer.user = self.user

        row = {
            "tmdbId": 1396,
            "tvdbId": 81189,
            "title": "Absolutely Numbered Cartoon",
            "seriesType": "anime",
        }

        self.assertIsNone(importer._find_series_item(row))

    def test_sonarr_never_reads_series_type(self):
        """Guard the decision itself: the field is deliberately unused."""
        from pathlib import Path

        source = Path(sonarr.__file__).read_text()
        self.assertNotIn("seriesType", source)
