import logging
import re
from collections import defaultdict
from csv import DictReader

import pandas as pd
import requests
from bs4 import BeautifulSoup
from django.apps import apps
from django.utils import timezone
from django.utils.dateparse import parse_datetime

import app
from app.models import MediaTypes, Sources, Status
from app.providers import services, tmdb
from integrations.imports import helpers
from integrations.imports.helpers import MediaImportError

logger = logging.getLogger(__name__)


def text(entry, tag, namespaces, default=None):
    """Read text from an XML entry."""
    value = entry.findtext(tag, namespaces=namespaces)
    return value.strip() if value and value.strip() else default


def integer(entry, tag, namespaces, default=None):
    """Read an integer from an XML entry."""
    value = text(entry, tag, namespaces)
    try:
        return int(value) if value is not None else default
    except (ValueError, TypeError):
        return default


def number(entry, tag, namespaces, default=None):
    """Read a number from an XML entry."""
    value = text(entry, tag, namespaces)
    try:
        return float(value) if value is not None else default
    except (ValueError, TypeError):
        return default


def boolean(entry, tag, namespaces):
    """Read a boolean from an XML entry."""
    value = text(entry, tag, namespaces)
    if value is None:
        return False
    return value.lower() in {"yes", "true", "1"}


def description(entry, namespaces, default=None):
    """Read the description from an XML entry and skip the <img> tag."""
    value = text(entry, "description", namespaces)

    if not value:
        return default

    soup = BeautifulSoup(value, "html.parser")
    parts = []

    for p in soup.find_all("p"):
        # Skip <p> elements containing an image with no text.
        paragraph = p.get_text(" ", strip=True)

        if paragraph:
            parts.append(paragraph)

    return "\n".join(parts) or default


"""
TODO:
- figure out how overwriting films works (if i want to include rewatches it
should add a new entry but also not delete? maybe don't include those in
the bulk create?)
- real error handling
    - missing ids and failed imports
    - incorrect names
- make tests
    - empty csv
    - rss feed with no films
    - incorrect username
    - rss feed with many films?
    - rss feed with ids that dont work?
- what happens with a limited series, will that count as a movie? maybe just
filter out exclusively films?
"""

DIARY_COLUMNS = [
    "Date",
    "Name",
    "Year",
    "Letterboxd URI",
    "Rating",
    "Rewatch",
    "Tags",
    "Watched Date",
]

RATINGS_COLUMNS = [
    "Date",
    "Name",
    "Year",
    "Letterboxd URI",
    "Rating",
]

REVIEWS_COLUMNS = [
    "Date",
    "Name",
    "Year",
    "Letterboxd URI",
    "Rating",
    "Rewatch",
    "Review",
    "Tags",
    "Watched Date",
]

WATCHED_COLUMNS = [
    "Date",
    "Name",
    "Year",
    "Letterboxd URI",
]


def importer_csv(files, user, mode):
    """Letterboxd CSV importer function."""
    letterboxd_importer = LetterboxdCSVImporter(files, user, mode)
    return letterboxd_importer.import_data()


def importer_rss(letterboxd_username, user, mode):
    """Letterboxd RSS importer function."""
    letterboxd_importer = LetterboxdRSSImporter(letterboxd_username, user, mode)
    return letterboxd_importer.import_data()


