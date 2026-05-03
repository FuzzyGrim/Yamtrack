import datetime
import logging
import re
from collections import defaultdict
from csv import DictReader

from django.apps import apps

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
# should be exported with langauage set to english, as csv, with unix timestamps (epoch ms)
#
# relevant columns format:
# Column	Description
# Date Watched	When the item was watched. e.g 1771455569000
# Type	Whether the item is a 'movie' or a 'series' episode.
# Title	The movie title or series name. e.g 'Two and a Half Men, Season 11'
# Episode Title	The episode title (empty for movies). e.g. 'Episode 21: Dial 1-900-mix-a-lot'
# ##
class AmazonImporter:
    """Class to handle importing user data from Amazon CSV."""

    def __init__(self, file, user, mode):
        """Initialize the Amazon importer with file, user, and mode."""
        self.file = file
        self.user = user
        self.mode = mode
        self.warnings = []
        self.existing_media = helpers.get_existing_media(user)
        self.to_delete = defaultdict(lambda: defaultdict(set))
        self.bulk_media = defaultdict(list)
        logger.info(
            "Initialized Amazon importer for user %s with mode %s",
            user.username,
            mode,
        )

    def import_data(self):
        """Import all user data from CSV."""
        try:
            decoded_file = self.file.read().decode("utf-8").splitlines()
        except UnicodeDecodeError as e:
            msg = "Invalid file format. Please upload a CSV file."
            raise MediaImportError(msg) from e

        reader = DictReader(decoded_file)
        rows = list(reader)
        logger.info("amazon importer started with %d lines", len(rows))
        for row in rows:
            try:
                self._process_row(row)
            except Exception as error:  # noqa: BLE001
                logger.warning(
                    "Error processing entry: %r\n%r",
                    row,
                    error,
                )
        logger.info("processed %d rows", len(rows))
        helpers.cleanup_existing_media(self.to_delete, self.user)
        helpers.bulk_create_media(self.bulk_media, self.user)

        imported_counts = {
            media_type: len(media_list)
            for media_type, media_list in self.bulk_media.items()
        }
        return imported_counts, None

    def _process_row(self, row):
        logger.info("amazon importer: processing row:\n%s", row)
        media_type = AMAZON_TYPE_MAPPING.get(row.get("Type", ""))
        logger.info("type: %s", media_type)
        title = row.get("Title", "").strip()
        episode_title = row.get("Episode Title", "").strip()
        date_watched_raw = row.get("Date Watched", "").strip()
        date_watched = None
        if date_watched_raw:
            try:
                # Amazon exports as epoch ms
                from django.utils import timezone

                dt = datetime.datetime.fromtimestamp(int(date_watched_raw) / 1000)
                if timezone.is_naive(dt):
                    date_watched = timezone.make_aware(
                        dt, timezone.get_current_timezone()
                    )
                else:
                    date_watched = dt
            except Exception as e:
                logger.warning(
                    "Could not parse date_watched '%s': %r", date_watched_raw, e
                )

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

        item, _ = self._create_or_update_item(tmdb_data, resolved_media_type)
        instance = self._create_media_instance(item, resolved_media_type, date_watched)
        # Prevent duplicate user/item pairs in current batch
        already_queued = any(
            queued_instance.item == item and queued_instance.user == self.user
            for queued_instance in self.bulk_media[resolved_media_type]
        )
        if already_queued:
            logger.info(
                "Skipping duplicate in current batch for user %s, item %s",
                self.user,
                item,
            )
            return
        self.bulk_media[resolved_media_type].append(instance)

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
        match = re.search(r"\\((\\d{4})\\)", title)
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

    seriesTitleRegex = re.compile(r"(?P<title>.+?)[\s\-,]+Season (?P<number>\d+)")
    episodeTitleRegex = re.compile(r"Episode (?P<number>\d+): (?P<title>.+)")

    def extract_episode_info(
        self, title: str, episode_title: str
    ) -> tuple[str | None, int | None, int | None, str | None]:
        """Parse Series Title and Season Number."""
        m = self.seriesTitleRegex.match(title)
        d = m.groupdict() if m else {}
        show_title = d.get("title")
        season_raw = d.get("number") or ""
        season_number = int(season_raw) if season_raw.isdigit() else None
        # Parse Episode Number and Episode Title
        m = self.episodeTitleRegex.match(episode_title)
        d = m.groupdict() if m else {}
        episode_title_str = d.get("title")
        ep_num_raw = d.get("number") or ""
        episode_number = int(ep_num_raw) if ep_num_raw.isdigit() else None
        return (show_title, season_number, episode_number, episode_title_str)

    def _lookup_series_in_tmdb(self, title: str, episode_title: str):
        showTitle, seasonNumber, episodeNumber, episodeTitle = (
            self.extract_episode_info(title, episode_title)
        )
        response = search(MediaTypes.TV.value, showTitle, 1)
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
            if show["title"].strip().lower() == (showTitle or title).strip().lower():
                best = show
                logger.info("title match: using %s", best)
                break
        if not best and results:
            best = results[0]
            logger.info("no title match: using first entry %s", best)
        if not best:
            logger.warning("no result at all for %s %s", title, episode_title)
            return None

        mediaId = best["media_id"]
        episodeMetadata = (
            get_media_metadata(
                MediaTypes.EPISODE.value,
                mediaId,
                Sources.TMDB.value,
                season_numbers=[seasonNumber],
                episode_number=episodeNumber,
            )
            or {}
        )

        return {
            "media_id": mediaId,
            "media_type": MediaTypes.EPISODE.value,
        } | episodeMetadata

    def _create_or_update_item(self, tmdb_data, media_type):
        logger.info(
            "creating item for type '%s' from tmdb data %s", media_type, tmdb_data
        )
        item_model = apps.get_model(app_label="app", model_name="item")
        return item_model.objects.update_or_create(
            media_id=tmdb_data["media_id"],
            source=Sources.TMDB.value,
            media_type=media_type,
            season_number=tmdb_data.get("season_number"),
            episode_number=tmdb_data.get("episode_number"),
            defaults={
                "title": tmdb_data["title"],
                "image": tmdb_data["image"],
            },
        )

    def _create_media_instance(self, item, media_type, date_watched=None):
        logger.info("creating instance for type '%s' from item %s", media_type, item)
        model = apps.get_model(app_label="app", model_name=media_type)
        params = {
            "item": item,
            "user": self.user,
            "score": None,  # Amazon does not provide ratings
            "status": Status.COMPLETED.value,  # Could infer from context if needed
        }
        instance = model(**params)
        # Set watched date appropriately
        if date_watched:
            if hasattr(instance, "end_date"):
                instance.end_date = date_watched
            elif hasattr(instance, "date_watched"):
                instance.date_watched = date_watched
        return instance
