"""Shared logic for writing Trakt collection (ownership) data into CollectionEntry.

Used by both the live Trakt API import (``TraktImporter.process_collection`` in
``trakt.py``) and the Trakt collection CSV import (``TraktCollectionCsvImporter``
below), since both sources describe ownership with the same field vocabulary
(resolution, hdr, audio, audio_channels, 3d, media_type).
"""

import logging
from csv import DictReader

from django.utils.dateparse import parse_datetime

from app.models import CollectionEntry, MediaTypes, Sources
from integrations import import_progress
from integrations.imports.helpers import MediaImportError, retry_on_lock
from integrations.imports.trakt import TraktMetadataResolverMixin

logger = logging.getLogger(__name__)


def _parse_3d(value):
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes"}


def normalize_trakt_collection_metadata(raw):
    """Map a Trakt collection metadata payload onto CollectionEntry field names.

    Accepts either the Trakt API's nested ``metadata`` object or the flat CSV
    export's columns — both use the same field names (media_type, resolution,
    hdr, audio, audio_channels, 3d).
    """
    raw = raw or {}
    return {
        "media_type": str(raw.get("media_type") or "").strip(),
        "resolution": str(raw.get("resolution") or "").strip(),
        "hdr": str(raw.get("hdr") or "").strip(),
        "audio_codec": str(raw.get("audio") or "").strip(),
        "audio_channels": str(raw.get("audio_channels") or "").strip(),
        "is_3d": _parse_3d(raw.get("3d")),
    }


def upsert_collection_entry(user, item, *, mode, collected_at=None, metadata=None):
    """Get-or-create/update a CollectionEntry for ``item`` from Trakt data.

    "new" mode leaves an existing entry untouched; "overwrite" mode updates it.
    """
    fields = normalize_trakt_collection_metadata(metadata)

    def _upsert():
        entry = (
            CollectionEntry.objects.filter(user=user, item=item)
            .order_by("-updated_at", "-collected_at", "-id")
            .first()
        )
        if entry and mode == "new":
            return entry

        created = False
        if entry is None:
            entry = CollectionEntry.objects.create(user=user, item=item)
            created = True

        update_fields = []
        for field_name, value in fields.items():
            if getattr(entry, field_name) != value:
                setattr(entry, field_name, value)
                update_fields.append(field_name)
        if update_fields:
            entry.save(update_fields=[*update_fields, "updated_at"])

        if collected_at and (created or mode == "overwrite"):
            CollectionEntry.objects.filter(id=entry.id).update(
                collected_at=collected_at,
            )

        return entry

    return retry_on_lock(_upsert)


def apply_season_and_show_rollup(
    user,
    mode,
    tv_item,
    season_item,
    tv_metadata,
    season_metadata,
    collected_at=None,
    show_collected_at=None,
):
    """Create season/show CollectionEntry rows once every episode is owned.

    Mirrors how ``TraktImporter._update_completion_status`` rolls episode watch
    events up into season/show watch status, but for ownership.

    ``collected_at`` dates the season entry and ``show_collected_at`` the show
    entry (falling back to ``collected_at``), so historical dates survive.
    """
    max_progress = season_metadata.get("max_progress")
    if not max_progress:
        return

    collected_episode_count = (
        CollectionEntry.objects.filter(
            user=user,
            item__media_id=season_item.media_id,
            item__source=season_item.source,
            item__media_type=MediaTypes.EPISODE.value,
            item__season_number=season_item.season_number,
        )
        .values("item_id")
        .distinct()
        .count()
    )
    if collected_episode_count < max_progress:
        return

    upsert_collection_entry(user, season_item, mode=mode, collected_at=collected_at)

    season_numbers = {
        season.get("season_number")
        for season in (tv_metadata.get("related") or {}).get("seasons") or []
        if season.get("season_number") not in (None, 0)
    }
    if not season_numbers:
        return

    collected_season_numbers = set(
        CollectionEntry.objects.filter(
            user=user,
            item__media_id=tv_item.media_id,
            item__source=tv_item.source,
            item__media_type=MediaTypes.SEASON.value,
            item__season_number__in=season_numbers,
        ).values_list("item__season_number", flat=True),
    )
    if collected_season_numbers >= season_numbers:
        upsert_collection_entry(
            user,
            tv_item,
            mode=mode,
            collected_at=show_collected_at or collected_at,
        )


