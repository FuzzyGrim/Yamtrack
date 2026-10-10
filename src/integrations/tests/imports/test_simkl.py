from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import (
    TV,
    Anime,
    Episode,
    Item,
    MediaTypes,
    Movie,
    Season,
    Status,
)
from integrations.imports import (
    helpers,
    simkl,
)

mock_path = Path(__file__).resolve().parent.parent / "mock_data"
app_mock_path = (
    Path(__file__).resolve().parent.parent.parent.parent / "app" / "tests" / "mock_data"
)


class ImportSimkl(TestCase):
    """Test importing media from SIMKL."""

    def setUp(self):
        """Create user for the tests."""
        credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**credentials)
        self.importer = simkl.SimklImporter(
            helpers.encrypt("token"),
            self.user,
            "new",
        )

    @patch("integrations.imports.simkl.SimklImporter._get_user_list")
    def test_importer(
        self,
        user_list,
    ):
        """Test importing media from SIMKL."""
        user_list.return_value = {
            "shows": [
                {
                    "last_watched_at": "2023-01-02T00:00:00Z",
                    "show": {"title": "Breaking Bad", "ids": {"tmdb": 1396}},
                    "status": "watching",
                    "user_rating": 8,
                    "seasons": [
                        {
                            "number": 1,
                            "episodes": [
                                {"number": 1},
                                {"number": 2, "watched_at": "2023-01-02T00:00:00Z"},
                            ],
                        },
                    ],
                    "memo": {},
                },
            ],
            "movies": [
                {
                    "added_to_watchlist_at": "2023-01-01T00:00:00Z",
                    "movie": {"title": "Perfect Blue", "ids": {"tmdb": 10494}},
                    "status": "completed",
                    "user_rating": 9,
                    "last_watched_at": "2023-02-01T00:00:00Z",
                    "memo": {},
                },
            ],
            "anime": [
                {
                    "added_to_watchlist_at": "2023-01-01T00:00:00Z",
                    "show": {"title": "Example Anime", "ids": {"mal": 1}},
                    "status": "plantowatch",
                    "user_rating": 7,
                    "watched_episodes_count": 0,
                    "last_watched_at": None,
                    "memo": {"text": "Great series!"},
                },
            ],
        }

        imported_counts, warnings = self.importer.import_data()

        self.assertEqual(imported_counts[MediaTypes.TV.value], 1)
        self.assertEqual(imported_counts[MediaTypes.MOVIE.value], 1)
        self.assertEqual(imported_counts[MediaTypes.ANIME.value], 1)
        self.assertEqual(warnings, "")

        tv_item = Item.objects.get(media_type=MediaTypes.TV.value)
        self.assertEqual(tv_item.title, "Breaking Bad")
        tv_obj = TV.objects.get(item=tv_item)
        self.assertEqual(tv_obj.status, Status.IN_PROGRESS.value)
        self.assertEqual(tv_obj.score, 8)

        movie_item = Item.objects.get(media_type=MediaTypes.MOVIE.value)
        self.assertEqual(movie_item.title, "Perfect Blue")
        movie_obj = Movie.objects.get(item=movie_item)
        self.assertEqual(movie_obj.status, Status.COMPLETED.value)
        self.assertEqual(movie_obj.score, 9)
        self.assertEqual(movie_obj.progress, 1)

        anime_item = Item.objects.get(media_type=MediaTypes.ANIME.value)
        self.assertEqual(anime_item.title, "Cowboy Bebop")
        anime_obj = Anime.objects.get(item=anime_item)
        self.assertEqual(anime_obj.status, Status.PLANNING.value)
        self.assertEqual(anime_obj.score, 7)
        self.assertEqual(anime_obj.notes, "Great series!")

    def test_get_status(self):
        """Test mapping SIMKL status to internal status."""
        self.assertEqual(self.importer._get_status("completed"), Status.COMPLETED.value)
        self.assertEqual(
            self.importer._get_status("watching"),
            Status.IN_PROGRESS.value,
        )
        self.assertEqual(
            self.importer._get_status("plantowatch"),
            Status.PLANNING.value,
        )
        self.assertEqual(self.importer._get_status("hold"), Status.PAUSED.value)
        self.assertEqual(self.importer._get_status("dropped"), Status.DROPPED.value)
        self.assertEqual(
            self.importer._get_status("unknown"),
            Status.IN_PROGRESS.value,
        )  # Default case

    def test_get_date(self):
        """Test getting date from SIMKL."""
        self.assertEqual(
            self.importer._get_date("2023-01-01T00:00:00Z"),
            datetime(2023, 1, 1, 0, 0, 0, tzinfo=UTC),
        )
        self.assertIsNone(self.importer._get_date(None))

    def test_get_date_strips_seconds(self):
        """SIMKL timestamps with seconds should be truncated to the minute."""
        self.assertEqual(
            self.importer._get_date("2023-01-01T10:04:54Z"),
            datetime(2023, 1, 1, 10, 4, 0, tzinfo=UTC),
        )

    @patch("integrations.imports.simkl.SimklImporter._get_user_list")
    @patch("app.providers.tmdb.tv_with_seasons")
    def test_season_status_logic_with_completed_seasons(
        self,
        mock_tv_with_seasons,
        mock_user_list,
    ):
        """Test that seasons are marked as completed when all episodes are watched."""
        mock_tv_with_seasons.return_value = {
            "title": "Breaking Bad",
            "image": "https://image.tmdb.org/t/p/w500/test.jpg",
            "season/1": {
                "image": "https://image.tmdb.org/t/p/w500/season1.jpg",
                "max_progress": 7,
                "episodes": [
                    {"episode_number": 1, "still_path": "/ep1.jpg"},
                    {"episode_number": 2, "still_path": "/ep2.jpg"},
                    {"episode_number": 3, "still_path": "/ep3.jpg"},
                    {"episode_number": 4, "still_path": "/ep4.jpg"},
                    {"episode_number": 5, "still_path": "/ep5.jpg"},
                    {"episode_number": 6, "still_path": "/ep6.jpg"},
                    {"episode_number": 7, "still_path": "/ep7.jpg"},
                ],
            },
            "season/2": {
                "image": "https://image.tmdb.org/t/p/w500/season2.jpg",
                "max_progress": 13,
            },
        }

        mock_user_list.return_value = {
            "shows": [
                {
                    "last_watched_at": "2023-01-15T00:00:00Z",
                    "show": {"title": "Breaking Bad", "ids": {"tmdb": 1396}},
                    "status": "watching",  # TV show is still in progress
                    "user_rating": 9,
                    "seasons": [
                        {
                            "number": 1,
                            "episodes": [
                                {"number": 1, "watched_at": "2023-01-01T00:00:00Z"},
                                {"number": 2, "watched_at": "2023-01-02T00:00:00Z"},
                                {"number": 3, "watched_at": "2023-01-03T00:00:00Z"},
                                {"number": 4, "watched_at": "2023-01-04T00:00:00Z"},
                                {"number": 5, "watched_at": "2023-01-05T00:00:00Z"},
                                {"number": 6, "watched_at": "2023-01-06T00:00:00Z"},
                                {"number": 7, "watched_at": "2023-01-07T00:00:00Z"},
                            ],
                        },
                    ],
                    "memo": {},
                },
            ],
            "movies": [],
            "anime": [],
        }

        imported_counts, _ = self.importer.import_data()

        self.assertEqual(imported_counts[MediaTypes.TV.value], 1)
        self.assertEqual(imported_counts[MediaTypes.SEASON.value], 1)
        self.assertEqual(
            imported_counts[MediaTypes.EPISODE.value],
            7,
        )

        tv_item = Item.objects.get(media_type=MediaTypes.TV.value)
        tv_obj = TV.objects.get(item=tv_item)
        self.assertEqual(tv_obj.status, Status.IN_PROGRESS.value)

        season1_item = Item.objects.get(
            media_type=MediaTypes.SEASON.value,
            season_number=1,
        )
        season1_obj = Season.objects.get(item=season1_item)
        self.assertEqual(
            season1_obj.status,
            Status.COMPLETED.value,
            "Season 1 should be completed when all episodes are watched",
        )

        season1_episodes = Episode.objects.filter(
            item__season_number=1,
            item__media_type=MediaTypes.EPISODE.value,
        )
        self.assertEqual(season1_episodes.count(), 7)

        for episode in season1_episodes:
            self.assertIsNotNone(episode.end_date)


