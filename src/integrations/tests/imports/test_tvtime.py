import io
import zipfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import (
    TV,
    Episode,
    MediaTypes,
    Movie,
    Season,
    Status,
)
from integrations.imports import tvtime
from integrations.imports.helpers import MediaImportError
from integrations.imports.tvtime import TVTimeImporter


def build_zip(files):
    """Return an in-memory zip file containing the given {name: rows} CSVs."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    buffer.seek(0)
    return buffer


SHOW_DATA = (
    "tv_show_name,user_id,tv_show_id,is_followed,is_favorited,nb_episodes_seen\n"
    "Test Show,1,111,1,0,2\n"
    "Planned Show,1,222,1,0,0\n"
)

TRACKING_V2 = (
    "gsi,s_id,ep_id,season_number,key,user_id,created_at,episode_id,series_name,"
    "episode_number\n"
    "watch-episode-1,111,1001,1,k1,1,2021-01-01 10:00:00,1001,Test Show,1\n"
    "watch-episode-2,111,1002,1,k2,1,2021-01-02 10:00:00,1002,Test Show,2\n"
    # duplicate (rewatch) of episode 1 with a later date, should be deduped
    "watch-episode-3,111,1001,1,k3,1,2021-03-01 10:00:00,1001,Test Show,1\n"
)

EPISODE_RATINGS = (
    "series_name,season_number,episode_number,user_id,vote_key,episode_id\n"
    "Test Show,1,1,1,1001-1-3,1001\n"
)


def tv_metadata_side_effect(media_type, _, __, ___=None):
    """Return fake TMDB metadata for the importer."""
    if media_type == MediaTypes.TV.value:
        return {
            "title": "Test Show",
            "image": "tv.jpg",
            "last_episode_season": 1,
            "max_progress": 2,
        }
    if media_type == MediaTypes.SEASON.value:
        return {
            "title": "Season 1",
            "image": "season.jpg",
            "max_progress": 2,
            "episodes": [
                {"episode_number": 1, "still_path": "/1.jpg"},
                {"episode_number": 2, "still_path": "/2.jpg"},
            ],
        }
    return None


class ImportTVTime(TestCase):
    """Test importing media from a TV Time export."""

    def setUp(self):
        """Create user for the tests."""
        credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**credentials)

    @patch("integrations.imports.tvtime.TVTimeImporter._map_series")
    @patch("integrations.imports.tvtime.TVTimeImporter._get_metadata")
    def test_full_import_flow(self, mock_get_metadata, mock_map_series):
        """Test importing watched episodes and a watchlist entry."""
        mock_get_metadata.side_effect = tv_metadata_side_effect
        mock_map_series.side_effect = lambda series_id, _: {
            "111": "500",
            "222": "600",
        }.get(series_id)

        zip_file = build_zip(
            {
                "user_tv_show_data.csv": SHOW_DATA,
                "tracking-prod-records-v2.csv": TRACKING_V2,
                "ratings-3-prod-episode_votes.csv": EPISODE_RATINGS,
            },
        )

        imported_counts, _ = tvtime.importer(zip_file, self.user, "new")

        # 1 watched show + 1 planning show
        self.assertEqual(imported_counts[MediaTypes.TV.value], 2)
        self.assertEqual(imported_counts[MediaTypes.SEASON.value], 1)
        # rewatch of episode 1 deduped -> 2 episodes
        self.assertEqual(imported_counts[MediaTypes.EPISODE.value], 2)

        watched = TV.objects.get(item__media_id="500")
        self.assertEqual(watched.status, Status.COMPLETED.value)

        planned = TV.objects.get(item__media_id="600")
        self.assertEqual(planned.status, Status.PLANNING.value)

        season = Season.objects.get(item__media_id="500")
        self.assertEqual(season.status, Status.COMPLETED.value)
        self.assertEqual(Episode.objects.filter(related_season=season).count(), 2)

    @patch("integrations.imports.tvtime.TVTimeImporter._map_series")
    @patch("integrations.imports.tvtime.TVTimeImporter._get_metadata")
    def test_fallback_watched_file(self, mock_get_metadata, mock_map_series):
        """Test using watched_on_episode when the v2 tracking file is absent."""
        mock_get_metadata.side_effect = tv_metadata_side_effect
        mock_map_series.side_effect = lambda series_id, _: (
            "500" if series_id == "111" else None
        )

        watched_simple = (
            "user_id,episode_id,watched_on_source_id,created_at,updated_at,"
            "tv_show_name,episode_season_number,episode_number\n"
            "1,1001,1,2021-01-01 10:00:00,2021-01-01 10:00:00,Test Show,1,1\n"
        )
        zip_file = build_zip(
            {
                "user_tv_show_data.csv": SHOW_DATA,
                "watched_on_episode.csv": watched_simple,
            },
        )

        imported_counts, _ = tvtime.importer(zip_file, self.user, "new")

        self.assertEqual(imported_counts[MediaTypes.EPISODE.value], 1)

    @patch("integrations.imports.tvtime.services.search")
    def test_movie_import(self, mock_search):
        """Test importing watched and watchlisted movies matched by title."""
        catalog = {
            "The Batman": {"media_id": 414906, "title": "The Batman", "image": "b"},
            "Dune: Part Two": {
                "media_id": 693134,
                "title": "Dune: Part Two",
                "image": "d",
            },
        }

        def search_side_effect(_, query, __):
            match = catalog.get(query)
            results = (
                [{"source": "tmdb", "media_type": "movie", **match}] if match else []
            )
            return {"results": results}

        mock_search.side_effect = search_side_effect

        v1_movies = (
            "type,entity_type,movie_name,release_date,watch_date,created_at\n"
            "watch,movie,The Batman,2022-03-04 00:00:00,2022-04-01 00:00:00,"
            "2022-04-01 00:00:00\n"
            # rewatch duplicate of the same movie -> deduped
            "watch,movie,The Batman,2022-03-04 00:00:00,2022-05-01 00:00:00,"
            "2022-05-01 00:00:00\n"
            "towatch,movie,Dune: Part Two,2024-03-01 00:00:00,,2024-01-01 00:00:00\n"
        )
        zip_file = build_zip({"tracking-prod-records.csv": v1_movies})

        imported_counts, _ = tvtime.importer(zip_file, self.user, "new")

        self.assertEqual(imported_counts[MediaTypes.MOVIE.value], 2)

        watched = Movie.objects.get(item__media_id="414906")
        self.assertEqual(watched.status, Status.COMPLETED.value)
        self.assertEqual(watched.progress, 1)

        planned = Movie.objects.get(item__media_id="693134")
        self.assertEqual(planned.status, Status.PLANNING.value)

    def test_invalid_zip(self):
        """Test that a non-zip upload raises a clear error."""
        with self.assertRaises(MediaImportError):
            tvtime.importer(io.BytesIO(b"not a zip"), self.user, "new")

    def test_missing_known_files(self):
        """Test that a zip without recognized files raises an error."""
        zip_file = build_zip({"random.csv": "a,b\n1,2\n"})
        with self.assertRaises(MediaImportError):
            tvtime.importer(zip_file, self.user, "new")

    def test_normalize_name(self):
        """Test the filename normalization used to detect export files."""
        self.assertEqual(
            tvtime._normalize_name("abc123-tracking-prod-records-v2.csv"),
            "abc123trackingprodrecordsv2csv",
        )
        self.assertEqual(
            tvtime._normalize_name("dir/ratings-3-prod-episode_votes.csv"),
            "ratings3prodepisodevotescsv",
        )

    def test_vote_to_score_unmapped(self):
        """Test that unmapped TV Time votes are skipped."""
        importer_instance = TVTimeImporter(io.BytesIO(), self.user, "new")
        self.assertIsNone(importer_instance._vote_to_score("1001-1-3"))
        self.assertIsNone(importer_instance._vote_to_score(""))

    @patch("integrations.imports.tvtime.tmdb.find")
    def test_map_series(self, mock_find):
        """Test mapping a TVDB series id to a TMDB id."""
        mock_find.return_value = {"tv_results": [{"id": 1396}]}
        importer_instance = TVTimeImporter(io.BytesIO(), self.user, "new")

        self.assertEqual(importer_instance._map_series("81189", "Breaking Bad"), "1396")
        # cached on the second call
        self.assertEqual(importer_instance._map_series("81189", "Breaking Bad"), "1396")
        mock_find.assert_called_once()

    @patch("integrations.imports.tvtime.tmdb.find")
    def test_map_series_not_found(self, mock_find):
        """Test a warning is added when a series can't be matched."""
        mock_find.return_value = {"tv_results": []}
        importer_instance = TVTimeImporter(io.BytesIO(), self.user, "new")

        self.assertIsNone(importer_instance._map_series("999", "Unknown Show"))
        self.assertTrue(
            any("Unknown Show" in warning for warning in importer_instance.warnings),
        )
