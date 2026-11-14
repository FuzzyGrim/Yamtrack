"""Radarr importer for movie collection ownership sync."""

import logging
from collections import defaultdict
from functools import partial
from http import HTTPStatus

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
)
from integrations.models import RadarrInstance
from integrations.safe_fetch import send_to_self_hosted
from integrations.source_sync import (
    prune_collection_source_states,
    sync_collection_source_state,
)

logger = logging.getLogger(__name__)


class RadarrClient:
    """Thin API client for Radarr v3."""

    def __init__(self, base_url: str, api_key: str):
        """Store the extra keyword arguments this form needs."""
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
            msg = f"Could not reach Radarr: {error}"
            raise MediaImportError(msg) from error
        if response.status_code in (401, 403):
            msg = "Radarr API key is invalid or unauthorized"
            raise ConnectionAuthError(msg)
        if response.status_code >= HTTPStatus.BAD_REQUEST:
            msg = f"Radarr request failed ({response.status_code}) for {path}"
            raise MediaImportError(msg)
        try:
            return response.json()
        except ValueError as error:
            msg = "Radarr returned a response that is not JSON"
            raise MediaImportError(msg) from error

    def healthcheck(self):
        """Verify connection."""
        return self._request("/api/v3/system/status")

    def movies(self):
        """Fetch movie collection rows."""
        return self._request("/api/v3/movie")

    def movie_by_tmdb_id(self, tmdb_id, timeout=8):
        """Return the Radarr movie row for a TMDB id, or None."""
        rows = self._request("/api/v3/movie", {"tmdbId": tmdb_id}, timeout=timeout)
        return rows[0] if rows else None

    def queue(self, movie_id, timeout=8):
        """Return the queue rows for one movie."""
        return self._request(
            "/api/v3/queue/details", {"movieId": movie_id}, timeout=timeout
        )

    def history(self, movie_id, timeout=8):
        """Return the history rows for one movie."""
        return self._request(
            "/api/v3/history/movie", {"movieId": movie_id}, timeout=timeout
        )

    def search_movie(self, movie_id, timeout=8):
        """Ask Radarr to search for one movie."""
        return self._request(
            "/api/v3/command",
            method="POST",
            json={"name": "MoviesSearch", "movieIds": [movie_id]},
            timeout=timeout,
        )


def importer(identifier, user, mode, instance_id=None):
    """Import Radarr collection ownership."""
    instance = (
        RadarrInstance.objects.get(pk=instance_id, user=user) if instance_id else None
    )
    return RadarrImporter(user, instance=instance).import_data()


class RadarrImporter:
    """Import collection data from Radarr."""

    def __init__(self, user, instance=None):
        """Bind the importer to a user with a connected Radarr instance."""
        self.user = user
        if instance is not None:
            self.instance = instance
        else:
            self.instance = user.radarr_instances.first()
        if self.instance is None:
            msg = "Connect Radarr before importing"
            raise MediaImportError(msg)

        try:
            api_key = decrypt_or_raise(self.instance.api_key)
        except MediaImportError as error:
            # An unreadable stored key needs a reconnect as much as a rejected one.
            connection_health.record_failure(self.instance, error, auth=True)
            raise

        self.client = RadarrClient(self.instance.base_url, api_key)
        self.warnings = []

    def import_data(self):
        """Return the import data."""
        imported_counts = defaultdict(int)
        owned_item_ids = set()

        try:
            movies = self.client.movies()
        except MediaImportError as error:
            connection_health.record_failure(
                self.instance,
                error,
                auth=isinstance(error, ConnectionAuthError),
            )
            raise

        total = len(movies)
        for i, row in enumerate(movies, start=1):
            import_progress.report(i, total, "Radarr")
            if not row.get("hasFile"):
                continue

            item = self._resolve_movie_item(row)
            if not item:
                imported_counts["skipped_missing_ids"] += 1
                continue

            owned_item_ids.add(item.id)
            quality_label = (row.get("movieFile") or {}).get("quality", {}).get(
                "quality", {}
            ).get("name") or ""
            updated_at = self._parse_source_timestamp(row)
            _entry, changed = sync_collection_source_state(
                user=self.user,
                item=item,
                source="radarr",
                source_instance_id=self.instance.pk,
                quality_label=quality_label,
                source_updated_at=updated_at,
            )
            # An unchanged copy is not an import: counting it would start a
            # calendar reload and a Statistics rebuild every scheduled run.
            if changed:
                imported_counts["collection"] += 1
                imported_counts["updated"] += 1
            else:
                imported_counts["unchanged"] += 1

        # The full movie list was read, so anything missing is no longer owned.
        removed = prune_collection_source_states(
            user=self.user,
            source="radarr",
            source_instance_id=self.instance.pk,
            keep_item_ids=owned_item_ids,
        )
        if removed:
            imported_counts["removed"] += removed
        self.instance.last_sync_at = timezone.now()
        connection_health.record_success(self.instance, extra_fields=["last_sync_at"])

        return dict(imported_counts), "\n".join(dict.fromkeys(self.warnings))

    def _resolve_movie_item(self, row):
        tmdb_id = row.get("tmdbId")
        imdb_id = row.get("imdbId")

        if tmdb_id:
            existing = find_item_across_buckets(
                media_id=str(tmdb_id),
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
            )
            if existing:
                return existing

            try:
                metadata = services.get_media_metadata(
                    MediaTypes.MOVIE.value,
                    str(tmdb_id),
                    Sources.TMDB.value,
                )
            except services.ProviderAPIError as error:
                if getattr(error, "status_code", None) == HTTPStatus.NOT_FOUND:
                    title = row.get("title") or row.get("sortTitle") or tmdb_id
                    self.warnings.append(
                        f"{title}: not found in {Sources.TMDB.label} with ID {tmdb_id}.",
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
            item, _ = Item.objects.update_or_create(
                media_id=str(tmdb_id),
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                defaults=defaults,
            )
            return item

        if imdb_id:
            return find_item_across_buckets(
                media_id=str(imdb_id),
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
            )

        return None

    def _parse_source_timestamp(self, row):
        for key in ("movieFile", "added", "lastInfoSync", "updated"):
            value = row.get(key)
            if isinstance(value, dict):
                value = value.get("dateAdded") or value.get("date")
            if not value:
                continue
            try:
                return timezone.datetime.fromisoformat(
                    str(value).replace("Z", "+00:00")
                )
            except ValueError:
                continue
        # None: Radarr sent no timestamp, so the stored one stays.
        return None
