import logging
from collections import defaultdict
from csv import DictReader

import requests
from django.apps import apps
from django.utils import timezone
from django.utils.dateparse import parse_datetime

import app
from app.models import MediaTypes, Sources, Status
from app.providers import services, tmdb
from integrations.imports import helpers
from integrations.imports.helpers import MediaImportError

logger = logging.getLogger(__name__)


def importer_csv(file, user, mode):
    """Letterboxd CSV importer function."""
    letterboxd_importer = LetterboxdCSVImporter(file, user, mode)
    return letterboxd_importer.import_data()


def importer_rss(letterboxd_username, user, mode):
    """Letterboxd RSS importer function."""
    letterboxd_importer = LetterboxdRSSImporter(letterboxd_username, user, mode)
    return letterboxd_importer.import_data()


class LetterboxdCSVImporter:
    """letterboxd csv importer."""

    def __init__(self, file, user, mode):
        """Letterboxd csv importer."""
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

    def _get_films(self):
        try:
            decoded_file = self.file.read().decode("utf-8").splitlines()
        except UnicodeDecodeError as e:
            msg = "Invalid file format. Please upload a CSV file."
            raise MediaImportError(msg) from e

        reader = DictReader(decoded_file)

        for row in reader:
            self._process_row(row)

    def _process_row(self, row):
        date = row["Date"]
        name = row["Name"]
        year = int(row["Year"])
        rating = float(row["Rating"]) * 2  # letterboxd is out of 5

        film = tmdb.search(MediaTypes.MOVIE, f"{name}", 1, primary_release_year=year)
        if not film["results"]:
            return  # error or something

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

        status = Status.COMPLETED.value if rating is not None else Status.PLANNING.value

        params = {
            "item": item,
            "user": self.user,
            "score": rating,
            "status": status,
            "progress": 1,
        }

        date = parse_datetime(date)
        if date:
            date = date.replace(
                hour=0, minute=0, second=0, tzinfo=timezone.get_current_timezone()
            )

        params["end_date"] = date

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
        self.existing_media = helpers.get_existing_media(user)

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

            logger.debug("xml found")

            for item in xml.findall("./channel/item"):
                films.extend(
                    {
                        "title": item.findtext(
                            "letterboxd:filmTitle", namespaces=namespaces
                        ),
                        "year": int(
                            item.findtext("letterboxd:filmYear", namespaces=namespaces)
                        ),
                        "rating": float(
                            item.findtext(
                                "letterboxd:memberRating", namespaces=namespaces
                            )
                        ),
                        "watched_date": item.findtext(
                            "letterboxd:watchedDate", namespaces=namespaces
                        ),
                        "rewatch": item.findtext(
                            "letterboxd:rewatch", namespaces=namespaces
                        )
                        == "Yes",
                        "tmdb_id": item.findtext("tmdb:movieId", namespaces=namespaces),
                        "url": item.findtext("link"),
                    }
                )

        except requests.HTTPError as e:
            msg = f"Letterboxd access error: {e.response.status_code}"
            raise MediaImportError(msg) from e
        else:
            return films

    def _process_film(self, film):
        rating = film["rating"] * 2  # letterboxd is out of 5
        watched_date = film["watched_date"]
        tmdb_id = film["tmdb_id"]
        if not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            MediaTypes.MOVIE.value,
            Sources.TMDB.value,
            str(tmdb_id),
            self.mode,
        ):
            return
        logger.debug(film["title"])
        logger.debug(film["watched_date"])
        film_tmdb = tmdb.movie(tmdb_id)

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

        status = Status.COMPLETED.value if rating is not None else Status.PLANNING.value

        params = {
            "item": item,
            "user": self.user,
            "score": rating,
            "status": status,
            "progress": 1,
        }

        date = parse_datetime(watched_date)
        if date:
            date = date.replace(
                hour=0, minute=0, second=0, tzinfo=timezone.get_current_timezone()
            )

        params["end_date"] = date

        instance = model(**params)
        instance._history_date = date or timezone.now()

        logger.debug(film_tmdb["media_type"])
        self.bulk_media[film_tmdb["media_type"]].append(instance)
