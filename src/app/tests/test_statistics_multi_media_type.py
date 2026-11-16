import datetime

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from app.models import (
    Game,
    Item,
    MediaTypes,
    Movie,
    Sources,
    Status,
)
from app.statistics_cache import get_statistics_data
from app.stats_activity import calculate_streak_details

EPOCH_DAY = datetime.date(1970, 1, 1)


def _expand_runs(runs):
    return {
        EPOCH_DAY + datetime.timedelta(days=start + i)
        for start, n in runs
        for i in range(n)
    }


class SummaryStatsByTypeMergePiecesTests(TestCase):
    """The raw pieces the page adds up when several media types are selected."""

    def setUp(self):
        # Day and range statistics live in the shared cache, keyed by user id;
        # SQLite reuses ids after rollback, so start every test empty.
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="multi-type-stats",
            password="password123",
        )
        self.now = timezone.now()
        for index, (title, score) in enumerate((("Movie A", 8), ("Movie B", 9))):
            item = Item.objects.create(
                media_id=f"tmdb-{700 + index}",
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title=title,
                runtime_minutes=100,
            )
            Movie.objects.create(
                item=item,
                user=self.user,
                status=Status.COMPLETED.value,
                score=score,
                start_date=self.now - datetime.timedelta(days=index + 1),
                end_date=self.now - datetime.timedelta(days=index + 1),
            )
        game_item = Item.objects.create(
            media_id="igdb-700",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="A Game",
        )
        Game.objects.create(
            item=game_item,
            user=self.user,
            status=Status.COMPLETED.value,
            score=6,
            progress=120,
            start_date=self.now - datetime.timedelta(days=5),
            end_date=self.now - datetime.timedelta(days=5),
        )
        self.summary = get_statistics_data(self.user, None, None)[
            "summary_stats_by_type"
        ]

    def test_every_type_with_activity_carries_the_merge_pieces(self):
        movie = self.summary[MediaTypes.MOVIE.value]
        for key in (
            "score_count",
            "score_sum",
            "weekday_minutes",
            "active_runs",
            "streak_end_day",
        ):
            self.assertIn(key, movie)
        self.assertEqual(len(movie["weekday_minutes"]), 7)

    def test_score_pieces_rebuild_the_server_average(self):
        movie = self.summary[MediaTypes.MOVIE.value]
        self.assertEqual(movie["score_count"], 2)
        self.assertEqual(
            round(movie["score_sum"] / movie["score_count"], 2),
            movie["average_score"],
        )

    def test_weekday_minutes_add_up_to_the_type_total(self):
        for media_type in (MediaTypes.MOVIE.value, MediaTypes.GAME.value):
            stats = self.summary[media_type]
            self.assertAlmostEqual(
                sum(stats["weekday_minutes"]),
                stats["total_minutes"],
                places=3,
                msg=media_type,
            )

    def test_active_runs_reproduce_the_server_streaks(self):
        for media_type in (MediaTypes.MOVIE.value, MediaTypes.GAME.value):
            stats = self.summary[media_type]
            days = _expand_runs(stats["active_runs"])
            self.assertTrue(days, media_type)
            end_date = EPOCH_DAY + datetime.timedelta(days=stats["streak_end_day"])
            streaks = calculate_streak_details(dict.fromkeys(days, 1), end_date)
            self.assertEqual(streaks["longest_streak"], stats["longest_streak"])
            self.assertEqual(streaks["current_streak"], stats["current_streak"])

    def test_runs_are_consecutive_days_grouped_together(self):
        # The two movies were finished on consecutive days, the game 5 days ago.
        runs = self.summary[MediaTypes.MOVIE.value]["active_runs"]
        self.assertEqual([length for _start, length in runs], [2])
        self.assertEqual(
            [
                length
                for _start, length in self.summary[MediaTypes.GAME.value]["active_runs"]
            ],
            [1],
        )