class TraktCollectionCsvImporter(TraktMetadataResolverMixin):
    """Import Trakt's collection export CSV into CollectionEntry rows.

    For users without a live Trakt account (e.g. a canceled subscription), this
    parses the "collection" CSV Trakt's export tool produces — distinct from the
    live API paginated endpoints used by TraktImporter.process_collection.
    """

    def __init__(self, file, user, mode):
        """Bind the importer to an uploaded CSV file, user, and import mode."""
        self.file = file
        self.user = user
        self.mode = mode
        self.warnings = []
        # season key -> (tv_item, season_item, tv_metadata, season_metadata)
        self._season_cache = {}

    def import_data(self):
        """Import all rows from the CSV file."""
        try:
            decoded_file = self.file.read().decode("utf-8-sig").splitlines()
        except UnicodeDecodeError as e:
            msg = "Invalid file format. Please upload a CSV file."
            raise MediaImportError(msg) from e

        rows = list(DictReader(decoded_file))
        total = len(rows)
        imported_counts = {"movie": 0, "episode": 0}

        for i, row in enumerate(rows, start=1):
            import_progress.report(i, total, "Trakt collection CSV")
            row_type = (row.get("type") or "").strip().lower()
            try:
                if row_type == "movie":
                    if self._process_movie_row(row):
                        imported_counts["movie"] += 1
                elif row_type == "episode":
                    if self._process_episode_row(row):
                        imported_counts["episode"] += 1
                else:
                    self.warnings.append(f"Skipping unknown row type: {row_type}")
            except Exception as error:
                logger.exception("Skipping malformed Trakt collection CSV row")
                title = row.get("title") or "Unknown title"
                self.warnings.append(
                    f"{title}: skipped a collection row due to an unexpected error "
                    f"({error}).",
                )

        for (
            season_item,
            tv_item,
            tv_metadata,
            season_metadata,
        ) in self._season_cache.values():
            apply_season_and_show_rollup(
                self.user,
                self.mode,
                tv_item,
                season_item,
                tv_metadata,
                season_metadata,
            )

        deduplicated_messages = "\n".join(dict.fromkeys(self.warnings))
        return imported_counts, deduplicated_messages

    def _row_collected_at(self, row):
        collected_at = (row.get("collected_at") or "").strip()
        if not collected_at:
            return None
        return parse_datetime(collected_at)

    def _row_metadata(self, row):
        return {
            "media_type": row.get("media_type"),
            "resolution": row.get("resolution"),
            "hdr": row.get("hdr"),
            "audio": row.get("audio"),
            "audio_channels": row.get("audio_channels"),
            "3d": row.get("3d"),
        }

    def _process_movie_row(self, row):
        tmdb_id = (row.get("tmdb_id") or "").strip()
        title = row.get("title") or "Unknown title"
        if not tmdb_id:
            self.warnings.append(f"{title}: No {Sources.TMDB.label} ID found.")
            return False

        metadata = self._get_metadata(MediaTypes.MOVIE.value, tmdb_id, title)
        if not metadata:
            return False

        item = self._get_or_create_item(MediaTypes.MOVIE.value, tmdb_id, metadata)
        upsert_collection_entry(
            self.user,
            item,
            mode=self.mode,
            collected_at=self._row_collected_at(row),
            metadata=self._row_metadata(row),
        )
        return True

    def _process_episode_row(self, row):
        tmdb_id = (row.get("tmdb_id") or "").strip()
        title = row.get("title") or "Unknown title"
        season_number_raw = (row.get("season_number") or "").strip()
        episode_number_raw = (row.get("episode_number") or "").strip()

        if not tmdb_id or season_number_raw == "" or episode_number_raw == "":
            self.warnings.append(
                f"{title}: missing show/season/episode identifiers, skipped.",
            )
            return False

        season_number = int(season_number_raw)
        episode_number = int(episode_number_raw)

        tv_metadata = self._get_metadata(MediaTypes.TV.value, tmdb_id, title)
        if not tv_metadata:
            return False

        season_metadata = self._get_metadata(
            MediaTypes.SEASON.value,
            tmdb_id,
            title,
            season_number,
        )
        if not season_metadata:
            return False

        episode_exists = any(
            ep["episode_number"] == episode_number for ep in season_metadata["episodes"]
        )
        if not episode_exists:
            self.warnings.append(
                f"{title} S{int(season_number):02d}E{int(episode_number):02d}: not found in "
                f"{Sources.TMDB.label} with ID {tmdb_id}.",
            )
            return False

        tv_item = self._get_or_create_item(MediaTypes.TV.value, tmdb_id, tv_metadata)
        season_item = self._get_or_create_item(
            MediaTypes.SEASON.value,
            tmdb_id,
            season_metadata,
            season_number,
        )

        episode_image = self._get_episode_image(episode_number, season_metadata)
        episode_metadata = {
            "title": tv_metadata["title"],
            "original_title": tv_metadata.get("original_title"),
            "localized_title": tv_metadata.get("localized_title"),
            "image": episode_image,
        }
        episode_item = self._get_or_create_item(
            MediaTypes.EPISODE.value,
            tmdb_id,
            episode_metadata,
            season_number,
            episode_number,
        )

        upsert_collection_entry(
            self.user,
            episode_item,
            mode=self.mode,
            collected_at=self._row_collected_at(row),
            metadata=self._row_metadata(row),
        )

        self._season_cache[f"{tmdb_id}:{season_number}"] = (
            season_item,
            tv_item,
            tv_metadata,
            season_metadata,
        )
        return True


def importer(file, user, mode):
    """Import media collection ownership from a Trakt collection CSV export."""
    csv_importer = TraktCollectionCsvImporter(file, user, mode)
    return csv_importer.import_data()
