"""Importer for TV Time GDPR export archives.

TV Time identifies TV shows and episodes with TheTVDB ids, which map to
Yamtrack's TMDB source through TMDB's ``/find?external_source=tvdb_id``
endpoint (see ``TVTimeImporter._map_series``). Movies are only exported with a
title and release date (no TheTVDB/TMDB/IMDb id), so they are matched to TMDB
by title search instead (see ``TVTimeImporter._search_movie``).
"""

import csv
import io
import logging
import re
import zipfile
from collections import defaultdict

import requests
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

import app
from app.models import MediaTypes, Sources, Status
from app.providers import services, tmdb
from integrations.imports import helpers
from integrations.imports.helpers import MediaImportError, MediaImportUnexpectedError
from lists.models import CustomList, CustomListItem

logger = logging.getLogger(__name__)

# The custom-list export is a dump of Go maps, e.g.
# ``map[created_at:... id:83322 type:series]``. This matches one such map block
# (list items never contain nested arrays).
_GO_MAP_BLOCK = re.compile(r"map\[([^\[\]]*)\]")

# TV Time delivers a GDPR export as a zip with one CSV per database table. The
# file names are inconsistent (hyphens, underscores, service prefixes), so we
# match them by a normalized key (lowercase, alphanumeric only) instead of an
# exact name.
FILE_WATCHED_V2 = "trackingprodrecordsv2csv"
FILE_WATCHED_V1 = "trackingprodrecordscsv"
FILE_WATCHED_SIMPLE = "watchedonepisodecsv"
FILE_SHOW_DATA = "usertvshowdatacsv"
FILE_FOLLOWED = "followedtvshowcsv"
FILE_EPISODE_RATINGS = "ratings3prodepisodevotescsv"
FILE_LISTS = "listsprodlistscsv"

KNOWN_FILES = {
    FILE_WATCHED_V2,
    FILE_WATCHED_V1,
    FILE_WATCHED_SIMPLE,
    FILE_SHOW_DATA,
    FILE_FOLLOWED,
    FILE_EPISODE_RATINGS,
    FILE_LISTS,
}

# TV Time stores ratings as an opaque "vote" value that is not publicly
# documented as a 1-10 score (leading importers such as TVmaze could not map
# it either). Map the values that can be translated confidently here; unknown
# values are skipped rather than guessed. Extend this table if the mapping
# becomes known.
VOTE_SCORE_MAP = {}


def _normalize_name(name):
    """Return a lowercase, alphanumeric-only key for a zip member name."""
    basename = name.replace("\\", "/").rsplit("/", 1)[-1]
    return "".join(char for char in basename.lower() if char.isalnum())


def importer(file, user, mode):
    """Import media from a TV Time GDPR export zip file."""
    return TVTimeImporter(file, user, mode).import_data()


