"""Sonarr importer for TV collection ownership sync."""

import logging
from collections import defaultdict
from functools import partial

import requests
from django.conf import settings
from django.utils import timezone

from app.models import Item, MediaTypes, Sources
from app.providers import services
from integrations import connection_health, import_progress
from integrations.imports.helpers import (
    ConnectionAuthError,
    MediaImportError,
    decrypt_or_raise,
    find_item_across_buckets,
    retry_on_lock,
)
from integrations.models import CollectionSourceState, SonarrInstance
from integrations.safe_fetch import send_to_self_hosted
from integrations.source_sync import (
    prune_collection_source_states,
    remove_collection_source_state,
    sync_collection_source_state,
)

logger = logging.getLogger(__name__)
HTTP_STATUS_BAD_REQUEST = 400
HTTP_STATUS_NOT_FOUND = 404


class SonarrClient:
    """Thin API client for Sonarr v3."""

    def __init__(self, base_url: str, api_key: str):
        """Store Sonarr connection settings."""
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _request(self, path: str, params=None, *, method="GET", json=None, timeout=20):
        try:
            response = send_to_self_hosted(
                partial(requests.request, method) if method != "GET" else requests.get,
                f"{self.base_url}{path}",
                headers={"X-Api-Key": self.api_key},
                params=params,
                timeout=timeout,
                **({"json": json} if json is not None else {}),
            )
        except requests.RequestException as error:
            msg = f"Could not reach Sonarr: {error}"
            raise MediaImportError(msg) from error
        if response.status_code in (401, 403):
            msg = "Sonarr API key is invalid or unauthorized"
            raise ConnectionAuthError(msg)
        if response.status_code >= HTTP_STATUS_BAD_REQUEST:
            msg = f"Sonarr request failed ({response.status_code}) for {path}"
            raise MediaImportError(msg)
        try:
            return response.json()
        except ValueError as error:
            msg = "Sonarr returned a response that is not JSON"
            raise MediaImportError(msg) from error

    def healthcheck(self):
        """Verify connection."""
        return self._request("/api/v3/system/status")

    def series(self):
        """Fetch tracked series rows."""
        return self._request("/api/v3/series")

    def episodes(self, series_id, timeout=20):
        """Fetch all episodes for a Sonarr series, with their file details."""
        # Sonarr only embeds `episodeFile` (quality, path, size) on request.
        return self._request(
            "/api/v3/episode",
            params={"seriesId": series_id, "includeEpisodeFile": "true"},
            timeout=timeout,
        )

    def queue(self, series_id, timeout=8):
        """Return the queue rows for one series."""
        return self._request(
            "/api/v3/queue/details", {"seriesId": series_id}, timeout=timeout
        )

    def history(self, series_id, season_number=None, timeout=8):
        """Return the history rows for a series, or one season of it."""
        params = {"seriesId": series_id, "includeEpisode": "true"}
        if season_number is not None:
            params["seasonNumber"] = season_number
        return self._request("/api/v3/history/series", params, timeout=timeout)

    def search(self, command, timeout=8):
        """Ask Sonarr to run a search command (EpisodeSearch, SeasonSearch...)."""
        return self._request(
            "/api/v3/command", method="POST", json=command, timeout=timeout
        )


def importer(identifier, user, mode, instance_id=None):
    """Import Sonarr collection ownership."""
    instance = (
        SonarrInstance.objects.get(pk=instance_id, user=user) if instance_id else None
    )
    return SonarrImporter(user, instance=instance).import_data()