class LetterboxdCSVImporter:
    """letterboxd csv importer."""

    def __init__(self, files, user, mode):
        """Letterboxd csv importer."""
        self.files = files
        self.user = user
        self.mode = mode
        self.warnings = []

        self.existing_media = helpers.get_existing_media(user)

        self.to_delete = defaultdict(lambda: defaultdict(set))

        self.bulk_media = defaultdict(list)

        logger.info(
            "Initialized Letterboxd CSV importer for user %s with mode %s",
            user.username,
            mode,
        )

    def import_data(self):
        """Import data."""
        self._get_films()
        helpers.cleanup_existing_media(self.to_delete, self.user)
        helpers.bulk_create_media(self.bulk_media, self.user)

        imported_counts = {
            media_type: len(media_list)
            for media_type, media_list in self.bulk_media.items()
        }

        deduplicated_messages = "\n".join(dict.fromkeys(self.warnings))
        return imported_counts, deduplicated_messages if self.warnings else None

    def _combine_files(self):
        all_columns = list(
            dict.fromkeys(
                DIARY_COLUMNS + RATINGS_COLUMNS + REVIEWS_COLUMNS + WATCHED_COLUMNS
            )
        )

        combined = pd.DataFrame(columns=all_columns)

        for file in self.files:
            try:
                decoded_file = file.read().decode("utf-8-sig").splitlines()
            except UnicodeDecodeError as e:
                msg = (
                    "Invalid file format in uploaded CSV. "
                    "Please upload a valid CSV file."
                )
                raise MediaImportError(msg) from e

            reader = DictReader(decoded_file)
            columns = set(reader.fieldnames)
            if set(columns) not in [
                set(DIARY_COLUMNS),
                set(RATINGS_COLUMNS),
                set(REVIEWS_COLUMNS),
                set(WATCHED_COLUMNS),
            ]:
                msg = "Invalid columns in uploaded CSV. Please upload a valid CSV file."
                raise MediaImportError(msg)

            for row in reader:
                name = row["Name"]
                year = row["Year"]
                date = row["Date"]
                watched_date = row.get("Watched Date") or ""

                same_film = combined["Name"].eq(name) & combined["Year"].eq(year)

                if watched_date:
                    exists = same_film & (
                        combined["Watched Date"].eq(watched_date)
                        | combined["Date"].eq(date)
                        & (
                            combined["Watched Date"].isna()
                            | combined["Watched Date"].eq("")
                        )
                    )
                else:
                    exists = same_film & combined["Date"].eq(date)

                if exists.any():
                    index = combined.index[exists][0]

                    for column, value in row.items():
                        if not value or not value.strip():
                            continue

                        existing = combined.at[index, column]

                        if pd.isna(existing) or not str(existing).strip():
                            combined.at[index, column] = value

                else:
                    new_row = {
                        column: row.get(column, "") for column in combined.columns
                    }

                    combined.loc[len(combined)] = new_row

        return combined.to_csv(index=False).splitlines()

    def _get_films(self):
        combined_csvs = self._combine_files()
        reader = DictReader(combined_csvs)
        logger.debug("Combined CSVs: %s", combined_csvs)

        for row in reader:
            self._process_row(row)

    def _process_row(self, row):

        date = parse_datetime(row["Date"])
        name = row["Name"]
        year = int(row["Year"])
        reviews = row["Review"]
        watched_date = parse_datetime(row["Watched Date"])
        if date:
            date = date.replace(
                hour=0, minute=0, second=0, tzinfo=timezone.get_current_timezone()
            )
        rating = (
            float(row["Rating"]) * 2 if row["Rating"] else None
        )  # letterboxd is out of 5

        film = tmdb.search(MediaTypes.MOVIE, f"{name}", 1, primary_release_year=year)
        if not film["results"]:
            msg = (
                f"Film {name} not found on TMDB "
                f"(it's possible that it's actually a TV series instead)"
            )
            logger.debug(msg)
            self.warnings.append(msg)
            return

        film = film["results"][0]
        if not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            film["media_type"],
            Sources.TMDB.value,
            str(film["media_id"]),
            self.mode,
        ):
            return

        item, _ = app.models.Item.objects.update_or_create(
            media_id=film["media_id"],
            source=Sources.TMDB.value,
            media_type=film["media_type"],
            defaults={
                "title": film["title"],
                "image": film["image"],
            },
        )

        model = apps.get_model(app_label="app", model_name=film["media_type"])

        status = Status.COMPLETED.value

        params = {
            "item": item,
            "user": self.user,
            "score": rating,
            "status": status,
            "progress": 1,
            "notes": reviews,
            "end_date": watched_date,
            "start_date": watched_date,
        }

        instance = model(**params)
        instance._history_date = date or timezone.now()

        self.bulk_media[film["media_type"]].append(instance)


