"""Sync Plex Discover watchlist items into Floppy Planning entries."""

import logging
from collections import defaultdict
from copy import deepcopy
from datetime import timedelta

from django.db import IntegrityError
from django.utils import timezone

from app.mixins import disable_fetch_releases
from app.models import TV, Item, ItemTag, MediaTypes, Movie, Sources, Status
from app.providers import services, tmdb
from integrations import external_references
from integrations import plex as plex_api
from integrations.imports.helpers import MediaImportError
from integrations.models import PlexWatchlistSyncItem

logger = logging.getLogger(__name__)

WATCHLIST_SYNC_INTERVAL_MINUTES = 15
WATCHLIST_TASK_NAME = "Sync Plex Watchlist"
WATCHLIST_PAGE_SIZE = 100
# The sync runs every 15 minutes. Re-stamping when an item was last seen on
# every run rewrote each ledger row 96 times a day for no reader; it is now
# refreshed with any other change, or once it is this old.
LAST_SEEN_REFRESH = timedelta(days=1)


class PlexWatchlistSyncService:
    """Synchronize a user's Plex Discover watchlist into Floppy."""

    def __init__(self, user, account):
        """Store the extra keyword arguments this form needs."""
        self.user = user
        self.account = account
        self.counts = defaultdict(int)
        self.warnings: list[str] = []
        self.source_username = ""
        self.source_account_id = ""
        self.source_server_id = ""
        self.watchlist_tag = user.tags.filter(pk=user.watchlist_import_tag_id).first() if user.watchlist_import_tag_id else None

    def sync(self) -> tuple[dict[str, int], str]:
        """Run the watchlist sync and return counts plus warning text."""
        if not self.account or not self.account.plex_token:
            msg = "Plex is not connected for this user."
            raise MediaImportError(msg)

        self.counts = defaultdict(int)
        self.warnings = []
        (
            self.source_username,
            self.source_account_id,
            self.source_server_id,
        ) = self._ensure_source_identity()
        seen_item_ids: set[int] = set()

        with disable_fetch_releases():
            for entry in self._fetch_watchlist_entries():
                item_id = self._sync_entry(entry, seen_item_ids)
                if item_id is not None:
                    seen_item_ids.add(item_id)

            self._reconcile_removals(seen_item_ids)

        warning_text = "\n".join(dict.fromkeys(self.warnings))
        return dict(self.counts), warning_text

    def _ensure_source_identity(self) -> tuple[str, str, str]:
        """Return and persist the best-known connected Plex account identity."""
        username = (self.account.plex_username or "").strip()
        account_id = str(self.account.plex_account_id or "").strip()

        server_id = str(self.account.machine_identifier or "").strip()
        if username and account_id:
            return username, account_id, server_id

        try:
            account_data = plex_api.fetch_account(self.account.plex_token)
        except plex_api.PlexAuthError as exc:
            msg = "Plex token expired; reconnect and try again."
            raise MediaImportError(msg) from exc
        except plex_api.PlexClientError as exc:
            msg = f"Could not read Plex account details: {exc}"
            raise MediaImportError(msg) from exc

        username = username or (account_data.get("username") or "").strip()
        account_id = account_id or str(account_data.get("id") or "").strip()
        server_id = server_id or str(
            account_data.get("machineIdentifier")
            or account_data.get("machine_identifier")
            or "",
        ).strip()

        updated_fields = []
        if username and username != self.account.plex_username:
            self.account.plex_username = username
            updated_fields.append("plex_username")
        if account_id and str(self.account.plex_account_id or "").strip() != account_id:
            self.account.plex_account_id = account_id
            updated_fields.append("plex_account_id")
        if updated_fields:
            self.account.save(update_fields=updated_fields)

        return username, account_id, server_id

    def _fetch_watchlist_entries(self) -> list[dict]:
        """Fetch the full watchlist from Plex Discover."""
        entries: list[dict] = []
        start = 0

        while True:
            try:
                page_entries, total = plex_api.fetch_watchlist(
                    self.account.plex_token,
                    start=start,
                    size=WATCHLIST_PAGE_SIZE,
                )
            except plex_api.PlexAuthError as exc:
                msg = "Plex token expired; reconnect and try again."
                raise MediaImportError(msg) from exc
            except plex_api.PlexClientError as exc:
                msg = f"Could not fetch Plex watchlist: {exc}"
                raise MediaImportError(msg) from exc

            if not page_entries:
                break

            entries.extend(page_entries)
            start += len(page_entries)
            if start >= total or len(page_entries) < WATCHLIST_PAGE_SIZE:
                break

        return entries

    def _sync_entry(self, entry: dict, seen_item_ids: set[int]) -> int | None:
        """Sync a single watchlist entry into Floppy."""
        entry = self._hydrate_entry_with_external_ids(entry)
        raw_type = str(entry.get("type") or "").strip().lower()
        if raw_type == "movie":
            media_type = MediaTypes.MOVIE.value
            model_class = Movie
        elif raw_type in {"show", "series"}:
            media_type = MediaTypes.TV.value
            model_class = TV
        else:
            self.counts["skipped_unknown_type"] += 1
            return None

        guids = self._normalize_guid_list(entry.get("Guid") or entry.get("guid"))
        external_ids = plex_api.extract_external_ids_from_guids(guids)
        reference = external_references.lookup_plex_reference(
            self.user,
            self.account,
            entry,
            media_type,
            payload=entry,
        )
        if reference and reference.review_status == (
            external_references.ExternalReferenceReviewStatus.IGNORED.value
        ):
            self.counts["skipped_ignored"] += 1
            return None
        tmdb_id = self._resolve_tmdb_id(media_type, external_ids, entry, reference)
        if not tmdb_id:
            self.counts["skipped_missing_ids"] += 1
            self._warn(
                f"Skipped Plex watchlist entry without resolvable IDs: {entry.get('title') or 'Unknown title'}.",
            )
            return None

        metadata = self._fetch_tmdb_metadata(media_type, tmdb_id, entry)
        if not metadata:
            return None

        item = self._get_or_create_item(media_type, tmdb_id, metadata, entry)
        if item.id in seen_item_ids:
            return item.id

        _media_obj, created_media = self._get_or_create_user_media(model_class, item)
        if self.watchlist_tag:
            ItemTag.objects.get_or_create(tag=self.watchlist_tag, item=item)
        if created_media:
            self.counts["created"] += 1
            self.counts[media_type] += 1
        else:
            self.counts["linked_existing"] += 1

        self._upsert_sync_item(
            item=item,
            external_ids=external_ids,
            entry=entry,
            created_media=created_media,
        )
        identity = external_references.plex_identity(entry)
        if identity:
            external_references.save_observation(
                self.user,
                "plex",
                external_references.plex_source_account(
                    self.account,
                    machine_identifier=self.source_server_id,
                    payload=entry,
                ),
                *identity,
                media_type,
                matched_item=item,
                metadata={
                    "title": entry.get("title"),
                    "year": entry.get("year"),
                    "rating_key": entry.get("ratingKey") or entry.get("ratingkey"),
                    "guid": external_ids.get("plex_guid"),
                },
            )
        return item.id

    def _hydrate_entry_with_external_ids(self, entry: dict) -> dict:
        """Fetch item details when the list payload omits external GUIDs."""
        guids = self._normalize_guid_list(entry.get("Guid") or entry.get("guid"))
        external_ids = plex_api.extract_external_ids_from_guids(guids)
        if any(external_ids.get(key) for key in ("tmdb_id", "tvdb_id", "imdb_id")):
            return entry

        rating_key = str(entry.get("ratingKey") or entry.get("ratingkey") or "").strip()
        if not rating_key:
            return entry

        try:
            detail_entry = plex_api.fetch_watchlist_metadata(
                self.account.plex_token, rating_key
            )
        except plex_api.PlexAuthError as exc:
            msg = "Plex token expired; reconnect and try again."
            raise MediaImportError(msg) from exc
        except plex_api.PlexClientError as exc:
            self._warn(
                f"Could not load Plex watchlist metadata for {entry.get('title') or rating_key}: {exc}",
            )
            return entry

        merged_entry = deepcopy(entry)
        for key, value in detail_entry.items():
            if value not in (None, "", []):
                merged_entry[key] = value
        return merged_entry

    @staticmethod
    def _normalize_guid_list(guid_list) -> list[dict[str, str]]:
        """Normalize Plex GUID payloads into a list of dicts."""
        if not guid_list:
            return []
        if isinstance(guid_list, (dict, str)):
            guid_list = [guid_list]

        normalized = []
        for guid in guid_list:
            if isinstance(guid, dict):
                guid_value = guid.get("id") or guid.get("Id") or guid.get("guid")
            else:
                guid_value = guid
            if guid_value:
                normalized.append({"id": str(guid_value)})
        return normalized

    def _resolve_tmdb_id(
        self,
        media_type: str,
        external_ids: dict[str, str],
        entry: dict,
        reference=None,
    ) -> str | None:
        """Resolve the TMDB ID from Plex GUIDs or TMDB find lookups."""
        target = external_references.reference_target(reference)
        if target and target.media_type == media_type:
            return str(target.media_id)

        tmdb_id = external_ids.get("tmdb_id")
        if tmdb_id:
            return str(tmdb_id)

        title = entry.get("title") or "Unknown title"
        for source_key in ("tvdb_id", "imdb_id"):
            external_id = external_ids.get(source_key)
            if not external_id:
                continue
            try:
                find_results = tmdb.find(external_id, source_key)
            except services.ProviderAPIError as exc:
                self._warn(
                    f"TMDB find lookup failed for Plex watchlist entry {title}: {exc}",
                )
                continue

            if media_type == MediaTypes.MOVIE.value:
                candidates = find_results.get("movie_results") or []
            else:
                candidates = find_results.get("tv_results") or []
                if not candidates:
                    candidates = [
                        {"id": result.get("show_id")}
                        for result in find_results.get("tv_episode_results") or []
                        if result.get("show_id")
                    ]

            if not candidates:
                continue

            resolved_id = candidates[0].get("id") or candidates[0].get("media_id")
            if resolved_id:
                return str(resolved_id)

        return None

    def _fetch_tmdb_metadata(
        self, media_type: str, tmdb_id: str, entry: dict
    ) -> dict | None:
        """Fetch TMDB metadata for the resolved item."""
        try:
            return services.get_media_metadata(
                media_type,
                tmdb_id,
                Sources.TMDB.value,
            )
        except services.ProviderAPIError as exc:
            self._warn(
                f"Skipped Plex watchlist entry {entry.get('title') or tmdb_id}: {exc}",
            )
            self.counts["skipped_metadata"] += 1
            return None

    def _get_or_create_item(
        self,
        media_type: str,
        tmdb_id: str,
        metadata: dict,
        entry: dict,
    ) -> Item:
        """Get or create the shared TMDB item for a watchlist entry."""
        defaults = {
            **Item.title_fields_from_metadata(
                metadata,
                fallback_title=entry.get("title") or "",
            ),
            "image": metadata.get("image") or "",
        }
        item, created = Item.objects.get_or_create(
            media_id=str(tmdb_id),
            source=Sources.TMDB.value,
            media_type=media_type,
            defaults=defaults,
        )

        if created:
            return item

        updated_fields = []
        for field_name in ("title", "original_title", "localized_title", "image"):
            current_value = getattr(item, field_name)
            default_value = defaults.get(field_name)
            if (not current_value) and default_value:
                setattr(item, field_name, default_value)
                updated_fields.append(field_name)
        if updated_fields:
            item.save(update_fields=updated_fields)

        return item

    def _get_or_create_user_media(self, model_class, item: Item):
        """Return the user's tracked media row, creating a Planning row if needed."""
        media_obj = (
            model_class.objects.filter(
                user=self.user,
                item=item,
            )
            .order_by("created_at")
            .first()
        )
        if media_obj:
            return media_obj, False

        try:
            media_obj = model_class.objects.create(
                user=self.user,
                item=item,
                status=self.user.watchlist_import_status if self.user.watchlist_import_status in Status.values else Status.PLANNING.value,
                score=None,
                notes="",
            )
        except IntegrityError:
            media_obj = (
                model_class.objects.filter(
                    user=self.user,
                    item=item,
                )
                .order_by("created_at")
                .first()
            )
            if media_obj is None:  # pragma: no cover - defensive
                raise
            return media_obj, False

        return media_obj, True

    def _upsert_sync_item(
        self,
        item: Item,
        external_ids: dict[str, str],
        entry: dict,
        created_media: bool,
    ) -> None:
        """Create or update the ledger row for a watchlist item."""
        now = timezone.now()
        rating_key = str(entry.get("ratingKey") or entry.get("ratingkey") or "").strip()
        plex_guid = external_ids.get("plex_guid", "")

        sync_item, created = PlexWatchlistSyncItem.objects.get_or_create(
            user=self.user,
            item=item,
            source_username=self.source_username,
            source_server_id=self.source_server_id,
            defaults={
                "source_account_id": self.source_account_id,
                "source_server_id": self.source_server_id,
                "plex_rating_key": rating_key,
                "plex_guid": plex_guid,
                "tmdb_id": str(external_ids.get("tmdb_id") or ""),
                "tvdb_id": str(external_ids.get("tvdb_id") or ""),
                "imdb_id": str(external_ids.get("imdb_id") or ""),
                "created_by_sync": created_media,
                "is_active": True,
                "removed_at": None,
            },
        )

        if created:
            return

        updated_fields = []
        desired_values = {
            "source_account_id": self.source_account_id,
            "source_server_id": self.source_server_id,
            "plex_rating_key": rating_key,
            "plex_guid": plex_guid,
            "tmdb_id": str(external_ids.get("tmdb_id") or ""),
            "tvdb_id": str(external_ids.get("tvdb_id") or ""),
            "imdb_id": str(external_ids.get("imdb_id") or ""),
            "is_active": True,
            "removed_at": None,
        }
        for field_name, desired_value in desired_values.items():
            if getattr(sync_item, field_name) != desired_value:
                setattr(sync_item, field_name, desired_value)
                updated_fields.append(field_name)

        if created_media and not sync_item.created_by_sync:
            sync_item.created_by_sync = True
            updated_fields.append("created_by_sync")

        if updated_fields or (
            not sync_item.last_seen_at
            or now - sync_item.last_seen_at >= LAST_SEEN_REFRESH
        ):
            sync_item.last_seen_at = now
            sync_item.save(update_fields=[*updated_fields, "last_seen_at"])

    def _reconcile_removals(self, seen_item_ids: set[int]) -> None:
        """Deactivate ledger rows that disappeared from the current watchlist."""
        stale_items = (
            PlexWatchlistSyncItem.objects.filter(
                user=self.user,
                source_username=self.source_username,
                source_server_id=self.source_server_id,
                is_active=True,
            )
            .exclude(
                item_id__in=seen_item_ids,
            )
            .select_related("item")
        )

        now = timezone.now()
        for sync_item in stale_items:
            removed_media = False
            if sync_item.created_by_sync:
                media_obj = self._get_media_instance(sync_item.item)
                if media_obj and self._can_remove_synced_media(
                    sync_item.item, media_obj
                ):
                    media_obj.delete()
                    removed_media = True
                    self.counts["removed"] += 1

            if not removed_media:
                self.counts["deactivated"] += 1

            sync_item.is_active = False
            sync_item.removed_at = now
            sync_item.save(update_fields=["is_active", "removed_at"])

    def _get_media_instance(self, item: Item):
        """Return the tracked Movie/TV row for the given item, if any."""
        if item.media_type == MediaTypes.MOVIE.value:
            return (
                Movie.objects.filter(user=self.user, item=item)
                .order_by("created_at")
                .first()
            )
        if item.media_type == MediaTypes.TV.value:
            return TV.objects.filter(user=self.user, item=item).first()
        return None

    def _can_remove_synced_media(self, item: Item, media_obj) -> bool:
        """Return True when a synced Planning row is safe to auto-remove."""
        if item.media_type == MediaTypes.MOVIE.value:
            if Movie.objects.filter(user=self.user, item=item).count() != 1:
                return False
        elif item.media_type == MediaTypes.TV.value:
            if TV.objects.filter(user=self.user, item=item).count() != 1:
                return False
        else:
            return False

        if media_obj.status != Status.PLANNING.value:
            return False
        if getattr(media_obj, "score", None) is not None:
            return False
        if (getattr(media_obj, "progress", 0) or 0) != 0:
            return False
        if getattr(media_obj, "notes", "").strip():
            return False
        if getattr(media_obj, "start_date", None) is not None:
            return False
        return getattr(media_obj, "end_date", None) is None

    def _warn(self, message: str) -> None:
        """Collect a warning without duplicating format logic in callers."""
        self.warnings.append(message)
