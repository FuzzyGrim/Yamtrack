import io
import logging
import re
from collections import defaultdict
from csv import DictReader
from dataclasses import dataclass
from datetime import datetime

from django.apps import apps
from django.utils import timezone

import app.forms
from app.models import MediaTypes, Sources, Status
from app.providers import services
from app.providers.services import ProviderAPIError
from app.providers.tmdb import get_image_url
from integrations.imports import helpers
from integrations.imports.helpers import MediaImportError, MediaImportUnexpectedError

logger = logging.getLogger(__name__)


@dataclass
class TMDBInfo:
    """Data container for hierarchical media information retrieved from TMDB."""

    media_id: str
    season_cover: str
    num_episodes: int
    episode_number: int
    episode_cover: str


def importer(file, user, mode):
    """Import media from CSV file using the class-based importer."""
    csv_importer = NetflixImporter(file, user, mode)
    return csv_importer.import_data()


def _parse_date(date_str):
    if not date_str:
        return None

    try:
        date = datetime.strptime(date_str.strip(), "%m/%d/%y").astimezone()
    except ValueError:
        logger.warning("Could not parse date: %s", date_str)
        return None

    return date.replace(
        hour=0,
        minute=0,
        second=0,
    )


def _series_completion_status(num_seasons, seasons):
    if len(seasons) < num_seasons:
        return Status.IN_PROGRESS.value

    for season in seasons:
        if len(season["watched_episodes"]) < season["number_of_episodes"]:
            return Status.IN_PROGRESS.value

    return Status.COMPLETED.value


