"""Import TV Time's GDPR data-export CSVs.

TV Time has no public API, so the only way to recover a user's watch history
is the two CSVs from a GDPR data request: ``tracking-prod-records.csv``
(episode watch history) and ``tracking-prod-records-v2.csv`` (movie watch
activity). Neither carries a TMDB/TVDB id, so every row is resolved by title
search via ``TraktMetadataResolverMixin`` - the same fallback Trakt imports
use when a Trakt entry has no cross-referenced TMDB id (see trakt.py#965).
"""

import logging
from collections import defaultdict
from csv import DictReader

from django.utils.dateparse import parse_datetime

import app
from app.models import MediaTypes, Sources, Status
from app.providers import services
from integrations import import_progress
from integrations.imports import helpers
from integrations.imports.helpers import MediaImportUnexpectedError
from integrations.imports.trakt import TraktMetadataResolverMixin

logger = logging.getLogger(__name__)


def _decode_csv(file):
    """Decode an uploaded CSV file's bytes, tolerating non-UTF-8 exports."""
    raw_file = file.read()
    try:
        return raw_file.decode("utf-8-sig").splitlines()
    except UnicodeDecodeError:
        return raw_file.decode("latin-1").splitlines()


class TvTimeShowImporter(TraktMetadataResolverMixin):
    """Import TV Time's episode watch-history CSV (tracking-prod-records.csv)."""

    def __init__(self, file, user, mode):
        """Bind the importer to an uploaded CSV file, user, and import mode."""
        self.file = file
        self.user = user
        self.mode = mode
        self.warnings = []

        self.existing_media = helpers.get_existing_media(user)
        self.existing_children = helpers.get_existing_children(user)
        self.existing_episode_watch_keys = self._get_existing_episode_watch_keys()

        self.to_delete = defaultdict(lambda: defaultdict(set))
        self.bulk_media = defaultdict(list)

        # In-memory TV/Season rows created (or reused) this run, keyed by
        # tmdb id / (tmdb id, season number), so repeated episode rows for
        # the same show reuse one instance instead of queuing duplicates
        # before helpers.bulk_create_media persists them.
        self.pending_tv = {}
        self.pending_season = {}
        self.tv_created_this_run = set()
        self.season_created_this_run = set()

        # Rows already queued this run, so a duplicate row within the same
        # CSV isn't imported twice.
        self.seen_episode_keys = set()

        self._tmdb_id_cache = {}
        self._tv_metadata_cache = {}
        self._season_metadata_cache = {}

        logger.info(
            "Initialized TV Time show importer for user %s with mode %s",
            user.username,
            mode,
        )

    def _get_existing_episode_watch_keys(self):
        """Return exact episode play keys already stored for this user.

        Mirrors TraktImporter._get_existing_episode_watch_keys: only needed
        in "new" mode, since "overwrite" mode deletes the whole show (and its
        seasons/episodes cascade) before re-importing it from this CSV.
        """
        if self.mode != "new":
            return set()

        return set(
            app.models.Episode.objects.filter(
                related_season__user=self.user,
                end_date__isnull=False,
            ).values_list(
                "item__media_id",
                "item__season_number",
                "item__episode_number",
                "end_date",
            ),
        )

    def import_data(self):
        """Import all rows from the TV Time shows CSV."""
        decoded_file = _decode_csv(self.file)
        rows = list(DictReader(decoded_file))
        total = len(rows)

        for i, row in enumerate(rows, start=1):
            import_progress.report(i, total, "TV Time")
            # Rows without an episode number aren't episode watch entries
            # (e.g. "follow" activity), matching the reference tool's filter.
            if not (row.get("series_name") or "").strip() or not (
                row.get("episode_number") or ""
            ).strip():
                continue
            try:
                self._process_row(row)
            except services.ProviderAPIError as error:
                title = (row.get("series_name") or "Unknown title").strip()
                logger.warning("Error processing TV Time entry %s: %s", title, error)
                self.warnings.append(f"Error processing entry: {title} - {error}")
            except Exception as error:
                error_msg = f"Error processing entry: {row}"
                raise MediaImportUnexpectedError(error_msg) from error

        helpers.cleanup_existing_media(self.to_delete, self.user)
        helpers.bulk_create_media(self.bulk_media, self.user)

        imported_counts = {
            media_type: len(media_list)
            for media_type, media_list in self.bulk_media.items()
        }
        deduplicated_messages = "\n".join(dict.fromkeys(self.warnings))
        return imported_counts, deduplicated_messages

    def _resolve_tmdb_id(self, series_name):
        if series_name not in self._tmdb_id_cache:
            self._tmdb_id_cache[series_name] = self._get_tmdb_id(
                {"title": series_name},
                MediaTypes.TV.value,
            )
        return self._tmdb_id_cache[series_name]

    def _get_tv_metadata(self, tmdb_id, title):
        if tmdb_id not in self._tv_metadata_cache:
            self._tv_metadata_cache[tmdb_id] = self._get_metadata(
                MediaTypes.TV.value,
                tmdb_id,
                title,
            )
        return self._tv_metadata_cache[tmdb_id]

    def _get_season_metadata(self, tmdb_id, title, season_number):
        key = (tmdb_id, season_number)
        if key not in self._season_metadata_cache:
            self._season_metadata_cache[key] = self._get_metadata(
                MediaTypes.SEASON.value,
                tmdb_id,
                title,
                season_number,
            )
        return self._season_metadata_cache[key]

    def _process_row(self, row):
        series_name = row["series_name"].strip()
        try:
            season_number = int(row["season_number"].strip())
            episode_number = int(row["episode_number"].strip())
        except ValueError:
            self.warnings.append(
                f"{series_name}: non-numeric season/episode number, skipped.",
            )
            return

        watched_at = parse_datetime((row.get("created_at") or "").strip())
        if watched_at is None:
            self.warnings.append(
                f"{series_name} S{int(season_number):02d}E{int(episode_number):02d}: "
                "unrecognized watched-at date, skipped.",
            )
            return

        tmdb_id = self._resolve_tmdb_id(series_name)
        if not tmdb_id:
            return

        episode_watch_key = (tmdb_id, season_number, episode_number, watched_at)
        if (
            episode_watch_key in self.existing_episode_watch_keys
            or episode_watch_key in self.seen_episode_keys
        ):
            return

        if self.mode == "overwrite" and tmdb_id in self.existing_media[
            MediaTypes.TV.value
        ][Sources.TMDB.value]:
            self.to_delete[MediaTypes.TV.value][Sources.TMDB.value].add(tmdb_id)

        tv_metadata = self._get_tv_metadata(tmdb_id, series_name)
        if not tv_metadata:
            return

        season_metadata = self._get_season_metadata(tmdb_id, series_name, season_number)
        if not season_metadata:
            return

        episode_exists = any(
            ep["episode_number"] == episode_number for ep in season_metadata["episodes"]
        )
        if not episode_exists:
            self.warnings.append(
                f"{series_name} S{int(season_number):02d}E{int(episode_number):02d}: not found in "
                f"{Sources.TMDB.label} with ID {tmdb_id}.",
            )
            return

        tv_item = self._get_or_create_item(MediaTypes.TV.value, tmdb_id, tv_metadata)
        tv_obj, tv_is_new = self._get_or_create_tv(tmdb_id, tv_item)

        season_item = self._get_or_create_item(
            MediaTypes.SEASON.value,
            tmdb_id,
            season_metadata,
            season_number,
        )
        season_obj, season_is_new = self._get_or_create_season(
            tmdb_id,
            season_number,
            season_item,
            tv_obj,
        )

        episode_image = self._get_episode_image(episode_number, season_metadata)
        matched_episode = next(
            (
                ep
                for ep in season_metadata["episodes"]
                if ep["episode_number"] == episode_number
            ),
            None,
        )
        episode_metadata = {
            **app.models.Item.title_fields_from_episode_metadata(
                matched_episode,
                fallback_title=tv_metadata["title"],
            ),
            "image": episode_image,
        }
        episode_item = self._get_or_create_item(
            MediaTypes.EPISODE.value,
            tmdb_id,
            episode_metadata,
            season_number,
            episode_number,
        )

        episode_obj = app.models.Episode(
            item=episode_item,
            related_season=season_obj,
            end_date=watched_at,
        )
        episode_obj._history_date = watched_at
        self.bulk_media[MediaTypes.EPISODE.value].append(episode_obj)
        self.seen_episode_keys.add(episode_watch_key)

        # Only roll a season/show up to Completed when Floppy is seeing it
        # for the first time this run - never clobber the status of a show
        # the user already tracks locally through another source.
        if season_is_new:
            self._maybe_complete_season(
                season_obj,
                tv_obj,
                tv_is_new,
                episode_number,
                season_metadata,
                tv_metadata,
            )

    def _get_or_create_tv(self, tmdb_id, tv_item):
        """Return (tv_obj, created_this_run), reusing an existing DB/pending row."""
        if tmdb_id in self.pending_tv:
            return self.pending_tv[tmdb_id], tmdb_id in self.tv_created_this_run

        tv_obj = None
        if tmdb_id not in self.to_delete[MediaTypes.TV.value][Sources.TMDB.value]:
            tv_obj = self.existing_media[MediaTypes.TV.value][Sources.TMDB.value].get(
                tmdb_id,
            )

        created = tv_obj is None
        if created:
            tv_obj = app.models.TV(
                item=tv_item,
                user=self.user,
                status=Status.IN_PROGRESS.value,
            )
            self.bulk_media[MediaTypes.TV.value].append(tv_obj)
            self.tv_created_this_run.add(tmdb_id)

        self.pending_tv[tmdb_id] = tv_obj
        return tv_obj, created

    def _get_or_create_season(self, tmdb_id, season_number, season_item, tv_obj):
        """Return (season_obj, created_this_run), reusing an existing DB/pending row."""
        season_key = (tmdb_id, season_number)
        if season_key in self.pending_season:
            return self.pending_season[season_key], season_key in self.season_created_this_run

        season_obj = None
        if tmdb_id not in self.to_delete[MediaTypes.TV.value][Sources.TMDB.value]:
            season_obj = self.existing_children[MediaTypes.SEASON.value][
                Sources.TMDB.value
            ].get(season_key)

        created = season_obj is None
        if created:
            season_obj = app.models.Season(
                item=season_item,
                user=self.user,
                related_tv=tv_obj,
                status=Status.IN_PROGRESS.value,
            )
            self.bulk_media[MediaTypes.SEASON.value].append(season_obj)
            self.season_created_this_run.add(season_key)

        self.pending_season[season_key] = season_obj
        return season_obj, created

    @staticmethod
    def _maybe_complete_season(
        season_obj,
        tv_obj,
        tv_is_new,
        episode_number,
        season_metadata,
        tv_metadata,
    ):
        """Mark the season (and show, if it's also finished) Completed."""
        if episode_number != helpers.season_last_episode_number(season_metadata):
            return

        season_obj.status = Status.COMPLETED.value

        last_season = tv_metadata.get("last_episode_season")
        if last_season and last_season == season_obj.item.season_number and tv_is_new:
            tv_obj.status = Status.COMPLETED.value


