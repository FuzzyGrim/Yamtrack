from pathlib import Path
from unittest.mock import MagicMock, patch
from xml.etree import ElementTree as ET

import requests
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from app.models import MediaTypes, Sources
from integrations.imports import letterboxd
from integrations.imports.helpers import MediaImportError

mock_path = Path(__file__).resolve().parent.parent / "mock_data"
letterboxd_mock_path = mock_path / "import_letterboxd_csvs"


class ImportLetterboxd(TestCase):
    """Test importing media from Letterboxd."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

    # CSV
    def test_combine_csv_files(self):
        """Test combining Letterboxd CSV files."""
        files = [
            (letterboxd_mock_path / filename).open("rb")
            for filename in (
                "diary.csv",
                "ratings.csv",
                "reviews.csv",
                "watched.csv",
            )
        ]

        importer_instance = letterboxd.LetterboxdCSVImporter(
            files,
            self.user,
            "new",
        )

        combined = importer_instance._combine_files()

        self.assertTrue(combined)
        self.assertIn("Date,Name,Year", combined[0])

        for file in files:
            file.close()

    def test_get_films_from_csv(self):
        """Test getting and combining films from Letterboxd CSV files."""
        files = [
            (letterboxd_mock_path / filename).open("rb")
            for filename in (
                "diary.csv",
                "ratings.csv",
                "reviews.csv",
                "watched.csv",
            )
        ]

        importer_instance = letterboxd.LetterboxdCSVImporter(
            files,
            self.user,
            "new",
        )

        with patch.object(
            importer_instance,
            "_process_row",
        ) as mock_process_row:
            importer_instance._get_films()

        rows = [call.args[0] for call in mock_process_row.call_args_list]
        rows_by_title = {row["Name"]: row for row in rows}

        godfather_part_ii = rows_by_title["The Godfather Part II"]
        self.assertEqual(godfather_part_ii["Year"], "1974")
        self.assertEqual(godfather_part_ii["Rating"], "3")
        self.assertEqual(godfather_part_ii["Rewatch"], "Yes")
        self.assertEqual(godfather_part_ii["Review"], "test review")
        self.assertEqual(godfather_part_ii["Watched Date"], "2025-02-24")

        godfather_part_iii_rows = [
            row for row in rows if row["Name"] == "The Godfather Part III"
        ]

        self.assertEqual(len(godfather_part_iii_rows), 2)

        watched_dates = {row["Watched Date"] for row in godfather_part_iii_rows}

        self.assertEqual(
            watched_dates,
            {
                "2026-04-14",
                "2026-08-18",
            },
        )

        boiling_point = rows_by_title["Boiling Point"]
        self.assertEqual(boiling_point["Year"], "2023")
        self.assertEqual(boiling_point["Rating"], "3")
        self.assertEqual(boiling_point["Review"], "fdsfdf")
        self.assertEqual(boiling_point["Watched Date"], "2026-08-13")

        life_aquatic = rows_by_title["The Life Aquatic with Steve Zissou"]
        self.assertEqual(life_aquatic["Year"], "2004")
        self.assertEqual(
            life_aquatic["Review"],
            "dasdasdasdasdsadasdasdsad",
        )
        self.assertEqual(life_aquatic["Watched Date"], "2026-08-18")

        for file in files:
            file.close()

    def test_invalid_csv_columns(self):
        """Test invalid Letterboxd CSV columns."""
        invalid_csv = b"Invalid,Columns\nfoo,bar\n"

        importer_instance = letterboxd.LetterboxdCSVImporter(
            [],
            self.user,
            "new",
        )

        file = SimpleUploadedFile(
            "invalid.csv",
            invalid_csv,
            content_type="text/csv",
        )

        importer_instance.files = [file]

        with self.assertRaisesRegex(
            MediaImportError,
            "Invalid columns",
        ):
            importer_instance._combine_files()

    def test_invalid_csv_encoding(self):
        """Test invalid Letterboxd CSV encoding."""
        file = SimpleUploadedFile(
            "invalid.csv",
            b"Date,Name,Year\n\xff\xfe\xfa",
            content_type="text/csv",
        )

        importer_instance = letterboxd.LetterboxdCSVImporter(
            [file],
            self.user,
            "new",
        )

        with self.assertRaisesRegex(
            MediaImportError,
            "Invalid file format",
        ):
            importer_instance._combine_files()

    @patch("integrations.imports.letterboxd.tmdb.search")
    def test_process_csv_row_film_not_found(self, mock_tmdb_search):
        """Test CSV import when a film cannot be found on TMDB."""
        mock_tmdb_search.return_value = {"results": []}

        importer_instance = letterboxd.LetterboxdCSVImporter(
            [],
            self.user,
            "new",
        )

        row = {
            "Date": "2026-08-18T00:00:00",
            "Name": "Missing Film",
            "Year": "2026",
            "Rating": "",
            "Review": "",
            "Watched Date": "2026-08-18",
        }

        importer_instance._process_row(row)

        self.assertIn(
            "Film Missing Film not found on TMDB "
            "(it's possible that it's actually a TV series instead)",
            importer_instance.warnings,
        )

    @patch("integrations.imports.letterboxd.tmdb.search")
    def test_import_csv_films(self, mock_tmdb_search):
        """Test importing films from Letterboxd CSV files."""

        def search_side_effect(_, name, __, ___):
            films = {
                "The Godfather Part II": {
                    "name": "The Godfather Part II",
                    "media_id": 123123,
                    "year": 1974,
                    "image": "https://example.com/example.jpg",
                },
                "Boiling Point": {
                    "name": "Boiling Point",
                    "media_id": 123456,
                    "year": 2023,
                    "image": "https://example.com/example.jpg",
                },
            }

            if name in films:
                return {
                    "results": [
                        {
                            "media_id": films[name]["media_id"],
                            "media_type": MediaTypes.MOVIE.value,
                            "title": name,
                            "year": films[name]["year"],
                            "image": "https://example.com/example.jpg",
                        }
                    ]
                }

            return {"results": []}

        mock_tmdb_search.side_effect = search_side_effect

        files = [
            (letterboxd_mock_path / filename).open("rb")
            for filename in (
                "diary.csv",
                "ratings.csv",
                "reviews.csv",
                "watched.csv",
            )
        ]

        importer_instance = letterboxd.LetterboxdCSVImporter(
            files,
            self.user,
            "new",
        )

        with (
            patch.object(letterboxd.helpers, "bulk_create_media"),
            patch.object(letterboxd.helpers, "cleanup_existing_media"),
        ):
            imported_counts, _ = importer_instance.import_data()

        self.assertEqual(
            imported_counts[MediaTypes.MOVIE.value],
            2,
        )

        imported_titles = {
            film.item.title
            for film in importer_instance.bulk_media[MediaTypes.MOVIE.value]
        }

        self.assertEqual(
            imported_titles,
            {
                "The Godfather Part II",
                "Boiling Point",
            },
        )

        for file in files:
            file.close()

    # RSS
    @patch("integrations.imports.letterboxd.services.api_request")
    def test_get_films_from_rss(self, mock_api_request):
        """Test getting films from a mocked Letterboxd RSS feed."""
        with (letterboxd_mock_path / "example.rss").open("rb") as file:
            mock_api_request.return_value = ET.fromstring(file.read())  # noqa: S314

        importer_instance = letterboxd.LetterboxdRSSImporter(
            "testuser",
            self.user,
            "new",
        )

        films = importer_instance._get_films()

        mock_api_request.assert_called_once_with(
            "Letterboxd",
            "GET",
            "https://letterboxd.com/testuser/rss",
            response_format="xml",
        )

        self.assertTrue(films)

        self.assertIn("title", films[0])
        self.assertIn("film_title", films[0])
        self.assertIn("film_year", films[0])
        self.assertIn("rating", films[0])
        self.assertIn("watched_date", films[0])
        self.assertIn("rewatch", films[0])
        self.assertIn("member_like", films[0])
        self.assertIn("tmdb_id", films[0])
        self.assertIn("description", films[0])

    @patch("integrations.imports.letterboxd.services.api_request")
    def test_rss_film_data_parsing(self, mock_api_request):
        """Test parsing fields from a Letterboxd RSS film."""
        with (letterboxd_mock_path / "example.rss").open("rb") as file:
            mock_api_request.return_value = ET.fromstring(file.read())  # noqa: S314

        importer_instance = letterboxd.LetterboxdRSSImporter(
            "testuser",
            self.user,
            "new",
        )

        films = importer_instance._get_films()
        film = films[0]

        self.assertEqual(film["title"], "The Life Aquatic with Steve Zissou, 2004")
        self.assertEqual(film["film_title"], "The Life Aquatic with Steve Zissou")
        self.assertEqual(film["film_year"], 2004)
        self.assertEqual(film["watched_date"], "2026-08-18")
        self.assertFalse(film["rewatch"])
        self.assertFalse(film["member_like"])
        self.assertEqual(film["tmdb_id"], 421)

    @patch("integrations.imports.letterboxd.services.api_request")
    def test_rss_access_error(self, mock_api_request):
        """Test RSS importer when Letterboxd returns an HTTP error."""
        response = requests.Response()
        response.status_code = 404

        mock_api_request.side_effect = requests.HTTPError(
            "404 Client Error",
            response=response,
        )

        importer_instance = letterboxd.LetterboxdRSSImporter(
            "testuser",
            self.user,
            "new",
        )

        with self.assertRaisesRegex(
            MediaImportError,
            "Letterboxd access error: 404",
        ):
            importer_instance._get_films()

    @patch("integrations.imports.letterboxd.tmdb.movie")
    def test_process_rss_film_tmdb_lookup(self, mock_tmdb_movie):
        """Test processing an RSS film using its TMDB ID."""
        mock_tmdb_movie.return_value = {
            "media_id": 12345,
            "media_type": MediaTypes.MOVIE.value,
            "title": "Example Film",
            "image": "example.jpg",
        }

        importer_instance = letterboxd.LetterboxdRSSImporter(
            "testuser",
            self.user,
            "new",
        )

        film = {
            "title": "Example Film",
            "film_title": "Example Film",
            "film_year": 2026,
            "rating": 4.0,
            "watched_date": "2026-08-18",
            "tmdb_id": 12345,
            "description": "A review.",
        }

        mock_item = MagicMock()
        mock_instance = MagicMock()

        with (
            patch.object(
                letterboxd.app.models.Item.objects,
                "update_or_create",
                return_value=(mock_item, True),
            ),
            patch.object(letterboxd.apps, "get_model") as mock_get_model,
        ):
            mock_get_model.return_value.return_value = mock_instance

            importer_instance._process_film(film)

        mock_tmdb_movie.assert_called_once_with(12345)
        self.assertEqual(
            len(importer_instance.bulk_media[MediaTypes.MOVIE.value]),
            1,
        )

    @patch("integrations.imports.letterboxd.tmdb.movie")
    def test_process_rss_film_not_found(self, mock_tmdb_movie):
        """Test RSS import when a film cannot be found on TMDB."""
        mock_tmdb_movie.side_effect = letterboxd.services.ProviderAPIError(
            "tmdb", "Film not found on TMDB"
        )

        importer_instance = letterboxd.LetterboxdRSSImporter(
            "testuser",
            self.user,
            "new",
        )

        film = {
            "title": "Missing Film",
            "film_title": "Missing Film",
            "film_year": 2026,
            "rating": None,
            "watched_date": "2026-08-18",
            "tmdb_id": 12345,
            "description": None,
        }

        importer_instance._process_film(film)

        self.assertIn(
            "Film Missing Film not found on TMDB "
            "(it's possible that it's actually a TV series instead)",
            importer_instance.warnings,
        )

    @patch("integrations.imports.letterboxd.tmdb.movie")
    @patch("integrations.imports.letterboxd.helpers.get_existing_media_with_duplicates")
    def test_process_rss_film_overwrite(
        self,
        mock_get_existing_media,
        mock_tmdb_movie,
    ):
        """Test that an existing film is marked for deletion in overwrite mode."""
        tmdb_id = 12345

        mock_tmdb_movie.return_value = {
            "media_id": tmdb_id,
            "media_type": MediaTypes.MOVIE.value,
            "title": "Example Film",
            "image": "example.jpg",
        }

        mock_get_existing_media.return_value = {
            MediaTypes.MOVIE.value: {
                Sources.TMDB.value: {
                    str(tmdb_id): [MagicMock()],
                },
            },
        }

        importer_instance = letterboxd.LetterboxdRSSImporter(
            "testuser",
            self.user,
            "overwrite",
        )

        film = {
            "title": "Example Film",
            "film_title": "Example Film",
            "film_year": 2026,
            "rating": 4.0,
            "watched_date": "2026-08-18",
            "rewatch": False,
            "member_like": False,
            "tmdb_id": tmdb_id,
            "description": "A review.",
        }

        with (
            patch.object(
                letterboxd.app.models.Item.objects,
                "update_or_create",
                return_value=(MagicMock(), True),
            ),
            patch.object(letterboxd.apps, "get_model") as mock_get_model,
        ):
            mock_get_model.return_value = MagicMock()

            importer_instance._process_film(film)

        self.assertEqual(
            importer_instance.to_delete[MediaTypes.MOVIE.value][Sources.TMDB.value],
            {tmdb_id},
        )

        self.assertEqual(
            len(importer_instance.bulk_media[MediaTypes.MOVIE.value]),
            1,
        )

        mock_tmdb_movie.assert_called_once_with(tmdb_id)