class LetterboxdRSSImporter:
    """letterboxd RSS importer."""

    def __init__(self, username, user, mode):
        """Letterboxd RSS importer."""
        self.letterboxd_username = username
        self.letterboxd_rss_url = (
            f"https://letterboxd.com/{self.letterboxd_username}/rss"
        )
        self.user = user
        self.mode = mode
        self.warnings = []

        # Track existing media for "new" mode
        self.existing_media = helpers.get_existing_media_with_duplicates(user)

        # Track media IDs to delete in overwrite mode
        self.to_delete = defaultdict(lambda: defaultdict(set))

        # Track bulk creation lists for each media type
        self.bulk_media = defaultdict(list)

        logger.info(
            "Initialized Letterboxd RSS importer for user %s with mode %s",
            user.username,
            mode,
        )

    def import_data(self):
        """Import data."""
        films = self._get_films()
        for film in films:
            self._process_film(film)

        helpers.cleanup_existing_media(self.to_delete, self.user)
        helpers.bulk_create_media(self.bulk_media, self.user)

        imported_counts = {
            media_type: len(media_list)
            for media_type, media_list in self.bulk_media.items()
        }

        deduplicated_messages = "\n".join(dict.fromkeys(self.warnings))
        return imported_counts, deduplicated_messages if self.warnings else None

    def _get_films(self):
        logger.debug("Getting films from Letterboxd")
        namespaces = {
            "letterboxd": "https://letterboxd.com",
            "tmdb": "https://themoviedb.org",
            "dc": "http://purl.org/dc/elements/1.1/",
        }

        films = []

        try:
            xml = services.api_request(
                "Letterboxd", "GET", self.letterboxd_rss_url, response_format="xml"
            )

            films.extend(
                {
                    "title": re.sub(r"\s*-\s*★+$", "", text(item, "title", namespaces)),
                    "pub_date": text(item, "pubDate", namespaces),
                    "film_title": text(item, "letterboxd:filmTitle", namespaces),
                    "film_year": integer(item, "letterboxd:filmYear", namespaces),
                    "rating": number(item, "letterboxd:memberRating", namespaces),
                    "watched_date": text(item, "letterboxd:watchedDate", namespaces),
                    "rewatch": boolean(item, "letterboxd:rewatch", namespaces),
                    "member_like": boolean(item, "letterboxd:memberLike", namespaces),
                    "tmdb_id": integer(item, "tmdb:movieId", namespaces),
                    "description": description(item, namespaces),
                }
                for item in xml.findall("./channel/item")
            )

        except requests.HTTPError as e:
            msg = f"Letterboxd access error: {e.response.status_code}"
            raise MediaImportError(msg) from e
        else:
            return films

    def _process_film(self, film):
        rating = (
            float(film["rating"]) * 2 if film["rating"] else None
        )  # letterboxd is out of 5
        watched_date = film["watched_date"]
        tmdb_id = film["tmdb_id"]
        desc = film["description"]
        date = parse_datetime(watched_date)
        if date:
            date = date.replace(
                hour=0, minute=0, second=0, tzinfo=timezone.get_current_timezone()
            )

        if (
            str(tmdb_id)
            in self.existing_media[MediaTypes.MOVIE.value][Sources.TMDB.value]
        ):
            # skip adding if the media already exists with a matching date
            old_entries = self.existing_media[MediaTypes.MOVIE.value][
                Sources.TMDB.value
            ][str(tmdb_id)]

            for old_entry in old_entries:
                if date in (old_entry.end_date, old_entry.start_date):
                    logger.debug(
                        "%s skipped: date matches existing record", film["film_title"]
                    )
                    return

        try:
            film_tmdb = tmdb.movie(tmdb_id)
        except services.ProviderAPIError:
            msg = (
                f"Film {film['title']} not found on TMDB "
                f"(it's possible that it's actually a TV series instead)"
            )
            logger.debug(msg)
            self.warnings.append(msg)
            return

        item, _ = app.models.Item.objects.update_or_create(
            media_id=film_tmdb["media_id"],
            source=Sources.TMDB.value,
            media_type=film_tmdb["media_type"],
            defaults={
                "title": film_tmdb["title"],
                "image": film_tmdb["image"],
            },
        )

        model = apps.get_model(app_label="app", model_name=film_tmdb["media_type"])

        status = Status.COMPLETED.value

        params = {
            "item": item,
            "user": self.user,
            "score": rating,
            "status": status,
            "progress": 1,
            "notes": desc,
            "end_date": date,
            "start_date": date,
        }

        instance = model(**params)
        instance._history_date = timezone.now()

        self.bulk_media[film_tmdb["media_type"]].append(instance)