class TvTimeMovieImporter(TraktMetadataResolverMixin):
    """Import TV Time's movie watch-activity CSV (tracking-prod-records-v2.csv)."""

    def __init__(self, file, user, mode):
        """Bind the importer to an uploaded CSV file, user, and import mode."""
        self.file = file
        self.user = user
        self.mode = mode
        self.warnings = []

        self.existing_media = helpers.get_existing_media(user)
        self.to_delete = defaultdict(lambda: defaultdict(set))
        self.bulk_media = defaultdict(list)
        self._tmdb_id_cache = {}

    def import_data(self):
        """Import all rows from the TV Time movies CSV."""
        decoded_file = _decode_csv(self.file)
        rows = list(DictReader(decoded_file))
        total = len(rows)

        for i, row in enumerate(rows, start=1):
            import_progress.report(i, total, "TV Time movies")
            movie_name = (row.get("movie_name") or "").strip()
            # TV Time logs watchlist/follow activity in the same file; only
            # "watch" rows represent a completed watch.
            if not movie_name or (row.get("type") or "").strip().lower() != "watch":
                continue
            try:
                self._process_row(row, movie_name)
            except services.ProviderAPIError as error:
                logger.warning(
                    "Error processing TV Time movie %s: %s",
                    movie_name,
                    error,
                )
                self.warnings.append(f"Error processing entry: {movie_name} - {error}")
            except Exception as error:
                error_msg = f"Error processing entry: {row}"
                raise MediaImportUnexpectedError(error_msg) from error

        helpers.cleanup_existing_media(self.to_delete, self.user)
        helpers.bulk_create_media(self.bulk_media, self.user)

        imported_counts = {
            media_type: len(media_list)
            for media_type, media_list in self.bulk_media.items()
        }
        deduplicated_messages = "\n".join(dict.fromkeys(self.warnings))
        return imported_counts, deduplicated_messages

    def _process_row(self, row, movie_name):
        watched_at = parse_datetime((row.get("updated_at") or "").strip())

        if movie_name not in self._tmdb_id_cache:
            self._tmdb_id_cache[movie_name] = self._get_tmdb_id(
                {"title": movie_name},
                MediaTypes.MOVIE.value,
            )
        tmdb_id = self._tmdb_id_cache[movie_name]
        if not tmdb_id:
            return

        if not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            MediaTypes.MOVIE.value,
            Sources.TMDB.value,
            tmdb_id,
            self.mode,
        ):
            return

        metadata = self._get_metadata(MediaTypes.MOVIE.value, tmdb_id, movie_name)
        if not metadata:
            return

        item = self._get_or_create_item(MediaTypes.MOVIE.value, tmdb_id, metadata)
        movie_obj = app.models.Movie(
            item=item,
            user=self.user,
            end_date=watched_at,
            status=Status.COMPLETED.value,
            progress=1,
        )
        if watched_at is not None:
            movie_obj._history_date = watched_at
        self.bulk_media[MediaTypes.MOVIE.value].append(movie_obj)


def importer_shows(file, user, mode):
    """Import TV Time episode watch history from a GDPR export CSV."""
    return TvTimeShowImporter(file, user, mode).import_data()


def importer_movies(file, user, mode):
    """Import TV Time movie watch activity from a GDPR export CSV."""
    return TvTimeMovieImporter(file, user, mode).import_data()