class NetflixImporter:
    """Class to handle importing Netflix watch history from CSV files."""

    def __init__(self, file, user, mode):
        """Initialize the importer with file, user, and mode.

        Args:
            file: Uploaded CSV file object
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

        # map for TMDb show IDs to avoid repeated lookups
        self.series_title_id_map = defaultdict(list)

        # data structure to manage identified media
        self.identified_media = defaultdict(dict)

        logger.info(
            "Initialized Netflix watch history CSV importer for user %s with mode %s",
            user.username,
            mode,
        )

    def import_data(self):
        """Import all Netflix watch history data from the CSV file."""
        try:
            text_stream = io.TextIOWrapper(self.file, encoding="utf-8", newline="")
        except UnicodeDecodeError as e:
            msg = "Invalid file format. Please upload a CSV file."
            raise MediaImportError(msg) from e

        reader = DictReader(text_stream)

        # match netflix watch history to exact TMDB entries
        for row in reader:
            try:
                self._map_title_to_tmdb_id(row)
            except services.ProviderAPIError as error:
                error_msg = f"Error processing entry with ID {row['Title']} \n {error}"
                self.warnings.append(error_msg)
                continue
            except Exception as error:
                error_msg = f"Error processing entry: {row}"
                raise MediaImportUnexpectedError(error_msg) from error

        # process identified media
        for media_id, media in self.identified_media.items():
            try:
                self._process_identified_media(media_id, media)
            except Exception as error:
                error_msg = f"Error processing entry: {media}"
                logger.exception(error_msg)
                self.warnings.append(f"{error_msg}. Error: {error}")

        helpers.cleanup_existing_media(self.to_delete, self.user)
        helpers.bulk_create_media(self.bulk_media, self.user)

        deduplicated_messages = "\n".join(self.warnings)

        imported_counts = {
            media_type: len(media_list)
            for media_type, media_list in self.bulk_media.items()
        }

        return imported_counts, deduplicated_messages

    def _map_title_to_tmdb_id(self, row):
        title_string = row.get("Title", "").strip()
        date = row.get("Date", "").strip()

        if title_string.startswith(":"):
            # handles the case ": episode title"
            # no information available because it was deleted from Netflix
            self.warnings.append(
                f"'{title_string}, {date}' information removed by Netflix"
            )
            return

        regex_match = re.search(
            r"^(?P<show>.+?):\s*Season\s*(?P<season>\d+):\s*(?P<title>.*)$",
            title_string,
        )

        if regex_match:
            # handles the case "show: Season X: episode title"
            show, season_number, episode_name = regex_match.groups()
            self._create_tv_series_entry(show, int(season_number), episode_name, date)
            return

        # handles the case "movie title" or "show: episode title"
        try:
            tmdb_response = app.providers.tmdb.search(
                MediaTypes.MOVIE.value, title_string, 1
            )
        except ProviderAPIError as e:
            logger.warning("Error looking up %s in TMDB: %s", title_string, e)
            return

        if len(tmdb_response["results"]) == 0:
            # case no movie found, potentially the "show: episode title" format
            if ":" not in title_string:
                # does not match the "show: episode title" format
                self.warnings.append(
                    f"'{title_string}' could not identify movie. Watched on {date}"
                )
                return

            show, episode_name = map(str.strip, title_string.split(":", 1))
            self._create_tv_series_entry(show, 1, episode_name, date)

        elif len(tmdb_response["results"]) == 1:
            # case one movie identified
            media_id = tmdb_response["results"][0]["media_id"]
            if media_id not in self.identified_media:
                self.identified_media[media_id] = {
                    "title": title_string,
                    "date": date,
                    "media_type": MediaTypes.MOVIE,
                    "image": tmdb_response["results"][0]["image"],
                }

        else:
            # case multiple movies found
            self.warnings.append(
                f"'{title_string}' is ambiguous, multiple movies found. Watched {date}"
            )

    def _create_tv_series_entry(self, title, season_number, episode_name, date):
        info = self._get_series_info_from_tmdb(title, season_number, episode_name)

        if not info:
            return

        if info.media_id not in self.identified_media:
            try:
                series = app.providers.tmdb.tv(info.media_id)
            except ProviderAPIError as e:
                logger.warning("Error looking up %s, in TMDB: %s", title, e)
                return

            self.identified_media[info.media_id] = {
                "title": title,
                "media_type": MediaTypes.TV,
                "image": series["image"],
                "number_of_seasons": series["details"]["seasons"],
                "seasons": [],
            }

        seasons_list = self.identified_media[info.media_id]["seasons"]
        season_entry = next(
            (
                s
                for s in self.identified_media[info.media_id]["seasons"]
                if s["season_number"] == season_number
            ),
            None,
        )

        if not season_entry:
            season_entry = {
                "season_number": season_number,
                "image": info.season_cover,
                "number_of_episodes": info.num_episodes,
                "watched_episodes": [],
            }
            seasons_list.append(season_entry)

        season_entry["watched_episodes"].append(
            {
                "episode_name": episode_name,
                "episode_number": info.episode_number,
                "image": info.episode_cover,
                "date": date,
            }
        )

    def _get_series_info_from_tmdb(
        self, series_title, season_number, episode_name
    ) -> TMDBInfo | None:
        if not self.series_title_id_map[series_title]:
            try:
                tmdb_response = app.providers.tmdb.search(
                    MediaTypes.TV.value, series_title, 1
                )
            except ProviderAPIError as e:
                logger.warning("Error looking up %s, in TMDB: %s", series_title, e)
                return None

            if len(tmdb_response["results"]) == 1:
                # Assume single match is correct, even if imperfect.
                self.series_title_id_map[series_title].append(
                    tmdb_response["results"][0]["media_id"]
                )
            else:
                for show in tmdb_response["results"]:
                    if show["title"] == series_title:
                        self.series_title_id_map[series_title].append(show["media_id"])

        for media_id in self.series_title_id_map[series_title]:
            try:
                response = app.providers.tmdb.tv_with_seasons(media_id, [season_number])
            except ProviderAPIError as e:
                logger.warning(
                    "Error looking up season %s of %s, in TMDB: %s",
                    season_number,
                    media_id,
                    e,
                )
                break

            season = response["season/" + str(season_number)]

            for episode in season["episodes"]:
                if episode["name"] == episode_name:
                    return TMDBInfo(
                        media_id=media_id,
                        season_cover=season["image"],
                        num_episodes=season["details"]["episodes"],
                        episode_number=episode["episode_number"],
                        episode_cover=get_image_url(episode["still_path"]),
                    )

        self.warnings.append(
            f"No exact match found: '{episode_name}' ({series_title} S{season_number})"
        )
        return None

    def _process_identified_media(self, media_id, media):
        media_type = media["media_type"]
        title = media["title"]

        if media_type is MediaTypes.MOVIE:
            date = _parse_date(media["date"])
            self._create_db_entry(
                title=title,
                media_id=media_id,
                media_type=media_type,
                image=media["image"],
                date=date,
            )

        elif media_type is MediaTypes.TV:
            self._create_db_entry(
                title=title,
                media_id=media_id,
                media_type=media_type,
                image=media["image"],
                status=_series_completion_status(
                    media["number_of_seasons"], media["seasons"]
                ),
            )

            for season in media["seasons"]:
                self._create_db_entry(
                    title=title,
                    media_id=media_id,
                    media_type=MediaTypes.SEASON.value,
                    image=season["image"],
                    status=(
                        Status.IN_PROGRESS.value
                        if len(season["watched_episodes"])
                        < season["number_of_episodes"]
                        else Status.COMPLETED.value
                    ),
                    season_number=season["season_number"],
                )

                for episode in season["watched_episodes"]:
                    self._create_db_entry(
                        title=episode["episode_name"],
                        media_id=media_id,
                        media_type=MediaTypes.EPISODE.value,
                        image=episode["image"],
                        season_number=season["season_number"],
                        episode_number=episode["episode_number"],
                        date=_parse_date(episode["date"]),
                    )

    def _create_db_entry(
        self,
        title,
        media_id,
        media_type,
        image,
        status=Status.COMPLETED.value,
        date=None,
        season_number=None,
        episode_number=None,
    ):
        parent_type = (
            MediaTypes.TV.value
            if media_type in (MediaTypes.SEASON.value, MediaTypes.EPISODE.value)
            else media_type
        )

        # Check if we should process based on mode
        if not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            parent_type,
            Sources.TMDB.value,
            media_id,
            self.mode,
        ):
            return

        item, _ = app.models.Item.objects.update_or_create(
            media_id=str(media_id),
            source=Sources.TMDB.value,
            media_type=media_type,
            season_number=season_number,
            episode_number=episode_number,
            defaults={
                "title": title,
                "image": image,
            },
        )

        model = apps.get_model(app_label="app", model_name=media_type)
        instance = model(item=item)

        row = {
            "source": Sources.TMDB.value,
            "media_id": media_id,
            "media_type": media_type,
        }

        if media_type is not MediaTypes.EPISODE:
            instance.user = self.user
            row["status"] = status
        else:
            row["end_date"] = date

        if media_type is MediaTypes.MOVIE:
            row["end_date"] = date
            row["start_date"] = date

        form = app.forms.get_form_class(media_type)(
            row,
            instance=instance,
        )

        if form.is_valid():
            form.instance._history_date = date or timezone.now()
            self.bulk_media[media_type].append(form.instance)
        else:
            error_msg = f"{media_id} ({media_type}): {form.errors.as_json()}"
            self.warnings.append(error_msg)
            logger.error(error_msg)