class TVTimeImporter:
    """Class to handle importing user data from a TV Time export zip."""

    def __init__(self, file, user, mode):
        """Initialize the importer with the uploaded zip, user, and mode.

        Args:
            file: Uploaded TV Time export zip file
            user: Django user object to import data for
            mode (str): Import mode ("new" or "overwrite")
        """
        self.file = file
        self.user = user
        self.mode = mode
        self.warnings = []

        # Track existing media for "new" mode
        self.existing_media = helpers.get_existing_media(user)

        # Track media IDs to delete in overwrite mode
        self.to_delete = defaultdict(lambda: defaultdict(set))

        # Track bulk creation lists for each media type
        self.bulk_media = defaultdict(list)

        # Runtime state populated while processing
        self.show_meta = {}  # tvdb_series_id -> {name, is_followed}
        self.name_to_series = {}  # tv_show_name -> tvdb_series_id
        self.tmdb_id_cache = {}  # tvdb_series_id -> tmdb_id (or None)
        self.tv_instances = {}  # tvdb_series_id -> TV instance
        self.season_instances = {}  # (tvdb_series_id, season) -> Season instance
        self.series_scores = defaultdict(list)  # tvdb_series_id -> [score, ...]
        self.movie_tmdb_ids = set()  # tmdb ids already added as movies
        self.lists_created = 0
        self.has_comments = False

        logger.info(
            "Initialized TV Time importer for user %s with mode %s",
            user.username,
            mode,
        )

    def import_data(self):
        """Import all user data from the TV Time export."""
        files = self._read_zip()

        if not any(key in files for key in KNOWN_FILES):
            msg = (
                "No supported TV Time data found in the zip. Upload the full "
                "GDPR export archive from TV Time."
            )
            raise MediaImportError(msg)

        self._load_show_data(files)
        self._load_episode_ratings(files)

        watched = self._collect_watched(files)
        self._process_watched(watched)
        self._process_watchlist()
        self._process_movies(files)

        helpers.cleanup_existing_media(self.to_delete, self.user)
        helpers.bulk_create_media(self.bulk_media, self.user)

        # Lists reference Items directly, so build them after media is created.
        self._process_lists(files)

        if self.has_comments:
            self.warnings.append(
                "Comments were not imported: TV Time exports them without an "
                "identifier that can be matched to The Movie Database.",
            )

        imported_counts = {
            media_type: len(media_list)
            for media_type, media_list in self.bulk_media.items()
        }
        if self.lists_created:
            imported_counts["list"] = self.lists_created

        deduplicated_messages = "\n".join(dict.fromkeys(self.warnings))
        return imported_counts, deduplicated_messages

    def _read_zip(self):
        """Read the uploaded zip and return the recognized CSV files as rows.

        Only the media files in ``KNOWN_FILES`` are read; every other member is
        ignored. A TV Time export also contains account files such as
        ``access_token.csv`` and ``auth-prod-login.csv`` (password hashes,
        tokens) which are never opened.
        """
        try:
            archive = zipfile.ZipFile(self.file)
        except zipfile.BadZipFile as error:
            msg = "Invalid file. Please upload the TV Time export zip file."
            raise MediaImportError(msg) from error

        files = {}
        with archive:
            for name in archive.namelist():
                if not name.lower().endswith(".csv"):
                    continue

                normalized = _normalize_name(name)

                if "comments" in normalized:
                    self.has_comments = True

                if normalized not in KNOWN_FILES:
                    continue

                try:
                    raw = archive.read(name).decode("utf-8-sig")
                except (UnicodeDecodeError, KeyError) as error:
                    msg = f"Could not read {name} from the TV Time export."
                    raise MediaImportError(msg) from error

                files[normalized] = list(csv.DictReader(io.StringIO(raw)))

        return files

    def _load_show_data(self, files):
        """Load per-show metadata (follow status and TVDB series ids)."""
        for row in files.get(FILE_SHOW_DATA, []):
            series_id = (row.get("tv_show_id") or "").strip()
            name = (row.get("tv_show_name") or "").strip()
            if not series_id:
                continue

            self.show_meta[series_id] = {
                "name": name,
                "is_followed": row.get("is_followed") == "1",
            }
            if name:
                self.name_to_series[name] = series_id

        # followed_tv_show.csv adds shows the user follows but may not have
        # any tracked episodes for.
        for row in files.get(FILE_FOLLOWED, []):
            series_id = (row.get("tv_show_id") or "").strip()
            name = (row.get("tv_show_name") or "").strip()
            if not series_id:
                continue

            entry = self.show_meta.setdefault(
                series_id,
                {"name": name, "is_followed": False},
            )
            if row.get("active") == "1":
                entry["is_followed"] = True
            if name and name not in self.name_to_series:
                self.name_to_series[name] = series_id

    def _load_episode_ratings(self, files):
        """Load episode ratings keyed by TVDB series id."""
        for row in files.get(FILE_EPISODE_RATINGS, []):
            series_name = (row.get("series_name") or "").strip()
            series_id = self.name_to_series.get(series_name)
            if not series_id:
                continue

            score = self._vote_to_score(row.get("vote_key"))
            if score is not None:
                self.series_scores[series_id].append(score)

    def _vote_to_score(self, vote_key):
        """Translate a TV Time vote_key into a 0-10 score if it is mappable."""
        if not vote_key:
            return None

        vote = vote_key.rsplit("-", 1)[-1].strip()
        return VOTE_SCORE_MAP.get(vote)

    def _collect_watched(self, files):
        """Return deduplicated watched episodes grouped by TVDB series id.

        Prefers the comprehensive ``tracking-prod-records-v2`` file and falls
        back to ``watched_on_episode`` when it is not present.
        """
        watched = defaultdict(dict)  # series_id -> {episode_id: entry}

        if FILE_WATCHED_V2 in files:
            rows = files[FILE_WATCHED_V2]
            for row in rows:
                self._add_watched_entry(
                    watched,
                    series_id=(row.get("s_id") or "").strip(),
                    series_name=(row.get("series_name") or "").strip(),
                    season=row.get("season_number"),
                    episode=row.get("episode_number"),
                    episode_id=(row.get("episode_id") or "").strip(),
                    watched_at=row.get("created_at"),
                )
        else:
            rows = files.get(FILE_WATCHED_SIMPLE, [])
            for row in rows:
                name = (row.get("tv_show_name") or "").strip()
                self._add_watched_entry(
                    watched,
                    series_id=self.name_to_series.get(name, ""),
                    series_name=name,
                    season=row.get("episode_season_number"),
                    episode=row.get("episode_number"),
                    episode_id=(row.get("episode_id") or "").strip(),
                    watched_at=row.get("created_at"),
                )

        return watched

    def _add_watched_entry(
        self,
        watched,
        series_id,
        series_name,
        season,
        episode,
        episode_id,
        watched_at,
    ):
        """Add a single watched episode, keeping the earliest watch date."""
        if not series_id:
            if series_name:
                self.warnings.append(
                    f"{series_name}: could not determine its TV Time series id.",
                )
            return

        try:
            season_number = int(season)
            episode_number = int(episode)
        except (TypeError, ValueError):
            return

        if series_id not in self.show_meta:
            self.show_meta[series_id] = {
                "name": series_name,
                "is_followed": False,
            }

        key = episode_id or f"{season_number}:{episode_number}"
        watched_at = self._parse_dt(watched_at)

        existing = watched[series_id].get(key)
        if existing and existing["watched_at"] and watched_at:
            watched_at = min(existing["watched_at"], watched_at)

        watched[series_id][key] = {
            "season": season_number,
            "episode": episode_number,
            "watched_at": watched_at,
        }

    def _parse_dt(self, value):
        """Parse a TV Time timestamp into an aware datetime."""
        if not value:
            return None

        parsed = parse_datetime(value.strip())
        if parsed and timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
        return parsed

    def _process_watched(self, watched):
        """Create TV, season and episode records for watched episodes."""
        for series_id, episodes in watched.items():
            series_name = self.show_meta.get(series_id, {}).get("name", series_id)
            try:
                self._process_series(series_id, series_name, episodes)
            except (MediaImportError, MediaImportUnexpectedError):
                raise
            except Exception as error:
                msg = f"Error processing TV Time series: {series_name}"
                raise MediaImportUnexpectedError(msg) from error

    def _process_series(self, series_id, series_name, episodes):
        """Process all watched episodes for a single TV series."""
        tmdb_id = self._map_series(series_id, series_name)
        if not tmdb_id:
            return

        if not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            MediaTypes.TV.value,
            Sources.TMDB.value,
            tmdb_id,
            self.mode,
        ):
            return

        tv_metadata = self._get_metadata(MediaTypes.TV.value, tmdb_id, series_name)
        if not tv_metadata:
            return

        tv_instance = self._get_or_create_tv(series_id, tmdb_id, tv_metadata)

        # Group episodes by season to fetch each season's metadata once.
        by_season = defaultdict(list)
        for entry in episodes.values():
            by_season[entry["season"]].append(entry)

        for season_number, season_episodes in sorted(by_season.items()):
            self._process_season(
                series_id,
                tmdb_id,
                season_number,
                season_episodes,
                tv_instance,
                tv_metadata,
                series_name,
            )

        scores = self.series_scores.get(series_id)
        if scores:
            tv_instance.score = round(sum(scores) / len(scores), 1)

    def _process_season(
        self,
        series_id,
        tmdb_id,
        season_number,
        season_episodes,
        tv_instance,
        tv_metadata,
        series_name,
    ):
        """Process the watched episodes of a single season."""
        season_metadata = self._get_metadata(
            MediaTypes.SEASON.value,
            tmdb_id,
            series_name,
            season_number,
        )
        if not season_metadata:
            return

        valid_episode_numbers = {
            ep["episode_number"] for ep in season_metadata["episodes"]
        }

        season_instance = self._get_or_create_season(
            series_id,
            tmdb_id,
            season_number,
            season_metadata,
            tv_instance,
        )

        for entry in season_episodes:
            episode_number = entry["episode"]
            if episode_number not in valid_episode_numbers:
                self.warnings.append(
                    f"{series_name} S{season_number}E{episode_number}: "
                    f"not found in {Sources.TMDB.label}.",
                )
                continue

            self._create_episode(
                tmdb_id,
                season_number,
                episode_number,
                entry["watched_at"],
                season_metadata,
                tv_metadata,
                season_instance,
            )

            self._update_completion_status(
                season_instance,
                tv_instance,
                season_number,
                episode_number,
                season_metadata,
                tv_metadata,
            )

    def _map_series(self, series_id, series_name):
        """Map a TVDB series id to a TMDB id, caching the lookup."""
        if series_id in self.tmdb_id_cache:
            return self.tmdb_id_cache[series_id]

        tmdb_id = None
        try:
            response = tmdb.find(series_id, "tvdb_id")
        except services.ProviderAPIError as error:
            logger.warning("Error looking up TVDB id %s: %s", series_id, error)
            response = {}

        results = response.get("tv_results") or []
        if results:
            tmdb_id = str(results[0]["id"])
        else:
            self.warnings.append(
                f"{series_name}: could not be matched in {Sources.TMDB.label}.",
            )

        self.tmdb_id_cache[series_id] = tmdb_id
        return tmdb_id

    def _get_metadata(self, media_type, tmdb_id, title, season_number=None):
        """Get metadata for a media item, warning if it is missing."""
        try:
            season_numbers = [season_number] if season_number is not None else None
            return services.get_media_metadata(
                media_type,
                tmdb_id,
                Sources.TMDB.value,
                season_numbers,
            )
        except services.ProviderAPIError as error:
            if error.status_code == requests.codes.not_found:
                if media_type == MediaTypes.SEASON.value:
                    title = f"{title} S{season_number}"
                self.warnings.append(
                    f"{title}: not found in {Sources.TMDB.label} with ID {tmdb_id}.",
                )
                return None
            raise

    def _get_or_create_item(
        self,
        media_type,
        tmdb_id,
        metadata,
        season_number=None,
        episode_number=None,
    ):
        """Get or create an Item in the database."""
        item_kwargs = {
            "media_id": tmdb_id,
            "source": Sources.TMDB.value,
            "media_type": media_type,
        }
        if season_number is not None:
            item_kwargs["season_number"] = season_number
        if episode_number is not None:
            item_kwargs["episode_number"] = episode_number

        item, _ = app.models.Item.objects.get_or_create(
            **item_kwargs,
            defaults={
                "title": metadata["title"],
                "image": metadata["image"],
            },
        )
        return item

    def _get_or_create_tv(self, series_id, tmdb_id, tv_metadata):
        """Get or create the TV instance for a series."""
        if series_id in self.tv_instances:
            return self.tv_instances[series_id]

        item = self._get_or_create_item(MediaTypes.TV.value, tmdb_id, tv_metadata)
        tv_instance = app.models.TV(
            item=item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        tv_instance._history_date = timezone.now()
        self.bulk_media[MediaTypes.TV.value].append(tv_instance)
        self.tv_instances[series_id] = tv_instance
        return tv_instance

    def _get_or_create_season(
        self,
        series_id,
        tmdb_id,
        season_number,
        season_metadata,
        tv_instance,
    ):
        """Get or create the Season instance for a series season."""
        key = (series_id, season_number)
        if key in self.season_instances:
            return self.season_instances[key]

        item = self._get_or_create_item(
            MediaTypes.SEASON.value,
            tmdb_id,
            season_metadata,
            season_number,
        )
        season_instance = app.models.Season(
            item=item,
            user=self.user,
            related_tv=tv_instance,
            status=Status.IN_PROGRESS.value,
        )
        season_instance._history_date = timezone.now()
        self.bulk_media[MediaTypes.SEASON.value].append(season_instance)
        self.season_instances[key] = season_instance
        return season_instance

    def _create_episode(
        self,
        tmdb_id,
        season_number,
        episode_number,
        watched_at,
        season_metadata,
        tv_metadata,
        season_instance,
    ):
        """Create an Episode instance."""
        episode_image = self._get_episode_image(episode_number, season_metadata)
        item = self._get_or_create_item(
            MediaTypes.EPISODE.value,
            tmdb_id,
            {"title": tv_metadata["title"], "image": episode_image},
            season_number,
            episode_number,
        )

        episode_instance = app.models.Episode(
            item=item,
            related_season=season_instance,
            end_date=watched_at,
        )
        episode_instance._history_date = watched_at or timezone.now()
        self.bulk_media[MediaTypes.EPISODE.value].append(episode_instance)

    def _get_episode_image(self, episode_number, season_metadata):
        """Extract the episode image URL from season metadata."""
        for episode in season_metadata["episodes"]:
            if episode["episode_number"] == episode_number and episode.get(
                "still_path",
            ):
                return f"https://image.tmdb.org/t/p/w500{episode['still_path']}"
        return settings.IMG_NONE

    def _update_completion_status(
        self,
        season_instance,
        tv_instance,
        season_number,
        episode_number,
        season_metadata,
        tv_metadata,
    ):
        """Mark the season and show completed when the finale is watched."""
        if episode_number == season_metadata["max_progress"]:
            season_instance.status = Status.COMPLETED.value

            last_season = tv_metadata.get("last_episode_season")
            if last_season and last_season == season_number:
                tv_instance.status = Status.COMPLETED.value

    def _process_watchlist(self):
        """Create planning entries for followed shows without watched episodes."""
        for series_id, meta in self.show_meta.items():
            if not meta.get("is_followed") or series_id in self.tv_instances:
                continue

            tmdb_id = self._map_series(series_id, meta.get("name", series_id))
            if not tmdb_id:
                continue

            if not helpers.should_process_media(
                self.existing_media,
                self.to_delete,
                MediaTypes.TV.value,
                Sources.TMDB.value,
                tmdb_id,
                self.mode,
            ):
                continue

            metadata = self._get_metadata(
                MediaTypes.TV.value,
                tmdb_id,
                meta.get("name", series_id),
            )
            if not metadata:
                continue

            item = self._get_or_create_item(MediaTypes.TV.value, tmdb_id, metadata)
            tv_instance = app.models.TV(
                item=item,
                user=self.user,
                status=Status.PLANNING.value,
            )
            tv_instance._history_date = timezone.now()
            self.bulk_media[MediaTypes.TV.value].append(tv_instance)
            self.tv_instances[series_id] = tv_instance

    def _process_movies(self, files):
        """Import movies from the v1 tracking file.

        TV Time only exports movies with an internal UUID and a title, so they
        are matched to The Movie Database by title (and release year), unlike
        TV shows which carry a TheTVDB id.
        """
        rows = files.get(FILE_WATCHED_V1)
        if not rows:
            return

        watched, planned = self._collect_movies(rows)

        for (title, _year), watched_at in watched.items():
            self._import_movie(title, Status.COMPLETED.value, watched_at)

        for key in planned:
            if key in watched:
                continue
            self._import_movie(key[0], Status.PLANNING.value, None)

    def _collect_movies(self, rows):
        """Split v1 rows into deduplicated watched and watchlisted movies."""
        watched = {}  # (title, year) -> earliest watched_at
        planned = {}  # (title, year) -> None

        for row in rows:
            if (row.get("entity_type") or "").strip() != "movie":
                continue

            title = (row.get("movie_name") or "").strip()
            if not title:
                continue

            key = (title, self._release_year(row.get("release_date")))
            row_type = (row.get("type") or "").strip()

            if row_type == "watch":
                self._record_watched_movie(watched, key, row)
            elif row_type in ("towatch", "follow"):
                planned.setdefault(key, None)

        return watched, planned

    def _record_watched_movie(self, watched, key, row):
        """Store a watched movie, keeping the earliest watch date."""
        watched_at = self._parse_dt(row.get("watch_date") or row.get("created_at"))
        existing = watched.get(key)
        if key not in watched or (existing and watched_at and watched_at < existing):
            watched[key] = watched_at

    def _release_year(self, release_date):
        """Extract the release year from a TV Time release_date value."""
        if not release_date:
            return None

        digits = release_date.strip()[:4]
        return digits if digits.isdigit() else None

    def _import_movie(self, title, status, watched_at):
        """Match a movie to TMDB by title and create a Movie instance."""
        match = self._search_movie(title)
        if not match:
            self.warnings.append(
                f"{title}: could not be matched in {Sources.TMDB.label}.",
            )
            return

        tmdb_id, matched_title, image = match
        if tmdb_id in self.movie_tmdb_ids:
            return

        if not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            MediaTypes.MOVIE.value,
            Sources.TMDB.value,
            tmdb_id,
            self.mode,
        ):
            self.movie_tmdb_ids.add(tmdb_id)
            return

        self.movie_tmdb_ids.add(tmdb_id)

        item = self._get_or_create_item(
            MediaTypes.MOVIE.value,
            tmdb_id,
            {"title": matched_title, "image": image},
        )
        movie_instance = app.models.Movie(
            item=item,
            user=self.user,
            status=status,
            progress=1 if status == Status.COMPLETED.value else 0,
            start_date=watched_at,
            end_date=watched_at,
        )
        movie_instance._history_date = watched_at or timezone.now()
        self.bulk_media[MediaTypes.MOVIE.value].append(movie_instance)

    def _search_movie(self, title):
        """Return (tmdb_id, title, image) for the best TMDB match, or None."""
        try:
            results = services.search(MediaTypes.MOVIE.value, title, 1)["results"]
        except services.ProviderAPIError as error:
            logger.warning("Error searching TMDB for movie %s: %s", title, error)
            return None

        if not results:
            return None

        # Prefer an exact (case-insensitive) title match over TMDB's ordering.
        wanted = title.casefold()
        best = next(
            (r for r in results if r["title"].casefold() == wanted),
            results[0],
        )
        return str(best["media_id"]), best["title"], best["image"]

    def _process_lists(self, files):
        """Import TV Time custom lists as Yamtrack custom lists."""
        rows = files.get(FILE_LISTS)
        if not rows:
            return

        metadata = {}
        list_rows = []
        for row in rows:
            if (row.get("s_key") or "").strip() == "collection":
                metadata = self._parse_list_metadata(row.get("lists"))
            elif (row.get("type") or "").strip() == "list":
                list_rows.append(row)

        for row in list_rows:
            try:
                self._create_list(row, metadata)
            except Exception as error:
                s_key = (row.get("s_key") or "").strip()
                msg = f"Error processing TV Time list: {s_key}"
                raise MediaImportUnexpectedError(msg) from error

    def _create_list(self, row, metadata):
        """Create a single custom list and add its resolvable items."""
        s_key = (row.get("s_key") or "").strip()
        meta = metadata.get(s_key, {})
        name = meta.get("name") or (row.get("name") or "").strip() or s_key

        items = self._parse_list_items(row.get("objects"))
        resolved = []
        skipped_movies = 0
        for entry_type, tvdb_id in items:
            if entry_type != "series":
                skipped_movies += 1
                continue
            item = self._list_series_item(tvdb_id)
            if item:
                resolved.append(item)

        if skipped_movies:
            self.warnings.append(
                f"{name}: skipped {skipped_movies} list movie(s) that TV Time "
                "exported without a usable identifier.",
            )

        if not resolved:
            return

        custom_list, created = CustomList.objects.get_or_create(
            owner=self.user,
            name=name,
            defaults={"description": meta.get("description", "")},
        )
        if created:
            self.lists_created += 1

        for item in resolved:
            CustomListItem.objects.get_or_create(custom_list=custom_list, item=item)

    def _list_series_item(self, tvdb_id):
        """Resolve a TheTVDB series id from a list to a TMDB TV Item."""
        name = self.show_meta.get(tvdb_id, {}).get("name", tvdb_id)
        tmdb_id = self._map_series(tvdb_id, name)
        if not tmdb_id:
            return None

        metadata = self._get_metadata(MediaTypes.TV.value, tmdb_id, name)
        if not metadata:
            return None

        return self._get_or_create_item(MediaTypes.TV.value, tmdb_id, metadata)

    def _parse_list_metadata(self, blob):
        """Parse the ``collection`` row into ``{s_key: {name, description}}``.

        The value is a Go map dump where each list's keys are printed in sorted
        order (``description`` before ``name`` before ``s_key``), so each field
        can be pulled out positionally and zipped back together by list.
        """
        blob = blob or ""
        names = re.findall(r"name:(.*?) order:", blob)
        descriptions = re.findall(r"description:(.*?) fanart:", blob)
        s_keys = re.findall(r"s_key:(\S+)", blob)

        metadata = {}
        for index, s_key in enumerate(s_keys):
            raw_description = descriptions[index] if index < len(descriptions) else ""
            description = raw_description.strip()
            if description == "<nil>":
                description = ""
            metadata[s_key] = {
                "name": names[index].strip() if index < len(names) else "",
                "description": description,
            }
        return metadata

    def _parse_list_items(self, blob):
        """Parse a list's ``objects`` blob into ``(type, tvdb_id)`` tuples."""
        items = []
        for inner in _GO_MAP_BLOCK.findall(blob or ""):
            entry_type = self._go_token(inner, "type")
            if entry_type == "series":
                items.append(("series", self._go_token(inner, "id")))
            elif entry_type == "movie":
                items.append(("movie", None))
        return items

    def _go_token(self, inner, key):
        """Extract a single-token value for ``key`` from a Go map body."""
        match = re.search(rf"(?:^|\s){key}:(\S+)", inner)
        return match.group(1) if match else None