class ImportSimklNewMode(TestCase):
    """Test that "new" mode only imports what is missing from Yamtrack."""

    TV_METADATA = {
        "title": "Breaking Bad",
        "image": "https://image.tmdb.org/t/p/w500/test.jpg",
        "season/1": {
            "image": "https://image.tmdb.org/t/p/w500/season1.jpg",
            "max_progress": 2,
            "episodes": [
                {"episode_number": 1, "still_path": "/ep1.jpg"},
                {"episode_number": 2, "still_path": "/ep2.jpg"},
            ],
        },
        "season/2": {
            "image": "https://image.tmdb.org/t/p/w500/season2.jpg",
            "max_progress": 2,
            "episodes": [
                {"episode_number": 1, "still_path": "/ep1.jpg"},
                {"episode_number": 2, "still_path": "/ep2.jpg"},
            ],
        },
    }

    def setUp(self):
        """Create user for the tests."""
        credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**credentials)

    def show(self, episodes, status="watching", user_rating=None):
        """Return a SIMKL show with the given watched episodes of season 1."""
        return {
            "last_watched_at": "2023-01-02T00:00:00Z",
            "show": {"title": "Breaking Bad", "ids": {"tmdb": 1396}},
            "status": status,
            "user_rating": user_rating,
            "seasons": [{"number": 1, "episodes": episodes}],
            "memo": {},
        }

    def movie(self, status, last_watched_at=None):
        """Return a SIMKL movie."""
        return {
            "added_to_watchlist_at": "2023-01-01T00:00:00Z",
            "movie": {"title": "Perfect Blue", "ids": {"tmdb": 10494}},
            "status": status,
            "user_rating": None,
            "last_watched_at": last_watched_at,
            "memo": {},
        }

    def anime(self, status, watched_episodes_count, user_rating=None):
        """Return a SIMKL anime."""
        return {
            "added_to_watchlist_at": "2023-01-01T00:00:00Z",
            "show": {"title": "Cowboy Bebop", "ids": {"mal": 1}},
            "status": status,
            "user_rating": user_rating,
            "watched_episodes_count": watched_episodes_count,
            "last_watched_at": "2023-03-01T00:00:00Z",
            "memo": {},
        }

    def run_import(self, shows=(), movies=(), anime=()):
        """Run a SIMKL import in "new" mode."""
        metadata = {"title": "Title", "image": "image.jpg"}
        with (
            patch.object(
                simkl.SimklImporter,
                "_get_user_list",
                return_value={
                    "shows": list(shows),
                    "movies": list(movies),
                    "anime": list(anime),
                },
            ),
            patch("app.providers.tmdb.tv_with_seasons", return_value=self.TV_METADATA),
            patch("app.providers.tmdb.movie", return_value=metadata),
            patch("app.providers.mal.anime", return_value=metadata),
        ):
            return simkl.importer(helpers.encrypt("token"), self.user, "new")

    def test_new_episode_added_to_existing_show(self):
        """A new episode of an existing show is imported and completes the season."""
        first_episode = {"number": 1, "watched_at": "2023-01-01T10:00:30Z"}
        self.run_import(shows=[self.show([first_episode])])
        self.assertEqual(
            Season.objects.get(user=self.user).status,
            Status.IN_PROGRESS.value,
        )

        finale = {"number": 2, "watched_at": "2023-01-02T10:00:30Z"}
        imported_counts, _ = self.run_import(
            shows=[self.show([first_episode, finale], status="completed")],
        )

        self.assertEqual(imported_counts, {MediaTypes.EPISODE.value: 1})
        self.assertEqual(TV.objects.filter(user=self.user).count(), 1)
        self.assertEqual(
            Episode.objects.filter(related_season__user=self.user).count(),
            2,
        )
        self.assertEqual(
            Season.objects.get(user=self.user).status,
            Status.COMPLETED.value,
        )
        self.assertEqual(TV.objects.get(user=self.user).status, Status.COMPLETED.value)

    def test_import_twice_adds_nothing(self):
        """Importing the same data again does not duplicate anything."""
        data = {
            "shows": [self.show([{"number": 1, "watched_at": "2023-01-01T10:00:30Z"}])],
            "movies": [self.movie("completed", "2023-02-01T10:00:30Z")],
            "anime": [self.anime("watching", 3)],
        }
        self.run_import(**data)
        imported_counts, _ = self.run_import(**data)

        self.assertEqual(imported_counts, {})
        self.assertEqual(Movie.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Anime.objects.filter(user=self.user).count(), 1)
        self.assertEqual(
            Episode.objects.filter(related_season__user=self.user).count(),
            1,
        )

    def test_new_season_reopens_completed_show(self):
        """A completed show goes back to in progress when a new season is started."""
        season_1 = [
            {"number": 1, "watched_at": "2023-01-01T10:00:00Z"},
            {"number": 2, "watched_at": "2023-01-02T10:00:00Z"},
        ]
        self.run_import(shows=[self.show(season_1, status="completed")])
        self.assertEqual(TV.objects.get(user=self.user).status, Status.COMPLETED.value)

        show = self.show(season_1)
        show["seasons"].append(
            {
                "number": 2,
                "episodes": [{"number": 1, "watched_at": "2024-01-01T10:00:00Z"}],
            },
        )
        self.run_import(shows=[show])

        self.assertEqual(
            TV.objects.get(user=self.user).status,
            Status.IN_PROGRESS.value,
        )
        self.assertEqual(
            list(
                Season.objects.filter(user=self.user)
                .order_by("item__season_number")
                .values_list("status", flat=True),
            ),
            [Status.COMPLETED.value, Status.IN_PROGRESS.value],
        )

    def test_watch_at_around_the_same_time_is_not_duplicated(self):
        """An episode already recorded a few minutes apart is not imported again."""
        self.run_import(
            shows=[self.show([{"number": 1, "watched_at": "2023-01-01T10:00:00Z"}])],
        )

        watched_at = datetime(2023, 1, 1, 10, 0, tzinfo=UTC)
        watched_at += helpers.WATCH_MATCH_TOLERANCE
        imported_counts, _ = self.run_import(
            shows=[
                self.show(
                    [
                        {
                            "number": 1,
                            "watched_at": watched_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        },
                    ],
                ),
            ],
        )

        self.assertEqual(imported_counts, {})
        self.assertEqual(
            Episode.objects.filter(related_season__user=self.user).count(),
            1,
        )

    def test_dropped_show_keeps_status(self):
        """New episodes are added to a dropped show without changing its status."""
        first_episode = {"number": 1, "watched_at": "2023-01-01T10:00:00Z"}
        self.run_import(shows=[self.show([first_episode])])
        TV.objects.filter(user=self.user).update(status=Status.DROPPED.value)
        Season.objects.filter(user=self.user).update(status=Status.DROPPED.value)

        finale = {"number": 2, "watched_at": "2023-01-02T10:00:00Z"}
        self.run_import(shows=[self.show([first_episode, finale])])

        self.assertEqual(
            Episode.objects.filter(related_season__user=self.user).count(),
            2,
        )
        self.assertEqual(TV.objects.get(user=self.user).status, Status.DROPPED.value)
        self.assertEqual(
            Season.objects.get(user=self.user).status,
            Status.DROPPED.value,
        )

    def test_planning_movie_is_completed_in_place(self):
        """Watching a planned movie completes it instead of adding another one."""
        self.run_import(movies=[self.movie("plantowatch")])
        self.assertEqual(
            Movie.objects.get(user=self.user).status, Status.PLANNING.value
        )

        self.run_import(movies=[self.movie("completed", "2023-02-01T10:00:00Z")])

        movie = Movie.objects.get(user=self.user)
        self.assertEqual(movie.status, Status.COMPLETED.value)
        self.assertEqual(movie.progress, 1)
        self.assertEqual(movie.end_date, datetime(2023, 2, 1, 10, 0, tzinfo=UTC))

    def test_existing_anime_progress_is_raised(self):
        """An existing anime gets its progress raised and keeps its own score."""
        self.run_import(anime=[self.anime("watching", 3, user_rating=7)])

        self.run_import(anime=[self.anime("completed", 26, user_rating=9)])

        anime = Anime.objects.get(user=self.user)
        self.assertEqual(anime.progress, 26)
        self.assertEqual(anime.status, Status.COMPLETED.value)
        self.assertEqual(anime.end_date, datetime(2023, 3, 1, 0, 0, tzinfo=UTC))
        self.assertEqual(anime.score, 7)

        # Lower progress in SIMKL never lowers it in Yamtrack
        self.run_import(anime=[self.anime("watching", 5)])
        self.assertEqual(Anime.objects.get(user=self.user).progress, 26)