class SonarrImporter:
    """Import collection data from Sonarr."""

    def __init__(self, user, instance=None):
        """Bind the importer to a user with a connected Sonarr instance."""
        self.user = user
        if instance is not None:
            self.instance = instance
        else:
            self.instance = user.sonarr_instances.first()
        if self.instance is None:
            msg = "Connect Sonarr before importing"
            raise MediaImportError(msg)

        try:
            api_key = decrypt_or_raise(self.instance.api_key)
        except MediaImportError as error:
            # An unreadable stored key needs a reconnect as much as a rejected one.
            connection_health.record_failure(self.instance, error, auth=True)
            raise

        self.client = SonarrClient(self.instance.base_url, api_key)
        self.warnings = []

    def import_data(self):
        """Sync Sonarr ownership into Floppy collection rows."""
        imported_counts = defaultdict(int)
        owned_item_ids = set()
        complete = True

        try:
            series_rows = self.client.series()
        except MediaImportError as error:
            connection_health.record_failure(
                self.instance,
                error,
                auth=isinstance(error, ConnectionAuthError),
            )
            raise

        total = len(series_rows)
        for i, row in enumerate(series_rows, start=1):
            import_progress.report(i, total, "Sonarr")
            stats = row.get("statistics") or {}
            episode_file_count = int(stats.get("episodeFileCount") or 0)
            if episode_file_count <= 0:
                item = self._find_series_item(row)
                if item:
                    self._prune_series_collection_state(item)
                continue

            item = self._resolve_series_item(row)
            if not item:
                imported_counts["skipped_missing_ids"] += 1
                continue

            self._series_changed = False
            synced_item_ids = self._sync_series_episode_collection(item, row)
            if synced_item_ids is None:
                # A failed episode fetch must not make the whole series look
                # deleted, so skip the reconciliation for this run.
                complete = False
                continue
            owned_item_ids.update(synced_item_ids)
            # A series Sonarr reports exactly as before is not an import:
            # counting it would start a calendar reload and a Statistics
            # rebuild every scheduled run.
            if self._series_changed:
                imported_counts["collection"] += 1
                imported_counts["updated"] += 1
            else:
                imported_counts["unchanged"] += 1

        if complete:
            removed = prune_collection_source_states(
                user=self.user,
                source="sonarr",
                source_instance_id=self.instance.pk,
                keep_item_ids=owned_item_ids,
            )
            if removed:
                imported_counts["removed"] += removed
        self.instance.last_sync_at = timezone.now()
        connection_health.record_success(self.instance, extra_fields=["last_sync_at"])

        return dict(imported_counts), "\n".join(dict.fromkeys(self.warnings))

    def _find_series_item(self, row):
        """Find an existing Floppy show item without creating one."""
        for source, media_id in self._series_ids(row):
            item = find_item_across_buckets(
                media_id=media_id,
                source=source,
                media_type=MediaTypes.TV.value,
            )
            if item:
                return item
        return None

    def _series_ids(self, row):
        """Return (source, id) pairs for a series, the user's provider first.

        Sonarr indexes by TVDB, so a user who prefers TheTVDB must not get a
        second TMDB copy of a show they already track there (#1911).
        """
        pairs = []
        if row.get("tvdbId"):
            pairs.append((Sources.TVDB.value, str(row["tvdbId"])))
        if row.get("tmdbId"):
            pairs.append((Sources.TMDB.value, str(row["tmdbId"])))
        if self._preferred_source(row) != Sources.TVDB.value:
            pairs.reverse()
        return pairs

    def _preferred_source(self, row):
        """Return the metadata source the user wants for TV shows."""
        return self.user.tv_metadata_source_default

    def _resolve_series_item(self, row):
        pairs = self._series_ids(row)
        existing = self._find_series_item(row)
        if existing:
            return existing
        if not pairs:
            return None

        # Nothing tracked yet: create it on the user's preferred provider.
        source, media_id = pairs[0]
        if source == Sources.TVDB.value and self._preferred_source(row) != source:
            # No TMDB id and the user does not use TheTVDB: nothing to create.
            return None
        try:
            metadata = services.get_media_metadata(
                MediaTypes.TV.value,
                media_id,
                source,
            )
        except services.ProviderAPIError as error:
            if getattr(error, "status_code", None) == HTTP_STATUS_NOT_FOUND:
                title = row.get("title") or row.get("sortTitle") or media_id
                self.warnings.append(
                    f"{title}: not found in {Sources(source).label} "
                    f"with ID {media_id}.",
                )
                return None
            raise

        defaults = {
            "title": metadata["title"],
            "image": metadata.get("image") or settings.IMG_NONE,
            "release_datetime": metadata.get("release_datetime"),
            "genres": metadata.get("genres") or [],
            "original_title": metadata.get("original_title") or metadata["title"],
            "localized_title": metadata.get("localized_title") or metadata["title"],
        }
        item, _ = retry_on_lock(
            lambda: Item.objects.update_or_create(
                media_id=media_id,
                source=source,
                media_type=MediaTypes.TV.value,
                defaults=defaults,
            ),
        )
        return item

    def _sync_series_episode_collection(self, show_item, row):
        """Own the series' episodes, returning the item ids now kept.

        Returns None when the episode list could not be read, so the caller
        can skip reconciliation instead of treating the series as deleted.
        """
        series_id = row.get("id")
        if series_id in (None, ""):
            self._sync_state(show_item, "", row)
            return {show_item.id}

        try:
            episode_rows = self.client.episodes(series_id)
        except MediaImportError as error:
            title = row.get("title") or row.get("sortTitle") or show_item.title
            self.warnings.append(f"{title}: failed to fetch Sonarr episodes ({error}).")
            return None

        self._ensure_season_items(show_item, row, episode_rows)

        collected_episode_item_ids = set()
        for episode_row in episode_rows:
            episode_item = self._ensure_episode_item(show_item, episode_row)
            if not episode_item:
                continue

            if not self._episode_has_file(episode_row):
                continue

            self._sync_state(
                episode_item,
                self._extract_episode_quality_label(episode_row),
                episode_row,
            )
            collected_episode_item_ids.add(episode_item.id)

        self._prune_series_collection_state(
            show_item,
            keep_episode_item_ids=collected_episode_item_ids,
        )
        if not collected_episode_item_ids:
            return collected_episode_item_ids

        # The show gets its own entry so it lists under TV Shows, as Plex does.
        self._sync_state(show_item, "", row)
        return collected_episode_item_ids | {show_item.id}

    def _sync_state(self, item, quality_label, row):
        """Write one Sonarr copy, noting whether anything changed."""
        _entry, changed = sync_collection_source_state(
            user=self.user,
            item=item,
            source="sonarr",
            source_instance_id=self.instance.pk,
            quality_label=quality_label,
            # None when Sonarr sent no timestamp, so the stored one stays.
            source_updated_at=self._parse_source_timestamp(row, fallback_now=False),
        )
        self._series_changed = self._series_changed or changed

    def _child_bucket(self, show_item, default_bucket):
        """Return the library bucket a show's season/episode rows belong in.

        Mirrors Season.get_episode_item: children follow the show's grouping
        bucket (grouped anime lives on TV rows) and otherwise fall back to
        their own media type, never inheriting a container's 'tv' bucket.
        """
        show_bucket = show_item.library_media_type
        if show_bucket and show_bucket != MediaTypes.TV.value:
            return show_bucket
        return default_bucket

    def _ensure_season_items(self, show_item, row, episode_rows):
        """Ensure season items exist so collection stats can map episodes correctly."""
        season_numbers = {
            season.get("seasonNumber")
            for season in row.get("seasons") or []
            if season.get("seasonNumber") is not None
        }
        season_numbers.update(
            episode.get("seasonNumber")
            for episode in episode_rows
            if episode.get("seasonNumber") is not None
        )

        season_bucket = self._child_bucket(show_item, MediaTypes.SEASON.value)

        for season_number in season_numbers:
            season_title = (
                "Specials"
                if season_number == 0
                else f"{show_item.title} Season {season_number}"
            )
            seeded_at = timezone.now()

            def _get_or_create_season_item(
                season_number=season_number,
                season_title=season_title,
                seeded_at=seeded_at,
            ):
                season_item = find_item_across_buckets(
                    preferred_bucket=season_bucket,
                    media_id=show_item.media_id,
                    source=show_item.source,
                    media_type=MediaTypes.SEASON.value,
                    season_number=season_number,
                    episode_number=None,
                )
                if season_item is None:
                    season_item, _created = Item.objects.get_or_create(
                        media_id=show_item.media_id,
                        source=show_item.source,
                        media_type=MediaTypes.SEASON.value,
                        library_media_type=season_bucket,
                        season_number=season_number,
                        episode_number=None,
                        defaults={
                            "title": season_title,
                            "original_title": season_title,
                            "localized_title": season_title,
                            "image": show_item.image or settings.IMG_NONE,
                            "release_datetime": show_item.release_datetime,
                            "genres": show_item.genres or [],
                            "metadata_fetched_at": seeded_at,
                        },
                    )
                update_fields = []
                if season_item.metadata_fetched_at is None:
                    season_item.metadata_fetched_at = seeded_at
                    update_fields.append("metadata_fetched_at")
                if (
                    (not season_item.image or season_item.image == settings.IMG_NONE)
                    and show_item.image
                    and show_item.image != settings.IMG_NONE
                ):
                    season_item.image = show_item.image
                    update_fields.append("image")
                if not season_item.release_datetime and show_item.release_datetime:
                    season_item.release_datetime = show_item.release_datetime
                    update_fields.append("release_datetime")
                if not season_item.genres and show_item.genres:
                    season_item.genres = show_item.genres
                    update_fields.append("genres")
                if update_fields:
                    season_item.save(update_fields=update_fields)
                return season_item

            retry_on_lock(
                _get_or_create_season_item,
            )

    def _ensure_episode_item(self, show_item, row):
        """Create the episode item if it does not exist already."""
        season_number = row.get("seasonNumber")
        episode_number = row.get("episodeNumber")
        if season_number is None or episode_number is None:
            return None
        episode_title = row.get("title") or f"Episode {episode_number}"
        release_datetime = self._parse_episode_release_datetime(row)
        seeded_at = timezone.now()
        episode_bucket = self._child_bucket(show_item, MediaTypes.EPISODE.value)

        def _get_or_create_episode_item():
            episode_item = find_item_across_buckets(
                preferred_bucket=episode_bucket,
                media_id=show_item.media_id,
                source=show_item.source,
                media_type=MediaTypes.EPISODE.value,
                season_number=season_number,
                episode_number=episode_number,
            )
            if episode_item is None:
                episode_item, _created = Item.objects.get_or_create(
                    media_id=show_item.media_id,
                    source=show_item.source,
                    media_type=MediaTypes.EPISODE.value,
                    library_media_type=episode_bucket,
                    season_number=season_number,
                    episode_number=episode_number,
                    defaults={
                        "title": episode_title,
                        "original_title": episode_title,
                        "localized_title": episode_title,
                        "image": show_item.image or settings.IMG_NONE,
                        "release_datetime": release_datetime,
                        "genres": show_item.genres or [],
                        "metadata_fetched_at": seeded_at,
                    },
                )
            update_fields = []
            if episode_item.metadata_fetched_at is None:
                episode_item.metadata_fetched_at = seeded_at
                update_fields.append("metadata_fetched_at")
            if not episode_item.title and episode_title:
                episode_item.title = episode_title
                update_fields.append("title")
            if not episode_item.original_title and episode_title:
                episode_item.original_title = episode_title
                update_fields.append("original_title")
            if not episode_item.localized_title and episode_title:
                episode_item.localized_title = episode_title
                update_fields.append("localized_title")
            if (
                (not episode_item.image or episode_item.image == settings.IMG_NONE)
                and show_item.image
                and show_item.image != settings.IMG_NONE
            ):
                episode_item.image = show_item.image
                update_fields.append("image")
            if not episode_item.release_datetime and release_datetime:
                episode_item.release_datetime = release_datetime
                update_fields.append("release_datetime")
            if not episode_item.genres and show_item.genres:
                episode_item.genres = show_item.genres
                update_fields.append("genres")
            if update_fields:
                episode_item.save(update_fields=update_fields)
            return episode_item

        return retry_on_lock(_get_or_create_episode_item)

    def _episode_has_file(self, row):
        """Return True when Sonarr reports a local file for the episode."""
        if row.get("hasFile") is True:
            return True
        return int(row.get("episodeFileId") or 0) > 0

    def _extract_episode_quality_label(self, row):
        """Extract the episode file quality label when Sonarr includes it."""
        episode_file = row.get("episodeFile") or {}
        quality = episode_file.get("quality") or {}
        return (quality.get("quality") or {}).get("name") or quality.get("name") or ""

    def _prune_series_collection_state(self, show_item, *, keep_episode_item_ids=None):
        """Drop stale Sonarr collection ownership for a series.

        Returns how many copies were removed.
        """
        if keep_episode_item_ids is None:
            keep_episode_item_ids = set()

        stale_episode_item_ids = list(
            Item.objects.filter(
                media_id=show_item.media_id,
                source=show_item.source,
                media_type=MediaTypes.EPISODE.value,
            )
            .exclude(id__in=keep_episode_item_ids)
            .filter(
                source_states__user=self.user,
                source_states__source="sonarr",
                source_states__source_instance_id=self.instance.pk,
            )
            .values_list("id", flat=True)
            .distinct()
        )
        for episode_item_id in stale_episode_item_ids:
            episode_item = Item.objects.get(id=episode_item_id)
            remove_collection_source_state(
                user=self.user,
                item=episode_item,
                source="sonarr",
                source_instance_id=self.instance.pk,
            )
        removed = len(stale_episode_item_ids)

        if not keep_episode_item_ids:
            show_state = CollectionSourceState.objects.filter(
                user=self.user,
                item=show_item,
                source="sonarr",
                source_instance_id=self.instance.pk,
            )
            if show_state.exists():
                removed += 1
                remove_collection_source_state(
                    user=self.user,
                    item=show_item,
                    source="sonarr",
                    source_instance_id=self.instance.pk,
                )
        if removed:
            self._series_changed = True
        return removed

    def _parse_episode_release_datetime(self, row):
        return self._parse_source_timestamp(
            row,
            keys=("airDateUtc", "airDate"),
            default=None,
        )

    def _parse_source_timestamp(
        self,
        row,
        keys=("lastInfoSync", "added", "updated"),
        default=None,
        *,
        fallback_now=True,
    ):
        for key in keys:
            value = row.get(key)
            if not value:
                continue
            try:
                parsed = timezone.datetime.fromisoformat(
                    str(value).replace("Z", "+00:00"),
                )
            except ValueError:
                continue

            if timezone.is_naive(parsed):
                parsed = timezone.make_aware(
                    parsed,
                    timezone.get_default_timezone(),
                )
            return parsed
        if default is None and fallback_now:
            return timezone.now()
        return default
