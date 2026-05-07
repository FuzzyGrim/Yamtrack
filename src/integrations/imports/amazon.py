import datetime
import logging
import re
from collections import defaultdict
from csv import DictReader

from django.apps import apps
from django.db.models import Count, Prefetch
from django.utils import timezone

from app.models import MediaTypes, Sources, Status
from app.providers.services import ProviderAPIError, get_media_metadata, search
from app.providers.tmdb import movie as tmdb_movie
from integrations.imports import helpers
from integrations.imports.helpers import MediaImportError

logger = logging.getLogger(__name__)

AMAZON_TYPE_MAPPING = {
    "Movie": MediaTypes.MOVIE,
    "Series": MediaTypes.TV,
}


def importer(file, user, mode):
    """Import media from Amazon CSV file."""
    amazon_importer = AmazonImporter(file, user, mode)
    return amazon_importer.import_data()


###
# amazon prime watch history csv:
# should be exported with langauage set to english, as csv,
# with unix timestamps (epoch ms)
#
# relevant columns format:
# Column	Description
# Date Watched	When the item was watched. e.g 1771455569000
# Type	Whether the item is a 'movie' or a 'series' episode.
# Title	The movie title or series name. e.g 'Two and a Half Men, Season 11'
# Episode Title    The episode title (empty for movies).
# e.g. 'Episode 21: Dial 1-900-mix-a-lot'
# ##
class AmazonImporter:
    """Class to handle importing user data from Amazon CSV."""

    def __init__(self, file, user, mode):
        """Initialize the Amazon importer with file, user, and mode."""
        self.file = file
        self.user = user
        self.mode = mode
        self.warnings = []

        # Fast in-memory lookups to avoid per-row DB queries.
        self.existing_media = helpers.get_existing_media(user)

        # helpers.get_existing_media intentionally excludes seasons/episodes.
        # Episode imports need to create/link seasons efficiently.
        season_model = apps.get_model(
            app_label="app", model_name=MediaTypes.SEASON.value
        )
        self.existing_seasons = {
            (s.item.media_id, s.item.season_number): s
            for s in season_model.objects.filter(user=user).select_related(
                "item",
                "related_tv",
                "related_tv__item",
            )
        }

        self.to_delete = defaultdict(lambda: defaultdict(set))
        self.bulk_media = defaultdict(list)

        # Per-run caches to avoid repeated DB/API work in tight loops.
        self._item_cache = {}
        self._queued_tv_by_media_id = {}
        self._queued_seasons_by_key = {}

        # Avoid O(n) scans of `self.bulk_media[media_type]` to detect duplicates.
        # Keyed by `Item.pk` (which is stable because we use update_or_create).
        self._queued_item_ids_by_media_type = defaultdict(set)
        self._queued_instance_by_media_type_and_item_id = defaultdict(dict)

        self._affected_tv_media_ids = set()
        self._affected_season_keys = set()

        logger.info(
            "Initialized Amazon importer for user %s with mode %s",
            user.username,
            mode,
        )

    def import_data(self):
        """Import all user data from CSV."""
        try:
            # Decode the file line by line to save memory
            decoded_file = (line.decode("utf-8") for line in self.file)
            reader = DictReader(decoded_file)
        except UnicodeDecodeError as e:
            msg = "Invalid file format. Please upload a CSV file."
            raise MediaImportError(msg) from e

        logger.info("amazon importer started")
        processed_rows = 0
        failed_rows = 0
        for row in reader:
            # Skip empty lines/rows
            if not any((v or "").strip() for v in row.values()):
                continue

            processed_rows += 1
            try:
                self._process_row(row)
            except Exception as error:  # noqa: BLE001
                failed_rows += 1
                logger.warning(
                    "Error processing entry: %r\n%r",
                    row,
                    error,
                )

        logger.info("processed %d rows", processed_rows)
        if failed_rows > 0:
            logger.warning("%d failed rows", failed_rows)
        helpers.cleanup_existing_media(self.to_delete, self.user)
        helpers.bulk_create_media(self.bulk_media, self.user)

        # --- Post-import: update season and show completion status ---
        self._update_season_and_show_status()

        imported_counts = {
            media_type: len(media_list)
            for media_type, media_list in self.bulk_media.items()
        }
        return imported_counts, None

    def _update_season_and_show_status(self):
        """Update season/show completion status for TV affected by this import.

        We only update seasons/shows touched by the current import and we batch
        TMDB lookups per show via `tv_with_seasons`.
        """
        if not self._affected_tv_media_ids and not self._affected_season_keys:
            logger.info(
                "No affected seasons detected from this import; skipping status update",
            )
            return

        season_model = apps.get_model(
            app_label="app", model_name=MediaTypes.SEASON.value
        )
        episode_model = apps.get_model(
            app_label="app", model_name=MediaTypes.EPISODE.value
        )
        tv_model = apps.get_model(app_label="app", model_name=MediaTypes.TV.value)

        affected_tv_media_ids, affected_season_keys = self._get_affected_targets()

        seasons = self._fetch_affected_seasons(season_model, affected_season_keys)
        if seasons:
            episode_counts = self._get_episode_counts_for_seasons(
                episode_model, seasons
            )
            tmdb_metadata_by_show = self._fetch_tmdb_metadata_by_show(seasons)
            self._update_season_statuses(seasons, episode_counts, tmdb_metadata_by_show)

        if affected_tv_media_ids:
            self._update_tv_statuses(tv_model, season_model, affected_tv_media_ids)

    def _get_affected_targets(self):
        affected_season_keys = {
            (media_id, season_number)
            for (media_id, season_number) in self._affected_season_keys
            if season_number is not None
        }
        affected_tv_media_ids = set(self._affected_tv_media_ids) | {
            media_id for (media_id, _) in affected_season_keys
        }
        return affected_tv_media_ids, affected_season_keys

    def _fetch_affected_seasons(self, season_model, affected_season_keys):
        if not affected_season_keys:
            return []

        affected_media_ids = {media_id for (media_id, _) in affected_season_keys}
        affected_season_numbers = {
            season_number for (_, season_number) in affected_season_keys
        }

        seasons_qs = season_model.objects.filter(
            user=self.user,
            item__source=Sources.TMDB.value,
            item__media_id__in=affected_media_ids,
            item__season_number__in=affected_season_numbers,
        ).select_related(
            "item",
            "related_tv",
            "related_tv__item",
        )

        return [
            s
            for s in seasons_qs
            if (s.item.media_id, s.item.season_number) in affected_season_keys
        ]

    def _get_episode_counts_for_seasons(self, episode_model, seasons):
        counts_qs = (
            episode_model.objects.filter(related_season__in=seasons)
            .values("related_season_id")
            .annotate(c=Count("id"))
        )
        return {row["related_season_id"]: row["c"] for row in counts_qs}

    def _fetch_tmdb_metadata_by_show(self, seasons):
        seasons_by_show = defaultdict(set)
        for season_obj in seasons:
            seasons_by_show[season_obj.item.media_id].add(season_obj.item.season_number)

        tmdb_metadata_by_show = {}
        for media_id, season_numbers in seasons_by_show.items():
            try:
                tmdb_metadata_by_show[media_id] = get_media_metadata(
                    "tv_with_seasons",
                    media_id,
                    Sources.TMDB.value,
                    season_numbers=sorted(season_numbers),
                )
            except ProviderAPIError as e:
                logger.warning(
                    "Could not fetch TMDB metadata for tv media_id=%s seasons=%s: %r",
                    media_id,
                    sorted(season_numbers),
                    e,
                )
                tmdb_metadata_by_show[media_id] = {}
        return tmdb_metadata_by_show

    def _update_season_statuses(self, seasons, episode_counts, tmdb_metadata_by_show):
        for season_obj in seasons:
            tv_media_id = season_obj.item.media_id
            season_number = season_obj.item.season_number
            season_key = f"season/{season_number}"

            show_md = tmdb_metadata_by_show.get(tv_media_id) or {}
            season_md = show_md.get(season_key)
            if not season_md:
                logger.warning(
                    "Missing %s in TMDB metadata for tv media_id=%s; skipping",
                    season_key,
                    tv_media_id,
                )
                continue

            expected_count = season_md.get("details", {}).get("episodes")
            if expected_count is None:
                logger.warning(
                    "TMDB metadata missing episode count for tv media_id=%s %s; "
                    "skipping",
                    tv_media_id,
                    season_key,
                )
                continue

            actual_count = episode_counts.get(season_obj.id, 0)
            target_status = (
                Status.COMPLETED.value
                if actual_count == expected_count
                else Status.IN_PROGRESS.value
            )

            if season_obj.status != target_status:
                season_obj.status = target_status
                season_obj.save(update_fields=["status"])

    def _update_tv_statuses(self, tv_model, season_model, affected_tv_media_ids):
        tv_qs = (
            tv_model.objects.filter(
                user=self.user,
                item__source=Sources.TMDB.value,
                item__media_id__in=affected_tv_media_ids,
            )
            .select_related("item")
            .prefetch_related(
                Prefetch(
                    "seasons",
                    queryset=season_model.objects.select_related("item"),
                )
            )
        )

        for tv_obj in tv_qs:
            seasons = list(tv_obj.seasons.all())
            target_status = (
                Status.COMPLETED.value
                if seasons
                and all(season.status == Status.COMPLETED.value for season in seasons)
                else Status.IN_PROGRESS.value
            )

            if tv_obj.status != target_status:
                tv_obj.status = target_status
                tv_obj.save(update_fields=["status"])

    def _parse_date_watched(self, date_watched_raw):
        """Parse the date watched from Amazon CSV, return aware datetime or None."""
        if not date_watched_raw:
            return None
        try:
            dt = datetime.datetime.fromtimestamp(
                int(date_watched_raw) / 1000, tz=datetime.UTC
            )
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt, timezone.get_current_timezone())
        except (ValueError, OSError, OverflowError) as e:
            logger.warning("Could not parse date_watched '%s': %r", date_watched_raw, e)
            return None
        else:
            return dt

    def _ensure_tv_and_season(self, tmdb_data):
        """Ensure TV and Season exist for episode imports.

        This method runs in the per-row loop, so it must avoid DB queries like
        `.exists()`/`.first()` on every call.
        """
        tv_media_id = str(tmdb_data["media_id"])

        # 1) Ensure TV exists
        tv_tmdb_data = {
            "media_id": tv_media_id,
            "media_type": MediaTypes.TV.value,
            "title": tmdb_data["title"],
            "image": tmdb_data.get("image"),
        }
        tv_item, _ = self._create_or_update_item(tv_tmdb_data, MediaTypes.TV.value)
        tv_model = apps.get_model(app_label="app", model_name=MediaTypes.TV.value)

        tv_instance = self._queued_tv_by_media_id.get(tv_media_id)
        if tv_instance is None:
            tv_instance = self.existing_media[MediaTypes.TV.value][
                Sources.TMDB.value
            ].get(
                tv_media_id,
            )

        if tv_instance is None:
            tv_instance = tv_model(
                item=tv_item,
                user=self.user,
                status=Status.IN_PROGRESS.value,
                score=None,
            )
            self.bulk_media[MediaTypes.TV.value].append(tv_instance)
            self._queued_tv_by_media_id[tv_media_id] = tv_instance
            if tv_item.pk is not None:
                self._queued_item_ids_by_media_type[MediaTypes.TV.value].add(tv_item.pk)
                self._queued_instance_by_media_type_and_item_id[MediaTypes.TV.value][
                    tv_item.pk
                ] = tv_instance

            # Make it discoverable for subsequent rows in this run.
            self.existing_media[MediaTypes.TV.value][Sources.TMDB.value][
                tv_media_id
            ] = tv_instance

        # 2) Ensure Season exists
        season_number = tmdb_data.get("season_number")
        if season_number is None:
            logger.warning(
                "Episode row missing season_number for tv media_id=%s; "
                "cannot ensure season",
                tv_media_id,
            )
            return

        season_tmdb_data = {
            "media_id": tv_media_id,
            "media_type": MediaTypes.SEASON.value,
            "season_number": season_number,
            "title": tmdb_data.get("season_title") or tmdb_data["title"],
            "image": tmdb_data.get("image"),
        }
        season_item, _ = self._create_or_update_item(
            season_tmdb_data,
            MediaTypes.SEASON.value,
        )

        season_key = (tv_media_id, season_number)
        if season_key in self._queued_seasons_by_key:
            return
        if season_key in self.existing_seasons:
            return

        season_model = apps.get_model(
            app_label="app", model_name=MediaTypes.SEASON.value
        )
        season_instance = season_model(
            item=season_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            score=None,
            related_tv=tv_instance,
        )
        self.bulk_media[MediaTypes.SEASON.value].append(season_instance)
        self._queued_seasons_by_key[season_key] = season_instance
        if season_item.pk is not None:
            self._queued_item_ids_by_media_type[MediaTypes.SEASON.value].add(
                season_item.pk,
            )
            self._queued_instance_by_media_type_and_item_id[MediaTypes.SEASON.value][
                season_item.pk
            ] = season_instance

    def _process_row(self, row):
        logger.info("amazon importer: processing row:\n%s", row)
        media_type = AMAZON_TYPE_MAPPING.get(row.get("Type", ""))
        logger.info("type: %s", media_type)
        title = row.get("Title", "").strip()
        episode_title = row.get("Episode Title", "").strip()
        date_watched_raw = row.get("Date Watched", "").strip()
        date_watched = self._parse_date_watched(date_watched_raw)

        if not media_type:
            logger.warning(
                "%s: Unknown or unsupported type '%s' - skipped",
                title,
                row.get("Type", ""),
            )
            return

        tmdb_data = self._lookup_in_tmdb(media_type, title, episode_title)
        if not tmdb_data:
            logger.warning(
                "%s / %s: Couldn't find a match in %s",
                title,
                episode_title,
                Sources(Sources.TMDB).label,
            )
            return

        # Use the resolved media_type from TMDB data (important for episodes)
        resolved_media_type = tmdb_data.get("media_type", media_type)

        # Only process if not a duplicate (matches IMDB logic)
        if not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            resolved_media_type,
            Sources.TMDB.value,
            str(tmdb_data["media_id"]),
            self.mode,
        ):
            return

        # --- Ensure TV and Season exist for episodes ---
        if resolved_media_type == MediaTypes.EPISODE.value:
            tv_media_id = str(tmdb_data["media_id"])
            self._affected_tv_media_ids.add(tv_media_id)
            season_number = tmdb_data.get("season_number")
            if season_number is not None:
                self._affected_season_keys.add((tv_media_id, season_number))
            self._ensure_tv_and_season(tmdb_data)

        item, _ = self._create_or_update_item(tmdb_data, resolved_media_type)

        # Prevent duplicates in the current batch in O(1).
        # If the same item appears multiple times in the CSV, keep the latest watch
        # date we see.
        if item.pk is not None and (
            item.pk in self._queued_item_ids_by_media_type[resolved_media_type]
        ):
            queued = self._queued_instance_by_media_type_and_item_id[
                resolved_media_type
            ].get(
                item.pk,
            )
            if (
                queued is not None
                and date_watched
                and hasattr(queued, "end_date")
                and (queued.end_date is None or date_watched > queued.end_date)
            ):
                queued.end_date = date_watched
            logger.info(
                "Skipping duplicate in current batch for user %s, item %s",
                self.user,
                item,
            )
            return

        instance = self._create_media_instance(item, resolved_media_type, date_watched)
        self.bulk_media[resolved_media_type].append(instance)

        if item.pk is not None:
            self._queued_item_ids_by_media_type[resolved_media_type].add(item.pk)
            self._queued_instance_by_media_type_and_item_id[resolved_media_type][
                item.pk
            ] = instance

    def _lookup_in_tmdb(self, media_type, title, episode_title=None):
        try:
            if media_type == MediaTypes.MOVIE:
                return self._lookup_movie_in_tmdb(title)
            if media_type == MediaTypes.TV:
                return self._lookup_series_in_tmdb(title, episode_title or "")
        except ProviderAPIError as e:
            logger.warning(
                "Error looking up '%s' in TMDB: %r",
                title,
                e,
            )
        return None

    def _lookup_movie_in_tmdb(self, title):
        year = None
        match = re.search(r"\((\d{4})\)", title)
        search_title = title
        if match:
            year = match.group(1)
            search_title = title[: match.start()].strip()
        response = search(MediaTypes.MOVIE.value, search_title, 1)
        results = response.get("results", [])

        logger.info("found %d results for movie search of %s", len(results), title)

        best = None
        for movie in results:
            if movie["title"].strip().lower() == search_title.strip().lower():
                if year:
                    details = tmdb_movie(movie["media_id"])
                    release_date = details.get("release_date", "")
                    if release_date.startswith(year):
                        best = movie
                        break
                else:
                    best = movie
                    break
        if not best and results:
            best = results[0]
        if best:
            logger.info("using match %r", best)
            return {
                "media_id": best["media_id"],
                "title": best["title"],
                "image": best.get("image"),
                "media_type": MediaTypes.MOVIE.value,
            }
        return None

    series_title_regex = re.compile(r"(?P<title>.+?)[\s\-,]+Season (?P<number>\d+)")
    episode_title_regex = re.compile(r"Episode (?P<number>\d+): (?P<title>.+)")

    def extract_episode_info(
        self, title: str, episode_title: str
    ) -> tuple[str | None, int | None, int | None, str | None]:
        """Parse Series Title and Season Number."""
        m = self.series_title_regex.match(title)
        d = m.groupdict() if m else {}
        show_title = d.get("title")
        season_raw = d.get("number") or ""
        season_number = int(season_raw) if season_raw.isdigit() else None
        # Parse Episode Number and Episode Title
        m = self.episode_title_regex.match(episode_title)
        d = m.groupdict() if m else {}
        episode_title_str = d.get("title")
        ep_num_raw = d.get("number") or ""
        episode_number = int(ep_num_raw) if ep_num_raw.isdigit() else None
        return (show_title, season_number, episode_number, episode_title_str)

    def _lookup_series_in_tmdb(self, title: str, episode_title: str):
        show_title, season_number, episode_number, _ = self.extract_episode_info(
            title, episode_title
        )
        response = search(MediaTypes.TV.value, show_title, 1)
        results = response.get("results", [])

        logger.info(
            "found %d results for series search of %s | %s",
            len(results),
            title,
            episode_title,
        )
        for show in results:
            logger.info("found show: %s", show)

        best = None
        for show in results:
            if show["title"].strip().lower() == (show_title or title).strip().lower():
                best = show
                logger.info("title match: using %s", best)
                break
        if not best and results:
            best = results[0]
            logger.info("no title match: using first entry %s", best)
        if not best:
            logger.warning("no result at all for %s %s", title, episode_title)
            return None

        media_id = best["media_id"]
        episode_metadata = (
            get_media_metadata(
                MediaTypes.EPISODE.value,
                media_id,
                Sources.TMDB.value,
                season_numbers=[season_number],
                episode_number=episode_number,
            )
            or {}
        )

        return {
            "media_id": media_id,
            "media_type": MediaTypes.EPISODE.value,
            "season_number": season_number,
            "episode_number": episode_number,
            "title": best["title"],
            "image": episode_metadata.get("image") or best.get("image"),
            "season_title": episode_metadata.get("season_title"),
            "episode_title": episode_metadata.get("episode_title"),
        }

    def _create_or_update_item(self, tmdb_data, media_type):
        logger.info(
            "creating item for type '%s' from tmdb data %s", media_type, tmdb_data
        )
        item_model = apps.get_model(app_label="app", model_name="item")

        cache_key = (
            Sources.TMDB.value,
            media_type,
            str(tmdb_data["media_id"]),
            tmdb_data.get("season_number"),
            tmdb_data.get("episode_number"),
        )
        if cache_key in self._item_cache:
            return self._item_cache[cache_key], False

        item, created = item_model.objects.update_or_create(
            media_id=str(tmdb_data["media_id"]),
            source=Sources.TMDB.value,
            media_type=media_type,
            season_number=tmdb_data.get("season_number"),
            episode_number=tmdb_data.get("episode_number"),
            defaults={
                "title": tmdb_data["title"],
                "image": tmdb_data["image"],
            },
        )
        self._item_cache[cache_key] = item
        return item, created

    def _create_media_instance(self, item, media_type, date_watched=None):
        logger.info("creating instance for type '%s' from item %s", media_type, item)
        model = apps.get_model(app_label="app", model_name=media_type)
        if media_type == "episode":
            # Only set item and end_date for Episode
            instance = model(item=item)
            if date_watched:
                instance.end_date = date_watched
            return instance
        params = {
            "item": item,
            "user": self.user,
            "score": None,  # Amazon does not provide ratings
            "status": Status.COMPLETED.value,  # Could infer from context if needed
        }
        instance = model(**params)
        if date_watched and hasattr(instance, "end_date"):
            instance.end_date = date_watched
        return instance
