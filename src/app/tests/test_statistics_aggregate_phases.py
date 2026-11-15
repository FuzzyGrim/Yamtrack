"""Phases of the range aggregate, each run on its own from hand-built sums.

The aggregate is a day walk into ``_RangeAccumulators`` followed by one
function per finishing phase. These tests build the accumulators by hand so a
phase can be checked without day payloads or a cache.
"""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from app.models import Book, Item, MediaTypes, Movie, Sources, Status
from app.statistics_aggregator import (
    _build_reading_consumption,
    _build_top_played,
    _merge_day,
    _RangeAccumulators,
)


class MergeDayTests(TestCase):
    def test_a_day_adds_to_the_sums_and_keeps_the_newest_status(self):
        acc = _RangeAccumulators()
        first = date(2026, 11, 2)  # a Monday
        second = first + timedelta(days=1)
        _merge_day(
            acc,
            first,
            {
                "items": {"movie": {"7": {"media_id": 70, "status": "In progress"}}},
                "totals": {
                    "minutes_by_type": {"movie": 90},
                    "plays_by_type": {"movie": 1},
                },
                "hour_counts": {"movie": {"20": 1}, "tv": {}},
                "daily_minutes_by_type": {"movie": 90},
            },
        )
        _merge_day(
            acc,
            second,
            {
                "items": {
                    "movie": {
                        "7": {
                            "media_id": 71,
                            "status": "Completed",
                            "activity_dt": "2026-11-03T21:00:00+00:00",
                        }
                    }
                },
                "totals": {
                    "minutes_by_type": {"movie": 30},
                    "plays_by_type": {"movie": 1},
                },
                "daily_minutes_by_type": {"movie": 30, "book": 12},
            },
        )

        self.assertEqual(acc.minutes_by_type["movie"], 120)
        self.assertEqual(acc.plays_by_type["movie"], 2)
        self.assertEqual(acc.items_by_type["movie"][7]["status"], "Completed")
        self.assertEqual(acc.items_by_type["movie"][7]["media_id"], 71)
        self.assertEqual(acc.weekday_hour_counts["movie"][0][20], 1)
        # A type with no hours that day adds no empty heatmap row.
        self.assertNotIn("tv", acc.hour_counts)
        self.assertNotIn("tv", acc.weekday_hour_counts)
        # Reading time counts as one activity even without a play.
        self.assertEqual(acc.activity_counts[second], 2)
        self.assertEqual(acc.activity_counts_by_type[second], {"movie": 1, "book": 1})


class FinisherTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="aggregate-phases")

    def item(self, media_type, index, **extra):
        return Item.objects.create(
            media_id=f"phases-{media_type}-{index}",
            source=Sources.MANUAL.value,
            media_type=media_type,
            library_media_type=media_type,
            title=f"Phases {media_type} {index}",
            **extra,
        )


class TopPlayedTests(FinisherTestCase):
    def test_most_minutes_first_and_rows_without_minutes_left_out(self):
        movies = [
            Movie.objects.create(
                user=self.user,
                item=self.item(MediaTypes.MOVIE.value, index),
                status=Status.COMPLETED.value,
            )
            for index in range(3)
        ]
        acc = _RangeAccumulators()
        for movie, minutes in zip(movies, (90, 240, 0), strict=True):
            acc.top_played_by_type["movie"][movie.item_id] = {
                "item_id": movie.item_id,
                "media_id": movie.id,
                "minutes": minutes,
                "plays": 1,
                "episode_count": 0,
                "activity_dt": None,
            }

        top_played = _build_top_played(acc, {})

        self.assertEqual(
            [entry["media"].id for entry in top_played["movie"]],
            [movies[1].id, movies[0].id],
        )
        self.assertEqual(top_played["movie"][0]["formatted_duration"], "4h 00min")
        self.assertEqual(top_played["tv"], [])

    def test_a_shared_memo_hands_out_a_copy_per_call(self):
        movie = Movie.objects.create(
            user=self.user,
            item=self.item(MediaTypes.MOVIE.value, 9),
            status=Status.COMPLETED.value,
        )
        acc = _RangeAccumulators()
        acc.top_played_by_type["movie"][movie.item_id] = {
            "item_id": movie.item_id,
            "media_id": movie.id,
            "minutes": 30,
            "plays": 1,
            "episode_count": 0,
            "activity_dt": None,
        }
        memo = {}

        first = _build_top_played(acc, memo)["movie"][0]["media"]
        with self.assertNumQueries(0):
            second = _build_top_played(acc, memo)["movie"][0]["media"]

        self.assertEqual(first.pk, second.pk)
        self.assertIsNot(first, second)
        self.assertIsNot(first.item, second.item)


class ReadingConsumptionTests(FinisherTestCase):
    def test_completions_count_titles_and_lengths_count_entries(self):
        now = timezone.now()
        book_item = self.item(MediaTypes.BOOK.value, 1, number_of_pages=300)
        audiobook_item = self.item(MediaTypes.BOOK.value, 2, format="audiobook")
        for progress, days_ago in ((300, 40), (280, 5)):
            Book.objects.create(
                user=self.user,
                item=book_item,
                status=Status.COMPLETED.value,
                progress=progress,
                start_date=now - timedelta(days=days_ago + 2),
                end_date=now - timedelta(days=days_ago),
            )
        Book.objects.create(
            user=self.user,
            item=audiobook_item,
            status=Status.COMPLETED.value,
            progress=600,
            start_date=now - timedelta(days=3),
            end_date=now - timedelta(days=2),
        )
        acc = _RangeAccumulators()
        acc.day_minutes_by_type["book"] = {"2026-11-01": 50}
        acc.listening_minutes_total = 600

        all_time = _build_reading_consumption(
            acc, self.user, MediaTypes.BOOK.value, None, None, {}
        )
        recent = _build_reading_consumption(
            acc,
            self.user,
            MediaTypes.BOOK.value,
            now - timedelta(days=10),
            now,
            {},
        )

        # One finished title per item, however many times it was finished.
        self.assertEqual(all_time["completions"]["total"], 2)
        # Every completed entry has a length (a finished book is all its
        # pages); an audiobook's minutes are not a length.
        self.assertEqual(all_time["completed_lengths"], [300, 300])
        self.assertEqual(all_time["listening"]["minutes"], 600)
        self.assertEqual(all_time["units"]["total"], 50)
        # A bounded range only counts entries finished inside it.
        self.assertEqual(recent["completions"]["total"], 2)
        self.assertEqual(recent["completed_lengths"], [300])

    def test_a_type_with_nothing_in_range_is_empty(self):
        consumption = _build_reading_consumption(
            _RangeAccumulators(), self.user, MediaTypes.MANGA.value, None, None, {}
        )

        self.assertFalse(consumption["has_data"])
        self.assertEqual(consumption["completion_label"], "Manga Finished")
        self.assertEqual(consumption["top_items"], [])
