import json
import logging

from dateutil import parser

from integrations.imports.yamtrack import YamtrackImporter

logger = logging.getLogger(__name__)


class UnknownStateError(Exception):
    """Custom exception for unexpected state string."""


def importer(file, user, mode):
    """Import media from Watcharr JSON file resuing the YamtrackImporter."""
    csv_importer = WatcharrImporter(file, user, mode)
    return csv_importer.import_data()


def get_state(state):
    """Convert the Watcharr status to a Yamtrack status."""
    match state:
        case "FINISHED":
            return "Completed"
        case "WATCHING":
            return "In progress"
        case "PLANNED":
            return "Planning"
        case "PAUSED":
            return "Paused"
        case "DROPPED":
            return "Dropped"
        case _:
            error_msg = f"Unknown state: '{state}'"
            raise UnknownStateError(error_msg)


def to_date(date_str):
    """Convert the Watcharr date to ISO 8601."""
    date = parser.parse(date_str)
    return date.isoformat()


class WatcharrImporter(YamtrackImporter):
    """Class to handle importing user data from JSON files."""

    def __init__(self, file, user, mode):
        """Initialize the importer with file, user, and mode.

        Args:
            file: Uploaded CSV file object
            user: Django user object to import data for
            mode (str): Import mode ("new" or "overwrite")
        """
        super().__init__(file, user, mode)
        self._rows = []

    def _add_entry(self, media_type, content_entry, state_entry, dict_entry):
        """Add a single entry to the list of rows."""
        dict_entry["media_type"] = media_type
        dict_entry["source"] = "tmdb"
        # when testing, in integrations/imports/helpers.py::update_season_references()
        # existing_tv uses strings as keys:
        dict_entry["media_id"] = str(content_entry["content"]["tmdbId"])
        dict_entry["title"] = content_entry["content"]["title"]

        dict_entry["score"] = state_entry["rating"]
        dict_entry["status"] = get_state(state_entry["status"])
        dict_entry["created_at"] = to_date(state_entry["createdAt"])
        dict_entry["progressed_at"] = to_date(state_entry["updatedAt"])
        dict_entry["start_date"] = to_date(state_entry["createdAt"])
        dict_entry["end_date"] = (
            to_date(state_entry["updatedAt"])
            if state_entry["status"] == "FINISHED"
            else ""
        )

        dict_entry["image"] = ""
        dict_entry["notes"] = ""

        dict_entry.setdefault("season_number", "")
        dict_entry.setdefault("episode_number", "")
        dict_entry.setdefault("progress", "")

        self._rows.append(dict_entry)

    def get_iterator(self):
        """Process the JSON file and return an array for YamtrackImporter."""
        self._rows = []
        json_structure = json.load(self.file)

        for entry in json_structure:
            try:
                self._process_entry(entry)
            except Exception as error:
                error_msg = f"Error processing entry: {entry}"
                logger.exception(error_msg)
                self.warnings.append(f"{error_msg}. Error: {error}")
        return self._rows

    def _process_entry(self, entry):
        """Process a single entry from the main array in the JSON file."""
        match entry["content"]["type"]:
            case "movie":
                self._add_entry(
                    "movie",
                    entry,
                    entry,
                    {},
                )
            case "tv":
                self._add_entry("tv", entry, entry, {})
                if "watchedSeasons" in entry:
                    for season in entry["watchedSeasons"]:
                        self._add_entry(
                            "season",
                            entry,
                            season,
                            {"season_number": season["seasonNumber"]},
                        )
                if "watchedEpisodes" in entry:
                    for episode in entry["watchedEpisodes"]:
                        self._add_entry(
                            "episode",
                            entry,
                            episode,
                            {
                                "season_number": episode["seasonNumber"],
                                "episode_number": episode["episodeNumber"],
                            },
                        )
